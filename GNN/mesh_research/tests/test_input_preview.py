import base64
from io import BytesIO
import tempfile
import unittest
from pathlib import Path
import numpy as np
import rasterio
from affine import Affine
from PIL import Image
from shapely.geometry import Polygon
from gis_mesh_workbench.input_preview import raster_preview,boundary_preview


class InputPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'raster.tif'

    def write(self,data,transform=Affine(1,0,500000,0,-1,1000001),crs='EPSG:32649'):
        data=np.asarray(data,dtype='float64')
        with rasterio.open(self.path,'w',driver='GTiff',width=data.shape[1],height=data.shape[0],
            count=1,dtype='float64',nodata=-9999,transform=transform,crs=crs) as src:src.write(data,1)

    def image(self,report):
        self.assertTrue(report['image'].startswith('data:image/png;base64,'))
        image=Image.open(BytesIO(base64.b64decode(report['image'].split(',')[1])))
        self.assertEqual(image.size,(900,500));return image

    def test_real_pixels_and_no_source_mutation(self):
        self.write([[10,30]]);before=self.path.read_bytes();report=raster_preview(self.path)
        self.image(report);self.assertEqual(report['source_shape'],[1,2]);self.assertEqual(report['display_shape'],[1,2])
        self.assertEqual(report['crs'],'EPSG:32649');self.assertEqual(self.path.read_bytes(),before)

    def test_downsample_rotation_and_nodata(self):
        self.write(np.full((800,600),-9999),Affine(1,.2,500000,.1,-1,1000000))
        report=raster_preview(self.path);self.image(report)
        self.assertTrue(report['sampled']);self.assertLessEqual(max(report['display_shape']),512)
        self.assertTrue(report['no_valid_samples'])

    def test_hole_is_not_filled(self):
        plain=Polygon([(0,0),(4,0),(4,4),(0,4)])
        hole=Polygon(plain.exterior.coords,holes=[[(1,1),(3,1),(3,3),(1,3)]])
        one=self.image(boundary_preview(plain,'EPSG:32649'))
        two=self.image(boundary_preview(hole,'EPSG:32649'))
        a=np.asarray(one);b=np.asarray(two)
        pink=(a[:,:,0]>200)&(a[:,:,1]<235)&(a[:,:,2]>210)
        changed=np.any(a!=b,axis=2)&pink
        self.assertGreater(changed.sum(),5000)

    def test_missing_source_crs_is_explicit_and_bad_band_rejected(self):
        self.write([[10,30]],crs=None);self.assertEqual(raster_preview(self.path)['crs'],'Chua co CRS')
        with self.assertRaisesRegex(ValueError,'Band'):raster_preview(self.path,2)
