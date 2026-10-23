"""Metadata-only admission using the measured comparison's window policy.

This counts I/O work, not valid pixels. Masks and NoData cannot reduce a read
window and are deliberately not inspected. Cross-CRS culling stays disabled.
"""
from .indexed_extraction import MAX_WINDOWS, metadata, window_candidates, windows
from .job_control import checkpoint, observe


class ComparisonAdmissionError(ValueError):
    """Predictable whole-job ceiling rejection, with a bounded workload record."""

    def __init__(self, message, estimate):
        super().__init__(message)
        self.estimate = estimate


def estimate_read_workload(src, target, tree, cell_count, *, band, semantics, cap):
    """Count the exact selected read windows before any raster values are read.

    A rejected estimate may be an early lower bound: scanning stops as soon as
    a ceiling is proved exceeded. Accepted counts equal runtime window counts
    for immutable inputs. Pixel/cell pairs and class entries remain runtime
    guards, since their exact costs require value/geometry evaluation.
    """
    if semantics not in {'intensive', 'extensive', 'categorical'}:
        raise ValueError('declared comparison semantics required')
    metadata(src, band, cap)
    pass_count = 2 if semantics == 'intensive' else 1
    result = dict(pass_count=pass_count, windows_scanned_per_pass=0,
        windows_read_per_pass=0, pixels_read_per_pass=0,
        aggregate_windows_scanned=0, aggregate_windows_read=0,
        aggregate_pixels_read=0, complete=False,
        scope='whole comparison job; includes every intensive pass',
        window_culling='affine_cell_index' if src.crs == target else 'disabled_cross_crs_conservative')
    observe('comparison_admission')
    for window in windows(src, band, cap):
        checkpoint()
        result['windows_scanned_per_pass'] += 1
        result['aggregate_windows_scanned'] = result['windows_scanned_per_pass'] * pass_count
        if result['aggregate_windows_scanned'] > MAX_WINDOWS:
            raise ComparisonAdmissionError('comparison window scan limit exceeded before evaluation', result)
        candidates = window_candidates(src, target, tree, window, cell_count)
        checkpoint()
        if len(candidates):
            result['windows_read_per_pass'] += 1
            result['pixels_read_per_pass'] += int(window.width * window.height)
            result['aggregate_windows_read'] = result['windows_read_per_pass'] * pass_count
            result['aggregate_pixels_read'] = result['pixels_read_per_pass'] * pass_count
            if result['aggregate_pixels_read'] > cap['max_pixels_read']:
                raise ComparisonAdmissionError('comparison read pixel limit exceeded before evaluation', result)
    checkpoint()
    result['complete'] = True
    return result
