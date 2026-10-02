import hashlib
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch, Mock

import numpy as np
import rasterio
from rasterio.transform import from_origin

from gis_mesh_workbench.runner import run, preflight, effective_config, validate_config
from gis_mesh_workbench.gmsh_reference import PRESET, PRESET_PATH, inspect_preset, validate_child, public_command


class GmshReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = json.loads(PRESET_PATH.read_text(encoding='utf-8'))
        executable = (PRESET_PATH.parent.parent / config['generator']['executable']).resolve()
        if not executable.is_file():
            raise unittest.SkipTest('External Gmsh dependency is not distributed; install it to run integration tests')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.path = self.root/'config.json'
        self.raster = self.root/'constant.tif'
        with rasterio.open(self.raster,'w',driver='GTiff',width=4,height=4,count=1,
            dtype='float64',crs='EPSG:32649',transform=from_origin(0,1000,250,250)) as dst:
            dst.write(np.full((4,4),17.0),1)
        self.config = dict(schema_version='gis-workbench-run-config-v0',run_name='gmsh-test',
            analysis_crs='EPSG:32649',raster_path='constant.tif',band=1,semantics='intensive',
            value_units='test',output_root='outputs',cell_source=dict(kind='gmsh_reference',preset=PRESET))

    def save(self):
        self.path.write_text(json.dumps(self.config),encoding='utf-8')
        return effective_config(self.path)

    def test_static_preflight_unknown_cells_and_schema(self):
        config = self.save(); validate_config(config)
        report,cells = preflight(config)
        self.assertEqual(report['status'],'pass',report)
        self.assertIsNone(cells); self.assertIsNone(report['cell_count'])
        self.assertTrue(report['mesh_generation_required']); self.assertEqual(report['target_edge_m'],250)
        for field,value in [('preset','unknown'),('target_edge',125),('algorithm',6)]:
            changed = json.loads(json.dumps(config)); changed['cell_source'][field] = value
            with self.assertRaises(ValueError): validate_config(changed)

    def test_missing_executable_and_version_mismatch(self):
        with patch('gis_mesh_workbench.gmsh_reference.Path.is_file',return_value=False):
            with self.assertRaisesRegex(ValueError,'missing'): inspect_preset('EPSG:32649')
        with patch('gis_mesh_workbench.gmsh_reference.subprocess.run',return_value=Mock(returncode=0,stdout='5.0',stderr='')):
            with self.assertRaisesRegex(ValueError,'version mismatch'): inspect_preset('EPSG:32649')
        with self.assertRaisesRegex(ValueError,'CRS mismatch'): inspect_preset('EPSG:32648')

    def test_real_gmsh_end_to_end_and_child_rejections(self):
        preset_hash = hashlib.sha256(PRESET_PATH.read_bytes()).hexdigest()
        raster_hash = hashlib.sha256(self.raster.read_bytes()).hexdigest()
        self.save(); result = run(self.path)
        self.assertEqual(result['status'],'pass',result)
        out = Path(result['output']); manifest = json.loads((out/'manifest.json').read_text())
        proof = manifest['gmsh_reference']; child = Path(proof['child_manifest_path']).parent
        self.assertEqual(proof['generator_version'],'4.15.2'); self.assertEqual(proof['target_edge_m'],250)
        self.assertGreater(proof['research_wall_time_seconds'],0)
        attrs = json.loads((out/'attributes.json').read_text())['cells']
        self.assertGreater(len(attrs),2)
        for index,cell in enumerate(attrs):
            self.assertEqual(cell['cell_id'],f'cell-{index}')
            self.assertAlmostEqual(cell['value'],17)
            self.assertAlmostEqual(cell['valid_coverage'],1)
        for name,digest in manifest['artifact_hashes'].items():
            self.assertEqual(hashlib.sha256((out/name).read_bytes()).hexdigest(),digest)
        self.assertEqual(hashlib.sha256(PRESET_PATH.read_bytes()).hexdigest(),preset_hash)
        self.assertEqual(hashlib.sha256(self.raster.read_bytes()).hexdigest(),raster_hash)
        child_manifest = child/'manifest.json'; original = child_manifest.read_text()
        for field,value in [('status','fail'),('crs','EPSG:32648'),('units',{'coordinate':'degree'}),
                            ('generator',{'name':'fixture'}),
                            ('generator',{'name':'gmsh','version':'5.0','parameters':{'target_edge':250}}),
                            ('generator',{'name':'gmsh','version':'4.15.2','parameters':{'target_edge':125}}),
                            ('artifact_hashes',{})]:
            data = json.loads(original); data[field] = value
            child_manifest.write_text(json.dumps(data))
            with self.assertRaises(ValueError): validate_child(child,'EPSG:32649',1000)
        child_manifest.write_text(original)
        with self.assertRaises(ValueError): validate_child(child,'EPSG:32649',1)
        qa_path = child/'qa/qa.json'; qa = json.loads(qa_path.read_text()); qa['hard_gates']=[]
        qa_path.write_text(json.dumps(qa))
        with self.assertRaisesRegex(ValueError,'topology'): validate_child(child,'EPSG:32649',1000)

    def test_public_command_timeout_and_nonzero(self):
        process=Mock(pid=123456,wait=Mock(side_effect=[subprocess.TimeoutExpired('research',45),0]))
        with patch('gis_mesh_workbench.gmsh_reference.subprocess.Popen',return_value=process), \
             patch('gis_mesh_workbench.gmsh_reference.subprocess.run') as kill, \
             patch('gis_mesh_workbench.gmsh_reference.os.killpg',create=True) as killpg:
            with self.assertRaisesRegex(ValueError,'timeout'):
                public_command(self.path,self.root/'timeout.log')
            if __import__('os').name == 'nt':
                self.assertEqual(kill.call_args.args[0],['taskkill','/PID','123456','/T','/F'])
            else: killpg.assert_called_once()
        with patch('gis_mesh_workbench.gmsh_reference.subprocess.Popen',return_value=Mock(wait=Mock(return_value=2))):
            with self.assertRaisesRegex(ValueError,'exited 2'):
                public_command(self.path,self.root/'failed.log')

    def test_meshing_timeout_failure_retained(self):
        self.save()
        with patch('gis_mesh_workbench.gmsh_reference.public_command',side_effect=ValueError('Gmsh reference timeout')):
            result = run(self.path)
        self.assertEqual(result['status'],'fail')
        manifest=json.loads((Path(result['output'])/'manifest.json').read_text())
        self.assertEqual(manifest['failed_stage'],'meshing')
        self.assertIn('timeout',manifest['error'])
        self.assertFalse((Path(result['output'])/'attributes.json').exists())

    def test_generated_workload_gate_before_extract(self):
        self.config['max_pairs']=16
        self.save(); result=run(self.path)
        self.assertEqual(result['status'],'fail')
        manifest=json.loads((Path(result['output'])/'manifest.json').read_text())
        self.assertEqual(manifest['failed_stage'],'cells')
        self.assertFalse((Path(result['output'])/'attributes.json').exists())


if __name__ == '__main__': unittest.main()
