from __future__ import annotations

import hashlib
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


class SemanticRemappingCliTests(unittest.TestCase):
    def test_intensive_raster_uses_overlap_area_and_round_trips_to_raster(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            raster_path = workspace / "rainfall.json"
            raster_path.write_text(
                json.dumps(
                    {
                        "schema_version": "synthetic-raster-v0",
                        "name": "rainfall",
                        "quantity_type": "intensive",
                        "units": "mm",
                        "grid": {
                            "x_min": 0.0,
                            "y_min": 0.0,
                            "cell_width": 0.5,
                            "cell_height": 0.5,
                            "row_order": "bottom_to_top",
                            "values": [[1.0, 2.0], [3.0, 4.0]],
                            "nodata": None,
                        },
                    }
                ),
                encoding="utf-8",
            )
            config_path = workspace / "remap-run.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-run-config-v0",
                        "run_name": "intensive-remap",
                        "generator": {"name": "fixture", "version": "1"},
                        "domain": {"name": "unit-square", "kind": "fixture"},
                        "crs": "EPSG:3857",
                        "coordinate_units": "m",
                        "state_units": {"volume": "m3", "depth": "m"},
                        "remapping": {
                            "datasets": [{"path": str(raster_path)}],
                            "conservation_tolerance": 1e-12,
                        },
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

            self.assertEqual(result.returncode, 0, result.stderr)
            run_directory = next(output_root.iterdir())
            remapping = json.loads(
                (run_directory / "remapping" / "rainfall.json").read_text("utf-8")
            )
            remapping_schema = json.loads(
                (SCHEMA_ROOT / "semantic-remapping-v0.schema.json").read_text("utf-8")
            )
            assert_matches_schema(self, remapping, remapping_schema)
            self.assertEqual(remapping["quantity_type"], "intensive")
            self.assertEqual(remapping["cell_values"], [2.25, 2.75])
            self.assertEqual(remapping["cell_coverage_fractions"], [1.0, 1.0])
            self.assertEqual(
                remapping["comparison_raster"]["values"],
                [[2.5, 2.25], [2.75, 2.5]],
            )
            self.assertEqual(remapping["source_sha256"], hashlib.sha256(raster_path.read_bytes()).hexdigest())
            manifest = json.loads((run_directory / "manifest.json").read_text("utf-8"))
            self.assertIn(
                {
                    "role": "remapping_raster",
                    "logical_path": str(raster_path),
                    "sha256": hashlib.sha256(raster_path.read_bytes()).hexdigest(),
                },
                manifest["inputs"],
            )

    def test_extensive_raster_preserves_total_in_both_directions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            raster_path = workspace / "water-volume.json"
            raster_path.write_text(
                json.dumps(
                    {
                        "schema_version": "synthetic-raster-v0",
                        "name": "water-volume",
                        "quantity_type": "extensive",
                        "units": "m3",
                        "grid": {
                            "x_min": 0.0,
                            "y_min": 0.0,
                            "cell_width": 0.5,
                            "cell_height": 0.5,
                            "row_order": "bottom_to_top",
                            "values": [[10.0, 20.0], [30.0, 40.0]],
                            "nodata": None,
                        },
                    }
                ),
                encoding="utf-8",
            )
            config_path = workspace / "remap-run.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-run-config-v0",
                        "run_name": "extensive-remap",
                        "generator": {"name": "fixture", "version": "1"},
                        "domain": {"name": "unit-square", "kind": "fixture"},
                        "crs": "EPSG:3857",
                        "coordinate_units": "m",
                        "state_units": {"volume": "m3", "depth": "m"},
                        "remapping": {
                            "datasets": [{"path": str(raster_path)}],
                            "conservation_tolerance": 1e-12,
                        },
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

            self.assertEqual(result.returncode, 0, result.stderr)
            run_directory = next(output_root.iterdir())
            remapping = json.loads(
                (run_directory / "remapping" / "water-volume.json").read_text("utf-8")
            )
            self.assertEqual(remapping["cell_values"], [45.0, 55.0])
            self.assertEqual(sum(remapping["cell_values"]), 100.0)
            self.assertEqual(
                sum(sum(row) for row in remapping["comparison_raster"]["values"]),
                100.0,
            )
            self.assertEqual(remapping["conservation"]["source_total"], 100.0)
            self.assertEqual(remapping["conservation"]["mesh_total"], 100.0)
            self.assertEqual(remapping["conservation"]["comparison_total"], 100.0)
            self.assertEqual(remapping["conservation"]["residual"], 0.0)
            qa = json.loads((run_directory / "qa" / "qa.json").read_text("utf-8"))
            self.assertIn(
                {
                    "name": "water-volume",
                    "quantity_type": "extensive",
                    "status": "pass",
                    "minimum_cell_coverage_fraction": 1.0,
                    "mean_cell_coverage_fraction": 1.0,
                    "conservation": {
                        "source_total": 100.0,
                        "mesh_total": 100.0,
                        "comparison_total": 100.0,
                        "residual": 0.0,
                        "tolerance": 1e-12,
                    },
                },
                qa["remapping_checks"],
            )

    def test_extensive_conservation_uses_only_the_mesh_footprint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            raster_path = workspace / "larger-raster.json"
            raster_path.write_text(
                json.dumps(
                    {
                        "schema_version": "synthetic-raster-v0",
                        "name": "larger-raster",
                        "quantity_type": "extensive",
                        "units": "m3",
                        "grid": {
                            "x_min": 0.0,
                            "y_min": 0.0,
                            "cell_width": 1.0,
                            "cell_height": 1.0,
                            "row_order": "bottom_to_top",
                            "values": [[10.0, 90.0]],
                            "nodata": None,
                        },
                    }
                ),
                encoding="utf-8",
            )
            config_path = workspace / "remap-run.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-run-config-v0",
                        "run_name": "mesh-footprint-conservation",
                        "generator": {"name": "fixture", "version": "1"},
                        "domain": {"name": "unit-square", "kind": "fixture"},
                        "crs": "EPSG:3857",
                        "coordinate_units": "m",
                        "state_units": {"volume": "m3", "depth": "m"},
                        "remapping": {
                            "datasets": [{"path": str(raster_path)}],
                            "conservation_tolerance": 1e-12,
                        },
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

            self.assertEqual(result.returncode, 0, result.stderr)
            run_directory = next(output_root.iterdir())
            remapping = json.loads(
                (run_directory / "remapping" / "larger-raster.json").read_text("utf-8")
            )
            self.assertEqual(remapping["conservation"]["source_total"], 10.0)
            self.assertEqual(remapping["conservation"]["mesh_total"], 10.0)
            self.assertEqual(remapping["conservation"]["comparison_total"], 10.0)
            self.assertEqual(remapping["conservation"]["residual"], 0.0)

    def test_categorical_raster_retains_class_fractions_and_majority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            raster_path = workspace / "land-cover.json"
            raster_path.write_text(
                json.dumps(
                    {
                        "schema_version": "synthetic-raster-v0",
                        "name": "land-cover",
                        "quantity_type": "categorical",
                        "units": "class",
                        "grid": {
                            "x_min": 0.0,
                            "y_min": 0.0,
                            "cell_width": 0.5,
                            "cell_height": 0.5,
                            "row_order": "bottom_to_top",
                            "values": [["forest", "urban"], ["water", "urban"]],
                            "nodata": None,
                        },
                    }
                ),
                encoding="utf-8",
            )
            config_path = workspace / "remap-run.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-run-config-v0",
                        "run_name": "categorical-remap",
                        "generator": {"name": "fixture", "version": "1"},
                        "domain": {"name": "unit-square", "kind": "fixture"},
                        "crs": "EPSG:3857",
                        "coordinate_units": "m",
                        "state_units": {"volume": "m3", "depth": "m"},
                        "remapping": {"datasets": [{"path": str(raster_path)}]},
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

            self.assertEqual(result.returncode, 0, result.stderr)
            run_directory = next(output_root.iterdir())
            remapping = json.loads(
                (run_directory / "remapping" / "land-cover.json").read_text("utf-8")
            )
            self.assertEqual(
                remapping["cell_class_fractions"],
                [
                    {"forest": 0.25, "urban": 0.75},
                    {"forest": 0.25, "urban": 0.25, "water": 0.5},
                ],
            )
            self.assertEqual(remapping["cell_majority_classes"], ["urban", "water"])
            for fractions in remapping["cell_class_fractions"]:
                self.assertAlmostEqual(sum(fractions.values()), 1.0)
            comparison = remapping["comparison_raster"]
            self.assertEqual(comparison["values"], [["urban", "urban"], ["water", "urban"]])
            for row in comparison["class_fractions"]:
                for fractions in row:
                    self.assertAlmostEqual(sum(fractions.values()), 1.0)

    def test_nodata_reduces_coverage_and_is_not_converted_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            raster_path = workspace / "rainfall-with-gap.json"
            raster_path.write_text(
                json.dumps(
                    {
                        "schema_version": "synthetic-raster-v0",
                        "name": "rainfall-with-gap",
                        "quantity_type": "intensive",
                        "units": "mm",
                        "grid": {
                            "x_min": 0.0,
                            "y_min": 0.0,
                            "cell_width": 0.5,
                            "cell_height": 0.5,
                            "row_order": "bottom_to_top",
                            "values": [[1.0, None], [3.0, 4.0]],
                            "nodata": None,
                        },
                    }
                ),
                encoding="utf-8",
            )
            config_path = workspace / "remap-run.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-run-config-v0",
                        "run_name": "nodata-remap",
                        "generator": {"name": "fixture", "version": "1"},
                        "domain": {"name": "unit-square", "kind": "fixture"},
                        "crs": "EPSG:3857",
                        "coordinate_units": "m",
                        "state_units": {"volume": "m3", "depth": "m"},
                        "remapping": {"datasets": [{"path": str(raster_path)}]},
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

            self.assertEqual(result.returncode, 0, result.stderr)
            run_directory = next(output_root.iterdir())
            remapping = json.loads(
                (run_directory / "remapping" / "rainfall-with-gap.json").read_text("utf-8")
            )
            self.assertEqual(remapping["cell_values"], [2.5, 2.75])
            self.assertEqual(remapping["cell_coverage_fractions"], [0.5, 1.0])
            self.assertIsNone(remapping["comparison_raster"]["values"][0][1])
            self.assertNotIn(0.0, remapping["cell_values"])


if __name__ == "__main__":
    unittest.main()
