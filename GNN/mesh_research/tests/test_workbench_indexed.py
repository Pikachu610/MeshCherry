import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from affine import Affine
import numpy as np
import rasterio
from rasterio.warp import transform

from gis_mesh_workbench.extraction import Cell, extract_attributes
from gis_mesh_workbench.indexed_extraction import extract_indexed, estimate
from gis_mesh_workbench.runner import run


class IndexedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.path=self.root/'sample.tif'
        self.affine=Affine(1,0,500000,0,-1,1000004)
        self.rect=Cell('r','rectangle',((500000,1000000),(500004,1000000),
                                       (500004,1000004),(500000,1000004)))
        self.triangles=[Cell('t0','triangle',(self.rect.xy[0],self.rect.xy[1],self.rect.xy[2])),
                        Cell('t1','triangle',(self.rect.xy[0],self.rect.xy[2],self.rect.xy[3]))]

    def write(self,data,affine=None,crs='EPSG:32649',mask=None,tiled=False):
        array=np.array(data,dtype='float64')
        with rasterio.open(self.path,'w',driver='GTiff',width=array.shape[1],height=array.shape[0],
            count=1,dtype='float64',crs=crs,transform=affine or self.affine,
            **(dict(tiled=True,blockxsize=32,blockysize=32) if tiled else {})) as dst:
            dst.write(array,1)
            if mask is not None: dst.write_mask(np.array(mask,dtype='uint8'))

    def options(self,semantics='intensive',crs='EPSG:32649'):
        return dict(cell_crs=crs,analysis_crs=crs,band=1,semantics=semantics,value_units='test')

    def parity(self,cells,options):
        oracle=extract_attributes(self.path,cells,**options)
        indexed=extract_indexed(self.path,cells,**options,hard_limits=dict(max_window_pixels=4))
        self.assertEqual([r['cell_id'] for r in oracle['cells']],[r['cell_id'] for r in indexed['cells']])
        for a,b in zip(oracle['cells'],indexed['cells']):
            self.assertEqual(set(a),set(b))
            for key in a:
                if isinstance(a[key],dict):
                    self.assertEqual(set(a[key]),set(b[key]))
                    for code in a[key]: self.assertAlmostEqual(a[key][code],b[key][code],delta=1e-10)
                elif type(a[key]) is float: self.assertAlmostEqual(a[key],b[key],delta=max(1e-10,abs(a[key])*1e-10))
                else: self.assertEqual(a[key],b[key])
        if options['semantics']=='extensive':
            for key in ('cell_total','source_total_on_cell_union','residual'):
                self.assertAlmostEqual(oracle['qa'][key],indexed['qa'][key],delta=1e-10)
        return indexed

    def test_parity_semantics_mask_zero_outside_and_triangles(self):
        values=np.array([[0,1,1,2],[2,2,0,1],[1,2,3,0],[4,0,2,1]],float)
        mask=np.full((4,4),255,dtype='uint8'); mask[0,1]=0
        values[1,1]=np.nan; self.write(values,mask=mask)
        big=Cell('outside','rectangle',((499999,1000000),(500005,1000000),
                                          (500005,1000004),(499999,1000004)))
        missing=Cell('missing','rectangle',((500010,1000000),(500011,1000000),
                                            (500011,1000001),(500010,1000001)))
        for kind in ([self.rect,missing],self.triangles,[big]):
            for semantics in ('intensive','extensive','categorical'):
                self.parity(kind,self.options(semantics))

    def test_rotated_affine_parity(self):
        affine=Affine(1,.3,500000,.2,-1,1000004)
        self.write(np.arange(16).reshape(4,4),affine=affine)
        cell=Cell('rotated','triangle',tuple(affine @ p for p in ((0,0),(4,0),(4,4))))
        for semantics in ('intensive','extensive','categorical'):
            self.parity([cell],self.options(semantics))

    def test_cross_crs_parity_and_conservative_reads(self):
        self.write([[0,9],[4,2]],affine=Affine(.01,0,10,0,-.01,20.02),crs='EPSG:4326')
        x,y=transform('EPSG:4326','EPSG:3857',[10.005],[20.005])
        cell=Cell('projected','rectangle',tuple((x[0]+dx,y[0]+dy) for dx,dy in ((-5,-5),(5,-5),(5,5),(-5,5))))
        for semantics in ('intensive','extensive','categorical'):
            result=self.parity([cell],self.options(semantics,'EPSG:3857'))
            self.assertEqual(result['qa']['engine_evidence']['pixels_read'],4)
            self.assertEqual(result['provenance']['window_culling'],'disabled_cross_crs_conservative')

    def test_large_tiny_footprint_outside_subwindows_and_source_hash(self):
        self.write(np.ones((512,512)),tiled=True)
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        result=extract_indexed(self.path,[self.rect],**self.options(),hard_limits=dict(max_window_pixels=16))
        evidence=result['qa']['engine_evidence']
        self.assertLess(evidence['pixels_read'],512*512/100)
        self.assertLessEqual(evidence['peak_window_pixels'],16)
        self.assertLess(evidence['candidate_pairs'],evidence['source_pixels'])
        outside=Cell('none','rectangle',((0,0),(1,0),(1,1),(0,1)))
        result=extract_indexed(self.path,[outside],**self.options())
        self.assertEqual(result['qa']['engine_evidence']['windows_read'],0)
        self.assertIsNone(result['cells'][0]['value'])
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)

    def test_guards_pair_read_source_scale_crs_and_nonoverlap(self):
        self.write(np.ones((4,4)))
        for hard in (dict(max_candidate_pairs=1),dict(max_pixels_read=1),dict(max_source_pixels=1),dict(max_source_pixels=10000001)):
            with self.assertRaises(ValueError): extract_indexed(self.path,self.triangles,**self.options(),hard_limits=hard)
        with self.assertRaisesRegex(ValueError,'overlap'):
            duplicate=Cell('other','rectangle',self.rect.xy)
            extract_indexed(self.path,[self.rect,duplicate],**self.options())
        with rasterio.open(self.path,'r+') as dst: dst.scales=(.1,)
        with self.assertRaisesRegex(ValueError,'scale/offset'):
            extract_indexed(self.path,self.triangles,**self.options())
        with self.assertRaisesRegex(ValueError,'projected'):
            extract_indexed(self.path,self.triangles,**self.options(crs='EPSG:4326'))

    def test_v1_runner_and_diagnostic_pair_limit(self):
        self.write(np.ones((4,4)))
        config=dict(schema_version='gis-workbench-run-config-v1',extraction_engine='indexed_windowed_v1',
            run_name='indexed',analysis_crs='EPSG:32649',raster_path='sample.tif',band=1,
            semantics='intensive',value_units='test',output_root='runs',cell_source=dict(kind='structured_grid',
                extent=[500000,1000000,500004,1000004],cell_width_m=2,cell_height_m=2,boundary_policy='strict'))
        path=self.root/'config.json'; path.write_text(json.dumps(config))
        result=run(path); self.assertEqual(result['status'],'pass',result)
        manifest=json.loads((Path(result['output'])/'manifest.json').read_text())
        self.assertEqual(manifest['engine_evidence']['extraction_engine'],'indexed_windowed_v1')
        self.assertEqual(manifest['workbench_memory_metric']['name'],'process_lifetime_peak_rss_bytes')
        config['indexed_limits']=dict(max_candidate_pairs=1); path.write_text(json.dumps(config))
        result=run(path); self.assertEqual(result['status'],'fail')
        self.assertEqual(json.loads((Path(result['output'])/'manifest.json').read_text())['failed_stage'],'extraction')
        self.assertFalse((Path(result['output'])/'attributes.json').exists())


if __name__ == '__main__': unittest.main()
