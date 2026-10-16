"""Generate clearly synthetic high-detail SHP/DEM data for desktop acceptance."""
import argparse
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
import shapefile


def create(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    if any(directory.iterdir()):raise ValueError('Fixture directory must be empty')
    ring=[]
    for i in range(2200):
        angle=-2*math.pi*i/2200;radius=1000+3*math.sin(17*angle)
        ring.append((500000+radius*math.cos(angle),1000000+radius*math.sin(angle)))
    ring.append(ring[0])
    hole=[(499900,999900),(500100,999900),(500100,1000100),(499900,1000100),(499900,999900)]
    path=directory/'original_boundary_2206.shp'
    with shapefile.Writer(str(path),shapeType=shapefile.POLYGON) as writer:
        writer.field('id','N');writer.poly([ring,hole]);writer.record(1)
    path.with_suffix('.prj').write_text(CRS.from_epsg(32649).to_wkt(),encoding='utf-8')
    with rasterio.open(directory/'constant_7.tif','w',driver='GTiff',width=12,height=12,count=1,
        dtype='float64',crs='EPSG:32649',transform=from_origin(498800,1001200,200,200)) as dst:
        dst.write(np.full((12,12),7.),1)
    return path


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('directory',type=Path)
    print(create(parser.parse_args().directory))
