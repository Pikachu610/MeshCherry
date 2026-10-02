"""Native window around the existing authenticated server; no JS/Python bridge."""
import argparse
from pathlib import Path
import threading

from .onboarding import preference_path
from .webui import ASSETS, WORKSPACE, WorkbenchServer


def launch(workspace=WORKSPACE, webview_module=None, preferences=None):
    if webview_module is None:
        try:
            import webview as webview_module
        except ImportError as error:
            raise RuntimeError('Desktop cần pywebview: pip install "mesh-research[desktop]". Không tự mở trình duyệt.') from error
    server = WorkbenchServer(workspace=workspace,
        tutorial_preferences=preference_path() if preferences is None else preferences)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        window = webview_module.create_window('MeshCherry · Bản đồ nhỏ, ý tưởng lớn',
            server.origin + '/#token=' + server.token, width=1280, height=860,
            min_size=(820, 620))
        def closing():
            with server.lock:
                if any(job['status']=='running' for job in server.jobs.values()):
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
