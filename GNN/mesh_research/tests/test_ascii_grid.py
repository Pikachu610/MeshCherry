import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rasterio.crs import CRS
from gis_mesh_workbench.raster_source import open_raster, snapshot, assert_unchanged
from gis_mesh_workbench.raster_resolution import read
from gis_mesh_workbench.adapters import structured_grid
from gis_mesh_workbench.extraction import extract_attributes
from gis_mesh_workbench.indexed_extraction import extract_indexed
from gis_mesh_workbench.runner import run, write_json


class ASCIIGridTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'sample.asc'
        self.write()
        self.cells=structured_grid(extent=[500000,1000000,500002,1000001],
            cell_width_m=1,cell_height_m=1,analysis_crs='EPSG:32649').cells

    def write(self, values='10 30', center=False, crs=True):
        origin='xllcenter 500000.5\nyllcenter 1000000.5' if center else 'xllcorner 500000\nyllcorner 1000000'
        self.path.write_text(f'ncols 2\nnrows 1\n{origin}\ncellsize 1\nNODATA_value -9999\n{values}\n',encoding='ascii')
        if crs:self.path.with_suffix('.prj').write_text(CRS.from_epsg(32649).to_wkt(),encoding='ascii')

    def extract(self, engine=extract_attributes):
        return engine(self.path,self.cells,cell_crs='EPSG:32649',analysis_crs='EPSG:32649',
            band=1,semantics='intensive',value_units='m')

    def test_corner_center_and_pixel_resolution(self):
        with open_raster(self.path) as src:
            expected=src.transform;self.assertEqual(src.driver,'AAIGrid');self.assertEqual(src.crs.to_epsg(),32649)
        self.write(center=True)
        with open_raster(self.path) as src:self.assertEqual(src.transform,expected)
        self.assertEqual(read(self.path,'EPSG:32649')['representative_pixel_size_m'],[1,1])

    def test_float64_precision_both_engines_and_metadata_hash(self):
        self.write(values='10.123456789 30.987654321')
        for engine in (extract_attributes,extract_indexed):
            result=self.extract(engine)
            for row,expected in zip(result['cells'],[10.123456789,30.987654321]):self.assertAlmostEqual(row['value'],expected,places=10)
            self.assertEqual(result['provenance']['band_dtype'],'float64')
            self.assertEqual(len(result['provenance']['source_files_sha256']),2)

    def test_nodata_is_null_not_zero(self):
        self.write(values='10 -9999')
        for engine in (extract_attributes,extract_indexed):
            rows=self.extract(engine)['cells'];self.assertEqual(rows[0]['value'],10)
            self.assertIsNone(rows[1]['value']);self.assertEqual(rows[1]['footprint_coverage'],1)
            self.assertEqual(rows[1]['valid_coverage'],0)

    def test_missing_crs_and_wrong_format_rejected(self):
        self.path.with_suffix('.prj').unlink()
        with self.assertRaisesRegex(ValueError,'ASC thiếu CRS'):
            with open_raster(self.path):pass
        with open_raster(self.path,require_crs=False) as src:self.assertIsNone(src.crs)
        self.path.write_text('not an ASCII grid',encoding='ascii')
        with self.assertRaises(Exception):
            with open_raster(self.path):pass

    def test_metadata_changes_and_additions_detected(self):
        before=snapshot(self.path)
        self.path.with_suffix('.prj').write_text(CRS.from_epsg(32648).to_wkt(),encoding='ascii')
        with self.assertRaisesRegex(ValueError,'thay đổi'):assert_unchanged(self.path,before)
        self.write();before=snapshot(self.path)
        Path(str(self.path)+'.aux.xml').write_text('<PAMDataset/>',encoding='ascii')
        with self.assertRaisesRegex(ValueError,'thay đổi'):assert_unchanged(self.path,before)

    def test_mesh_first_then_frozen_ASC_extraction(self):
        cfg=dict(schema_version='gis-workbench-run-config-v2',operation='mesh_only',
            extraction_engine='indexed_windowed_v1',run_name='ascii',analysis_crs='EPSG:32649',
            output_root=str(self.root/'output'),cell_source=dict(kind='structured_grid',
                roi_raster_path=str(self.path),extent=[500000,1000000,500002,1000001],
                cell_width_m=1,cell_height_m=1,boundary_policy='strict',max_cells=1000))
        file=self.root/'config.json';write_json(file,cfg);mesh=run(file)
        self.assertEqual(mesh['status'],'pass',mesh);folder=Path(mesh['output'])
        geometry=(folder/'cells.json').read_bytes()
        cfg.update(schema_version='gis-workbench-run-config-v1',operation='extract',raster_path=str(self.path),band=1,semantics='intensive',value_units='m',
            cell_source=dict(kind='frozen_cells',path=str(folder/'cells.json'),max_cells=1000))
        write_json(file,cfg)
        with patch('gis_mesh_workbench.runner.generate_gmsh',side_effect=AssertionError('no remeshing')):result=run(file)
        self.assertEqual(result['status'],'pass',result)
        self.assertEqual((folder/'cells.json').read_bytes(),geometry)
        rows=json.loads((Path(result['output'])/'attributes.json').read_text())['cells']
        self.assertEqual([r['value'] for r in rows],[10,30])

    def test_native_picker_grants_ASC_bundle_not_parent(self):
        from gis_mesh_workbench.webui import WorkbenchServer
        workspace=self.root/'workspace';workspace.mkdir()
        server=WorkbenchServer(workspace);self.addCleanup(server.server_close)
        server.native_picker=lambda kind:[str(self.path)]
        server.choose_native({'kind':'raster'})
        self.assertEqual(server.permitted(self.path),self.path)
        self.assertEqual(server.permitted(self.path.with_suffix('.prj')),self.path.with_suffix('.prj'))
        unrelated=self.root/'unrelated.asc';unrelated.write_text('secret',encoding='ascii')
        with self.assertRaises(ValueError):server.permitted(unrelated)

    def test_ASC_ROI_relief_real_Gmsh(self):
        from gis_mesh_workbench.gmsh_reference import PRESET_PATH
        preset=json.loads(PRESET_PATH.read_text())
        exe=(PRESET_PATH.parent.parent/preset['generator']['executable']).resolve()
        if not exe.is_file():self.skipTest('External Gmsh unavailable')
        cfg=dict(schema_version='gis-workbench-run-config-v2',operation='mesh_only',
            extraction_engine='indexed_windowed_v1',run_name='ascii-gmsh',analysis_crs='EPSG:32649',
            output_root=str(self.root/'output'),cell_source=dict(kind='gmsh_generated',executable=str(exe),
                roi_raster_path=str(self.path),dem_raster_path=str(self.path),dem_band=1,
                sizing='relief',h_min_m=.3,h_max_m=.6,relief_radius_m=.2,max_cells=1000))
        file=self.root/'config.json';write_json(file,cfg);mesh=run(file)
        self.assertEqual(mesh['status'],'pass',mesh)
        geometry=json.loads((Path(mesh['output'])/'cells.json').read_text())['cells']
        self.assertGreater(len(geometry),2)
