"""Local bounded MRU metadata, never startup filesystem authority."""
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

KINDS={'raster':{'.tif','.tiff','.asc'},'mesh':{'.json'},'boundary':{'.shp'}}


def read_recent(path):
    path=Path(path)
    try:
        if path.stat().st_size>65536: return []
        data=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data,list): return []
        result=[]
        for item in data[:10]:
            if not isinstance(item,dict) or item.get('kind') not in KINDS: continue
            file=Path(item.get('path',''))
            if not file.is_absolute() or file.suffix.lower() not in KINDS[item['kind']]: continue
            result.append(dict(id=hashlib.sha256((item['kind']+str(file)).encode()).hexdigest()[:24],
                kind=item['kind'],path=str(file),opened=str(item.get('opened',''))[:40]))
        return result
    except (OSError,ValueError,TypeError): return []


def remember(path,kind,file):
    file=Path(file).resolve(strict=True)
    if kind not in KINDS or not file.is_file() or file.suffix.lower() not in KINDS[kind]: return
    items=[dict(kind=kind,path=str(file),opened=datetime.now(timezone.utc).isoformat()),
        *[item for item in read_recent(path) if not (item['kind']==kind and item['path']==str(file))]]
    save_recent(path,items[:10])


def save_recent(path,items):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)
