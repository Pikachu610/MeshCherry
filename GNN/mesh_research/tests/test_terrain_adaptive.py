"""Analytical contracts for the optional GIS/adaptive backend."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from affine import Affine

from mesh_research.terrain import (exact_mask_polygon_area, normalize_score, relief,
                                   limit_log_size, field_gradient_bound)
from mesh_research.terrain_evaluation import overlap_weights, representation
from mesh_research.size_field import write_structured_field
from mesh_research.gmsh_adapter import generate_spatial_polygon_domain
from mesh_research.adaptive_experiment import (_best_eligible_case, _selection_trace,
                                               _winner_and_display,
                                               finalize_existing_adaptive_run)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TerrainContracts(unittest.TestCase):
    def test_exact_partial_pixel_support_area(self):
        from shapely.geometry import box
        mask = np.ones((1, 1), dtype=bool)
        transform = Affine(1, 0, 0, 0, -1, 1)
        self.assertAlmostEqual(exact_mask_polygon_area(mask, box(0, 0, .5, 1), transform), .5)

    def test_constant_normalization(self):
        score, quantiles = normalize_score(np.ones((5, 5)), np.ones((5, 5), bool))
        self.assertEqual(float(score.sum()), 0)
        self.assertEqual(quantiles, [1.0, 1.0])

    def test_relief_does_not_treat_nodata_as_zero(self):
        z = np.full((7, 7), 100.0)
        mask = np.ones(z.shape, bool)
        mask[0, :] = False
        z[0, :] = -9999
        r, support = relief(z, mask, 5)
        np.testing.assert_array_equal(r[mask], 0)
        self.assertLess(support[1, 1], 1)

    def test_limiter_bounds_and_physical_gradient(self):
        h = np.full((40, 40), 1000.0)
        h[20, 20] = 250
        limited, info = limit_log_size(h, 30, 1.3)
        self.assertGreaterEqual(limited.min(), 250 - 1e-9)
        self.assertLessEqual(limited.max(), 1000 + 1e-9)
        self.assertLessEqual(field_gradient_bound(np.log(limited), 30), np.log(1.3) / 250 + 1e-12)
        self.assertTrue(info['pass'])
        np.testing.assert_allclose(limit_log_size(limited, 30, 1.3)[0], limited)

    def test_exact_overlap_and_piecewise_constant_error(self):
        # Two triangles partition four 1 m2 pixels; no centroid sampling.
        vertices = np.array([[0, 0], [2, 0], [2, 2], [0, 2]])
        triangles = np.array([[0, 1, 2], [0, 2, 3]])
        w = overlap_weights(vertices, triangles, (2, 2), Affine(1, 0, 0, 0, -1, 2))
        np.testing.assert_allclose(np.asarray(w.sum(axis=0)).ravel(), 1)
        np.testing.assert_allclose(np.asarray(w.sum(axis=1)).ravel(), 2)
        z = np.array([[0., 0.], [2., 2.]])
        means, metrics = representation(w, z, np.ones((2, 2), bool), np.ones((2, 2), bool))
        np.testing.assert_allclose(means, [1.5, .5])
        self.assertAlmostEqual(metrics['global']['rmse'], np.sqrt(.625))
        self.assertAlmostEqual(metrics['global']['mae'], .75)

    def test_missing_pixels_excluded_not_zero_filled(self):
        from scipy.sparse import csr_matrix
        w = csr_matrix([[1., 1.]])
        values = np.array([[4., -9999.]])
        mask = np.array([[True, False]])
        means, metrics = representation(w, values, mask, mask)
        self.assertEqual(means[0], 4)
        self.assertEqual(metrics['global']['area_m2'], 1)
        self.assertEqual(metrics['global']['rmse'], 0)

    def test_representation_uses_declared_fixed_support_area_for_coverage(self):
        from scipy.sparse import csr_matrix
        # The mesh covers only half of a valid 1 m² reference pixel.  Coverage
        # must be 0.5 against the predeclared support, not silently promoted
        # to 1.0 because that is the only pixel the mesh happened to touch.
        w = csr_matrix([[0.5]])
        values = np.array([[7.]])
        mask = np.array([[True]])
        _, metrics = representation(w, values, mask, mask,
                                    fixed_support_areas={'global': 1.0, 'high_complexity': 1.0})
        self.assertAlmostEqual(metrics['global']['expected_support_area_m2'], 1.0)
        self.assertAlmostEqual(metrics['global']['covered_area_m2'], 0.5)
        self.assertAlmostEqual(metrics['global']['uncovered_area_m2'], 0.5)
        self.assertAlmostEqual(metrics['global']['coverage_fraction'], 0.5)

    def test_selection_uses_two_point_benefit_tie_window_then_interfaces(self):
        def case(case_id, benefit, interfaces, wall=1.0):
            return {'case_id': case_id, 'status': 'complete', 'benefit_pct': benefit,
                    'benefit_pass': True, 'eligibility': {'eligible': True},
                    'interface_count': interfaces, 'resources': {'wall_time_seconds': wall},
                    'improvements': {'high_elevation_pct': benefit, 'high_slope_pct': 0},
                    'evaluation': {'elevation': {'high_complexity': {'rmse': 100 - benefit}},
                                   'slope_source_observed': {'high_complexity': {'rmse': 10}}},
                    'quality': {'minimum_angle_degrees': {'min': 30}}}
        selected = _best_eligible_case([case('highest-benefit', 27.6, 20491),
                                        case('within-two-points', 26.0, 19000),
                                        case('outside-window', 25.5, 10000)])
        self.assertEqual(selected['case_id'], 'within-two-points')

    def test_selection_trace_and_no_winner_never_promote_exploratory_case(self):
        row = {'case_id': 'exploratory-best', 'status': 'complete', 'benefit_pct': 25,
               'benefit_pass': True, 'eligibility': {'eligible': True},
               'interface_count': 100, 'resources': {'wall_time_seconds': 1},
               'evaluation': {'elevation': {'high_complexity': {'rmse': 10}},
                              'slope_source_observed': {'high_complexity': {'rmse': 5}}},
               'quality': {'minimum_angle_degrees': {'min': 30}}}
        self.assertEqual(_selection_trace([row], 2.0)['selected_winner'], 'exploratory-best')
        selected, display = _winner_and_display([row], 'no_winner', 2.0)
        self.assertIsNone(selected)
        self.assertEqual(display['case_id'], 'exploratory-best')

    def test_finalize_no_winner_keeps_exploratory_case_display_only(self):
        import rasterio

        row = {'case_id': 'exploratory-best', 'status': 'complete', 'benefit_pct': 25,
               'benefit_pass': True, 'eligibility': {'eligible': True},
               'interface_count': 100, 'resources': {'wall_time_seconds': 1},
               'evaluation': {'elevation': {'high_complexity': {'rmse': 10}},
                              'slope_source_observed': {'high_complexity': {'rmse': 5}}},
               'quality': {'minimum_angle_degrees': {'min': 30}}}
        mesh = {'vertices': [[0, 0], [1, 0], [0, 1]], 'triangles': [[0, 1, 2]],
                'physical_interpretation': {'watershed_divide_status': 'UNVERIFIED',
                                            'outlet_status': 'UNVERIFIED'}}
        graph = {'nodes': [], 'interfaces': [], 'directed_edges': []}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            study = root / 'study'
            historical = root / 'historical'
            case_root = study / 'cases/exploratory-best'
            for directory in (study / 'baseline/mesh', study / 'boundary',
                              historical / 'mesh', historical / 'graph',
                              case_root / 'mesh', case_root / 'graph', case_root / 'field'):
                directory.mkdir(parents=True, exist_ok=True)

            (study / 'summary.json').write_text(json.dumps({
                'selection_status': 'no_winner', 'selected_winner': None,
                'display_case': 'exploratory-best', 'cases': [row],
                'baseline': {'parity': {}}}))
            (study / 'baseline/mesh/mesh.json').write_text(json.dumps(mesh))
            (study / 'boundary/selected-boundary.json').write_text(json.dumps({
                'coordinates': [[0, 0], [1, 0], [0, 1], [0, 0]]}))
            (historical / 'mesh/mesh.json').write_text(json.dumps(mesh))
            (historical / 'graph/graph.json').write_text(json.dumps(graph))
            (case_root / 'mesh/mesh.json').write_text(json.dumps(mesh))
            (case_root / 'graph/graph.json').write_text(json.dumps(graph))
            with rasterio.open(case_root / 'field/final-h.tif', 'w', driver='GTiff',
                               height=1, width=1, count=1, dtype='float32',
                               transform=Affine(1, 0, 0, 0, -1, 1)) as dataset:
                dataset.write(np.array([[500]], dtype=np.float32), 1)

            config_path = root / 'config.json'
            config_path.write_text(json.dumps({
                'experiment': {'benefit_tie_window_pct': 2.0},
                'generator': {'executable': 'gmsh'},
                'terrain_bundle': 'terrain',
                'historical_uniform_500m': str(historical),
                'domain': {'boundary_source': 'boundary.shp'}}))

            with (patch('mesh_research.adaptive_experiment.build_cell_graph',
                        return_value=(mesh['triangles'], graph, {})),
                  patch('mesh_research.adaptive_experiment.read_single_polygon_shp',
                        return_value=[[0, 0], [1, 0], [0, 1], [0, 0]]),
                  patch('mesh_research.adaptive_experiment.load_terrain', return_value=object()),
                  patch('mesh_research.adaptive_experiment._repeatability',
                        return_value={'pass': True}),
                  patch('mesh_research.adaptive_experiment.render_adaptive_figure') as render):
                result = finalize_existing_adaptive_run(study, config_path)

            finalized = json.loads((study / 'summary.json').read_text())
            self.assertIsNone(result['selected_winner'])
            self.assertEqual(result['display_case'], 'exploratory-best')
            self.assertEqual(result['repeatability_role'], 'representative_repeatability_only')
            self.assertIsNone(finalized['selected_winner'])
            self.assertEqual(finalized['display_case'], 'exploratory-best')
            self.assertIsNone(finalized['selection_trace']['selected_winner'])
            self.assertEqual(finalized['repeatability']['role'], 'representative_repeatability_only')
            self.assertIn('no-winner exploratory display', render.call_args.args[-1])
            self.assertNotIn('retained winner', render.call_args.args[-1])

    def test_gmsh_responds_to_two_zone_spatial_field(self):
        executable = PROJECT_ROOT.parents[1] / 'Test_chialuoi/gmsh-4.15.2-Windows64/gmsh.exe'
        if not executable.is_file():
            self.skipTest('project Gmsh binary absent')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            x = np.arange(-1000., 12000., 1000.)
            y = np.arange(-1000., 12000., 1000.)
            h = np.full((len(y), len(x)), 1000.)
            h[:, x <= 5000] = 250.
            background = root / 'two-zone.dat'
            write_structured_field(background, x, y, h)
            vertices, triangles, *_ = generate_spatial_polygon_domain(
                {'executable': str(executable), 'parameters': {'algorithm': 5, 'random_seed': 1}},
                [(0.,0.), (10000.,0.), (10000.,10000.), (0.,10000.)], background, root / 'mesh')
            centers = np.asarray(vertices)[np.asarray(triangles), :2].mean(axis=1)
            left = int((centers[:, 0] < 4000).sum())
            right = int((centers[:, 0] > 6000).sum())
            self.assertGreater(left, 3 * right, (len(triangles), (root / 'mesh/gmsh.log').read_text()))


if __name__ == '__main__':
    unittest.main()
