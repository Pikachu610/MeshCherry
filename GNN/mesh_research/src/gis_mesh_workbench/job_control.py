"""Job-scoped cancellation, bounded actual logs and phase-only observations."""
from collections import deque
from contextlib import contextmanager
import codecs
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
import uuid

_local=threading.local()
MAX_LOG_BYTES=16*1024*1024


class Cancelled(BaseException):
    """Control flow like KeyboardInterrupt, never swallowed as a validation error."""


def current(): return getattr(_local,'control',None)


@contextmanager
def job_scope(control):
    previous=current(); _local.control=control
    try: yield control
    finally: _local.control=previous


def checkpoint():
    control=current()
    if control is not None and control.cancel.is_set(): raise Cancelled('Đã dừng theo yêu cầu')


def observe(stage,completed=None,total=None):
    checkpoint()
    control=current()
    if control is not None:
        control.stage(stage)
        if total and completed is not None: control.measure(completed,total)


class JobControl:
    def __init__(self,lock=None,clock=time.monotonic):
        self.lock=lock if lock is not None else threading.RLock()
        self.clock=clock; self.started=clock(); self.finished=None
        self.cancel=threading.Event(); self.committed=False; self.committing=False; self.process=None
        self.phase='config'; self.phase_started=self.started
        self.phase_percent=None; self.points=deque(maxlen=8)
        self.lines=deque(maxlen=200); self.line_count=0; self.output=None
        self.long_lines=False
        self.has_gmsh_log=False

    def request_cancel(self):
        with self.lock:
            if self.committed or self.committing or self.finished is not None: return False
            self.cancel.set(); return True

    def stage(self,value):
        with self.lock:
            if value!=self.phase:
                self.phase=value; self.phase_started=self.clock()
                self.phase_percent=None; self.points.clear()

    def measure(self,completed,total):
        if not total: return
        percent=100*completed/total
        if not math.isfinite(percent) or not 0<=percent<=100: return
        with self.lock:
            if self.phase_percent is not None and percent<self.phase_percent: self.points.clear()
            self.phase_percent=percent
            now=self.clock()
            if not self.points or (percent>self.points[-1][1] and now-self.points[-1][0]>=.25):
                self.points.append((now,percent))

    def log(self,line):
        with self.lock:
            self.line_count+=1
            self.long_lines |= len(line)>2000
            self.lines.append(line[:2000])
            while sum(len(s.encode('utf-8')) for s in self.lines)>256*1024: self.lines.popleft()
            match=re.search(r'\bMeshing ([123])D\b',line,re.I)
            if match and 'done meshing' not in line.lower(): self.stage('gmsh_'+match[1]+'d')
            progress=re.search(r'\[\s*(\d+(?:\.\d+)?)%\s*\]',line)
            if progress and self.phase.startswith('gmsh_'): self.measure(float(progress[1]),100)
            if 'done meshing' in line.lower() and self.phase.startswith('gmsh_'): self.measure(100,100)

    def snapshot(self):
        with self.lock:
            now=self.finished if self.finished is not None else self.clock()
            eta=None
            # Gmsh bracketed percentages may count curves/surfaces rather than
            # equal work. Keep the observed number, but never extrapolate its ETA.
            if (not self.phase.startswith('gmsh_') and not self.cancel.is_set()
                    and self.finished is None and len(self.points)>=3):
                a,p=self.points[0]; b,q=self.points[-1]
                rates=[(y[1]-x[1])/(y[0]-x[0]) for x,y in zip(self.points,list(self.points)[1:]) if y[0]>x[0]]
                stable=rates and min(rates)>0 and max(rates)/min(rates)<=4
                if stable and b-a>=2 and 0<q-p and q<100 and now-b<=10:
                    remaining=(100-q)*(b-a)/(q-p)-(now-b)
                    if remaining>0:
                        eta=dict(scope=self.phase,basis='observed_phase_rate',
                            low_seconds=max(1,remaining*.5),high_seconds=max(2,remaining*2))
            return dict(phase=self.phase,phase_percent=self.phase_percent,
                elapsed_seconds=max(0,now-self.started),cancel_requested=self.cancel.is_set(),
                eta=eta,log_lines=list(self.lines),log_line_count=self.line_count,
                log_dropped=max(0,self.line_count-len(self.lines)),has_gmsh_log=self.has_gmsh_log)

    def log_snapshot(self,cursor=0):
        if type(cursor) is not int or cursor<0: raise ValueError('Invalid log cursor')
        with self.lock:
            if cursor>self.line_count: raise ValueError('Log cursor is ahead of stream')
            base=self.line_count-len(self.lines); start=max(base,cursor); text=''; next_cursor=start
            for line in list(self.lines)[start-base:]:
                candidate=text+line+'\n'
                if len(json.dumps(candidate,ensure_ascii=False).encode('utf-8'))>24000: break
                text=candidate; next_cursor+=1
            return dict(text=text,next_cursor=next_cursor,dropped=max(0,base-cursor),
                truncated_lines=self.long_lines,complete=self.finished is not None)

    @contextmanager
    def publication(self):
        with self.lock:
            if self.cancel.is_set(): raise Cancelled('Đã dừng trước khi lưu kết quả hoàn chỉnh')
            self.committing=True
            try:
                yield
                self.committed=True
            finally:self.committing=False


def _terminate(process,owned_job=None):
    """Only the process/group just created here; never a name-wide kill."""
    if owned_job is not None:
        owned_job.close()
        process.wait(timeout=5)
        return
    if process.poll() is not None: return
    if os.name=='nt':
        try:
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,
                timeout=5,check=False,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except (OSError,subprocess.TimeoutExpired): pass
    else:
        try: os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError: pass
    if process.poll() is None: process.kill()
    process.wait(timeout=5)


def run_logged(command,path,timeout,*,cwd=None,env=None):
    control=current(); path=Path(path)
    if control is None:
        with path.open('w',encoding='utf-8') as log:
            return subprocess.run(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,
                timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    checkpoint(); pending=''; decoder=codecs.getincrementaldecoder('utf-8')('replace')
    def drain(reader,final=False):
        nonlocal pending
        raw=reader.read(65536)
        pending+=decoder.decode(raw,final=final)
        while True:
            match=re.search(r'[\r\n]',pending)
            if match is None: break
            offset=match.start()
            if pending[offset:]=='\r' and not final: break
            length=2 if pending[offset:offset+2]=='\r\n' else 1
            control.log(pending[:offset]);pending=pending[offset+length:]
        # Bound even a stream with no newline, without changing the disk log.
        if len(pending)>4096: control.log(pending); pending=''
        if final and pending: control.log(pending); pending=''
    kwargs=dict(start_new_session=True) if os.name!='nt' else dict(
        creationflags=subprocess.CREATE_NO_WINDOW|0x4)  # CREATE_SUSPENDED
    with path.open('wb') as log, path.open('rb') as reader:
        owned_job=None
        if os.name=='nt':
            from .windows_job import WindowsJob
            owned_job=WindowsJob()
        try:
            process=subprocess.Popen(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,**kwargs)
        except BaseException:
            if owned_job: owned_job.close()
            raise
        with control.lock: control.process=process
        started=time.monotonic()
        try:
            if owned_job:
                try:
                    owned_job.assign(process)
                    checkpoint()
                    owned_job.resume(process)
                except BaseException:
                    _terminate(process)
                    raise
            if path.name=='gmsh.log': control.has_gmsh_log=True
            while process.poll() is None:
                drain(reader); checkpoint()
                if path.stat().st_size>MAX_LOG_BYTES: raise ValueError('Gmsh log vượt ngân sách 16 MiB')
                if time.monotonic()-started>timeout: raise subprocess.TimeoutExpired(command,timeout)
                control.cancel.wait(.1)
            checkpoint()
            result=subprocess.CompletedProcess(command,process.returncode)
        finally:
            try:
                _terminate(process,owned_job)
                if path.stat().st_size>MAX_LOG_BYTES:
                    log.truncate(MAX_LOG_BYTES); log.flush()
                # Drain all remaining bounded data after the child has been reaped.
                while reader.tell()<path.stat().st_size: drain(reader)
                drain(reader,final=True)
            finally:
                with control.lock: control.process=None
    checkpoint()
    return result


def finalize_manifest(output,manifest):
    """Cancellation and successful publication have one shared linearization point."""
    from .extraction import _digest
    from .runner import write_json
    control=current(); output=Path(output)
    try:
        checkpoint()
        manifest['artifact_hashes']={p.relative_to(output).as_posix():_digest(p)
            for p in output.rglob('*') if p.is_file() and p.name!='manifest.json'}
        guard=control.publication() if control is not None else _no_guard()
        with guard:
            temporary=output/('manifest-'+uuid.uuid4().hex+'.tmp')
            try:
                write_json(temporary,manifest); temporary.replace(output/'manifest.json')
            finally: temporary.unlink(missing_ok=True)
    except Cancelled as error:
        manifest.update(status='cancelled',error=str(error),artifact_hashes={},
                        cancelled_stage=control.phase if control else 'unknown')
        write_json(output/'qa.json',dict(status='cancelled',error=str(error)))
        write_json(output/'manifest.json',manifest)
    return manifest


@contextmanager
def _no_guard(): yield
