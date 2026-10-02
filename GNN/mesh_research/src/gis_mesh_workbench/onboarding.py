"""Version-only local onboarding preferences and packaged synthetic fixtures."""
import json
import os
from pathlib import Path
from importlib.resources import files

VERSION = 1


def sample_directory():
    return Path(str(files('gis_mesh_workbench').joinpath('sample_data'))).resolve(strict=True)


def preference_path():
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local/share')))
    return base / 'MeshCherry' / 'tutorial.json'


def tutorial_seen(path):
    if path is None:
        return False
    try:
        return json.loads(Path(path).read_text(encoding='utf-8')).get('tutorial_version_seen') == VERSION
    except (OSError, ValueError, AttributeError):
        return False


def acknowledge(path):
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps({'tutorial_version_seen': VERSION}), encoding='utf-8')
    temp.replace(path)
