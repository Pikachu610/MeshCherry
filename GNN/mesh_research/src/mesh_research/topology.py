from __future__ import annotations

from collections import defaultdict, deque
from math import acos, degrees, hypot
from typing import Any


class MeshValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _signed_area(vertices: list[list[float]], triangle: list[int]) -> float:
    a, b, c = (vertices[index] for index in triangle)
    return 0.5 * (
        (b[0] - a[0]) * (c[1] - a[1])
        - (c[0] - a[0]) * (b[1] - a[1])
    )


def build_cell_graph(
    vertices: list[list[float]], triangles: list[list[int]]
) -> tuple[list[list[int]], dict[str, Any], dict[str, Any]]:
    errors: list[str] = []
    normalized_triangles: list[list[int]] = []
    seen_cells: set[tuple[int, int, int]] = set()

    for cell_index, triangle in enumerate(triangles):
        if len(triangle) != 3 or len(set(triangle)) != 3:
            errors.append(f"cell {cell_index} is not a three-vertex triangle")
            continue
        if any(index < 0 or index >= len(vertices) for index in triangle):
            errors.append(f"cell {cell_index} references an absent vertex")
            continue
        area = _signed_area(vertices, triangle)
        if area == 0:
            errors.append(f"cell {cell_index} has non-positive area")
            continue
        if area < 0:
            errors.append(f"cell {cell_index} is inverted")
            continue
        normalized = list(triangle)
        identity = tuple(sorted(normalized))
        if identity in seen_cells:
            errors.append(f"cell {cell_index} duplicates another cell")
            continue
        seen_cells.add(identity)
        normalized_triangles.append(normalized)

    edge_cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    for cell_index, triangle in enumerate(normalized_triangles):
        for first, second in (
            (triangle[0], triangle[1]),
            (triangle[1], triangle[2]),
            (triangle[2], triangle[0]),
        ):
            edge_cells[tuple(sorted((first, second)))].append(cell_index)
    for edge, cells in edge_cells.items():
        if len(cells) > 2:
            errors.append(f"edge {edge} is non-manifold with {len(cells)} cells")

    if errors:
        raise MeshValidationError(errors)

    centroids: list[list[float]] = []
    areas: list[float] = []
    minimum_angles: list[float] = []
    aspect_ratios: list[float] = []
    all_cell_edge_lengths: list[float] = []
    for triangle in normalized_triangles:
        points = [vertices[index] for index in triangle]
        centroids.append(
            [sum(point[0] for point in points) / 3, sum(point[1] for point in points) / 3]
        )
        area = _signed_area(vertices, triangle)
        areas.append(area)
        edge_lengths = [
            hypot(
                points[(index + 1) % 3][0] - points[index][0],
                points[(index + 1) % 3][1] - points[index][1],
            )
            for index in range(3)
        ]
        all_cell_edge_lengths.extend(edge_lengths)
        angles = []
        for index in range(3):
            first = edge_lengths[index - 1]
            second = edge_lengths[index]
            opposite = edge_lengths[(index + 1) % 3]
            cosine = (first**2 + second**2 - opposite**2) / (2 * first * second)
            angles.append(degrees(acos(max(-1.0, min(1.0, cosine)))))
        minimum_angles.append(min(angles))
        longest_edge = max(edge_lengths)
        shortest_altitude = 2 * area / longest_edge
        aspect_ratios.append(longest_edge / shortest_altitude)

    boundary_cells = {
        cells[0] for cells in edge_cells.values() if len(cells) == 1
    }
    nodes = [
        {
            "cell_id": f"cell-{index}",
            "area": areas[index],
            "centroid": centroids[index],
            "boundary_relationship": (
                "TOUCHES_EXTERIOR" if index in boundary_cells else "INTERIOR"
            ),
        }
        for index in range(len(normalized_triangles))
    ]

    interfaces: list[dict[str, Any]] = []
    directed_edges: list[dict[str, str]] = []
    adjacency: dict[int, set[int]] = defaultdict(set)
    for interface_index, (edge, cells) in enumerate(sorted(edge_cells.items())):
        first_vertex, second_vertex = (vertices[index] for index in edge)
        interface_id = f"interface-{interface_index}"
        interior = len(cells) == 2
        interface: dict[str, Any] = {
            "interface_id": interface_id,
            "cell_ids": [f"cell-{index}" for index in cells],
            "vertex_ids": list(edge),
            "length": hypot(
                second_vertex[0] - first_vertex[0],
                second_vertex[1] - first_vertex[1],
            ),
            "geometry_class": (
                "INTERIOR_INTERFACE" if interior else "EXTERIOR_EDGE"
            ),
            "physical_boundary_status": "NOT_APPLICABLE" if interior else "UNVERIFIED",
        }
        if interior:
            first_cell, second_cell = cells
            interface["centroid_distance"] = hypot(
                centroids[second_cell][0] - centroids[first_cell][0],
                centroids[second_cell][1] - centroids[first_cell][1],
            )
            adjacency[first_cell].add(second_cell)
            adjacency[second_cell].add(first_cell)
            directed_edges.extend(
                [
                    {
                        "source": f"cell-{first_cell}",
                        "target": f"cell-{second_cell}",
                        "interface_id": interface_id,
                    },
                    {
                        "source": f"cell-{second_cell}",
                        "target": f"cell-{first_cell}",
                        "interface_id": interface_id,
                    },
                ]
            )
        else:
            interface["centroid_distance"] = None
        interfaces.append(interface)

    if normalized_triangles:
        visited = {0}
        queue = deque([0])
        while queue:
            current = queue.popleft()
            for neighbor in adjacency[current] - visited:
                visited.add(neighbor)
                queue.append(neighbor)
        if len(visited) != len(normalized_triangles):
            raise MeshValidationError(["cell graph is disconnected"])

    graph = {
        "schema_version": "mesh-graph-v0",
        "nodes": nodes,
        "interfaces": interfaces,
        "directed_edges": directed_edges,
    }
    def statistics(values: list[float]) -> dict[str, float]:
        return {
            "min": min(values),
            "max": max(values),
            "mean": sum(values) / len(values),
        }

    qa = {
        "schema_version": "mesh-qa-v0",
        "status": "pass",
        "hard_gates": [
            {"name": "positive_unique_triangles", "status": "pass"},
            {"name": "manifold_interfaces", "status": "pass"},
            {"name": "edge_only_adjacency", "status": "pass"},
            {"name": "connected_cell_graph", "status": "pass"},
        ],
        "metrics": {
            "cell_count": len(normalized_triangles),
            "physical_interface_count": len(interfaces),
            "directed_edge_count": len(directed_edges),
            "quality": {
                "area": statistics(areas),
                "edge_length": statistics(all_cell_edge_lengths),
                "minimum_angle_degrees": statistics(minimum_angles),
                "aspect_ratio": statistics(aspect_ratios),
                "aspect_ratio_definition": "longest_edge / shortest_altitude",
            },
        },
    }
    return normalized_triangles, graph, qa
