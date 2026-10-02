from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
REPOSITORY_ROOT = PROJECT_ROOT.parents[1]
GMSH_EXECUTABLE = (
    REPOSITORY_ROOT
    / "Test_chialuoi"
    / "gmsh-4.15.2-Windows64"
    / "gmsh.exe"
)


class SyntheticDomainTests(unittest.TestCase):
    def test_v_valley_has_a_lower_symmetric_centerline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            config_path = workspace / "v-valley.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-run-config-v0",
                        "run_name": "gmsh-v-valley-250m",
                        "generator": {
                            "name": "gmsh",
                            "version": "auto",
                            "executable": str(GMSH_EXECUTABLE),
                            "parameters": {
                                "target_edge": 250.0,
                                "algorithm": 5,
                                "random_seed": 1,
                            },
                        },
                        "domain": {
                            "name": "v-valley",
                            "kind": "synthetic_v_valley",
                            "width": 1000.0,
                            "height": 1000.0,
                            "base_elevation": 100.0,
                            "downstream_slope": 0.01,
                            "cross_slope": 0.02,
                        },
                        "crs": "EPSG:32649",
                        "coordinate_units": "m",
                        "state_units": {"volume": "m3", "depth": "m"},
                        "output_root": str(output_root),
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(SOURCE_ROOT)

            result = subprocess.run(
                [sys.executable, "-m", "mesh_research", "run", str(config_path)],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            run_directory = next(output_root.iterdir())
            mesh = json.loads((run_directory / "mesh" / "mesh.json").read_text("utf-8"))
            qa = json.loads((run_directory / "qa" / "qa.json").read_text("utf-8"))

            def nearest(x: float, y: float) -> list[float]:
                return min(
                    mesh["vertices"],
                    key=lambda vertex: (vertex[0] - x) ** 2 + (vertex[1] - y) ** 2,
                )

            left = nearest(0.0, 500.0)
            center = nearest(500.0, 500.0)
            right = nearest(1000.0, 500.0)
            self.assertLess(center[2], left[2])
            self.assertLess(center[2], right[2])
            self.assertAlmostEqual(left[2], right[2], places=9)
            self.assertIn(
                {
                    "name": "v_valley_centerline_lower_than_sides",
                    "status": "pass",
                },
                qa["domain_checks"],
            )

    def test_radial_bowl_has_a_lower_center_and_symmetric_corners(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            config_path = workspace / "radial-bowl.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-run-config-v0",
                        "run_name": "gmsh-radial-bowl-250m",
                        "generator": {
                            "name": "gmsh",
                            "version": "auto",
                            "executable": str(GMSH_EXECUTABLE),
                            "parameters": {
                                "target_edge": 250.0,
                                "algorithm": 5,
                                "random_seed": 1,
                            },
                        },
                        "domain": {
                            "name": "radial-bowl",
                            "kind": "synthetic_radial_bowl",
                            "width": 1000.0,
                            "height": 1000.0,
                            "base_elevation": 100.0,
                            "curvature": 0.00004,
                        },
                        "crs": "EPSG:32649",
                        "coordinate_units": "m",
                        "state_units": {"volume": "m3", "depth": "m"},
                        "output_root": str(output_root),
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(SOURCE_ROOT)
            result = subprocess.run(
                [sys.executable, "-m", "mesh_research", "run", str(config_path)],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            run_directory = next(output_root.iterdir())
            mesh = json.loads((run_directory / "mesh" / "mesh.json").read_text("utf-8"))
            qa = json.loads((run_directory / "qa" / "qa.json").read_text("utf-8"))

            def nearest(x: float, y: float) -> list[float]:
                return min(
                    mesh["vertices"],
                    key=lambda vertex: (vertex[0] - x) ** 2 + (vertex[1] - y) ** 2,
                )

            center = nearest(500.0, 500.0)
            corners = [
                nearest(0.0, 0.0),
                nearest(1000.0, 0.0),
                nearest(1000.0, 1000.0),
                nearest(0.0, 1000.0),
            ]
            self.assertTrue(all(center[2] < corner[2] for corner in corners))
            self.assertAlmostEqual(min(corner[2] for corner in corners), max(corner[2] for corner in corners), places=9)
            self.assertIn(
                {
                    "name": "radial_bowl_center_lower_than_boundary",
                    "status": "pass",
                },
                qa["domain_checks"],
            )

    def test_y_drainage_surface_has_lower_trunk_and_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            config_path = workspace / "y-drainage.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-run-config-v0",
                        "run_name": "gmsh-y-drainage-125m",
                        "generator": {
                            "name": "gmsh",
                            "version": "auto",
                            "executable": str(GMSH_EXECUTABLE),
                            "parameters": {
                                "target_edge": 125.0,
                                "algorithm": 5,
                                "random_seed": 1,
                            },
                        },
                        "domain": {
                            "name": "y-drainage",
                            "kind": "synthetic_y_drainage",
                            "width": 1000.0,
                            "height": 1000.0,
                            "base_elevation": 100.0,
                            "downstream_slope": 0.005,
                            "cross_slope": 0.02,
                        },
                        "crs": "EPSG:32649",
                        "coordinate_units": "m",
                        "state_units": {"volume": "m3", "depth": "m"},
                        "output_root": str(output_root),
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(SOURCE_ROOT)
            result = subprocess.run(
                [sys.executable, "-m", "mesh_research", "run", str(config_path)],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            run_directory = next(output_root.iterdir())
            mesh = json.loads((run_directory / "mesh" / "mesh.json").read_text("utf-8"))
            qa = json.loads((run_directory / "qa" / "qa.json").read_text("utf-8"))

            def elevation_near(x: float, y: float) -> float:
                return min(
                    mesh["vertices"],
                    key=lambda vertex: (vertex[0] - x) ** 2 + (vertex[1] - y) ** 2,
                )[2]

            self.assertLess(elevation_near(500, 250), elevation_near(250, 250))
            self.assertLess(elevation_near(375, 750), elevation_near(125, 750))
            self.assertLess(elevation_near(625, 750), elevation_near(875, 750))
            self.assertIn(
                {"name": "y_drainage_channels_lower_than_sides", "status": "pass"},
                qa["domain_checks"],
            )

    def test_synthetic_matrix_runs_four_domains_at_three_refinements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "matrix-runs"
            config_path = workspace / "synthetic-matrix.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-matrix-config-v0",
                        "run_name": "gmsh-synthetic-matrix",
                        "generator": {
                            "name": "gmsh",
                            "version": "auto",
                            "executable": str(GMSH_EXECUTABLE),
                            "parameters": {"algorithm": 5, "random_seed": 1},
                        },
                        "domains": [
                            {
                                "name": "inclined-plane",
                                "kind": "synthetic_inclined_plane",
                                "width": 1000.0,
                                "height": 1000.0,
                                "base_elevation": 100.0,
                                "slope_x": 0.01,
                                "slope_y": 0.005,
                            },
                            {
                                "name": "v-valley",
                                "kind": "synthetic_v_valley",
                                "width": 1000.0,
                                "height": 1000.0,
                                "base_elevation": 100.0,
                                "downstream_slope": 0.01,
                                "cross_slope": 0.02,
                            },
                            {
                                "name": "radial-bowl",
                                "kind": "synthetic_radial_bowl",
                                "width": 1000.0,
                                "height": 1000.0,
                                "base_elevation": 100.0,
                                "curvature": 0.00004,
                            },
                            {
                                "name": "y-drainage",
                                "kind": "synthetic_y_drainage",
                                "width": 1000.0,
                                "height": 1000.0,
                                "base_elevation": 100.0,
                                "downstream_slope": 0.005,
                                "cross_slope": 0.02,
                            },
                        ],
                        "refinements": [
                            {"name": "coarse", "target_edge": 500.0},
                            {"name": "medium", "target_edge": 250.0},
                            {"name": "fine", "target_edge": 125.0},
                        ],
                        "crs": "EPSG:32649",
                        "coordinate_units": "m",
                        "state_units": {"volume": "m3", "depth": "m"},
                        "output_root": str(output_root),
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(SOURCE_ROOT)

            result = subprocess.run(
                [sys.executable, "-m", "mesh_research", "run", str(config_path)],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            matrix_directory = next(output_root.iterdir())
            summary = json.loads(
                (matrix_directory / "synthetic-matrix-summary.json").read_text("utf-8")
            )
            self.assertEqual(summary["schema_version"], "synthetic-matrix-summary-v0")
            self.assertEqual(len(summary["runs"]), 12)
            self.assertTrue(all(row["status"] == "pass" for row in summary["runs"]))
            for domain_name in ("inclined-plane", "v-valley", "radial-bowl", "y-drainage"):
                rows = {
                    row["refinement"]: row
                    for row in summary["runs"]
                    if row["domain"] == domain_name
                }
                self.assertLess(rows["coarse"]["cell_count"], rows["medium"]["cell_count"])
                self.assertLess(rows["medium"]["cell_count"], rows["fine"]["cell_count"])
                self.assertIn("minimum_angle_degrees", rows["fine"]["quality"])
                self.assertGreater(rows["fine"]["peak_rss_bytes"], 0)

    def test_failed_synthetic_expectation_fails_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            config_path = workspace / "invalid-v-valley.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-run-config-v0",
                        "run_name": "invalid-v-valley",
                        "generator": {
                            "name": "gmsh",
                            "version": "auto",
                            "executable": str(GMSH_EXECUTABLE),
                            "parameters": {
                                "target_edge": 250.0,
                                "algorithm": 5,
                                "random_seed": 1,
                            },
                        },
                        "domain": {
                            "name": "invalid-v-valley",
                            "kind": "synthetic_v_valley",
                            "width": 1000.0,
                            "height": 1000.0,
                            "base_elevation": 100.0,
                            "downstream_slope": 0.01,
                            "cross_slope": -0.02,
                        },
                        "crs": "EPSG:32649",
                        "coordinate_units": "m",
                        "state_units": {"volume": "m3", "depth": "m"},
                        "output_root": str(output_root),
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(SOURCE_ROOT)
            result = subprocess.run(
                [sys.executable, "-m", "mesh_research", "run", str(config_path)],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

            self.assertNotEqual(result.returncode, 0)
            run_directory = next(output_root.iterdir())
            manifest = json.loads((run_directory / "manifest.json").read_text("utf-8"))
            qa = json.loads((run_directory / "qa" / "qa.json").read_text("utf-8"))
            self.assertEqual(manifest["status"], "fail")
            self.assertEqual(qa["status"], "fail")
            self.assertIn("v_valley_centerline_lower_than_sides", manifest["errors"])


if __name__ == "__main__":
    unittest.main()
