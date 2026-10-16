import hashlib
import tempfile
import unittest
from pathlib import Path

from gis_mesh_workbench._version import __version__
from gis_mesh_workbench.build_info import authored_manifest, json_bytes, make_build_info


class BuildIdentityTests(unittest.TestCase):
    def test_source_or_lock_change_changes_recorded_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('src/gis_mesh_workbench/a.py','tests/t.py','packaging/requirements-v2.lock.txt','pyproject.toml','README.md'):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('initial')
            initial=make_build_info(root)
            self.assertEqual(initial['app_version'],__version__)
            self.assertEqual(initial['authored_source_manifest_sha256'],hashlib.sha256(json_bytes(authored_manifest(root))).hexdigest())
            (root/'src/gis_mesh_workbench/a.py').write_text('changed')
            changed=make_build_info(root)
            self.assertNotEqual(initial['authored_source_manifest_sha256'],changed['authored_source_manifest_sha256'])
            self.assertEqual(initial['dependency_lock_sha256'],changed['dependency_lock_sha256'])
            (root/'packaging/requirements-v2.lock.txt').write_text('changed')
            self.assertNotEqual(initial['dependency_lock_sha256'],make_build_info(root)['dependency_lock_sha256'])

    def test_generated_graph_cache_is_not_authored_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('pyproject.toml','README.md','src/gis_mesh_workbench/graphify-out/cache.json'):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('test')
            self.assertEqual(len(authored_manifest(root)),2)
