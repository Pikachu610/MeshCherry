import unittest

from gis_mesh_workbench.job_control import Cancelled, JobControl, job_scope
from gis_mesh_workbench.quality_assessment import evaluate_mesh_quality
from gis_mesh_workbench.quality_context import context_config, enrich_quality


CRS = "EPSG:32649"


def tri(cell_id, points):
    return {"cell_id": cell_id, "kind": "triangle", "xy": points}


def mesh(cells):
    return {"crs_wkt": CRS, "coordinate_units": "m", "cells": cells}


def rectangle(prefix, x0, y0, x1, y1):
    a, b, c, d = (x0, y0), (x1, y0), (x1, y1), (x0, y1)
    return [tri(prefix + "a", [a, b, c]), tri(prefix + "b", [a, c, d])]


def segmented_rectangle(prefix, xs, ys):
    cells = []
    for col, (x0, x1) in enumerate(zip(xs, xs[1:])):
        for row, (y0, y1) in enumerate(zip(ys, ys[1:])):
            cells.extend(rectangle(f"{prefix}{col}-{row}-", x0, y0, x1, y1))
    return cells


def polygon_geometry(rings):
    return {"type": "Polygon", "coordinates": rings}


def roi(geometry, crs=CRS):
    return {"geometry": geometry, "crs_wkt": crs}


class QualityContextTests(unittest.TestCase):
    def enrich(self, cells, reference, field=None, config=None):
        payload = mesh(cells)
        result = evaluate_mesh_quality(payload)
        return enrich_quality(result, payload, roi=reference, size_field=field, config=config)

    def test_boundary_preserves_polygon_hole(self):
        cells = (segmented_rectangle("bottom", [0, 1, 3, 4], [0, 1])
                 + segmented_rectangle("top", [0, 1, 3, 4], [3, 4])
                 + segmented_rectangle("left", [0, 1], [1, 3])
                 + segmented_rectangle("right", [3, 4], [1, 3]))
        domain = polygon_geometry([[(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)],
                                   [(1, 1), (1, 3), (3, 3), (3, 1), (1, 1)]])
        result = self.enrich(cells, roi(domain))
        self.assertEqual(result["boundary"]["status"], "PASS")
        self.assertAlmostEqual(result["boundary"]["missing_area"], 0)
        self.assertAlmostEqual(result["boundary"]["extra_area"], 0)

    def test_boundary_accepts_multipart_roi(self):
        cells = rectangle("west", 0, 0, 1, 1) + rectangle("east", 3, 0, 4, 1)
        geom = {"type": "MultiPolygon", "coordinates": [
            [[(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]],
            [[(3, 0), (4, 0), (4, 1), (3, 1), (3, 0)]]]}
        self.assertEqual(self.enrich(cells, roi(geom))["boundary"]["status"], "PASS")

    def test_missing_roi_and_crs_mismatch_are_explicit(self):
        cells = rectangle("c", 0, 0, 1, 1)
        missing = self.enrich(cells, None)
        self.assertEqual(missing["boundary"]["status"], "NOT_AVAILABLE")
        self.assertIn("Original ROI", missing["boundary"]["reason"])
        mismatch = self.enrich(cells, roi(polygon_geometry([[(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]]), "EPSG:32648"))
        self.assertEqual(mismatch["boundary"]["status"], "NOT_AVAILABLE")
        self.assertIn("CRS differ", mismatch["boundary"]["reason"])

    def test_size_crs_mismatch_does_not_infer_or_relabel(self):
        cells = rectangle("c", 0, 0, 1, 1)
        field = {"definition": "gmsh_characteristic_length", "crs_wkt": "EPSG:32648", "uniform": 1}
        result = self.enrich(cells, None, field)
        self.assertEqual(result["size"]["status"], "NOT_AVAILABLE")
        self.assertIn("CRS differs", result["size"]["reason"])

    def test_uniform_gmsh_size_is_reported_as_diagnostic(self):
        cells = [tri("one", [[0, 0], [1, 0], [0, 1]])]
        field = {"definition": "gmsh_characteristic_length", "crs_wkt": CRS,
                 "uniform": 1.0, "source_sha256": "a" * 64}
        result = self.enrich(cells, None, field)
        self.assertEqual(result["size"]["status"], "WARNING")
        self.assertEqual(result["elements"][0]["target_size"], 1.0)
        self.assertAlmostEqual(result["elements"][0]["size_ratio"], 2 ** 0.5)
        self.assertIn("not a guaranteed maximum edge", result["size"]["interpretation"])

    def test_retained_per_cell_targets_drive_ratios_and_reference_hash(self):
        cells = [tri("one", [[0, 0], [1, 0], [0, 1]])]
        field = {"definition": "gmsh_characteristic_length", "crs_wkt": CRS,
                 "target_sizes": {"one": 2.0}, "source_sha256": "b" * 64}
        a = self.enrich(cells, None, field)
        changed = dict(field, target_sizes={"one": 4.0})
        b = self.enrich(cells, None, changed)
        self.assertEqual(a["size"]["status"], "PASS")
        self.assertEqual(a["elements"][0]["target_size"], 2.0)
        self.assertAlmostEqual(a["elements"][0]["size_ratio"], 2 ** -0.5)
        self.assertNotEqual(a["reference_hash"], b["reference_hash"])
        self.assertEqual(a["size"]["source_sha256"], "b" * 64)

    def test_context_configuration_requires_finite_values(self):
        with self.assertRaises(ValueError):
            context_config({"size_tolerance": float("nan")})
        with self.assertRaises(ValueError):
            context_config({"extra": 1})

    def test_boundary_assessment_honors_job_cancellation(self):
        cells = rectangle("c", 0, 0, 1, 1)
        control = JobControl()
        control.request_cancel()
        with job_scope(control), self.assertRaises(Cancelled):
            self.enrich(cells, roi(polygon_geometry([[(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]])))


if __name__ == "__main__":
    unittest.main()
