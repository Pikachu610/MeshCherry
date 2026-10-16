"""Measure real PNG preview and bounded UI payload in independent processes."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from gis_mesh_workbench.extraction import Cell
from gis_mesh_workbench.indexed_extraction import process_memory
from gis_mesh_workbench.presentation import artifact_view
from gis_mesh_workbench.runner import preview


def measure(count):
    cells=tuple(Cell(str(i),'triangle',((i%500,i//500),(i%500+1,i//500),(i%500,i//500+1))) for i in range(count))
    with tempfile.TemporaryDirectory(prefix='meshcherry-preview-bench-') as directory:
        target=Path(directory)/'preview.png'
        started=time.perf_counter();preview(target,cells);seconds=time.perf_counter()-started
        full=dict(cells=[dict(cell_id=c.cell_id,kind=c.kind,xy=c.xy) for c in cells])
        started=time.perf_counter();view=artifact_view('cells',full);encoded=json.dumps(view).encode()
        return dict(cell_count=count,preview_seconds=seconds,preview_png_bytes=target.stat().st_size,
                    view_seconds=time.perf_counter()-started,view_bytes=len(encoded),
                    transmitted_cells=len(view['cells']),memory=process_memory())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--count',type=int);parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.count:
        print(json.dumps(measure(args.count)))
    else:
        reports=[]
        for count in (10000,100000):
            run=subprocess.run([sys.executable,__file__,'--count',str(count)],check=True,capture_output=True,text=True)
            reports.append(json.loads(run.stdout))
        output=json.dumps(dict(cases=reports,method='independent Python process per case; lifetime peak memory; real runner.preview'),indent=2)
        if args.output:
            if args.output.exists():raise FileExistsError(args.output)
            args.output.write_text(output+'\n',encoding='utf-8')
        print(output)
