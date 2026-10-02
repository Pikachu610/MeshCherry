"""Read-only triangle import and deterministic structured-grid adapter."""
from dataclasses import dataclass
import json
import math
from pathlib import Path
from types import MappingProxyType

import numpy as np

from .extraction import Cell, _cells, _digest
from .spatial import metric_crs


@dataclass(frozen=True)
class CellSet:
    cells: tuple[Cell, ...]
    crs_wkt: str
    coordinate_units: str
    adapter_kind: str
    provenance: MappingProxyType


def _json(path):
    return json.loads(path.read_text(encoding='utf-8'),
                      parse_constant=lambda value: (_ for _ in ()).throw(
                          ValueError('nonfinite JSON constant: ' + value)))


def import_mesh(path, *, analysis_crs, max_cells=1000):
    """mesh-artifact-v0 only; cell-N is derived from retained triangle order.

    No reprojection or renumbering. Retained-run manifest and sibling graph are
    checked when present. Standalone artifacts remain provenance-unverified.
    """
    path = Path(path).resolve()
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('mesh input exceeds 16 MiB reference limit')
    before = _digest(path)
    mesh = _json(path)
    if mesh.get('schema_version') != 'mesh-artifact-v0':
        raise ValueError('unsupported mesh schema')
    crs = metric_crs(mesh.get('crs'))
    if crs != metric_crs(analysis_crs):
        raise ValueError('mesh CRS mismatch; relabeling forbidden')
    if mesh.get('coordinate_units') != 'm':
        raise ValueError('mesh coordinate_units must be m')
    source_vertices = mesh.get('vertices')
    if (not isinstance(source_vertices, list) or not source_vertices
            or any(not isinstance(v, list) or len(v) not in (2, 3)
                   or any(type(x) not in (int, float) or not math.isfinite(x) for x in v)
                   for v in source_vertices)
            or len({len(v) for v in source_vertices}) != 1):
        raise ValueError('vertices must be uniform finite numeric XY or XYZ records')
    source_dimension = len(source_vertices[0])
    vertices = np.asarray(source_vertices, dtype=float)[:, :2]
    triangles = mesh.get('triangles')
    if (vertices.ndim != 2 or vertices.shape[1] != 2 or not np.isfinite(vertices).all()
            or not isinstance(triangles, list) or not 0 < len(triangles) <= max_cells
            or len(triangles)**2 > 1000000):
        raise ValueError('invalid vertices or cell workload limit')
    cells = []
    for index, triangle in enumerate(triangles):
        if (not isinstance(triangle, list) or len(triangle) != 3
                or any(type(i) is not int or not 0 <= i < len(vertices) for i in triangle)
                or len(set(triangle)) != 3):
            raise ValueError('invalid triangle indices')
        cells.append(Cell(f'cell-{index}', 'triangle',
                          tuple(tuple(map(float, vertices[i])) for i in triangle)))
    cells = tuple(cells)
    _cells(cells)
    provenance = dict(source_path=str(path), source_sha256=before,
                      source_vertex_dimension=source_dimension,
                      planar_geometry_policy='first two coordinates are XY; Z ignored',
                      id_policy='cell-N from triangle order; not encoded in mesh.json',
                      source_run_id=None, source_run_path=None, manifest_path=None,
                      manifest_sha256=None, graph_path=None, graph_sha256=None,
                      run_provenance='UNVERIFIED')
    run = path.parent.parent if path.parent.name == 'mesh' else path.parent
    manifest_path = run / 'manifest.json'
    component_hashes = {path: before}
    if manifest_path.exists():
        component_hashes[manifest_path] = _digest(manifest_path)
        manifest = _json(manifest_path)
        if manifest.get('schema_version') != 'mesh-run-manifest-v0' or manifest.get('status') != 'pass':
            raise ValueError('source manifest must be supported and passing')
        key = path.relative_to(run).as_posix()
        recorded = manifest.get('artifact_hashes', {}).get(key)
        if recorded is not None and recorded != before:
            raise ValueError('source mesh manifest hash mismatch')
        provenance.update(source_run_id=manifest.get('run_id'), source_run_path=str(run),
                          manifest_path=str(manifest_path), manifest_sha256=component_hashes[manifest_path],
                          run_provenance='pass_manifest_hash_verified' if recorded else 'pass_manifest_hash_absent')
    graph_path = run / 'graph' / 'graph.json'
    if graph_path.exists():
        component_hashes[graph_path] = _digest(graph_path)
        graph = _json(graph_path)
        nodes = graph.get('nodes', [])
        if (graph.get('schema_version') != 'mesh-graph-v0' or not isinstance(nodes, list)
                or [n.get('cell_id') for n in nodes] != [c.cell_id for c in cells]):
            raise ValueError('sibling graph ID order mismatch')
        provenance.update(graph_path=str(graph_path), graph_sha256=component_hashes[graph_path])
    if any(_digest(p) != digest for p, digest in component_hashes.items()):
        raise ValueError('source component changed during import')
    return CellSet(cells, crs.to_wkt(), 'm', 'mesh-artifact-v0', MappingProxyType(provenance))


def structured_grid(*, extent, cell_width_m, cell_height_m, analysis_crs,
                    boundary_policy='strict', max_cells=1000):
    """Extent=(west,south,east,north); rows start south, columns eastward.

    strict accepts whole-cell counts within 1e-9 relative/absolute count
    tolerance. clip never enlarges extent; last east/north cells can be narrow.
    """
    crs = metric_crs(analysis_crs)
    if (len(extent) != 4 or any(not math.isfinite(float(v)) for v in extent)
            or boundary_policy not in {'strict', 'clip'}):
        raise ValueError('finite extent and strict/clip policy required')
    west, south, east, north = map(float, extent)
    dx, dy = float(cell_width_m), float(cell_height_m)
    if not all(math.isfinite(v) and v > 0 for v in (dx, dy)) or east <= west or north <= south:
        raise ValueError('positive resolution and increasing extent required')
    counts = ((east-west)/dx, (north-south)/dy)
    if any(not math.isfinite(v) for v in counts):
        raise ValueError('grid count exceeds numeric limits')
    whole = [math.isclose(v, round(v), rel_tol=1e-9, abs_tol=1e-9) for v in counts]
    if boundary_policy == 'strict' and not all(whole):
        raise ValueError('strict extent must divide into whole cells')
    cols, rows = [round(v) if is_whole else math.ceil(v) for v, is_whole in zip(counts, whole)]
    count = cols * rows
    if count <= 0 or count > max_cells or count**2 > 1000000:
        raise ValueError('grid workload exceeds reference limit before materialization')
    cells = []
    for row in range(rows):
        y0, y1 = south+row*dy, min(north, south+(row+1)*dy)
        for col in range(cols):
            x0, x1 = west+col*dx, min(east, west+(col+1)*dx)
            cells.append(Cell(f'grid-r{row:03d}-c{col:03d}', 'rectangle',
                              ((x0,y0),(x1,y0),(x1,y1),(x0,y1))))
    cells = tuple(cells)
    _cells(cells)
    return CellSet(cells, crs.to_wkt(), 'm', 'structured-grid', MappingProxyType(dict(
        extent=tuple(map(float, extent)), cell_width_m=dx, cell_height_m=dy,
        rows=rows, columns=cols, boundary_policy=boundary_policy,
        id_policy='grid-rRRR-cCCC; row0 south; columns east',
        strict_count_tolerance=1e-9)))
