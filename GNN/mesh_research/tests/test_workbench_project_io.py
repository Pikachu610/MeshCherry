import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import warnings

import numpy as np
import rasterio
from affine import Affine

from gis_mesh_workbench.extraction import Cell
from gis_mesh_workbench.indexed_extraction import extract_indexed
from gis_mesh_workbench.project_io import load_project, save_project


CRS = "EPSG:32648"
CELLS = {
    "schema_version": "workbench-cells-v1",
    "crs_wkt": CRS,
    "coordinate_units": "m",
    "cells": [
        {"cell_id": "cell-a", "kind": "rectangle",
         "xy": [[500000, 2000000], [500001, 2000000],
                [500001, 2000001], [500000, 2000001]]},
        {"cell_id": "cell-b", "kind": "triangle",
         "xy": [[500001, 2000000], [500002, 2000000],
                [500002, 2000001]]},
    ],
}
QA = {"status": "pass", "checks": {"cell_count": 2},
      "bounds": [500000, 2000000, 500002, 2000001]}
ATTRIBUTES = {"schema_version": "cell-attributes-reference-v0",
              "cells": [
                  {"cell_id": "cell-a", "value": None,
                   "footprint_coverage": 0.8, "valid_coverage": 0.0,
                   "class_fractions": {}},
                  {"cell_id": "cell-b", "value": 2**60,
                   "footprint_coverage": 0.9, "valid_coverage": 0.75,
                   "class_fractions": {str(2**60): 1.0}},
              ],
              "provenance": {"semantics": "categorical",
                             "source_path": "D:\\Private\\raw.tif"}}


class ProjectIOTests(unittest.TestCase):
    def test_real_indexed_extraction_round_trips_in_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            raster_path = Path(tmp) / "sample.tif"
            with rasterio.open(raster_path, "w", driver="GTiff", width=2, height=2,
                               count=1, dtype="float32", crs=CRS,
                               transform=Affine(1, 0, 500000, 0, -1, 2000002)) as dst:
                dst.write(np.array([[1, 2], [3, 4]], dtype="float32"), 1)
            source_cells = (
                Cell("cell-a", "rectangle", ((500000, 2000000), (500001, 2000000),
                                               (500001, 2000001), (500000, 2000001))),
                Cell("cell-b", "rectangle", ((500001, 2000000), (500002, 2000000),
                                               (500002, 2000001), (500001, 2000001))),
            )
            result = extract_indexed(raster_path, source_cells, cell_crs=CRS,
                                     analysis_crs=CRS, band=1, semantics="intensive",
                                     value_units="test")
            self.assertEqual(result["schema_version"], "cell-attributes-indexed-v1")
            path = Path(tmp) / "indexed.mcy"
            save_project(path, cells=CELLS, qa=QA, attributes=result)
            loaded = load_project(path)
            self.assertEqual(loaded["attributes"]["schema_version"],
                             "cell-attributes-indexed-v1")
            self.assertEqual(loaded["attributes"]["cells"], result["cells"])

    def test_round_trip_is_offline_and_scrubs_paths_and_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.mcy"
            save_project(path, cells=CELLS, qa=QA,
                         config={"schema_version": "gis-workbench-run-config-v2",
                                 "analysis_crs": CRS,
                                 "raster_path": "D:\\Private\\raw.tif",
                                 "output_root": "D:\\Private\\results",
                                 "cell_source": {"kind": "gmsh_generated",
                                                 "executable": "D:\\Tools\\gmsh.exe",
                                                 "boundary_path": "D:\\Private\\roi.shp"},
                                 "auth_token": "secret-value"},
                         attributes=ATTRIBUTES,
                         provenance={"source_path": "D:\\Private\\raw.tif",
                                     "mesh_manifest": {"input_hashes": {
                                         "D:\\Private\\raw.tif": "abc123"}}},
                         view_settings={"view_box": "1 2 3 4", "show_nodes": True,
                                        "extra": "discard me"})
            raw = path.read_bytes()
            for private in (b"D:\\Private", b"D:\\Tools", b"secret-value"):
                self.assertNotIn(private, raw)
            opened = load_project(path)
            self.assertEqual([cell.cell_id for cell in opened["cell_objects"]],
                             ["cell-a", "cell-b"])
            self.assertEqual(opened["cells"]["cells"], CELLS["cells"])
            self.assertEqual(opened["attributes"]["cells"][1]["value"], 2**60)
            self.assertIsNone(opened["attributes"]["cells"][0]["value"])
            self.assertEqual(opened["view_settings"], {"view_box": "1 2 3 4", "show_nodes": True})
            self.assertIsNone(opened["config"]["cell_source"]["executable"])
            self.assertEqual(opened["execution_mode"], "offline_saved_result_no_implicit_rerun")
            self.assertNotIn("status", opened["qa"])
            self.assertEqual(opened["qa"]["historical_status"], "pass")
            self.assertTrue(opened["unavailable_inputs"])

    def test_rejects_attribute_membership_mismatch_and_invalid_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.mcy"
            attributes = dict(ATTRIBUTES)
            attributes["cells"] = [dict(ATTRIBUTES["cells"][0]),
                                    {**ATTRIBUTES["cells"][1], "cell_id": "foreign"}]
            with self.assertRaisesRegex(ValueError, "exactly match"):
                save_project(path, cells=CELLS, qa=QA, attributes=attributes)
            bad_cells = {**CELLS, "cells": [dict(CELLS["cells"][0]),
                                             dict(CELLS["cells"][0])]}
            with self.assertRaises(ValueError):
                save_project(path, cells=bad_cells, qa=QA)

    def test_rejects_bad_archive_inventory_and_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.mcy"
            save_project(good, cells=CELLS, qa=QA)
            bad_path = Path(tmp) / "traversal.mcy"
            with zipfile.ZipFile(bad_path, "w") as archive:
                archive.writestr("../escape", b"x")
            with self.assertRaisesRegex(ValueError, "unexpected paths"):
                load_project(bad_path)
            with zipfile.ZipFile(good) as archive:
                raw = {name: archive.read(name) for name in archive.namelist()}
            raw["cells.json"] = raw["cells.json"].replace(b"cell-a", b"cell-x")
            tampered = Path(tmp) / "tampered.mcy"
            with zipfile.ZipFile(tampered, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in raw.items():
                    archive.writestr(name, data)
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                load_project(tampered)

    def test_rejects_duplicate_members_and_high_compression_ratio(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.mcy"
            save_project(good, cells=CELLS, qa=QA)
            duplicate = Path(tmp) / "duplicate.mcy"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(good) as source, zipfile.ZipFile(duplicate, "w") as target:
                    for info in source.infolist():
                        target.writestr(info.filename, source.read(info.filename))
                    target.writestr("cells.json", source.read("cells.json"))
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_project(duplicate)
            bomb = Path(tmp) / "ratio.mcy"
            with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("project.json", b"a" * 200_000)
                archive.writestr("cells.json", b"{}")
                archive.writestr("qa.json", b"{}")
                archive.writestr("config.json", b"{}")
            with self.assertRaisesRegex(ValueError, "compression ratio"):
                load_project(bomb)

    def test_rejects_missing_required_members_and_rechecks_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.mcy"
            with zipfile.ZipFile(missing, "w") as archive:
                archive.writestr("project.json", "{}")
            with self.assertRaisesRegex(ValueError, "missing required"):
                load_project(missing)

            good = Path(tmp) / "good.mcy"
            save_project(good, cells=CELLS, qa=QA)
            with zipfile.ZipFile(good) as source:
                contents = {name: source.read(name) for name in source.namelist()}
            cells = json.loads(contents["cells.json"])
            cells["schema_version"] = "workbench-cells-v999"
            contents["cells.json"] = json.dumps(cells, separators=(",", ":")).encode()
            manifest = json.loads(contents["project.json"])
            manifest["files"]["cells.json"] = {
                "size": len(contents["cells.json"]),
                "sha256": hashlib.sha256(contents["cells.json"]).hexdigest()}
            contents["project.json"] = json.dumps(manifest, separators=(",", ":")).encode()
            changed = Path(tmp) / "schema.mcy"
            with zipfile.ZipFile(changed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in contents.items():
                    archive.writestr(name, data)
            with self.assertRaisesRegex(ValueError, "Unsupported project cell schema"):
                load_project(changed)

    def test_load_rescrubs_absolute_paths_from_historical_qa_and_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.mcy"
            save_project(good, cells=CELLS, qa=QA, attributes=ATTRIBUTES)
            with zipfile.ZipFile(good) as source:
                contents = {name: source.read(name) for name in source.namelist()}
            qa = json.loads(contents["qa.json"])
            qa["legacy_report_path"] = "D:\\Private\\old-qa.json"
            contents["qa.json"] = json.dumps(qa, separators=(",", ":")).encode()
            config = json.loads(contents["config.json"])
            config["raster_path"] = "D:\\Private\\config-raster.tif"
            config["auth_token"] = "do-not-keep"
            contents["config.json"] = json.dumps(config, separators=(",", ":")).encode()
            attributes = json.loads(contents["attributes.json"])
            attributes["provenance"]["source_path"] = "D:\\Private\\old-raster.tif"
            contents["attributes.json"] = json.dumps(attributes, separators=(",", ":")).encode()
            manifest = json.loads(contents["project.json"])
            for name in ("qa.json", "attributes.json", "config.json"):
                manifest["files"][name] = {"size": len(contents[name]),
                                            "sha256": hashlib.sha256(contents[name]).hexdigest()}
            contents["project.json"] = json.dumps(manifest, separators=(",", ":")).encode()
            changed = Path(tmp) / "metadata-paths.mcy"
            with zipfile.ZipFile(changed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in contents.items():
                    archive.writestr(name, data)
            opened = load_project(changed)
            persisted = json.dumps({"qa": opened["qa"], "attributes": opened["attributes"],
                                    "config": opened["config"]})
            self.assertNotIn("D:\\\\Private", persisted)
            self.assertNotIn("do-not-keep", persisted)
            self.assertIsNone(opened["config"]["raster_path"])
            self.assertTrue(any(item["label"] == "old-qa.json"
                                for item in opened["unavailable_inputs"]))
            self.assertTrue(any(item["label"] == "old-raster.tif"
                                for item in opened["unavailable_inputs"]))

    def test_rejects_nonfinite_geometry_and_invalid_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.mcy"
            broken = {**CELLS, "cells": [dict(CELLS["cells"][0]), CELLS["cells"][1]]}
            broken["cells"][0]["xy"] = [[500000, 2000000], [500001, 2000000],
                                         [500001, float("nan")], [500000, 2000001]]
            with self.assertRaises(ValueError):
                save_project(path, cells=broken, qa=QA)
            attributes = {**ATTRIBUTES, "cells": [
                {**ATTRIBUTES["cells"][0], "valid_coverage": -0.1},
                ATTRIBUTES["cells"][1]]}
            with self.assertRaisesRegex(ValueError, "coverage"):
                save_project(path, cells=CELLS, qa=QA, attributes=attributes)

    def test_attribute_records_require_values_and_consistent_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.mcy"
            missing = {"schema_version": "cell-attributes-reference-v0", "cells": [
                {"cell_id": "cell-a", "value": 1, "footprint_coverage": 0.8,
                 "valid_coverage": 0.6},
                {"cell_id": "cell-b", "value": 2, "valid_coverage": 0.5}]}
            with self.assertRaisesRegex(ValueError, "footprint_coverage"):
                save_project(path, cells=CELLS, qa=QA, attributes=missing)
            inconsistent = {"schema_version": "cell-attributes-reference-v0", "cells": [
                {"cell_id": "cell-a", "value": 1, "footprint_coverage": 0.5,
                 "valid_coverage": 0.6},
                {"cell_id": "cell-b", "value": 2, "footprint_coverage": 0.9,
                 "valid_coverage": 0.5}]}
            with self.assertRaisesRegex(ValueError, "valid_coverage"):
                save_project(path, cells=CELLS, qa=QA, attributes=inconsistent)
            invalid_value = {"schema_version": "cell-attributes-reference-v0", "cells": [
                {"cell_id": "cell-a", "value": {"nested": 1}, "footprint_coverage": 0.8,
                 "valid_coverage": 0.6},
                {"cell_id": "cell-b", "value": 2, "footprint_coverage": 0.9,
                 "valid_coverage": 0.5}]}
            with self.assertRaisesRegex(ValueError, "scalar"):
                save_project(path, cells=CELLS, qa=QA, attributes=invalid_value)


if __name__ == "__main__":
    unittest.main()
