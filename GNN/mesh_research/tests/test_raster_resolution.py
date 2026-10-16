"""Resolution guidance does not confer raster/extraction/meshing authority."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from affine import Affine

from gis_mesh_workbench.raster_resolution import read, planning_reference
from gis_mesh_workbench.runner import run, preflight, effective_config, write_json, validate_config
from gis_mesh_workbench.meshing_parameters import policy


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.output=self.root/'output';self.output.mkdir()
        self.path=self.root/'reference.tif';self.config_path=self.root/'config.json'

    def raster(self,affine,crs='EPSG:32649',nodata=None):
        with rasterio.open(self.path,'w',driver='GTiff',width=10,height=10,count=1,
            dtype='float64',crs=crs,transform=affine,nodata=nodata) as dst:
            dst.write(np.zeros((10,10)),1)
        return read(self.path,'EPSG:32649')

    def config(self):
        return dict(schema_version='gis-workbench-run-config-v2',operation='mesh_only',
            extraction_engine='indexed_windowed_v1',run_name='resolution-test',
            analysis_crs='EPSG:32649',output_root=str(self.output),
            cell_source=dict(kind='structured_grid',extent=[500000,1000000,500002,1000001],
                cell_width_m=1,cell_height_m=1,boundary_policy='strict',max_cells=1000))

    def test_projected_nonsquare_exact_and_mask_independent(self):
        affine=Affine(10,0,500000,0,-30,1000000)
        a=self.raster(affine);b=self.raster(affine,nodata=0)
        self.assertEqual(a,b)
        self.assertEqual(a['native_pixel_size'],[10,30])
        self.assertEqual(a['representative_pixel_size_m'],[10,30])
        self.assertFalse(a['approximate']);self.assertEqual(a['projected_spacing'][0]['sample_count'],1)

    def test_rotation_shear_uses_vectors_not_diagonal(self):
        r=self.raster(Affine(3,5,500000,4,-12,1000000))
        self.assertEqual(r['native_pixel_size'],[5,13]);self.assertEqual(r['representative_pixel_size_m'],[5,13])
        self.assertTrue(r['rotated_or_sheared'])

    def test_geographic_degree_and_projected_sample_range(self):
        r=self.raster(Affine(.01,0,109,0,-.01,13),'EPSG:4326')
        self.assertEqual(r['native_units'],'degree');self.assertTrue(r['approximate'])
        self.assertEqual(len(r['sample_locations_pixel']),9)
        for direction in r['projected_spacing']:
            self.assertEqual(direction['sample_count'],9)
            self.assertGreater(direction['minimum_m'],0)
            self.assertLessEqual(direction['minimum_m'],direction['representative_m'])
            self.assertGreater(direction['maximum_m'],direction['minimum_m'])
        self.assertIn('not geodesic',r['spacing_method'])

    def test_feet_not_labelled_metres_and_bad_band(self):
        r=self.raster(Affine(10,0,1000000,0,-30,200000),'EPSG:2263')
        self.assertIn('foot',r['native_units'])
        with self.assertRaisesRegex(ValueError,'band'):read(self.path,'EPSG:32649',2)

    def test_unknown_vector_roi_without_raster_io(self):
        with patch('rasterio.open',side_effect=AssertionError('no raster expected')):
            self.assertEqual(planning_reference(self.config())['status'],'unknown')
            write_json(self.config_path,self.config());result=run(self.config_path)
        self.assertEqual(result['status'],'pass',result)

    def test_advisory_reference_does_not_change_cells_or_extract_roles(self):
        config=self.config();write_json(self.config_path,config);a=run(self.config_path)
        self.raster(Affine(10,0,500000,0,-30,1000000))
        config['resolution_reference']=dict(path=str(self.path),band=1)
        write_json(self.config_path,config);b=run(self.config_path)
        self.assertEqual(b['status'],'pass',b)
        self.assertEqual((Path(a['output'])/'cells.json').read_bytes(),(Path(b['output'])/'cells.json').read_bytes())
        manifest=json.loads((Path(b['output'])/'manifest.json').read_text())
        r=manifest['meshing_request']['resolution_reference']
        self.assertFalse(r['affects_mesh_computation']);self.assertEqual(r['role'],'planned_extraction')
        self.assertEqual(len(r['main_file_sha256']),64)
        self.assertEqual(manifest['geometry_digest'],json.loads((Path(a['output'])/'manifest.json').read_text())['geometry_digest'])
        self.assertNotIn('raster_path',config);self.assertNotIn('dem_raster_path',config['cell_source'])
        self.assertFalse((Path(b['output'])/'attributes.json').exists())

    def test_invalid_advisory_is_not_pass_preflight(self):
        config=self.config();config['resolution_reference']=dict(path=str(self.path),band=1)
        write_json(self.config_path,config);report,_=preflight(effective_config(self.config_path))
        self.assertEqual(report['status'],'fail');self.assertTrue(report['errors'])

    def test_uniform_and_roi_contract_and_legacy(self):
        config=self.config();config['cell_source']=dict(kind='gmsh_generated',executable='gmsh.exe',
            extent=[0,0,1000,1000],sizing='uniform',h_min_m=100,h_max_m=100,max_cells=1000)
        validate_config(config)
        source=config['cell_source']
        source['h_min_m']=50
        with self.assertRaisesRegex(ValueError,'Uniform'):validate_config(config)
        source['h_min_m']=100;source['relief_radius_m']=100
        with self.assertRaisesRegex(ValueError,'Uniform'):validate_config(config)
        source.pop('relief_radius_m');source['boundary_path']='boundary.shp'
        with self.assertRaisesRegex(ValueError,'ROI'):validate_config(config)
        source.pop('boundary_path');source['h_min_m']=50
        config.update(schema_version='gis-workbench-run-config-v1',raster_path=str(self.path),band=1,semantics='intensive',value_units='m')
        source['relief_radius_m']=100  # Legacy schema requires this even for uniform.
        validate_config(config)

    def test_fixed_policy_not_editable_config(self):
        p=policy();self.assertEqual(p['control_shape'],[33,33]);self.assertEqual(p['gamma'],2)
        config=self.config();config['gamma']=3
        with self.assertRaises(ValueError):validate_config(config)

    def test_real_gmsh_advisory_does_not_change_geo_mesh(self):
        exe=Path(__file__).resolve().parents[3]/'Test_chialuoi/gmsh-4.15.2-Windows64/gmsh.exe'
        if not exe.is_file():self.skipTest('external Gmsh not installed')
        config=self.config();config['cell_source']=dict(kind='gmsh_generated',executable=str(exe),
            extent=[500000,1000000,501000,1001000],sizing='uniform',h_min_m=250,h_max_m=250,max_cells=1000)
        write_json(self.config_path,config);a=run(self.config_path)
        self.raster(Affine(30,0,500000,0,-30,1001000))
        config['resolution_reference']=dict(path=str(self.path),band=1)
        write_json(self.config_path,config);b=run(self.config_path)
        self.assertEqual(a['status'],'pass',a);self.assertEqual(b['status'],'pass',b)
        for name in ('cells.json','meshing/domain.geo'):
            self.assertEqual((Path(a['output'])/name).read_bytes(),(Path(b['output'])/name).read_bytes())
        qa=json.loads((Path(b['output'])/'qa.json').read_text())['mesh_quality']
        self.assertEqual(qa['sizing_summary']['requested_target_size_m'],250)
        self.assertNotEqual(qa['edge_min_m'],250)
