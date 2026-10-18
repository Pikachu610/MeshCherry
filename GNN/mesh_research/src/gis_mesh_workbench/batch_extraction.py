"""Sequential raster extraction joined to one immutable accepted cell set.

The SQLite file is the durable result. Raster reads reuse the indexed extractor
one feature at a time, so memory does not grow with the number of input rasters.
"""
from __future__ import annotations

import csv
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid

from rasterio.crs import CRS

from .extraction import Cell, _cells
from .indexed_extraction import extract_indexed
from .job_control import Cancelled, checkpoint, observe
from .mesh_workflow import geometry_digest
from .workload_limits import MAX_MESH_CELLS

MAX_FEATURES = 500
MAX_FEATURE_CELL_ROWS = 10_000_000
MAX_DATABASE_BYTES = 2 * 1024 * 1024 * 1024
MAX_EXPORT_BYTES = 2 * 1024 * 1024 * 1024
MAX_FEATURE_PAIRS = 5_000_000
MAX_AGGREGATE_PAIRS = 2_500_000_000
MAX_PREVIEW_COLUMNS = 20
MAX_PREVIEW_LIMIT = 200
_RESERVED_FEATURE_NAMES = {'cell_id', 'id', 'kind', 'area', 'area_m2',
                          'centroid_x', 'centroid_y'}

_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE batch (
  id TEXT PRIMARY KEY, geometry_digest TEXT NOT NULL, cell_crs TEXT NOT NULL,
  analysis_crs TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
  feature_count INTEGER NOT NULL, cell_count INTEGER NOT NULL,
  aggregate_candidate_pairs INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE cells (
  cell_id TEXT PRIMARY KEY, kind TEXT NOT NULL, area_m2 REAL NOT NULL,
  centroid_x REAL NOT NULL, centroid_y REAL NOT NULL
);
CREATE TABLE features (
  position INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, path TEXT NOT NULL,
  band INTEGER NOT NULL, semantics TEXT NOT NULL, value_units TEXT NOT NULL,
  status TEXT NOT NULL, error TEXT, provenance_json TEXT, quality_json TEXT
);
CREATE TABLE feature_cells (
  name TEXT NOT NULL REFERENCES features(name),
  cell_id TEXT NOT NULL REFERENCES cells(cell_id), value_json TEXT,
  PRIMARY KEY(name, cell_id)
);
CREATE INDEX feature_cells_cell ON feature_cells(cell_id, name);
"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _load(db_path):
    path = Path(db_path)
    if not path.is_file():
        raise ValueError('batch database not found')
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def _validate_specs(specs):
    if not isinstance(specs, (list, tuple)) or not 1 <= len(specs) <= MAX_FEATURES:
        raise ValueError('batch must contain from 1 to 500 feature specs')
    names = set()
    cleaned = []
    for spec in specs:
        if not isinstance(spec, dict):
            raise ValueError('feature spec must be an object')
        name, path, band = spec.get('name'), spec.get('path'), spec.get('band')
        semantics, units = spec.get('semantics'), spec.get('value_units')
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError('feature name must be nonempty and at most 200 characters')
        key = name.casefold()
        if key in _RESERVED_FEATURE_NAMES:
            raise ValueError('feature name conflicts with a reserved cell column')
        if key in names:
            raise ValueError('feature names must be unique')
        names.add(key)
        if not isinstance(path, (str, os.PathLike)) or not str(path):
            raise ValueError('feature raster path is required')
        if type(band) is not int or band < 1:
            raise ValueError('feature band must be a positive integer')
        if semantics not in {'intensive', 'extensive', 'categorical'}:
            raise ValueError('unsupported feature semantics')
        if not isinstance(units, str) or not units.strip() or len(units) > 200:
            raise ValueError('feature value units are required')
        cleaned.append(dict(name=name, path=str(Path(path)), band=band,
                            semantics=semantics, value_units=units))
    return cleaned


def _notify(callback, **event):
    if callback is not None:
        callback(event)


def run_batch(db_path, cells, specs, *, cell_crs, analysis_crs,
              accepted_geometry_digest, hard_limits=None,
              max_aggregate_candidate_pairs=MAX_AGGREGATE_PAIRS, progress=None):
    """Create a fresh durable batch; each feature commits independently.

    `Cancelled` is re-raised only after active and queued statuses are persisted.
    A failed feature does not prevent subsequent feature specs from running.
    """
    path = Path(db_path)
    if path.exists():
        raise ValueError('batch database path already exists')
    if not 0 < len(cells) <= MAX_MESH_CELLS:
        raise ValueError('indexed cell limit exceeded')
    if not isinstance(specs, (list, tuple)) or len(cells) * len(specs) > MAX_FEATURE_CELL_ROWS:
        raise ValueError('batch feature-cell row limit exceeded')
    if type(max_aggregate_candidate_pairs) is not int or not 1 <= max_aggregate_candidate_pairs <= MAX_AGGREGATE_PAIRS:
        raise ValueError('aggregate candidate-pair limit cannot exceed hard ceiling')
    if cell_crs is None or CRS.from_user_input(cell_crs) != CRS.from_user_input(analysis_crs):
        raise ValueError('cells must explicitly use analysis CRS; relabeling forbidden')
    specs = _validate_specs(specs)
    identity = geometry_digest(cells, cell_crs)
    if identity != accepted_geometry_digest:
        raise ValueError('accepted geometry digest mismatch')
    polygons = _cells(cells)
    if len({cell.cell_id for cell in cells}) != len(cells):
        raise ValueError('cell IDs must be unique')

    path.parent.mkdir(parents=True, exist_ok=True)
    batch_id = uuid.uuid4().hex
    db = sqlite3.connect(path)
    aggregate_pairs = 0
    current_name = None
    try:
        db.executescript(_SCHEMA)
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        page_size = db.execute('PRAGMA page_size').fetchone()[0]
        db.execute(f'PRAGMA max_page_count={MAX_DATABASE_BYTES // page_size}')
        db.execute('INSERT INTO batch(id,geometry_digest,cell_crs,analysis_crs,status,created_at,feature_count,cell_count) '
                   'VALUES(?,?,?,?,?,?,?,?)',
                   (batch_id, identity, str(cell_crs), str(analysis_crs), 'running',
                    __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                    len(specs), len(cells)))
        for position, spec in enumerate(specs):
            db.execute('INSERT INTO features(position,name,path,band,semantics,value_units,status) '
                       'VALUES(?,?,?,?,?,?,?)',
                       (position, spec['name'], spec['path'], spec['band'], spec['semantics'],
                        spec['value_units'], 'queued'))
        cell_rows = []
        for cell, polygon in zip(cells, polygons):
            centroid = polygon.centroid
            if not all(math.isfinite(v) for v in (polygon.area, centroid.x, centroid.y)):
                raise ValueError('cell geometry has nonfinite area or centroid')
            cell_rows.append((cell.cell_id, cell.kind, float(polygon.area),
                              float(centroid.x), float(centroid.y)))
        db.executemany('INSERT INTO cells VALUES(?,?,?,?,?)', cell_rows)
        db.commit()

        for position, spec in enumerate(specs):
            current_name = spec['name']
            try:
                checkpoint()
                db.execute('UPDATE features SET status=? WHERE name=?', ('running', current_name))
                db.commit()
                _notify(progress, stage='feature_start', feature_index=position,
                        total=len(specs), name=current_name, status='running')
                observe('batch_extraction', position, len(specs))
                limits = dict(hard_limits or {})
                remaining_pairs = max_aggregate_candidate_pairs - aggregate_pairs
                if remaining_pairs < 1:
                    raise ValueError('aggregate candidate-pair limit exceeded')
                limits['max_candidate_pairs'] = min(limits.get('max_candidate_pairs', MAX_FEATURE_PAIRS),
                                                    MAX_FEATURE_PAIRS, remaining_pairs)
                result = extract_indexed(spec['path'], cells, cell_crs=cell_crs,
                    analysis_crs=analysis_crs, band=spec['band'],
                    semantics=spec['semantics'], value_units=spec['value_units'],
                    hard_limits=limits)
                evidence = result['qa']['engine_evidence']
                feature_pairs = int(evidence['candidate_pairs'])
                if aggregate_pairs + feature_pairs > max_aggregate_candidate_pairs:
                    raise ValueError('aggregate candidate-pair limit exceeded')
                values = [(current_name, rec['cell_id'], _json(rec))
                          for rec in result['cells']]
                no_data_cells = sum(rec['valid_area_m2'] <= 0 for rec in result['cells'])
                feature_status = 'complete' if no_data_cells < len(result['cells']) else 'no_data'
                quality = dict(result['qa'], no_data_cells=no_data_cells,
                               cell_count=len(result['cells']))
                db.executemany('INSERT INTO feature_cells VALUES(?,?,?)', values)
                db.execute('UPDATE features SET status=?,provenance_json=?,quality_json=? WHERE name=?',
                           (feature_status, _json(result['provenance']), _json(quality), current_name))
                aggregate_pairs += feature_pairs
                db.execute('UPDATE batch SET aggregate_candidate_pairs=? WHERE id=?',
                           (aggregate_pairs, batch_id))
                db.commit()
                _notify(progress, stage='feature_' + feature_status, feature_index=position,
                        total=len(specs), name=current_name, status=feature_status)
            except Cancelled:
                db.rollback()
                db.execute('UPDATE features SET status=?,error=? WHERE name=?',
                           ('cancelled', 'cancelled by job controller', current_name))
                db.execute("UPDATE features SET status='queued' WHERE status='running'")
                db.execute('UPDATE batch SET status=?,aggregate_candidate_pairs=? WHERE id=?',
                           ('cancelled', aggregate_pairs, batch_id))
                db.commit()
                _notify(progress, stage='batch_cancelled', feature_index=position,
                        total=len(specs), name=current_name, status='cancelled')
                raise
            except Exception as error:
                db.rollback()
                db.execute('UPDATE features SET status=?,error=? WHERE name=?',
                           ('failed', str(error)[:2000], current_name))
                db.commit()
                _notify(progress, stage='feature_failed', feature_index=position,
                        total=len(specs), name=current_name, status='failed', error=str(error)[:500])
        statuses = [row[0] for row in db.execute('SELECT status FROM features ORDER BY position')]
        final_status = 'complete' if all(s == 'complete' for s in statuses) else 'partial'
        db.execute('UPDATE batch SET status=?,aggregate_candidate_pairs=? WHERE id=?',
                   (final_status, aggregate_pairs, batch_id))
        db.commit()
        return inspect_batch(path)
    except BaseException:
        db.close()
        raise
    finally:
        if db:
            db.close()


def inspect_batch(db_path):
    """Return batch status and bounded per-feature metadata without cell rows."""
    with closing(_load(db_path)) as db:
        batch = db.execute('SELECT * FROM batch LIMIT 1').fetchone()
        if batch is None:
            raise ValueError('batch database has no batch record')
        features = []
        for row in db.execute('SELECT * FROM features ORDER BY position'):
            feature = {k: row[k] for k in ('position', 'name', 'path', 'band',
                       'semantics', 'value_units', 'status', 'error')}
            feature['provenance'] = json.loads(row['provenance_json']) if row['provenance_json'] else None
            feature['quality'] = json.loads(row['quality_json']) if row['quality_json'] else None
            features.append(feature)
        return dict(batch_id=batch['id'], geometry_digest=batch['geometry_digest'],
                    cell_crs=batch['cell_crs'], analysis_crs=batch['analysis_crs'],
                    status=batch['status'], feature_count=batch['feature_count'],
                    cell_count=batch['cell_count'],
                    aggregate_candidate_pairs=batch['aggregate_candidate_pairs'],
                    features=features)


def preview_batch(db_path, *, offset=0, limit=100, feature_names=None,
                  cell_id=None, cell_id_mode='exact'):
    if type(offset) is not int or offset < 0:
        raise ValueError('offset must be a nonnegative integer')
    if type(limit) is not int or not 1 <= limit <= MAX_PREVIEW_LIMIT:
        raise ValueError('preview limit must be from 1 to 200')
    with closing(_load(db_path)) as db:
        names = [row[0] for row in db.execute('SELECT name FROM features ORDER BY position')]
        if feature_names is not None:
            if not isinstance(feature_names, (list, tuple)) or len(feature_names) > MAX_PREVIEW_COLUMNS:
                raise ValueError('at most 20 preview feature columns may be selected')
            if len(set(feature_names)) != len(feature_names) or set(feature_names) - set(names):
                raise ValueError('unknown or duplicate preview feature name')
            names = list(feature_names)
        else:
            names = names[:MAX_PREVIEW_COLUMNS]
        if cell_id_mode not in {'exact', 'substring'}:
            raise ValueError('cell_id_mode must be exact or substring')
        where = ' WHERE cell_id=?' if cell_id is not None and cell_id_mode == 'exact' else (
                ' WHERE cell_id LIKE ? ESCAPE \'\\\'' if cell_id is not None else '')
        params = ((str(cell_id),) if cell_id is not None and cell_id_mode == 'exact' else
                  ('%' + str(cell_id).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%',)
                  if cell_id is not None else ())
        total = db.execute('SELECT COUNT(*) FROM cells' + where, params).fetchone()[0]
        query = 'SELECT * FROM cells' + where + ' ORDER BY cell_id LIMIT ? OFFSET ?'
        cells_page = list(db.execute(query, (*params, limit, offset)))
        page_values = {cell['cell_id']: {} for cell in cells_page}
        if cells_page and names:
            cell_marks = ','.join('?' for _ in cells_page)
            feature_marks = ','.join('?' for _ in names)
            values_query = (f'SELECT cell_id,name,value_json FROM feature_cells '
                f'WHERE cell_id IN ({cell_marks}) AND name IN ({feature_marks})')
            for value_row in db.execute(values_query,
                    (*[cell['cell_id'] for cell in cells_page], *names)):
                page_values[value_row['cell_id']][value_row['name']] = json.loads(value_row['value_json']).get('value')
        rows = [dict(cell_id=cell['cell_id'], kind=cell['kind'], area_m2=cell['area_m2'],
                     centroid_x=cell['centroid_x'], centroid_y=cell['centroid_y'],
                     values={name: page_values[cell['cell_id']].get(name) for name in names})
                for cell in cells_page]
        return dict(total_cells=total, offset=offset, limit=limit,
                    feature_names=names, rows=rows, batch=inspect_batch(db_path))


def _safe_csv(value):
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + value
    return value


def _atomic_export(destination, writer):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=destination.name + '.', suffix='.tmp', dir=destination.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        writer(temp)
        if temp.stat().st_size > MAX_EXPORT_BYTES:
            raise ValueError('export byte limit exceeded')
        temp.replace(destination)
        return str(destination)
    finally:
        temp.unlink(missing_ok=True)


def export_wide_csv(db_path, destination):
    with closing(_load(db_path)) as db:
        names = [r[0] for r in db.execute('SELECT name FROM features ORDER BY position')]
        def write(temp):
            with temp.open('w', encoding='utf-8-sig', newline='') as stream:
                out = csv.writer(stream)
                out.writerow(['cell_id', 'kind', 'area_m2', 'centroid_x', 'centroid_y', *_map(_safe_csv, names)])
                for cell, values in _joined_cells(db):
                    decoded = [values[name].get('value') if values[name] is not None else None
                               for name in names]
                    out.writerow([_safe_csv(cell[k]) if k in {'cell_id','kind'} else cell[k]
                                  for k in ('cell_id','kind','area_m2','centroid_x','centroid_y')]
                                 + [_safe_csv(v) for v in decoded])
                    if stream.tell() > MAX_EXPORT_BYTES: raise ValueError('export byte limit exceeded')
        return _atomic_export(destination, write)


def _map(func, values):
    return [func(value) for value in values]


def _joined_cells(db):
    """Stream one cell and its bounded feature values from a single ordered join."""
    cursor = db.execute('SELECT c.cell_id,c.kind,c.area_m2,c.centroid_x,c.centroid_y, '
        'f.name,fc.value_json FROM cells c CROSS JOIN features f '
        'LEFT JOIN feature_cells fc ON fc.cell_id=c.cell_id AND fc.name=f.name '
        'ORDER BY c.cell_id,f.position')
    cell_id = None
    cell = None
    values = {}
    for row in cursor:
        checkpoint()
        if cell_id is not None and row['cell_id'] != cell_id:
            yield cell, values
            values = {}
        if row['cell_id'] != cell_id:
            cell_id = row['cell_id']
            cell = {key: row[key] for key in
                    ('cell_id', 'kind', 'area_m2', 'centroid_x', 'centroid_y')}
        values[row['name']] = json.loads(row['value_json']) if row['value_json'] else None
    if cell_id is not None:
        yield cell, values


def export_long_csv(db_path, destination):
    with closing(_load(db_path)) as db:
        def write(temp):
            with temp.open('w', encoding='utf-8-sig', newline='') as stream:
                out = csv.writer(stream)
                out.writerow(['cell_id','kind','area_m2','centroid_x','centroid_y',
                              'feature_name','value','feature_status'])
                cursor = db.execute('SELECT c.*,f.name,f.status,fc.value_json FROM cells c '
                    'CROSS JOIN features f LEFT JOIN feature_cells fc ON fc.cell_id=c.cell_id AND fc.name=f.name '
                    'ORDER BY c.cell_id,f.position')
                for row in cursor:
                    checkpoint()
                    value = json.loads(row['value_json']).get('value') if row['value_json'] else None
                    out.writerow([_safe_csv(row[k]) if k in {'cell_id','kind'} else row[k]
                                  for k in ('cell_id','kind','area_m2','centroid_x','centroid_y')]
                                 + [_safe_csv(row['name']), _safe_csv(value), row['status']])
                    if stream.tell() > MAX_EXPORT_BYTES: raise ValueError('export byte limit exceeded')
        return _atomic_export(destination, write)


def export_json(db_path, destination):
    with closing(_load(db_path)) as db:
        info = inspect_batch(db_path)
        def write(temp):
            with temp.open('w', encoding='utf-8') as stream:
                stream.write('{"batch":')
                json.dump(info, stream, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
                stream.write(',"cells":[')
                first = True
                for cell, values in _joined_cells(db):
                    record = {**cell, 'values': values}
                    if not first: stream.write(',')
                    json.dump(record, stream, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
                    first = False
                    if stream.tell() > MAX_EXPORT_BYTES: raise ValueError('export byte limit exceeded')
                stream.write(']}')
        return _atomic_export(destination, write)
