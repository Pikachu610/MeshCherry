"""Bounded geospatial exports for accepted mesh cells and their attributes."""
from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
import sqlite3
import struct
import zipfile

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import transform_geom
from shapely.geometry import Polygon, mapping
from shapely import to_wkb

from .extraction import Cell, _cells
from .spatial import metric_crs
from .workload_limits import MAX_MESH_CELLS

DEFAULT_MAX_PIXELS = 1_000_000
DEFAULT_NODATA = -9999


def _normalize_cells(cells, crs):
    """Accept the retained JSON schema or its cells list, preserving IDs/order."""
    if isinstance(cells, dict):
        rows = cells.get("cells")
        embedded = cells.get("crs_wkt") or cells.get("crs")
        if embedded is not None and CRS.from_user_input(embedded) != CRS.from_user_input(crs):
            raise ValueError("Export CRS does not match accepted cell CRS")
        if cells.get("coordinate_units", "m") != "m":
            raise ValueError("Accepted cells must use metre coordinates")
    else:
        rows = cells
    if not isinstance(rows, list) or not 0 < len(rows) <= MAX_MESH_CELLS:
        raise ValueError("Export requires 1 to 100000 accepted cells")
    parsed = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"cell_id", "kind", "xy"}:
            raise ValueError("Export cell record must contain cell_id, kind, and xy")
        if not isinstance(row["xy"], (list, tuple)):
            raise ValueError("Cell coordinates must be arrays")
        xy = tuple(tuple(point) for point in row["xy"])
        parsed.append(Cell(row["cell_id"], row["kind"], xy))
    _cells(tuple(parsed))
    target = metric_crs(crs)
    polygons = [Polygon(cell.xy) for cell in parsed]
    return tuple(parsed), polygons, target


def _attribute_rows(attributes, cells):
    if attributes is None:
        return [{} for _ in cells]
    if isinstance(attributes, dict):
        rows = attributes.get("cells")
        if attributes.get("schema_version") not in {
                "cell-attributes-reference-v0", "cell-attributes-indexed-v1"}:
            raise ValueError("Unsupported attribute result schema")
    else:
        rows = attributes
    if not isinstance(rows, list) or len(rows) != len(cells):
        raise ValueError("Attributes must contain exactly one record per cell")
    indexed = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("cell_id"), str):
            raise ValueError("Attribute records require string cell_id")
        if row["cell_id"] in indexed:
            raise ValueError("Duplicate attribute cell_id")
        indexed[row["cell_id"]] = row
    expected = {cell.cell_id for cell in cells}
    if set(indexed) != expected:
        raise ValueError("Attribute IDs must exactly match accepted cell IDs")
    return [indexed[cell.cell_id] for cell in cells]


def _write_geojson(path, cells, polygons, rows, crs):
    features = []
    for cell, polygon, attributes in zip(cells, polygons, rows):
        geom = transform_geom(crs, CRS.from_epsg(4326), mapping(polygon), precision=-1)
        props = {"cell_id": cell.cell_id, "kind": cell.kind}
        props.update({key: value for key, value in attributes.items() if key != "cell_id"})
        features.append({"type": "Feature", "geometry": geom, "properties": props})
    document = {"type": "FeatureCollection", "features": features}
    Path(path).write_text(json.dumps(document, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":")) + "\n", encoding="utf-8")


def _quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def _gpkg_blob(polygon, srs_id):
    minx, miny, maxx, maxy = polygon.bounds
    # GeoPackageBinary header: magic, version, little-endian + XY envelope, SRS.
    header = b"GP" + bytes((0, 3)) + struct.pack("<i4d", srs_id, minx, maxx, miny, maxy)
    return header + to_wkb(polygon, byte_order=1, output_dimension=2)


def _write_gpkg(path, cells, polygons, rows, crs):
    path = Path(path)
    if path.exists():
        path.unlink()
    auth = crs.to_epsg()
    srs_id = auth if auth and auth > 0 else 990001
    org, org_id = ("EPSG", auth) if auth and auth > 0 else ("NONE", srs_id)
    table = "cells"
    fields = []
    seen = {"fid", "geom", "cell_id", "kind"}
    for row in rows:
        for key in row:
            if key != "cell_id" and key not in seen:
                if not isinstance(key, str) or not key:
                    raise ValueError("GeoPackage attribute names must be nonempty strings")
                fields.append(key)
                seen.add(key)
    # SQLite's dynamic typing preserves 64-bit integer category codes and NULL.
    columns = ["fid INTEGER PRIMARY KEY AUTOINCREMENT", "geom BLOB NOT NULL",
               "cell_id TEXT NOT NULL UNIQUE", "kind TEXT NOT NULL"]
    columns.extend(f"{_quote_identifier(name)}" for name in fields)
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA application_id=1196444487")  # 0x47504B47, GPKG
        con.execute("PRAGMA user_version=10300")
        con.executescript("""
            CREATE TABLE gpkg_spatial_ref_sys (
              srs_name TEXT NOT NULL, srs_id INTEGER NOT NULL PRIMARY KEY,
              organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL,
              definition TEXT NOT NULL, description TEXT);
            CREATE TABLE gpkg_contents (
              table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL,
              identifier TEXT UNIQUE, description TEXT DEFAULT '',
              last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
              min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE,
              srs_id INTEGER, FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id));
            CREATE TABLE gpkg_geometry_columns (
              table_name TEXT NOT NULL, column_name TEXT NOT NULL,
              geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL,
              z TINYINT NOT NULL, m TINYINT NOT NULL,
              PRIMARY KEY (table_name, column_name),
              FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id));
        """)
        con.execute("INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
                    (str(crs), srs_id, org, org_id, crs.to_wkt(version="WKT1_GDAL"), ""))
        con.execute("INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
                    ("Undefined Cartesian", -1, "NONE", -1, "undefined", ""))
        con.execute("INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
                    ("Undefined Geographic", 0, "NONE", 0, "undefined", ""))
        con.execute(f"CREATE TABLE {table} ({', '.join(columns)})")
        all_bounds = [polygon.bounds for polygon in polygons]
        bounds = (min(box[0] for box in all_bounds), min(box[1] for box in all_bounds),
                  max(box[2] for box in all_bounds), max(box[3] for box in all_bounds))
        con.execute("INSERT INTO gpkg_contents(table_name,data_type,identifier,min_x,min_y,max_x,max_y,srs_id) VALUES(?,?,?,?,?,?,?,?)",
                    (table, "features", "MeshCherry accepted cells", *bounds, srs_id))
        con.execute("INSERT INTO gpkg_geometry_columns VALUES(?,?,?,?,?,?)",
                    (table, "geom", "POLYGON", srs_id, 0, 0))
        names = ["geom", "cell_id", "kind", *fields]
        insert = f"INSERT INTO {table} ({','.join(_quote_identifier(n) for n in names)}) VALUES ({','.join('?' for _ in names)})"
        for cell, polygon, row in zip(cells, polygons, rows):
            values = [_gpkg_blob(polygon, srs_id), cell.cell_id, cell.kind]
            for name in fields:
                value = row.get(name)
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
                elif value is not None and type(value) not in (str, int, float, bool, bytes):
                    raise ValueError(f"Unsupported GeoPackage attribute type for {name}")
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError("GeoPackage attributes must be finite")
                values.append(int(value) if type(value) is bool else value)
            con.execute(insert, values)
        con.commit()
    except Exception:
        con.close()
        path.unlink(missing_ok=True)
        raise
    finally:
        try:
            con.close()
        except Exception:
            pass


def _raster_data(cells, polygons, rows, crs, field, resolution, nodata, max_pixels,
                 semantics=None):
    if isinstance(resolution, bool) or not isinstance(resolution, (int, float)) or not math.isfinite(resolution) or resolution <= 0:
        raise ValueError("Raster exports require a finite positive metric resolution")
    if type(max_pixels) is not int or max_pixels <= 0:
        raise ValueError("max_pixels must be a positive integer")
    if not isinstance(field, str) or not field:
        raise ValueError("Choose a numeric cell attribute field")
    if semantics == "extensive":
        raise ValueError("Extensive cell totals cannot be painted per pixel without a conservation-preserving allocation")
    # Categorical output can only be rasterized when each integer code remains exact.
    # String category values are rejected; numeric int64 categories remain exact.
    values = []
    integer_only = True
    for row in rows:
        if field not in row:
            raise ValueError(f"Attribute field {field!r} is unavailable")
        value = row.get(field)
        if value is None:
            values.append(None)
        elif type(value) is int:
            if not -(2**63) <= value < 2**63:
                raise ValueError("Integer attribute exceeds Int64 raster range")
            values.append(value)
        elif type(value) is float and math.isfinite(value):
            if semantics == "categorical":
                if not value.is_integer() or abs(value) > 2**53:
                    raise ValueError("Categorical raster values must be exact integer codes")
                values.append(int(value))
            else:
                integer_only = False
                values.append(value)
        else:
            raise ValueError("Raster attribute must be finite numeric data; categories must be numeric")
    if integer_only:
        dtype = "int64"
        fill = int(nodata)
        if type(nodata) not in (int, float) or not math.isfinite(nodata) or int(nodata) != nodata or not -(2**63) <= int(nodata) < 2**63:
            raise ValueError("Integer rasters require an Int64 nodata sentinel")
        if fill in [v for v in values if v is not None]:
            raise ValueError("Nodata sentinel collides with a cell value")
    else:
        dtype = "float64"
        if type(nodata) not in (int, float) or not math.isfinite(nodata):
            raise ValueError("Float rasters require a finite nodata value")
        fill = float(nodata)
        if fill in [v for v in values if v is not None]:
            raise ValueError("Nodata sentinel collides with a cell value")
    minx = min(poly.bounds[0] for poly in polygons)
    miny = min(poly.bounds[1] for poly in polygons)
    maxx = max(poly.bounds[2] for poly in polygons)
    maxy = max(poly.bounds[3] for poly in polygons)
    width = max(1, math.ceil((maxx-minx)/resolution))
    height = max(1, math.ceil((maxy-miny)/resolution))
    if width * height > max_pixels:
        raise ValueError("Rasterization exceeds the pixel budget")
    transform = from_origin(minx, maxy, resolution, resolution)
    shapes = [(poly, value) for poly, value in zip(polygons, values) if value is not None]
    array = rasterize(shapes, out_shape=(height, width), transform=transform,
                      fill=fill, dtype=dtype, all_touched=False)
    return array, transform, dtype, fill, width, height


def _write_geotiff(path, data, transform, dtype, nodata, crs):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=data.shape[1], height=data.shape[0],
                       count=1, dtype=dtype, crs=crs, transform=transform,
                       nodata=nodata, compress="deflate") as dst:
        dst.write(data, 1)


def _ascii_bytes(data, transform, nodata):
    height, width = data.shape
    cellsize = transform.a
    xll = transform.c
    yll = transform.f - height * cellsize
    lines = [f"ncols {width}", f"nrows {height}", f"xllcorner {xll:.15g}",
             f"yllcorner {yll:.15g}", f"cellsize {cellsize:.15g}",
             f"NODATA_value {nodata:.15g}"]
    lines.extend(" ".join(str(int(v)) if np.issubdtype(data.dtype, np.integer)
                           else f"{float(v):.15g}" for v in row) for row in data)
    return ("\n".join(lines) + "\n").encode("ascii")


def _write_ascii_zip(path, data, transform, nodata, crs):
    asc_name = "meshcherry.asc"
    prj_name = "meshcherry.prj"
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(asc_name, _ascii_bytes(data, transform, nodata))
        archive.writestr(prj_name, crs.to_wkt(version="WKT1_GDAL") + "\n")


def _write_csv(path, cells, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(["cell_id", "kind", *[key for row in rows for key in row if key != "cell_id"]]))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for cell, row in zip(cells, rows):
            encoded = {key: json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
                       if isinstance(value, (dict, list)) else value for key, value in row.items()}
            writer.writerow({"cell_id": cell.cell_id, "kind": cell.kind, **encoded})


def export_cells(path, format, cells, attributes, *, crs, field="value", resolution=None,
                 nodata=DEFAULT_NODATA, max_pixels=DEFAULT_MAX_PIXELS):
    """Export accepted polygons and attributes; raster formats need explicit resolution.

    `asc` produces a ZIP with an ESRI ASCII grid and matching `.prj` sidecar.
    `tif`/`geotiff` produces a one-band GeoTIFF. Rasterization uses cell-center
    inclusion, preserves NULL as nodata, and rejects categorical strings.
    """
    cells, polygons, target = _normalize_cells(cells, crs)
    rows = _attribute_rows(attributes, cells)
    format = format.lower().lstrip(".")
    if format in {"geojson", "json"}:
        _write_geojson(path, cells, polygons, rows, target)
        result_path = Path(path)
    elif format in {"gpkg", "geopackage"}:
        _write_gpkg(path, cells, polygons, rows, target)
        result_path = Path(path)
    elif format in {"csv"}:
        _write_csv(path, cells, rows)
        result_path = Path(path)
    elif format in {"tif", "tiff", "geotiff", "asc", "ascii", "ascii-grid"}:
        semantics = (attributes.get("provenance", {}).get("semantics")
                     if isinstance(attributes, dict)
                     and isinstance(attributes.get("provenance"), dict) else None)
        data, affine, dtype, sentinel, width, height = _raster_data(
            cells, polygons, rows, target, field, resolution, nodata, max_pixels,
            semantics=semantics)
        if format in {"asc", "ascii", "ascii-grid"}:
            _write_ascii_zip(path, data, affine, sentinel, target)
            result_path = Path(path)
        else:
            _write_geotiff(path, data, affine, dtype, sentinel, target)
            result_path = Path(path)
        return {"path": str(result_path), "format": format, "cell_count": len(cells),
                "attribute": field, "resolution_m": float(resolution),
                "width": width, "height": height, "nodata": sentinel}
    else:
        raise ValueError("Unsupported export format")
    return {"path": str(result_path), "format": format, "cell_count": len(cells)}
