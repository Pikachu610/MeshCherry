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


class GmshInclinedPlaneTests(unittest.TestCase):
    def test_gmsh_inclined_plane_exports_valid_cell_graph(self) -> None:
        self.assertTrue(GMSH_EXECUTABLE.is_file(), GMSH_EXECUTABLE)
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            config_path = workspace / "inclined-plane.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-run-config-v0",
                        "run_name": "gmsh-inclined-plane",
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
                            "name": "inclined-plane",
                            "kind": "synthetic_inclined_plane",
                            "width": 1000.0,
                            "height": 1000.0,
                            "base_elevation": 100.0,
                            "slope_x": 0.01,
                            "slope_y": 0.005,
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
            manifest = json.loads((run_directory / "manifest.json").read_text("utf-8"))
            mesh = json.loads((run_directory / "mesh" / "mesh.json").read_text("utf-8"))
            graph = json.loads((run_directory / "graph" / "graph.json").read_text("utf-8"))
            qa = json.loads((run_directory / "qa" / "qa.json").read_text("utf-8"))

            self.assertEqual(manifest["generator"]["name"], "gmsh")
            self.assertRegex(manifest["generator"]["version"], r"^4\.15\.2")
            self.assertEqual(manifest["generator"]["parameters"]["target_edge"], 250.0)
            self.assertGreaterEqual(manifest["resources"]["wall_time_seconds"], 0.0)
            self.assertGreater(manifest["resources"]["peak_rss_bytes"], 0)
            self.assertGreater(len(mesh["triangles"]), 10)
            self.assertEqual(len(graph["nodes"]), len(mesh["triangles"]))
            self.assertEqual(qa["status"], "pass")
            quality = qa["metrics"]["quality"]
            self.assertGreater(quality["area"]["min"], 0)
            self.assertGreaterEqual(quality["area"]["max"], quality["area"]["min"])
            self.assertGreater(quality["minimum_angle_degrees"]["min"], 0)
            self.assertLessEqual(quality["minimum_angle_degrees"]["max"], 60)
            self.assertGreaterEqual(quality["aspect_ratio"]["max"], 1)

            interior_interfaces = [
                interface
                for interface in graph["interfaces"]
                if interface["geometry_class"] == "INTERIOR_INTERFACE"
            ]
            directed_edges = graph["directed_edges"]
            self.assertGreater(len(interior_interfaces), 0)
            self.assertEqual(len(directed_edges), 2 * len(interior_interfaces))
            interface_ids = {item["interface_id"] for item in interior_interfaces}
            self.assertEqual(
                {edge["interface_id"] for edge in directed_edges},
                interface_ids,
            )
            triangle_vertices = [set(triangle) for triangle in mesh["triangles"]]
            adjacent_pairs = {
                tuple(
                    sorted(
                        (
                            int(edge["source"].removeprefix("cell-")),
                            int(edge["target"].removeprefix("cell-")),
                        )
                    )
                )
                for edge in directed_edges
            }
            for first, second in adjacent_pairs:
                self.assertEqual(
                    len(triangle_vertices[first] & triangle_vertices[second]),
                    2,
                )
            point_touch_pairs = {
                (first, second)
                for first in range(len(triangle_vertices))
                for second in range(first + 1, len(triangle_vertices))
                if len(triangle_vertices[first] & triangle_vertices[second]) == 1
            }
            self.assertGreater(len(point_touch_pairs), 0)
            self.assertTrue(point_touch_pairs.isdisjoint(adjacent_pairs))

    def test_invalid_topologies_fail_with_retained_diagnostics(self) -> None:
        cases = {
            "duplicate-cell": {
                "vertices": [[0, 0], [1, 0], [0, 1]],
                "triangles": [[0, 1, 2], [0, 1, 2]],
                "error_fragment": "duplicates another cell",
            },
            "zero-area-cell": {
                "vertices": [[0, 0], [1, 0], [2, 0]],
                "triangles": [[0, 1, 2]],
                "error_fragment": "non-positive area",
            },
            "inverted-cell": {
                "vertices": [[0, 0], [1, 0], [0, 1]],
                "triangles": [[0, 2, 1]],
                "error_fragment": "inverted",
            },
            "non-manifold-edge": {
                "vertices": [[0, 0], [1, 0], [0, 1], [0, -1], [0.5, 1]],
                "triangles": [[0, 1, 2], [1, 0, 3], [0, 1, 4]],
                "error_fragment": "non-manifold",
            },
        }
        for case_name, case in cases.items():
            with self.subTest(case=case_name), tempfile.TemporaryDirectory() as temporary_directory:
                workspace = Path(temporary_directory)
                output_root = workspace / "runs"
                config_path = workspace / f"{case_name}.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "mesh-run-config-v0",
                            "run_name": case_name,
                            "generator": {"name": "fixture", "version": "1"},
                            "domain": {
                                "name": case_name,
                                "kind": "fixture",
                                "mesh": {
                                    "vertices": case["vertices"],
                                    "triangles": case["triangles"],
                                },
                            },
                            "crs": "EPSG:3857",
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
                )

                self.assertNotEqual(result.returncode, 0)
                run_directory = next(output_root.iterdir())
                manifest = json.loads(
                    (run_directory / "manifest.json").read_text("utf-8")
                )
                qa = json.loads((run_directory / "qa" / "qa.json").read_text("utf-8"))
                self.assertEqual(manifest["status"], "fail")
                self.assertEqual(qa["status"], "fail")
                self.assertIn(case["error_fragment"], " ".join(manifest["errors"]))
                self.assertIn(case["error_fragment"], result.stderr)

    def test_representative_gmsh_rerun_is_topologically_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            config_path = workspace / "stable-gmsh.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-run-config-v0",
                        "run_name": "stable-gmsh",
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
                            "name": "inclined-plane",
                            "kind": "synthetic_inclined_plane",
                            "width": 1000.0,
                            "height": 1000.0,
                            "base_elevation": 100.0,
                            "slope_x": 0.01,
                            "slope_y": 0.005,
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
            command = [sys.executable, "-m", "mesh_research", "run", str(config_path)]

            results = [
                subprocess.run(
                    command,
                    cwd=PROJECT_ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                for _ in range(2)
            ]

            self.assertTrue(all(result.returncode == 0 for result in results))
            manifests = [
                json.loads((run / "manifest.json").read_text("utf-8"))
                for run in sorted(output_root.iterdir())
            ]
            for artifact in ("mesh/mesh.json", "graph/graph.json", "qa/qa.json"):
                self.assertEqual(
                    manifests[0]["artifact_hashes"][artifact],
                    manifests[1]["artifact_hashes"][artifact],
                )


if __name__ == "__main__":
    unittest.main()
