import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gis_mesh_workbench.gmsh_meshing import geo_meshing_controls
from gis_mesh_workbench.meshing_parameters import advanced_meshing_options, validate_advanced_meshing
from gis_mesh_workbench.runner import run, validate_config, write_json

GMSH = Path(__file__).resolve().parents[3] / 'Test_chialuoi/gmsh-4.15.2-Windows64/gmsh.exe'

def config(sizing='uniform', advanced=None):
    source = dict(kind='gmsh_generated', executable='gmsh.exe', sizing=sizing,
                  h_min_m=500, h_max_m=500, extent=[0, 0, 1000, 1000],
                  max_cells=100000)
    if sizing == 'sf1_relief':
        source.update(h_min_m=250, h_max_m=1000, dem_raster_path='dem.tif',
                      dem_band=1, dem_value_units='m', sf1_scale=1)
    elif sizing == 'relief':
        source.update(h_min_m=250, h_max_m=1000, dem_raster_path='dem.tif',
                      dem_band=1, relief_radius_m=500)
    if advanced is not None:
        source['advanced'] = advanced
    return dict(schema_version='gis-workbench-run-config-v2', extraction_engine='indexed_windowed_v1',
                run_name='advanced-mesh', analysis_crs='EPSG:32649', cell_source=source,
                output_root='outputs', operation='mesh_only')


class AdvancedMeshingControlsTests(unittest.TestCase):
    def test_absent_controls_resolve_to_legacy_values_and_geo(self):
        options = advanced_meshing_options(config()['cell_source'])
        self.assertEqual(options, dict(algorithm_2d=5, smoothing=0, optimize_2d=0))
        self.assertEqual(geo_meshing_controls(config()['cell_source']), [])

    def test_allowlisted_algorithm_and_2d_commands(self):
        advanced = dict(algorithm_2d=6, smoothing=2, optimize_2d=3)
        source = config(advanced=advanced)['cell_source']
        self.assertEqual(geo_meshing_controls(source), [
            'Mesh.Algorithm=6;', 'Mesh 2;',
            'OptimizeMesh "Laplace2D";', 'OptimizeMesh "Laplace2D";',
            'OptimizeMesh "Relocate2D";', 'OptimizeMesh "Relocate2D";',
            'OptimizeMesh "Relocate2D";', 'Save "mesh.msh";'])

    def test_schema_rejects_invalid_or_unsupported_controls_before_launch(self):
        invalid = [
            dict(algorithm_2d=8, smoothing=0, optimize_2d=0),
            dict(algorithm_2d=6.0, smoothing=0, optimize_2d=0),
            dict(algorithm_2d=True, smoothing=0, optimize_2d=0),
            dict(algorithm_2d=5, smoothing=11, optimize_2d=0),
            dict(algorithm_2d=5, smoothing=0, optimize_2d=-1),
            dict(algorithm_2d=5, smoothing=0, optimize_2d=0,
                 arbitrary_gmsh_option='Mesh.RecombineAll=1'),
        ]
        with patch('gis_mesh_workbench.gmsh_meshing.run_logged') as launch:
            for advanced in invalid:
                with self.subTest(advanced=advanced), self.assertRaises(ValueError):
                    validate_config(config(advanced=advanced))
            self.assertFalse(launch.called)

    def test_algorithm_codes_must_be_integer_types(self):
        for algorithm in (6.0, True):
            with self.subTest(algorithm=algorithm), self.assertRaises(ValueError):
                validate_advanced_meshing(config(advanced=dict(algorithm_2d=algorithm,
                    smoothing=0, optimize_2d=0))['cell_source'])

    def test_sf1_rejects_controls_and_legacy_profile_stays_fixed(self):
        with self.assertRaises(ValueError):
            validate_config(config('sf1_relief', dict(algorithm_2d=6,
                smoothing=0, optimize_2d=0)))
        self.assertEqual(advanced_meshing_options(config('sf1_relief')['cell_source']),
                         dict(algorithm_2d=5, smoothing=0, optimize_2d=0))
        self.assertEqual(geo_meshing_controls(config('sf1_relief')['cell_source']), [])

    def test_real_gmsh_executes_explicit_2d_passes_once(self):
        if not GMSH.is_file():
            self.skipTest('External Gmsh 4.15.2 is not installed')
        from gis_mesh_workbench.gmsh_meshing import generate, run_logged
        import tempfile
        from unittest.mock import patch

        request = config(advanced=dict(algorithm_2d=6, smoothing=1, optimize_2d=1))
        request['schema_version'] = 'gis-workbench-run-config-v1'
        request['cell_source']['executable'] = str(GMSH)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp).resolve()
            with patch('gis_mesh_workbench.gmsh_meshing.run_logged', wraps=run_logged) as launch:
                _, evidence = generate(request, output)
            args = launch.call_args.args[0]
            geo = (output/'meshing/domain.geo').read_text(encoding='utf-8')
            self.assertIn('-parse_and_exit', args)
            self.assertNotIn('-2', args)
            self.assertIn('Mesh.Algorithm=6;', geo)
            self.assertIn('Mesh 2;', geo)
            self.assertEqual(geo.count('OptimizeMesh "Laplace2D";'), 1)
            self.assertEqual(geo.count('OptimizeMesh "Relocate2D";'), 1)
            self.assertTrue((output/'meshing/mesh.msh').is_file())
            self.assertEqual(evidence['effective_meshing_controls'],
                dict(algorithm_2d=6, smoothing=1, optimize_2d=1))

    def test_v2_preflight_to_real_gmsh_preserves_selected_controls_in_qa(self):
        if not GMSH.is_file():
            self.skipTest('External Gmsh 4.15.2 is not installed')
        request = config(advanced=dict(algorithm_2d=6, smoothing=1, optimize_2d=1))
        request['cell_source']['executable'] = str(GMSH)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            request['output_root'] = str(root/'runs')
            config_path = root/'run.json'
            write_json(config_path, request)
            result = run(config_path)
            self.assertEqual(result['status'], 'pass', result)
            output = Path(result['output'])
            manifest = json.loads((output/'manifest.json').read_text(encoding='utf-8'))
            qa = json.loads((output/'qa.json').read_text(encoding='utf-8'))
            evidence = manifest['gmsh_generated']
            selected = dict(algorithm_2d=6, smoothing=1, optimize_2d=1)
            self.assertEqual(evidence['effective_meshing_controls'], selected)
            self.assertEqual(evidence['requested_meshing_controls'], selected)
            self.assertEqual(evidence['version'], '4.15.2')
            self.assertTrue(qa)
            geo = (output/'meshing/domain.geo').read_text(encoding='utf-8')
            self.assertIn('Mesh.Algorithm=6;', geo)
            self.assertIn('OptimizeMesh "Laplace2D";', geo)
            self.assertIn('OptimizeMesh "Relocate2D";', geo)


if __name__ == '__main__':
    unittest.main()
