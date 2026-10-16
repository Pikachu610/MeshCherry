import hashlib
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.warp import transform

from gis_mesh_workbench.extraction import Cell, extract_attributes


class ExtractionCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'sample.tif'
        self.crs = 'EPSG:32649'
        self.affine = Affine(1, 0, 500000, 0, -1, 1000001)
        self.rect = Cell('rect', 'rectangle', ((500000,1000000),(500002,1000000),
                                              (500002,1000001),(500000,1000001)))
        self.triangles = [
            Cell('t0', 'triangle', (self.rect.xy[0], self.rect.xy[1], self.rect.xy[2])),
            Cell('t1', 'triangle', (self.rect.xy[0], self.rect.xy[2], self.rect.xy[3])),
        ]

    def write(self, data=((10,30),), *, crs='EPSG:32649', affine=None, mask=None):
        values = np.array(data, dtype='float64')
        with rasterio.open(self.path, 'w', driver='GTiff', width=values.shape[1],
                           height=values.shape[0], count=1, dtype='float64',
                           crs=crs, transform=affine or self.affine) as dst:
            dst.write(values, 1)
            if mask is not None:
                dst.write_mask(np.array(mask, dtype='uint8'))

    def extract(self, cells=None, semantics='intensive', **kwargs):
        options = dict(cell_crs=self.crs, analysis_crs=self.crs, band=1,
                       semantics=semantics, value_units='test-unit')
        options.update(kwargs)
        return extract_attributes(self.path, cells or [self.rect], **options)

    def test_triangle_and_rectangle_intensive_hand_values(self):
        self.write()
        self.assertEqual(self.extract()['cells'][0]['value'], 20)
        values = [c['value'] for c in self.extract(self.triangles)['cells']]
        np.testing.assert_allclose(values, [25, 15])

    def test_extensive_partition_conserves_pixel_total(self):
        self.write()
        for cells in ([self.rect], self.triangles):
            result = self.extract(cells, 'extensive')
            self.assertAlmostEqual(result['qa']['cell_total'], 40)
            self.assertAlmostEqual(result['qa']['residual'], 0)

    def test_categories_fractions_tie_and_zero_valid(self):
        self.write(((0,2),))
        record = self.extract(semantics='categorical')['cells'][0]
        self.assertEqual(record['class_fractions'], {'0.0': .5, '2.0': .5})
        self.assertEqual(record['value'], 0)
        self.assertEqual(record['valid_coverage'], 1)

    def test_mask_coverage_not_zero_and_outside_distinct(self):
        self.write(((10,30),), mask=((255,0),))
        big = Cell('big', 'rectangle', ((500000,1000000),(500004,1000000),
                                        (500004,1000001),(500000,1000001)))
        record = self.extract([big])['cells'][0]
        self.assertEqual(record['footprint_coverage'], .5)
        self.assertEqual(record['valid_coverage'], .25)
        self.assertEqual(record['value'], 10)
        self.assertEqual(self.extract([big], 'extensive')['cells'][0]['value'], 10)

    def test_no_valid_area_is_null_for_all_semantics(self):
        self.write(((np.nan,np.inf),))
        for semantics in ('intensive','extensive','categorical'):
            record = self.extract(semantics=semantics)['cells'][0]
            self.assertIsNone(record['value'])
            self.assertEqual(record['valid_coverage'], 0)
            self.assertEqual(record['footprint_coverage'], 1)

    def test_rotated_affine_actual_pixel_geometry(self):
        affine = Affine(1,.3,500000,.2,-1,1000001)
        self.write(((7,),), affine=affine)
        corners = tuple(affine @ p for p in ((0,0),(1,0),(1,1)))
        cell = Cell('rotated', 'triangle', corners)
        record = self.extract([cell])['cells'][0]
        self.assertAlmostEqual(record['area_m2'], .53)
        self.assertAlmostEqual(record['valid_coverage'], 1)
        self.assertAlmostEqual(record['value'], 7)

    def test_reprojection_longitude_latitude_not_relabeling(self):
        affine = Affine(.01,0,10,0,-.01,20.01)
        self.write(((9,),), crs='EPSG:4326', affine=affine)
        x,y = transform('EPSG:4326','EPSG:3857',[10.005],[20.005])
        self.assertAlmostEqual(x[0],6378137 * math.radians(10.005), places=4)
        self.assertAlmostEqual(y[0],6378137 * math.log(math.tan(math.pi/4 + math.radians(20.005)/2)), places=4)
        center = (x[0],y[0])
        cell = Cell('projected','rectangle', tuple((center[0]+dx,center[1]+dy)
                                                 for dx,dy in ((-5,-5),(5,-5),(5,5),(-5,5))))
        result = self.extract([cell], cell_crs='EPSG:3857', analysis_crs='EPSG:3857')
        self.assertEqual(result['cells'][0]['value'],9)
        self.assertAlmostEqual(result['cells'][0]['valid_coverage'],1)
        sx,sy = transform('EPSG:4326','EPSG:3857',[20.005],[10.005])
        swapped = Cell('swapped','rectangle',tuple((sx[0]+dx,sy[0]+dy)
                      for dx,dy in ((-5,-5),(5,-5),(5,5),(-5,5))))
        self.assertIsNone(self.extract([swapped],cell_crs='EPSG:3857',
                                      analysis_crs='EPSG:3857')['cells'][0]['value'])

    def test_crs_guards(self):
        self.write(crs=None)
        with self.assertRaisesRegex(ValueError,'thiếu CRS'):
            self.extract()
        self.write()
        with self.assertRaisesRegex(ValueError,'projected'):
            self.extract(analysis_crs='EPSG:4326')
        with self.assertRaisesRegex(ValueError,'relabeling'):
            self.extract(cell_crs='EPSG:3857')

    def test_cell_validation_and_overlap(self):
        self.write()
        duplicate = Cell('other','rectangle',self.rect.xy)
        with self.assertRaisesRegex(ValueError,'overlap'):
            self.extract([self.rect,duplicate])
        with self.assertRaisesRegex(ValueError,'unique'):
            self.extract([self.rect,self.rect])
        invalid = Cell('invalid','triangle',((0,0),(1,0),(2,0)))
        with self.assertRaisesRegex(ValueError,'positive'):
            self.extract([invalid])

    def test_immutability_and_provenance(self):
        self.write()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        geometry = self.rect.xy
        result = self.extract()
        self.assertEqual(result['provenance']['source_sha256'],before)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
        self.assertEqual(self.rect.xy,geometry)
        self.assertEqual(result['provenance']['raster_resampling'],'none')
        self.assertEqual(result['provenance']['hash_scope'], 'raster_dataset_bundle_v1')
        self.assertIn(str(self.path.resolve()), result['provenance']['source_files_sha256'])
        self.assertTrue(result['qa']['main_raster_unchanged'])
        self.assertNotIn('source_unchanged', result['qa'])
        self.assertEqual(result['provenance']['band_dtype'], 'float64')
        self.assertEqual(result['provenance']['band_scale'], 1)
        self.assertEqual(result['provenance']['band_offset'], 0)
        self.assertIsNone(result['provenance']['band_nodata'])
        self.assertEqual(result['provenance']['band_mask_flags'], ['all_valid'])

    def test_band_scale_and_offset_rejected(self):
        for scale, offset in ((.1, 0), (1, 10)):
            self.write()
            with rasterio.open(self.path, 'r+') as dst:
                dst.scales = (scale,)
                dst.offsets = (offset,)
            with self.assertRaisesRegex(ValueError, 'scale/offset'):
                self.extract()

    def test_high_aspect_skew_rectangle_rejected(self):
        self.write()
        skew = Cell('skew', 'rectangle', ((0,0),(1e6,0),(1e6+1e-5,1),(1e-5,1)))
        with self.assertRaisesRegex(ValueError, 'perpendicular'):
            self.extract([skew])

    def test_projected_feet_crs_rejected(self):
        self.write()
        with self.assertRaisesRegex(ValueError, 'metre'):
            self.extract(cell_crs='EPSG:2263', analysis_crs='EPSG:2263')

    def test_workload_limit_and_required_semantics(self):
        self.write()
        with self.assertRaisesRegex(ValueError,'workload'):
            self.extract(max_pixels=1)
        with self.assertRaisesRegex(ValueError,'semantics'):
            self.extract(semantics='density')


if __name__ == '__main__':
    unittest.main()
