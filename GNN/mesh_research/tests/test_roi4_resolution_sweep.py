from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.test_cli_run import assert_matches_schema


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
SCHEMA_ROOT = PROJECT_ROOT / "schemas"
REPOSITORY_ROOT = PROJECT_ROOT.parents[1]
ROI4 = REPOSITORY_ROOT / "ROI" / "ROI4.shp"
GMSH = REPOSITORY_ROOT / "Test_chialuoi" / "gmsh-4.15.2-Windows64" / "gmsh.exe"


class Roi4ResolutionSweepTests(unittest.TestCase):
    def test_public_sweep_reports_matched_geometry_cost_and_evidence_state(self) -> None:
        self.assertTrue(ROI4.is_file(), ROI4)
        self.assertTrue(GMSH.is_file(), GMSH)
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary_directory:
            workspace = Path(temporary_directory)
            config_directory = workspace / "configs"
            config_directory.mkdir()
            output_root = workspace / "runs"
            config_path = config_directory / "roi4-sweep.json"
            config = {
                "schema_version": "mesh-matrix-config-v0",
                "matrix_kind": "roi4_resolution_sweep",
                "run_name": "gmsh-roi4-resolution-sweep-test",
                "generator": {
                    "name": "gmsh",
                    "version": "auto",
                    "executable": str(GMSH),
                    "parameters": {"algorithm": 5, "random_seed": 1},
                },
                "domains": [
                    {
                        "name": "ROI4",
                        "kind": "roi_boundary",
                        "boundary_source": os.path.relpath(ROI4, config_directory),
                        "simplification": {
                            "method": "rdp_closed_ring",
                            "tolerances": [10.0, 25.0, 50.0, 100.0, 200.0, 500.0],
                            "max_relative_area_difference": 0.005,
                            "max_hausdorff_distance": 250.0,
                            "hausdorff_sampling_interval": 25.0,
                        },
                    }
                ],
                "refinements": [
                    {"name": "250m", "target_edge": 250.0},
                    {"name": "500m", "target_edge": 500.0},
                    {"name": "1000m", "target_edge": 1000.0},
                ],
                "semantic_evidence": {
                    "status": "not_available",
                    "audit": [
                        {
                            "candidate": "workspace GeoTIFF inventory",
                            "status": "rejected",
                            "reason": "no source is proven to be derived from canonical ROI4 and supported by the remapper",
                        }
                    ],
                },
                "crs": "EPSG:32649",
                "coordinate_units": "m",
                "state_units": {"volume": "m3", "depth": "m"},
                "output_root": str(output_root),
            }
            config_path.write_text(json.dumps(config), encoding="utf-8")
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(SOURCE_ROOT)
            result = subprocess.run(
                [sys.executable, "-m", "mesh_research", "run", str(config_path)],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            sweep_directory = next(output_root.iterdir())
            summary_path = sweep_directory / "roi4-resolution-sweep.json"
            self.assertTrue(summary_path.is_file())
            summary = json.loads(summary_path.read_text("utf-8"))
            schema = json.loads(
                (SCHEMA_ROOT / "roi4-resolution-sweep-v0.schema.json").read_text("utf-8")
            )
            assert_matches_schema(self, summary, schema)
            self.assertEqual(summary["schema_version"], "roi4-resolution-sweep-v0")
            self.assertEqual(summary["semantic_evidence"]["status"], "not_available")
            self.assertEqual({row["target_edge"] for row in summary["runs"]}, {250.0, 500.0, 1000.0})
            self.assertTrue(all(row["status"] == "pass" for row in summary["runs"]))
            self.assertEqual(len({row["boundary"]["source_set_sha256"] for row in summary["runs"]}), 1)
            self.assertEqual(len({row["boundary"]["selected_tolerance"] for row in summary["runs"]}), 1)
            self.assertTrue(
                all(
                    row["boundary"]["watershed_divide_status"] == "UNVERIFIED"
                    and row["boundary"]["outlet_status"] == "UNVERIFIED"
                    for row in summary["runs"]
                )
            )
            by_target = {row["target_edge"]: row for row in summary["runs"]}
            self.assertGreater(by_target[250.0]["cell_count"], by_target[500.0]["cell_count"])
            self.assertGreater(by_target[500.0]["cell_count"], by_target[1000.0]["cell_count"])
            self.assertEqual(by_target[500.0]["cell_count"], 13515)
            self.assertEqual(by_target[500.0]["physical_interface_count"], 20459)
            self.assertEqual(by_target[500.0]["directed_edge_count"], 40172)
            self.assertTrue(all(row["wall_time_seconds"] > 0 for row in summary["runs"]))
            self.assertTrue(all(row["peak_rss_bytes"] > 0 for row in summary["runs"]))
            self.assertTrue(all(row["hard_gates"] for row in summary["runs"]))
            self.assertTrue(
                all(gate["status"] == "pass" for row in summary["runs"] for gate in row["hard_gates"])
            )
            self.assertEqual(summary["pareto"]["scope"], "mesh_geometry_resolution_vs_cost")
            self.assertFalse(summary["claims"]["hydrological_routing_validity"])


if __name__ == "__main__":
    unittest.main()
