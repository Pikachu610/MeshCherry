"""Generate tiny, explicit ESRI ASCII Grid fixtures for desktop acceptance."""
import argparse
from pathlib import Path
from rasterio.crs import CRS


def main():
    parser=argparse.ArgumentParser();parser.add_argument('directory',type=Path);args=parser.parse_args()
    args.directory.mkdir(parents=True,exist_ok=True)
    header='ncols 2\nnrows 1\nxllcorner 500000\nyllcorner 1000000\ncellsize 1\nNODATA_value -9999\n'
    for name,values in [('sample_10_30','10 30'),('sample_nodata','10 -9999')]:
        path=args.directory/(name+'.asc')
        if path.exists() or path.with_suffix('.prj').exists():raise FileExistsError(path)
        path.write_text(header+values+'\n',encoding='ascii')
        path.with_suffix('.prj').write_text(CRS.from_epsg(32649).to_wkt(),encoding='ascii')


if __name__=='__main__':main()
