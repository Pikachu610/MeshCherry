"""SF-1 desktop profile parity, explicit support and actual Gmsh integration."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box

from gis_mesh_workbench import sf1_sizing as sf1
from gis_mesh_workbench.gmsh_meshing import inspect_domain
from gis_mesh_workbench.job_control import Cancelled
from gis_mesh_workbench.meshing_parameters import request_summary
from gis_mesh_workbench.runner import run, validate_config, write_json


EXE = Path(__file__).resolve().parents[3] / 'Test_chialuoi/gmsh-4.15.2-Windows64/gmsh.exe'


class Sf1SizingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'dem.tif'
        self.transform = from_origin(500000, 1001920, 30, 30)
        self.z = np.zeros((64, 64))
        self.z[:, 30:] = np.arange(34)[None, :] ** 2
        self.save()
        self.config = dict(schema_version='gis-workbench-run-config-v2',
            extraction_engine='indexed_windowed_v1', operation='mesh_only',
            run_name='sf1-profile', analysis_crs='EPSG:32649', output_root=str(self.root / 'runs'),
            cell_source=dict(kind='gmsh_generated', executable=str(EXE),
                extent=[500000, 1000000, 501920, 1001920], sizing='sf1_relief',
                dem_raster_path=str(self.path), dem_band=1, dem_value_units='m', h_min_m=250, h_max_m=1000,
                sf1_scale=1.0, max_cells=1000))

    def save(self, **overrides):
        profile = dict(driver='GTiff', width=64, height=64, count=1, dtype='float64',
            crs='EPSG:32649', transform=self.transform, nodata=-9999)
        profile.update(overrides)
        with rasterio.open(self.path, 'w', **profile) as dst:
            dst.write(self.z, 1)

    def test_exact_parity_with_established_research_kernels(self):
        from mesh_research.terrain import normalize_score, relief
        from mesh_research.size_field import build_size_field, control_field
        valid = np.ones(self.z.shape, bool)
        valid[8:12, 7:11] = False
        support = valid.copy()
        support[:2] = False
        scores = [normalize_score(relief(self.z, valid, width)[0], support)[0] for width in (5, 15)]
        complexity = np.maximum.reduce(scores)
        _, _, final, _ = build_size_field(complexity, 30, 2, 1.5, scale=1.0905077326652577)
        x0, y0, h0, _ = control_field(final, self.transform, factor=4, ratio=1.5)
        x, y, h, info = sf1.build_controls(self.z, valid, support, self.transform, 1.0905077326652577)
        np.testing.assert_array_equal(x, x0)
        np.testing.assert_array_equal(y, y0)
        np.testing.assert_allclose(h, h0, rtol=0, atol=1e-12)
        self.assertTrue(info['native_gradation']['pass'])
        self.assertTrue(info['control_gradation']['pass'])

    def test_constant_dem_nan_mask_and_step_refinement(self):
        valid = np.ones(self.z.shape, bool)
        flat = np.full(self.z.shape, 17.)
        flat[:4, :4] = np.nan
        valid &= np.isfinite(flat)
        _, _, h, info = sf1.build_controls(flat, valid, valid, self.transform)
        np.testing.assert_allclose(h, 1000, atol=1e-10)
        self.assertEqual(info['normalization']['relief150'], [0, 0])
        x, y, h, info = sf1.build_controls(self.z, np.ones_like(valid), np.ones_like(valid), self.transform)
        self.assertLess(h.min(), h.max())
        self.assertGreaterEqual(h.min(), 250 - 1e-10)
        self.assertLessEqual(h.max(), 1000 + 1e-10)
        self.assertLess(np.median(h[:, 10:]), np.median(h[:, :5]))
        self.assertTrue(np.isfinite(h).all())
        with self.assertRaisesRegex(ValueError, 'tâm pixel'):
            sf1.build_controls(flat, valid, np.zeros_like(valid), self.transform)

    def test_each_gradation_pass_remains_cancellable(self):
        for logarithmic in (False, True):
            with self.subTest(logarithmic=logarithmic), patch(
                'gis_mesh_workbench.sf1_sizing.checkpoint', side_effect=Cancelled('stopped')):
                with self.assertRaises(Cancelled): sf1.limit_field(np.full((8, 8), 1000.), 30, logarithmic)

    def test_request_records_profile_and_explicit_scale(self):
        self.config['cell_source']['sf1_scale'] = 1.2
        result = request_summary(self.config)
        self.assertEqual(result['sf1_scale'], 1.2)
        self.assertEqual(result['fixed_policy']['predictor_windows_m'], [150, 450])
        self.assertEqual(result['fixed_policy']['control_spacing_m'], 120)
        self.assertEqual(result['dem_resolution']['representative_pixel_size_m'], [30, 30])
        self.assertNotIn('radius_m', result)

    def test_grid_and_resource_restrictions_before_gmsh(self):
        for transform, crs, message in (
            (from_origin(500000, 1001920, 50, 50), 'EPSG:32649', '30 × 30'),
            (self.transform, 'EPSG:32648', 'cùng CRS'),
            (from_origin(106, 12, .0003, .0003), 'EPSG:4326', 'cùng CRS')):
            self.save(transform=transform, crs=crs)
            with patch('gis_mesh_workbench.gmsh_meshing.inspect_executable') as executable:
                with self.assertRaisesRegex(ValueError, message): inspect_domain(self.config)
                executable.assert_not_called()
        with rasterio.open(self.path) as src:
            fake = SimpleNamespace(crs=src.crs, count=1, dtypes=['float64'], scales=[1], offsets=[0],
                width=2000, height=1001, transform=self.transform)
        with self.assertRaisesRegex(ValueError, '2.000.000'):
            sf1.inspect_source(fake, 1, 'EPSG:32649')

    def test_known_nonmetric_elevation_units_are_rejected(self):
        with rasterio.open(self.path, 'r+') as src:
            src.set_band_unit(1, 'ft')
        with rasterio.open(self.path) as src:
            with self.assertRaisesRegex(ValueError, 'đơn vị m'):
                sf1.inspect_source(src, 1, 'EPSG:32649')

    def test_schema_refuses_profile_drift_and_mixed_parameters(self):
        validate_config(self.config)
        variants = [dict(h_min_m=200), dict(h_max_m=1200), dict(relief_radius_m=150),
            dict(sf1_scale=0), dict(sf1_scale=4.1), dict(sf1_scale=float('nan')), dict(dem_value_units='ft')]
        for change in variants:
            config = copy.deepcopy(self.config)
            config['cell_source'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError): validate_config(config)
        for missing in ('dem_raster_path', 'dem_band', 'dem_value_units', 'sf1_scale'):
            config = copy.deepcopy(self.config)
            config['cell_source'].pop(missing)
            with self.assertRaises(ValueError): validate_config(config)
        for kind in ('uniform', 'relief'):
            config = copy.deepcopy(self.config)
            config['cell_source']['sizing'] = kind
            if kind == 'relief': config['cell_source']['relief_radius_m'] = 150
            with self.assertRaises(ValueError): validate_config(config)

    def test_missing_scale_rejected_by_schema_runner_and_field_writer(self):
        import jsonschema
        self.config['cell_source'].pop('sf1_scale')
        schema_path = Path(sf1.__file__).with_name('run-config-v2.schema.json')
        validator = jsonschema.Draft202012Validator(json.loads(schema_path.read_text()))
        self.assertFalse(validator.is_valid(self.config))
        with self.assertRaisesRegex(ValueError, 'sf1_scale'):
            validate_config(self.config)
        with self.assertRaisesRegex(ValueError, 'sf1_scale'):
            sf1.write_field(self.config, self.root, box(*self.config['cell_source']['extent']))
        self.assertFalse((self.root / 'size.dat').exists())

    def test_cli_missing_scale_returns_failed_run_before_meshing(self):
        self.config['cell_source'].pop('sf1_scale')
        config_path = self.root / 'missing-scale.json'
        write_json(config_path, self.config)
        result = subprocess.run([sys.executable, '-m', 'gis_mesh_workbench.cli', 'run', str(config_path)],
            capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'fail')
        self.assertIn('sf1_scale', report['error'])
        self.assertFalse((Path(report['output']) / 'meshing/mesh.msh').exists())

    def test_roi_holes_normalization_source_hash_and_structured_orientation(self):
        before = self.path.read_bytes()
        domain = Polygon([(500000, 1000000), (501920, 1000000), (501920, 1001920), (500000, 1001920)],
            [[(500600, 1000600), (501200, 1000600), (501200, 1001200), (500600, 1001200)]])
        evidence = sf1.write_field(self.config, self.root, domain)
        self.assertEqual(evidence['normalization_valid_pixel_count'], 64 * 64 - 20 * 20)
        self.assertEqual(evidence['scale'], 1)
        self.assertIn('not performed', evidence['calibration'])
        self.assertFalse(evidence['fixed_policy']['boundary_simplification'])
        self.assertEqual(self.path.read_bytes(), before)
        controls = np.load(self.root / 'sf1-control-field.npz')
        lines = (self.root / 'size.dat').read_text().splitlines()
        self.assertEqual(lines[1], '120 120 1')
        np.testing.assert_array_equal(np.array(lines[3:], float), controls['h'][::-1, :].T.reshape(-1))
        self.assertEqual(json.loads((self.root / 'size-field.json').read_text())['method'], 'gnn_sf1_two_window_relief')

    def test_outside_roi_and_source_mutation_fail(self):
        with self.assertRaisesRegex(ValueError, 'vượt phạm vi'):
            sf1.write_field(self.config, self.root, box(499000, 999000, 503000, 1003000))
        with patch('gis_mesh_workbench.sf1_sizing.assert_unchanged', side_effect=ValueError('mutated')):
            with self.assertRaisesRegex(ValueError, 'mutated'):
                sf1.write_field(self.config, self.root, box(*self.config['cell_source']['extent']))

    def test_interpolation_halo_does_not_extend_actual_dem_coverage(self):
        # A 50 m extension fits inside the 105 m control halo but has no source support.
        self.config['cell_source']['extent'][0] -= 50
        domain = box(*self.config['cell_source']['extent'])
        with patch('gis_mesh_workbench.gmsh_meshing.inspect_executable') as executable:
            with self.assertRaisesRegex(ValueError, 'vượt phạm vi DEM'): inspect_domain(self.config)
            executable.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'vượt phạm vi DEM'):
            sf1.write_field(self.config, self.root, domain)
        self.assertFalse((self.root / 'size.dat').exists())

    def test_missing_pixels_inside_dem_footprint_remain_supported(self):
        self.z[:4] = -9999
        self.save()
        evidence = sf1.write_field(self.config, self.root, box(*self.config['cell_source']['extent']))
        self.assertEqual(evidence['normalization_valid_pixel_count'], 60 * 64)
        self.assertEqual(evidence['native_valid_pixel_count'], 60 * 64)
        self.assertTrue(evidence['native_gradation']['pass'])
        self.assertTrue(evidence['control_gradation']['pass'])

    def test_real_gmsh_profile_generates_mesh_with_recorded_gradation(self):
        if not EXE.is_file(): self.skipTest('External Gmsh is not installed')
        path = self.root / 'config.json'
        write_json(path, self.config)
        result = run(path)
        self.assertEqual(result['status'], 'pass', result)
        output = Path(result['output'])
        generator = json.loads((output / 'meshing/generator.json').read_text())
        self.assertEqual(generator['size_field']['method'], 'gnn_sf1_two_window_relief')
        self.assertTrue(generator['size_field']['native_gradation']['pass'])
        self.assertTrue(generator['size_field']['control_gradation']['pass'])
        self.assertEqual(generator['domain_coverage'], 'pass')
        self.assertIn('Field[1]=Structured;', (output / 'meshing/domain.geo').read_text())
        self.assertGreater(generator['cell_count'], 2)


if __name__ == '__main__': unittest.main()
