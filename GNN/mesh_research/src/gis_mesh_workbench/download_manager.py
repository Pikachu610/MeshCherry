"""Desktop artifact downloads through a user-selected Save As destination."""
import os
from pathlib import Path
import tempfile
import threading
import uuid
from .job_control import JobControl, Cancelled, job_scope, checkpoint


class DownloadManager:
    """Copy server-authorized artifacts without exposing arbitrary client paths."""

    CHUNK_BYTES = 1024 * 1024
    TERMINAL = {'saved', 'cancelled', 'error'}
    HISTORY_LIMIT = 50

    def __init__(self, server):
        self.server = server
        self._lock = threading.RLock()
        self._downloads = {}
        self._active_id = None

    @property
    def active(self):
        with self._lock:
            return self._active_id is not None

    def start(self, data):
        from .download_sources import resolve
        filename,producer,generated=resolve(self.server,data)
        ident = uuid.uuid4().hex
        state = dict(download_id=ident, state='choosing', total_bytes=None,
                     bytes_written=0, path=None, error=None, _cancel=False,
                     _control=JobControl())
        with self.server.lock, self._lock:
            if self._active_id is not None or self.server.picker_active:
                raise ValueError('Hãy chờ hộp thoại hoặc lượt tải xuống hiện tại')
            if self.server.native_save is None:
                raise ValueError('Hộp thoại lưu tệp chỉ có trong bản desktop')
            self._active_id = ident
            for old_id in list(self._downloads):
                if len(self._downloads) < self.HISTORY_LIMIT:
                    break
                if self._downloads[old_id]['state'] in self.TERMINAL:
                    del self._downloads[old_id]
            self._downloads[ident] = state
            # Reserve the shared picker slot before the worker starts, preventing a
            # concurrent native input picker from opening over Save As.
            self.server.picker_active = True
        worker = threading.Thread(target=self._copy, args=(ident, filename,producer,generated), daemon=True,
                                  name='meshcherry-download-'+ident[:8])
        worker.start()
        return {'download_id': ident}

    def status(self, ident):
        with self._lock:
            state = self._downloads.get(ident)
            if state is None:
                raise ValueError('Unknown download')
            return {key: value for key, value in state.items() if not key.startswith('_')}

    def cancel(self, ident):
        with self._lock:
            state = self._downloads.get(ident)
            if state is None:
                raise ValueError('Unknown download')
            if state['state'] in self.TERMINAL:
                return {'download_id': ident, 'accepted': False, 'state': state['state']}
            state['_cancel'] = True
            state['_control'].request_cancel()
            state['state'] = 'cancelling'
            return {'download_id': ident, 'accepted': True, 'state': 'cancelling'}

    def _cancelled(self, ident):
        with self._lock:
            state = self._downloads[ident]
            if state['_cancel']:
                state['state'] = 'cancelled'
                return True
            return False

    def _copy(self, ident, filename,producer,generated):
        temporary = None;source=None;owns_picker=True
        try:
            selection = self.server.native_save(filename)
            with self.server.lock:
                self.server.picker_active=False;owns_picker=False
            if not selection:
                with self._lock:
                    self._downloads[ident]['state'] = 'cancelled'
                return
            if self._cancelled(ident):return
            with self._lock:self._downloads[ident]['state']='preparing'
            with job_scope(self._downloads[ident]['_control']):
                checkpoint();source=Path(producer()).resolve(strict=True);checkpoint()
            if not source.is_file():raise ValueError('Kết quả xuất không phải tệp')
            before=source.stat()
            with self._lock:self._downloads[ident]['total_bytes']=before.st_size
            if not isinstance(selection, (tuple, list)) or len(selection) != 1:
                raise ValueError('Save As must return one selected destination')
            destination = Path(selection[0]).expanduser()
            if not destination.is_absolute():
                raise ValueError('Save As must return an absolute destination')
            if Path(filename).suffix and destination.suffix.lower()!=Path(filename).suffix.lower():
                raise ValueError('Giữ phần mở rộng '+Path(filename).suffix+' cho tệp kết quả')
            if destination.is_symlink():
                raise ValueError('Cannot save through a symbolic link')
            destination = destination.resolve(strict=False)
            # Preserve retained evidence from every job, not just the current source.
            with self.server.lock:
                retained=[self.server.session_dir]
                for job in self.server.jobs.values():
                    result=job.get('result') or {}
                    for raw in (result.get('output'),job.get('batch_output')):
                        if raw:retained.append(Path(raw).resolve(strict=False))
            if any(destination==folder or folder in destination.parents for folder in retained):
                raise ValueError('Chọn vị trí lưu bên ngoài thư mục kết quả nội bộ')
            if destination.exists():
                # A hard-link alias outside retained folders is the same artifact.
                for folder in retained:
                    if folder.is_dir():
                        for artifact in folder.rglob('*'):
                            if artifact.is_file() and os.path.samefile(destination,artifact):
                                raise ValueError('Không ghi đè liên kết tới kết quả nội bộ')
            if destination == source or (destination.exists() and os.path.samefile(destination, source)):
                raise ValueError('Cannot overwrite the source artifact')
            for selected in self.server.selected_files | self.server.protected_inputs:
                selected = Path(selected).resolve(strict=False)
                if destination == selected or (destination.exists() and selected.exists()
                                                and os.path.samefile(destination, selected)):
                    raise ValueError('Cannot overwrite a selected input file')
            if not destination.parent.is_dir():
                raise ValueError('Destination folder does not exist')
            fd, temporary = tempfile.mkstemp(prefix='.'+destination.name+'.',
                suffix='.partial', dir=destination.parent)
            os.close(fd)
            with self._lock:
                self._downloads[ident]['state'] = 'copying'
            with source.open('rb') as input_stream, open(temporary, 'wb') as output_stream:
                while True:
                    if self._cancelled(ident):
                        return
                    chunk = input_stream.read(self.CHUNK_BYTES)
                    if not chunk:
                        break
                    output_stream.write(chunk)
                    with self._lock:
                        self._downloads[ident]['bytes_written'] += len(chunk)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            after=source.stat()
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                raise ValueError('Tệp nguồn đã thay đổi trong khi lưu')
            # Serialize the final cancellation check and replacement with cancel().
            with self._lock:
                state = self._downloads[ident]
                if state['_cancel']:
                    state['state'] = 'cancelled'
                    return
                os.replace(temporary, destination)
                temporary = None
                state.update(state='saved', path=str(destination),
                             bytes_written=state['total_bytes'])
        except (Exception,Cancelled) as error:
            with self._lock:
                state = self._downloads[ident]
                state['state'] = 'cancelled' if state['_cancel'] else 'error'
                state['error'] = str(error)
        finally:
            if generated and source is not None:
                try:source.unlink()
                except OSError:pass
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            with self.server.lock, self._lock:
                if owns_picker:self.server.picker_active = False
                if self._active_id == ident:
                    self._active_id = None
