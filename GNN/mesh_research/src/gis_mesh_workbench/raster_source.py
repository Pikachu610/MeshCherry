"""Read-only raster datasets with explicit, same-directory companion identity."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

import rasterio

RASTER_SUFFIXES = {'.tif', '.tiff', '.asc'}
RASTER_DRIVERS = {'GTiff', 'AAIGrid'}
HASH_SCOPE = 'raster_dataset_bundle_v1'
_read_authorizer = ContextVar('meshcherry_raster_read_authorizer', default=None)


@contextmanager
def authorized_reads(check):
    """Apply the desktop session's path grants throughout one worker thread.

    CLI callers have no session restriction. Desktop snapshots and every open
    recheck each exact companion, including files created after preflight.
    """
    token = _read_authorizer.set(check)
    try: yield
    finally: _read_authorizer.reset(token)


def bundle_paths(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.suffix.lower() not in RASTER_SUFFIXES:
        raise ValueError('Chọn raster GeoTIFF (.tif/.tiff) hoặc ESRI ASCII Grid (.asc)')
    files = [path]
    # Enumerate only recognized names; never grant a whole directory. Include
    # absent-to-present changes on each snapshot, even if GDAL did not list an
    # optional world file while an internal transform took precedence.
    suffix = path.suffix.lower()
    names = [str(path)+ending for ending in ('.aux.xml', '.msk', '.ovr', '.msk.ovr', '.ovr.aux.xml')]
    names += [str(path.with_suffix(ending)) for ending in ('.prj', '.aux', '.wld')]
    if suffix != '.asc':
        names += [str(path.with_suffix('.tfw')), str(path)+'w']
    for name in dict.fromkeys(names):
        sibling = Path(name)
        if not sibling.exists() and not sibling.is_symlink():
            continue
        resolved = sibling.resolve(strict=True)
        if resolved != sibling or resolved.parent != path.parent or not resolved.is_file():
            raise ValueError('Raster sidecar phải là file trực tiếp cùng thư mục với raster')
        if (str(sibling).lower().endswith(('.xml', '.prj', '.wld', '.tfw', '.tifw', '.tiffw'))
                and resolved.stat().st_size > 1024*1024):
            raise ValueError('Raster metadata vượt 1 MiB')
        files.append(resolved)
    check = _read_authorizer.get()
    if check is not None:
        for file in files: check(file)
    return tuple(files)


def snapshot(path):
    from .extraction import _digest
    return {str(file): _digest(file) for file in bundle_paths(path)}


def assert_unchanged(path, before):
    if snapshot(path) != before:
        raise ValueError('Raster hoặc file đi kèm thay đổi trong khi xử lý')


def raster_snapshots(config):
    source=config.get('cell_source', {})
    paths=[config.get('raster_path'),source.get('roi_raster_path'),source.get('dem_raster_path'),
           config.get('resolution_reference', {}).get('path')]
    return {str(Path(p)):snapshot(p) for p in paths if p}


def assert_rasters_unchanged(before):
    for path, expected in before.items():
        assert_unchanged(path, expected)


def check_driver(src):
    if src.driver not in RASTER_DRIVERS:
        raise ValueError('Raster phải là GeoTIFF hoặc ESRI ASCII Grid; đổi đuôi tệp không đổi định dạng')


@contextmanager
def open_raster(path, require_crs=True):
    path = Path(path).resolve(strict=True)
    allowed = set(bundle_paths(path))
    # ASCII has no declared binary dtype. Preserve decimal precision on read.
    with rasterio.Env(AAIGRID_DATATYPE='Float64'):
        with rasterio.open(path, 'r') as src:
            check_driver(src)
            if path.suffix.lower() == '.asc' and src.driver != 'AAIGrid':
                raise ValueError('File .asc phải có cấu trúc ESRI ASCII Grid')
            if path.suffix.lower() != '.asc' and src.driver != 'GTiff':
                raise ValueError('File .tif/.tiff phải có định dạng GeoTIFF')
            # Fail closed if GDAL reports another dependency, rather than making
            # an unrecorded/unauthorized external file part of the result.
            if any(Path(file).resolve(strict=True) not in allowed for file in src.files):
                raise ValueError('GDAL sử dụng file ngoài bộ raster được hỗ trợ')
            if require_crs and src.crs is None:
                kind = 'ASC' if path.suffix.lower() == '.asc' else 'Raster'
                raise ValueError(kind+' thiếu CRS. Với ASC, đặt file .prj cùng tên cạnh .asc; CRS tính toán không thay thế CRS nguồn.')
            yield src
