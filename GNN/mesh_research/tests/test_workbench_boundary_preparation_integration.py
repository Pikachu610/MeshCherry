"""Boundary option validation, source preservation and fixed sizing support."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import shapefile
from rasterio.crs import CRS

from gis_mesh_workbench.boundary import bundle_paths
from gis_mesh_workbench.gmsh_meshing import mesh_domain, generate
from gis_mesh_workbench.meshing_parameters import request_summary
from gis_mesh_workbench.runner import validate_config


class BoundaryPreparationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.path=self.root/'roi.shp'
        with shapefile.Writer(str(self.path),shapeType=shapefile.POLYGON) as writer:
            writer.field('id','N')
            writer.poly([[(0,0),(0,100),(100,100),(100,0),(60,0),(50,.1),(40,0),(0,0)]])
            writer.record(1)
        self.path.with_suffix('.prj').write_text(CRS.from_epsg(32649).to_wkt(),encoding='utf-8')
        self.config=dict(schema_version='gis-workbench-run-config-v2',operation='mesh_only',
            extraction_engine='indexed_windowed_v1',run_name='derived-test',
            analysis_crs='EPSG:32649',output_root=str(self.root/'runs'),
            cell_source=dict(kind='gmsh_generated',executable='gmsh.exe',
                boundary_path=str(self.path),sizing='uniform',h_min_m=25,h_max_m=25,max_cells=1000))
        self.options=dict(tolerance_m=1,max_area_change_fraction=.01,max_boundary_distance_m=2)

    def test_old_config_remains_exact_raw_geometry(self):
        validate_config(self.config)
        domain,evidence=mesh_domain(self.config)
        self.assertFalse(evidence['simplification_applied'])
        self.assertEqual(len(domain.exterior.coords),8)

    def test_opt_in_preserves_source_and_exposes_gate_evidence(self):
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle_paths(self.path)}
        self.config['cell_source']['boundary_preparation']=self.options
        validate_config(self.config)
        derived,evidence=mesh_domain(self.config)
        canonical,raw=mesh_domain(self.config,prepared=False)
        self.assertGreater(len(canonical.exterior.coords),len(derived.exterior.coords))
        self.assertTrue(evidence['derived_boundary']['accepted'])
        self.assertFalse(raw['simplification_applied'])
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle_paths(self.path)})
        summary=request_summary(self.config,evidence)
        self.assertTrue(summary['boundary_vertices']['simplification_applied'])
        self.assertEqual(summary['boundary_preparation'],self.options)

    def test_rejects_option_without_shp(self):
        self.config['cell_source'].pop('boundary_path')
        self.config['cell_source'].update(extent=[0,0,100,100],boundary_preparation=self.options)
        with self.assertRaises(ValueError):validate_config(self.config)

    def test_gnn_method_survives_validation_and_domain_generation(self):
        self.config['cell_source']['boundary_preparation']={**self.options,'method':'gnn_closed_rdp'}
        validate_config(self.config)
        domain,evidence=mesh_domain(self.config)
        self.assertEqual(evidence['derived_boundary']['method'],'gnn_closed_rdp')
        self.assertTrue(domain.is_valid)
        self.assertEqual(request_summary(self.config,evidence)['boundary_preparation']['method'],'gnn_closed_rdp')

    def test_rejects_unknown_method_and_unknown_keys(self):
        for changes in ({'method':'typo'},{'method':[]},{'method':False},{'unexpected':1}):
            config=copy.deepcopy(self.config)
            config['cell_source']['boundary_preparation']={**self.options,**changes}
            with self.subTest(changes=changes),self.assertRaises(ValueError):validate_config(config)

    def test_project_scrubber_retains_method_but_not_local_boundary_path(self):
        from gis_mesh_workbench.project_io import _safe_config
        self.config['cell_source']['boundary_preparation']={**self.options,'method':'gnn_closed_rdp'}
        safe,unavailable=_safe_config(self.config)
        self.assertIsNone(safe['cell_source']['boundary_path'])
        self.assertEqual(safe['cell_source']['boundary_preparation'],self.config['cell_source']['boundary_preparation'])
        self.assertTrue(unavailable)

    def test_generation_normalizes_sf1_on_canonical_not_derived_domain(self):
        self.config['cell_source'].update(boundary_preparation=self.options,sizing='sf1_relief')
        canonical,_=mesh_domain(self.config,prepared=False)
        derived,boundary=mesh_domain(self.config)
        self.assertFalse(canonical.equals(derived))
        output=self.root/'output';output.mkdir()
        class StopAtField(Exception):pass
        with patch('gis_mesh_workbench.gmsh_meshing.raster_snapshots',return_value={}), \
             patch('gis_mesh_workbench.gmsh_meshing.inspect_domain',return_value={'boundary':boundary}), \
             patch('gis_mesh_workbench.sf1_sizing.write_field',side_effect=StopAtField) as writer:
            with self.assertRaises(StopAtField):generate(self.config,output)
        self.assertTrue(writer.call_args.args[2].equals_exact(canonical,0))

    def test_rejects_unsafe_or_incomplete_settings(self):
        for changes in ({'tolerance_m':0},{'max_area_change_fraction':.1},
                        {'max_boundary_distance_m':float('nan')},{'tolerance_m':True}):
            config=copy.deepcopy(self.config)
            config['cell_source']['boundary_preparation']={**self.options,**changes}
            with self.subTest(changes=changes),self.assertRaises(ValueError):validate_config(config)


if __name__=='__main__':unittest.main()
