"""Launch MeshCherry in its native desktop window."""
from pathlib import Path
from gis_mesh_workbench.desktop import launch

if __name__ == "__main__":
    launch(Path(__file__).resolve().parent)
