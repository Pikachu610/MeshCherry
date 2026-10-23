"""Session-authenticated loopback UI, restricted local metadata and owned outputs."""
import argparse
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sys
import stat
import threading
from urllib.parse import urlsplit, parse_qs
import uuid

import rasterio
from .runner import effective_config, preflight, run, write_json
from .spatial import metric_crs
from .indexed_extraction import footprint
from .adapters import _json
from .gmsh_meshing import inspect_executable
from .onboarding import sample_directory, tutorial_seen, acknowledge
from .recent import read_recent, remember, save_recent
from .gmsh_preferences import read_saved, save_checked
from .job_control import JobControl, Cancelled, job_scope, checkpoint, finalize_manifest
from .boundary import bundle_paths, read_boundary
from .meshing_parameters import policy as meshing_policy
from .workload_limits import MAX_MESH_CELLS, MAX_MESH_BYTES
from .presentation import INTERACTIVE_CELL_LIMIT, display_policy, artifact_view
from ._version import __version__
from .build_info import identity
from .failure_details import describe_failure
from .comparison_failures import describe_comparison_failure
from .raster_source import RASTER_SUFFIXES, bundle_paths as raster_bundle_paths, open_raster, check_driver, authorized_reads

ASSETS=Path(__file__).with_name('webui')
WORKSPACE=(Path(sys.executable).resolve().parent/'workspace'
           if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[4])
ARTIFACTS={'manifest':'manifest.json','qa':'qa.json','cells':'cells.json',
           'attributes':'attributes.json','csv':'attributes.csv','preview':'preview.png',
           'overview':'run-overview.png',
           'msh':'meshing/mesh.msh','mesh':'meshing/mesh.json','gmsh_log':'meshing/gmsh.log',
           'comparison':'comparison.json','comparison_counts':'comparison-counts.json',
           'quality':'quality.json'}


class AccessError(ValueError): pass


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,workspace=WORKSPACE,port=0,allow_roots=(),tutorial_preferences=None,recent_preferences=None,gmsh_preferences=None,startup_project=None):
        self.workspace=Path(workspace).resolve(strict=True)
        self.lock=threading.RLock()
        self._startup_project_bytes=self._read_startup_project(startup_project)
        self._startup_project_inflight=False
        self.sample_root=sample_directory()
        self.tutorial_preferences=tutorial_preferences
        self.recent_preferences=Path(recent_preferences) if recent_preferences is not None else self.workspace/'.scratch/gis-mesh-workbench/recent-files.json'
        self.gmsh_preferences=Path(gmsh_preferences) if gmsh_preferences is not None else self.workspace/'.scratch/gis-mesh-workbench/gmsh.json'
        self.native_picker=None
        self.native_save=None
        self.native_window_control=None
        self.document_active=False
        self.picker_active=False
        self.selected_files=set()
        self.protected_inputs=set()
        self.output_roots=tuple(dict.fromkeys([self.workspace]+[Path(p).resolve(strict=True) for p in allow_roots]))
        self.roots=tuple(dict.fromkeys([*self.output_roots,self.sample_root]))
        if any(not p.is_dir() for p in self.roots): raise ValueError('allowed roots must be directories')
        self.token=secrets.token_urlsafe(32)
        self.session_dir=self.permitted(self.workspace/'.scratch/gis-mesh-workbench/ui-sessions'/uuid.uuid4().hex,exists=False,write=True)
        self.session_dir.mkdir(parents=True,exist_ok=False)
        self.output_root=self.permitted(self.workspace/'.scratch/gis-mesh-workbench/ui-runs',exists=False,write=True)
        self.output_root.mkdir(parents=True,exist_ok=True)
        self.validated={}; self.jobs={}; self.controls={}; self.current_preflight=None
        self.quality_latest={}
        self.preflight_generation=0; self.preflight_inflight=set()
        from .download_manager import DownloadManager
        self.downloads=DownloadManager(self)
        super().__init__(('127.0.0.1',port),Handler)
        self.host=f'127.0.0.1:{self.server_port}'; self.origin='http://'+self.host

    @staticmethod
    def _read_startup_project(path):
        if path is None:return None
        from .document_api import MAX_PROJECT_BYTES
        candidate=Path(path).expanduser()
        if candidate.suffix.lower()!='.mcy':
            raise ValueError('Startup project must be a .mcy file.')
        try:
            if not stat.S_ISREG(candidate.lstat().st_mode):
                raise ValueError('Startup project must be a regular file.')
            resolved=candidate.resolve(strict=True)
            with resolved.open('rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError('Startup project must be a regular file.')
                data=stream.read(MAX_PROJECT_BYTES+1)
        except ValueError:raise
        except OSError as error:
            raise ValueError('Startup project cannot be read.') from error
        if not data:raise ValueError('Startup project is empty.')
        if len(data)>MAX_PROJECT_BYTES:raise ValueError('Startup project exceeds 64 MiB.')
        return data

    @property
    def has_startup_project(self):
        with self.lock:return self._startup_project_bytes is not None

    def saved_gmsh(self):
        return read_saved(self.gmsh_preferences)

    def _active_job_locked(self):
        return self.document_active or any(job['status'] in {'running','stopping'} for job in self.jobs.values())

    def _invalidate_preflight_locked(self):
        self.preflight_generation+=1
        self.current_preflight=None
        self.validated.clear()

    def permitted(self,path,*,exists=True,write=False):
        path=Path(path)
        if not path.is_absolute(): path=self.workspace/path
        resolved=path.resolve(strict=False)
        roots=self.output_roots if write else self.roots
        if write and (resolved==self.sample_root or self.sample_root in resolved.parents):
            raise AccessError('Dữ liệu mẫu chỉ được đọc, không được ghi kết quả tại đây')
        if not (not write and resolved in self.selected_files) and not any(resolved==root or root in resolved.parents for root in roots):
            raise AccessError('Path nằm ngoài thư mục được cho phép')
        if exists:
            resolved=resolved.resolve(strict=True)
            if not (not write and resolved in self.selected_files) and not any(resolved==root or root in resolved.parents for root in roots):
                raise AccessError('Resolved path escaped allowed roots')
        return resolved

    def choose_native(self,data):
        if not isinstance(data,dict) or set(data)!={'kind'} or data['kind'] not in {'raster','raster_many','mesh','gmsh','output','boundary'}:
            raise ValueError('Unsupported picker kind')
        with self.lock:
            if self.native_picker is None: raise ValueError('Hộp chọn Windows chỉ có trong bản desktop')
            if self.picker_active or self.preflight_inflight or self._active_job_locked():
                raise ValueError('Đang xử lý. Hãy chờ trước khi chọn tệp khác.')
            self.picker_active=True
        try:
            paths=self.native_picker(data['kind'])
            if not paths: return dict(cancelled=True)
            if data['kind']=='raster_many':
                if not isinstance(paths,(tuple,list)) or not 1<=len(paths)<=500:
                    raise ValueError('Chọn từ 1 đến 500 raster')
                selected=[]; grants=set()
                for candidate in paths:
                    candidate=Path(candidate)
                    if not candidate.is_absolute():raise ValueError('Đường dẫn raster phải tuyệt đối')
                    candidate=candidate.resolve(strict=True)
                    if not candidate.is_file() or candidate.suffix.lower() not in RASTER_SUFFIXES:
                        raise ValueError('Chọn raster .tif, .tiff hoặc .asc')
                    selected.append(candidate);grants.update(raster_bundle_paths(candidate))
                with self.lock:self.selected_files.update(grants)
                return dict(cancelled=False,paths=[str(p) for p in dict.fromkeys(selected)])
            if not isinstance(paths,(tuple,list)) or len(paths)!=1: raise ValueError('Chọn đúng một tệp hoặc thư mục')
            path=Path(paths[0])
            if not path.is_absolute(): raise ValueError('Native picker must return an absolute path')
            path=path.resolve(strict=True)
            kind=data['kind']
            with self.lock:
                if kind=='output':
                    if not path.is_dir() or path==Path(path.anchor): raise ValueError('Chọn một thư mục kết quả riêng, không chọn gốc ổ đĩa')
                    if path==self.sample_root or self.sample_root in path.parents or path in self.sample_root.parents:
                        raise AccessError('Dữ liệu mẫu chỉ được đọc')
                    self.output_roots=tuple(dict.fromkeys([*self.output_roots,path]))
                    self.roots=tuple(dict.fromkeys([*self.roots,path]))
                else:
                    suffixes={'raster':RASTER_SUFFIXES,'mesh':{'.json'},'gmsh':{'.exe'},'boundary':{'.shp'}}
                    if not path.is_file() or path.suffix.lower() not in suffixes[kind]: raise ValueError('Tệp không đúng định dạng đã chọn')
                    if kind=='gmsh' and path.name.lower()!='gmsh.exe': raise ValueError('Hãy chọn gmsh.exe')
                    # Grant only this selected input, never its parent directory.
                    if kind!='gmsh':
                        self.selected_files.update(bundle_paths(path) if kind=='boundary' else raster_bundle_paths(path) if kind=='raster' else [path])
                        self.remember_input(kind,path)
                self._invalidate_preflight_locked()
            return dict(cancelled=False,path=str(path))
        finally:
            with self.lock: self.picker_active=False

    def remember_input(self,kind,path):
        if self.sample_root==path or self.sample_root in path.parents: return
        remember(self.recent_preferences,kind,path)

    def protect_config_inputs(self,config):
        """Record known sources for overwrite protection without granting access."""
        source=config.get('cell_source') or {}
        references=[config.get('raster_path'),(config.get('resolution_reference') or {}).get('path')]
        references.append((config.get('overview_dem') or {}).get('path'))
        references.extend(source.get(key) for key in ('path','boundary_path','roi_raster_path','dem_raster_path','executable'))
        protected=set()
        for raw in references:
            if not raw:continue
            path=Path(raw).resolve(strict=False);protected.add(path)
            if path.suffix.lower()=='.shp':protected.update(bundle_paths(path))
            elif path.suffix.lower() in RASTER_SUFFIXES:protected.update(raster_bundle_paths(path))
        with self.lock:self.protected_inputs.update(protected)

    def recent_action(self,data):
        with self.lock:
            if self.picker_active or self.preflight_inflight or self._active_job_locked(): raise ValueError('Hãy chờ xử lý xong')
            if data=={'action':'clear'}:
                save_recent(self.recent_preferences,[]); return dict(items=[])
            if not isinstance(data,dict) or set(data)!={'id'}: raise ValueError('Recent open accepts only id')
            item=next((i for i in read_recent(self.recent_preferences) if i['id']==data['id']),None)
            if item is None: raise ValueError('Tệp không còn trong lịch sử')
            path=Path(item['path']).resolve(strict=True)
            if not path.is_file(): raise ValueError('Tệp đã bị di chuyển hoặc xóa. Hãy chọn lại.')
            if item['kind']=='raster':
                with open_raster(path,require_crs=False) as source:
                    check_driver(source)
            elif item['kind']=='mesh':
                if path.stat().st_size>MAX_MESH_BYTES or _json(path).get('schema_version')!='mesh-artifact-v0':
                    raise ValueError('Tệp gần đây không còn là mesh JSON được hỗ trợ')
            else:
                files=bundle_paths(path)
                crs=rasterio.crs.CRS.from_wkt(files[3].read_text(encoding='utf-8-sig'))
                if not crs or not (crs.is_projected or crs.is_geographic): raise ValueError('SHP cần CRS ngang hợp lệ')
                import shapefile
                with shapefile.Reader(shp=str(files[0]),shx=str(files[1]),dbf=str(files[2])) as reader:
                    if reader.shapeType!=shapefile.POLYGON: raise ValueError('SHP gần đây không còn là Polygon 2D')
            # Explicit user reopen grants exactly the input; no parent/root grants.
            self.selected_files.update(bundle_paths(path) if item['kind']=='boundary' else raster_bundle_paths(path) if item['kind']=='raster' else [path])
            self.remember_input(item['kind'],path)
            self._invalidate_preflight_locked()
            return dict(kind=item['kind'],path=str(path))

    def normalize_form(self,data):
        if not isinstance(data,dict) or data.get('schema_version') not in {'gis-workbench-run-config-v1','gis-workbench-run-config-v2'}:
            raise ValueError('UI requires v1/v2 config')
        config=copy.deepcopy(data)
        raster=None
        if config.get('raster_path'):
            raster=self.permitted(config['raster_path'])
            if raster.suffix.lower() not in RASTER_SUFFIXES or not raster.is_file(): raise ValueError('GeoTIFF/ASC required')
            config['raster_path']=str(raster)
        elif config.get('operation')!='mesh_only': raise ValueError('GeoTIFF/ASC required for extraction')
        output=self.permitted(config['output_root'],write=True)
        if not output.is_dir(): raise ValueError('output root must be an existing directory')
        config['output_root']=str(output)
        source=config.get('cell_source',{})
        inputs=[raster] if raster else []
        if config.get('overview_dem'):
            reference=config['overview_dem']
            overview_dem=self.permitted(reference['path'])
            if overview_dem.suffix.lower() not in RASTER_SUFFIXES or not overview_dem.is_file():
                raise ValueError('DEM tham chiếu cần là GeoTIFF/ASC')
            reference['path']=str(overview_dem);inputs.append(overview_dem)
        if config.get('resolution_reference'):
            reference=config['resolution_reference']
            planned=self.permitted(reference['path'])
            if planned.suffix.lower() not in RASTER_SUFFIXES or not planned.is_file(): raise ValueError('Planned extraction GeoTIFF/ASC required')
            reference['path']=str(planned);inputs.append(planned)
        if source.get('roi_raster_path'):
            roi=self.permitted(source['roi_raster_path'])
            if roi.suffix.lower() not in RASTER_SUFFIXES: raise ValueError('ROI GeoTIFF/ASC required')
            source['roi_raster_path']=str(roi);inputs.append(roi)
        if source.get('dem_raster_path'):
            dem=self.permitted(source['dem_raster_path'])
            if dem.suffix.lower() not in RASTER_SUFFIXES or not dem.is_file(): raise ValueError('DEM GeoTIFF/ASC required')
            source['dem_raster_path']=str(dem);inputs.append(dem)
        if source.get('kind')=='gmsh_generated':
            executable=Path(source['executable'])
            if not executable.is_absolute(): raise ValueError('Gmsh cần đường dẫn tuyệt đối')
            source['executable']=str(executable.resolve(strict=True))
            if source.get('boundary_path'):
                boundary=self.permitted(source['boundary_path'])
                files=bundle_paths(boundary)
                for file in files: self.permitted(file)
                source['boundary_path']=str(boundary);inputs.extend(files)
        if source.get('kind') in {'mesh_artifact','frozen_cells'}:
            mesh=self.permitted(source['path'])
            if mesh.suffix.lower()!='.json' or not mesh.is_file(): raise ValueError('mesh JSON required')
            source['path']=str(mesh)
            if source['kind']=='frozen_cells':
                if output==mesh.parent: raise ValueError('Output must not be frozen mesh directory')
            else: inputs.append(mesh)
        for candidate in list(inputs):
            if candidate.suffix.lower() in RASTER_SUFFIXES:
                for file in raster_bundle_paths(candidate):
                    self.permitted(file)
                    if file not in inputs:inputs.append(file)
        if any(output==p.parent or output in p.parents for p in inputs):
            raise ValueError('output root must be isolated from source directories')
        return config

    def validate_form(self,data):
        with self.lock:
            if self.picker_active: raise ValueError('Hãy đóng hộp chọn tệp trước khi kiểm tra')
            if self._active_job_locked(): raise ValueError('Hãy chờ tác vụ hiện tại hoàn tất trước khi kiểm tra')
            if self.preflight_inflight: raise ValueError('Hãy chờ kiểm tra dữ liệu hiện tại hoàn tất')
            self.preflight_generation+=1
            generation=self.preflight_generation
            self.current_preflight=None; self.validated.clear()
            self.preflight_inflight.add(generation)
        try:
            # Normalization can inspect bundles and filesystem paths; serialize
            # outside the shared job lock while the in-flight guard prevents
            # pickers and job starts from changing the validated inputs.
            config=self.normalize_form(data)
            self.protect_config_inputs(config)
            ident=uuid.uuid4().hex; path=self.session_dir/(ident+'.json')
            write_json(path,config)
            effective=effective_config(path)
            report,cells=preflight(effective)
            display=display_policy(len(cells.cells) if cells else 0)
            rendered_cells=([dict(cell_id=c.cell_id,kind=c.kind,xy=c.xy) for c in cells.cells]
                            if cells and display['mode']=='interactive' else [])
        except BaseException:
            with self.lock: self.preflight_inflight.discard(generation)
            raise
        with self.lock:
            current=(generation==self.preflight_generation and not self.picker_active
                     and not self._active_job_locked())
            if current and report['status']=='pass':
                self.validated[ident]=path; self.current_preflight=ident
            self.preflight_inflight.discard(generation)
            return dict(preflight_id=ident if current and report['status']=='pass' else None,
                report=report,display=display,
                cells=rendered_cells if cells and display['mode']=='interactive' else [],
                superseded=not current)

    def start_job(self,preflight_id,request_id=None):
        with self.lock:
            if request_id is not None:
                if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',request_id):
                    raise ValueError('Invalid run request ID')
                existing=next(((ident,job) for ident,job in self.jobs.items()
                               if job.get('request_kind')=='run' and job.get('request_id')==request_id),None)
                if existing:
                    if existing[1].get('request_fingerprint')!=preflight_id:
                        raise ValueError('Request ID already belongs to a different run')
                    return dict(job_id=existing[0],operation=existing[1]['operation'],
                                request_id=request_id,status=existing[1]['status'])
            if self.picker_active: raise ValueError('Hãy đóng hộp chọn tệp trước khi chạy')
            # Older validations may still be doing filesystem work, but once a
            # newer generation has passed they must not block its run.
            if self.preflight_generation in self.preflight_inflight:
                raise ValueError('Hãy chờ preflight hiện tại hoàn tất trước khi chạy')
            if preflight_id!=self.current_preflight or preflight_id not in self.validated:
                raise ValueError('Preflight đã hết hiệu lực; hãy kiểm tra lại')
            if any(j['status'] in {'running','stopping'} for j in self.jobs.values()): raise ValueError('Một job đang chạy hoặc đang dừng')
            if len(self.jobs)>=50: raise ValueError('Session job limit reached; restart UI')
            path=self.validated[preflight_id]; self.current_preflight=None
            operation=_json(path).get('operation','extract')
            job_id=uuid.uuid4().hex
            job=dict(status='running',stage='config',operation=operation,result=None)
            if request_id:
                job.update(request_kind='run',request_id=request_id,request_fingerprint=preflight_id)
            self.jobs[job_id]=job
            control=JobControl(lock=self.lock); self.controls[job_id]=control
            def update(stage):
                with self.lock:
                    self.jobs[job_id]['stage']=stage
                    control.stage(stage)
            def worker():
                try:
                    with job_scope(control), authorized_reads(self.permitted):
                        checkpoint()
                        result=run(path,progress=update)
                except Cancelled as error:
                    result=dict(status='cancelled',output=str(control.output) if control.output else None,error=str(error))
                except Exception as error: result=dict(status='fail',output=None,error=str(error))
                with self.lock:
                    # The shared lock orders accepted Stop against terminal success.
                    if control.cancel.is_set() and not control.committed:
                        result.update(status='cancelled',error='Đã dừng theo yêu cầu')
                    if result['status'] == 'fail':
                        result['failure'] = describe_failure(result.get('error'))
                    elif result['status'] == 'cancelled':
                        result.pop('failure', None)
                    control.finished=control.clock()
                    self.jobs[job_id].update(status=result['status'],stage='done' if result['status']=='pass' else result['status'],result=result)
            threading.Thread(target=worker,daemon=True).start()
            if request_id:
                return dict(job_id=job_id,operation=operation,request_id=request_id,status='running')
            return dict(job_id=job_id)

    def start_comparison(self,data):
        """Compare an owned accepted mesh; calculations never replace that mesh."""
        if not isinstance(data,dict):
            raise ValueError('Comparison requires a configuration object')
        baseline_mode=data.get('baseline_mode','target_grid')
        if baseline_mode not in {'native_raster','target_grid','resolution_estimate'}:
            raise ValueError('Chọn pixel gốc, lưới theo độ phân giải hoặc ước tính số node')
        required={'mesh_job_id'}
        if baseline_mode=='resolution_estimate':
            required|={'cell_size_m','source_resolution_m'}
        else:
            required|={'raster_path','band','semantics','value_units'}
            if baseline_mode=='target_grid':required.add('cell_size_m')
        allowed=required|{'request_id','baseline_mode'}
        if baseline_mode!='resolution_estimate':allowed.add('workload_mode')
        if baseline_mode=='native_raster':allowed.add('cell_size_m')
        if required-set(data) or set(data)-allowed:
            raise ValueError('Thiếu trường bắt buộc hoặc có trường không phù hợp với cách so sánh')
        if baseline_mode!='resolution_estimate':
            workload_mode=data.get('workload_mode','bounded')
            if not isinstance(workload_mode,str) or workload_mode not in {'bounded','research'}:
                raise ValueError('Chọn chế độ tiêu chuẩn hoặc nghiên cứu dữ liệu lớn')
            data={**data,'workload_mode':workload_mode}
        import math
        import hashlib
        import re
        request_id=data.get('request_id')
        if request_id is not None and (not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',request_id)):
            raise ValueError('Invalid comparison request ID')
        def positive_size(value):
            try:return type(value) in (int,float) and math.isfinite(value) and value>0
            except OverflowError:return False
        if not isinstance(data['mesh_job_id'],str):
            raise ValueError('Cấu hình so sánh không hợp lệ')
        if baseline_mode!='native_raster' and not positive_size(data['cell_size_m']):
            raise ValueError('Nhập kích thước ô tham chiếu dương, hữu hạn theo mét')
        if baseline_mode=='native_raster' and data.get('cell_size_m') is not None:
            raise ValueError('Pixel gốc dùng độ phân giải của raster, không nhập kích thước ô mới')
        if baseline_mode=='resolution_estimate':
            if not positive_size(data['source_resolution_m']):
                raise ValueError('Nhập độ phân giải nguồn dương, hữu hạn theo mét')
        elif (not isinstance(data['raster_path'],str) or not data['raster_path'].strip()
                or type(data['band']) is not int or data['band']<1
                or data['semantics'] not in {'intensive','extensive','categorical'}
                or not isinstance(data['value_units'],str) or not data['value_units'].strip() or len(data['value_units'])>128):
            raise ValueError('Cấu hình raster so sánh không hợp lệ')
        with self.lock:
            fingerprint=hashlib.sha256(json.dumps(data,sort_keys=True,allow_nan=False).encode()).hexdigest()
            if request_id:
                existing=next(((ident,job) for ident,job in self.jobs.items() if job.get('request_id')==request_id),None)
                if existing:
                    if existing[1].get('request_fingerprint')!=fingerprint:
                        raise ValueError('Request ID already belongs to a different comparison')
                    return dict(job_id=existing[0])
            if self.picker_active or self.preflight_inflight or self._active_job_locked():
                raise ValueError('Hãy chờ tác vụ hiện tại hoàn tất')
            if len(self.jobs)>=50: raise ValueError('Session job limit reached; restart UI')
            cells=self.artifact(data['mesh_job_id'],'cells')
            mesh_manifest=self.artifact(data['mesh_job_id'],'manifest')
            mesh_data=_json(cells)
            if (not mesh_data.get('cells') or any(row.get('kind')!='triangle' for row in mesh_data['cells'])):
                raise ValueError('Chọn lưới tam giác đã tạo để so sánh với lưới vuông')
            raster=None
            if baseline_mode!='resolution_estimate':
                raster=self.permitted(data['raster_path'])
                for file in raster_bundle_paths(raster): self.permitted(file)
            analysis_crs=metric_crs(mesh_data['crs_wkt']).to_wkt()
            if raster is not None:
                # Research preflight is metadata-only: no full window traversal
                # or STRtree construction while holding the session lock.
                from .comparison import preflight_comparison
                with authorized_reads(self.permitted):
                    preflight_comparison(cells,raster,analysis_crs=analysis_crs,
                        band=data['band'],semantics=data['semantics'],mesh_data=mesh_data,
                        workload_mode=data['workload_mode'])
            config={**copy.deepcopy(data),'baseline_mode':baseline_mode,'analysis_crs':analysis_crs,
                    'cells_path':str(cells),'edge_subdivisions':8}
            if raster is not None:config['raster_path']=str(raster)
            from .extraction import _digest
            retained={str(cells):_digest(cells),str(mesh_manifest):_digest(mesh_manifest)}
            manifest=dict(schema_version='mesh-comparison-manifest-v1',operation='comparison',
                status='running',application_build=identity(),mesh_job_id=config['mesh_job_id'],
                input_hashes=retained,artifact_hashes={})
            job_id=uuid.uuid4().hex
            self.jobs[job_id]=dict(status='running',stage='comparison',operation='comparison',
                request_id=request_id,request_fingerprint=fingerprint,result=None)
            control=JobControl(lock=self.lock);self.controls[job_id]=control
            def worker():
                output=None;comparison_manifest=copy.deepcopy(manifest)
                try:
                    with job_scope(control),authorized_reads(self.permitted):
                        checkpoint()
                        from .comparison import compare_mesh
                        output=self.permitted(self.output_root/('comparison-'+job_id),exists=False,write=True)
                        output.mkdir(exist_ok=False);control.output=output
                        write_json(output/'config.json',config)
                        if baseline_mode=='resolution_estimate':
                            from .comparison_estimate import compare_resolution_estimate
                            report=compare_resolution_estimate(cells,analysis_crs=analysis_crs,
                                cell_size_m=config['cell_size_m'],source_resolution_m=config['source_resolution_m'])
                        else:
                            options=dict(analysis_crs=analysis_crs,cell_size_m=config.get('cell_size_m'),
                                band=config['band'],semantics=config['semantics'],
                                value_units=config['value_units'],edge_subdivisions=8,
                                workload_mode=config['workload_mode'])
                            if baseline_mode=='native_raster':options['baseline_mode']=baseline_mode
                            report=compare_mesh(cells,raster,**options)
                            visualization=report.get('visualization')
                            if isinstance(visualization,dict):
                                checkpoint()
                                from .input_preview import raster_preview
                                try:
                                    thumbnail=raster_preview(raster,band=config['band'])
                                except (ValueError,OSError) as preview_error:
                                    visualization.setdefault('source',{})['reason']='Không tạo được ảnh nguồn: '+str(preview_error)
                                else:
                                    visualization.setdefault('source',{}).update({key:thumbnail[key] for key in
                                        ('image','crs','bounds','sampled','display_shape','source_shape')})
                                    visualization['source'].update(available=True,shared_zoom=False,
                                        frame='native source CRS and full-source extent')
                                    visualization['source'].pop('reason',None)
                                    visualization['shared_zoom_layers']=['structured','unstructured','overlay']
                                checkpoint()
                                # The optional display read cannot weaken the measured raster identity.
                                source_identity=report.get('provenance',{}).get('source_files_sha256')
                                if source_identity:
                                    from .raster_source import assert_unchanged
                                    assert_unchanged(raster,source_identity)
                        from shapely.geometry import Polygon
                        from .comparison_allocation import allocation_summary
                        areas=[]
                        for row in mesh_data['cells']:
                            checkpoint()
                            areas.append(Polygon(row['xy']).area)
                        reference_area=None
                        if baseline_mode!='native_raster':
                            reference_area=config['cell_size_m']**2
                        report['spatial_allocation']=allocation_summary(areas,reference_area)
                        checkpoint()
                        if any(_digest(Path(p))!=expected for p,expected in retained.items()):
                            raise ValueError('Lưới mốc đã thay đổi trong lúc so sánh')
                        write_json(output/'comparison.json',report)
                        comparison_manifest['status']='pass'
                        comparison_manifest=finalize_manifest(output,comparison_manifest)
                        result=dict(status=comparison_manifest['status'],output=str(output),error=comparison_manifest.get('error'))
                except Cancelled as error:
                    result=dict(status='cancelled',output=str(output) if output else None,error=str(error))
                    comparison_manifest.update(status='cancelled',error=str(error),artifact_hashes={},
                                    cancelled_stage=control.phase)
                except Exception as error:
                    result=dict(status='fail',output=str(output) if output else None,error=str(error))
                    comparison_manifest.update(status='fail',error=str(error),artifact_hashes={},
                                    failed_stage=control.phase)
                with self.lock:
                    if control.cancel.is_set() and not control.committed:
                        result.update(status='cancelled',error='Đã dừng theo yêu cầu')
                        comparison_manifest.update(status='cancelled',error=result['error'],artifact_hashes={},
                                        cancelled_stage=control.phase)
                    if result['status']=='fail':
                        result['failure']=describe_comparison_failure(result.get('error'))
                    else:
                        result.pop('failure',None)
                    if output and result['status']!='pass':
                        comparison_manifest.update(status=result['status'],error=result.get('error'),artifact_hashes={})
                        if result['status']=='cancelled': comparison_manifest.setdefault('cancelled_stage',control.phase)
                        else: comparison_manifest.setdefault('failed_stage',control.phase)
                        diagnostic=dict(comparison_manifest)
                        try:
                            write_json(output/'manifest.json',diagnostic)
                            write_json(output/'qa.json',dict(schema_version='mesh-comparison-qa-v1',
                                status=result['status'],operation='comparison',error=result.get('error')))
                        except OSError as error:
                            result['diagnostic_error']=str(error)
                    control.finished=control.clock()
                    self.jobs[job_id].update(status=result['status'],stage='done' if result['status']=='pass' else result['status'],result=result)
            threading.Thread(target=worker,daemon=True).start()
            return dict(job_id=job_id)

    def start_comparison_count(self,data):
        """Start a separate, cancellable count-only accepted-mesh inventory."""
        if not isinstance(data,dict):
            raise ValueError('Comparison count requires a configuration object')
        mode=data.get('baseline_mode')
        if mode not in {'target_grid','native_raster'}:
            raise ValueError('Choose target_grid or native_raster baseline mode')
        required={'mesh_job_id','baseline_mode','request_id'}
        if mode=='target_grid': required.add('cell_size_m')
        else: required.add('raster_path')
        allowed=required|({'cell_size_m'} if mode=='target_grid' else {'raster_path'})
        if required-set(data) or set(data)-allowed:
            raise ValueError('Missing or unsupported comparison count fields')
        request_id=data['request_id']
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',request_id):
            raise ValueError('Invalid comparison count request ID')
        if not isinstance(data['mesh_job_id'],str) or not data['mesh_job_id']:
            raise ValueError('Accepted mesh job ID is required')
        if mode=='target_grid':
            size=data['cell_size_m']
            import math
            try: size=float(size) if type(size) in (int,float) else math.nan
            except (OverflowError,ValueError): size=math.nan
            if not math.isfinite(size) or size<=0:
                raise ValueError('Target-grid cell size must be finite and positive')
        else:
            path=data['raster_path']
            if not isinstance(path,str) or not path.strip():
                raise ValueError('Native-raster inventory requires a raster path')

        from .extraction import _digest
        fingerprint=hashlib.sha256(json.dumps(
            {key:value for key,value in data.items() if key!='request_id'},
            sort_keys=True,allow_nan=False).encode()).hexdigest()
        with self.lock:
            existing=next(((ident,job) for ident,job in self.jobs.items()
                if job.get('operation')=='comparison_count' and job.get('request_id')==request_id),None)
            if existing:
                if existing[1].get('request_fingerprint')!=fingerprint:
                    raise ValueError('Request ID already belongs to a different comparison count')
                return dict(job_id=existing[0],operation='comparison_count',request_id=request_id,
                    status=existing[1]['status'],mesh_count=copy.deepcopy(existing[1]['mesh_count']),
                    mesh_sha256=existing[1]['mesh_sha256'])
            if self.picker_active or self.preflight_inflight or self._active_job_locked():
                raise ValueError('Hãy chờ tác vụ hiện tại hoàn tất')
            if len(self.jobs)>=50: raise ValueError('Session job limit reached; restart UI')
            cells=self.artifact(data['mesh_job_id'],'cells')
            mesh_manifest=self.artifact(data['mesh_job_id'],'manifest')
            mesh_manifest_data=_json(mesh_manifest)
            if mesh_manifest_data.get('operation')!='mesh_only':
                raise ValueError('Chọn một lưới tam giác đã được chấp nhận')
            accepted_cells_hash=mesh_manifest_data.get('artifact_hashes',{}).get('cells.json')
            if not accepted_cells_hash or _digest(cells)!=accepted_cells_hash:
                raise ValueError('Accepted mesh manifest/hash mismatch')
            mesh_data=_json(cells)
            rows=mesh_data.get('cells')
            if (not isinstance(rows,list) or not 0<len(rows)<=MAX_MESH_CELLS
                    or any(row.get('kind')!='triangle' for row in rows)):
                raise ValueError('Chọn lưới tam giác đã được chấp nhận')
            # Bind the immediate KPI to the exact accepted JSON bytes that
            # were parsed. The worker repeats this check during computation.
            if _digest(cells)!=accepted_cells_hash:
                raise ValueError('Accepted mesh changed while reading its full cell count')
            mesh_count=dict(count=len(rows),definition='accepted_triangle_cells',unit='cells')
            raster=None
            if mode=='native_raster':
                raster=self.permitted(data['raster_path'])
                for path in raster_bundle_paths(raster): self.permitted(path)
            retained={str(cells):accepted_cells_hash,str(mesh_manifest):_digest(mesh_manifest)}
            job_id=uuid.uuid4().hex
            manifest=dict(schema_version='mesh-comparison-inventory-manifest-v1',
                operation='comparison_count',status='running',application_build=identity(),
                mesh_job_id=data['mesh_job_id'],request_id=request_id,input_hashes=retained,
                artifact_hashes={})
            self.jobs[job_id]=dict(status='running',stage='comparison_count',operation='comparison_count',
                request_id=request_id,request_fingerprint=fingerprint,mesh_count=mesh_count,
                mesh_sha256=accepted_cells_hash,result=None)
            control=JobControl(lock=self.lock);self.controls[job_id]=control
            def progress(phase,completed,total,unit):
                control.stage(phase)
                control.measure(completed,total,unit=unit)
            def worker():
                output=None;inventory_manifest=copy.deepcopy(manifest)
                try:
                    with job_scope(control),authorized_reads(self.permitted):
                        checkpoint()
                        from .comparison_inventory import build_inventory
                        control.stage('verify_accepted_mesh')
                        if raster is not None:
                            inventory_manifest['input_hashes'].update({
                                str(path):_digest(path) for path in raster_bundle_paths(raster)})
                        mesh_data=_json(cells)
                        analysis_crs=metric_crs(mesh_data['crs_wkt']).to_wkt()
                        output=self.permitted(self.output_root/('comparison-count-'+job_id),exists=False,write=True)
                        output.mkdir(exist_ok=False);control.output=output
                        inventory=build_inventory(cells,mesh_job_id=data['mesh_job_id'],
                            baseline_mode=mode,analysis_crs=analysis_crs,
                            cell_size_m=data.get('cell_size_m'),raster_path=raster,progress=progress)
                        inventory_retained=dict(retained)
                        inventory_retained.update({path:digest for path,digest in
                            inventory_manifest['input_hashes'].items() if path not in inventory_retained})
                        if any(_digest(Path(path))!=expected for path,expected in inventory_retained.items()):
                            raise ValueError('Accepted mesh or raster bundle changed during inventory count')
                        # Keep the sole user-facing artifact atomic and private until
                        # its manifest is committed and the job reaches terminal pass.
                        temporary=output/'comparison-counts.tmp'
                        write_json(temporary,inventory)
                        checkpoint()
                        temporary.replace(output/'comparison-counts.json')
                        inventory_manifest['status']='pass'
                        inventory_manifest=finalize_manifest(output,inventory_manifest)
                        result=dict(status=inventory_manifest['status'],output=str(output),
                            error=inventory_manifest.get('error'))
                except Cancelled as error:
                    result=dict(status='cancelled',output=str(output) if output else None,error=str(error))
                    inventory_manifest.update(status='cancelled',error=str(error),artifact_hashes={},
                        cancelled_stage=control.phase)
                except Exception as error:
                    result=dict(status='fail',output=str(output) if output else None,error=str(error))
                    inventory_manifest.update(status='fail',error=str(error),artifact_hashes={},
                        failed_stage=control.phase)
                with self.lock:
                    if control.cancel.is_set() and not control.committed:
                        result.update(status='cancelled',error='Đã dừng theo yêu cầu')
                        inventory_manifest.update(status='cancelled',error=result['error'],artifact_hashes={},
                            cancelled_stage=control.phase)
                    if result['status']=='fail':
                        result['failure']=describe_comparison_failure(result.get('error'))
                    else:
                        result.pop('failure',None)
                    if output and result['status']!='pass':
                        try: write_json(output/'manifest.json',inventory_manifest)
                        except OSError as error: result['diagnostic_error']=str(error)
                    control.finished=control.clock()
                    self.jobs[job_id].update(status=result['status'],
                        stage='done' if result['status']=='pass' else result['status'],result=result)
            threading.Thread(target=worker,daemon=True).start()
            return dict(job_id=job_id,operation='comparison_count',request_id=request_id,
                status='running',mesh_count=mesh_count,mesh_sha256=accepted_cells_hash)

    def job(self,ident):
        with self.lock:
            if ident not in self.jobs: raise ValueError('Unknown job')
            result=copy.deepcopy(self.jobs[ident])
            if ident in self.controls:
                result['monitor']=self.controls[ident].snapshot()
                result['monitor'].pop('log_lines')
            return result

    def stop_job(self,data):
        if not isinstance(data,dict) or set(data)!={'job_id'} or not isinstance(data['job_id'],str):
            raise ValueError('Stop accepts only job_id')
        ident=data['job_id']
        with self.lock:
            if ident not in self.jobs or ident not in self.controls: raise ValueError('Unknown job')
            accepted=self.jobs[ident]['status'] in {'running','stopping'} and self.controls[ident].request_cancel()
            if accepted: self.jobs[ident]['status']='stopping'
            return dict(job_id=ident,accepted=accepted,status='stopping' if accepted else self.jobs[ident]['status'])

    def artifact(self,ident,name):
        if name not in ARTIFACTS: raise AccessError('Unsupported artifact')
        job=self.job(ident)
        if job['status'] in {'running','stopping'} or not job['result'] or not job['result'].get('output'):
            raise ValueError('Artifact chưa sẵn sàng')
        if job['status']!='pass' and name not in {'manifest','qa','gmsh_log'}:
            raise AccessError('Lượt chạy chưa hoàn tất; chỉ có chẩn đoán và log')
        output=self.permitted(job['result']['output'])
        if name not in {'manifest','qa','gmsh_log'}:
            manifest=self.permitted(output/'manifest.json')
            if _json(manifest).get('status')!='pass': raise AccessError('Run manifest chưa xác nhận thành công')
        path=(output/ARTIFACTS[name]).resolve(strict=True)
        if output not in path.parents: raise AccessError('Artifact escaped run directory')
        return path


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def discard_rejected_body(self):
        # A close with unread TCP data can discard an error response on Windows.
        # Drain only a small, fixed-size rejected body, with a short timeout;
        # never parse it or pass it to the runner. Huge/slow uploads may reset.
        try: count=int(self.headers.get('Content-Length','0'))
        except ValueError: count=0
        if not self.headers.get('Transfer-Encoding') and 0<count<=131072:
            previous=self.connection.gettimeout()
            try:
                self.connection.settimeout(.25)
                self.rfile.read(count)
            except (OSError,TimeoutError): pass
            finally: self.connection.settimeout(previous)
        self.close_connection=True
    def respond(self,value,status=200,content_type='application/json',filename=None):
        if status>=400 and self.command=='POST' and not getattr(self,'body_consumed',False):
            self.discard_rejected_body()
        body=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',content_type+'; charset=utf-8' if content_type.startswith(('text/','application/json')) else content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        if filename: self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
        self.end_headers(); self.wfile.write(body)

    def guard(self,api=False,post=False):
        if self.headers.get('Host')!=self.server.host: raise AccessError('Host not allowed')
        origin=self.headers.get('Origin')
        if (post and origin!=self.server.origin) or (origin and origin!=self.server.origin):
            raise AccessError('Origin not allowed')
        if api and not secrets.compare_digest(self.headers.get('X-Workbench-Token','').encode('utf-8'),self.server.token.encode('utf-8')):
            raise AccessError('Token missing or invalid')

    def stream_file(self,path,content_type='application/octet-stream',filename=None):
        """Stream only a previously authorized file; never buffer a large export."""
        with Path(path).open('rb') as source:
            size=os.fstat(source.fileno()).st_size
            self.send_response(200)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(size))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            if filename:
                safe=re.sub(r'[^A-Za-z0-9._-]','_',filename)[:180]
                self.send_header('Content-Disposition','attachment; filename="'+safe+'"')
            self.end_headers()
            remaining=size
            while remaining:
                block=source.read(min(1024*1024,remaining))
                if not block:break
                self.wfile.write(block);remaining-=len(block)

    def do_GET(self):
        try:
            parsed=urlsplit(self.path); path=parsed.path
            self.guard(api=path.startswith('/api/'))
            fonts = {'/fonts/SourceSans3-VF.ttf': 'SourceSans3-VF.ttf'}
            if path == '/window_chrome.js':
                return self.respond((ASSETS/'window_chrome.js').read_bytes(), content_type='text/javascript')
            if path in fonts:
                return self.respond((ASSETS/'fonts'/fonts[path]).read_bytes(), content_type='font/ttf')
            if path in {'/','/app.js','/coach.js','/attribute_table.js','/downloads.js','/extraction_draft.js','/extraction_draft.css','/attribute_table.css','/workspace_layout.css','/style.css','/wizard.css','/icon.png','/favicon.ico','/quality_panel.js','/quality_panel.css','/quality_bridge.js','/comparison_view.js','/comparison_view.css'}:
                name={'/':'index.html','/icon.png':'meshcherry-icon-v1.png',
                      '/favicon.ico':'meshcherry-icon-v1.ico'}.get(path,path[1:])
                mime={'index.html':'text/html','app.js':'text/javascript','coach.js':'text/javascript','attribute_table.js':'text/javascript','downloads.js':'text/javascript','extraction_draft.js':'text/javascript','extraction_draft.css':'text/css','attribute_table.css':'text/css','workspace_layout.css':'text/css','style.css':'text/css','wizard.css':'text/css',
                      'quality_panel.js':'text/javascript','quality_bridge.js':'text/javascript','quality_panel.css':'text/css','comparison_view.js':'text/javascript','comparison_view.css':'text/css',
                      'meshcherry-icon-v1.png':'image/png','meshcherry-icon-v1.ico':'image/vnd.microsoft.icon'}[name]
                body=(ASSETS/name).read_bytes()
                if name=='index.html':
                    body=body.replace(b'{{APP_VERSION}}',__version__.encode('ascii'))
                    body=body.replace(b'{{DESKTOP_CHROME}}',b'true' if callable(self.server.native_window_control) else b'false')
                return self.respond(body,content_type=mime)
            if path=='/api/bootstrap':
                gmsh_saved=self.server.saved_gmsh()
                gmsh_path=gmsh_saved.get('executable','')
                return self.respond(dict(roots=[str(p) for p in self.server.roots],output_roots=[str(p) for p in self.server.output_roots],output_root=str(self.server.output_root),
                    meshing_parameters=meshing_policy(),application_build=identity(),
                    mesh_limits=dict(max_cells=MAX_MESH_CELLS,interactive_cells=INTERACTIVE_CELL_LIMIT),
                    has_startup_project=self.server.has_startup_project,
                    gmsh_path=gmsh_path,gmsh_saved=gmsh_saved,native_picker=self.server.native_picker is not None,native_save=self.server.native_save is not None,
                    tutorial_seen=tutorial_seen(self.server.tutorial_preferences),
                    sample_raster=str(self.server.sample_root/'sample_10_30.tif'),
                    sample_mesh=str(self.server.sample_root/'two_triangles.json')))
            if path=='/api/recent':
                return self.respond(dict(items=[dict(id=i['id'],kind=i['kind'],name=Path(i['path']).name,opened=i['opened'])
                    for i in read_recent(self.server.recent_preferences)]))
            if path=='/api/research/sf1':
                from .research_reference import sf1_reference
                return self.respond(sf1_reference())
            query=parse_qs(parsed.query)
            if path=='/api/batch/status':
                request_id=query.get('request_id',[''])[0]
                if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',request_id):
                    raise ValueError('Invalid batch request ID')
                with self.server.lock:
                    found=next(((ident,job) for ident,job in self.server.jobs.items()
                                if job.get('operation')=='batch_extract' and job.get('request_id')==request_id),None)
                    result=(dict(job_id=found[0],operation='batch_extract',request_id=request_id,
                                 status=found[1]['status']) if found else dict(job_id=None))
                return self.respond(result)
            if path=='/api/download/status':
                return self.respond(self.server.downloads.status(query.get('id',[''])[0]))
            if path=='/api/job-log':
                ident=query.get('id',[''])[0]
                with self.server.lock:
                    if ident not in self.server.controls: raise ValueError('Unknown job')
                    snapshot=self.server.controls[ident].log_snapshot(int(query.get('cursor',['0'])[0]))
                return self.respond(snapshot)
            if path=='/api/active-job':
                # Authenticated session metadata only; no source/config/path data.
                with self.server.lock:
                    active=next((ident for ident,job in self.server.jobs.items()
                                 if job['status'] in {'running','stopping'}),None)
                    operation=self.server.jobs[active].get('operation') if active else None
                    request_id=self.server.jobs[active].get('request_id') if active else None
                    mesh_count=self.server.jobs[active].get('mesh_count') if active else None
                    mesh_sha256=self.server.jobs[active].get('mesh_sha256') if active else None
                return self.respond(dict(job_id=active,**({'operation':operation} if operation else {}),
                    **({'request_id':request_id} if request_id else {}),
                    **({'mesh_count':mesh_count,'mesh_sha256':mesh_sha256}
                       if operation=='comparison_count' else {})))
            if path=='/api/comparison/status':
                request_id=query.get('request_id',[''])[0]
                if not isinstance(request_id,str) or not 8<=len(request_id)<=80:
                    raise ValueError('Invalid comparison request ID')
                with self.server.lock:
                    found=next(((ident,job) for ident,job in self.server.jobs.items()
                                if job.get('operation')=='comparison' and job.get('request_id')==request_id),None)
                    result=dict(job_id=found[0],operation='comparison',status=found[1]['status']) if found else dict(job_id=None)
                return self.respond(result)
            if path=='/api/comparison/count/status':
                request_id=query.get('request_id',[''])[0]
                if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',request_id):
                    raise ValueError('Invalid comparison count request ID')
                with self.server.lock:
                    found=next(((ident,job) for ident,job in self.server.jobs.items()
                        if job.get('operation')=='comparison_count' and job.get('request_id')==request_id),None)
                    result=(dict(job_id=found[0],operation='comparison_count',request_id=request_id,
                        status=found[1]['status'],mesh_count=copy.deepcopy(found[1]['mesh_count']),
                        mesh_sha256=found[1]['mesh_sha256']) if found else dict(job_id=None))
                return self.respond(result)
            if path=='/api/run/status':
                request_id=query.get('request_id',[''])[0]
                if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',request_id):
                    raise ValueError('Invalid run request ID')
                with self.server.lock:
                    found=next(((ident,job) for ident,job in self.server.jobs.items()
                                if job.get('request_kind')=='run' and job.get('request_id')==request_id),None)
                    result=(dict(job_id=found[0],operation=found[1]['operation'],request_id=request_id,
                                 status=found[1]['status']) if found else dict(job_id=None))
                return self.respond(result)
            if path=='/api/job': return self.respond(self.server.job(query.get('id',[''])[0]))
            if path=='/api/mesh-config':
                from .document_api import _accepted
                ident=query.get('id',[''])[0]
                _accepted(self.server,ident)
                output=Path(self.server.job(ident)['result']['output'])
                config_path=output/'config.json'
                if config_path.is_file():config=_json(config_path)
                else:config=_json(output/'project-history.json').get('config')
                if not config:raise ValueError('Kết quả này chưa có cấu hình để chia lại lưới')
                return self.respond(dict(config=config))
            if path=='/api/artifact':
                name=query.get('name',[''])[0]; artifact=self.server.artifact(query.get('id',[''])[0],name)
                if query.get('view')==['1'] and query.get('download')!=['1'] and name in {'cells','attributes','qa'}:
                    return self.respond(artifact_view(name,_json(artifact)))
                mime='image/png' if name in {'preview','overview'} else 'text/csv' if name=='csv' else 'text/plain' if name in {'msh','gmsh_log'} else 'application/json'
                return self.stream_file(artifact,content_type=mime,
                    filename=Path(ARTIFACTS[name]).name if query.get('download')==['1'] else None)
            self.respond(dict(error='Not found'),404)
        except AccessError as error: self.respond(dict(error=str(error)),403)
        except Exception as error: self.respond(dict(error=str(error)),400)

    def do_POST(self):
        try:
            path=urlsplit(self.path).path; self.guard(api=True,post=True)
            if self.headers.get('Transfer-Encoding'):
                return self.respond(dict(error='Transfer-Encoding unsupported'),400)
            if path=='/api/project/startup':
                count=int(self.headers.get('Content-Length','0'))
                if count!=0:return self.respond(dict(error='Startup project request must have an empty body.'),400)
                with self.server.lock:
                    if self.server._startup_project_bytes is None:
                        return self.respond(dict(error='No startup project is pending.'),404)
                    if self.server._startup_project_inflight:
                        return self.respond(dict(error='Startup project is already opening.'),409)
                    self.server._startup_project_inflight=True
                    raw=self.server._startup_project_bytes
                try:
                    from .document_api import open_project
                    result=open_project(self.server,raw)
                except BaseException:
                    with self.server.lock:self.server._startup_project_inflight=False
                    raise
                with self.server.lock:
                    self.server._startup_project_bytes=None
                    self.server._startup_project_inflight=False
                return self.respond(result)
            if path=='/api/project/open':
                from .document_api import MAX_PROJECT_BYTES, open_project
                if self.headers.get_content_type()!='application/octet-stream':
                    return self.respond(dict(error='application/octet-stream required'),415)
                count=int(self.headers.get('Content-Length','0'))
                if not 0<count<=MAX_PROJECT_BYTES:
                    return self.respond(dict(error='Project body size limit'),413)
                raw=self.rfile.read(count);self.body_consumed=True
                return self.respond(open_project(self.server,raw))
            if self.headers.get_content_type()!='application/json':
                return self.respond(dict(error='application/json required'),415)
            count=int(self.headers.get('Content-Length','0'))
            ceiling=1048576 if path in {'/api/batch/start','/api/extraction-draft/preview'} else 65536
            if not 0<count<=ceiling: return self.respond(dict(error='Request body size limit'),413)
            raw=self.rfile.read(count); self.body_consumed=True
            data=json.loads(raw,parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
            if path == '/api/window/control':
                allowed = {'minimize','toggle_maximize','close',
                           'resize-n','resize-ne','resize-e','resize-se',
                           'resize-s','resize-sw','resize-w','resize-nw'}
                if (not isinstance(data,dict) or set(data) != {'action'} or
                        not isinstance(data['action'],str) or data['action'] not in allowed):
                    raise ValueError('Thao tác cửa sổ không được hỗ trợ.')
                callback=self.server.native_window_control
                if not callable(callback):
                    raise ValueError('Điều khiển cửa sổ chỉ có trong bản desktop.')
                # Never hold the server lock across a native UI-thread call.
                return self.respond(callback(data['action']))
            if path=='/api/extraction-draft/preview':
                from .batch_api import draft_preview
                return self.respond(draft_preview(self.server,data))
            if path=='/api/batch/start':
                from .batch_api import start_batch
                return self.respond(start_batch(self.server,data))
            if path=='/api/batch/preview':
                from .batch_api import batch_preview
                return self.respond(batch_preview(self.server,data))
            if path=='/api/batch/export':
                from .batch_api import batch_export_file
                from .document_api import document_action
                with document_action(self.server):artifact=batch_export_file(self.server,data)
                try:return self.stream_file(artifact['path'],artifact['mime'],artifact['filename'])
                finally:Path(artifact['path']).unlink(missing_ok=True)
            if path=='/api/download/start':return self.respond(self.server.downloads.start(data))
            if path=='/api/download/cancel':
                if not isinstance(data,dict) or set(data)!={'id'}:raise ValueError('Download cancellation requires id')
                return self.respond(self.server.downloads.cancel(data['id']))
            if path=='/api/project/save':
                from .document_api import save_project
                return self.respond(save_project(self.server,data),content_type='application/zip',filename='MeshCherry.mcy')
            if path=='/api/export':
                from .document_api import export_result
                body,mime,filename=export_result(self.server,data)
                return self.respond(body,content_type=mime,filename=filename)
            if path=='/api/picker': return self.respond(self.server.choose_native(data))
            if path=='/api/recent': return self.respond(self.server.recent_action(data))
            if path=='/api/input-preview':
                from .input_preview import raster_preview, boundary_preview
                if not isinstance(data,dict) or set(data)-{'path','kind','band','analysis_crs'}:
                    raise ValueError('Unsupported preview fields')
                source=self.server.permitted(data['path'])
                if data.get('kind')=='raster':
                    for file in raster_bundle_paths(source):self.server.permitted(file)
                    return self.respond(raster_preview(source,data.get('band',1)))
                if data.get('kind')=='boundary':
                    for file in bundle_paths(source):self.server.permitted(file)
                    domain,report=read_boundary(source,data['analysis_crs'])
                    return self.respond(boundary_preview(domain,data['analysis_crs']))
                raise ValueError('Unsupported preview kind')
            if path=='/api/boundary':
                if set(data)!={'path','analysis_crs'}: raise ValueError('Boundary accepts only path and analysis_crs')
                boundary=self.server.permitted(data['path'])
                for file in bundle_paths(boundary): self.server.permitted(file)
                domain,report=read_boundary(boundary,data['analysis_crs'])
                with self.server.lock: self.server.remember_input('boundary',boundary)
                return self.respond(report)
            if path=='/api/tutorial':
                if data != {'action':'acknowledge'}: raise ValueError('unsupported tutorial action')
                acknowledge(self.server.tutorial_preferences)
                return self.respond(dict(ok=True))
            if path=='/api/fs/list':
                directory=self.server.permitted(data['path'])
                if not directory.is_dir(): raise ValueError('Directory required')
                entries=[]
                for index,item in enumerate(directory.iterdir()):
                    if index>=2000: raise ValueError('Directory listing limit exceeded')
                    try: item=self.server.permitted(item)
                    except (AccessError,FileNotFoundError,OSError): continue
                    entries.append(dict(name=item.name,path=str(item),kind='directory' if item.is_dir() else 'file',
                                        size=item.stat().st_size,extension=item.suffix.lower()))
                entries.sort(key=lambda e:(e['kind']!='directory',e['name'].casefold()))
                return self.respond(dict(path=str(directory),entries=entries))
            if path=='/api/source':
                source=self.server.permitted(data['path'])
                if source.suffix.lower() not in RASTER_SUFFIXES: raise ValueError('GeoTIFF/ASC required')
                for file in raster_bundle_paths(source):self.server.permitted(file)
                with open_raster(source,require_crs=False) as src:
                    check_driver(src)
                    band=data.get('band',1)
                    if type(band) is not int or not 1<=band<=src.count: raise ValueError('band out of range')
                    crs=str(src.crs) if src.crs else None
                    try: metric_crs(crs); eligible=True
                    except Exception: eligible=False
                    report=dict(path=str(source),driver=src.driver,crs=crs,crs_wkt=src.crs.to_wkt() if src.crs else None,
                        shape=[src.height,src.width],band_count=src.count,band=band,dtype=src.dtypes[band-1],
                        nodata=str(src.nodatavals[band-1]),mask=[f.name for f in src.mask_flag_enums[band-1]],
                        scale=str(src.scales[band-1]),offset=str(src.offsets[band-1]),bounds=list(src.bounds),
                        projected_metre=eligible)
                    if src.crs and data.get('analysis_crs'):
                        target=metric_crs(data['analysis_crs'])
                        from .raster_resolution import describe
                        report['resolution']=describe(src,target,band)
                        polygon=footprint(src,target,0,0,src.width,src.height,subdivisions=8)
                        report['display_footprint']=dict(xy=list(polygon.exterior.coords)[:-1],
                            crs_wkt=target.to_wkt(),bounds=list(polygon.bounds),
                            approximate=src.crs!=target)
                with self.server.lock: self.server.remember_input('raster',source)
                return self.respond(report)
            if path=='/api/gmsh':
                if set(data)!={'path'}: raise ValueError('Gmsh check accepts only path')
                if not isinstance(data['path'],str) or not data['path'].strip(): raise ValueError('Chưa chọn gmsh.exe. Hãy chọn tệp hoặc dán đường dẫn thật.')
                with self.server.lock:
                    if self.server.picker_active or self.server.preflight_inflight or self.server._active_job_locked():
                        raise ValueError('Hãy chờ xử lý xong trước khi kiểm tra Gmsh')
                if not Path(data['path']).is_absolute(): raise ValueError('Chọn đường dẫn Gmsh tuyệt đối')
                report=inspect_executable(data['path'])
                save_checked(self.server.gmsh_preferences,report)
                return self.respond(report)
            if path=='/api/job/stop': return self.respond(self.server.stop_job(data))
            if path.startswith('/api/quality/'):
                from .quality_jobs import start_quality, quality_result, quality_page, quality_export, quality_latest
                if path=='/api/quality/start': return self.respond(start_quality(self.server,data),202)
                if path=='/api/quality/result': return self.respond(quality_result(self.server,data))
                if path=='/api/quality/latest': return self.respond(quality_latest(self.server,data))
                if path=='/api/quality/page': return self.respond(quality_page(self.server,data))
                if path=='/api/quality/export':
                    body,mime,filename=quality_export(self.server,data)
                    return self.respond(body,content_type=mime,filename=filename)
            if path=='/api/comparison/count': return self.respond(self.server.start_comparison_count(data),202)
            if path=='/api/comparison': return self.respond(self.server.start_comparison(data),202)
            if path=='/api/preflight': return self.respond(self.server.validate_form(data))
            if path=='/api/run':
                if not isinstance(data,dict) or 'preflight_id' not in data or set(data)-{'preflight_id','request_id'}:
                    raise ValueError('Run accepts preflight_id and optional request_id')
                return self.respond(self.server.start_job(data['preflight_id'],data.get('request_id')),202)
            self.respond(dict(error='Not found'),404)
        except AccessError as error: self.respond(dict(error=str(error)),403)
        except Exception as error:
            body=dict(error=str(error))
            if path in {'/api/comparison','/api/comparison/count'}:
                body['failure']=describe_comparison_failure(error)
            self.respond(body,400)

    def do_OPTIONS(self): self.respond(dict(error='Method not allowed'),405)
    do_PUT=do_DELETE=do_PATCH=do_OPTIONS


def main(argv=None):
    parser=argparse.ArgumentParser(prog='gis-mesh-workbench ui')
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--allow-root',action='append',default=[])
    args=parser.parse_args(argv)
    server=WorkbenchServer(port=args.port,allow_roots=args.allow_root)
    print(server.origin+'/#token='+server.token,flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
