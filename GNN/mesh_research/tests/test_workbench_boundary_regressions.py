"""Regression coverage for degenerate SHP ring edges at the meshing seam."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import shapefile
from rasterio.crs import CRS
from shapely.geometry import Polygon, box

from gis_mesh_workbench.gmsh_meshing import inspect_domain
from gis_mesh_workbench.boundary import geometry_lines, read_boundary


class BoundaryRegressionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'domain.shp'

    def write_boundary(self, ring):
        with shapefile.Writer(str(self.path), shapeType=shapefile.POLYGON) as writer:
            writer.field('id', 'N')
            writer.poly([ring])
            writer.record(1)
        self.path.with_suffix('.prj').write_text(
            CRS.from_epsg(32649).to_wkt(), encoding='utf-8')

    def config(self):
        return dict(analysis_crs='EPSG:32649', cell_source=dict(
            kind='gmsh_generated', sizing='uniform',
            boundary_path=str(self.path), h_min_m=100, h_max_m=250,
            max_cells=1000, executable=str(self.root / 'gmsh.exe')))

    def test_consecutive_duplicate_vertex_is_rejected_before_executable_inspection(self):
        # Shapely considers this clockwise polygon valid and positive-area, but
        # the repeated 4th/5th vertices would create a zero-length Gmsh line.
        ring = [(500000, 1000000), (500000, 1001000), (501000, 1001000),
                (501000, 1000000), (501000, 1000000), (500000, 1000000)]
        self.assertTrue(Polygon(ring).is_valid)
        self.assertGreater(Polygon(ring).area, 0)
        self.write_boundary(ring)

        with patch('gis_mesh_workbench.gmsh_meshing.inspect_executable') as inspect:
            with self.assertRaisesRegex(ValueError, r'ring 1.*vertex 4.*vertex 5'):
                inspect_domain(self.config())
            inspect.assert_not_called()

    def test_ordinary_closed_boundary_keeps_same_domain(self):
        ring = [(500000, 1000000), (500000, 1001000), (501000, 1001000),
                (501000, 1000000), (500000, 1000000)]
        self.write_boundary(ring)

        domain, evidence = read_boundary(self.path, 'EPSG:32649')

        self.assertTrue(domain.equals(box(500000, 1000000, 501000, 1001000)))
        self.assertEqual(list(domain.exterior.coords), ring)
        self.assertEqual(evidence['source_vertices'], 5)
        self.assertFalse(evidence['simplification_applied'])

    def test_gmsh_geometry_serializer_defensively_rejects_zero_length_edges(self):
        ring = [(0, 0), (0, 1), (1, 1), (1, 0), (1, 0), (0, 0)]

        with self.assertRaisesRegex(ValueError, r'surface 1, ring 1.*vertex 4.*vertex 5'):
            geometry_lines(Polygon(ring))


if __name__ == '__main__':
    unittest.main()
