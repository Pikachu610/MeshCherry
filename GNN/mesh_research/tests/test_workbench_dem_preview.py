"""DEM preview API coverage for GeoTIFF and ESRI ASCII Grid sources."""
import base64
import http.client
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS

from gis_mesh_workbench.webui import WorkbenchServer


class WorkbenchDEMPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.server = WorkbenchServer(self.workspace)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, payload):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        try:
            connection.request(
                "POST",
                "/api/input-preview",
                body=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "Origin": self.server.origin,
                    "X-Workbench-Token": self.server.token,
                },
            )
            response = connection.getresponse()
            raw = response.read()
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                body = raw
            return response.status, body
        finally:
            connection.close()

    def write_tif(
        self,
        path,
        data,
        *,
        crs=CRS.from_epsg(32649),
        nodata=-9999,
        transform=Affine(30, 0, 500000, 0, -30, 1000000),
    ):
        values = np.asarray(data, dtype="float64")
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=values.shape[1],
            height=values.shape[0],
            count=1,
            dtype="float64",
            nodata=nodata,
            transform=transform,
            crs=crs,
        ) as dataset:
            dataset.write(values, 1)

    def assert_png_preview(self, report):
        image = report["image"]
        self.assertTrue(image.startswith("data:image/png;base64,"))
        self.assertTrue(base64.b64decode(image.split(",", 1)[1]).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_geotiff_preview_masks_nodata_and_keeps_source_unchanged(self):
        path = self.workspace / "dem.tif"
        self.write_tif(path, [[10, -9999], [30, 40]])
        before = path.read_bytes()

        status, report = self.request({"path": str(path), "kind": "raster", "band": 1})

        self.assertEqual(status, 200, report)
        self.assertEqual(report["driver"], "GTiff")
        self.assertEqual(report["crs"], "EPSG:32649")
        self.assertTrue(report["crs_defined"])
        self.assertEqual(report["coordinate_units"], "m")
        self.assertEqual(report["pixel_size"], [30.0, 30.0])
        self.assertEqual(report["nodata"], -9999.0)
        self.assertEqual(report["source_shape"], [2, 2])
        self.assertFalse(report["no_valid_samples"])
        self.assert_png_preview(report)
        self.assertEqual(path.read_bytes(), before)

    def test_ascii_grid_preview_reads_matching_prj_and_nodata(self):
        path = self.workspace / "dem.asc"
        path.write_text(
            "ncols 3\nnrows 2\nxllcorner 500000\nyllcorner 999940\ncellsize 30\n"
            "NODATA_value -9999\n10 20 30\n40 -9999 60\n",
            encoding="ascii",
        )
        path.with_suffix(".prj").write_text(CRS.from_epsg(32649).to_wkt(), encoding="ascii")

        status, report = self.request({"path": str(path), "kind": "raster", "band": 1})

        self.assertEqual(status, 200, report)
        self.assertEqual(report["driver"], "AAIGrid")
        self.assertEqual(report["crs"], "EPSG:32649")
        self.assertTrue(report["crs_defined"])
        self.assertEqual(report["coordinate_units"], "m")
        self.assertEqual(report["pixel_size"], [30.0, 30.0])
        self.assertEqual(report["nodata"], -9999.0)
        self.assertEqual(report["source_shape"], [2, 3])
        self.assertFalse(report["no_valid_samples"])
        self.assert_png_preview(report)

    def test_missing_crs_is_previewable_but_explicit_and_invalid_band_fails(self):
        path = self.workspace / "unreferenced.tif"
        self.write_tif(path, [[10, 20]], crs=None)

        status, report = self.request({"path": str(path), "kind": "raster", "band": 1})
        self.assertEqual(status, 200, report)
        self.assertEqual(report["crs"], "Chua co CRS")
        self.assertFalse(report["crs_defined"])
        self.assertEqual(report["coordinate_units"], "unknown")

        status, error = self.request({"path": str(path), "kind": "raster", "band": 2})
        self.assertNotEqual(status, 200)
        self.assertIn("Band", str(error))

    def test_ascii_without_prj_is_previewable_but_not_usable_for_processing(self):
        path = self.workspace / "unreferenced.asc"
        path.write_text(
            "ncols 2\nnrows 1\nxllcorner 500000\nyllcorner 999970\ncellsize 30\n"
            "NODATA_value -9999\n10 20\n",
            encoding="ascii",
        )

        status, report = self.request({"path": str(path), "kind": "raster", "band": 1})

        self.assertEqual(status, 200, report)
        self.assertEqual(report["crs"], "Chua co CRS")
        self.assertFalse(report["crs_defined"])
        self.assertEqual(report["coordinate_units"], "unknown")
        self.assert_png_preview(report)

        from gis_mesh_workbench.raster_source import open_raster

        with self.assertRaisesRegex(ValueError, r"ASC thiếu CRS.*file \.prj"):
            with open_raster(path):
                pass

    def test_missing_nodata_is_json_null(self):
        path = self.workspace / "no-nodata.tif"
        self.write_tif(path, [[10, 20]], nodata=None)

        status, report = self.request({"path": str(path), "kind": "raster", "band": 1})

        self.assertEqual(status, 200, report)
        self.assertIsNone(report["nodata"])

    def test_nonfinite_nodata_is_json_null(self):
        path = self.workspace / "nan-nodata.tif"
        self.write_tif(path, [[10, 20]], nodata=float("nan"))

        status, report = self.request({"path": str(path), "kind": "raster", "band": 1})

        self.assertEqual(status, 200, report)
        self.assertIsNone(report["nodata"])

    def test_geographic_and_rotated_pixel_vectors_use_native_crs_units(self):
        path = self.workspace / "rotated-geographic.tif"
        transform = Affine(0.01, 0.002, 105, 0.001, -0.01, 20)
        self.write_tif(path, [[10, 20], [30, 40]], crs=CRS.from_epsg(4326), transform=transform)

        status, report = self.request({"path": str(path), "kind": "raster", "band": 1})

        self.assertEqual(status, 200, report)
        self.assertEqual(report["coordinate_units"], "degree")
        self.assertAlmostEqual(report["pixel_size"][0], math.hypot(transform.a, transform.d))
        self.assertAlmostEqual(report["pixel_size"][1], math.hypot(transform.b, transform.e))

    def test_all_nodata_is_reported_and_render_is_bounded(self):
        path = self.workspace / "large.tif"
        self.write_tif(path, np.full((800, 600), -9999, dtype="float64"))

        status, report = self.request({"path": str(path), "kind": "raster", "band": 1})

        self.assertEqual(status, 200, report)
        self.assertTrue(report["sampled"])
        self.assertLessEqual(max(report["display_shape"]), 512)
        self.assertTrue(report["no_valid_samples"])
        self.assert_png_preview(report)

    def test_path_outside_authorized_workspace_is_rejected(self):
        path = self.base / "outside.tif"
        self.write_tif(path, [[10, 20]])

        status, _ = self.request({"path": str(path), "kind": "raster", "band": 1})

        self.assertNotEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
