import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

import rasterio
from rasterio.crs import CRS
from affine import Affine
import numpy as np

from gis_mesh_workbench.extraction import Cell
from gis_mesh_workbench.indexed_extraction import extract_indexed
from gis_mesh_workbench.exports import export_cells


ANALYSIS_CRS = "EPSG:32648"
CELLS = {"schema_version": "workbench-cells-v1", "crs_wkt": ANALYSIS_CRS,
         "coordinate_units": "m", "cells": [
             {"cell_id": "a", "kind": "rectangle",
              "xy": [[500000, 2000000], [500001, 2000000],
                     [500001, 2000001], [500000, 2000001]]},
             {"cell_id": "b", "kind": "rectangle",
              "xy": [[500001, 2000000], [500002, 2000000],
                     [500002, 2000001], [500001, 2000001]]},
         ]}
ATTRIBUTES = {"schema_version": "cell-attributes-reference-v0", "cells": [
    {"cell_id": "a", "value": None, "class_fractions": {}},
    {"cell_id": "b", "value": 2**60, "class_fractions": {str(2**60): 1.0}},
]}


class WorkbenchExportTests(unittest.TestCase):
    def test_real_indexed_extraction_exports_to_geojson(self):
        with tempfile.TemporaryDirectory() as tmp:
            raster_path = Path(tmp) / "sample.tif"
            with rasterio.open(raster_path, "w", driver="GTiff", width=2, height=2,
                               count=1, dtype="float32", crs=ANALYSIS_CRS,
                               transform=Affine(1, 0, 500000, 0, -1, 2000002)) as dst:
                dst.write(np.array([[1, 2], [3, 4]], dtype="float32"), 1)
            source_cells = (
                Cell("a", "rectangle", ((500000, 2000000), (500001, 2000000),
                                          (500001, 2000001), (500000, 2000001))),
                Cell("b", "rectangle", ((500001, 2000000), (500002, 2000000),
                                          (500002, 2000001), (500001, 2000001))),
            )
            attributes = extract_indexed(raster_path, source_cells,
                                         cell_crs=ANALYSIS_CRS,
                                         analysis_crs=ANALYSIS_CRS, band=1,
                                         semantics="intensive", value_units="test")
            self.assertEqual(attributes["schema_version"], "cell-attributes-indexed-v1")
            path = Path(tmp) / "indexed.geojson"
            export_cells(path, "geojson", CELLS, attributes, crs=ANALYSIS_CRS)
            output = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([item["properties"]["cell_id"]
                              for item in output["features"]], ["a", "b"])
            self.assertEqual([item["properties"]["value"]
                              for item in output["features"]], [3.0, 4.0])

    def test_geojson_is_rfc7946_wgs84_and_keeps_cell_ids_nulls_and_ints(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cells.geojson"
            export_cells(path, "geojson", CELLS, ATTRIBUTES, crs=ANALYSIS_CRS)
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("crs", result)
            self.assertEqual([f["properties"]["cell_id"] for f in result["features"]], ["a", "b"])
            self.assertIsNone(result["features"][0]["properties"]["value"])
            self.assertEqual(result["features"][1]["properties"]["value"], 2**60)
            longitude, latitude = result["features"][0]["geometry"]["coordinates"][0][0]
            self.assertTrue(-180 <= longitude <= 180)
            self.assertTrue(-90 <= latitude <= 90)

    def test_geopackage_has_valid_core_tables_and_preserves_int64_and_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cells.gpkg"
            export_cells(path, "gpkg", CELLS, ATTRIBUTES, crs=ANALYSIS_CRS)
            con = sqlite3.connect(path)
            try:
                self.assertEqual(con.execute("PRAGMA application_id").fetchone()[0], 0x47504B47)
                self.assertEqual(con.execute("SELECT data_type FROM gpkg_contents WHERE table_name='cells'").fetchone()[0], "features")
                self.assertEqual(con.execute("SELECT geometry_type_name FROM gpkg_geometry_columns").fetchone()[0], "POLYGON")
                values = con.execute("SELECT cell_id, value FROM cells ORDER BY cell_id").fetchall()
                self.assertEqual(values, [("a", None), ("b", 2**60)])
                blob = con.execute("SELECT geom FROM cells WHERE cell_id='a'").fetchone()[0]
                self.assertEqual(blob[:2], b"GP")
                self.assertEqual(int.from_bytes(blob[4:8], "little", signed=True), 32648)
            finally:
                con.close()

    def test_geotiff_uses_explicit_resolution_single_band_and_nodata(self):
        attributes = {**ATTRIBUTES, "cells": [
            {"cell_id": "a", "value": None}, {"cell_id": "b", "value": 17}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cells.tif"
            result = export_cells(path, "geotiff", CELLS, attributes,
                                  crs=ANALYSIS_CRS, resolution=1)
            with rasterio.open(path) as source:
                self.assertEqual(source.count, 1)
                self.assertEqual(source.crs, CRS.from_epsg(32648))
                self.assertEqual((source.width, source.height), (2, 1))
                self.assertEqual(source.nodata, -9999)
                self.assertEqual(source.read(1).tolist(), [[-9999, 17]])
            self.assertEqual(result["resolution_m"], 1.0)

    def test_ascii_export_bundles_projection_sidecar_and_checks_budget(self):
        attributes = {**ATTRIBUTES, "cells": [
            {"cell_id": "a", "value": None}, {"cell_id": "b", "value": 3}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cells.zip"
            export_cells(path, "asc", CELLS, attributes, crs=ANALYSIS_CRS,
                         resolution=1)
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(set(archive.namelist()), {"meshcherry.asc", "meshcherry.prj"})
                text = archive.read("meshcherry.asc").decode("ascii")
                self.assertIn("ncols 2\nnrows 1", text)
                self.assertIn("NODATA_value -9999", text)
                self.assertIn("PROJCS", archive.read("meshcherry.prj").decode("ascii"))
                extracted = Path(tmp) / "meshcherry.asc"
                extracted.write_bytes(archive.read("meshcherry.asc"))
            with rasterio.open(extracted) as source:
                self.assertEqual(source.read(1).tolist(), [[-9999, 3]])
                self.assertEqual(source.count, 1)
            with self.assertRaisesRegex(ValueError, "pixel budget"):
                export_cells(Path(tmp) / "too-big.tif", "tif", CELLS, attributes,
                             crs=ANALYSIS_CRS, resolution=0.01, max_pixels=10)

    def test_rasterization_rejects_category_strings_and_crs_relabel(self):
        categories = {"schema_version": "cell-attributes-reference-v0", "cells": [
            {"cell_id": "a", "value": "forest"}, {"cell_id": "b", "value": "water"}]}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "numeric"):
                export_cells(Path(tmp) / "bad.tif", "tif", CELLS, categories,
                             crs=ANALYSIS_CRS, resolution=1)
            with self.assertRaisesRegex(ValueError, "does not match"):
                export_cells(Path(tmp) / "bad.geojson", "geojson", CELLS,
                             ATTRIBUTES, crs="EPSG:32649")

    def test_raster_all_null_is_nodata_and_extensive_is_rejected(self):
        all_null = {"schema_version": "cell-attributes-reference-v0", "cells": [
            {"cell_id": "a", "value": None}, {"cell_id": "b", "value": None}]}
        extensive = {"schema_version": "cell-attributes-reference-v0",
                     "provenance": {"semantics": "extensive"}, "cells": [
                         {"cell_id": "a", "value": 5}, {"cell_id": "b", "value": 8}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.tif"
            export_cells(path, "tif", CELLS, all_null, crs=ANALYSIS_CRS, resolution=1)
            with rasterio.open(path) as source:
                self.assertEqual(source.read(1).tolist(), [[-9999, -9999]])
            with self.assertRaisesRegex(ValueError, "Extensive"):
                export_cells(Path(tmp) / "extensive.tif", "tif", CELLS, extensive,
                             crs=ANALYSIS_CRS, resolution=1)

    def test_categorical_numeric_codes_remain_exact_int64(self):
        categories = {"schema_version": "cell-attributes-reference-v0",
                      "provenance": {"semantics": "categorical"}, "cells": [
                          {"cell_id": "a", "value": 1.0},
                          {"cell_id": "b", "value": 2**60}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "categories.tif"
            export_cells(path, "tif", CELLS, categories, crs=ANALYSIS_CRS,
                         resolution=1)
            with rasterio.open(path) as source:
                self.assertEqual(source.dtypes, ("int64",))
                self.assertEqual(source.read(1).tolist(), [[1, 2**60]])
            unsafe = {**categories, "cells": [
                {"cell_id": "a", "value": float(2**54)},
                {"cell_id": "b", "value": 2**60}]}
            with self.assertRaisesRegex(ValueError, "exact integer codes"):
                export_cells(Path(tmp) / "unsafe.tif", "tif", CELLS, unsafe,
                             crs=ANALYSIS_CRS, resolution=1)


if __name__ == "__main__":
    unittest.main()
