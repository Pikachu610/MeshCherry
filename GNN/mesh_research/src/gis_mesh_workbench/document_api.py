"""Session-owned project and export operations; input paths never grant access."""
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import tempfile
import uuid
import zipfile

from .adapters import _json
from .runner import write_json
from .mesh_workflow import geometry_digest, quality
from .extraction import Cell
from .build_info import identity

MAX_PROJECT_BYTES = 64 * 1024 * 1024


@contextmanager
def document_action(server):
    with server.lock:
        if server.picker_active or server.preflight_inflight or server._active_job_locked():
            raise ValueError('Hãy chờ tác vụ hiện tại hoàn tất.')
        server.document_active = True
    try:
        yield
    finally:
        with server.lock:
            server.document_active = False


def _accepted(server, ident):
    manifest = _json(server.artifact(ident, 'manifest'))
    if manifest.get('status') != 'pass' or manifest.get('operation') == 'comparison':
        raise ValueError('Chọn kết quả lưới đã hoàn thành.')
    raw = _json(server.artifact(ident, 'cells'))
    # Extraction artifacts additionally carry adapter provenance and historically
    # lack a schema tag. That metadata is not a geometry change.
    cells = dict(schema_version='workbench-cells-v1', crs_wkt=raw['crs_wkt'],
                 coordinate_units=raw['coordinate_units'], cells=raw['cells'])
    return manifest, cells


def save_project(server, data):
    from .project_io import save_project as write_project
    if not isinstance(data, dict) or set(data) - {'job_id', 'mesh_job_id', 'view_settings'}:
        raise ValueError('Unsupported project fields')
    with document_action(server):
        manifest, cells = _accepted(server, data['mesh_job_id'])
        qa = _json(server.artifact(data['mesh_job_id'], 'qa'))
        attributes = None
        ident = data.get('job_id', data['mesh_job_id'])
        current, current_cells = _accepted(server, ident)
        if (current_cells['cells'] != cells['cells'] or
                current_cells['crs_wkt'] != cells['crs_wkt']):
            raise ValueError('Kết quả thuộc tính không thuộc lưới đang lưu.')
        attribute_path = Path(server.job(ident)['result']['output']) / 'attributes.json'
        if attribute_path.is_file():
            attributes = _json(server.artifact(ident, 'attributes'))
        # Historical config is display/provenance only; archive never runs it.
        config_path = Path(server.job(data['mesh_job_id'])['result']['output']) / 'config.json'
        history_path = config_path.with_name('project-history.json')
        history = _json(history_path) if history_path.is_file() else {}
        # Imported projects retain their source QA in history. The current QA
        # artifact describes only the geometry revalidated during this open.
        if history.get('qa') is not None:
            qa = history['qa']
        config = _json(config_path) if config_path.is_file() else history.get('config')
        with tempfile.TemporaryDirectory(dir=server.session_dir, prefix='save-') as temporary:
            path = Path(temporary) / 'project.mcy'
            write_project(path, cells=cells, qa=qa, config=config, attributes=attributes,
                          provenance={'mesh_manifest': manifest, 'result_manifest': current},
                          view_settings=data.get('view_settings'))
            if path.stat().st_size > MAX_PROJECT_BYTES:
                raise ValueError('Dự án vượt giới hạn 64 MiB.')
            return path.read_bytes()


def open_project(server, raw):
    from .project_io import load_project
    if not raw or len(raw) > MAX_PROJECT_BYTES:
        raise ValueError('Dự án phải có kích thước từ 1 byte đến 64 MiB.')
    with document_action(server):
        with tempfile.TemporaryDirectory(dir=server.session_dir, prefix='open-') as temporary:
            path = Path(temporary) / 'project.mcy'
            path.write_bytes(raw)
            saved = load_project(path)
        # A new session-owned result, never the archive's original output path.
        ident = 'project-' + uuid.uuid4().hex
        output = server.output_root / ident
        cells = saved['cells']
        parsed = tuple(Cell(row['cell_id'], row['kind'], tuple(tuple(xy) for xy in row['xy']))
                       for row in cells['cells'])
        digest = geometry_digest(parsed, cells['crs_wkt'])
        artifacts = {'cells.json': cells, 'qa.json': dict(status='pass',
            mesh_quality=quality(parsed), historical_qa=saved.get('qa'), restored_project=True)}
        if saved.get('attributes') is not None:
            artifacts['attributes.json'] = saved['attributes']
        artifacts['project-history.json'] = {'config': saved.get('config'), 'qa': saved.get('qa'),
            'provenance': saved.get('provenance'), 'unavailable_inputs': saved.get('unavailable_inputs', [])}
        # Publish only complete restored artifacts. TemporaryDirectory owns only
        # this newly created staging folder and removes it on any write failure.
        with tempfile.TemporaryDirectory(dir=server.output_root, prefix='.project-import-') as temporary:
            staged=Path(temporary)/'result';staged.mkdir()
            for name, value in artifacts.items():
                write_json(staged / name, value)
            if saved.get('attributes') is not None:
                from .exports import export_cells
                export_cells(staged/'attributes.csv', 'csv', cells, saved['attributes'],
                             crs=cells['crs_wkt'])
            manifest = dict(schema_version='gis-workbench-run-manifest-v0', status='pass',
                operation='mesh_only', geometry_digest=digest, application_build=identity(),
                restored_project=True, original_inputs_authorized=False,
                artifact_hashes={name: hashlib.sha256((staged/name).read_bytes()).hexdigest()
                                 for name in (*artifacts, *(('attributes.csv',) if saved.get('attributes') is not None else ()))})
            write_json(staged / 'manifest.json', manifest)
            staged.rename(output)
        with server.lock:
            server._invalidate_preflight_locked()
            server.jobs[ident] = dict(status='pass', stage='done', operation='mesh_only',
                result=dict(status='pass', output=str(output)), restored_project=True)
        return dict(job_id=ident, has_attributes=saved.get('attributes') is not None,
                    view_settings=saved.get('view_settings') or {},
                    unavailable_inputs=saved.get('unavailable_inputs', []))


def export_result(server, data):
    from .exports import export_cells
    if not isinstance(data, dict) or set(data) - {'job_id', 'format', 'field', 'resolution', 'nodata'}:
        raise ValueError('Unsupported export fields')
    nodata = data.get('nodata', -9999)
    if type(nodata) not in (int, float) or not math.isfinite(nodata):
        raise ValueError('NoData must be a finite number.')
    fmt = data.get('format')
    suffixes = {'geojson': '.geojson', 'geotiff': '.tif', 'asc': '.asc'}
    if fmt not in suffixes:
        raise ValueError('Chọn GeoJSON, GeoTIFF hoặc ASC.')
    with document_action(server):
        _, cells = _accepted(server, data['job_id'])
        output = Path(server.job(data['job_id'])['result']['output'])
        attributes = (_json(server.artifact(data['job_id'], 'attributes'))
                      if (output/'attributes.json').is_file() else None)
        with tempfile.TemporaryDirectory(dir=server.session_dir, prefix='export-') as temporary:
            path = Path(temporary) / ('meshcherry-asc.zip' if fmt=='asc' else 'meshcherry' + suffixes[fmt])
            export_cells(path, fmt, cells, attributes, crs=cells['crs_wkt'],
                         field=data.get('field', 'value'), resolution=data.get('resolution'),
                         nodata=nodata, max_pixels=1_000_000)
            if fmt == 'asc':
                return path.read_bytes(), 'application/zip', path.name
            return path.read_bytes(), 'application/geo+json' if fmt == 'geojson' else 'image/tiff', path.name
