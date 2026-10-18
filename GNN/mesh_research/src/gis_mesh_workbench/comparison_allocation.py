"""Measured cell-size distribution summaries for mesh comparisons."""
import math

from .job_control import checkpoint
from .workload_limits import MAX_MESH_CELLS


def _finite_positive(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field} must be finite and positive')
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f'{field} must be finite and positive') from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f'{field} must be finite and positive')
    return number


def _quantiles(values):
    ordered = sorted(values)
    last = len(ordered) - 1

    def at(probability):
        rank = probability * last
        lower = math.floor(rank)
        upper = math.ceil(rank)
        if lower == upper:
            return ordered[lower]
        fraction = rank - lower
        return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction

    return dict(min=ordered[0], p10=at(.1), median=at(.5), p90=at(.9), max=ordered[-1])


def allocation_summary(areas_m2, reference_cell_area_m2=None):
    """Summarize cell areas and equivalent-square lengths without inferring relief.

    The equivalent square length is sqrt(cell area); it is not a triangle edge
    length or the realized Gmsh size-field value.
    """
    if not isinstance(areas_m2, (list, tuple)) or not areas_m2:
        raise ValueError('cell areas must be a nonempty list or tuple')
    if len(areas_m2) > MAX_MESH_CELLS:
        raise ValueError('cell area count exceeds the mesh cell limit')
    checkpoint()
    areas = []
    for index, value in enumerate(areas_m2):
        if index % 128 == 0:
            checkpoint()
        areas.append(_finite_positive(value, 'cell area'))
    try:
        total_area = math.fsum(areas)
    except (ArithmeticError, OverflowError, ValueError) as error:
        raise ValueError('total cell area must be finite') from error
    if not math.isfinite(total_area) or total_area <= 0:
        raise ValueError('total cell area must be finite and positive')

    equivalent_lengths = [math.sqrt(area) for area in areas]
    result = dict(
        assessment_status='cell_size_distribution_only',
        node_count=len(areas),
        total_area_m2=total_area,
        quantile_method='linear interpolation at rank p*(n-1), calculated independently for areas and sqrt(areas)',
        cell_area_quantiles_m2=_quantiles(areas),
        equivalent_square_size_m=_quantiles(equivalent_lengths),
        equivalent_square_size_definition='sqrt(cell area); not triangle edge length or realized Gmsh target size',
        relief_alignment='not_assessed',
        interpretation='measured cell-size distribution only; does not show alignment with terrain relief or GNN prediction accuracy',
    )

    if reference_cell_area_m2 is not None:
        reference_area = _finite_positive(reference_cell_area_m2, 'reference_cell_area_m2')
        below = [area for area in areas if area < reference_area]
        try:
            below_area = math.fsum(below)
        except (ArithmeticError, OverflowError, ValueError) as error:
            raise ValueError('area below reference size must be finite') from error
        if not math.isfinite(below_area):
            raise ValueError('area below reference size must be finite')
        result.update(
            refinement=dict(
                reference_cell_area_m2=reference_area,
                reference_equivalent_square_size_m=math.sqrt(reference_area),
                threshold='cell_area_m2 < reference_cell_area_m2',
                node_count_below_reference=len(below),
                node_fraction_below_reference=len(below) / len(areas),
                roi_area_below_reference_m2=below_area,
                roi_area_fraction_below_reference=below_area / total_area,
            ),
        )
    return result
