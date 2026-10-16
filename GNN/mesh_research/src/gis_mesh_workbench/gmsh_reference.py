"""Bounded public research CLI integration; no MSH parser or ROI reader."""
import json
import os
from pathlib import Path
import subprocess
import sys

from .adapters import import_mesh, _json
from .extraction import _digest
from .spatial import metric_crs

PRESET = 'inclined_plane_250m_v0'
VERSION = '4.15.2'
TIMEOUT_SECONDS = 45
PRESET_PATH = Path(__file__).resolve().parents[2] / 'configs/gmsh-inclined-plane-250m.json'


def inspect_preset(analysis_crs, preset=PRESET):
    if getattr(sys, 'frozen', False):
        raise ValueError('Gmsh demo không có trong EXE v2. Dùng bản source với Gmsh cài ngoài; không có fallback âm thầm.')
    if preset != PRESET:
        raise ValueError('unsupported Gmsh reference preset')
    source = PRESET_PATH.resolve()
    digest = _digest(source)
    config = _json(source)
    generator = config.get('generator', {})
    domain = config.get('domain', {})
    if (config.get('schema_version') != 'mesh-run-config-v0'
            or generator.get('name') != 'gmsh'
            or generator.get('parameters') != dict(target_edge=250.0,algorithm=5,random_seed=1)
            or domain != dict(name='inclined-plane',kind='synthetic_inclined_plane',
                             width=1000.0,height=1000.0,base_elevation=100.0,slope_x=.01,slope_y=.005)
            or config.get('coordinate_units') != 'm'
            or metric_crs(config.get('crs')) != metric_crs('EPSG:32649')):
        raise ValueError('reference preset contract changed; revalidation required')
    if metric_crs(analysis_crs) != metric_crs(config['crs']):
        raise ValueError('Gmsh preset CRS mismatch; relabeling forbidden')
    # Existing research CLI treats executable paths relative to its documented
    # package working directory, not the config directory. Preserve that policy
    # while making the generated child config independent of the caller's CWD.
    executable = (source.parent.parent / generator['executable']).resolve()
    if not executable.is_file():
        raise ValueError('reference Gmsh executable missing')
    version = subprocess.run([str(executable),'-version'],capture_output=True,text=True,
                             timeout=5,check=False)
    if version.returncode or (version.stdout.strip() or version.stderr.strip()) != VERSION:
        raise ValueError('reference Gmsh version mismatch; expected '+VERSION)
    if _digest(source) != digest:
        raise ValueError('preset changed during inspection')
    config['generator']['executable'] = str(executable)
    return config, dict(preset=PRESET,preset_source_path=str(source),preset_source_sha256=digest,
        generator_version=VERSION,target_edge_m=250,mesh_generation_required=True,cell_count=None,
        timeout_seconds=TIMEOUT_SECONDS,
        preset_executable_resolution='relative to mesh_research package directory')


def public_command(config_path, log_path):
    """Kill whole spawned process tree on timeout, retaining console evidence."""
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[1])
    env['PYTHONPATH'] = src + os.pathsep + env.get('PYTHONPATH','')
    from .job_control import current, run_logged
    if current() is not None:
        result=run_logged([sys.executable,'-m','mesh_research','run',str(config_path)],
            log_path,TIMEOUT_SECONDS,env=env)
        if result.returncode: raise ValueError(f'public mesh-research exited {result.returncode}; see meshing/research-console.log')
        return
    kwargs = {'start_new_session':True} if os.name != 'nt' else {}
    with Path(log_path).open('w',encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable,'-m','mesh_research','run',str(config_path)],
            stdout=log,stderr=subprocess.STDOUT,env=env,**kwargs)
        try:
            code = process.wait(timeout=TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                    stdout=log,stderr=subprocess.STDOUT,timeout=5,check=False)
            else:
                import signal
                os.killpg(process.pid,signal.SIGKILL)
            process.wait(timeout=5)
            raise ValueError('Gmsh reference timeout; see meshing/research-console.log')
    if code:
        raise ValueError(f'public mesh-research exited {code}; see meshing/research-console.log')


def validate_child(child, analysis_crs, max_cells):
    child = Path(child)
    manifest_path, qa_path = child/'manifest.json', child/'qa/qa.json'
    manifest, qa = _json(manifest_path), _json(qa_path)
    generator = manifest.get('generator',{})
    if (manifest.get('schema_version') != 'mesh-run-manifest-v0' or manifest.get('status') != 'pass'
            or qa.get('schema_version') != 'mesh-qa-v0' or qa.get('status') != 'pass'
            or generator.get('name') != 'gmsh' or generator.get('version') != VERSION
            or generator.get('parameters',{}).get('target_edge') != 250
            or metric_crs(manifest.get('crs')) != metric_crs(analysis_crs)
            or manifest.get('units',{}).get('coordinate') != 'm'):
        raise ValueError('child Gmsh manifest/QA/generator/CRS/size contract failed')
    required = {'positive_unique_triangles','manifold_interfaces','edge_only_adjacency','connected_cell_graph'}
    gates = qa.get('hard_gates',[])
    if not required.issubset({g.get('name') for g in gates if g.get('status') == 'pass'}) or any(g.get('status') != 'pass' for g in gates):
        raise ValueError('child topology gates failed or missing')
    artifacts = {'mesh':'mesh/mesh.json','graph':'graph/graph.json','qa':'qa/qa.json'}
    provenance = dict(child_run_id=manifest.get('run_id'),child_manifest_path=str(manifest_path),
                      child_manifest_sha256=_digest(manifest_path))
    for key,relative in artifacts.items():
        path = child/relative
        digest = _digest(path)
        if manifest.get('artifact_hashes',{}).get(relative) != digest:
            raise ValueError('child artifact hash missing or mismatched: '+relative)
        provenance.update({f'child_{key}_path':str(path),f'child_{key}_sha256':digest})
    cells = import_mesh(child/'mesh/mesh.json',analysis_crs=analysis_crs,max_cells=max_cells)
    resources = manifest.get('resources',{})
    provenance.update(research_wall_time_seconds=resources.get('wall_time_seconds'),
                      research_peak_rss_bytes=resources.get('peak_rss_bytes'))
    return cells, provenance


def generate_reference(output, *, analysis_crs, preset, max_cells):
    config, provenance = inspect_preset(analysis_crs,preset)
    meshing = Path(output)/'meshing'
    meshing.mkdir(exist_ok=False)
    config['output_root'] = str((meshing/'runs').resolve())
    config['run_name'] = 'workbench-'+PRESET
    config_path = meshing/'research-config.json'
    config_path.write_text(json.dumps(config,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    public_command(config_path,meshing/'research-console.log')
    runs = list((meshing/'runs').iterdir())
    if len(runs) != 1 or not runs[0].is_dir():
        raise ValueError('expected exactly one child research run')
    cells, child_provenance = validate_child(runs[0],analysis_crs,max_cells)
    if _digest(Path(provenance['preset_source_path'])) != provenance['preset_source_sha256']:
        raise ValueError('source preset changed during meshing')
    provenance.update(child_provenance,generated_research_config=str(config_path),
                      generated_research_config_sha256=_digest(config_path))
    return cells, provenance
