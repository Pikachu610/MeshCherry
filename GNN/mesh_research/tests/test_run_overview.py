import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_bounds, from_origin
from rasterio.warp import transform

from gis_mesh_workbench.extraction import Cell
from gis_mesh_workbench.run_overview import render_overview, _shared_edges
from gis_mesh_workbench.job_control import Cancelled


class RunOverviewTests(unittest.TestCase):
    def setUp(self):
        self.cells = (
            Cell('a', 'triangle', ((0, 0), (1, 0), (1, 1))),
            Cell('b', 'triangle', ((0, 0), (1, 1), (0, 1))),
            Cell('c', 'triangle', ((1, 1), (2, 1), (2, 2))),
        )

    def test_adjacency_uses_shared_positive_length_edges_only(self):
        edges = _shared_edges(self.cells)
        self.assertEqual(len(edges), 1)
        self.assertEqual(set(edges[0][1]), {0, 1})

    def test_renders_dem_sf1_field_and_actual_mesh_panels(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            dem = output / 'dem.tif'
            with rasterio.open(dem, 'w', driver='GTiff', width=4, height=4,
                               count=1, dtype='float32', crs='EPSG:32649',
                               transform=from_origin(0, 2, .5, .5), nodata=-9999) as dst:
                dst.write(np.arange(16, dtype=np.float32).reshape(4, 4), 1)
            (output / 'meshing').mkdir()
            np.savez_compressed(output / 'meshing' / 'sf1-control-field.npz',
                                x=np.array([0., 2.]), y=np.array([2., 0.]),
                                h=np.array([[300., 400.], [500., 600.]]))
            ok = render_overview(output, self.cells,
                {'analysis_crs': 'EPSG:32649',
                 'cell_source': {'dem_raster_path': str(dem), 'dem_band': 1,
                                 'sizing': 'sf1_relief'}})
            self.assertTrue(ok)
            self.assertTrue((output / 'run-overview.png').is_file())
            metadata = json.loads((output / 'run-overview.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['dem_status'], 'rendered')
            self.assertEqual(metadata['target_field_status'], 'saved_control_field')
            self.assertEqual(metadata['shared_positive_length_edges'], 1)

    def test_missing_dem_and_field_are_labeled_without_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            self.assertTrue(render_overview(output, self.cells,
                {'cell_source': {'sizing': 'relief'}}))
            metadata = json.loads((output / 'run-overview.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['dem_status'], 'unavailable')
            self.assertEqual(metadata['target_field_status'], 'unavailable')

    def test_uniform_target_is_reported_as_configured_not_observed(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output / 'meshing').mkdir()
            (output / 'meshing' / 'size-field.json').write_text(
                json.dumps({'method': 'uniform', 'requested_target_size_m': 500}), encoding='utf-8')
            self.assertTrue(render_overview(output, self.cells,
                {'cell_source': {'sizing': 'uniform'}}))
            metadata = json.loads((output / 'run-overview.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['target_field_status'], 'saved_uniform_field')

    def test_configured_uniform_without_saved_field_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            self.assertTrue(render_overview(output, self.cells,
                {'cell_source': {'sizing': 'uniform', 'h_max_m': 500}}))
            metadata = json.loads((output / 'run-overview.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['target_field_status'], 'unavailable')

    def test_evidence_field_path_cannot_escape_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / 'run'
            output.mkdir()
            outside = root / 'sf1-control-field.npz'
            np.savez_compressed(outside, x=np.array([0., 1.]), y=np.array([0., 1.]),
                                h=np.array([[300., 400.], [500., 600.]]))
            self.assertTrue(render_overview(output, self.cells,
                {'cell_source': {'sizing': 'sf1_relief'}},
                {'control_field_path': str(outside)}))
            metadata = json.loads((output / 'run-overview.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['target_field_status'], 'unavailable')

    def test_cancellation_propagates_and_closes_partial_figure(self):
        import matplotlib.pyplot as plt

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            before = set(plt.get_fignums())
            with patch('gis_mesh_workbench.run_overview.checkpoint',
                       side_effect=[None, None, Cancelled('cancelled')]):
                with self.assertRaises(Cancelled):
                    render_overview(output, self.cells, {'cell_source': {'sizing': 'relief'}})
            self.assertEqual(set(plt.get_fignums()), before)
            self.assertFalse((output / 'run-overview.png').exists())

    def test_geographic_dem_is_warped_to_mesh_analysis_crs_for_display(self):
        from gis_mesh_workbench import run_overview

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            dem = output / 'geographic-dem.tif'
            projected_ring = [(500000., 1000000.), (501000., 1000000.),
                              (501000., 1001000.), (500000., 1001000.)]
            lon, lat = transform('EPSG:32649', 'EPSG:4326',
                                 [point[0] for point in projected_ring],
                                 [point[1] for point in projected_ring])
            west, east = min(lon) - .001, max(lon) + .001
            south, north = min(lat) - .001, max(lat) + .001
            with rasterio.open(dem, 'w', driver='GTiff', width=16, height=16,
                               count=1, dtype='float32', crs='EPSG:4326',
                               transform=from_bounds(west, south, east, north, 16, 16),
                               nodata=-9999) as dst:
                dst.write(np.arange(256, dtype=np.float32).reshape(16, 16), 1)

            cells = (
                Cell('a', 'triangle', ((500000., 1000000.), (501000., 1000000.), (501000., 1001000.))),
                Cell('b', 'triangle', ((500000., 1000000.), (501000., 1001000.), (500000., 1001000.))),
            )
            original_warped_vrt = run_overview.WarpedVRT
            with patch('gis_mesh_workbench.run_overview.WarpedVRT',
                       side_effect=lambda *args, **kwargs: original_warped_vrt(*args, **kwargs)) as warp:
                self.assertTrue(render_overview(output, cells,
                    {'analysis_crs': 'EPSG:32649',
                     'cell_source': {'dem_raster_path': str(dem), 'dem_band': 1, 'sizing': 'relief'}}))
            warp.assert_called_once()
            metadata = json.loads((output / 'run-overview.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['dem_status'], 'rendered')


if __name__ == '__main__':
    unittest.main()
