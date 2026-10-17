"""Bounded source previews; display sampling never alters mesh or extraction."""
import base64
from io import BytesIO
import math
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.path import Path as PlotPath
from matplotlib.patches import PathPatch
from matplotlib.ticker import MaxNLocator
from rasterio.enums import Resampling
from shapely.geometry.polygon import orient
from .raster_source import open_raster, check_driver, snapshot, assert_unchanged


def finish(figure, report):
    stream=BytesIO();FigureCanvasAgg(figure).print_png(stream)
    report['image']='data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode('ascii')
    return report


def raster_preview(path, band=1):
    before=snapshot(path)
    figure=Figure(figsize=(9,5),dpi=100,facecolor='#fffdfb');axis=figure.add_subplot(111)
    with open_raster(path,require_crs=False) as src:
        check_driver(src)
        if type(band) is not int or not 1<=band<=src.count:raise ValueError('Band ngoài phạm vi raster')
        if src.width*src.height>10000000:raise ValueError('Raster vượt giới hạn xem trước 10M pixel')
        scale=min(1,512/max(src.width,src.height))
        height,width=max(1,math.ceil(src.height*scale)),max(1,math.ceil(src.width*scale))
        values=src.read(band,out_shape=(height,width),masked=True,resampling=Resampling.nearest)
        values=np.ma.masked_invalid(values.astype(float))
        transform=src.transform @ src.transform.scale(src.width/width,src.height/height)
        columns,rows=np.meshgrid(np.arange(width+1),np.arange(height+1))
        x=transform.a*columns+transform.b*rows+transform.c
        y=transform.d*columns+transform.e*rows+transform.f
        valid=values.compressed()
        bounds=list(src.bounds);crs=str(src.crs) if src.crs else 'Chua co CRS'
        if valid.size:
            low,high=np.percentile(valid,[2,98]);high=high if high>low else low+1
            artist=axis.pcolormesh(x,y,values,cmap='viridis',vmin=low,vmax=high,shading='flat')
            figure.colorbar(artist,ax=axis,pad=.03,shrink=.8,label=f'Band {band} (raw)')
        else:axis.text(.5,.5,'No valid pixels',ha='center',transform=axis.transAxes)
        axis.set_xlim(bounds[0],bounds[2]);axis.set_ylim(bounds[1],bounds[3])
        units='unknown' if src.crs is None else ('degree' if src.crs.is_geographic else src.crs.linear_units)
        if units in {'metre','meter','metres','meters'}:units='m'
        nodata=src.nodata
        report=dict(kind='raster',crs=crs,band=band,bounds=bounds,
            crs_defined=src.crs is not None,driver=src.driver,
            pixel_size=[math.hypot(src.transform.a,src.transform.d),
                        math.hypot(src.transform.b,src.transform.e)],
            coordinate_units=units,
            nodata=float(nodata) if nodata is not None and math.isfinite(nodata) else None,
            source_shape=[src.height,src.width],display_shape=[height,width],
            sampled=scale<1,stretch='P2-P98 display only',no_valid_samples=not bool(valid.size))
    axis.set_aspect('equal',adjustable='box');axis.set_xlabel('X');axis.set_ylabel('Y')
    axis.xaxis.set_major_locator(MaxNLocator(4));axis.yaxis.set_major_locator(MaxNLocator(4))
    axis.ticklabel_format(style='plain',useOffset=False);figure.tight_layout()
    assert_unchanged(path,before)
    return finish(figure,report)


def boundary_preview(domain, crs):
    figure=Figure(figsize=(9,5),dpi=100,facecolor='#fffdfb');axis=figure.add_subplot(111)
    polygons=[domain] if domain.geom_type=='Polygon' else domain.geoms
    for polygon in polygons:
        polygon=orient(polygon,sign=1);vertices=[];codes=[]
        for ring in [polygon.exterior,*polygon.interiors]:
            points=list(ring.coords);vertices.extend(points)
            codes.extend([PlotPath.MOVETO]+[PlotPath.LINETO]*(len(points)-2)+[PlotPath.CLOSEPOLY])
        axis.add_patch(PathPatch(PlotPath(vertices,codes),facecolor='#f7dce5',edgecolor='#bc2948',linewidth=1.2))
    axis.autoscale_view();axis.set_aspect('equal',adjustable='box');axis.margins(.06)
    axis.set_xlabel('X');axis.set_ylabel('Y');axis.ticklabel_format(style='plain',useOffset=False)
    axis.xaxis.set_major_locator(MaxNLocator(4));axis.yaxis.set_major_locator(MaxNLocator(4))
    figure.tight_layout()
    return finish(figure,dict(kind='boundary',crs=crs,bounds=list(domain.bounds)))
