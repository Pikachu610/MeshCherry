import hashlib
import json
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import gis_mesh_workbench.quality_jobs as quality_jobs
from gis_mesh_workbench.document_api import open_project
from gis_mesh_workbench.project_io import save_project
from gis_mesh_workbench.quality_jobs import quality_page, start_quality
from gis_mesh_workbench.webui import WorkbenchServer


class _Server:
    def __init__(self):
        self.lock = threading.RLock()
        self.quality_jobs = {}
        self.quality_latest = {
            "mesh": {
                "mesh_hash": "mesh-hash",
                "elements": [
                    {"element_id": "c", "quality_q": 0.3},
                    {"element_id": "a", "quality_q": 0.1},
                    {"element_id": "b", "quality_q": 0.2},
                ],
                "issues": [],
            }
        }


class QualityReadPerformanceTests(unittest.TestCase):
    def test_verified_order_is_reused_and_client_rows_are_defensive_copies(self):
        server = _Server()
        report = server.quality_latest["mesh"]
        with patch("gis_mesh_workbench.quality_jobs._current_report",
                   return_value=(report, "verified-sha256")), \
             patch("gis_mesh_workbench.quality_jobs._remember_order",
                   wraps=quality_jobs._remember_order) as remember:
            first = quality_page(server, {"job_id": "mesh", "sort_by": "quality_q", "limit": 2})
            first["items"][0]["element_id"] = "caller-mutated"
            second = quality_page(server, {"job_id": "mesh", "sort_by": "quality_q", "offset": 1, "limit": 2})
        self.assertEqual([row["element_id"] for row in second["items"]], ["b", "c"])
        self.assertEqual(report["elements"][1]["element_id"], "a")
        self.assertEqual(remember.call_count, 1)

    def test_report_identity_change_builds_a_new_order(self):
        server = _Server()
        report = server.quality_latest["mesh"]
        with patch("gis_mesh_workbench.quality_jobs._current_report",
                   side_effect=[(report, "sha-one"), (report, "sha-two")]), \
             patch("gis_mesh_workbench.quality_jobs._remember_order",
                   wraps=quality_jobs._remember_order) as remember:
            quality_page(server, {"job_id": "mesh", "sort_by": "quality_q"})
            quality_page(server, {"job_id": "mesh", "sort_by": "quality_q"})
        self.assertEqual(remember.call_count, 2)

    def test_order_cache_is_isolated_by_quality_job(self):
        server = _Server()
        report = server.quality_latest["mesh"]
        server.quality_jobs.update({"job-a": {"mesh_job_id": "mesh"},
                                    "job-b": {"mesh_job_id": "mesh"}})
        server.quality_latest.update({"job-a": report, "job-b": report})
        with patch("gis_mesh_workbench.quality_jobs._current_report",
                   return_value=(report, "same-report-sha")), \
             patch("gis_mesh_workbench.quality_jobs._remember_order",
                   wraps=quality_jobs._remember_order) as remember:
            quality_page(server, {"job_id": "job-a", "sort_by": "quality_q"})
            quality_page(server, {"job_id": "job-b", "sort_by": "quality_q"})
        self.assertEqual(remember.call_count, 2)

    def test_verified_disk_report_is_serialized_once_per_page(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "input.mcy"
            mesh = {"schema_version": "workbench-cells-v1", "crs_wkt": "EPSG:32649",
                    "coordinate_units": "m", "cells": [{"cell_id": "tri-1", "kind": "triangle",
                    "xy": [[0, 0], [1, 0], [0, 1]]}]}
            save_project(archive, cells=mesh, qa={"status": "pass"})
            server = WorkbenchServer(folder)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                parent = open_project(server, archive.read_bytes())["job_id"]
                started = start_quality(server, {"mesh_job_id": parent})
                deadline = time.monotonic() + 15
                while server.job(started["job_id"])["status"] in {"running", "stopping"}:
                    if time.monotonic() > deadline:
                        self.fail("Quality job did not finish")
                    time.sleep(0.01)
                with patch("gis_mesh_workbench.quality_jobs._json_bytes",
                           wraps=quality_jobs._json_bytes) as serialize:
                    page = quality_page(server, {"job_id": started["job_id"], "limit": 1})
                self.assertEqual(page["total"], 1)
                self.assertEqual(serialize.call_count, 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_cached_indices_never_bypass_same_size_same_mtime_file_checks(self):
        for target in ("cells", "manifest", "report", "roi", "geo", "size"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as folder:
                archive = Path(folder) / "input.mcy"
                mesh = {"schema_version": "workbench-cells-v1", "crs_wkt": "EPSG:32649",
                        "coordinate_units": "m", "cells": [{"cell_id": "tri-1", "kind": "triangle",
                        "xy": [[0, 0], [1, 0], [0, 1]]}]}
                save_project(archive, cells=mesh, qa={"status": "pass"})
                server = WorkbenchServer(folder)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    parent = open_project(server, archive.read_bytes())["job_id"]
                    output = Path(server.job(parent)["result"]["output"])
                    if target in {"roi", "geo", "size"}:
                        meshing = output / "meshing"
                        meshing.mkdir(parents=True)
                        references = {
                            "domain-boundary.json": json.dumps({"crs_wkt": "EPSG:32649", "geometry": {
                                "type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 1], [0, 0]]]}}),
                            "domain.geo": 'Field[1].F="250";',
                            "size.pos": 'View "h" { ST(0,0,0,1,0,0,0,1,0){10,20,30}; };',
                        }
                        sidecar = meshing / "domain-boundary.json"
                        geo = meshing / "domain.geo"
                        size = meshing / "size.pos"
                        for path, contents in ((sidecar, references["domain-boundary.json"]),
                                               (geo, references["domain.geo"]),
                                               (size, references["size.pos"])):
                            path.write_text(contents, encoding="utf-8")
                        manifest_path = output / "manifest.json"
                        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                        hashes = manifest.setdefault("artifact_hashes", {})
                        for path in (sidecar, geo, size):
                            hashes[f"meshing/{path.name}"] = hashlib.sha256(path.read_bytes()).hexdigest()
                        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                    started = start_quality(server, {"mesh_job_id": parent})
                    deadline = time.monotonic() + 15
                    while server.job(started["job_id"])["status"] in {"running", "stopping"}:
                        if time.monotonic() > deadline:
                            self.fail("Quality job did not finish")
                        time.sleep(0.01)
                    request = {"job_id": started["job_id"], "sort_by": "element_id", "limit": 1}
                    quality_page(server, request)  # Warm the sorted-index cache.
                    if target == "cells":
                        path = server.artifact(parent, "cells")
                        old = path.read_bytes()
                        changed = old.replace(b"tri-1", b"tri-2", 1)
                    elif target == "manifest":
                        path = server.artifact(parent, "manifest")
                        old = path.read_bytes()
                        changed = old.replace(b'"restored_project": true', b'"restored_project": null', 1)
                    elif target == "report":
                        path = Path(server.quality_latest_meta[started["job_id"]]["output"]) / "quality.json"
                        old = path.read_bytes()
                        changed = old.replace(b'"status": "PARTIAL"', b'"status": "WARNING"', 1)
                    elif target == "roi":
                        path = output / "meshing" / "domain-boundary.json"
                        old = path.read_bytes()
                        changed = old.replace(b'"Polygon"', b'"Polygox"', 1)
                    elif target == "geo":
                        path = output / "meshing" / "domain.geo"
                        old = path.read_bytes()
                        changed = old.replace(b"250", b"251", 1)
                    else:
                        path = output / "meshing" / "size.pos"
                        old = path.read_bytes()
                        changed = old.replace(b"{10,", b"{11,", 1)
                    self.assertNotEqual(changed, old, f"fixture did not mutate {target}")
                    self.assertEqual(len(changed), len(old), f"mutation changed {target} file size")
                    stat = path.stat()
                    path.write_bytes(changed)
                    import os
                    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                    with self.assertRaisesRegex(ValueError, "stale|verification"):
                        quality_page(server, request)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)

    def test_order_cache_evicts_oldest_entry_at_configured_bound(self):
        server = _Server()
        report = server.quality_latest["mesh"]
        allowed = ["element_id", "quality_q", "min_angle_deg", "max_angle_deg", "aspect_ratio"]
        with patch("gis_mesh_workbench.quality_jobs._current_report",
                   side_effect=[(report, f"sha-{index}") for index in range(len(allowed))]):
            for sort in allowed:
                quality_page(server, {"job_id": "mesh", "sort_by": sort})
        self.assertEqual(len(server.quality_order_cache), quality_jobs.MAX_QUALITY_ORDER_CACHE)
        self.assertNotIn(("mesh", "sha-0", "elements", "element_id", "asc", None),
                         server.quality_order_cache)


if __name__ == "__main__":
    unittest.main()
