from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import re
from pathlib import Path
import subprocess
import threading
import time
from typing import Any


class GmshRunError(RuntimeError):
    pass


def _distance_to_segment(
    x: float,
    y: float,
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    delta_x = end[0] - start[0]
    delta_y = end[1] - start[1]
    squared_length = delta_x**2 + delta_y**2
    if squared_length == 0:
        return math.hypot(x - start[0], y - start[1])
    fraction = max(
        0.0,
        min(
            1.0,
            ((x - start[0]) * delta_x + (y - start[1]) * delta_y)
            / squared_length,
        ),
    )
    nearest_x = start[0] + fraction * delta_x
    nearest_y = start[1] + fraction * delta_y
    return math.hypot(x - nearest_x, y - nearest_y)


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _peak_rss_bytes(process: subprocess.Popen[str]) -> int:
    if not hasattr(ctypes, "windll"):
        return 0
    query_information = 0x0400
    virtual_memory_read = 0x0010
    handle = ctypes.windll.kernel32.OpenProcess(
        query_information | virtual_memory_read, False, process.pid
    )
    if not handle:
        return 0
    try:
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        success = ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        )
        return int(counters.PeakWorkingSetSize) if success else 0
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _run_process(command: list[str]) -> tuple[subprocess.CompletedProcess[str], float, int]:
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    peak_rss = 0
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    def drain(stream: Any, destination: list[str]) -> None:
        for line in iter(stream.readline, ""):
            destination.append(line)
    stdout_thread = threading.Thread(target=drain, args=(process.stdout, stdout_parts), daemon=True)
    stderr_thread = threading.Thread(target=drain, args=(process.stderr, stderr_parts), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    while process.poll() is None:
        peak_rss = max(peak_rss, _peak_rss_bytes(process))
        time.sleep(0.005)
    stdout_thread.join()
    stderr_thread.join()
    stdout, stderr = "".join(stdout_parts), "".join(stderr_parts)
    if process.stdout:
        process.stdout.close()
    if process.stderr:
        process.stderr.close()
    peak_rss = max(peak_rss, _peak_rss_bytes(process))
    completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    return completed, time.perf_counter() - started, peak_rss


def _detect_version(executable: Path) -> str:
    result = subprocess.run(
        [str(executable), "-version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    output = f"{result.stdout}\n{result.stderr}"
    match = re.search(r"\b(\d+\.\d+(?:\.\d+)?)\b", output)
    if match:
        return match.group(1)
    match = re.search(r"gmsh-(\d+\.\d+(?:\.\d+)?)", str(executable), re.IGNORECASE)
    if match:
        return match.group(1)
    return "unknown"


def _geo_text(domain: dict[str, Any], parameters: dict[str, Any]) -> str:
    width = float(domain["width"])
    height = float(domain["height"])
    target_edge = float(parameters["target_edge"])
    algorithm = int(parameters["algorithm"])
    horizontal_nodes = max(2, math.ceil(width / target_edge) + 1)
    vertical_nodes = max(2, math.ceil(height / target_edge) + 1)

    points = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
    lines = [
        "// Synthetic inclined plane; coordinates and h use declared config units",
        f"h = {target_edge:.17g};",
        "Mesh.MeshSizeMin = h;",
        "Mesh.MeshSizeMax = h;",
        "Mesh.MeshSizeFromCurvature = 0;",
        "Mesh.MeshSizeExtendFromBoundary = 1;",
        "Mesh.MshFileVersion = 2.2;",
        f"Mesh.Algorithm = {algorithm};",
        "Field[1] = MathEval;",
        f'Field[1].F = "{target_edge:.17g}";',
        "Background Field = 1;",
    ]
    for point_id, (x, y) in enumerate(points, 1):
        lines.append(f"Point({point_id}) = {{{x:.17g}, {y:.17g}, 0, h}};")
    lines.extend(
        [
            "Line(1) = {1, 2};",
            "Line(2) = {2, 3};",
            "Line(3) = {3, 4};",
            "Line(4) = {4, 1};",
            f"Transfinite Curve {{1, 3}} = {horizontal_nodes} Using Progression 1;",
            f"Transfinite Curve {{2, 4}} = {vertical_nodes} Using Progression 1;",
            "Curve Loop(1) = {1, 2, 3, 4};",
            "Plane Surface(1) = {1};",
        ]
    )
    return "\n".join(lines) + "\n"


def _polygon_geo_text(
    ring: list[tuple[float, float]], parameters: dict[str, Any]
) -> str:
    target_edge = float(parameters["target_edge"])
    algorithm = int(parameters["algorithm"])
    if len(ring) < 3:
        raise GmshRunError("polygon boundary requires at least three vertices")
    signed_area = 0.5 * sum(
        ring[index][0] * ring[(index + 1) % len(ring)][1]
        - ring[(index + 1) % len(ring)][0] * ring[index][1]
        for index in range(len(ring))
    )
    oriented_ring = ring if signed_area > 0 else list(reversed(ring))
    lines = [
        "// Derived ROI boundary; canonical source is never written by Gmsh",
        f"h = {target_edge:.17g};",
        "Mesh.MeshSizeMin = h;",
        "Mesh.MeshSizeMax = h;",
        "Mesh.MeshSizeFromCurvature = 0;",
        "Mesh.MeshSizeExtendFromBoundary = 1;",
        "Mesh.MshFileVersion = 2.2;",
        f"Mesh.Algorithm = {algorithm};",
        "Field[1] = MathEval;",
        f'Field[1].F = "{target_edge:.17g}";',
        "Background Field = 1;",
    ]
    for point_id, (x, y) in enumerate(oriented_ring, 1):
        lines.append(f"Point({point_id}) = {{{x:.17g}, {y:.17g}, 0, h}};")
    for line_id in range(1, len(oriented_ring) + 1):
        next_point = line_id + 1 if line_id < len(oriented_ring) else 1
        lines.append(f"Line({line_id}) = {{{line_id}, {next_point}}};")
        start = oriented_ring[line_id - 1]
        end = oriented_ring[next_point - 1]
        nodes = max(2, math.ceil(math.hypot(end[0] - start[0], end[1] - start[1]) / target_edge) + 1)
        lines.append(f"Transfinite Curve {{{line_id}}} = {nodes} Using Progression 1;")
    line_ids = ", ".join(str(index) for index in range(1, len(oriented_ring) + 1))
    lines.extend([f"Curve Loop(1) = {{{line_ids}}};", "Plane Surface(1) = {1};"])
    return "\n".join(lines) + "\n"


def _adaptive_polygon_geo_text(
    ring: list[tuple[float, float]], parameters: dict[str, Any], background: Path
) -> str:
    """Planar ROI geometry driven only by a retained log-size POS view."""
    algorithm = int(parameters["algorithm"])
    signed_area = 0.5 * sum(
        ring[index][0] * ring[(index + 1) % len(ring)][1]
        - ring[(index + 1) % len(ring)][0] * ring[index][1]
        for index in range(len(ring))
    )
    oriented_ring = ring if signed_area > 0 else list(reversed(ring))
    background_text = str(background.resolve()).replace('\\', '/')
    lines = [
        "// SF-1 spatial background; geometry remains planar EPSG:32649 XY",
        "Mesh.MeshSizeMin = 250;",
        "Mesh.MeshSizeMax = 1000;",
        "Mesh.MeshSizeFromPoints = 0;",
        "Mesh.MeshSizeFromCurvature = 0;",
        "Mesh.MeshSizeExtendFromBoundary = 0;",
        "Mesh.MshFileVersion = 2.2;",
        f"Mesh.Algorithm = {algorithm};",
    ]
    for point_id, (x, y) in enumerate(oriented_ring, 1):
        lines.append(f"Point({point_id}) = {{{x:.17g}, {y:.17g}, 0, 1000}};")
    for line_id in range(1, len(oriented_ring) + 1):
        next_point = line_id + 1 if line_id < len(oriented_ring) else 1
        lines.append(f"Line({line_id}) = {{{line_id}, {next_point}}};")
    line_ids = ", ".join(str(index) for index in range(1, len(oriented_ring) + 1))
    lines.extend([f"Curve Loop(1) = {{{line_ids}}};", "Plane Surface(1) = {1};",
                  "Field[1] = Structured;", "Field[1].TextFormat = 1;",
                  f'Field[1].FileName = "{background_text}";',
                  "Field[1].SetOutsideValue = 1;", "Field[1].OutsideValue = 1000;",
                  "Background Field = 1;", "// adaptive-field-ready"])
    return "\n".join(lines) + "\n"


def _parse_msh2(path: Path) -> tuple[list[list[float]], list[list[int]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        nodes_start = lines.index("$Nodes")
        node_count = int(lines[nodes_start + 1])
        elements_start = lines.index("$Elements")
        element_count = int(lines[elements_start + 1])
    except (ValueError, IndexError) as error:
        raise GmshRunError("Gmsh output is not readable MSH2 ASCII") from error

    nodes: dict[int, list[float]] = {}
    for row in lines[nodes_start + 2 : nodes_start + 2 + node_count]:
        node_id, x, y, z = row.split()
        nodes[int(node_id)] = [float(x), float(y), float(z)]

    triangle_node_ids: list[list[int]] = []
    for row in lines[elements_start + 2 : elements_start + 2 + element_count]:
        fields = [int(value) for value in row.split()]
        if fields[1] != 2:
            continue
        tag_count = fields[2]
        triangle_node_ids.append(fields[3 + tag_count : 6 + tag_count])
    if not triangle_node_ids:
        raise GmshRunError("Gmsh output contains no triangular cells")

    used_node_ids = sorted({node for triangle in triangle_node_ids for node in triangle})
    node_index = {node_id: index for index, node_id in enumerate(used_node_ids)}
    vertices = [nodes[node_id] for node_id in used_node_ids]
    triangles = [
        [node_index[node_id] for node_id in triangle]
        for triangle in triangle_node_ids
    ]
    return vertices, triangles


def _assign_elevation(vertices: list[list[float]], domain: dict[str, Any]) -> None:
    kind = domain["kind"]
    base = float(domain["base_elevation"])
    if kind == "synthetic_inclined_plane":
        slope_x = float(domain["slope_x"])
        slope_y = float(domain["slope_y"])
        for vertex in vertices:
            vertex[2] = base + slope_x * vertex[0] + slope_y * vertex[1]
        return
    if kind == "synthetic_v_valley":
        center_x = float(domain["width"]) / 2
        downstream_slope = float(domain["downstream_slope"])
        cross_slope = float(domain["cross_slope"])
        for vertex in vertices:
            vertex[2] = (
                base
                + downstream_slope * vertex[1]
                + cross_slope * abs(vertex[0] - center_x)
            )
        return
    if kind == "synthetic_radial_bowl":
        center_x = float(domain["width"]) / 2
        center_y = float(domain["height"]) / 2
        curvature = float(domain["curvature"])
        for vertex in vertices:
            vertex[2] = base + curvature * (
                (vertex[0] - center_x) ** 2 + (vertex[1] - center_y) ** 2
            )
        return
    if kind == "synthetic_y_drainage":
        width = float(domain["width"])
        height = float(domain["height"])
        downstream_slope = float(domain["downstream_slope"])
        cross_slope = float(domain["cross_slope"])
        junction = (width / 2, height / 2)
        segments = [
            ((width / 2, 0.0), junction),
            (junction, (width / 4, height)),
            (junction, (3 * width / 4, height)),
        ]
        for vertex in vertices:
            channel_distance = min(
                _distance_to_segment(vertex[0], vertex[1], start, end)
                for start, end in segments
            )
            vertex[2] = (
                base
                + downstream_slope * vertex[1]
                + cross_slope * channel_distance
            )
        return
    raise GmshRunError(f"unsupported synthetic domain: {kind}")


def _domain_checks(
    vertices: list[list[float]], domain: dict[str, Any]
) -> list[dict[str, str]]:
    middle_y = float(domain["height"]) / 2
    center_x = float(domain["width"]) / 2

    def nearest(x: float, y: float) -> list[float]:
        return min(
            vertices,
            key=lambda vertex: (vertex[0] - x) ** 2 + (vertex[1] - y) ** 2,
        )

    if domain["kind"] == "synthetic_v_valley":
        left = nearest(0, middle_y)
        center = nearest(center_x, middle_y)
        right = nearest(float(domain["width"]), middle_y)
        passed = center[2] < left[2] and center[2] < right[2] and abs(left[2] - right[2]) <= 1e-9
        return [
            {
                "name": "v_valley_centerline_lower_than_sides",
                "status": "pass" if passed else "fail",
            }
        ]
    if domain["kind"] == "synthetic_radial_bowl":
        center = nearest(center_x, middle_y)
        corners = [
            nearest(0, 0),
            nearest(float(domain["width"]), 0),
            nearest(float(domain["width"]), float(domain["height"])),
            nearest(0, float(domain["height"])),
        ]
        corner_elevations = [corner[2] for corner in corners]
        passed = all(center[2] < elevation for elevation in corner_elevations) and (
            max(corner_elevations) - min(corner_elevations) <= 1e-9
        )
        return [
            {
                "name": "radial_bowl_center_lower_than_boundary",
                "status": "pass" if passed else "fail",
            }
        ]
    if domain["kind"] == "synthetic_y_drainage":
        width = float(domain["width"])
        height = float(domain["height"])
        base = float(domain["base_elevation"])
        downstream_slope = float(domain["downstream_slope"])
        cross_slope = float(domain["cross_slope"])
        junction = (width / 2, height / 2)
        segments = [
            ((width / 2, 0.0), junction),
            (junction, (width / 4, height)),
            (junction, (3 * width / 4, height)),
        ]

        def analytical_elevation(point: tuple[float, float]) -> float:
            channel_distance = min(
                _distance_to_segment(point[0], point[1], start, end)
                for start, end in segments
            )
            return base + downstream_slope * point[1] + cross_slope * channel_distance

        sample_pairs = [
            ((width / 2, height / 4), (width / 4, height / 4)),
            ((3 * width / 8, 3 * height / 4), (width / 8, 3 * height / 4)),
            ((5 * width / 8, 3 * height / 4), (7 * width / 8, 3 * height / 4)),
        ]
        passed = all(
            analytical_elevation(channel) < analytical_elevation(side)
            for channel, side in sample_pairs
        )
        return [
            {
                "name": "y_drainage_channels_lower_than_sides",
                "status": "pass" if passed else "fail",
            }
        ]
    return []


def generate_synthetic_domain(
    generator: dict[str, Any], domain: dict[str, Any], mesh_directory: Path
) -> tuple[
    list[list[float]],
    list[list[int]],
    dict[str, Any],
    dict[str, Any],
    list[dict[str, str]],
]:
    executable = Path(generator["executable"])
    if not executable.is_file():
        raise GmshRunError(f"Gmsh executable not found: {executable}")
    parameters = generator["parameters"]
    mesh_directory.mkdir(parents=True, exist_ok=True)
    geo_path = mesh_directory / "inclined-plane.geo"
    msh_path = mesh_directory / "inclined-plane.msh"
    log_path = mesh_directory / "gmsh.log"
    geo_path.write_text(_geo_text(domain, parameters), encoding="utf-8")
    command = [
        str(executable),
        str(geo_path),
        "-2",
        "-format",
        "msh2",
        "-clmin",
        str(float(parameters["target_edge"])),
        "-clmax",
        str(float(parameters["target_edge"])),
        "-o",
        str(msh_path),
        "-v",
        "3",
    ]
    completed, wall_time, peak_rss = _run_process(command)
    log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise GmshRunError(
            f"Gmsh exited with {completed.returncode}; see {log_path.name}"
        )
    vertices, triangles = _parse_msh2(msh_path)
    _assign_elevation(vertices, domain)
    effective_generator = {
        "name": "gmsh",
        "version": _detect_version(executable),
        "executable": str(executable),
        "parameters": parameters,
    }
    resources = {
        "wall_time_seconds": wall_time,
        "peak_rss_bytes": peak_rss,
    }
    return vertices, triangles, effective_generator, resources, _domain_checks(vertices, domain)


def generate_polygon_domain(
    generator: dict[str, Any],
    ring: list[tuple[float, float]],
    mesh_directory: Path,
) -> tuple[list[list[float]], list[list[int]], dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    executable = Path(generator["executable"])
    if not executable.is_file():
        raise GmshRunError(f"Gmsh executable not found: {executable}")
    parameters = generator["parameters"]
    mesh_directory.mkdir(parents=True, exist_ok=True)
    geo_path = mesh_directory / "domain.geo"
    msh_path = mesh_directory / "domain.msh"
    log_path = mesh_directory / "gmsh.log"
    geo_path.write_text(_polygon_geo_text(ring, parameters), encoding="utf-8")
    command = [
        str(executable), str(geo_path), "-2", "-format", "msh2",
        "-clmin", str(float(parameters["target_edge"])),
        "-clmax", str(float(parameters["target_edge"])),
        "-o", str(msh_path), "-v", "3",
    ]
    completed, wall_time, peak_rss = _run_process(command)
    log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        tail = (completed.stdout + completed.stderr)[-1000:].replace("\n", " | ")
        raise GmshRunError(f"Gmsh exited with {completed.returncode}; see {log_path.name}: {tail}")
    vertices, triangles = _parse_msh2(msh_path)
    effective_generator = {
        "name": "gmsh",
        "version": _detect_version(executable),
        "executable": str(executable),
        "parameters": parameters,
    }
    resources = {"wall_time_seconds": wall_time, "peak_rss_bytes": peak_rss}
    return vertices, triangles, effective_generator, resources, []


def generate_spatial_polygon_domain(
    generator: dict[str, Any], ring: list[tuple[float, float]],
    background: Path, mesh_directory: Path,
) -> tuple[list[list[float]], list[list[int]], dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    executable = Path(generator["executable"])
    if not executable.is_file() or not Path(background).is_file():
        raise GmshRunError("Gmsh executable or spatial background not found")
    parameters = generator["parameters"]
    mesh_directory.mkdir(parents=True, exist_ok=True)
    geo_path, msh_path, log_path = mesh_directory / "domain.geo", mesh_directory / "domain.msh", mesh_directory / "gmsh.log"
    geo_path.write_text(_adaptive_polygon_geo_text(ring, parameters, background), encoding="utf-8")
    command = [str(executable), str(geo_path), "-clmin", "250", "-clmax", "1000", "-2", "-format", "msh2",
               "-clscale", "1", "-o", str(msh_path), "-v", "5"]
    completed, wall_time, peak_rss = _run_process(command)
    log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        tail = (completed.stdout + completed.stderr)[-1000:].replace("\n", " | ")
        raise GmshRunError(f"Gmsh exited with {completed.returncode}; see {log_path.name}: {tail}")
    vertices, triangles = _parse_msh2(msh_path)
    effective_generator = {"name": "gmsh", "version": _detect_version(executable),
                           "executable": str(executable), "parameters": parameters,
                           "background": str(Path(background).resolve()), "command": command}
    return vertices, triangles, effective_generator, {"wall_time_seconds": wall_time, "peak_rss_bytes": peak_rss}, []
