import threading
import unittest
from unittest.mock import patch

from gis_mesh_workbench.quality_jobs import (
    MAX_PAGE_SIZE, _quality_config, quality_export, quality_page, quality_result,
)


class _Server:
    def __init__(self):
        self.lock = threading.RLock()
        self.quality_jobs = {}
        self.quality_latest = {}
        self.controls = {}


class QualityJobReadApiTests(unittest.TestCase):
    def setUp(self):
        self.server = _Server()
        self.server.quality_jobs["job"] = dict(status="pass", stage="done", result={"status": "pass"})
        self.server.quality_latest["mesh"] = {
            "schema_version": "meshcherry-quality-v1", "mesh_hash": "abc", "config_hash": "def",
            "status": "PASS", "summary": {}, "statistics": {}, "validation": {},
            "boundary": {}, "size": {}, "performance": {}, "definitions": {},
            "histograms": {}, "worst": {},
            "elements": [
                {"element_id": "b", "quality_q": 0.4},
                {"element_id": "a", "quality_q": 0.2},
                {"element_id": "c", "quality_q": None},
            ],
            "issues": [{"code": "Z"}, {"code": "A"}],
        }

    def test_quality_config_separates_context_from_core_and_rejects_unknown(self):
        core, context = _quality_config({"worst_limit": 7, "size_tolerance": 0.5})
        self.assertEqual(core["worst_limit"], 7)
        self.assertEqual(core["max_issues"], 10000)
        self.assertEqual(context["size_tolerance"], 0.5)
        with self.assertRaises(ValueError):
            _quality_config({"invented": 1})

    def test_result_is_copy_and_page_is_bounded_sorted_with_cursor(self):
        self.server.quality_jobs["job"]["mesh_job_id"] = "mesh"
        self.server.quality_latest["job"] = self.server.quality_latest["mesh"]
        with patch("gis_mesh_workbench.quality_jobs._current_report", side_effect=lambda server, parent, report, **kwargs: report):
            result = quality_result(self.server, {"job_id": "job"})
            result["summary"]["changed"] = True
            self.assertNotIn("changed", self.server.quality_latest["job"]["summary"])
            page = quality_page(self.server, {"job_id": "mesh", "kind": "elements", "sort_by": "element_id", "offset": 1, "limit": 1})
        self.assertEqual(self.server.quality_jobs["job"]["result"]["status"], "pass")
        self.assertEqual([row["element_id"] for row in page["items"]], ["b"])
        self.assertEqual(page["total"], 3)
        self.assertEqual(page["next_offset"], 2)
        with self.assertRaises(ValueError):
            quality_page(self.server, {"job_id": "mesh", "limit": MAX_PAGE_SIZE + 1})

    def test_issue_page_is_deterministic_and_export_returns_download_tuple(self):
        with patch("gis_mesh_workbench.quality_jobs._current_report", side_effect=lambda server, parent, report, **kwargs: report):
            page = quality_page(self.server, {"job_id": "mesh", "kind": "issues", "limit": 1})
            raw, content_type, filename = quality_export(self.server, {"job_id": "mesh", "format": "json"})
        self.assertEqual(page["items"][0]["code"], "A")
        self.assertIn(b"meshcherry-quality-v1", raw)
        self.assertEqual(content_type, "application/json; charset=utf-8")
        self.assertEqual(filename, "mesh-quality.json")

    def test_failed_quality_job_never_falls_back_to_previous_mesh_report(self):
        self.server.quality_jobs["failed-job"] = dict(status="cancelled", stage="cancelled",
            mesh_job_id="mesh", result={"status": "cancelled"})
        with self.assertRaisesRegex(ValueError, "No completed quality assessment"):
            quality_page(self.server, {"job_id": "failed-job", "kind": "elements"})
        with self.assertRaisesRegex(ValueError, "No completed quality assessment"):
            quality_export(self.server, {"job_id": "failed-job", "format": "json"})


if __name__ == "__main__":
    unittest.main()
