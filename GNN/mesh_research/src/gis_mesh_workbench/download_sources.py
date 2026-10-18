"""Allowlisted download producers; clients choose formats and job IDs, never paths."""
from pathlib import Path
import uuid


def resolve(server,data):
    if not isinstance(data,dict):raise ValueError('Chọn kết quả cần lưu')
    kind=data.get('kind')
    if kind=='artifact':
        if set(data)!={'kind','job_id','name'}:raise ValueError('Unsupported artifact download fields')
        source=server.artifact(data['job_id'],data['name'])
        return source.name,lambda:source,False
    if kind not in {'project','export','batch'} or set(data)!={'kind','request'} or not isinstance(data['request'],dict):
        raise ValueError('Unsupported download request')
    request=data['request']
    if kind=='batch':
        from .batch_api import _batch_database, _FORMATS, batch_export_file
        if set(request)!={'job_id','format'} or request.get('format') not in _FORMATS:
            raise ValueError('Chọn định dạng bảng thuộc tính')
        _batch_database(server,request,require_terminal=True)
        filename=_FORMATS[request['format']][2]
        def produce():
            from .document_api import document_action
            with document_action(server):return Path(batch_export_file(server,request)['path'])
        return filename,produce,True
    from .document_api import _accepted, save_project, export_result
    if kind=='project':
        if set(request)-{'mesh_job_id','job_id','view_settings'} or 'mesh_job_id' not in request:
            raise ValueError('Unsupported project fields')
        _accepted(server,request['mesh_job_id'])
        filename='MeshCherry.mcy'
    else:
        _accepted(server,request.get('job_id',''))
        formats={'geojson':'cells.geojson','geotiff':'attributes.tif','asc':'attributes-asc.zip'}
        filename=formats.get(request.get('format'))
        if filename is None:raise ValueError('Chọn định dạng xuất được hỗ trợ')
    def produce():
        if kind=='project':body=save_project(server,request)
        else:body,_,_=export_result(server,request)
        target=server.session_dir/(uuid.uuid4().hex+Path(filename).suffix)
        try:target.write_bytes(body)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target
    return filename,produce,True
