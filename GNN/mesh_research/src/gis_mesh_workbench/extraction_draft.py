"""Pure bounded draft planning for editing an extraction table before reading rasters.

Drafts are annotations over an already accepted cell set. They never create or
modify cells, geometry, or graph nodes. Raster values remain explicitly pending
until the batch extraction is run.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence

from .batch_extraction import MAX_FEATURES, MAX_PREVIEW_COLUMNS, MAX_PREVIEW_LIMIT
from .workload_limits import MAX_MESH_CELLS
from shapely.geometry import Polygon

MAX_DRAFT_BYTES = 1024 * 1024
MAX_DRAFT_ANNOTATIONS = 10_000
MAX_DRAFT_COLUMNS = MAX_FEATURES
MAX_DRAFT_MANUAL_ROWS = MAX_DRAFT_ANNOTATIONS
_RESERVED = {'cell_id', 'id', 'kind', 'area', 'area_m2', 'centroid_x',
             'centroid_y', 'row_kind'}
_IDENT = re.compile(r'^[A-Za-z0-9_-]{1,80}$')


def canonical_digest(value):
    """Hash canonical JSON, rejecting non-JSON and non-finite values."""
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError) as error:
        raise ValueError('Draft is not valid finite JSON') from error
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _prepared_plan_digest(plan):
    return canonical_digest({key: value for key, value in plan.items()
                             if key not in {'cells', 'source_cells', 'features',
                                            '_selected_cell_ids', 'plan_digest'}} | {
        'cell_ids': [cell.cell_id for cell in plan['cells']],
        'features': plan['features']})


def _bounded_json(value):
    try:
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False,
                             separators=(',', ':'))
    except (TypeError, ValueError) as error:
        raise ValueError('Draft is not valid finite JSON') from error
    if len(payload.encode('utf-8')) > MAX_DRAFT_BYTES:
        raise ValueError('Draft request exceeds 1 MiB')


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError('Column names must be nonempty text of at most 200 characters')
    result = value.strip()
    if result.casefold() in _RESERVED:
        raise ValueError('Column name conflicts with a reserved cell column')
    return result


def _validate_value(value, value_type):
    if value is None:
        return None
    if value_type == 'number':
        try:
            finite = math.isfinite(value) if isinstance(value, (int, float)) else False
        except (OverflowError, TypeError, ValueError):
            finite = False
        if isinstance(value, bool) or not finite:
            raise ValueError('Manual number values must be finite numbers or null')
        return value
    if value_type == 'text':
        if not isinstance(value, str) or len(value) > 10_000:
            raise ValueError('Manual text values must be text of at most 10000 characters or null')
        return value
    raise ValueError('Manual columns require type text or number')


def prepare_draft(mesh_job_id, geometry_digest, cells, features, draft):
    """Validate and normalize an editable draft against frozen accepted cells.

    `features` must use the original batch API raster schema. Draft raster
    columns reference those source names by ID and can rename/reorder/drop them.
    """
    if not isinstance(mesh_job_id, str) or not mesh_job_id or len(mesh_job_id) > 200:
        raise ValueError('Accepted mesh job ID required')
    if not isinstance(geometry_digest, str) or not re.fullmatch(r'[0-9a-f]{64}', geometry_digest):
        raise ValueError('Accepted geometry digest required')
    if not isinstance(cells, Sequence) or not 0 < len(cells) <= MAX_MESH_CELLS:
        raise ValueError('Accepted mesh cell limit exceeded')
    if not isinstance(features, (list, tuple)) or len(features) > MAX_FEATURES:
        raise ValueError('Raster feature limit exceeded')
    raster_by_name = {}
    for feature in features:
        if not isinstance(feature, Mapping) or not isinstance(feature.get('name'), str):
            raise ValueError('Invalid raster feature specification')
        key = feature['name']
        if key in raster_by_name:
            raise ValueError('Raster feature names must be unique')
        raster_by_name[key] = dict(feature)
    if draft is None:
        draft = dict(mesh_job_id=mesh_job_id, geometry_digest=geometry_digest,
            columns=[dict(id=f'raster_{index}', type='raster',
                          source_name=feature['name'], name=feature['name'])
                     for index, feature in enumerate(features)],
            rows=[], excluded_cell_ids=[], manual_rows=[])
    _bounded_json(draft)
    if not isinstance(draft, dict) or set(draft) - {
            'mesh_job_id', 'geometry_digest', 'columns', 'rows',
            'excluded_cell_ids', 'manual_rows'}:
        raise ValueError('Unsupported draft fields')
    if draft.get('mesh_job_id') != mesh_job_id or draft.get('geometry_digest') != geometry_digest:
        raise ValueError('Draft is bound to a different accepted mesh or geometry digest')
    cell_ids = [getattr(cell, 'cell_id', None) for cell in cells]
    if any(not isinstance(ident, str) or not ident for ident in cell_ids) or len(set(cell_ids)) != len(cell_ids):
        raise ValueError('Accepted cell IDs must be unique')
    known_ids = set(cell_ids)
    columns = draft.get('columns')
    if not isinstance(columns, list) or len(columns) > MAX_DRAFT_COLUMNS:
        raise ValueError('Draft column limit exceeded')
    normalized_columns = []
    seen_ids, seen_names = set(), set()
    for column in columns:
        if not isinstance(column, dict) or set(column) - {
                'id', 'type', 'name', 'value_type', 'source_name'}:
            raise ValueError('Invalid draft column')
        ident, kind = column.get('id'), column.get('type')
        if not isinstance(ident, str) or not _IDENT.fullmatch(ident) or ident in seen_ids:
            raise ValueError('Draft column IDs must be unique bounded identifiers')
        name = _name(column.get('name'))
        if name.casefold() in seen_names:
            raise ValueError('Draft column names must be unique')
        seen_ids.add(ident); seen_names.add(name.casefold())
        if kind == 'raster':
            source_name = column.get('source_name', ident)
            if not isinstance(source_name, str):
                raise ValueError('Draft raster source_name must be text')
            if source_name not in raster_by_name:
                raise ValueError('Draft references an unknown raster feature')
            normalized_columns.append(dict(id=ident, type='raster', name=name,
                                           source_name=source_name))
        elif kind == 'manual':
            value_type = column.get('value_type')
            if value_type not in {'text', 'number'}:
                raise ValueError('Manual columns require type text or number')
            normalized_columns.append(dict(id=ident, type='manual', name=name,
                                           value_type=value_type))
        else:
            raise ValueError('Draft column type must be raster or manual')
    if len(normalized_columns) > MAX_DRAFT_COLUMNS:
        raise ValueError('Draft column limit exceeded')
    if sum(1 for col in normalized_columns if col['type'] == 'raster') > MAX_FEATURES:
        raise ValueError('Raster feature limit exceeded')
    generated_names = {f"{col['id']}__{suffix}".casefold()
                       for col in normalized_columns if col['type'] == 'raster'
                       for suffix in ('state', 'status')}
    if seen_names & generated_names:
        raise ValueError('Column name conflicts with a generated raster state/status column')

    manual_cols = {col['id']: col for col in normalized_columns if col['type'] == 'manual'}
    rows = draft.get('rows', [])
    if not isinstance(rows, list) or len(rows) > MAX_DRAFT_ANNOTATIONS:
        raise ValueError('Manual cell annotation limit exceeded')
    overrides = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'cell_id', 'values'}:
            raise ValueError('Manual cell rows require cell_id and values')
        cell_id, values = row['cell_id'], row['values']
        if not isinstance(cell_id, str) or cell_id not in known_ids or cell_id in overrides or not isinstance(values, dict):
            raise ValueError('Unknown or duplicate cell ID in manual annotations')
        if set(values) - set(manual_cols):
            raise ValueError('Manual annotations reference an unknown or raster column')
        cleaned = {}
        for key, value in values.items():
            cleaned[key] = _validate_value(value, manual_cols[key]['value_type'])
        overrides[cell_id] = cleaned

    excluded = draft.get('excluded_cell_ids', [])
    if not isinstance(excluded, list) or len(excluded) > MAX_DRAFT_ANNOTATIONS:
        raise ValueError('Excluded cell limit exceeded')
    if any(not isinstance(cell_id, str) for cell_id in excluded):
        raise ValueError('Excluded cell IDs must be text')
    if len(set(excluded)) != len(excluded) or set(excluded) - known_ids:
        raise ValueError('Unknown or duplicate excluded cell ID')

    manual_rows = draft.get('manual_rows', [])
    if not isinstance(manual_rows, list) or len(manual_rows) > MAX_DRAFT_MANUAL_ROWS:
        raise ValueError('Manual row limit exceeded')
    if len(rows) + len(excluded) + len(manual_rows) > MAX_DRAFT_ANNOTATIONS:
        raise ValueError('Combined draft edits, exclusions, and manual rows exceed 10000')
    cleaned_manual_rows, row_ids = [], set()
    for row in manual_rows:
        if not isinstance(row, dict) or set(row) != {'row_id', 'values'}:
            raise ValueError('Manual rows require row_id and values')
        row_id, values = row['row_id'], row['values']
        if not isinstance(row_id, str) or not _IDENT.fullmatch(row_id) or row_id in row_ids:
            raise ValueError('Manual row IDs must be unique bounded identifiers')
        if not isinstance(values, dict) or set(values) - set(manual_cols):
            raise ValueError('Manual rows may only fill manual columns')
        row_ids.add(row_id)
        cleaned_manual_rows.append(dict(row_id=row_id, values={key: _validate_value(
            value, manual_cols[key]['value_type']) for key, value in values.items()}))

    normalized_features = []
    for col in normalized_columns:
        if col['type'] == 'raster':
            feature = dict(raster_by_name[col['source_name']])
            feature['name'] = col['name']
            normalized_features.append(feature)
    excluded_set = set(excluded)
    plan = dict(mesh_job_id=mesh_job_id, geometry_digest=geometry_digest,
                columns=normalized_columns, overrides=overrides,
                excluded_cell_ids=list(excluded), manual_rows=cleaned_manual_rows,
                features=normalized_features, source_cells=tuple(cells),
                _selected_cell_ids=frozenset(known_ids - excluded_set),
                cells=tuple(cell for cell in cells
                    if cell.cell_id not in excluded_set))
    plan['_source_cell_fingerprint'] = canonical_digest([
        (cell.cell_id, cell.kind, cell.xy) for cell in cells])
    plan['plan_digest'] = _prepared_plan_digest(plan)
    return plan


def freeze_draft(prepared, *, mesh_job_id, geometry_digest, cells):
    """Revalidate identity and emit immutable extraction/export instructions."""
    if not isinstance(prepared, dict) or prepared.get('mesh_job_id') != mesh_job_id or \
            prepared.get('geometry_digest') != geometry_digest:
        raise ValueError('Draft is stale or bound to a different accepted mesh')
    if canonical_digest([(cell.cell_id, cell.kind, cell.xy) for cell in cells]) != \
            prepared.get('_source_cell_fingerprint'):
        raise ValueError('Accepted mesh cell IDs or geometry changed after draft preparation')
    if _prepared_plan_digest(prepared) != prepared.get('plan_digest'):
        raise ValueError('Draft plan changed after it was prepared')
    excluded_set = set(prepared['excluded_cell_ids'])
    selected = tuple(cell for cell in cells if cell.cell_id not in excluded_set)
    if not selected:
        raise ValueError('Draft must include at least one accepted cell')
    return dict(mesh_job_id=mesh_job_id, parent_geometry_digest=geometry_digest,
                cells=selected, cell_ids=tuple(cell.cell_id for cell in selected),
                features=list(prepared['features']), columns=list(prepared['columns']),
                overrides=dict(prepared['overrides']), manual_rows=list(prepared['manual_rows']),
                excluded_cell_ids=list(prepared['excluded_cell_ids']),
                plan_digest=prepared['plan_digest'])


def canonical_wire_state(prepared):
    """Return the editable, stable draft representation safe to round-trip."""
    return dict(mesh_job_id=prepared['mesh_job_id'],
                geometry_digest=prepared['geometry_digest'],
                columns=[dict(column) for column in prepared['columns']],
                rows=[dict(cell_id=cell_id, values=dict(values))
                      for cell_id, values in prepared['overrides'].items()],
                excluded_cell_ids=list(prepared['excluded_cell_ids']),
                manual_rows=[dict(row_id=row['row_id'], values=dict(row['values']))
                             for row in prepared['manual_rows']])


def preview_draft(prepared, *, offset=0, limit=100, visible_columns=None):
    """Render a bounded pre-extraction page; raster cells use `not_computed`."""
    if type(offset) is not int or offset < 0:
        raise ValueError('offset must be a nonnegative integer')
    if type(limit) is not int or not 1 <= limit <= MAX_PREVIEW_LIMIT:
        raise ValueError('preview limit must be from 1 to 200')
    columns = prepared['columns']
    if visible_columns is not None:
        if not isinstance(visible_columns, list) or len(visible_columns) > MAX_PREVIEW_COLUMNS:
            raise ValueError('At most 20 visible columns may be selected')
        by_id = {column['id']: column for column in columns}
        if len(set(visible_columns)) != len(visible_columns) or set(visible_columns) - set(by_id):
            raise ValueError('Unknown or duplicate visible column')
        columns = [by_id[ident] for ident in visible_columns]
    else:
        columns = columns[:MAX_PREVIEW_COLUMNS]
    cells = prepared['source_cells']
    selected_ids = prepared['_selected_cell_ids']
    manual_rows = prepared['manual_rows']
    total_rows = len(cells) + len(manual_rows)
    stop = min(offset + limit, total_rows)
    rows = []
    for index in range(offset, min(stop, len(cells))):
        cell = cells[index]
        values = {}
        for col in columns:
            if col['type'] == 'raster':
                values[col['id']] = dict(state='not_computed')
            else:
                values[col['id']] = prepared['overrides'].get(cell.cell_id, {}).get(col['id'])
        polygon = Polygon(cell.xy)
        centroid = polygon.centroid
        rows.append(dict(row_kind='cell', cell_id=cell.cell_id, kind=cell.kind,
                         included=cell.cell_id in selected_ids,
                         area_m2=float(polygon.area), centroid_x=float(centroid.x),
                         centroid_y=float(centroid.y), values=values))
    manual_start = max(0, offset - len(cells))
    manual_count = max(0, stop - max(offset, len(cells)))
    for row in manual_rows[manual_start:manual_start + manual_count]:
        rows.append(dict(row_kind='manual', row_id=row['row_id'],
            values={col['id']: row['values'].get(col['id'])
                    for col in columns if col['type'] == 'manual'}))
    return dict(mesh_job_id=prepared['mesh_job_id'],
                geometry_digest=prepared['geometry_digest'], total_rows=total_rows,
                total_cells=len(selected_ids), accepted_cells=len(cells),
                selected_cells=len(selected_ids),
                offset=offset, limit=limit,
                columns=columns, rows=rows)
