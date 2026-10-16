"""Deterministic authored-source identity and frozen build provenance."""
from functools import lru_cache
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform
import sys

from ._version import __version__


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            result.update(block)
    return result.hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode('utf-8')


def authored_manifest(root):
    root = Path(root).resolve()
    paths = []
    for relative in ('src/gis_mesh_workbench', 'tests', 'packaging'):
        paths.extend(path for path in (root/relative).rglob('*') if path.is_file()
                     and not {'__pycache__','graphify-out','.pytest_cache'}.intersection(path.parts)
                     and path.suffix not in {'.pyc','.pyo'})
    paths.extend(root/name for name in ('pyproject.toml','README.md'))
    return [dict(path=path.relative_to(root).as_posix(),bytes=path.stat().st_size,sha256=digest(path))
            for path in sorted(paths)]


def dependency_versions():
    result = {}
    for name in ('pyinstaller','numpy','scipy','rasterio','shapely','pyshp','pywebview','matplotlib'):
        try: result[name] = version(name)
        except PackageNotFoundError: result[name] = None
    return result


def make_build_info(root):
    import rasterio
    import shapely
    root = Path(root)
    manifest = authored_manifest(root)
    return dict(schema_version='meshcherry-build-v1',app_version=__version__,
                authored_source_manifest_sha256=hashlib.sha256(json_bytes(manifest)).hexdigest(),
                dependency_lock_sha256=digest(root/'packaging/requirements-v2.lock.txt'),
                python_version=platform.python_version(),dependencies=dependency_versions(),
                native_gis_versions=dict(gdal=rasterio.__gdal_version__,proj=rasterio.__proj_version__,
                                         geos=shapely.geos_version_string),
                source_file_count=len(manifest))


@lru_cache(maxsize=1)
def identity():
    if getattr(sys, 'frozen', False):
        data = json.loads(Path(__file__).with_name('build-info.json').read_text(encoding='utf-8'))
        if data.get('app_version') != __version__:
            raise ValueError('Packaged application version disagrees with build metadata')
    else:
        data = make_build_info(Path(__file__).resolve().parents[2])
    return data


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[2]
    for name,data in (('authored-source-manifest.json',authored_manifest(root)),
                      ('build-info.json',make_build_info(root))):
        (args.output/name).write_bytes(json_bytes(data))
