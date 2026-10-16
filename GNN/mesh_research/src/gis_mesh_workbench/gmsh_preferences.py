"""Remembered executable metadata; never permission or current readiness."""
import json
import os
from pathlib import Path
import tempfile


def read_saved(path):
    try:
        with Path(path).open('rb') as stream:
            raw=stream.read(65537)
        if len(raw)>65536: return {}
        data=json.loads(raw)
        if not isinstance(data,dict): return {}
        saved={key:data[key] for key in ('executable','version','executable_sha256')
               if isinstance(data.get(key),str) and data[key]}
        if not Path(saved.get('executable','')).is_absolute(): return {}
        return saved
    except (OSError,ValueError): return {}


def save_checked(path,report,*,only_if_absent=False):
    """Publish complete JSON atomically, with no-clobber legacy migration."""
    path=Path(path)
    saved={key:report[key] for key in ('executable','version','executable_sha256')}
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',
                dir=path.parent,prefix='.gmsh-',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name)
            json.dump(saved,stream,ensure_ascii=False,allow_nan=False,indent=2)
            stream.write('\n')
        if only_if_absent:
            # Atomic no-replace publication, including concurrent desktop launches.
            os.link(temporary,path)
        else:
            temporary.replace(path)
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


def migrate_legacy(legacy,destination):
    """Copy historical metadata only; existing user preferences always win."""
    destination=Path(destination)
    if destination.exists(): return
    saved=read_saved(legacy)
    if set(saved)!={'executable','version','executable_sha256'}: return
    try:
        save_checked(destination,saved,only_if_absent=True)
    except OSError:
        # Migration is optional (e.g. no hard-link support). Explicit check/save
        # reports write failures normally; startup must remain usable.
        return
