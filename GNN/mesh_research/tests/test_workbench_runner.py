import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import rasterio
from affine import Affine

from gis_mesh_workbench.runner import run


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.path=self.root/'config.json'
        self.raster=self.root/'sample.tif'
        with rasterio.open(self.raster,'w',driver='GTiff',width=2,height=1,
                           count=1,dtype='float64',crs='EPSG:32649',
                           transform=Affine(1,0,500000,0,-1,1000001)) as dst:
            dst.write(np.array([[10,30]],dtype=float),1)
        self.config=dict(schema_version='gis-workbench-run-config-v0',run_name='fixture',
            analysis_crs='EPSG:32649',raster_path='sample.tif',band=1,semantics='extensive',
            value_units='test',output_root='outputs',cell_source=dict(kind='structured_grid',
                extent=[500000,1000000,500002,1000001],cell_width_m=1,cell_height_m=1,
                boundary_policy='strict'))

    def execute(self):
        self.path.write_text(json.dumps(self.config),encoding='utf-8')
        result=run(self.path)
        return result,Path(result['output'])

    def test_grid_artifacts_hashes_repeat_and_source_immutable(self):
        before=hashlib.sha256(self.raster.read_bytes()).hexdigest()
        first,out=self.execute(); second,_=self.execute()
        self.assertEqual(first['status'],'pass'); self.assertNotEqual(first['output'],second['output'])
        for name in ('config.json','cells.json','attributes.json','attributes.csv','preflight.json','qa.json','manifest.json','preview.png'):
            self.assertTrue((out/name).stat().st_size>0)
        manifest=json.loads((out/'manifest.json').read_text())
        contract=manifest['effective_contract']
        self.assertEqual(contract['cell_source_kind'],'structured_grid')
        self.assertEqual(contract['coordinate_units'],'m')
        self.assertEqual(contract['semantics'],'extensive')
        self.assertEqual(contract['value_units'],'test')
        self.assertIn('PROJCS',contract['analysis_crs_wkt'])
        self.assertEqual(contract['effective_limits'],dict(max_pixels=10000,max_pairs=1000000,
                                                          max_cells=1000,edge_subdivisions=8))
        for name,digest in manifest['artifact_hashes'].items():
            self.assertEqual(hashlib.sha256((out/name).read_bytes()).hexdigest(),digest)
        attrs=json.loads((out/'attributes.json').read_text())
        self.assertEqual(attrs['qa']['cell_total'],40)
        with (out/'attributes.csv').open(encoding='utf-8-sig',newline='') as stream:
            rows=list(csv.DictReader(stream))
        self.assertEqual([r['cell_id'] for r in rows],[r['cell_id'] for r in attrs['cells']])
        self.assertEqual(hashlib.sha256(self.raster.read_bytes()).hexdigest(),before)

    def test_xyz_import_via_cli_relative_paths(self):
        mesh=self.root/'mesh.json'
        mesh.write_text(json.dumps(dict(schema_version='mesh-artifact-v0',crs='EPSG:32649',
            coordinate_units='m',vertices=[[500000,1000000,5],[500002,1000000,6],
             [500002,1000001,7],[500000,1000001,8]],triangles=[[0,1,2],[0,2,3]])),encoding='utf-8')
        before=mesh.read_bytes()
        self.config['cell_source']=dict(kind='mesh_artifact',path='mesh.json')
        self.path.write_text(json.dumps(self.config),encoding='utf-8')
        env=dict(os.environ); env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
        proc=subprocess.run([sys.executable,'-m','gis_mesh_workbench.cli','run',str(self.path)],
                            cwd=self.temp.name,env=env,text=True,capture_output=True)
        self.assertEqual(proc.returncode,0,proc.stderr+proc.stdout)
        output=Path(json.loads(proc.stdout)['output'])
        contract=json.loads((output/'manifest.json').read_text())['effective_contract']
        self.assertEqual(contract['cell_source_kind'],'mesh_artifact')
        self.assertEqual(contract['coordinate_units'],'m')
        self.assertEqual(contract['semantics'],'extensive')
        self.assertEqual(contract['effective_limits']['max_cells'],1000)
        self.assertIn('PROJCS',contract['analysis_crs_wkt'])
        self.assertEqual(json.loads((output/'cells.json').read_text())['cells'][0]['cell_id'],'cell-0')
        self.assertEqual(mesh.read_bytes(),before)

    def test_retained_failures_for_crs_scale_limits_typo(self):
        for key,value in [('analysis_crs','EPSG:4326'),('max_pixels',10001),('bannd',1)]:
            original=dict(self.config); self.config[key]=value
            result,out=self.execute()
            self.assertEqual(result['status'],'fail')
            self.assertIn(json.loads((out/'manifest.json').read_text())['failed_stage'],('config','preflight'))
            self.config=original
        with rasterio.open(self.raster,'r+') as dst:
            dst.scales=(.1,)
        result,out=self.execute()
        self.assertEqual(result['status'],'fail'); self.assertIn('scale/offset',result['error'])

    def test_lower_limits_recorded_and_invalid_crs_not_fabricated(self):
        self.config.update(max_pixels=2,max_pairs=10,edge_subdivisions=4)
        self.config['cell_source']['max_cells']=2
        result,out=self.execute(); self.assertEqual(result['status'],'pass')
        contract=json.loads((out/'manifest.json').read_text())['effective_contract']
        self.assertEqual(contract['effective_limits'],dict(max_pixels=2,max_pairs=10,max_cells=2,edge_subdivisions=4))
        self.config['analysis_crs']='EPSG:4326'
        result,out=self.execute()
        self.assertNotIn('analysis_crs_wkt',json.loads((out/'manifest.json').read_text())['effective_contract'])

    def test_null_csv_and_category_fractions(self):
        self.config['cell_source']['extent']=[500010,1000000,500012,1000001]
        self.config['semantics']='intensive'
        result,out=self.execute(); self.assertEqual(result['status'],'pass')
        self.assertIsNone(json.loads((out/'attributes.json').read_text())['cells'][0]['value'])
        with (out/'attributes.csv').open(encoding='utf-8-sig',newline='') as stream:
            self.assertEqual(next(csv.DictReader(stream))['value'],'')
        self.config['cell_source']['extent']=[500000,1000000,500002,1000001]
        self.config['semantics']='categorical'
        result,out=self.execute(); self.assertEqual(result['status'],'pass')
        with (out/'attributes.csv').open(encoding='utf-8-sig',newline='') as stream:
            fractions=json.loads(next(csv.DictReader(stream))['class_fractions'])
        self.assertEqual(fractions,{'10.0':1})

    def test_malformed_no_output_and_cli_fail_exit(self):
        self.path.write_text('{bad',encoding='utf-8')
        with self.assertRaises(ValueError):
            run(self.path)
        self.assertFalse((self.root/'outputs').exists())
        self.config['analysis_crs']='EPSG:4326'
        self.path.write_text(json.dumps(self.config),encoding='utf-8')
        proc=subprocess.run([sys.executable,'-m','gis_mesh_workbench.cli','run',str(self.path)],
                             text=True,capture_output=True)
        self.assertEqual(proc.returncode,2,proc.stderr)


if __name__=='__main__':
    unittest.main()
