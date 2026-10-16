"""Original detailed ROI survives meshing and later extraction without decimation."""
import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import Polygon
import shapefile

from gis_mesh_workbench.boundary import read_boundary, geometry_lines, bundle_paths
from gis_mesh_workbench.extraction import Cell, _cells, _digest
from gis_mesh_workbench.runner import run, effective_config, write_json, validate_config
from gis_mesh_workbench.mesh_workflow import read_frozen

EXE=Path(__file__).resolve().parents[3]/'Test_chialuoi/gmsh-4.15.2-Windows64/gmsh.exe'


class OriginalBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.shp=self.root/'dense.shp';self.config=self.root/'config.json'
        self.output=self.root/'runs';self.output.mkdir()
        self.ring=[]
        for i in range(2200):
            angle=-2*math.pi*i/2200;radius=1000+3*math.sin(17*angle)
            self.ring.append((500000+radius*math.cos(angle),1000000+radius*math.sin(angle)))
        self.ring.append(self.ring[0])
        self.hole=[(499900,999900),(500100,999900),(500100,1000100),(499900,1000100),(499900,999900)]
        self.write_shape([self.ring,self.hole])

    def write_shape(self,rings,crs='EPSG:32649'):
        with shapefile.Writer(str(self.shp),shapeType=shapefile.POLYGON) as writer:
            writer.field('id','N');writer.poly(rings);writer.record(1)
        self.shp.with_suffix('.prj').write_text(CRS.from_string(crs).to_wkt(),encoding='utf-8')

    def test_dense_original_points_hole_and_warning(self):
        hashes={p:_digest(p) for p in bundle_paths(self.shp)}
        domain,report=read_boundary(self.shp,'EPSG:32649')
        self.assertEqual(report['source_vertices'],2206)
        self.assertEqual(report['transformed_vertices'],2206)
        self.assertFalse(report['simplification_applied']);self.assertTrue(report['high_detail'])
        self.assertIn('chạy lâu',report['warnings'][0]);self.assertEqual(report['holes'],1)
        self.assertEqual(list(domain.exterior.coords),self.ring)
        self.assertEqual(list(domain.interiors[0].coords),self.hole)
        self.assertEqual(sum(line.startswith('Point(') for line in geometry_lines(domain)),2204)
        self.assertAlmostEqual(domain.area,Polygon(self.ring,[self.hole]).area,places=6)
        self.assertEqual(hashes,{p:_digest(p) for p in bundle_paths(self.shp)})

    def test_dense_geographic_transform_retains_all_points_and_hole(self):
        ring=[(109+(x-500000)/100000,13+(y-1000000)/100000) for x,y in self.ring]
        hole=[(109+(x-500000)/100000,13+(y-1000000)/100000) for x,y in self.hole]
        self.write_shape([ring,hole],'EPSG:4326')
        _,report=read_boundary(self.shp,'EPSG:32649')
        self.assertEqual(report['source_vertices'],2206)
        self.assertEqual(report['transformed_vertices'],2204*8+2)
        self.assertEqual(report['holes'],1);self.assertFalse(report['simplification_applied'])

    def test_index_keeps_overlap_predicate_and_accepts_many_disjoint_cells(self):
        cells=tuple(Cell(str(i),'triangle',((i*2.,0.),(i*2.+1,0.),(i*2.,1.))) for i in range(2500))
        self.assertEqual(len(_cells(cells)),2500)
        with self.assertRaisesRegex(ValueError,'overlap'):
            _cells(cells+(Cell('overlap','triangle',((0.,0.),(.5,0.),(0.,.5))),))
        _cells((Cell('a','triangle',((0.,0.),(1.,0.),(0.,1.))),
                Cell('b','triangle',((1.,0.),(1.,1.),(0.,1.)))))

    def mesh_config(self):
        return dict(schema_version='gis-workbench-run-config-v2',operation='mesh_only',
            extraction_engine='indexed_windowed_v1',run_name='original-boundary',analysis_crs='EPSG:32649',
            output_root=str(self.output),cell_source=dict(kind='gmsh_generated',executable=str(EXE),
            boundary_path=str(self.shp),sizing='uniform',h_min_m=400,h_max_m=400))

    def test_v2_default_capacity_and_explicit_legacy_limit(self):
        write_json(self.config,self.mesh_config());config=effective_config(self.config)
        self.assertEqual(config['cell_source']['max_cells'],100000);validate_config(config)
        config.update(schema_version='gis-workbench-run-config-v1',raster_path='dem.tif',band=1,
            semantics='intensive',value_units='m');config['cell_source']['relief_radius_m']=50
        with self.assertRaises(ValueError):validate_config(config)

    def test_real_gmsh_dense_boundary_then_frozen_extraction(self):
        if not EXE.is_file():self.skipTest('Gmsh missing')
        hashes={p:_digest(p) for p in bundle_paths(self.shp)}
        config=self.mesh_config();write_json(self.config,config);result=run(self.config)
        self.assertEqual(result['status'],'pass',result)
        folder=Path(result['output']);self.assertGreater(len(json.loads((folder/'cells.json').read_text())['cells']),2000)
        qa=json.loads((folder/'qa.json').read_text())['mesh_quality']
        self.assertEqual(qa['hole_count'],1);self.assertEqual(qa['domain_coverage'],'pass')
        self.assertLess(qa['domain_symmetric_difference_m2'],.01)
        self.assertFalse(qa['sizing_summary']['boundary_vertices']['simplification_applied'])
        identity=qa['geometry_digest'];before=(folder/'cells.json').read_bytes()
        read_frozen(folder/'cells.json','EPSG:32649',100000,identity)
        raster=self.root/'values.tif'
        with rasterio.open(raster,'w',driver='GTiff',width=12,height=12,count=1,dtype='float64',
            crs='EPSG:32649',transform=from_origin(498800,1001200,200,200)) as dst:
            dst.write(np.full((12,12),7.),1)
        config.update(operation='extract',raster_path=str(raster),band=1,semantics='intensive',value_units='m',
            cell_source=dict(kind='frozen_cells',path=str(folder/'cells.json'),geometry_digest=identity))
        write_json(self.config,config);extracted=run(self.config)
        self.assertEqual(extracted['status'],'pass',extracted)
        destination=Path(extracted['output'])
        self.assertEqual((folder/'cells.json').read_bytes(),before)
        self.assertEqual(json.loads((destination/'cells.json').read_text())['cells'],json.loads(before)['cells'])
        values=json.loads((destination/'attributes.json').read_text())['cells']
        self.assertTrue(all(v['value'] is not None and abs(v['value']-7)<1e-8 for v in values))
        self.assertEqual(hashes,{p:_digest(p) for p in bundle_paths(self.shp)})
        print(f"Original ROI: {qa['sizing_summary']['boundary_vertices']['source']} points; mesh: {qa['cell_count']} cells; holes={qa['hole_count']}; unchanged IDs/XY after extraction; area difference={qa['domain_symmetric_difference_m2']}")
