"""Bounded geometry kernels and ephemeral intersection-weight reuse."""
from dataclasses import dataclass

import numpy as np
import shapely
from rasterio.warp import transform

from .job_control import checkpoint
from .workload_limits import MAX_MESH_CELLS


CHUNK_PIXELS = 256
# Bound the combined broad-phase result even when two partitions approach the
# mesh-cell ceiling. The adaptive chunk size reaches 256 for ordinary meshes
# and falls to 4 for two partitions of MAX_MESH_CELLS each.
MAX_BATCH_CANDIDATE_PAIRS = 800_000
DEFAULT_CACHE_BYTES = 16 * 1024 * 1024


def automatic_kernel(src, target, semantics, baseline_mode, parts):
    """Enable vector batches only inside the measured workload envelope."""
    verified = (src.crs == target and semantics == 'intensive'
        and baseline_mode == 'target_grid' and src.transform.b == 0
        and src.transform.d == 0 and src.transform.a > 0 and src.transform.e < 0
        and src.width * src.height <= 65536
        and len(parts.get('unstructured', ())) <= 2048
        and len(parts.get('structured', ())) <= 1024)
    return 'vectorized' if verified else 'scalar'


def geometry_chunk_pixels(parts):
    """Return the largest pixel batch whose worst-case candidate count is bounded."""
    cell_count = sum(len(polygons) for name, polygons in parts.items()
                     if name != '__domain__')
    if cell_count <= 0:
        return CHUNK_PIXELS
    return max(1, min(CHUNK_PIXELS, MAX_BATCH_CANDIDATE_PAIRS // cell_count))


def _scalar_footprints(src, target, cols, rows, subdivisions):
    from .indexed_extraction import footprint
    return np.asarray([
        footprint(src, target, int(col), int(row), subdivisions=subdivisions)
        for col, row in zip(cols, rows)
    ], dtype=object)


def _vector_footprints(src, target, cols, rows, subdivisions):
    count = len(cols)
    edge_steps = 1 if src.crs == target else subdivisions
    vertices = np.empty((count, 4 * edge_steps, 2), dtype=np.float64)
    corners = ((0, 0), (1, 0), (1, 1), (0, 1))
    position = 0
    affine = src.transform
    for (x0, y0), (x1, y1) in zip(corners, corners[1:] + corners[:1]):
        for step in range(edge_steps):
            fraction = step / edge_steps
            pixel_col = cols + x0 + (x1-x0)*fraction
            pixel_row = rows + y0 + (y1-y0)*fraction
            vertices[:, position, 0] = affine.a*pixel_col + affine.b*pixel_row + affine.c
            vertices[:, position, 1] = affine.d*pixel_col + affine.e*pixel_row + affine.f
            position += 1
    if src.crs != target:
        flat = vertices.reshape(-1, 2)
        x, y = transform(src.crs, target, flat[:, 0].tolist(), flat[:, 1].tolist())
        vertices[:, :, 0] = np.asarray(x).reshape(count, -1)
        vertices[:, :, 1] = np.asarray(y).reshape(count, -1)
    if not np.isfinite(vertices).all():
        raise ValueError('nonfinite reprojected footprint')
    polygons = shapely.polygons(vertices)
    areas = shapely.area(polygons)
    if np.any(~shapely.is_valid(polygons)) or np.any(areas <= 0):
        raise ValueError('invalid pixel/window footprint')
    return polygons


def _pair_intersections(pixels, polygons, tree, kernel, polygon_array=None):
    """Return sorted positive-area pixel/cell pairs for one bounded chunk."""
    if len(pixels) * len(polygons) > MAX_BATCH_CANDIDATE_PAIRS:
        raise ValueError('comparison geometry batch pair bound exceeded')
    if kernel == 'scalar':
        pixel_indices, cell_indices, values = [], [], []
        candidate_count = 0
        visited = 0
        for pixel_index, pixel in enumerate(pixels):
            checkpoint()
            candidates = tree.query(pixel)
            candidate_count += len(candidates)
            for cell_index in sorted(candidates.tolist()):
                if visited % 128 == 0:
                    checkpoint()
                visited += 1
                area = float(pixel.intersection(polygons[int(cell_index)]).area)
                if not np.isfinite(area):
                    raise ValueError('comparison intersection area is nonfinite')
                if area > 0:
                    pixel_indices.append(pixel_index)
                    cell_indices.append(int(cell_index))
                    values.append(area)
        return (np.asarray(pixel_indices, dtype=np.int64),
                np.asarray(cell_indices, dtype=np.int64),
                np.asarray(values, dtype=np.float64), candidate_count)
    if polygon_array is None:
        polygon_array = np.asarray(polygons, dtype=object)
    elif len(polygon_array) != len(polygons):
        raise ValueError('comparison polygon array does not match its spatial index')
    pairs = tree.query(pixels)
    checkpoint()
    if pairs.size == 0:
        empty = np.empty(0, dtype=np.int64)
        return empty, empty, np.empty(0, dtype=np.float64), 0
    pixel_indices, cell_indices = pairs
    order = np.lexsort((cell_indices, pixel_indices))
    checkpoint()
    pixel_indices = pixel_indices[order].astype(np.int64, copy=False)
    cell_indices = cell_indices[order].astype(np.int64, copy=False)
    areas = np.asarray(shapely.area(shapely.intersection(
        pixels[pixel_indices], polygon_array[cell_indices])),
        dtype=np.float64)
    checkpoint()
    if np.any(~np.isfinite(areas)):
        raise ValueError('comparison intersection area is nonfinite')
    positive = areas > 0
    return (pixel_indices[positive], cell_indices[positive], areas[positive],
            int(len(pairs[0])))


def geometry_batch(src, target, cols, rows, subdivisions, parts, trees, *,
                   valid_mask=None, kernel='vectorized', polygon_arrays=None):
    """Compute bounded footprints, domain support, and partition intersection weights."""
    if len(cols) != len(rows) or len(cols) > CHUNK_PIXELS:
        raise ValueError('comparison geometry batch exceeds its fixed pixel bound')
    if any(name != '__domain__' and len(polygons) > MAX_MESH_CELLS
           for name, polygons in parts.items()):
        raise ValueError('comparison geometry batch exceeds the partition cell bound')
    if valid_mask is not None and len(valid_mask) != len(cols):
        raise ValueError('comparison geometry validity mask does not match its pixel batch')
    active_count = len(cols) if valid_mask is None else int(np.count_nonzero(valid_mask))
    candidate_upper_bound = active_count * sum(
        len(polygons) for name, polygons in parts.items() if name != '__domain__')
    if candidate_upper_bound > MAX_BATCH_CANDIDATE_PAIRS:
        raise ValueError('comparison geometry batch pair bound exceeded')
    checkpoint()
    pixels = (_vector_footprints(src, target, cols, rows, subdivisions)
              if kernel == 'vectorized' else
              _scalar_footprints(src, target, cols, rows, subdivisions))
    pixel_areas = np.asarray(shapely.area(pixels), dtype=np.float64)
    domain_areas = np.asarray(shapely.area(shapely.intersection(
        pixels, parts['__domain__'])), dtype=np.float64)
    intersections = {}
    candidates = {}
    active = (np.arange(len(pixels), dtype=np.int64) if valid_mask is None
              else np.flatnonzero(valid_mask).astype(np.int64, copy=False))
    for name, polygons in parts.items():
        if name == '__domain__':
            continue
        checkpoint()
        pi, ci, area, candidate_count = _pair_intersections(
            pixels[active], polygons, trees[name], kernel,
            None if polygon_arrays is None else polygon_arrays[name])
        pi = active[pi]
        intersections[name] = (pi, ci, area)
        candidates[name] = candidate_count
    return pixels, pixel_areas, domain_areas, intersections, candidates


@dataclass(frozen=True)
class GeometryWeights:
    pixel_areas: np.ndarray
    domain_areas: np.ndarray
    intersections: dict
    candidate_counts: dict

    @property
    def estimated_bytes(self):
        return int(self.pixel_areas.nbytes + self.domain_areas.nbytes +
                   sum(sum(array.nbytes for array in entry)
                       for entry in self.intersections.values()) +
                   4096 + 64*sum(len(entry[0]) for entry in self.intersections.values()))


class GeometryWeightCache:
    """Run-local bounded cache; overflow clears entries and selects recomputation."""
    def __init__(self, max_bytes=DEFAULT_CACHE_BYTES):
        if type(max_bytes) is not int or max_bytes < 0:
            raise ValueError('geometry cache byte limit must be a nonnegative integer')
        self.max_bytes = max_bytes
        self.bytes_used = 0
        self.entries = {}
        self.enabled = True

    def get(self, key):
        return self.entries.get(key) if self.enabled else None

    def put(self, key, weights):
        if not self.enabled:
            return False
        size = weights.estimated_bytes
        if size > self.max_bytes-self.bytes_used:
            self.entries.clear()
            self.bytes_used = 0
            self.enabled = False
            return False
        self.entries[key] = weights
        self.bytes_used += size
        return True
