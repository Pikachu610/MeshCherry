import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from affine import Affine
import numpy as np
import rasterio
from shapely.geometry import box

from gis_mesh_workbench.comparison import compare_mesh, _categorical_metrics, _square_partition
from gis_mesh_workbench.indexed_extraction import CEILINGS, footprint
from gis_mesh_workbench.extraction import Cell, _digest
from gis_mesh_workbench.job_control import Cancelled, JobControl, job_scope
from gis_mesh_workbench.mesh_workflow import geometry_digest


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raster = self.root / 'input.tif'
        self.mesh = self.root / 'cells.json'
        self.crs = 'EPSG:32649'
        self.cells = (
            Cell('accepted-7', 'triangle', ((500000, 1000000), (500002, 1000000), (500002, 1000002))),
            Cell('accepted-29', 'triangle', ((500000, 1000000), (500002, 1000002), (500000, 1000002))),
        )
        self.freeze()
        self.write([[10, 30], [10, 30]])

    def freeze(self):
        self.mesh.write_text(json.dumps(dict(schema_version='workbench-cells-v1',
            coordinate_units='m', crs_wkt=self.crs,
            cells=[dict(cell_id=c.cell_id, kind=c.kind, xy=c.xy) for c in self.cells])))
        (self.root / 'manifest.json').write_text(json.dumps(dict(status='pass', operation='mesh_only',
            artifact_hashes={'cells.json': _digest(self.mesh)},
            geometry_digest=geometry_digest(self.cells, self.crs))))

    def write(self, data, mask=None):
        a = np.asarray(data, dtype='float64')
        with rasterio.open(self.raster, 'w', driver='GTiff', width=a.shape[1], height=a.shape[0],
            count=1, dtype='float64', crs=self.crs,
            transform=Affine(1, 0, 500000, 0, -1, 1000002)) as dst:
            dst.write(a, 1)
            if mask is not None:
                dst.write_mask(np.asarray(mask, dtype='uint8'))

    def compare(self, **kwargs):
        baseline_mode = kwargs.get('baseline_mode', 'target_grid')
        size = kwargs.pop('cell_size_m', None if baseline_mode == 'native_raster' else 1)
        return compare_mesh(self.mesh, self.raster, analysis_crs=self.crs,
                            cell_size_m=size, value_units='m', **kwargs)

    def test_intensive_comparison_measured_errors_and_identity(self):
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir()}
        report = self.compare()
        self.assertEqual(report['structured']['node_count'], 4)
        self.assertEqual(report['unstructured']['node_count'], 2)
        self.assertEqual(report['node_reduction_percent'], 50)
        self.assertEqual(report['graph_node_delta'], -2)
        self.assertEqual(report['common_support_area_m2'], 4)
        self.assertEqual(report['metrics']['structured']['rmse'], 0)
        self.assertAlmostEqual(report['metrics']['unstructured']['rmse'], np.sqrt(75))
        self.assertAlmostEqual(report['metrics']['unstructured']['mae'], 7.5)
        self.assertAlmostEqual(report['metrics']['unstructured']['bias'], 0)
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir()})
        self.assertIn('intersection', report['method'])

    def test_negative_reduction_and_masked_support(self):
        self.write([[10, 30], [10, 30]], [[255, 0], [255, 255]])
        result = self.compare(cell_size_m=2)
        self.assertEqual(result['node_reduction_percent'], -100)
        self.assertEqual(result['graph_node_delta'], 1)
        self.assertEqual(result['common_support_area_m2'], 3)
        for part in ('structured', 'unstructured'):
            self.assertAlmostEqual(result[part]['valid_support_area_m2'], 3)

    def test_native_raster_baseline_counts_roi_pixels_and_reports_identity(self):
        self.write([[10, 30], [10, 30], [90, 90], [90, 90]])
        result = self.compare(baseline_mode='native_raster')
        self.assertEqual(result['baseline']['mode'], 'native_raster')
        self.assertEqual(result['baseline']['source_pixel_count'], 4)
        self.assertEqual(result['baseline']['valid_source_pixel_count'], 4)
        self.assertEqual(result['baseline']['clipped_component_count'], 4)
        self.assertEqual(result['baseline']['source_resolution'],
                         dict(x=1., y=1., units='metre', crs='EPSG:32649'))
        self.assertEqual(result['provenance']['shape'], [4, 2])
        self.assertEqual(result['structured']['node_count'], 4)
        self.assertEqual(result['metrics']['structured']['rmse'], 0)
        self.assertIn('tautological', result['metrics']['structured']['interpretation'])
        self.assertAlmostEqual(result['metrics']['unstructured']['rmse'], np.sqrt(75))
        self.assertIsNone(result['square_grid']['cell_size_m'])

    def test_spatial_zones_reconcile_all_global_node_counts(self):
        for mode in ('target_grid', 'native_raster'):
            report = self.compare(baseline_mode=mode)
            zones = report['spatial_zones']
            self.assertEqual(zones['relief_alignment'], 'not_assessed')
            self.assertIn('representative point', zones['allocation_rule'])
            self.assertEqual(len(zones['rows']), 9)
            self.assertEqual(sum(z['mesh_node_count'] for z in zones['rows']),
                             report['unstructured']['node_count'])
            self.assertEqual(sum(z['structured_node_count'] for z in zones['rows']),
                             report['structured']['node_count'])
            self.assertEqual(sum(z['graph_node_delta'] for z in zones['rows']),
                             report['graph_node_delta'])
            for zone in zones['rows']:
                self.assertGreaterEqual(zone['roi_area_m2'], 0)
                self.assertGreaterEqual(zone['mesh_nodes_per_km2'], 0)
                self.assertGreaterEqual(zone['structured_nodes_per_km2'], 0)

    def test_intensive_workload_limits_are_job_aggregate(self):
        caps = dict(CEILINGS)
        caps['max_pixels_read'] = 5
        with patch('gis_mesh_workbench.comparison.limits', return_value=caps):
            with self.assertRaisesRegex(ValueError, 'read pixel limit'):
                self.compare()

    def test_native_pixel_count_preserves_rotated_affine_grid(self):
        transform = Affine(1, .2, 500000, .2, -1, 1000002)
        corners = [transform @ point for point in ((0, 0), (2, 0), (2, 2), (0, 2))]
        a, b, c, d = corners
        self.cells = (Cell('rot-a', 'triangle', (a, b, c)),
                      Cell('rot-b', 'triangle', (a, c, d)))
        self.freeze()
        with rasterio.open(self.raster, 'w', driver='GTiff', width=2, height=2,
            count=1, dtype='float64', crs=self.crs, transform=transform) as dst:
            dst.write(np.array([[10., 30.], [10., 30.]]), 1)
        result = self.compare(baseline_mode='native_raster')
        self.assertEqual(result['baseline']['source_pixel_count'], 4)
        self.assertEqual(result['baseline']['clipped_component_count'], 4)
        self.assertEqual(result['provenance']['affine'], list(transform))

    def test_native_pixel_count_separates_nodata_from_geometric_roi_count(self):
        self.write([[10, 30], [10, 30]], [[255, 0], [255, 255]])
        result = self.compare(baseline_mode='native_raster')
        self.assertEqual(result['baseline']['source_pixel_count'], 4)
        self.assertEqual(result['baseline']['valid_source_pixel_count'], 3)
        self.assertEqual(result['common_support_area_m2'], 3)
        self.assertEqual(result['metrics']['structured']['rmse'], 0)

    def test_native_mode_rejects_target_cell_size(self):
        with self.assertRaisesRegex(ValueError, 'only valid for target_grid'):
            self.compare(baseline_mode='native_raster', cell_size_m=1)

    def test_60m_native_vs_30m_target_grid_counts_and_piecewise_constant_error(self):
        self.cells = (
            Cell('wide-a', 'triangle', ((500000, 1000000), (500120, 1000000), (500120, 1000060))),
            Cell('wide-b', 'triangle', ((500000, 1000000), (500120, 1000060), (500000, 1000060))),
        )
        self.freeze()
        with rasterio.open(self.raster, 'w', driver='GTiff', width=2, height=1,
            count=1, dtype='float64', crs=self.crs,
            transform=Affine(60, 0, 500000, 0, -60, 1000060)) as dst:
            dst.write(np.array([[10., 30.]]), 1)

        target = compare_mesh(self.mesh, self.raster, analysis_crs=self.crs,
            cell_size_m=30, value_units='m')
        native = compare_mesh(self.mesh, self.raster, analysis_crs=self.crs,
            baseline_mode='native_raster', cell_size_m=None, value_units='m')
        self.assertEqual(target['baseline']['node_count'], 8)
        self.assertEqual(native['baseline']['node_count'], 2)
        self.assertEqual(native['baseline']['source_pixel_count'], 2)
        self.assertEqual(target['baseline']['alignment'], 'accepted triangle union bounding-box lower-left')
        self.assertEqual(target['baseline']['source_resolution']['x'], 60.)
        self.assertEqual(target['metrics']['structured']['rmse'], 0)
        self.assertEqual(native['metrics']['structured']['rmse'], 0)
        self.assertIn('do not create new source information',
                      target['baseline']['source_vs_requested']['interpretation'])

    def test_extensive_and_categorical_are_semantically_separate(self):
        r = self.compare(semantics='extensive')
        for part in r['metrics'].values():
            self.assertEqual(part['source_total'], 80)
            self.assertAlmostEqual(part['remapped_total'], 80)
            self.assertAlmostEqual(part['conservation_residual'], 0)
            self.assertNotIn('rmse', part)
        r = self.compare(semantics='categorical')
        for part in r['metrics'].values():
            self.assertEqual(part['source_class_fractions'], {'10.0': .5, '30.0': .5})
            self.assertEqual(part['remapped_class_fractions'], {'10.0': .5, '30.0': .5})
            self.assertEqual(part['max_fraction_error'], 0)
            self.assertNotIn('rmse', part)

    def test_categorical_spatial_loss_differs_despite_equal_class_conservation(self):
        # Vertical 10/30 stripes: each unit square retains one source class,
        # whereas each diagonal triangle contains 75% of its dominant class.
        report = self.compare(semantics='categorical')
        square, triangle = (report['metrics'][name] for name in ('structured', 'unstructured'))
        self.assertEqual(square['source_class_fractions'], triangle['source_class_fractions'])
        self.assertEqual(square['max_fraction_error'], 0)
        self.assertEqual(triangle['max_fraction_error'], 0)
        self.assertEqual(square['area_weighted_modal_agreement'], 1)
        self.assertEqual(square['area_weighted_modal_disagreement'], 0)
        self.assertEqual(triangle['area_weighted_modal_agreement'], .75)
        self.assertEqual(triangle['area_weighted_modal_disagreement'], .25)
        coarser = self.compare(semantics='categorical', cell_size_m=2)['metrics']['structured']
        self.assertEqual(coarser['max_fraction_error'], 0)
        self.assertEqual(coarser['area_weighted_modal_agreement'], .5)

    def test_categorical_preserves_adjacent_uint64_class_ids_above_float_precision(self):
        low, high = 2**53, 2**53+1
        values = np.array([[low, high], [low, high]], dtype='uint64')
        with rasterio.open(self.raster, 'w', driver='GTiff', width=2, height=2,
            count=1, dtype='uint64', crs=self.crs,
            transform=Affine(1, 0, 500000, 0, -1, 1000002)) as dst:
            dst.write(values, 1)
        result = self.compare(semantics='categorical')
        for part in result['metrics'].values():
            expected = {str(float(low)): .5, str(high): .5}
            self.assertEqual(part['source_class_fractions'], expected)
            self.assertEqual(part['remapped_class_fractions'], expected)
        self.assertEqual(result['metrics']['unstructured']['area_weighted_modal_agreement'], .75)

    def test_stop_interrupts_a_wide_single_row_square_partition(self):
        control = JobControl()
        created = 0
        def stop_after_five_squares(*args, **kwargs):
            nonlocal created
            created += 1
            result = box(*args, **kwargs)
            if created == 5:
                control.request_cancel()
            return result
        with job_scope(control), patch('gis_mesh_workbench.comparison.box', stop_after_five_squares):
            with self.assertRaises(Cancelled):
                _square_partition(box(0, 0, 1000, 1), 1)
        self.assertGreaterEqual(created, 5)
        self.assertLessEqual(created, 128)

    def test_stop_interrupts_many_components_inside_one_clipped_square(self):
        control = JobControl()
        visited = 0
        class Component:
            geom_type = 'Polygon'
            area = 1.
            bounds = (0., 0., 1., 1.)
            wkb = b'component'
        class Collection:
            area = 1000.
            geom_type = 'MultiPolygon'
            @property
            def geoms(self):
                nonlocal visited
                for _ in range(1000):
                    visited += 1
                    if visited == 5:
                        control.request_cancel()
                    yield Component()
        class Square:
            def intersection(self, _domain):
                return Collection()
        with job_scope(control), patch('gis_mesh_workbench.comparison.box', return_value=Square()):
            with self.assertRaises(Cancelled):
                _square_partition(box(0, 0, 1, 1), 1)
        self.assertGreaterEqual(visited, 5)
        self.assertLessEqual(visited, 129)

    def test_categorical_summary_visits_only_actual_class_entries(self):
        visited = calls = 0
        class CountedClasses(dict):
            def items(self):
                nonlocal visited, calls
                calls += 1
                for pair in super().items():
                    visited += 1
                    yield pair
            def get(self, *args):
                raise AssertionError('categorical aggregation must not scan all cells per class')
        count = 1024
        states = [dict(classes=CountedClasses({float(i): 1., -1.: 1.})) for i in range(count)]
        source = {float(i): 1. for i in range(count)}
        source[-1.] = float(count)
        report = _categorical_metrics(states, source, count*2.)
        self.assertEqual(calls, count)
        self.assertEqual(visited, count*2)
        self.assertEqual(report['max_fraction_error'], 0)
        self.assertEqual(report['area_weighted_modal_agreement'], .5)

    def test_stop_interrupts_categorical_sparse_collection(self):
        control = JobControl()
        visited = 0
        class StopClasses(dict):
            def items(self):
                nonlocal visited
                for pair in super().items():
                    visited += 1
                    if visited == 5:
                        control.request_cancel()
                    yield pair
        source = {float(i): 1. for i in range(1000)}
        with job_scope(control), self.assertRaises(Cancelled):
            _categorical_metrics([dict(classes=StopClasses(source))], source, 1000.)
        self.assertGreaterEqual(visited, 5)
        self.assertLessEqual(visited, 129)

    def test_stop_interrupts_categorical_final_class_sum(self):
        control = JobControl()
        consumed = 0
        fsum = math.fsum
        def sum_with_stop(values):
            def items():
                nonlocal consumed
                for value in values:
                    consumed += 1
                    if consumed == 5:
                        control.request_cancel()
                    yield value
            return fsum(items())
        states = [dict(classes={7.: 1.}) for _ in range(1000)]
        with job_scope(control), patch('gis_mesh_workbench.comparison.math.fsum', sum_with_stop):
            with self.assertRaises(Cancelled):
                _categorical_metrics(states, {7.: 1000.}, 1000.)
        self.assertGreaterEqual(consumed, 5)
        self.assertLessEqual(consumed, 128)

    def test_categorical_summary_uses_stable_finite_area_sums(self):
        states = [dict(classes={7.: value}) for value in (1e16, 1., 1.)]
        support = 1e16+2.
        result = _categorical_metrics(states, {7.: support}, support)
        self.assertEqual(result['max_fraction_error'], 0)
        self.assertEqual(result['area_weighted_modal_agreement'], 1)
        for value in (float('inf'), float('nan'), -1.):
            with self.assertRaisesRegex(ValueError, 'finite and nonnegative'):
                _categorical_metrics([dict(classes={7.: value})], {7.: 1.}, 1.)
        with self.assertRaisesRegex(ValueError, 'finite numeric'):
            _categorical_metrics([dict(classes={7.: 1e308})]*2, {7.: 1e308}, 1e308)

    def test_clipping_preserves_nonrectangular_roi(self):
        self.cells = self.cells[:1]
        self.freeze()
        r = self.compare()
        self.assertEqual(r['domain_area_m2'], 2)
        self.assertEqual(r['common_support_area_m2'], 2)
        self.assertEqual(r['structured']['cell_count'], 3)
        self.assertEqual(r['metrics']['structured']['rmse'], 0)

    def test_no_support_invalid_inputs_and_cancellation(self):
        for size in (0, -1, float('nan'), 1e-9, 5e-324):
            with self.assertRaises(ValueError): self.compare(cell_size_m=size)
        with self.assertRaises(ValueError): self.compare(semantics='unknown')
        with self.assertRaises(ValueError): self.compare(edge_subdivisions=0)
        self.write([[1, 2], [3, 4]], [[0, 0], [0, 0]])
        with self.assertRaisesRegex(ValueError, 'valid'): self.compare()
        control = JobControl(); control.request_cancel()
        with job_scope(control), self.assertRaises(Cancelled): self.compare()

    def test_hole_and_disconnected_components_share_exact_support(self):
        cells = []
        # Eight unit squares surrounding a unit hole, plus detached square.
        for x, y in [(x, y) for y in range(3) for x in range(3) if (x, y) != (1, 1)] + [(4, 0)]:
            a, b, c, d = [(500000+x+dx, 1000000+y+dy)
                           for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))]
            cells.extend((Cell('t'+str(len(cells)), 'triangle', (a, b, c)),
                          Cell('t'+str(len(cells)+1), 'triangle', (a, c, d))))
        self.cells = tuple(cells); self.freeze()
        with rasterio.open(self.raster, 'w', driver='GTiff', width=5, height=3,
            count=1, dtype='float64', crs=self.crs,
            transform=Affine(1, 0, 500000, 0, -1, 1000003)) as dst:
            values = np.full((3, 5), 17.); values[1, 1] = 9999
            dst.write(values, 1)
        r = self.compare(cell_size_m=2)
        self.assertEqual(r['domain_area_m2'], 9)
        self.assertEqual(r['common_support_area_m2'], 9)
        for part in r['metrics'].values():
            self.assertEqual(part['rmse'], 0)
        # One bounding square intersects two detached components: two nodes.
        r = self.compare(cell_size_m=100)
        self.assertEqual(r['structured']['cell_count'], 2)
        self.assertEqual(r['structured']['node_count'], 2)

    def test_window_pair_and_source_caps_are_enforced(self):
        for limit in ('max_source_pixels', 'max_pixels_read', 'max_candidate_pairs'):
            caps = dict(CEILINGS); caps[limit] = 1
            with patch('gis_mesh_workbench.comparison.limits', return_value=caps):
                with self.assertRaisesRegex(ValueError, 'limit'): self.compare()
        caps = dict(CEILINGS); caps['max_window_pixels'] = 1
        with patch('gis_mesh_workbench.comparison.limits', return_value=caps), \
                patch('gis_mesh_workbench.comparison.MAX_WINDOWS', 1):
            with self.assertRaisesRegex(ValueError, 'window scan'): self.compare()

    def test_cancel_after_source_read_and_changed_mesh_guard(self):
        control = JobControl()
        def stop_during_pixel(*args, **kwargs):
            result = footprint(*args, **kwargs)
            control.request_cancel()
            return result
        with job_scope(control), patch('gis_mesh_workbench.comparison.footprint', stop_during_pixel):
            with self.assertRaises(Cancelled): self.compare()
        changed = False
        def change_frozen_input(*args, **kwargs):
            nonlocal changed
            if not changed:
                self.mesh.write_text(self.mesh.read_text()+'\n')
                changed = True
            return footprint(*args, **kwargs)
        with patch('gis_mesh_workbench.comparison.footprint', change_frozen_input):
            with self.assertRaisesRegex(ValueError, 'input changed'): self.compare()

    def test_cross_crs_uses_densified_footprints_and_conservative_reads(self):
        from rasterio.warp import transform
        x, y = transform('EPSG:4326', 'EPSG:3857', [111, 111.00002], [9, 9.00002])
        self.crs = 'EPSG:3857'
        a, b, c, d = (x[0], y[0]), (x[1], y[0]), (x[1], y[1]), (x[0], y[1])
        self.cells = (Cell('same-a', 'triangle', (a, b, c)), Cell('same-b', 'triangle', (a, c, d)))
        self.freeze()
        with rasterio.open(self.raster, 'w', driver='GTiff', width=2, height=2,
            count=1, dtype='float64', crs='EPSG:4326',
            transform=Affine(.00001, 0, 111, 0, -.00001, 9.00002)) as dst:
            dst.write(np.full((2, 2), 17.), 1)
        r = self.compare(cell_size_m=1)
        self.assertAlmostEqual(r['common_support_area_m2'], r['domain_area_m2'], places=6)
        for p in r['workload']['passes']:
            self.assertEqual(p['pixels_read'], 4)
        self.assertEqual(r['workload']['aggregate']['pixels_read'], 8)
        for part in r['metrics'].values(): self.assertEqual(part['rmse'], 0)

    def test_mismatched_crs_and_scale_rejected(self):
        with self.assertRaisesRegex(ValueError, 'CRS mismatch'):
            compare_mesh(self.mesh, self.raster, analysis_crs='EPSG:3857', cell_size_m=1, value_units='m')
        with rasterio.open(self.raster, 'r+') as dst: dst.scales = (.1,)
        with self.assertRaisesRegex(ValueError, 'scale/offset'): self.compare()

    def test_extreme_finite_comparison_has_representable_rmse(self):
        self.write([[1e308, -1e308], [1e308, -1e308]])
        result = self.compare(cell_size_m=2)
        self.assertEqual(result['metrics']['structured']['rmse'], 1e308)
        for metrics in result['metrics'].values():
            self.assertTrue(all(math.isfinite(metrics[key]) for key in ('rmse', 'mae', 'bias')))

    def test_rectangular_source_mesh_and_manifest_tampering_rejected(self):
        self.cells = (Cell('rect', 'rectangle', ((500000, 1000000), (500002, 1000000),
            (500002, 1000002), (500000, 1000002))),)
        self.freeze()
        with self.assertRaisesRegex(ValueError, 'triangular'): self.compare()
        self.mesh.write_text(self.mesh.read_text()+' ')
        with self.assertRaisesRegex(ValueError, 'manifest/hash'): self.compare()


if __name__ == '__main__': unittest.main()
