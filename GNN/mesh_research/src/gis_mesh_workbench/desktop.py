"""Native window around the existing authenticated server; no JS/Python bridge."""
import argparse
import os
from pathlib import Path
import threading

from .onboarding import preference_path
from .gmsh_preferences import migrate_legacy
from .webui import ASSETS, WORKSPACE, WorkbenchServer


def _invoke_dialog(callback):
    """Windows common dialogs require STA; HTTP handlers run on Python threads."""
    if os.name != 'nt': return callback()
    from System.Threading import ApartmentState, Thread, ThreadStart
    result=[]; errors=[]
    def invoke():
        try: result.append(callback())
        except Exception as error: errors.append(error)
    thread=Thread(ThreadStart(invoke))
    thread.SetApartmentState(ApartmentState.STA)
    thread.IsBackground=True
    thread.Start(); thread.Join()
    if errors: raise errors[0]
    return result[0]


def launch(workspace=WORKSPACE, webview_module=None, preferences=None):
    if webview_module is None:
        try:
            import webview as webview_module
        except ImportError as error:
            raise RuntimeError('Desktop cần pywebview: pip install "mesh-research[desktop]". Không tự mở trình duyệt.') from error
    preferences=preference_path() if preferences is None else Path(preferences)
    gmsh_preferences=preferences.with_name('gmsh.json')
    migrate_legacy(Path(workspace)/'.scratch/gis-mesh-workbench/gmsh.json',gmsh_preferences)
    server = WorkbenchServer(workspace=workspace,tutorial_preferences=preferences,
        recent_preferences=preferences.with_name('recent-files.json'),
        gmsh_preferences=gmsh_preferences)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        window = webview_module.create_window('MeshCherry · Bản đồ nhỏ, ý tưởng lớn',
            server.origin + '/#token=' + server.token, width=1280, height=860,
            min_size=(820, 620))
        def native_picker(kind):
            filters = {'raster': ('Raster GeoTIFF / ASC (*.tif;*.tiff;*.asc)',),
                       'mesh': ('Mesh JSON (*.json)',),
                       'boundary': ('Shapefile boundary (*.shp)',),
                       'gmsh': ('Gmsh executable (*.exe)',)}
            return _invoke_dialog(lambda: window.create_file_dialog(
                webview_module.FileDialog.FOLDER if kind == 'output' else webview_module.FileDialog.OPEN,
                allow_multiple=False, file_types=filters.get(kind, ())))
        server.native_picker = native_picker
        def closing():
            with server.lock:
                if server.picker_active or any(job['status'] in {'running','stopping'} for job in server.jobs.values()):
                    return False
        window.events.closing += closing
        # No privileged js_api is exposed. Closing the window returns start().
        webview_module.start(gui='edgechromium', private_mode=True,
            icon=str(ASSETS / 'meshcherry-icon-v1.ico'))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='gis-mesh-workbench desktop')
    parser.add_argument('--workspace', type=Path, default=WORKSPACE)
    args = parser.parse_args(argv)
    try:
        launch(args.workspace)
    except Exception as error:
        print('Không mở được MeshCherry desktop:', error)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
