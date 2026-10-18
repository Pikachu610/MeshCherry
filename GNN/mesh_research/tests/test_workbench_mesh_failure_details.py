import json
from pathlib import Path
import tempfile
import unittest

from gis_mesh_workbench.runner import run, write_json


class MeshFailureDetailsTests(unittest.TestCase):
    def test_mesh_only_overlap_failure_has_structured_manifest_qa_and_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mesh = root / 'overlap.json'
            write_json(mesh, {
                'schema_version': 'mesh-artifact-v0',
                'crs': 'EPSG:32649',
                'coordinate_units': 'm',
                'vertices': [[0, 0], [2, 0], [0, 2]],
                'triangles': [[0, 1, 2], [0, 1, 2]],
            })
            config = root / 'config.json'
            write_json(config, {
                'schema_version': 'gis-workbench-run-config-v2',
                'operation': 'mesh_only',
                'extraction_engine': 'indexed_windowed_v1',
                'run_name': 'overlap-mesh-only',
                'analysis_crs': 'EPSG:32649',
                'output_root': str(root / 'outputs'),
                'cell_source': {
                    'kind': 'mesh_artifact',
                    'path': str(mesh),
                    'max_cells': 1000,
                },
            })

            result = run(config)
            output = Path(result['output'])
            manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
            qa = json.loads((output / 'qa.json').read_text(encoding='utf-8'))

            self.assertEqual(result['status'], 'fail')
            self.assertEqual(result['error'], manifest['error'])
            self.assertEqual(manifest['failed_stage'], 'preflight')
            self.assertEqual(manifest['failure']['code'], 'cell_overlap', manifest['error'])
            self.assertEqual(qa['failed_stage'], manifest['failed_stage'])
            self.assertEqual(qa['error'], manifest['error'])
            self.assertEqual(qa['failure'], manifest['failure'])
            self.assertEqual(result['failure'], manifest['failure'])


if __name__ == '__main__':
    unittest.main()
