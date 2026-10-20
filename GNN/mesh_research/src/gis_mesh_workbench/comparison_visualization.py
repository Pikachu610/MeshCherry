"""Bounded, display-only geometry previews for measured mesh comparisons."""
from itertools import chain
from shapely.geometry import mapping

from .job_control import checkpoint

MAX_PREVIEW_FEATURES = 1200
MAX_PREVIEW_COORDINATES = 120_000


def _coordinate_count(geometry, stop_after):
    count = visited = 0
    pending = [iter((geometry,))]
    while pending:
        if visited % 2048 == 0:
            checkpoint()
        try:
            part = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        visited += 1
        if part.geom_type == 'Polygon':
            for ring in chain((part.exterior,), part.interiors):
                count += len(ring.coords)
                if count > stop_after:
                    return count
                if visited % 2048 == 0:
                    checkpoint()
        elif hasattr(part, 'geoms'):
            pending.append(iter(part.geoms))
        if count > stop_after:
            return count
    return count


def _feature_sample(polygons, tolerance, limit=MAX_PREVIEW_FEATURES):
    """Return a deterministic, bounded subset of actual polygons for display."""
    total = len(polygons)
    if total <= limit:
        indices = range(total)
    else:
        # Evenly spread indices without materializing a full index array.
        indices = ((index * total) // limit for index in range(limit))
    features = []
    remaining_coordinates = MAX_PREVIEW_COORDINATES
    omitted_for_geometry_budget = 0
    for display_index, source_index in enumerate(indices):
        if display_index % 64 == 0:
            checkpoint()
        polygon = polygons[source_index]
        # Preserve topology while dropping coordinate noise. Metrics always
        # use original geometry; the shared coordinate ceiling bounds payloads.
        simplified = polygon.simplify(tolerance, preserve_topology=True)
        candidate = simplified if not simplified.is_empty else polygon
        coordinate_count = _coordinate_count(candidate, remaining_coordinates)
        if coordinate_count > remaining_coordinates:
            omitted_for_geometry_budget += 1
            continue
        features.append(mapping(candidate))
        remaining_coordinates -= coordinate_count
    checkpoint()
    return features, omitted_for_geometry_budget


def comparison_visualization(*, domain, structured=None, structured_total_count=None, unstructured,
                             source_reason='Source raster preview is not available.'):
    """Build display geometry only; never use its sampled features as metrics."""
    minx, miny, maxx, maxy = domain.bounds
    tolerance = max(maxx-minx, maxy-miny, 1e-12) / 1600
    roi = domain.simplify(tolerance, preserve_topology=True)
    if roi.is_empty:
        roi = domain
    roi_coordinates = _coordinate_count(roi, MAX_PREVIEW_COORDINATES)
    roi_available = roi_coordinates <= MAX_PREVIEW_COORDINATES
    def layer(polygons, total_count=None):
        actual_count = len(polygons) if total_count is None else total_count
        features, omitted = _feature_sample(polygons, tolerance)
        return {
            'available': True,
            'feature_count': len(features),
            'total_cell_count': actual_count,
            'sampled': actual_count > len(features) or omitted > 0,
            'geometries': features,
            'omitted_for_geometry_budget': omitted,
        }

    result = {
        'schema_version': 'comparison-visualization-v1',
        'crs_units': 'analysis CRS metres',
        'bounds': [float(minx), float(miny), float(maxx), float(maxy)],
        'roi': mapping(roi) if roi_available else None,
        'roi_available': roi_available,
        'roi_coordinate_count': roi_coordinates if roi_available else None,
        'roi_reason': (None if roi_available else
                       f'ROI outline omitted because its simplified display geometry exceeds the {MAX_PREVIEW_COORDINATES:,}-coordinate preview limit.'),
        'display_geometry_approximate': True,
        'display_simplification_tolerance_m': tolerance,
        'geometry_note': 'Display-only geometry is topology-preserving simplified and may be feature-sampled; all reported counts and metrics use full-resolution source geometry.',
        'source': {'available': False, 'reason': source_reason},
        'structured': (layer(structured, structured_total_count) if structured is not None else {
            'available': False,
            'reason': 'Structured pixel geometry is not included in this comparison preview.',
        }),
        'unstructured': layer(unstructured),
        'shared_zoom': True,
        'metrics_source': 'full comparison report; preview feature counts are not graph-node counts',
        'limit': MAX_PREVIEW_FEATURES,
        'coordinate_limit': MAX_PREVIEW_COORDINATES,
    }
    return result
