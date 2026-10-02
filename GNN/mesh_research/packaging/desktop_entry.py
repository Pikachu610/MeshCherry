"""Portable entry point. Self-test exercises the frozen backend, not a mock."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import http.client
import traceback

os.environ.setdefault('MPLBACKEND', 'Agg')


def self_test(report):
    from gis_mesh_workbench.adapters import structured_grid, import_mesh
    from gis_mesh_workbench.extraction import extract_attributes
    from gis_mesh_workbench.onboarding import sample_directory
    from gis_mesh_workbench.webui import WorkbenchServer, ASSETS
    import webview
    import clr
    checks = {}
    with tempfile.TemporaryDirectory(prefix='meshcherry-smoke-') as directory:
        server = WorkbenchServer(directory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection(*server.server_address, timeout=10)
            for asset in ('/', '/app.js', '/wizard.css', '/fonts/BeVietnamPro-Regular.ttf'):
                conn.request('GET', asset, headers={'Host': server.host})
                response = conn.getresponse()
                body = response.read()
                if response.status != 200 or not body:
                    raise RuntimeError('Missing packaged asset: '+asset)
            conn.close()
            checks['local_assets'] = 'pass'
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
    root = sample_directory()
    grid = structured_grid(extent=[500000,1000000,500002,1000001],
        cell_width_m=1,cell_height_m=1,analysis_crs='EPSG:32649')
    triangle = import_mesh(root/'two_triangles.json',analysis_crs='EPSG:32649')
    for name, cells, expected in (('grid',grid,[10,30]),('triangles',triangle,[25,15])):
        result = extract_attributes(root/'sample_10_30.tif',cells.cells,
            cell_crs='EPSG:32649',analysis_crs='EPSG:32649',band=1,
            semantics='intensive',value_units='synthetic-value')
        values = [row['value'] for row in result['cells']]
        if values != expected:
            raise RuntimeError(f'{name} result differs: {values}')
        checks[name] = values
    if 'Bé' in (ASSETS/'index.html').read_text(encoding='utf-8'):
        raise RuntimeError('Old UI wording remains')
    checks['pythonnet_import'] = 'pass'
    checks['frozen'] = bool(getattr(sys,'frozen',False))
    checks['executable'] = sys.executable
    report.write_text(json.dumps({'status':'pass','checks':checks},indent=2),encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--self-test', type=Path, metavar='REPORT_JSON')
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test(args.self_test)
        else:
            from gis_mesh_workbench.desktop import launch
            from gis_mesh_workbench.webui import WORKSPACE
            workspace = args.workspace or WORKSPACE
            workspace.mkdir(parents=True, exist_ok=True)
            launch(workspace)
        return 0
    except Exception:
        if args.self_test:
            args.self_test.write_text(json.dumps({'status':'fail','error':traceback.format_exc()}),encoding='utf-8')
        else:
            import ctypes
            log = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'MeshCherry' / 'startup-error.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(traceback.format_exc(), encoding='utf-8')
            ctypes.windll.user32.MessageBoxW(None,
                'Không mở được MeshCherry. Kiểm tra WebView2 Runtime và quyền ghi thư mục workspace.\n\n'+traceback.format_exc(),
                'MeshCherry · lỗi khởi động', 0x10)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
