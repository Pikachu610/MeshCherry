"""Launch MeshCherry locally from this source release."""
from pathlib import Path
import webbrowser
from gis_mesh_workbench.webui import WorkbenchServer

root = Path(__file__).resolve().parent
server = WorkbenchServer(workspace=root)
server.output_root = root / 'results'
server.output_root.mkdir(exist_ok=True)
url = server.origin + '/#token=' + server.token
print(url, flush=True)
print('Keep this console open. Ctrl+C stops MeshCherry.', flush=True)
webbrowser.open(url)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    server.server_close()
