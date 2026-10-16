"""Dataset identity includes GDAL companions, not only the main raster bytes."""
from pathlib import Path
from contextlib import contextmanager
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from affine import Affine
from gis_mesh_workbench.raster_source import bundle_paths, snapshot, assert_unchanged, open_raster, raster_snapshots, assert_rasters_unchanged
from gis_mesh_workbench.extraction import Cell, extract_attributes
from gis_mesh_workbench.indexed_extraction import extract_indexed


class RasterIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'input.tif'
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=False):
            with rasterio.open(self.path, 'w', driver='GTiff', width=2, height=1,
                    count=1, dtype='float64', crs='EPSG:32649',
                    transform=Affine(1, 0, 500000, 0, -1, 1000001)) as dst:
                dst.write(np.array([[10., 30.]]), 1)
                dst.write_mask(np.array([[255, 0]], dtype='uint8'))
        self.cell = Cell('one', 'rectangle', ((500000,1000000),(500002,1000000),
                                              (500002,1000001),(500000,1000001)))

    def test_tiff_mask_and_metadata_add_change_remove(self):
        mask = Path(str(self.path) + '.msk')
        self.assertTrue(mask.exists())
        self.assertIn(mask, bundle_paths(self.path))
        initial = snapshot(self.path)
        aux = Path(str(self.path) + '.aux.xml')
        aux.write_text('<PAMDataset/>')
        with self.assertRaises(ValueError): assert_unchanged(self.path, initial)
        before = snapshot(self.path)
        aux.write_text('<PAMDataset> </PAMDataset>')
        with self.assertRaises(ValueError): assert_unchanged(self.path, before)
        before = snapshot(self.path)
        aux.unlink()
        with self.assertRaises(ValueError): assert_unchanged(self.path, before)

    def test_both_extractors_record_mask_hash_and_masked_value(self):
        for extractor in (extract_attributes, extract_indexed):
            with self.subTest(extractor=extractor.__name__):
                result = extractor(self.path, [self.cell], cell_crs='EPSG:32649',
                    analysis_crs='EPSG:32649', band=1, semantics='intensive', value_units='m')
                self.assertEqual(result['cells'][0]['value'],10.)
                self.assertEqual(result['cells'][0]['valid_coverage'],.5)
                self.assertEqual(result['provenance']['source_files_sha256'], snapshot(self.path))
                self.assertEqual(result['provenance']['hash_scope'], 'raster_dataset_bundle_v1')

    def test_non_geotiff_disguised_with_tif_suffix_rejected(self):
        from PIL import Image
        disguised = self.path.parent / 'disguised.tif'
        Image.new('L',(2,2)).save(disguised,format='PNG')
        with self.assertRaisesRegex(ValueError,'GeoTIFF|định dạng'):
            with open_raster(disguised,require_crs=False): pass

    def test_added_companion_during_either_extractor_is_rejected(self):
        aux = Path(str(self.path) + '.aux.xml')
        @contextmanager
        def mutating_reader(*args, **kwargs):
            with open_raster(*args, **kwargs) as src: yield src
            aux.write_text('<PAMDataset/>')
        for extractor, module in ((extract_attributes,'extraction'),(extract_indexed,'indexed_extraction')):
            with self.subTest(extractor=module):
                aux.unlink(missing_ok=True)
                with patch('gis_mesh_workbench.'+module+'.open_raster',mutating_reader):
                    with self.assertRaisesRegex(ValueError,'thay đổi'):
                        extractor(self.path,[self.cell],cell_crs='EPSG:32649',analysis_crs='EPSG:32649',
                                  band=1,semantics='intensive',value_units='m')

    def test_every_raster_role_including_advisory_is_snapshotted(self):
        for config in (dict(raster_path=str(self.path)),
                       dict(cell_source=dict(roi_raster_path=str(self.path))),
                       dict(cell_source=dict(dem_raster_path=str(self.path))),
                       dict(resolution_reference=dict(path=str(self.path)))):
            with self.subTest(config=config):
                before=raster_snapshots(config)
                self.assertEqual(before,{str(self.path):snapshot(self.path)})
                mask=Path(str(self.path)+'.msk');old=mask.read_bytes()
                try:
                    mask.write_bytes(old+b'changed')
                    with self.assertRaisesRegex(ValueError,'thay đổi'):assert_rasters_unchanged(before)
                finally:mask.write_bytes(old)
