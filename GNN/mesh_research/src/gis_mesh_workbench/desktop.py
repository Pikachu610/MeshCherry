"""Native window around the existing authenticated server; no JS/Python bridge."""
import argparse
import os
from pathlib import Path
import threading

from .onboarding import preference_path
from .gmsh_preferences import migrate_legacy
from .native_chrome import install_window_controls
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


def launch(workspace=WORKSPACE, webview_module=None, preferences=None, startup_project=None):
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
        gmsh_preferences=gmsh_preferences,startup_project=startup_project)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        windows_chrome = os.name == 'nt'
        # pywebview uses this selector only for frameless windows. Keep the drag
        # target explicit so titlebar buttons and menus remain interactive.
        if windows_chrome:
            webview_module.settings['DRAG_REGION_SELECTOR'] = '.pywebview-drag-region'
        window = webview_module.create_window('MeshCherry',
            server.origin + '/#token=' + server.token, width=1280, height=860,
            min_size=(820, 620), frameless=windows_chrome,
            easy_drag=False if windows_chrome else True)
        def native_picker(kind):
            filters = {'raster': ('Raster GeoTIFF and ASC (*.tif;*.tiff;*.asc)',),
                       'raster_many': ('Raster GeoTIFF and ASC (*.tif;*.tiff;*.asc)',),
                       'mesh': ('Mesh JSON (*.json)',),
                       'boundary': ('Shapefile boundary (*.shp)',),
                       'gmsh': ('Gmsh executable (*.exe)',)}
            return _invoke_dialog(lambda: window.create_file_dialog(
                webview_module.FileDialog.FOLDER if kind == 'output' else webview_module.FileDialog.OPEN,
                allow_multiple=kind=='raster_many', file_types=filters.get(kind, ())))
        server.native_picker = native_picker
        def native_save(filename):
            suffix=Path(filename).suffix.lower()
            file_types=(f'Artifact (*{suffix})',) if suffix else ()
            return _invoke_dialog(lambda: window.create_file_dialog(
                webview_module.FileDialog.SAVE, allow_multiple=False,
                save_filename=filename, file_types=file_types))
        server.native_save = native_save
        def closing():
            with server.lock:
                if (server.picker_active or server.downloads.active or
                        server.document_active or server.preflight_inflight or
                        any(job['status'] in {'running','stopping'} for job in server.jobs.values())):
                    return False
        window.events.closing += closing
        # Register before start(): the server exposes controls to HTML only if
        # this native callback exists, and pywebview creates its native form in
        # start().
        server.native_window_control = install_window_controls(
            window, closing, windows_chrome=windows_chrome)
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
