from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


Point = tuple[float, float]


def _polygon_area(polygon: list[Point]) -> float:
    return abs(
        sum(
            polygon[index][0] * polygon[(index + 1) % len(polygon)][1]
            - polygon[(index + 1) % len(polygon)][0] * polygon[index][1]
            for index in range(len(polygon))
        )
    ) / 2.0 if len(polygon) >= 3 else 0.0


def _clip_half_plane(
    polygon: list[Point],
    inside: Any,
    intersect: Any,
) -> list[Point]:
    if not polygon:
        return []
    output: list[Point] = []
    previous = polygon[-1]
    previous_inside = inside(previous)
    for current in polygon:
        current_inside = inside(current)
        if current_inside:
            if not previous_inside:
                output.append(intersect(previous, current))
            output.append(current)
        elif previous_inside:
            output.append(intersect(previous, current))
        previous = current
        previous_inside = current_inside
    return output


def _clip_to_rectangle(
    polygon: list[Point], x_min: float, y_min: float, x_max: float, y_max: float
) -> list[Point]:
    def vertical(first: Point, second: Point, x: float) -> Point:
        scale = (x - first[0]) / (second[0] - first[0])
        return (x, first[1] + scale * (second[1] - first[1]))

    def horizontal(first: Point, second: Point, y: float) -> Point:
        scale = (y - first[1]) / (second[1] - first[1])
        return (first[0] + scale * (second[0] - first[0]), y)

    clipped = _clip_half_plane(
        polygon, lambda point: point[0] >= x_min, lambda a, b: vertical(a, b, x_min)
    )
    clipped = _clip_half_plane(
        clipped, lambda point: point[0] <= x_max, lambda a, b: vertical(a, b, x_max)
    )
    clipped = _clip_half_plane(
        clipped, lambda point: point[1] >= y_min, lambda a, b: horizontal(a, b, y_min)
    )
    return _clip_half_plane(
        clipped, lambda point: point[1] <= y_max, lambda a, b: horizontal(a, b, y_max)
    )


def remap_raster(
    raster_path: Path,
    vertices: list[list[float]],
    triangles: list[list[int]],
) -> dict[str, Any]:
    source_bytes = raster_path.read_bytes()
    source = json.loads(source_bytes)
    grid = source["grid"]
    values = grid["values"]
    row_count = len(values)
    column_count = len(values[0])
    x_min = float(grid["x_min"])
    y_min = float(grid["y_min"])
    cell_width = float(grid["cell_width"])
    cell_height = float(grid["cell_height"])
    nodata = grid.get("nodata")
    if grid.get("row_order") != "bottom_to_top":
        raise ValueError("only bottom_to_top raster rows are supported")
    quantity_type = source.get("quantity_type")
    if quantity_type not in {"intensive", "extensive", "categorical"}:
        raise ValueError(f"unsupported quantity type: {quantity_type}")
    pixel_area = cell_width * cell_height

    if quantity_type == "categorical":
        overlap_by_cell: list[list[tuple[int, int, float]]] = []
        cell_class_fractions: list[dict[str, float]] = []
        cell_majorities: list[str | None] = []
        coverage: list[float] = []
        for triangle in triangles:
            polygon = [(vertices[index][0], vertices[index][1]) for index in triangle]
            triangle_area = _polygon_area(polygon)
            overlaps: list[tuple[int, int, float]] = []
            class_areas: dict[str, float] = {}
            valid_area = 0.0
            for row in range(row_count):
                for column in range(column_count):
                    pixel_x_min = x_min + column * cell_width
                    pixel_y_min = y_min + row * cell_height
                    overlap_area = _polygon_area(
                        _clip_to_rectangle(
                            polygon,
                            pixel_x_min,
                            pixel_y_min,
                            pixel_x_min + cell_width,
                            pixel_y_min + cell_height,
                        )
                    )
                    if overlap_area <= 0.0:
                        continue
                    overlaps.append((row, column, overlap_area))
                    value = values[row][column]
                    if value is None or value == nodata:
                        continue
                    label = str(value)
                    class_areas[label] = class_areas.get(label, 0.0) + overlap_area
                    valid_area += overlap_area
            fractions = {
                label: class_areas[label] / valid_area
                for label in sorted(class_areas)
            } if valid_area else {}
            majority = min(
                fractions,
                key=lambda label: (-fractions[label], label),
                default=None,
            )
            overlap_by_cell.append(overlaps)
            cell_class_fractions.append(fractions)
            cell_majorities.append(majority)
            coverage.append(
                min(1.0, max(0.0, valid_area / triangle_area))
                if triangle_area
                else 0.0
            )

        comparison_fractions: list[list[dict[str, float]]] = []
        comparison_values: list[list[str | None]] = []
        for row in range(row_count):
            fraction_row: list[dict[str, float]] = []
            value_row: list[str | None] = []
            for column in range(column_count):
                source_value = values[row][column]
                if source_value is None or source_value == nodata:
                    fraction_row.append({})
                    value_row.append(None)
                    continue
                class_areas: dict[str, float] = {}
                covered_area = 0.0
                for cell_index, overlaps in enumerate(overlap_by_cell):
                    overlap_area = sum(
                        area
                        for overlap_row, overlap_column, area in overlaps
                        if overlap_row == row and overlap_column == column
                    )
                    if overlap_area <= 0.0:
                        continue
                    for label, fraction in cell_class_fractions[cell_index].items():
                        class_areas[label] = class_areas.get(label, 0.0) + fraction * overlap_area
                    covered_area += overlap_area
                fractions = {
                    label: class_areas[label] / covered_area
                    for label in sorted(class_areas)
                } if covered_area else {}
                majority = min(
                    fractions,
                    key=lambda label: (-fractions[label], label),
                    default=None,
                )
                fraction_row.append(fractions)
                value_row.append(majority)
            comparison_fractions.append(fraction_row)
            comparison_values.append(value_row)

        return {
            "schema_version": "semantic-remapping-v0",
            "name": source["name"],
            "quantity_type": quantity_type,
            "units": source["units"],
            "source_path": str(raster_path),
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "cell_class_fractions": cell_class_fractions,
            "cell_majority_classes": cell_majorities,
            "cell_coverage_fractions": coverage,
            "comparison_raster": {
                "grid": {
                    "x_min": x_min,
                    "y_min": y_min,
                    "cell_width": cell_width,
                    "cell_height": cell_height,
                    "row_order": "bottom_to_top",
                },
                "class_fractions": comparison_fractions,
                "values": comparison_values,
            },
        }

    overlap_by_cell: list[list[tuple[int, int, float]]] = []
    cell_values: list[float | None] = []
    coverage: list[float] = []
    for triangle in triangles:
        polygon = [(vertices[index][0], vertices[index][1]) for index in triangle]
        triangle_area = _polygon_area(polygon)
        overlaps: list[tuple[int, int, float]] = []
        weighted_total = 0.0
        valid_area = 0.0
        for row in range(row_count):
            for column in range(column_count):
                pixel_x_min = x_min + column * cell_width
                pixel_y_min = y_min + row * cell_height
                overlap_area = _polygon_area(
                    _clip_to_rectangle(
                        polygon,
                        pixel_x_min,
                        pixel_y_min,
                        pixel_x_min + cell_width,
                        pixel_y_min + cell_height,
                    )
                )
                if overlap_area <= 0.0:
                    continue
                overlaps.append((row, column, overlap_area))
                value = values[row][column]
                if value is None or value == nodata:
                    continue
                if quantity_type == "intensive":
                    weighted_total += float(value) * overlap_area
                else:
                    weighted_total += float(value) * overlap_area / pixel_area
                valid_area += overlap_area
        overlap_by_cell.append(overlaps)
        if quantity_type == "intensive":
            cell_values.append(weighted_total / valid_area if valid_area else None)
        else:
            cell_values.append(weighted_total if valid_area else None)
        coverage.append(
            min(1.0, max(0.0, valid_area / triangle_area))
            if triangle_area
            else 0.0
        )

    comparison_values: list[list[float | None]] = []
    for row in range(row_count):
        output_row: list[float | None] = []
        for column in range(column_count):
            source_value = values[row][column]
            if source_value is None or source_value == nodata:
                output_row.append(None)
                continue
            weighted_total = 0.0
            covered_area = 0.0
            for cell_index, overlaps in enumerate(overlap_by_cell):
                if cell_values[cell_index] is None:
                    continue
                overlap_area = sum(
                    area
                    for overlap_row, overlap_column, area in overlaps
                    if overlap_row == row and overlap_column == column
                )
                if quantity_type == "intensive":
                    weighted_total += float(cell_values[cell_index]) * overlap_area
                else:
                    valid_cell_area = sum(
                        area
                        for overlap_row, overlap_column, area in overlaps
                        if values[overlap_row][overlap_column] is not None
                        and values[overlap_row][overlap_column] != nodata
                    )
                    weighted_total += (
                        float(cell_values[cell_index]) * overlap_area / valid_cell_area
                    )
                covered_area += overlap_area
            if quantity_type == "intensive":
                output_row.append(weighted_total / covered_area if covered_area else None)
            else:
                output_row.append(weighted_total if covered_area else None)
        comparison_values.append(output_row)

    result = {
        "schema_version": "semantic-remapping-v0",
        "name": source["name"],
        "quantity_type": source["quantity_type"],
        "units": source["units"],
        "source_path": str(raster_path),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "cell_values": cell_values,
        "cell_coverage_fractions": coverage,
        "comparison_raster": {
            "grid": {
                "x_min": x_min,
                "y_min": y_min,
                "cell_width": cell_width,
                "cell_height": cell_height,
                "row_order": "bottom_to_top",
            },
            "values": comparison_values,
        },
    }
    if quantity_type == "extensive":
        source_total = 0.0
        for row in range(row_count):
            for column in range(column_count):
                value = values[row][column]
                if value is None or value == nodata:
                    continue
                covered_area = sum(
                    area
                    for overlaps in overlap_by_cell
                    for overlap_row, overlap_column, area in overlaps
                    if overlap_row == row and overlap_column == column
                )
                source_total += float(value) * min(1.0, covered_area / pixel_area)
        mesh_total = sum(float(value) for value in cell_values if value is not None)
        comparison_total = sum(
            float(value)
            for row in comparison_values
            for value in row
            if value is not None
        )
        result["conservation"] = {
            "source_total": source_total,
            "mesh_total": mesh_total,
            "comparison_total": comparison_total,
            "residual": max(
                abs(mesh_total - source_total),
                abs(comparison_total - source_total),
            ),
        }
    return result
