"""Save a real source preview PNG for visual inspection, without changing source."""
import argparse
import base64
from pathlib import Path
from gis_mesh_workbench.input_preview import raster_preview,boundary_preview
from gis_mesh_workbench.boundary import read_boundary


def main():
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path);parser.add_argument('--crs',default='EPSG:32649')
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    if args.source.suffix.lower()=='.shp':
        domain,_=read_boundary(args.source,args.crs);report=boundary_preview(domain,args.crs)
    else:report=raster_preview(args.source)
    args.output.write_bytes(base64.b64decode(report['image'].split(',')[1]))


if __name__=='__main__':main()
