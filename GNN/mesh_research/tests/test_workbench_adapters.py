import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine

from gis_mesh_workbench.adapters import import_mesh, structured_grid
from gis_mesh_workbench.extraction import extract_attributes


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'mesh' / 'mesh.json'
        self.path.parent.mkdir()
        self.mesh = dict(schema_version='mesh-artifact-v0', crs='EPSG:32649',
                         coordinate_units='m', vertices=[[0,0],[2,0],[2,1],[0,1]],
                         triangles=[[0,1,2],[0,2,3]])
        self.write()

    def write(self):
        self.path.write_text(json.dumps(self.mesh), encoding='utf-8')

    def load(self):
        return import_mesh(self.path, analysis_crs='EPSG:32649')

    def grid(self, **kwargs):
        options = dict(extent=(0,0,2,1), cell_width_m=1, cell_height_m=1,
                       analysis_crs='EPSG:32649')
        options.update(kwargs)
        return structured_grid(**options)

    def test_import_ids_and_immutable_hash(self):
        before = self.path.read_bytes()
        cells = self.load()
        self.assertEqual([c.cell_id for c in cells.cells], ['cell-0','cell-1'])
        self.assertEqual(cells.provenance['source_sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIsInstance(cells.cells, tuple)
        with self.assertRaises(TypeError):
            cells.provenance['source_path'] = 'changed'

    def test_manifest_provenance_and_hash_mismatch(self):
        manifest = dict(schema_version='mesh-run-manifest-v0', status='pass', run_id='test-run',
                        artifact_hashes={'mesh/mesh.json': hashlib.sha256(self.path.read_bytes()).hexdigest()})
        path = self.root / 'manifest.json'
        path.write_text(json.dumps(manifest), encoding='utf-8')
        result = self.load()
        self.assertEqual(result.provenance['source_run_id'], 'test-run')
        self.assertEqual(result.provenance['run_provenance'], 'pass_manifest_hash_verified')
        manifest['artifact_hashes']['mesh/mesh.json'] = 'wrong'
        path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.load()
        manifest['status'] = 'fail'
        path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'passing'):
            self.load()

    def test_graph_id_mismatch(self):
        path = self.root / 'graph' / 'graph.json'
        path.parent.mkdir()
        path.write_text(json.dumps(dict(schema_version='mesh-graph-v0', nodes=[
            dict(cell_id='cell-0'), dict(cell_id='cell-1')])), encoding='utf-8')
        self.assertEqual(len(self.load().cells), 2)
        path.write_text(json.dumps(dict(schema_version='mesh-graph-v0', nodes=[
            dict(cell_id='cell-1'), dict(cell_id='cell-0')])), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'ID order'):
            self.load()

    def test_invalid_metadata(self):
        for key, value in [('schema_version','bad'),('coordinate_units','feet'),
                           ('crs',None),('crs','EPSG:4326'),('crs','EPSG:2263')]:
            with self.subTest(key=key, value=value):
                original = self.mesh[key]
                self.mesh[key] = value
                self.write()
                with self.assertRaises(ValueError):
                    self.load()
                self.mesh[key] = original
        self.write()
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            import_mesh(self.path, analysis_crs='EPSG:3857')

    def test_invalid_vertices_connectivity(self):
        for triangle in ([0,0,1],[0,1,9],[True,1,2],[0,1.0,2],[0,1]):
            self.mesh['triangles'] = [triangle]
            self.write()
            with self.assertRaisesRegex(ValueError, 'indices'):
                self.load()
        self.mesh['triangles'] = [[0,1,2]]
        self.mesh['vertices'][0][0] = float('nan')
        self.write()
        with self.assertRaises(ValueError):
            self.load()

    def test_grid_order_and_strict(self):
        result = self.grid(extent=(0,0,2,2))
        self.assertEqual([c.cell_id for c in result.cells], [
            'grid-r000-c000','grid-r000-c001','grid-r001-c000','grid-r001-c001'])
        self.assertEqual(result.cells[2].xy[0], (0,1))
        with self.assertRaisesRegex(ValueError, 'divide'):
            self.grid(extent=(0,0,2.5,1))

    def test_xyz_is_planar_and_dimension_recorded(self):
        self.mesh['vertices'] = [v + [index * 100] for index, v in enumerate(self.mesh['vertices'])]
        self.write()
        result = self.load()
        self.assertEqual(result.provenance['source_vertex_dimension'], 3)
        self.assertEqual(result.cells[0].xy, ((0,0),(2,0),(2,1)))
        self.mesh['vertices'][0][2] = float('inf')
        self.write()
        with self.assertRaisesRegex(ValueError, 'nonfinite'):
            self.load()

    def test_ragged_short_and_nonnumeric_vertices(self):
        for vertices in ([[0],[1,0],[1,1]], [[0,0],[1,0,0],[1,1]],
                         [['0',0],[1,0],[1,1]], [[0,0,0,0],[1,0,0,0],[1,1,0,0]]):
            self.mesh['vertices'] = vertices
            self.write()
            with self.assertRaisesRegex(ValueError, 'uniform finite'):
                self.load()

    def test_clip_preserves_extent(self):
        cells = self.grid(extent=(0,0,2.5,1.5), boundary_policy='clip').cells
        self.assertEqual(len(cells), 6)
        self.assertEqual(cells[-1].xy, ((2,1),(2.5,1),(2.5,1.5),(2,1.5)))

    def test_invalid_requests_and_count_guard(self):
        for kwargs in [dict(cell_width_m=0),dict(cell_height_m=-1),
                       dict(extent=(2,0,1,1)),dict(extent=(0,0,float('inf'),1)),
                       dict(boundary_policy='expand'),dict(max_cells=1),
                       dict(extent=(0,0,100000,100000))]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.grid(**kwargs)

    def test_both_adapters_into_extraction_and_ids_retained(self):
        self.mesh['vertices'] = [v + [index * 100] for index, v in enumerate(self.mesh['vertices'])]
        self.write()
        raster = self.root / 'sample.tif'
        with rasterio.open(raster, 'w', driver='GTiff', width=2, height=1,
                           count=1, dtype='float64', crs='EPSG:32649',
                           transform=Affine(1,0,0,0,-1,1)) as dst:
            dst.write(np.array([[10,30]], dtype=float), 1)
        for cellset in (self.load(), self.grid()):
            result = extract_attributes(raster, cellset.cells, cell_crs=cellset.crs_wkt,
                analysis_crs=cellset.crs_wkt, band=1, semantics='extensive', value_units='test')
            self.assertEqual(result['qa']['cell_total'], 40)
            self.assertEqual([r['cell_id'] for r in result['cells']], [c.cell_id for c in cellset.cells])

    def test_retained_real_gmsh_run(self):
        path = Path(__file__).resolve().parents[2] / 'mesh_runs/20260926T022613457879Z-db0a5a7d/mesh/mesh.json'
        if not path.exists():
            self.skipTest('retained artifact not available on this checkout')
        result = import_mesh(path, analysis_crs='EPSG:32649')
        self.assertEqual(len(result.cells), 40)
        self.assertEqual(result.provenance['source_vertex_dimension'], 3)
        self.assertEqual(result.provenance['run_provenance'], 'pass_manifest_hash_verified')
        self.assertIsNotNone(result.provenance['graph_sha256'])


if __name__ == '__main__':
    unittest.main()
