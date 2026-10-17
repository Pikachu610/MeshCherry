"""Create a clearly synthetic, portable .mcy example for desktop acceptance."""
import argparse
from pathlib import Path

from gis_mesh_workbench.adapters import import_mesh
from gis_mesh_workbench.extraction import extract_attributes
from gis_mesh_workbench.mesh_workflow import quality
from gis_mesh_workbench.onboarding import sample_directory
from gis_mesh_workbench.project_io import save_project


def create(path):
    root = sample_directory()
    cells = import_mesh(root/'two_triangles.json', analysis_crs='EPSG:32649')
    attributes = extract_attributes(root/'sample_10_30.tif', cells.cells,
        cell_crs=cells.crs_wkt, analysis_crs=cells.crs_wkt, band=1,
        semantics='intensive', value_units='synthetic-value')
    payload = dict(schema_version='workbench-cells-v1', crs_wkt=cells.crs_wkt,
        coordinate_units='m', cells=[dict(cell_id=c.cell_id,kind=c.kind,xy=[list(p) for p in c.xy]) for c in cells.cells])
    save_project(path,cells=payload,qa=dict(status='pass',mesh_quality=quality(cells.cells)),
        attributes=attributes,provenance=dict(synthetic_example=True),
        view_settings=dict(show_edges=True,show_nodes=True,result_tab='extract'))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path)
    args=parser.parse_args();create(args.output)
