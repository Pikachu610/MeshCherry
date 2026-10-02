"""Session-authenticated loopback UI, restricted local metadata and owned outputs."""
import argparse
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import threading
from urllib.parse import urlsplit, parse_qs
import uuid

import rasterio
from .runner import effective_config, preflight, run, write_json
from .spatial import metric_crs
from .adapters import _json
from .onboarding import sample_directory, tutorial_seen, acknowledge

ASSETS=Path(__file__).with_name('webui')
WORKSPACE=(Path(sys.executable).resolve().parent/'workspace'
           if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[4])
ARTIFACTS={'manifest':'manifest.json','qa':'qa.json','cells':'cells.json',
           'attributes':'attributes.json','csv':'attributes.csv','preview':'preview.png'}


class AccessError(ValueError): pass


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,workspace=WORKSPACE,port=0,allow_roots=(),tutorial_preferences=None):
        self.workspace=Path(workspace).resolve(strict=True)
        self.sample_root=sample_directory()
        self.tutorial_preferences=tutorial_preferences
        self.output_roots=tuple(dict.fromkeys([self.workspace]+[Path(p).resolve(strict=True) for p in allow_roots]))
        self.roots=tuple(dict.fromkeys([*self.output_roots,self.sample_root]))
        if any(not p.is_dir() for p in self.roots): raise ValueError('allowed roots must be directories')
        self.token=secrets.token_urlsafe(32)
        self.session_dir=self.permitted(self.workspace/'.scratch/gis-mesh-workbench/ui-sessions'/uuid.uuid4().hex,exists=False,write=True)
        self.session_dir.mkdir(parents=True,exist_ok=False)
        self.output_root=self.permitted(self.workspace/'.scratch/gis-mesh-workbench/ui-runs',exists=False,write=True)
        self.output_root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock(); self.validated={}; self.jobs={}; self.current_preflight=None
        super().__init__(('127.0.0.1',port),Handler)
        self.host=f'127.0.0.1:{self.server_port}'; self.origin='http://'+self.host

    def permitted(self,path,*,exists=True,write=False):
        path=Path(path)
        if not path.is_absolute(): path=self.workspace/path
        resolved=path.resolve(strict=False)
        roots=self.output_roots if write else self.roots
        if write and (resolved==self.sample_root or self.sample_root in resolved.parents):
            raise AccessError('Dữ liệu mẫu chỉ được đọc, không được ghi kết quả tại đây')
        if not any(resolved==root or root in resolved.parents for root in roots):
            raise AccessError('Path nằm ngoài thư mục được cho phép')
        if exists:
            resolved=resolved.resolve(strict=True)
            if not any(resolved==root or root in resolved.parents for root in roots):
                raise AccessError('Resolved path escaped allowed roots')
        return resolved

    def normalize_form(self,data):
        if not isinstance(data,dict) or data.get('schema_version')!='gis-workbench-run-config-v1':
            raise ValueError('UI requires v1 config')
        config=copy.deepcopy(data)
        raster=self.permitted(config['raster_path'])
        if raster.suffix.lower() not in {'.tif','.tiff'} or not raster.is_file(): raise ValueError('GeoTIFF required')
        config['raster_path']=str(raster)
        output=self.permitted(config['output_root'],write=True)
        if not output.is_dir(): raise ValueError('output root must be an existing directory')
        config['output_root']=str(output)
        source=config.get('cell_source',{})
        inputs=[raster]
        if source.get('kind')=='mesh_artifact':
            mesh=self.permitted(source['path'])
            if mesh.suffix.lower()!='.json' or not mesh.is_file(): raise ValueError('mesh JSON required')
            source['path']=str(mesh); inputs.append(mesh)
        if any(output==p.parent or output in p.parents for p in inputs):
            raise ValueError('output root must be isolated from source directories')
        return config

    def validate_form(self,data):
        with self.lock:
            self.current_preflight=None; self.validated.clear()
            config=self.normalize_form(data)
            ident=uuid.uuid4().hex; path=self.session_dir/(ident+'.json')
            write_json(path,config); effective=effective_config(path)
            report,cells=preflight(effective)
            if report['status']=='pass':
                self.validated[ident]=path; self.current_preflight=ident
            return dict(preflight_id=ident if report['status']=='pass' else None,report=report,
                cells=[dict(cell_id=c.cell_id,kind=c.kind,xy=c.xy) for c in cells.cells] if cells else [])

    def start_job(self,preflight_id):
        with self.lock:
            if preflight_id!=self.current_preflight or preflight_id not in self.validated:
                raise ValueError('Preflight đã hết hiệu lực; hãy kiểm tra lại')
            if any(j['status']=='running' for j in self.jobs.values()): raise ValueError('Một job đang chạy')
            if len(self.jobs)>=50: raise ValueError('Session job limit reached; restart UI')
            path=self.validated[preflight_id]; self.current_preflight=None
            job_id=uuid.uuid4().hex; self.jobs[job_id]=dict(status='running',stage='config',result=None)
            def update(stage):
                with self.lock: self.jobs[job_id]['stage']=stage
            def worker():
                try: result=run(path,progress=update)
                except Exception as error: result=dict(status='fail',output=None,error=str(error))
                with self.lock:
                    self.jobs[job_id].update(status=result['status'],stage='done' if result['status']=='pass' else 'failed',result=result)
            threading.Thread(target=worker,daemon=True).start()
            return dict(job_id=job_id)

    def job(self,ident):
        with self.lock:
            if ident not in self.jobs: raise ValueError('Unknown job')
            return copy.deepcopy(self.jobs[ident])

    def artifact(self,ident,name):
        if name not in ARTIFACTS: raise AccessError('Unsupported artifact')
        job=self.job(ident)
        if job['status']=='running' or not job['result'] or not job['result'].get('output'):
            raise ValueError('Artifact chưa sẵn sàng')
        output=self.permitted(job['result']['output'])
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

    def do_GET(self):
        try:
            parsed=urlsplit(self.path); path=parsed.path
            self.guard(api=path.startswith('/api/'))
            fonts = {f'/fonts/BeVietnamPro-{weight}.ttf': f'BeVietnamPro-{weight}.ttf'
                     for weight in ('Regular', 'Medium', 'SemiBold', 'Bold')}
            if path in fonts:
                return self.respond((ASSETS/'fonts'/fonts[path]).read_bytes(), content_type='font/ttf')
            if path in {'/','/app.js','/style.css','/wizard.css','/icon.png','/favicon.ico'}:
                name={'/':'index.html','/icon.png':'meshcherry-icon-v1.png',
                      '/favicon.ico':'meshcherry-icon-v1.ico'}.get(path,path[1:])
                mime={'index.html':'text/html','app.js':'text/javascript','style.css':'text/css','wizard.css':'text/css',
                      'meshcherry-icon-v1.png':'image/png','meshcherry-icon-v1.ico':'image/vnd.microsoft.icon'}[name]
                return self.respond((ASSETS/name).read_bytes(),content_type=mime)
            if path=='/api/bootstrap':
                return self.respond(dict(roots=[str(p) for p in self.server.roots],output_roots=[str(p) for p in self.server.output_roots],output_root=str(self.server.output_root),
                    tutorial_seen=tutorial_seen(self.server.tutorial_preferences),
                    sample_raster=str(self.server.sample_root/'sample_10_30.tif'),
                    sample_mesh=str(self.server.sample_root/'two_triangles.json')))
            query=parse_qs(parsed.query)
            if path=='/api/job': return self.respond(self.server.job(query.get('id',[''])[0]))
            if path=='/api/artifact':
                name=query.get('name',[''])[0]; artifact=self.server.artifact(query.get('id',[''])[0],name)
                mime='image/png' if name=='preview' else 'text/csv' if name=='csv' else 'application/json'
                return self.respond(artifact.read_bytes(),content_type=mime,
                    filename=ARTIFACTS[name] if query.get('download')==['1'] else None)
            self.respond(dict(error='Not found'),404)
        except AccessError as error: self.respond(dict(error=str(error)),403)
        except Exception as error: self.respond(dict(error=str(error)),400)

    def do_POST(self):
        try:
            path=urlsplit(self.path).path; self.guard(api=True,post=True)
            if self.headers.get('Transfer-Encoding'):
                return self.respond(dict(error='Transfer-Encoding unsupported'),400)
            if self.headers.get_content_type()!='application/json':
                return self.respond(dict(error='application/json required'),415)
            count=int(self.headers.get('Content-Length','0'))
            if not 0<count<=65536: return self.respond(dict(error='Request body size limit'),413)
            raw=self.rfile.read(count); self.body_consumed=True
            data=json.loads(raw,parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
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
                if source.suffix.lower() not in {'.tif','.tiff'}: raise ValueError('GeoTIFF required')
                with rasterio.open(source,'r') as src:
                    band=data.get('band',1)
                    if type(band) is not int or not 1<=band<=src.count: raise ValueError('band out of range')
                    crs=str(src.crs) if src.crs else None
                    try: metric_crs(crs); eligible=True
                    except Exception: eligible=False
                    report=dict(path=str(source),crs=crs,crs_wkt=src.crs.to_wkt() if src.crs else None,
                        shape=[src.height,src.width],band_count=src.count,band=band,dtype=src.dtypes[band-1],
                        nodata=str(src.nodatavals[band-1]),mask=[f.name for f in src.mask_flag_enums[band-1]],
                        scale=str(src.scales[band-1]),offset=str(src.offsets[band-1]),bounds=list(src.bounds),
                        projected_metre=eligible)
                return self.respond(report)
            if path=='/api/preflight': return self.respond(self.server.validate_form(data))
            if path=='/api/run':
                if set(data)!={'preflight_id'}: raise ValueError('Run accepts only preflight_id')
                return self.respond(self.server.start_job(data['preflight_id']),202)
            self.respond(dict(error='Not found'),404)
        except AccessError as error: self.respond(dict(error=str(error)),403)
        except Exception as error: self.respond(dict(error=str(error)),400)

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
