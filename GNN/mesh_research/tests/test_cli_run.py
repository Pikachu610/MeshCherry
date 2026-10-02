from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
SCHEMA_ROOT = PROJECT_ROOT / "schemas"


def assert_matches_schema(
    test_case: unittest.TestCase,
    value: object,
    schema: dict[str, object],
    location: str = "$",
) -> None:
    expected_type = schema.get("type")
    python_types = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
    }
    if isinstance(expected_type, list):
        if value is None and "null" in expected_type:
            pass
        else:
            allowed_types = tuple(
                python_type
                for type_name in expected_type
                if type_name in python_types
                for python_type in (
                    python_types[type_name]
                    if isinstance(python_types[type_name], tuple)
                    else (python_types[type_name],)
                )
            )
            test_case.assertIsInstance(value, allowed_types, location)
    elif expected_type in python_types:
        test_case.assertIsInstance(value, python_types[expected_type], location)
    if "const" in schema:
        test_case.assertEqual(value, schema["const"], location)
    if isinstance(value, dict):
        for field in schema.get("required", []):
            test_case.assertIn(field, value, f"{location}.{field}")
        properties = schema.get("properties", {})
        for field, field_schema in properties.items():
            if field in value:
                assert_matches_schema(
                    test_case,
                    value[field],
                    field_schema,
                    f"{location}.{field}",
                )
    if isinstance(value, list):
        if "minItems" in schema:
            test_case.assertGreaterEqual(len(value), schema["minItems"], location)
        if "items" in schema:
            for index, item in enumerate(value):
                assert_matches_schema(
                    test_case,
                    item,
                    schema["items"],
                    f"{location}[{index}]",
                )


class MeshResearchCliTests(unittest.TestCase):
    def test_valid_fixture_run_writes_declared_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            config_path = workspace / "fixture-run.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-run-config-v0",
                        "run_name": "fixture-smoke",
                        "generator": {"name": "fixture", "version": "1"},
                        "domain": {"name": "unit-square", "kind": "fixture"},
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
                [
                    sys.executable,
                    "-m",
                    "mesh_research",
                    "run",
                    str(config_path),
                ],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            run_directories = list(output_root.iterdir())
            self.assertEqual(len(run_directories), 1)
            run_directory = run_directories[0]
            self.assertTrue((run_directory / "manifest.json").is_file())
            self.assertTrue((run_directory / "mesh" / "mesh.json").is_file())
            self.assertTrue((run_directory / "graph" / "graph.json").is_file())
            self.assertTrue((run_directory / "qa" / "qa.json").is_file())

    def test_repeated_runs_are_isolated_but_deterministic_artifacts_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            config_path = workspace / "fixture-run.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-run-config-v0",
                        "run_name": "repeatable-fixture",
                        "generator": {"name": "fixture", "version": "1"},
                        "domain": {"name": "unit-square", "kind": "fixture"},
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
            command = [
                sys.executable,
                "-m",
                "mesh_research",
                "run",
                str(config_path),
            ]

            first = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            second = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            run_directories = sorted(output_root.iterdir())
            self.assertEqual(len(run_directories), 2)
            manifests = [
                json.loads((run_directory / "manifest.json").read_text("utf-8"))
                for run_directory in run_directories
            ]
            for artifact_name in ("mesh/mesh.json", "graph/graph.json"):
                self.assertEqual(
                    manifests[0]["artifact_hashes"][artifact_name],
                    manifests[1]["artifact_hashes"][artifact_name],
                )

    def test_invalid_config_fails_with_a_diagnostic_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            config_path = workspace / "invalid-run.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-run-config-v0",
                        "run_name": "missing-crs",
                        "generator": {"name": "fixture", "version": "1"},
                        "domain": {"name": "unit-square", "kind": "fixture"},
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
                [
                    sys.executable,
                    "-m",
                    "mesh_research",
                    "run",
                    str(config_path),
                ],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            run_directories = list(output_root.iterdir())
            self.assertEqual(len(run_directories), 1)
            manifest = json.loads(
                (run_directories[0] / "manifest.json").read_text("utf-8")
            )
            self.assertEqual(manifest["status"], "fail")
            self.assertIn("crs", manifest["errors"])
            self.assertIn("crs", result.stderr)
            manifest_schema = json.loads(
                (SCHEMA_ROOT / "mesh-run-manifest-v0.schema.json").read_text("utf-8")
            )
            assert_matches_schema(self, manifest, manifest_schema)

    def test_manifest_records_verifiable_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            config_path = workspace / "provenance-run.json"
            config = {
                "schema_version": "mesh-run-config-v0",
                "run_name": "provenance-fixture",
                "generator": {"name": "fixture", "version": "1"},
                "domain": {"name": "unit-square", "kind": "fixture"},
                "crs": "EPSG:3857",
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
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            run_directory = next(output_root.iterdir())
            manifest = json.loads((run_directory / "manifest.json").read_text("utf-8"))
            self.assertEqual(manifest["schema_version"], "mesh-run-manifest-v0")
            self.assertEqual(manifest["crs"], "EPSG:3857")
            self.assertEqual(
                manifest["units"],
                {"coordinate": "m", "state": {"volume": "m3", "depth": "m"}},
            )
            self.assertEqual(manifest["generator"], {"name": "fixture", "version": "1"})
            self.assertEqual(manifest["domain"], {"name": "unit-square", "kind": "fixture"})
            self.assertEqual(
                manifest["inputs"][0],
                {
                    "role": "run_config",
                    "logical_path": str(config_path),
                    "sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
                },
            )
            for logical_path, recorded_hash in manifest["artifact_hashes"].items():
                self.assertEqual(
                    recorded_hash,
                    hashlib.sha256((run_directory / logical_path).read_bytes()).hexdigest(),
                )

    def test_successful_run_artifacts_match_their_public_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            config_path = workspace / "schema-run.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-run-config-v0",
                        "run_name": "schema-fixture",
                        "generator": {"name": "fixture", "version": "1"},
                        "domain": {"name": "unit-square", "kind": "fixture"},
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

            self.assertEqual(result.returncode, 0, result.stderr)
            run_directory = next(output_root.iterdir())
            artifact_schemas = {
                "manifest.json": "mesh-run-manifest-v0.schema.json",
                "mesh/mesh.json": "mesh-artifact-v0.schema.json",
                "graph/graph.json": "mesh-graph-v0.schema.json",
                "qa/qa.json": "mesh-qa-v0.schema.json",
            }
            for artifact_path, schema_path in artifact_schemas.items():
                artifact = json.loads((run_directory / artifact_path).read_text("utf-8"))
                schema = json.loads((SCHEMA_ROOT / schema_path).read_text("utf-8"))
                assert_matches_schema(self, artifact, schema)

    def test_unimplemented_generator_cannot_masquerade_as_fixture_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            output_root = workspace / "runs"
            config_path = workspace / "false-jigsaw-run.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "mesh-run-config-v0",
                        "run_name": "false-jigsaw",
                        "generator": {"name": "jigsaw", "version": "unknown"},
                        "domain": {"name": "unit-square", "kind": "fixture"},
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
            manifest = json.loads(
                (next(output_root.iterdir()) / "manifest.json").read_text("utf-8")
            )
            self.assertEqual(manifest["status"], "fail")
            self.assertEqual(
                manifest["errors"]["generator.name"],
                "unsupported generator: jigsaw",
            )
            self.assertFalse((next(output_root.iterdir()) / "mesh").exists())


if __name__ == "__main__":
    unittest.main()
