import csv
import io
import json
import unittest
import zipfile

from gis_mesh_workbench.quality_assessment import evaluate_mesh_quality
from gis_mesh_workbench.project_io import _cells_payload, quality_cache_state
from gis_mesh_workbench.quality_reports import ELEMENT_COLUMNS, export_quality
from gis_mesh_workbench.quality_benchmark import run_quality_benchmark


TRIANGLE = {
    "schema_version": "workbench-cells-v1",
    "coordinate_units": "m",
    "cells": [{"cell_id": "tri-1", "kind": "triangle",
               "xy": [[0, 0], [1, 0], [0, 1]]}],
}


class QualityReportTests(unittest.TestCase):
    def setUp(self):
        self.result = evaluate_mesh_quality(TRIANGLE)

    def test_json_export_is_finite_and_retains_raw_metrics(self):
        data, mime, filename = export_quality(self.result, "json")
        self.assertIn("application/json", mime)
        self.assertEqual(filename, "mesh-quality.json")
        parsed = json.loads(data)
        self.assertEqual(parsed["schema_version"], "meshcherry-quality-v1")
        element = parsed["elements"][0]
        self.assertAlmostEqual(element["quality_q"], 0.8284271247461901)
        self.assertEqual(parsed["performance"]["generation_time_seconds"], None)
        self.assertNotIn(b"NaN", data)

    def test_csv_bundle_has_stable_element_columns_and_issue_export(self):
        data, mime, filename = export_quality(self.result, "csv")
        self.assertEqual((mime, filename), ("application/zip", "mesh-quality-csv.zip"))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(set(archive.namelist()), {"elements.csv", "summary.csv", "issues.csv"})
            element_rows = list(csv.reader(io.StringIO(archive.read("elements.csv").decode("utf-8-sig"))))
            self.assertEqual(tuple(element_rows[0]), ELEMENT_COLUMNS)
            self.assertEqual(element_rows[1][0], "tri-1")
            self.assertEqual(element_rows[1][1], "VALID")
            issue_rows = list(csv.reader(io.StringIO(archive.read("issues.csv").decode("utf-8-sig"))))
            self.assertEqual(issue_rows[0][0:4], ["code", "severity", "category", "message"])

    def test_offline_html_escapes_untrusted_issue_text(self):
        result = dict(self.result)
        result["issues"] = [{"code": "BAD_TEXT", "severity": "warning",
                             "message": "<script>alert(1)</script>",
                             "element_ids": ["<img src=x>"], "details": {}}]
        data, mime, filename = export_quality(result, "html")
        content = data.decode("utf-8")
        self.assertIn("text/html", mime)
        self.assertEqual(filename, "mesh-quality.html")
        self.assertNotIn("<script>alert(1)</script>", content)
        self.assertIn("&lt;script&gt;", content)
        self.assertNotIn("https://", content)

    def test_unknown_schema_and_format_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "schema"):
            export_quality({**self.result, "schema_version": "unknown"}, "json")
        with self.assertRaisesRegex(ValueError, "format"):
            export_quality(self.result, "pdf")


class QualityBenchmarkTests(unittest.TestCase):
    def test_fixed_mesh_benchmark_separates_qa_and_pipeline_time(self):
        result = run_quality_benchmark(TRIANGLE, repetitions=2, warmup=1,
                                       memory_interval_seconds=0.001)
        self.assertEqual(result["schema_version"], "meshcherry-quality-benchmark-v1")
        self.assertEqual(result["requested_repetitions"], 2)
        self.assertEqual(result["warmup_runs"], 1)
        self.assertEqual(result["successful_runs"] + result["failed_runs"], 2)
        self.assertEqual(len(result["runs"]), 2)
        self.assertEqual({row["generation_time_seconds"] for row in result["runs"]}, {None})
        self.assertTrue(all(row["qa_time_seconds"] > 0 for row in result["runs"]))
        self.assertTrue(all(row["pipeline_time_seconds"] > row["qa_time_seconds"] for row in result["runs"]))
        for row in result["runs"]:
            self.assertGreaterEqual(row["pipeline_time_seconds"], row["qa_time_seconds"])
            self.assertEqual(row["mesh_hash"], result["runs"][0]["mesh_hash"])
        self.assertIn("median", result["aggregate"]["qa_time_seconds"])
        self.assertIn(result["memory"]["status"], {"MEASURED_SAMPLED", "NOT_AVAILABLE"})

    def test_generator_callback_reports_generation_and_qa_separately(self):
        calls = []
        def generate(index):
            calls.append(index)
            return TRIANGLE
        result = run_quality_benchmark(TRIANGLE, repetitions=2, warmup=1,
                                       generator=generate, memory_interval_seconds=0.001)
        self.assertEqual(calls, [0, 1, 2])
        self.assertEqual(result["successful_runs"], 2)
        self.assertTrue(all(row["generation_time_seconds"] >= 0 for row in result["runs"]))
        self.assertTrue(all(row["qa_time_seconds"] >= 0 for row in result["runs"]))

    def test_repetition_limits_prevent_unbounded_default_work(self):
        for repetitions, warmup in ((0, 0), (51, 0), (1, 6)):
            with self.subTest(repetitions=repetitions, warmup=warmup):
                with self.assertRaises(ValueError):
                    run_quality_benchmark(TRIANGLE, repetitions=repetitions, warmup=warmup)


class QualityProjectCacheTests(unittest.TestCase):
    def setUp(self):
        raw_cells = {"schema_version": "workbench-cells-v1", "crs_wkt": "EPSG:32648",
                     "coordinate_units": "m", "cells": [
                         {"cell_id": "tri-1", "kind": "triangle",
                          "xy": [[500000, 2000000], [500001, 2000000], [500000, 2000001]]}]}
        self.cells, _, _ = _cells_payload(raw_cells)
        self.assessment = evaluate_mesh_quality(self.cells)

    def test_cache_is_current_only_when_mesh_config_and_metric_version_match(self):
        current = quality_cache_state(self.assessment, self.cells)
        self.assertEqual(current["status"], "CURRENT")
        self.assertIs(current["assessment"], self.assessment)

        changed_cells = json.loads(json.dumps(self.cells))
        changed_cells["cells"][0]["xy"][1][0] += 0.25
        stale_mesh = quality_cache_state(self.assessment, changed_cells)
        self.assertEqual(stale_mesh["status"], "STALE")
        self.assertIn("mesh_hash", stale_mesh["reason"])

        changed_config = quality_cache_state(self.assessment, self.cells,
                                             {"warning_quality_q": 0.3})
        self.assertEqual(changed_config["status"], "STALE")
        self.assertIn("config_hash", changed_config["reason"])

        changed_version = dict(self.assessment, metric_definition_version="old")
        stale_version = quality_cache_state(changed_version, self.cells)
        self.assertEqual(stale_version["status"], "STALE")
        self.assertIn("metric_definition_version", stale_version["reason"])

    def test_missing_and_malformed_assessments_never_claim_current(self):
        self.assertEqual(quality_cache_state(None, self.cells)["status"], "NOT_AVAILABLE")
        malformed = quality_cache_state([], self.cells)
        self.assertEqual(malformed["status"], "STALE")
        self.assertIsNone(malformed["assessment"])


if __name__ == "__main__":
    unittest.main()
