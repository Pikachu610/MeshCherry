"""Bounded resources for original-boundary mesh processing; never simplifies ROI."""
MAX_MESH_CELLS = 100000
MAX_BOUNDARY_POINTS = 250000
MAX_MESH_NODES = 250000
MAX_MSH_ELEMENTS = 600000
MAX_MESH_BYTES = 64 * 1024 * 1024
MAX_OVERLAP_CANDIDATES = 5000000
GMSH_TIMEOUT_SECONDS = 300
HIGH_DETAIL_NOTICE_POINTS = 2000


def cell_cap(config):
    source = config.get('cell_source', {})
    if config.get('schema_version') == 'gis-workbench-run-config-v2' and source.get('kind') in {
        'gmsh_generated', 'mesh_artifact', 'frozen_cells'
    }:
        return MAX_MESH_CELLS
    return 1000
