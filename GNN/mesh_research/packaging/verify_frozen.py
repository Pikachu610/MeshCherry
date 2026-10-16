"""Compare executed frozen Python code and all resources with authored source."""
import argparse
import hashlib
import json
import marshal
from pathlib import Path
import types

from PyInstaller.archive.readers import CArchiveReader
from gis_mesh_workbench.build_info import authored_manifest, json_bytes, digest, make_build_info


def normalized(code):
    return code.replace(co_filename='<source>',co_consts=tuple(
        normalized(value) if isinstance(value,types.CodeType) else value for value in code.co_consts))


def verify(root, executable):
    root=Path(root).resolve();executable=Path(executable).resolve()
    source=authored_manifest(root);package=executable.parent/'_internal/gis_mesh_workbench'
    embedded=json.loads((package/'build-info.json').read_text(encoding='utf-8'))
    manifest=json.loads((package/'authored-source-manifest.json').read_text(encoding='utf-8'))
    errors=[]
    if manifest!=source:errors.append('embedded authored manifest differs from current source')
    expected=make_build_info(root)
    for key in ('app_version','authored_source_manifest_sha256','dependency_lock_sha256','source_file_count'):
        if embedded.get(key)!=expected[key]:errors.append('build identity differs: '+key)
    archive=CArchiveReader(str(executable))
    pyz_names=[name for name,value in archive.toc.items() if value[-1]=='z']
    if len(pyz_names)!=1:raise ValueError('Expected exactly one embedded PYZ')
    pyz=archive.open_embedded_archive(pyz_names[0]);checked=[]
    for row in source:
        relative=Path(row['path']);path=root/relative
        if relative.parts[:2]!=('src','gis_mesh_workbench'):continue
        target=package/Path(*relative.parts[2:])
        if path.suffix=='.py':
            parts=list(relative.with_suffix('').parts[1:])
            if parts[-1]=='__init__':parts.pop()
            module='.'.join(parts)
            if module not in pyz.toc:errors.append('missing frozen module: '+module);continue
            expected_code=normalized(compile(path.read_bytes(),str(path),'exec',dont_inherit=True,optimize=0))
            actual=normalized(pyz.extract(module))
            if expected_code!=actual:errors.append('frozen bytecode differs: '+module)
            checked.append(dict(path=row['path'],kind='normalized executed bytecode'))
        elif path.name!='README.md':
            if not target.is_file() or digest(target)!=row['sha256']:errors.append('packaged resource differs: '+row['path'])
            checked.append(dict(path=row['path'],kind='byte-for-byte resource'))
    if 'desktop_entry' not in archive.toc:errors.append('missing frozen desktop entry')
    else:
        expected_code=normalized(compile((root/'packaging/desktop_entry.py').read_bytes(),'<entry>','exec',dont_inherit=True,optimize=0))
        actual=normalized(marshal.loads(archive.extract('desktop_entry')))
        if expected_code!=actual:errors.append('desktop entry bytecode differs')
    return dict(status='fail' if errors else 'pass',executable_sha256=digest(executable),
                source_manifest_sha256=hashlib.sha256(json_bytes(source)).hexdigest(),
                build_identity=embedded,checked=checked,errors=errors,
                comparison='recursive code objects with only co_filename normalized; resources use SHA-256')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('executable',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    result=verify(Path(__file__).resolve().parents[1],args.executable)
    args.output.write_bytes(json_bytes(result));print(json.dumps(dict(status=result['status'],checked=len(result['checked']),errors=result['errors'])))
    raise SystemExit(0 if result['status']=='pass' else 1)
