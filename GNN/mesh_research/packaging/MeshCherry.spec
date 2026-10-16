# Build from this directory with the GIS environment.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

root = Path(SPECPATH).parent
sys.path.insert(0,str(root/'src'))
from gis_mesh_workbench.build_info import make_build_info, authored_manifest, json_bytes
identity_dir=Path(workpath)/'meshcherry-identity'
identity_dir.mkdir(parents=True,exist_ok=True)
for name,data in (('build-info.json',make_build_info(root)),
                  ('authored-source-manifest.json',authored_manifest(root))):
    (identity_dir/name).write_bytes(json_bytes(data))
assets = root/'src/gis_mesh_workbench'
datas = [(str(assets/'webui'),'gis_mesh_workbench/webui'),
         (str(assets/'sample_data'),'gis_mesh_workbench/sample_data')]
datas += [(str(path),'gis_mesh_workbench') for path in assets.glob('*.json')]
datas += [(str(path),'gis_mesh_workbench') for path in identity_dir.glob('*.json')]
binaries, hiddenimports = [], ['gis_mesh_workbench.cli']
for package in ('rasterio','webview'):
    data, binary, hidden = collect_all(package)
    datas += data; binaries += binary; hiddenimports += hidden
for distribution in ('pyshp','pywebview','pythonnet','numpy','scipy','rasterio',
                     'shapely','matplotlib','jsonschema','pillow','bottle','proxy_tools',
                     'clr_loader','affine','attrs','click','certifi','packaging',
                     'jsonschema_specifications','referencing','rpds_py'):
    datas += copy_metadata(distribution)

a = Analysis([str(root/'packaging/desktop_entry.py')], pathex=[str(root/'src')],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={'matplotlib':{'backends':['Agg']}},
    runtime_hooks=[], excludes=['PyQt5','PyQt6','PySide2','PySide6','tkinter','mesh_research'],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MeshCherry',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, icon=str(assets/'webui/meshcherry-icon-v1.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MeshCherry')
