"""Explicit comparison workload policy; never changes extraction defaults.

Research removes cumulative raster-work ceilings. It does not make an
unbounded-memory algorithm safe: windows, geometry batches, cache, mesh and
categorical accumulators retain their independent bounds.
"""


def validate_workload_mode(value):
    if not isinstance(value, str) or value not in {'bounded', 'research'}:
        raise ValueError('comparison workload mode must be bounded or research')
    return value


def comparison_limits(mode, bounded_limits):
    validate_workload_mode(mode)
    result = dict(bounded_limits)
    if mode == 'research':
        for name in ('max_source_pixels', 'max_pixels_read', 'max_candidate_pairs'):
            result[name] = None
    return result


def exceeds(value, maximum):
    """None is an explicit absent ceiling, not a large finite substitute."""
    return maximum is not None and value > maximum
