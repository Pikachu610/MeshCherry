from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mesh_research.boundary import (
    densified_symmetric_hausdorff,
    derive_boundary,
    inventory_components,
    ring_topology,
    verify_component_hashes,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
REPOSITORY_ROOT = PROJECT_ROOT.parents[1]
ROI4 = REPOSITORY_ROOT / "ROI" / "ROI4.shp"
GMSH = REPOSITORY_ROOT / "Test_chialuoi" / "gmsh-4.15.2-Windows64" / "gmsh.exe"


def roi4_domain() -> dict[str, object]:
    return {
        "name": "ROI4",
        "kind": "roi_boundary",
        "boundary_source": str(ROI4),
        "simplification": {
            "method": "rdp_closed_ring",
            "tolerances": [10.0, 25.0, 50.0, 100.0, 200.0, 500.0],
            "max_relative_area_difference": 0.005,
            "max_hausdorff_distance": 250.0,
            "hausdorff_sampling_interval": 25.0,
        },
    }


class Roi4BoundaryTests(unittest.TestCase):
    def test_sweep_is_complete_deterministic_and_non_destructive(self) -> None:
        before = inventory_components(ROI4)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first = derive_boundary(roi4_domain(), root / "config.json", root / "first")
            second = derive_boundary(roi4_domain(), root / "config.json", root / "second")

        self.assertEqual(first["selected"], second["selected"])
        self.assertEqual(first["selected"]["selected_candidate"]["tolerance"], 200.0)
        candidates = first["sweep"]["candidates"]
        self.assertEqual(len(candidates), 6)
        required = {
            "method",
            "tolerance",
            "source_set_sha256",
            "topology_valid",
            "topology_result",
            "absolute_area_difference",
            "relative_area_difference",
            "hausdorff_distance",
            "hausdorff_distance_upper_bound",
            "hausdorff_method",
            "eligible",
        }
        self.assertTrue(all(required.issubset(candidate) for candidate in candidates))
        immutable, changed = verify_component_hashes(before)
        self.assertTrue(immutable, changed)
        after = inventory_components(ROI4)
        self.assertEqual(
            [(item["suffix"], item["sha256"]) for item in before],
            [(item["suffix"], item["sha256"]) for item in after],
        )

    def test_topology_gate_rejects_a_self_intersection(self) -> None:
        valid, reason = ring_topology([(0.0, 0.0), (3.0, 2.0), (0.0, 3.0), (2.0, 0.0)])
        self.assertFalse(valid)
        self.assertIn("self-intersection", reason)

    def test_symmetric_hausdorff_includes_candidate_chord_to_source_distance(self) -> None:
        source = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (2.0, 2.0), (0.0, 4.0)]
        candidate = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
        result = densified_symmetric_hausdorff(source, candidate, 0.25)
        self.assertGreater(result["second_to_first"], 1.0)
        self.assertGreaterEqual(result["estimate"], result["second_to_first"])
        self.assertEqual(result["additive_error_bound"], 0.125)

    def test_topology_gate_rejects_non_adjacent_touch_and_overlap(self) -> None:
        touching = [(0.0, 0.0), (3.0, 0.0), (3.0, 3.0), (1.0, 0.0), (0.0, 3.0)]
        overlapping = [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (1.0, 0.0), (3.0, 0.0), (0.0, 3.0)]
        for ring in (touching, overlapping):
            with self.subTest(ring=ring):
                valid, reason = ring_topology(ring)
                self.assertFalse(valid)
                self.assertIn("self-intersection", reason)

    def test_public_command_emits_roi4_baseline_contract(self) -> None:
        self.assertTrue(GMSH.is_file(), GMSH)
        before = inventory_components(ROI4)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_root = root / "runs"
            config_path = root / "roi4.json"
            config = {
                "schema_version": "mesh-run-config-v0",
                "run_name": "gmsh-roi4-500m-test",
                "generator": {
                    "name": "gmsh",
                    "version": "auto",
                    "executable": str(GMSH),
                    "parameters": {"target_edge": 500.0, "algorithm": 5, "random_seed": 1},
                },
                "domain": roi4_domain(),
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
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            run = next(output_root.iterdir())
            for relative in (
                "boundary/source.json",
                "boundary/sweep.json",
                "boundary/selected-boundary.json",
                "mesh/mesh.json",
                "graph/graph.json",
                "qa/qa.json",
                "manifest.json",
            ):
                self.assertTrue((run / relative).is_file(), relative)
            manifest = json.loads((run / "manifest.json").read_text("utf-8"))
            graph = json.loads((run / "graph" / "graph.json").read_text("utf-8"))
            qa = json.loads((run / "qa" / "qa.json").read_text("utf-8"))

            self.assertEqual(manifest["status"], "pass")
            self.assertEqual(manifest["boundary"]["selected_tolerance"], 200.0)
            recorded_inputs = {item["logical_path"]: item["sha256"] for item in manifest["inputs"]}
            for component in before:
                self.assertEqual(recorded_inputs[component["logical_path"]], component["sha256"])
            self.assertGreater(qa["metrics"]["cell_count"], 10000)
            self.assertLessEqual(qa["metrics"]["quality"]["edge_length"]["max"], 1500.0)
            self.assertEqual(qa["boundary_assessment"]["watershed_divide_status"], "UNVERIFIED")
            self.assertEqual(qa["boundary_assessment"]["outlet_status"], "UNVERIFIED")
            exterior = [item for item in graph["interfaces"] if item["geometry_class"] == "EXTERIOR_EDGE"]
            self.assertTrue(exterior)
            self.assertTrue(all(item["physical_boundary_status"] == "UNVERIFIED" for item in exterior))
            for relative, expected in manifest["artifact_hashes"].items():
                self.assertEqual(
                    hashlib.sha256((run / relative).read_bytes()).hexdigest(),
                    expected,
                )

        immutable, changed = verify_component_hashes(before)
        self.assertTrue(immutable, changed)


if __name__ == "__main__":
    unittest.main()
