"""Count-only inventory for accepted mesh and structured comparison grids.

No raster values are read here. Native-raster counts are geometric source-pixel
counts, while valid-data pixels remain an explicitly uncomputed quantity.
"""
import math
from pathlib import Path

from shapely.geometry import box
from shapely.ops import unary_union

from .extraction import _cells, _digest
from .indexed_extraction import footprint
from .job_control import checkpoint, observe
from .mesh_workflow import read_frozen
from .pixel_inventory import hierarchical_count, supports_hierarchy
from .raster_source import open_raster
from .spatial import metric_crs
from .workload_limits import MAX_MESH_CELLS


def _positive_polygon_parts(geometry):
    """Yield each positive-area polygon component, preserving disconnected cells."""
    pending = [geometry]
    visited = 0
    while pending:
        if visited % 128 == 0:
            checkpoint()
        visited += 1
        part = pending.pop()
        if part.geom_type == 'Polygon' and part.area > 0:
            yield part
        elif hasattr(part, 'geoms'):
            pending.extend(part.geoms)


def _target_grid_count(domain, size, progress=None):
    xmin, ymin, xmax, ymax = domain.bounds
    counts = ((xmax-xmin)/size, (ymax-ymin)/size)
    if any(not math.isfinite(n) or n > MAX_MESH_CELLS for n in counts):
        raise ValueError('comparison square bounding-grid cell limit exceeded')
    nx, ny = map(math.ceil, counts)
    if nx * ny > MAX_MESH_CELLS:
        raise ValueError('comparison square bounding-grid cell limit exceeded')
    total = nx * ny
    result = []
    completed = 0
    for row in range(ny):
        checkpoint()
        for col in range(nx):
            if col % 128 == 0:
                checkpoint()
            square = box(xmin+col*size, ymin+row*size,
                         min(xmax, xmin+(col+1)*size), min(ymax, ymin+(row+1)*size))
            clipped = square.intersection(domain)
            if clipped.area > 0:
                # A clipped component, not its source square, is one control volume.
                result.extend(_positive_polygon_parts(clipped))
                if len(result) > MAX_MESH_CELLS:
                    raise ValueError('comparison connected square cell limit exceeded')
            completed += 1
            if progress is not None and (completed % 64 == 0 or completed == total):
                progress('count_target_grid', completed, total, 'bounding_grid_cells')
    if not result:
        raise ValueError('comparison square partition is empty')
    checkpoint()
    rebuilt = unary_union(result)
    checkpoint()
    if rebuilt.symmetric_difference(domain).area > max(1e-8, domain.area*1e-9):
        raise ValueError('comparison partitions do not cover the same ROI')
    return len(result), [xmin, ymin], dict(columns=nx, rows=ny, cells_examined=total)


def _native_pixel_count(raster_path, domain, analysis_crs, progress=None):
    import numpy as np
    from .indexed_extraction import limits
    target = metric_crs(analysis_crs)
    cap = limits()
    with open_raster(raster_path) as src:
        if src.crs is None:
            raise ValueError('source raster CRS missing')
        if not np.isfinite(tuple(src.transform)).all() or src.transform.determinant == 0:
            raise ValueError('finite nonsingular raster affine required')
        if src.width * src.height > cap['max_source_pixels']:
            raise ValueError('source pixel limit exceeded')
        if src.crs == target:
            xmin, ymin, xmax, ymax = domain.bounds
            inverse = ~src.transform
            corners = [inverse @ point for point in
                ((xmin,ymin),(xmin,ymax),(xmax,ymin),(xmax,ymax))]
            col0=max(0,min(src.width,math.floor(min(p[0] for p in corners))))
            col1=max(0,min(src.width,math.ceil(max(p[0] for p in corners))))
            row0=max(0,min(src.height,math.floor(min(p[1] for p in corners))))
            row1=max(0,min(src.height,math.ceil(max(p[1] for p in corners))))
        else:
            # A transformed ROI bound is not a safe source-pixel enclosure.
            col0=row0=0;col1=src.width;row1=src.height
        total = int((col1-col0)*(row1-row0))
        if supports_hierarchy(src, target):
            count = hierarchical_count(src, domain, target,
                (col0, row0, col1, row1), progress)
            return count, dict(width=src.width, height=src.height, crs=src.crs.to_string(),
                transform=list(src.transform), shape=[src.height, src.width],
                source_pixel_count=int(src.width*src.height), candidate_pixel_count=total,
                edge_subdivisions=8, footprint_approximate=False,
                area_model='projected planar; cross-CRS edges approximated',
                count_policy='positive-area intersections under the declared footprint model')
        count = 0
        completed = 0
        # Metadata-only count: do not read a band, mask, or NoData values.
        for row in range(row0,row1):
            checkpoint()
            for col in range(col0,col1):
                if col % 64 == 0:
                    checkpoint()
                pixel = footprint(src, target, col, row, subdivisions=8)
                if pixel.intersection(domain).area > 0:
                    count += 1
                completed += 1
                if progress is not None and (completed % 256 == 0 or completed == total):
                    progress('count_native_pixels', completed, total, 'source_pixels_examined')
        checkpoint()
        return count, dict(width=src.width, height=src.height, crs=src.crs.to_string(),
            transform=list(src.transform), shape=[src.height, src.width],
            source_pixel_count=int(src.width*src.height), candidate_pixel_count=total,
            edge_subdivisions=8, footprint_approximate=src.crs != target,
            area_model='projected planar; cross-CRS edges approximated',
            count_policy='positive-area intersections under the declared footprint model')


def build_inventory(cells_path, *, mesh_job_id, baseline_mode, analysis_crs,
                    cell_size_m=None, raster_path=None, progress=None):
    """Count under the declared footprint model after revalidating inputs."""
    if baseline_mode not in {'target_grid', 'native_raster'}:
        raise ValueError('baseline mode must be target_grid or native_raster')
    if baseline_mode == 'target_grid':
        try:
            valid_size=(not isinstance(cell_size_m,bool)
                and isinstance(cell_size_m,(int,float))
                and math.isfinite(float(cell_size_m)) and float(cell_size_m)>0)
        except (OverflowError,ValueError):
            valid_size=False
        if not valid_size or raster_path is not None:
            raise ValueError('target-grid inventory requires a positive cell size only')
    elif cell_size_m is not None or raster_path is None:
        raise ValueError('native-raster inventory requires a raster only')

    cells_path = Path(cells_path)
    manifest_path = cells_path.parent / 'manifest.json'
    mesh_hash = _digest(cells_path)
    manifest_hash = _digest(manifest_path)
    frozen = read_frozen(cells_path, analysis_crs, maximum=MAX_MESH_CELLS,
                         expected_digest=None)
    if any(cell.kind != 'triangle' for cell in frozen.cells):
        raise ValueError('comparison requires an accepted triangular mesh')
    if progress is not None:
        progress('prepare_domain', 0, len(frozen.cells), 'mesh_cells')
    triangles = _cells(frozen.cells)
    domain = unary_union(triangles)
    checkpoint()
    if progress is not None:
        progress('prepare_domain', len(frozen.cells), len(frozen.cells), 'mesh_cells')

    raster_hashes = {}
    if raster_path is not None:
        from .raster_source import bundle_paths
        raster_path = Path(raster_path)
        raster_hashes = {str(path): _digest(path) for path in bundle_paths(raster_path)}

    if baseline_mode == 'target_grid':
        baseline_count, origin, grid = _target_grid_count(domain, float(cell_size_m), progress)
        definition = 'positive_area_clipped_connected_components'
        provenance = dict(cell_size_m=float(cell_size_m), origin_xy=origin,
            boundary_policy='exact positive-area intersection with accepted triangle union; holes retained; disconnected polygon pieces counted separately',
            grid=grid)
        unit = 'cells'
    else:
        baseline_count, raster_grid = _native_pixel_count(raster_path, domain, analysis_crs, progress)
        definition = 'source_pixels_with_positive_area_intersection_with_mesh_union'
        provenance = dict(raster_grid=raster_grid,
            valid_data_count=dict(value=None, status='not_computed', reason='count-only inventory does not read raster values or masks'))
        unit = 'pixels'

    observe('comparison_publish')
    if _digest(cells_path) != mesh_hash or _digest(manifest_path) != manifest_hash:
        raise ValueError('Accepted mesh changed during inventory count')
    if raster_path is not None:
        from .raster_source import bundle_paths
        if {str(path): _digest(path) for path in bundle_paths(raster_path)} != raster_hashes:
            raise ValueError('Raster bundle changed during inventory count')

    mesh_count = len(frozen.cells)
    difference = mesh_count - baseline_count
    reduction = 100 * (baseline_count - mesh_count) / baseline_count if baseline_count else None
    return dict(schema_version='mesh-comparison-inventory-v1', status='pass',
        mesh_job_id=mesh_job_id, mesh_count=dict(count=mesh_count,
            definition='accepted_triangle_cells', unit='cells'),
        baseline=dict(mode=baseline_mode, count=baseline_count,
            definition=definition, unit=unit),
        difference=dict(signed_mesh_minus_baseline=difference,
            reduction_percent=reduction),
        provenance=dict(mesh_sha256=mesh_hash, mesh_manifest_sha256=manifest_hash,
            geometry_digest=frozen.provenance['geometry_digest'],
            raster_bundle_sha256=raster_hashes or None, **provenance))
