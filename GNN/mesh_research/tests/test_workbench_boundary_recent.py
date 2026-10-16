import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
import shapefile
from shapely.ops import unary_union

from gis_mesh_workbench.boundary import bundle_paths, read_boundary
from gis_mesh_workbench.recent import remember, read_recent, save_recent
from gis_mesh_workbench.webui import WorkbenchServer, AccessError
from gis_mesh_workbench.runner import run, write_json
from gis_mesh_workbench.gmsh_meshing import size_field
from gis_mesh_workbench.extraction import _cells

EXE=Path(__file__).resolve().parents[3]/'Test_chialuoi/gmsh-4.15.2-Windows64/gmsh.exe'


def shp(path,rings,crs='EPSG:32649'):
    with shapefile.Writer(str(path),shapeType=shapefile.POLYGON) as writer:
        writer.field('id','N');writer.poly(rings);writer.record(1)
    path.with_suffix('.prj').write_text(CRS.from_string(crs).to_wkt(),encoding='utf-8')
    return path


class BoundaryRecentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.workspace=self.root/'workspace';self.workspace.mkdir()
        self.path=self.root/'domain.shp'
        # Outer clockwise; hole counterclockwise. A concave outer boundary.
        self.outer=[(500000,1000000),(500000,1001000),(500600,1001000),
            (500600,1000600),(501000,1000600),(501000,1000000),(500000,1000000)]
        self.hole=[(500100,1000100),(500300,1000100),(500300,1000300),
            (500100,1000300),(500100,1000100)]
        shp(self.path,[self.outer,self.hole])
        self.raster=self.root/'dem.tif'
        with rasterio.open(self.raster,'w',driver='GTiff',width=20,height=20,count=1,
            dtype='float64',crs='EPSG:32649',transform=from_origin(500000,1001000,50,50)) as dst:
            dst.write(np.full((20,20),17.),1)
        self.config=dict(schema_version='gis-workbench-run-config-v1',extraction_engine='indexed_windowed_v1',
            run_name='shp-test',analysis_crs='EPSG:32649',raster_path=str(self.raster),band=1,
            semantics='intensive',value_units='m',output_root=str(self.workspace/'runs'),
            cell_source=dict(kind='gmsh_generated',executable=str(EXE),boundary_path=str(self.path),
                extent=[0,0,1,1],sizing='uniform',h_min_m=150,h_max_m=200,relief_radius_m=100,max_cells=1000))

    def server(self):
        server=WorkbenchServer(self.workspace,recent_preferences=self.root/'recent.json')
        self.addCleanup(server.server_close)
        return server

    def test_boundary_crs_hole_and_bundle(self):
        domain,report=read_boundary(self.path,'EPSG:32649')
        self.assertEqual(domain.area,800000)
        self.assertEqual(report['holes'],1);self.assertEqual(report['components'],1)
        self.assertEqual(report['transform_subdivisions'],1)
        self.assertEqual(len(report['files_sha256']),4)
        self.assertEqual(report['physical_boundary_status'],'UNVERIFIED')
        self.path.with_suffix('.prj').unlink()
        with self.assertRaisesRegex(ValueError,'Thiếu'):bundle_paths(self.path)

    def test_geographic_transform_and_bad_prj(self):
        shp(self.path,[[(109,13),(109,13.01),(109.01,13.01),(109.01,13),(109,13)]],'EPSG:4326')
        domain,report=read_boundary(self.path,'EPSG:32649')
        self.assertGreater(domain.area,1000000);self.assertEqual(report['transform_subdivisions'],8)
        self.assertGreater(report['transformed_vertices'],report['source_vertices'])
        self.path.with_suffix('.prj').write_text('not a CRS')
        with self.assertRaisesRegex(ValueError,'CRS'):read_boundary(self.path,'EPSG:32649')

    def test_invalid_polygon_wrong_type_and_metric_target(self):
        shp(self.path,[[(0,0),(1,1),(1,0),(0,1),(0,0)]])
        with self.assertRaisesRegex(ValueError,'không hợp lệ'):read_boundary(self.path,'EPSG:32649')
        with shapefile.Writer(str(self.path),shapeType=shapefile.POINT) as writer:
            writer.field('id','N');writer.point(0,0);writer.record(1)
        with self.assertRaisesRegex(ValueError,'Polygon'):read_boundary(self.path,'EPSG:32649')
        with self.assertRaises(ValueError):read_boundary(self.path,'EPSG:4326')

    def test_orphan_hole_rejected_without_automatic_repair(self):
        shp(self.path,[self.outer,[(502000,1000000),(502100,1000000),(502100,1000100),(502000,1000100),(502000,1000000)]])
        with self.assertRaisesRegex(ValueError,'không tự sửa'):read_boundary(self.path,'EPSG:32649')

    def test_replaced_recent_raster_denied(self):
        server=self.server();remember(server.recent_preferences,'raster',self.raster)
        ident=read_recent(server.recent_preferences)[0]['id'];self.raster.write_text('not raster')
        with self.assertRaises(Exception):server.recent_action({'id':ident})
        self.assertEqual(server.selected_files,set())

    def test_multiple_components_real_gmsh(self):
        if not EXE.is_file():self.skipTest('external Gmsh missing')
        from gis_mesh_workbench.gmsh_meshing import generate
        left=[(500000,1000000),(500000,1000300),(500300,1000300),(500300,1000000),(500000,1000000)]
        right=[(500700,1000700),(500700,1001000),(501000,1001000),(501000,1000700),(500700,1000700)]
        shp(self.path,[left,right]);output=self.workspace/'multi';output.mkdir()
        cells,evidence=generate(self.config,output)
        self.assertEqual(evidence['boundary']['components'],2)
        self.assertAlmostEqual(evidence['mesh_union_area_m2'],180000)
        self.assertLess(evidence['symmetric_difference_area_m2'],1e-6)

    def test_output_isolated_from_shp_source(self):
        server=self.server();server.native_picker=lambda kind:[str(self.path)];server.choose_native({'kind':'boundary'})
        server.selected_files.add(self.raster.resolve());server.output_roots=(*server.output_roots,self.root)
        # This is a path-normalization guard test, not a Gmsh execution test.
        # Keep it runnable in a source checkout without the research executable.
        executable=self.root/'normalization-fixture.exe';executable.write_bytes(b'not executed')
        self.config['cell_source']['executable']=str(executable)
        self.config['output_root']=str(self.root)
        with self.assertRaisesRegex(ValueError,'isolated'):server.normalize_form(self.config)

    def test_recent_bound_dedup_clear_and_no_startup_authority(self):
        history=self.root/'recent.json'
        for i in range(13):
            file=self.root/(str(i)+'.tif');file.write_bytes(self.raster.read_bytes());remember(history,'raster',file)
        self.assertEqual(len(read_recent(history)),10)
        remember(history,'raster',file);self.assertEqual(len(read_recent(history)),10)
        server=self.server();self.assertEqual(server.selected_files,set())
        with self.assertRaises(AccessError):server.permitted(file)
        items=read_recent(history);self.assertEqual(server.selected_files,set())
        server.current_preflight='old';opened=server.recent_action({'id':items[0]['id']})
        self.assertEqual(server.permitted(file),file.resolve());self.assertIsNone(server.current_preflight)
        with self.assertRaises(AccessError):server.permitted(self.root/'0.tif')
        with self.assertRaises(ValueError):server.recent_action({'id':'unknown'})
        with self.assertRaises(ValueError):server.recent_action({'id':items[0]['id'],'path':str(file)})
        server.recent_action({'action':'clear'});self.assertEqual(read_recent(history),[])

    def test_boundary_picker_exact_bundle_and_recent_reopen(self):
        server=self.server();server.native_picker=lambda kind:[str(self.path)]
        server.choose_native({'kind':'boundary'})
        self.assertEqual(server.selected_files,set(bundle_paths(self.path)))
        with self.assertRaises(AccessError):server.permitted(self.raster)
        items=read_recent(server.recent_preferences);self.assertEqual(items[0]['kind'],'boundary')
        server.selected_files.clear();server.recent_action({'id':items[0]['id']})
        self.assertEqual(server.selected_files,set(bundle_paths(self.path)))
        self.path.with_suffix('.prj').unlink()
        with self.assertRaises(ValueError):server.recent_action({'id':items[0]['id']})

    def test_sample_not_recent_and_deleted_recent_failure(self):
        server=self.server();sample=server.sample_root/'sample_10_30.tif'
        server.remember_input('raster',sample);self.assertEqual(read_recent(server.recent_preferences),[])
        remember(server.recent_preferences,'raster',self.raster)
        ident=read_recent(server.recent_preferences)[0]['id'];self.raster.unlink()
        with self.assertRaises(FileNotFoundError):server.recent_action({'id':ident})

    def test_relief_excludes_outside_and_hole(self):
        config=self.config;config['cell_source']['sizing']='relief'
        config['cell_source']['extent']=[500000,1000000,501000,1001000]
        field=size_field(config,self.root);self.assertEqual(field['p95'],0)
        data=np.full((20,20),17.)
        data[:8,12:]=10000 # outside concave domain
        data[14:18,2:6]=10000 # inside hole
        with rasterio.open(self.raster,'r+') as dst:dst.write(data,1)
        field=size_field(config,self.root);self.assertEqual(field['p95'],0)
        self.assertEqual(field['valid_control_fraction'],1)
        self.assertLess(field['valid_fraction_full_control_grid'],1)

    def test_real_gmsh_true_polygon_hole_uniform_relief(self):
        if not EXE.is_file():self.skipTest('external Gmsh missing')
        domain,_=read_boundary(self.path,'EPSG:32649')
        for sizing in ('uniform','relief'):
            self.config['cell_source']['sizing']=sizing
            config=self.workspace/(sizing+'.json');write_json(config,self.config)
            result=run(config);self.assertEqual(result['status'],'pass',result)
            output=Path(result['output']);attrs=json.loads((output/'attributes.json').read_text())
            self.assertTrue(all(abs(c['value']-17)<1e-8 for c in attrs['cells']))
            self.assertLessEqual(len(attrs['cells']),1000)
            manifest=json.loads((output/'manifest.json').read_text());evidence=manifest['gmsh_generated']
            self.assertLess(evidence['symmetric_difference_area_m2'],1e-6)
            self.assertEqual(evidence['boundary']['holes'],1)
            self.assertTrue((output/'meshing/domain-boundary.json').is_file())

    def test_bundle_mutation_fails_after_extraction(self):
        if not EXE.is_file():self.skipTest('external Gmsh missing')
        from gis_mesh_workbench.indexed_extraction import extract_indexed
        def mutate(*args,**kwargs):
            result=extract_indexed(*args,**kwargs)
            self.path.with_suffix('.prj').write_text(CRS.from_epsg(32648).to_wkt())
            return result
        config=self.workspace/'mutation.json';write_json(config,self.config)
        with patch('gis_mesh_workbench.runner.extract_indexed',side_effect=mutate):result=run(config)
        self.assertEqual(result['status'],'fail');self.assertIn('SHP bundle changed',result['error'])

    def test_bundle_mutation_during_gmsh_rejected(self):
        if not EXE.is_file():self.skipTest('external Gmsh missing')
        import subprocess
        from gis_mesh_workbench.gmsh_meshing import generate
        real_run=subprocess.run
        def mutate(command,*args,**kwargs):
            result=real_run(command,*args,**kwargs)
            if '-2' in command:self.path.with_suffix('.prj').write_text(CRS.from_epsg(32648).to_wkt())
            return result
        output=self.workspace/'meshing-mutation';output.mkdir()
        with patch('gis_mesh_workbench.gmsh_meshing.subprocess.run',side_effect=mutate):
            with self.assertRaisesRegex(ValueError,'trong khi meshing'):generate(self.config,output)

    def test_more_than_20_components_rejected(self):
        rings=[]
        for i in range(21):
            x=500000+i*20;rings.append([(x,1000000),(x,1000010),(x+10,1000010),(x+10,1000000),(x,1000000)])
        shp(self.path,rings)
        with self.assertRaisesRegex(ValueError,'20 thành phần'):read_boundary(self.path,'EPSG:32649')

    def test_more_than_2000_vertices_preserved_with_notice(self):
        angle=np.linspace(0,-2*np.pi,2002)
        ring=[(500000+100*np.cos(a),1000000+100*np.sin(a)) for a in angle];ring[-1]=ring[0]
        shp(self.path,[ring])
        domain,report=read_boundary(self.path,'EPSG:32649')
        self.assertEqual(report['source_vertices'],2002)
        self.assertEqual(list(domain.exterior.coords),ring)
        self.assertFalse(report['simplification_applied'])
        self.assertIn('chạy lâu',report['warnings'][0])

    def test_more_than_100_records_rejected(self):
        with shapefile.Writer(str(self.path),shapeType=shapefile.POLYGON) as writer:
            writer.field('id','N')
            for i in range(101):writer.poly([self.outer]);writer.record(i)
        with self.assertRaisesRegex(ValueError,'1–100'):read_boundary(self.path,'EPSG:32649')

    def test_real_geographic_shp_transform_gmsh_extraction(self):
        if not EXE.is_file():self.skipTest('external Gmsh missing')
        shp(self.path,[[(109,13),(109,13.01),(109.01,13.01),(109.01,13),(109,13)]],'EPSG:4326')
        domain,_=read_boundary(self.path,'EPSG:32649');w,s,e,n=domain.bounds
        from rasterio.transform import from_bounds
        with rasterio.open(self.raster,'w',driver='GTiff',width=20,height=20,count=1,dtype='float64',
            crs='EPSG:32649',transform=from_bounds(w-5,s-5,e+5,n+5,20,20)) as dst:dst.write(np.full((20,20),17.),1)
        config=self.workspace/'reprojected.json';write_json(config,self.config)
        result=run(config);self.assertEqual(result['status'],'pass',result)
        output=Path(result['output']);evidence=json.loads((output/'manifest.json').read_text())['gmsh_generated']
        self.assertEqual(evidence['domain_coverage'],'pass')
        self.assertEqual(evidence['boundary']['transform_subdivisions'],8)
        self.assertGreater(evidence['boundary']['transformed_vertices'],evidence['boundary']['source_vertices'])
        self.assertLess(evidence['symmetric_difference_area_m2'],domain.area*1e-8)
        cells=json.loads((output/'attributes.json').read_text())['cells']
        self.assertTrue(all(abs(c['value']-17)<1e-8 for c in cells))
