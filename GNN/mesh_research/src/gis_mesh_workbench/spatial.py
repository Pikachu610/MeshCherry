"""One spatial contract for adapters and extraction; no coordinate relabeling."""
import math
from rasterio.crs import CRS


def metric_crs(value):
    if value is None:
        raise ValueError('CRS is required')
    crs = CRS.from_user_input(value)
    if not crs.is_projected or not math.isclose(crs.linear_units_factor[1], 1.0):
        raise ValueError('analysis CRS must be projected with metre units')
    return crs
