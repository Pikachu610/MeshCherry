import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import rasterio
from affine import Affine

from gis_mesh_workbench.failure_details import describe_failure
from gis_mesh_workbench.runner import run


class FailureDetailsTests(unittest.TestCase):
    def test_overlap_failure_has_actionable_safe_report(self):
        failure = describe_failure('cells overlap in positive area')

        self.assertEqual(failure['code'], 'cell_overlap')
        self.assertEqual(failure['title'], 'Các ô lưới bị chồng lấn')
        self.assertIn('diện tích dương', failure['message'])
        self.assertTrue(failure['actions'])
        self.assertTrue(all(isinstance(item, str) for item in failure['actions']))

    def test_unknown_failure_is_safe_and_bounded(self):
        failure = describe_failure(r'failed reading D:\private\do-not-show.tif token=secret')

        self.assertEqual(failure['code'], 'unknown_failure')
        self.assertNotIn('do-not-show', json.dumps(failure, ensure_ascii=False))
        self.assertNotIn('secret', json.dumps(failure, ensure_ascii=False))
        strings = [failure['code'], failure['title'], failure['message'], *failure['actions']]
        self.assertLess(max(map(len, strings)), 500)

    def test_runner_propagates_failure_report_and_preserves_manifest_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raster = root / 'sample.tif'
            with rasterio.open(raster, 'w', driver='GTiff', width=2, height=1,
                               count=1, dtype='float64', crs='EPSG:32649',
                               transform=Affine(1, 0, 500000, 0, -1, 1000001)) as dst:
                dst.write(np.array([[10, 30]], dtype=float), 1)
            mesh = root / 'overlapping.json'
            mesh.write_text(json.dumps({
                'schema_version': 'mesh-artifact-v0',
                'crs': 'EPSG:32649',
                'coordinate_units': 'm',
                'vertices': [[500000, 1000000], [500002, 1000000],
                             [500000, 1000001], [500002, 1000001]],
                'triangles': [[0, 1, 2], [0, 1, 2]],
            }), encoding='utf-8')
            config_path = root / 'config.json'
            config_path.write_text(json.dumps({
                'schema_version': 'gis-workbench-run-config-v0',
                'run_name': 'overlap-fixture',
                'analysis_crs': 'EPSG:32649',
                'raster_path': 'sample.tif',
                'band': 1,
                'semantics': 'extensive',
                'value_units': 'test',
                'output_root': 'outputs',
                'cell_source': {'kind': 'mesh_artifact', 'path': 'overlapping.json'},
            }), encoding='utf-8')

            result = run(config_path)
            output = Path(result['output'])
            manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
            qa = json.loads((output / 'qa.json').read_text(encoding='utf-8'))

            self.assertEqual(result['status'], 'fail')
            self.assertEqual(manifest['error'], 'cells overlap in positive area')
            self.assertEqual(result['error'], manifest['error'])
            self.assertEqual(result['failure']['code'], 'cell_overlap')
            self.assertEqual(manifest['failure'], result['failure'])
            self.assertEqual(qa['failure'], result['failure'])
            self.assertIn('positive area', result['error'])


if __name__ == '__main__':
    unittest.main()
