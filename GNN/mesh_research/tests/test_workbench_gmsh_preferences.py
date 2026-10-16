import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gis_mesh_workbench.gmsh_preferences import migrate_legacy, read_saved, save_checked


class GmshPreferenceTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.legacy=self.root/'old-workspace'/'gmsh.json'
        self.destination=self.root/'profile'/'MeshCherry'/'gmsh.json'
        self.report=dict(executable=str(self.root/'missing-tools'/'gmsh.exe'),
                         version='4.15.2',executable_sha256='a'*64)

    def test_migration_copies_without_execution_and_preserves_source(self):
        save_checked(self.legacy,self.report)
        before=self.legacy.read_bytes()
        with patch('gis_mesh_workbench.gmsh_meshing.inspect_executable') as inspect:
            migrate_legacy(self.legacy,self.destination)
            self.assertEqual(read_saved(self.destination),self.report)
            inspect.assert_not_called()
        self.assertEqual(self.legacy.read_bytes(),before)
        self.assertFalse(Path(self.report['executable']).exists())
        self.assertNotIn('ready',read_saved(self.destination))

    def test_existing_user_settings_even_malformed_are_not_clobbered(self):
        save_checked(self.legacy,self.report)
        self.destination.parent.mkdir(parents=True)
        for raw in ('{broken',json.dumps(dict(self.report,version='4.14.1'))):
            self.destination.write_text(raw,encoding='utf-8')
            migrate_legacy(self.legacy,self.destination)
            self.assertEqual(self.destination.read_text(encoding='utf-8'),raw)

    def test_malformed_or_relative_legacy_is_not_migrated(self):
        self.legacy.parent.mkdir(parents=True)
        for data in ('{broken','[]',json.dumps(dict(self.report,executable='gmsh.exe')),
                     json.dumps({'executable':self.report['executable']}),'x'*65537):
            self.legacy.write_text(data,encoding='utf-8')
            migrate_legacy(self.legacy,self.destination)
            self.assertFalse(self.destination.exists())

    def test_atomic_save_error_keeps_previous_good_settings(self):
        save_checked(self.destination,self.report)
        with patch.object(Path,'replace',side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):
                save_checked(self.destination,dict(self.report,version='4.14.1'))
        self.assertEqual(read_saved(self.destination),self.report)
        self.assertEqual(list(self.destination.parent.glob('.gmsh-*.tmp')),[])

    def test_no_clobber_publication_handles_concurrent_migration(self):
        save_checked(self.destination,self.report)
        with self.assertRaises(FileExistsError):
            save_checked(self.destination,dict(self.report,version='4.14.1'),only_if_absent=True)
        self.assertEqual(read_saved(self.destination),self.report)
        self.assertEqual(list(self.destination.parent.glob('.gmsh-*.tmp')),[])

    def test_saved_metadata_is_allowlisted_and_corrupt_user_file_is_safe(self):
        self.destination.parent.mkdir(parents=True)
        self.destination.write_text(json.dumps(dict(self.report,ready=True,token='not-authority')),encoding='utf-8')
        self.assertEqual(read_saved(self.destination),self.report)
        for raw in ('{broken','null','{}',json.dumps(dict(self.report,executable='gmsh.exe'))):
            self.destination.write_text(raw,encoding='utf-8')
            self.assertEqual(read_saved(self.destination),{})
