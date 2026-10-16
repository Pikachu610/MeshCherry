"""Generate a clearly synthetic 100k-cell mesh for UI/export acceptance."""
import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    cols,rows=500,100
    vertices=[[500000+x,1000000+y] for y in range(rows+1) for x in range(cols+1)]
    triangles=[]
    for y in range(rows):
        for x in range(cols):
            a=y*(cols+1)+x;b=a+1;c=a+cols+1;d=c+1
            triangles.extend(((a,b,d),(a,d,c)))
    args.output.write_text(json.dumps(dict(schema_version='mesh-artifact-v0',crs='EPSG:32649',
        coordinate_units='m',vertices=vertices,triangles=triangles)),encoding='utf-8')
    print('Synthetic fixture:',len(triangles),'triangles;',args.output.stat().st_size,'bytes')


if __name__=='__main__':main()
