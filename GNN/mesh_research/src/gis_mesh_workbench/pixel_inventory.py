"""Hierarchical geometric counting for axis-aligned native raster grids."""
import math

from .indexed_extraction import footprint
from .job_control import checkpoint
from shapely.geometry import box
from shapely.prepared import prep


def supports_hierarchy(src, target):
    """Return whether tile shortcuts preserve valid native pixel geometry."""
    transform = src.transform
    if src.crs != target or transform.b != 0 or transform.d != 0:
        return False
    if transform.a == 0 or transform.e == 0:
        return False
    x_end = transform.c + transform.a*src.width
    y_end = transform.f + transform.e*src.height
    if not all(math.isfinite(value) for value in (x_end, y_end)):
        return False
    x_ulp = math.ulp(max(abs(transform.c), abs(x_end)))
    y_ulp = math.ulp(max(abs(transform.f), abs(y_end)))
    # Keep every one-pixel step comfortably above coordinate rounding. At
    # coarser origins a nominally nonzero affine can still collapse corners.
    return abs(transform.a) > 4*x_ulp and abs(transform.e) > 4*y_ulp


def hierarchical_count(src, domain, target, bounds, progress=None):
    """Count positive-area pixel intersections, accounting for every candidate.

    This is valid only for same-CRS, axis-aligned affine grids. Mixed tiles are
    recursively divided; one-pixel leaves use the inventory's original exact
    footprint and intersection-area predicate.
    """
    col0, row0, col1, row1 = bounds
    total = int((col1-col0)*(row1-row0))
    completed = 0
    count = 0
    next_report = 256
    prepared_domain = prep(domain)

    def account(amount):
        nonlocal completed, next_report
        completed += amount
        if progress is not None and (completed >= next_report or completed == total):
            progress('count_native_pixels', completed, total, 'source_pixels_examined')
            next_report = completed + 256

    def tile_geometry(left, top, right, bottom):
        x0, y0 = src.transform @ (left, top)
        x1, y1 = src.transform @ (right, bottom)
        return box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def visit(left, top, right, bottom):
        nonlocal count
        checkpoint()
        area = (right-left)*(bottom-top)
        tile = tile_geometry(left, top, right, bottom)
        if prepared_domain.disjoint(tile):
            account(area)
            return
        if prepared_domain.covers(tile):
            count += area
            account(area)
            return
        if area == 1:
            pixel = footprint(src, target, left, top)
            if pixel.intersection(domain).area > 0:
                count += 1
            account(1)
            return

        if right-left >= bottom-top and right-left > 1:
            middle = left + (right-left)//2
            visit(left, top, middle, bottom)
            visit(middle, top, right, bottom)
        else:
            middle = top + (bottom-top)//2
            visit(left, top, right, middle)
            visit(left, middle, right, bottom)

    if total:
        visit(col0, row0, col1, row1)
    checkpoint()
    if completed != total:
        raise RuntimeError('hierarchical pixel count did not account for every candidate')
    return count
