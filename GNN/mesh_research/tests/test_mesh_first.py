import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from affine import Affine

from gis_mesh_workbench.runner import run, write_json, effective_config, preflight
from gis_mesh_workbench.mesh_workflow import quality, read_frozen, geometry_digest
from gis_mesh_workbench.extraction import Cell


class MeshFirstTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.output=self.root/'output';self.output.mkdir()
        self.config=dict(schema_version='gis-workbench-run-config-v1',operation='mesh_only',
            extraction_engine='indexed_windowed_v1',run_name='mesh-first',analysis_crs='EPSG:32649',
            band=1,semantics='intensive',value_units='m',output_root=str(self.output),
            cell_source=dict(kind='structured_grid',extent=[500000,1000000,500002,1000001],
                cell_width_m=1,cell_height_m=1,boundary_policy='strict',max_cells=1000))
        self.path=self.root/'config.json'

    def test_mesh_without_raster_then_extract_identical_cells(self):
        write_json(self.path,self.config)
        with patch('rasterio.open',side_effect=AssertionError('mesh must not read raster')):
            mesh=run(self.path)
        self.assertEqual(mesh['status'],'pass',mesh)
        folder=Path(mesh['output']);before=(folder/'cells.json').read_bytes()
        self.assertFalse((folder/'attributes.json').exists())
        qa=json.loads((folder/'qa.json').read_text())['mesh_quality']
        self.assertEqual(qa['cell_count'],2);self.assertEqual(qa['total_area_m2'],2)
        self.assertEqual(qa['overlap_area_m2'],0);self.assertIsNone(qa['triangle_min_angle_deg'])
        tif=self.root/'attributes.tif'
        with rasterio.open(tif,'w',driver='GTiff',width=2,height=1,count=1,dtype='float64',
            crs='EPSG:32649',transform=Affine(1,0,500000,0,-1,1000001)) as dst:
            dst.write(np.array([[10,30]],float),1)
        config=dict(self.config,operation='extract',raster_path=str(tif),
            cell_source=dict(kind='frozen_cells',path=str(folder/'cells.json'),max_cells=1000))
        write_json(self.path,config)
        with patch('gis_mesh_workbench.runner.generate_gmsh',side_effect=AssertionError('no remeshing')):
            result=run(self.path)
        self.assertEqual(result['status'],'pass',result)
        self.assertEqual((folder/'cells.json').read_bytes(),before)
        geometry=json.loads(before)['cells']
        reused=json.loads((Path(result['output'])/'cells.json').read_text())['cells']
        self.assertEqual(reused,geometry)
        attrs=json.loads((Path(result['output'])/'attributes.json').read_text())
        self.assertEqual([r['value'] for r in attrs['cells']],[10,30])

    def test_quality_right_triangle_and_crs_guard(self):
        qa=quality((Cell('t','triangle',((0,0),(1,0),(0,1))),))
        self.assertAlmostEqual(qa['triangle_min_angle_deg'],45)
        self.assertAlmostEqual(qa['triangle_max_aspect'],2)
        write_json(self.path,self.config);result=run(self.path)
        with self.assertRaisesRegex(ValueError,'CRS mismatch'):
            read_frozen(Path(result['output'])/'cells.json','EPSG:32648')

    def test_planar_quality_accepts_either_winding_and_rejects_degenerate(self):
        ccw=((0,0),(2,0),(0,1));cw=tuple(reversed(ccw))
        one=quality((Cell('one','triangle',ccw),));two=quality((Cell('one','triangle',cw),))
        for key in ('total_area_m2','triangle_min_angle_deg','triangle_max_aspect'):
            self.assertAlmostEqual(one[key],two[key])
        with self.assertRaisesRegex(ValueError,'positive-area'):
            quality((Cell('zero','triangle',((0,0),(1,0),(2,0))),))

    def test_relief_without_dem_is_rejected(self):
        config=dict(self.config,cell_source=dict(kind='gmsh_generated',extent=[0,0,1000,1000],
            executable='missing.exe',sizing='relief',h_min_m=250,h_max_m=1000,relief_radius_m=100,max_cells=1000))
        write_json(self.path,config)
        self.assertEqual(preflight(effective_config(self.path))[0]['status'],'fail')

    def test_frozen_mesh_mutation_rejected(self):
        write_json(self.path,self.config);mesh=run(self.path)
        path=Path(mesh['output'])/'cells.json';data=json.loads(path.read_text())
        data['cells'][0]['cell_id']='edited';write_json(path,data)
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            read_frozen(path,'EPSG:32649')

    def test_rotated_raster_roi_preserved_for_gmsh(self):
        from gis_mesh_workbench.gmsh_meshing import mesh_domain
        from shapely.geometry import box
        tif=self.root/'rotated.tif'
        with rasterio.open(tif,'w',driver='GTiff',width=2,height=2,count=1,dtype='float64',
            crs='EPSG:32649',transform=Affine(1,.5,500000,.25,-1,1000001)) as dst:
            dst.write(np.zeros((2,2)),1)
        config=dict(self.config,cell_source=dict(roi_raster_path=str(tif),extent=[0,0,1,1]))
        domain,_=mesh_domain(config)
        self.assertAlmostEqual(domain.area,4.5)
        self.assertGreater(box(*domain.bounds).area,domain.area)
        source=dict(self.config['cell_source'],roi_raster_path=str(tif),extent=list(domain.bounds))
        config=dict(self.config,cell_source=source);write_json(self.path,config)
        report,_=preflight(effective_config(self.path))
        self.assertEqual(report['status'],'fail')
        self.assertIn('axis-aligned',report['errors'][0])

    def v2(self):
        config=dict(self.config,schema_version='gis-workbench-run-config-v2')
        for key in ('band','semantics','value_units'):config.pop(key)
        return config

    def test_v2_uniform_requires_no_extraction_fields(self):
        write_json(self.path,self.v2())
        with patch('rasterio.open',side_effect=AssertionError('no raster allowed')):result=run(self.path)
        self.assertEqual(result['status'],'pass',result)
        folder=Path(result['output']);manifest=json.loads((folder/'manifest.json').read_text())
        frozen=read_frozen(folder/'cells.json','EPSG:32649')
        self.assertEqual(manifest['geometry_digest'],geometry_digest(frozen.cells,frozen.crs_wkt))
        orphan=self.root/'orphan.json';orphan.write_bytes((folder/'cells.json').read_bytes())
        with self.assertRaisesRegex(ValueError,'manifest'):read_frozen(orphan,'EPSG:32649')
        with self.assertRaisesRegex(ValueError,'digest'):read_frozen(folder/'cells.json','EPSG:32649',expected_digest='0'*64)

    def test_v2_relief_missing_dem_actionable(self):
        config=dict(self.v2(),cell_source=dict(kind='gmsh_generated',executable='missing.exe',extent=[0,0,1000,1000],
            sizing='relief',h_min_m=250,h_max_m=500,relief_radius_m=100,max_cells=1000))
        write_json(self.path,config);report,_=preflight(effective_config(self.path))
        self.assertIn('DEM required for relief sizing',report['errors'][0])

    def test_two_extractions_preserve_digest_and_catch_mutation(self):
        write_json(self.path,self.v2());mesh=run(self.path);folder=Path(mesh['output'])
        original=(folder/'cells.json').read_bytes();identity=json.loads((folder/'manifest.json').read_text())['geometry_digest']
        tif=self.root/'values.tif'
        with rasterio.open(tif,'w',driver='GTiff',width=2,height=1,count=1,dtype='float64',crs='EPSG:32649',
            transform=Affine(1,0,500000,0,-1,1000001)) as dst:dst.write(np.array([[10,30]],float),1)
        config=dict(self.v2(),operation='extract',raster_path=str(tif),band=1,semantics='intensive',value_units='m',
            cell_source=dict(kind='frozen_cells',path=str(folder/'cells.json'),geometry_digest=identity,max_cells=1000))
        for semantics in ('intensive','extensive'):
            config['semantics']=semantics;write_json(self.path,config);result=run(self.path)
            self.assertEqual(result['status'],'pass',result)
            result_folder=Path(result['output']);self.assertEqual(json.loads((result_folder/'qa.json').read_text())['geometry_digest'],identity)
            self.assertEqual((folder/'cells.json').read_bytes(),original)
        from gis_mesh_workbench.indexed_extraction import extract_indexed
        def mutate(*args,**kwargs):
            result=extract_indexed(*args,**kwargs);(folder/'cells.json').write_bytes(original+b' ');return result
        with patch('gis_mesh_workbench.runner.extract_indexed',side_effect=mutate):result=run(self.path)
        self.assertEqual(result['status'],'fail');self.assertIn('mismatch',result['error'])

    def test_geographic_rotated_roi_provenance_and_mask_independence(self):
        from gis_mesh_workbench.gmsh_meshing import mesh_domain
        tif=self.root/'geographic.tif'
        def write(nodata):
            with rasterio.open(tif,'w',driver='GTiff',width=2,height=2,count=1,dtype='float64',crs='EPSG:4326',
                transform=Affine(.001,.0001,109,.0001,-.001,13),nodata=nodata) as dst:dst.write(np.zeros((2,2)),1)
        config=dict(self.v2(),edge_subdivisions=16,cell_source=dict(roi_raster_path=str(tif)))
        write(None);a,info=mesh_domain(config);write(0);b,_=mesh_domain(config)
        self.assertEqual(a.wkt,b.wkt);self.assertEqual(info['edge_subdivisions'],16)
        self.assertEqual(info['vertex_count'],64);self.assertIn('NoData/mask ignored',info['policy'])
        with rasterio.open(tif,'w',driver='GTiff',width=2,height=2,count=1,dtype='float64',transform=Affine.identity()) as dst:dst.write(np.zeros((2,2)),1)
        with self.assertRaisesRegex(ValueError,'CRS'):mesh_domain(config)

    def test_sliver_quality_is_reported(self):
        qa=quality((Cell('s','triangle',((0,0),(100,0),(50,.01))),))
        self.assertLess(qa['triangle_min_angle_deg'],.02)
        self.assertGreater(qa['triangle_max_aspect'],9999)

    def test_real_gmsh_shp_without_raster_and_roi_relief(self):
        import shapefile
        from rasterio.crs import CRS
        from gis_mesh_workbench.gmsh_meshing import mesh_domain
        executable=Path(__file__).resolve().parents[3]/'Test_chialuoi/gmsh-4.15.2-Windows64/gmsh.exe'
        if not executable.is_file():self.skipTest('external Gmsh not installed')
        shp=self.root/'boundary.shp'
        with shapefile.Writer(str(shp),shapeType=shapefile.POLYGON) as writer:
            writer.field('id','N');writer.poly([[(500000,1000000),(500000,1001000),(501000,1001000),(501000,1000000),(500000,1000000)],
                [(500400,1000400),(500600,1000400),(500600,1000600),(500400,1000600),(500400,1000400)]]);writer.record(1)
        shp.with_suffix('.prj').write_text(CRS.from_epsg(32649).to_wkt())
        source=dict(kind='gmsh_generated',boundary_path=str(shp),executable=str(executable),sizing='uniform',h_min_m=250,h_max_m=250,max_cells=1000)
        config=dict(self.v2(),cell_source=source);write_json(self.path,config)
        with patch('rasterio.open',side_effect=AssertionError('SHP uniform must not touch raster')):result=run(self.path)
        self.assertEqual(result['status'],'pass',result)
        qa=json.loads((Path(result['output'])/'qa.json').read_text())['mesh_quality']
        self.assertEqual(qa['hole_count'],1);self.assertEqual(qa['component_count'],1);self.assertEqual(qa['domain_coverage'],'pass')
        tif=self.root/'rotated-dem.tif'
        with rasterio.open(tif,'w',driver='GTiff',width=4,height=4,count=1,dtype='float64',crs='EPSG:32649',
            transform=Affine(250,25,500000,25,-250,1001000)) as dst:dst.write(np.arange(16).reshape(4,4).astype(float),1)
        source.pop('boundary_path');source.update(roi_raster_path=str(tif),dem_raster_path=str(tif),dem_band=1,sizing='relief',relief_radius_m=100)
        config=dict(self.v2(),cell_source=source);write_json(self.path,config);result=run(self.path)
        self.assertEqual(result['status'],'pass',result)
        manifest=json.loads((Path(result['output'])/'manifest.json').read_text());evidence=manifest['gmsh_generated']
        self.assertAlmostEqual(evidence['symmetric_difference_area_m2'],0,places=5)
        self.assertEqual(evidence['size_field']['dem_source_path'],str(tif.resolve()))
        self.assertEqual(evidence['size_field']['control_bounds'],list(mesh_domain(config)[0].bounds))
        field=evidence['size_field']
        self.assertEqual(field['control_shape'],[33,33]);self.assertEqual(field['gamma'],2)
        self.assertEqual(field['percentile_positions'],[25,95])
        self.assertIn('target_size_field_min_m',field);self.assertNotIn('h_min_realized',field)
        qa=json.loads((Path(result['output'])/'qa.json').read_text())['mesh_quality']
        self.assertEqual(qa['sizing_summary']['size_field'],field)
