"""Session-owned asynchronous batch extraction handlers.

HTTP routes call these functions only after the normal session-token guard. The
caller supplies job IDs and accepted feature specs; output paths remain owned
by the server session.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
import threading
import uuid
import zipfile
from contextlib import closing
from pathlib import Path

from .adapters import _json
from .batch_extraction import (export_json, export_long_csv, export_wide_csv,
                               inspect_batch, preview_batch)
from .batch_extraction import MAX_EXPORT_BYTES, MAX_FEATURES
from .extraction_draft import (MAX_DRAFT_BYTES, canonical_wire_state, freeze_draft,
                               prepare_draft, preview_draft)
from .document_api import _accepted
from .extraction import Cell, _digest
from .job_control import Cancelled, JobControl, checkpoint, job_scope
from .mesh_workflow import geometry_digest
from .raster_source import authorized_reads, bundle_paths
from .spatial import metric_crs

_FEATURE_KEYS = {'name', 'path', 'band', 'semantics', 'value_units'}
_FORMATS = {
    'wide_csv': (export_wide_csv, 'application/zip', 'MeshCherry-batch-wide.zip'),
    'long_csv': (export_long_csv, 'application/zip', 'MeshCherry-batch-long.zip'),
    'json': (export_json, 'application/json', 'MeshCherry-batch.json'),
}


def _request_id(value):
    if value is not None and (not isinstance(value, str) or
                              not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', value)):
        raise ValueError('Invalid batch request ID')


def _request_fingerprint(data):
    """Fingerprint the exact JSON request independently of current file state."""
    try:
        payload = json.dumps({'mesh_job_id': data['mesh_job_id'],
                              'features': data['features'], 'draft': data.get('draft'),
                              'plan_hash': data.get('plan_hash')},
                             sort_keys=True, ensure_ascii=False, allow_nan=False,
                             separators=(',', ':'))
    except (TypeError, ValueError, KeyError) as error:
        raise ValueError('Invalid batch request payload') from error
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _validate_data(server, data):
    if not isinstance(data, dict) or set(data) - {'mesh_job_id', 'features', 'request_id', 'draft', 'plan_hash'} or \
            not {'mesh_job_id', 'features'} <= set(data):
        raise ValueError('Batch requires mesh_job_id and feature specs')
    mesh_id = data['mesh_job_id']
    specs = data['features']
    _request_id(data.get('request_id'))
    if not isinstance(mesh_id, str) or not mesh_id:
        raise ValueError('Accepted mesh job ID required')
    has_draft = 'draft' in data
    if not isinstance(specs, list) or len(specs) > MAX_FEATURES or (not has_draft and not specs):
        raise ValueError('Batch must contain from 1 to 500 feature specs')
    manifest, cells_data = _accepted(server, mesh_id)
    crs = cells_data.get('crs_wkt')
    if not crs or not cells_data.get('cells'):
        raise ValueError('Accepted mesh has no cells or CRS')
    cells = tuple(Cell(row['cell_id'], row['kind'], tuple(tuple(xy) for xy in row['xy']))
                  for row in cells_data['cells'])
    digest = geometry_digest(cells, crs)
    if not manifest.get('geometry_digest') or manifest['geometry_digest'] != digest:
        raise ValueError('Accepted mesh geometry digest mismatch')
    cells_path = server.artifact(mesh_id, 'cells')
    manifest_path = server.artifact(mesh_id, 'manifest')
    expected_hash = manifest.get('artifact_hashes', {}).get('cells.json')
    if not expected_hash or _digest(cells_path) != expected_hash:
        raise ValueError('Accepted mesh cell artifact hash mismatch')

    analysis_crs = metric_crs(crs).to_wkt()
    if not isinstance(specs, list):
        raise ValueError('features must be a list')
    draft = None
    if has_draft:
        draft_raw = data['draft']
        try:
            draft_size = len(json.dumps(draft_raw, ensure_ascii=False, allow_nan=False,
                                        separators=(',', ':')).encode('utf-8'))
        except (TypeError, ValueError) as error:
            raise ValueError('Invalid draft payload') from error
        if draft_size > MAX_DRAFT_BYTES:
            raise ValueError('Draft request exceeds 1 MiB')
        draft = prepare_draft(mesh_id, digest, cells, specs, draft_raw)
        source_names = {column['source_name'] for column in draft['columns']
                        if column['type'] == 'raster'}
        specs_to_resolve = [spec for spec in specs if isinstance(spec, dict) and
                            spec.get('name') in source_names]
    else:
        specs_to_resolve = specs
    normalized = []
    protected_inputs = set()
    for spec in specs_to_resolve:
        if not isinstance(spec, dict) or set(spec) != _FEATURE_KEYS:
            raise ValueError('Each feature requires name, path, band, semantics, and value_units')
        if not isinstance(spec['path'], str):
            raise ValueError('Feature raster path must be text')
        raster = server.permitted(spec['path'])
        # Validate every exact companion grant before starting a worker. Opens
        # recheck these grants through authorized_reads and raster snapshots.
        for source in bundle_paths(raster):
            server.permitted(source)
            protected_inputs.add(Path(source).resolve(strict=False))
        normalized.append({**spec, 'path': str(raster)})
    retained = {str(cells_path): _digest(cells_path), str(manifest_path): _digest(manifest_path)}
    frozen = None
    if has_draft:
        # Only retained raster columns need path grants. Rebuild the plan with
        # the authorized paths before freezing it; the source names stay stable.
        draft = prepare_draft(mesh_id, digest, cells, normalized, draft_raw)
        frozen = freeze_draft(draft, mesh_job_id=mesh_id,
                              geometry_digest=digest, cells=cells)
        claimed_hash = data.get('plan_hash')
        if claimed_hash is not None and claimed_hash != frozen['plan_digest']:
            raise ValueError('Draft plan hash does not match the canonical draft')
        normalized = frozen['features']
        extraction_cells = frozen['cells']
        extraction_digest = geometry_digest(extraction_cells, crs)
        draft_metadata = dict(parent_geometry_digest=digest,
            plan_hash=frozen['plan_digest'], columns=frozen['columns'],
            overrides=frozen['overrides'], manual_rows=frozen['manual_rows'],
            excluded_cell_ids=frozen['excluded_cell_ids'])
    else:
        extraction_cells = cells
        extraction_digest = digest
        draft_metadata = None
    return dict(mesh_job_id=mesh_id, cells=cells, cell_crs=crs,
                analysis_crs=analysis_crs, geometry_digest=digest,
                extraction_cells=extraction_cells, extraction_geometry_digest=extraction_digest,
                draft=draft, frozen_draft=frozen, draft_metadata=draft_metadata,
                retained=retained, protected_inputs=protected_inputs, features=normalized)


def draft_preview(server, data):
    """Build the accepted-mesh table draft without opening any raster values."""
    if not isinstance(data, dict) or set(data) - {
            'mesh_job_id', 'features', 'draft', 'offset', 'limit', 'visible_columns'}:
        raise ValueError('Unsupported extraction draft preview fields')
    draft_request = {key: data[key] for key in ('mesh_job_id', 'features') if key in data}
    draft_request['draft'] = data.get('draft')
    prepared = _validate_data(server, draft_request)
    page = preview_draft(prepared['draft'], offset=data.get('offset', 0),
                         limit=data.get('limit', 100),
                         visible_columns=data.get('visible_columns'))
    return dict(draft=canonical_wire_state(prepared['draft']),
        plan_hash=prepared['draft']['plan_digest'],
        geometry_digest=prepared['geometry_digest'],
        columns=page['columns'], rows=page['rows'], total_rows=page['total_rows'],
        total_cells=page['total_cells'], accepted_cells=page['accepted_cells'],
        selected_cells=len(prepared['extraction_cells']),
        offset=page['offset'], limit=page['limit'])


def start_batch(server, data):
    """Queue a sequential batch against a successful accepted mesh job."""
    if not isinstance(data, dict) or set(data) - {'mesh_job_id', 'features', 'request_id', 'draft', 'plan_hash'} or \
            not {'mesh_job_id', 'features'} <= set(data):
        raise ValueError('Batch requires mesh_job_id and feature specs')
    request_id = data.get('request_id')
    _request_id(request_id)
    fingerprint = _request_fingerprint(data)
    with server.lock:
        if request_id:
            existing = next(((ident, job) for ident, job in server.jobs.items()
                             if job.get('request_id') == request_id), None)
            if existing:
                if (existing[1].get('operation') != 'batch_extract' or
                        existing[1].get('request_fingerprint') != fingerprint):
                    raise ValueError('Request ID already belongs to a different batch')
                return dict(job_id=existing[0], operation='batch_extract',
                            request_id=request_id,status=existing[1]['status'])
    prepared = _validate_data(server, data)
    with server.lock:
        # Another identical request may have passed validation concurrently.
        if request_id:
            existing = next(((ident, job) for ident, job in server.jobs.items()
                             if job.get('request_id') == request_id), None)
            if existing:
                if (existing[1].get('operation') != 'batch_extract' or
                        existing[1].get('request_fingerprint') != fingerprint):
                    raise ValueError('Request ID already belongs to a different batch')
                return dict(job_id=existing[0], operation='batch_extract',
                            request_id=request_id,status=existing[1]['status'])
        if server.picker_active or server.preflight_inflight or server._active_job_locked():
            raise ValueError('Hãy chờ tác vụ hiện tại hoàn tất')
        if len(server.jobs) >= 50:
            raise ValueError('Session job limit reached; restart UI')
        job_id = uuid.uuid4().hex
        server.protected_inputs.update(prepared['protected_inputs'])
        output = server.permitted(server.output_root / ('batch-' + job_id), exists=False, write=True)
        database = server.permitted(output / 'batch.sqlite', exists=False, write=True)
        server.jobs[job_id] = dict(status='running', stage='batch_queued',
            operation='batch_extract', request_id=request_id,
            request_fingerprint=fingerprint, result=None,
            batch_output=str(output), batch_database=str(database))
        control = JobControl(lock=server.lock)
        server.controls[job_id] = control

    def worker():
        result = None

        def accepted_mesh_unchanged():
            try:
                return all(_digest(Path(path)) == expected
                           for path, expected in prepared['retained'].items())
            except OSError:
                return False

        try:
            output.mkdir(parents=True, exist_ok=False)
            control.output = output
            with job_scope(control), authorized_reads(server.permitted):
                def progress(event):
                    stage = event['stage']
                    stage_name = f"batch_feature_{event['feature_index'] + 1}_of_{event['total']}"
                    control.stage(stage_name)
                    with server.lock:
                        server.jobs[job_id].update(stage=stage_name,
                            current_feature=event['name'],
                            feature_index=event['feature_index'],
                            feature_total=event['total'],
                            feature_status=event['status'])
                    if stage in {'feature_start', 'feature_complete', 'feature_no_data', 'feature_failed'}:
                        detail = f"{event['feature_index'] + 1}/{event['total']} {event['name']}: {event['status']}"
                        if event.get('error'):
                            detail += ' - ' + event['error']
                        control.log(detail)

                from .batch_extraction import run_batch
                batch = run_batch(database, prepared['extraction_cells'], prepared['features'],
                    cell_crs=prepared['cell_crs'], analysis_crs=prepared['analysis_crs'],
                    accepted_geometry_digest=prepared['extraction_geometry_digest'], progress=progress,
                    draft_plan=prepared['draft_metadata'],
                    allow_empty_features=prepared['draft'] is not None)
                if not accepted_mesh_unchanged():
                    raise ValueError('Accepted mesh changed during batch extraction')
                result = dict(status='pass' if batch['status'] == 'complete' else 'partial',
                              output=str(output), batch_database=str(database), batch=batch)
        except Cancelled as error:
            if accepted_mesh_unchanged():
                result = dict(status='cancelled', output=str(output), batch_database=str(database),
                              error=str(error))
            else:
                result = dict(status='fail', output=str(output), batch_database=str(database),
                              error='Accepted mesh changed during batch extraction')
        except Exception as error:
            result = dict(status='fail', output=str(output), batch_database=str(database),
                          error=str(error)[:2000])

        with server.lock:
            if control.cancel.is_set() and not control.committed and not accepted_mesh_unchanged():
                result = dict(status='fail', output=str(output), batch_database=str(database),
                              error='Accepted mesh changed during batch extraction')
            elif control.cancel.is_set() and not control.committed and result['status'] != 'fail':
                result = dict(status='cancelled', output=str(output),
                              batch_database=str(database), error='Đã dừng theo yêu cầu')
                if database.is_file():
                    try:
                        with closing(sqlite3.connect(database)) as db:
                            db.execute("UPDATE batch SET status='cancelled'")
                            db.commit()
                        result['batch'] = inspect_batch(database)
                    except sqlite3.Error as error:
                        result['database_status_error'] = str(error)[:500]
            control.finished = control.clock()
            server.jobs[job_id].update(status=result['status'], stage=result['status'], result=result)

    threading.Thread(target=worker, daemon=True, name='meshcherry-batch-' + job_id[:8]).start()
    return dict(job_id=job_id, operation='batch_extract', request_id=request_id,status='running')


def _batch_database(server, data, *, require_terminal=False):
    if not isinstance(data, dict) or set(data) - {'job_id', 'offset', 'limit',
            'feature_names', 'visible_columns', 'cell_id', 'cell_id_mode', 'format'} or \
            not isinstance(data.get('job_id'), str):
        raise ValueError('Batch action requires an owned job_id')
    job_id = data['job_id']
    job = server.job(job_id)
    if job.get('operation') != 'batch_extract':
        raise ValueError('Unknown batch job')
    if job['status'] == 'fail':
        raise ValueError('Batch result failed; preview and export are unavailable')
    if require_terminal and job['status'] in {'running', 'stopping'}:
        raise ValueError('Batch export is available after the job finishes')
    raw_path = job.get('batch_database')
    if not raw_path:
        raise ValueError('Batch database is not ready')
    try:
        path = server.permitted(raw_path)
        output = server.permitted(job['batch_output'])
    except (FileNotFoundError, OSError) as error:
        raise ValueError('Batch database is not ready') from error
    if output not in path.parents or path.name != 'batch.sqlite':
        raise ValueError('Batch result escaped its session-owned output')
    return job, path


def batch_preview(server, data):
    allowed = {'job_id', 'offset', 'limit', 'feature_names', 'visible_columns',
               'cell_id', 'cell_id_mode'}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError('Unsupported batch preview fields')
    job, database = _batch_database(server, data)
    try:
        page = preview_batch(database, offset=data.get('offset', 0), limit=data.get('limit', 100),
            feature_names=data.get('feature_names'), cell_id=data.get('cell_id'),
            cell_id_mode=data.get('cell_id_mode', 'exact'),
            visible_columns=data.get('visible_columns'))
    except (sqlite3.OperationalError, ValueError) as error:
        if not database.is_file() or 'locked' in str(error).lower() or 'not found' in str(error).lower():
            raise ValueError('Batch preview is not ready; retry shortly') from error
        raise
    return page


def batch_export_file(server, data):
    if not isinstance(data, dict) or set(data) != {'job_id', 'format'}:
        raise ValueError('Batch export requires job_id and format')
    job, database = _batch_database(server, data, require_terminal=True)
    file_format = data['format']
    if file_format not in _FORMATS:
        raise ValueError('Unsupported batch export format')
    export, mime, filename = _FORMATS[file_format]
    export_root = server.permitted(server.session_dir / 'batch-exports', exists=False, write=True)
    export_root.mkdir(parents=True, exist_ok=True)
    destination = server.permitted(export_root / (uuid.uuid4().hex + Path(filename).suffix),
                                   exists=False, write=True)
    try:
        if file_format in {'wide_csv', 'long_csv'}:
            with tempfile.TemporaryDirectory(prefix='batch-csv-', dir=export_root) as temp_root:
                table = Path(temp_root) / 'table.csv'
                export(database, table)
                metadata = inspect_batch(database)
                with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED,
                                     compresslevel=3, allowZip64=True) as archive:
                    with table.open('rb') as source, archive.open('table.csv', 'w') as target:
                        while True:
                            checkpoint()
                            block = source.read(1024 * 1024)
                            if not block:
                                break
                            target.write(block)
                            if destination.stat().st_size > MAX_EXPORT_BYTES:
                                raise ValueError('export byte limit exceeded')
                    archive.writestr('metadata.json', json.dumps(
                        metadata, ensure_ascii=False, allow_nan=False,
                        separators=(',', ':')))
            if destination.stat().st_size > MAX_EXPORT_BYTES:
                raise ValueError('export byte limit exceeded')
        else:
            export(database, destination)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return dict(path=str(destination), mime=mime, filename=filename)
