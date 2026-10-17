import json
import copy
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin

from gis_mesh_workbench.gmsh_meshing import inspect_executable, size_field, read_triangles, inspect_domain, generate
from gis_mesh_workbench.runner import run, write_json

EXE=Path(__file__).resolve().parents[3]/'Test_chialuoi/gmsh-4.15.2-Windows64/gmsh.exe'


class GeneratedGmshTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.raster=self.root/'dem.tif'
        with rasterio.open(self.raster,'w',driver='GTiff',width=20,height=20,count=1,
            dtype='float64',crs='EPSG:32649',transform=from_origin(500000,1001000,50,50)) as dst:
            dst.write(np.full((20,20),17.),1)
        self.config=dict(schema_version='gis-workbench-run-config-v1',extraction_engine='indexed_windowed_v1',
            run_name='generated-test',analysis_crs='EPSG:32649',raster_path=str(self.raster),band=1,
            semantics='intensive',value_units='m',output_root=str(self.root/'runs'),
            cell_source=dict(kind='gmsh_generated',executable=str(EXE),extent=[500000,1000000,501000,1001000],
                sizing='uniform',h_min_m=250,h_max_m=250,relief_radius_m=100,max_cells=1000))

    def test_reject_missing_executable(self):
        with self.assertRaises(FileNotFoundError): inspect_executable(self.root/'gmsh.exe')

    def test_gmsh_subprocess_output_preserves_unicode_and_replaces_bad_bytes(self):
        from gis_mesh_workbench.gmsh_meshing import _decode_gmsh_output
        decoded=_decode_gmsh_output(b'Gmsh '+bytes([0xe1,0xbb,0x87])+b' '+bytes([0xff])+b' output')
        self.assertEqual(decoded,'Gmsh ệ \ufffd output')
        self.assertEqual(_decode_gmsh_output('Gmsh ệ \udc00 output'),'Gmsh ệ \ufffd output')
        self.assertFalse(any(0xD800<=ord(char)<=0xDFFF for char in decoded))
        executable=self.root/'gmsh.exe';executable.write_bytes(b'fixture')
        with patch('gis_mesh_workbench.gmsh_meshing.subprocess.run',
                   return_value=subprocess.CompletedProcess([],0,b'4.15.2\xff',b'')):
            with self.assertRaisesRegex(ValueError,'4.15.2') as raised:
                inspect_executable(executable)
        self.assertNotIn('\\udc',str(raised.exception))

    def test_relief_dem_and_extraction_raster_may_be_different(self):
        if not EXE.is_file():self.skipTest('External Gmsh is not installed')
        other=self.root/'attributes.tif'
        with rasterio.open(other,'w',driver='GTiff',width=20,height=20,count=1,
            dtype='float64',crs='EPSG:32649',transform=from_origin(500000,1001000,50,50)) as dst:
            dst.write(np.full((20,20),42.),1)
        self.config.update(schema_version='gis-workbench-run-config-v2',operation='mesh_only')
        for field in ('raster_path','band','semantics','value_units'):self.config.pop(field)
        self.config['cell_source'].update(sizing='relief',dem_raster_path=str(self.raster),dem_band=1)
        path=self.root/'different-inputs.json';write_json(path,self.config)
        result=run(path);self.assertEqual(result['status'],'pass',result)
        mesh_output=Path(result['output'])
        mesh_manifest=json.loads((mesh_output/'manifest.json').read_text())
        self.assertEqual(set(mesh_manifest['raster_inputs']),{str(self.raster)})
        self.config.update(operation='extract',raster_path=str(other),band=1,semantics='intensive',value_units='m',
            cell_source=dict(kind='frozen_cells',path=str(mesh_output/'cells.json'),max_cells=1000,
                             geometry_digest=mesh_manifest['geometry_digest']))
        write_json(path,self.config);result=run(path);self.assertEqual(result['status'],'pass',result)
        attrs=json.loads((Path(result['output'])/'attributes.json').read_text())
        self.assertTrue(all(abs(row['value']-42)<1e-8 for row in attrs['cells']))
        manifest=json.loads((Path(result['output'])/'manifest.json').read_text())
        self.assertEqual(set(manifest['raster_inputs']),{str(other)})
        self.assertEqual(manifest['geometry_digest'],mesh_manifest['geometry_digest'])

    def test_relief_flat_and_variable(self):
        self.config['cell_source'].update(sizing='relief',h_min_m=100,h_max_m=300)
        evidence=size_field(self.config,self.root)
        self.assertEqual(evidence['h_min_realized'],300)
        values=np.zeros((20,20)); values[:,10:]=np.arange(10)[None,:]**2*10
        with rasterio.open(self.raster,'r+') as dst: dst.write(values,1)
        evidence=size_field(self.config,self.root)
        self.assertLess(evidence['h_min_realized'],evidence['h_max_realized'])
        self.assertGreaterEqual(evidence['h_min_realized'],100)

    def test_real_uniform_and_relief_generation_extraction(self):
        if not EXE.is_file(): self.skipTest('External Gmsh is not installed')
        self.assertEqual(inspect_executable(EXE)['version'],'4.15.2')
        for sizing in ('uniform','relief'):
            with self.subTest(sizing=sizing):
                self.config['cell_source']['sizing']=sizing
                path=self.root/(sizing+'.json'); write_json(path,self.config)
                result=run(path); self.assertEqual(result['status'],'pass',result)
                output=Path(result['output']); attrs=json.loads((output/'attributes.json').read_text())
                self.assertGreater(len(attrs['cells']),2)
                self.assertTrue(all(abs(c['value']-17)<1e-8 for c in attrs['cells']))
                self.assertTrue((output/'meshing/mesh.msh').is_file())
                self.assertTrue((output/'meshing/mesh.json').is_file())
                manifest=json.loads((output/'manifest.json').read_text())
                self.assertEqual(manifest['gmsh_generated']['domain_coverage'],'pass')
                self.assertEqual(manifest['gmsh_generated']['boundary_status'],'UNVERIFIED')

    def test_complex_relief_rejected_before_executable(self):
        self.config['cell_source']['sizing']='relief'
        with rasterio.open(self.raster,'w',driver='GTiff',width=20,height=20,count=1,
            dtype='complex64',crs='EPSG:32649',transform=from_origin(500000,1001000,50,50)) as dst:
            dst.write(np.ones((20,20),dtype='complex64')*(1+2j),1)
        with patch('gis_mesh_workbench.gmsh_meshing.inspect_executable') as execute:
            with self.assertRaisesRegex(ValueError,'số phức'): inspect_domain(self.config)
            execute.assert_not_called()
        with self.assertRaisesRegex(ValueError,'số phức'): size_field(self.config,self.root)

    def test_real_variable_relief_response(self):
        if not EXE.is_file(): self.skipTest('External Gmsh is not installed')
        values=np.zeros((20,20)); values[:,10:]=np.arange(10)[None,:]**2*10
        with rasterio.open(self.raster,'r+') as dst: dst.write(values,1)
        self.config['cell_source'].update(sizing='relief',h_min_m=80,h_max_m=250,relief_radius_m=100)
        path=self.root/'variable.json'; write_json(path,self.config)
        result=run(path); self.assertEqual(result['status'],'pass',result)
        output=Path(result['output']); field=json.loads((output/'meshing/size-field.json').read_text())
        self.assertGreater(field['p95'],field['p25'])
        self.assertLess(field['h_min_realized'],250)
        cells=json.loads((output/'attributes.json').read_text())['cells']
        # Flat left vs variable right, excluding transition at x=500500.
        mesh=json.loads((output/'meshing/mesh.json').read_text()); vertices=np.array(mesh['vertices'])
        centres=[vertices[t].mean(axis=0)[0] for t in mesh['triangles']]
        areas=[c['area_m2'] for c in cells]
        flat=[a for a,x in zip(areas,centres) if x<500350]
        rough=[a for a,x in zip(areas,centres) if x>500750]
        self.assertTrue(flat and rough)
        self.assertLess(np.median(rough),np.median(flat))

    def test_parser_valid_and_rejections(self):
        baseline='$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$Nodes\n3\n1 0 0 0\n2 1 0 0\n3 0 1 0\n$EndNodes\n$Elements\n1\n1 2 0 1 2 3\n$EndElements\n'
        msh=self.root/'test.msh'
        msh.write_text(baseline); self.assertEqual(len(read_triangles(msh,None,10)[0]),1)
        cases=[baseline.replace('2.2 0 8','2.2 1 8'),
            baseline.replace('1 2 0 1 2 3','1 1 0 1 2'),
            baseline.replace('1 2 0 1 2 3','1 3 0 1 2 3 1'),
            baseline.replace('2 1 0 0','1 1 0 0'),
            baseline.replace('2 1 0 0','2 nan 0 0'),
            baseline.replace('2 1 0 0','2 1 0 1'),
            baseline.replace('1 2 0 1 2 3','1 2 0 1 1 3'),
            baseline.replace('1 2 0 1 2 3','1 2 0 1 2 9'),
            baseline.replace('$Nodes\n3','$Nodes\n4'),
            baseline.replace('$EndElements',''),
            baseline.replace('$Elements\n1\n1 2 0 1 2 3','$Elements\n2\n1 2 0 1 2 3\n2 2 0 1 2 3')]
        for i,text in enumerate(cases):
            with self.subTest(i=i):
                msh.write_text(text)
                with self.assertRaises(ValueError): read_triangles(msh,None,10)
        msh.write_text(baseline)
        with self.assertRaises(ValueError): read_triangles(msh,None,0)

    def test_executable_failures(self):
        executable=self.root/'gmsh.exe'; executable.write_bytes(b'fixture-not-executed')
        for result in (subprocess.CompletedProcess([],1,'4.15.2',''),
                       subprocess.CompletedProcess([],0,'invalid',''),
                       subprocess.TimeoutExpired('gmsh',8)):
            with self.subTest(result=str(result)):
                with patch('gis_mesh_workbench.gmsh_meshing.subprocess.run',
                    **({'side_effect':result} if isinstance(result,Exception) else {'return_value':result})):
                    with self.assertRaises((ValueError,subprocess.TimeoutExpired)): inspect_executable(executable)
        with patch('gis_mesh_workbench.gmsh_meshing.subprocess.run',
            return_value=subprocess.CompletedProcess([],0,'4.15.2','')):
            with patch('gis_mesh_workbench.gmsh_meshing._digest',side_effect=['before','after']):
                with self.assertRaisesRegex(ValueError,'thay đổi'): inspect_executable(executable)

    def test_meshing_process_failures_retain_diagnostics(self):
        evidence=dict(executable=str(EXE),executable_sha256='unchanged')
        for index,result in enumerate((subprocess.CompletedProcess([],1),
            subprocess.TimeoutExpired('gmsh',60),subprocess.CompletedProcess([],0))):
            output=self.root/('failure'+str(index)); output.mkdir()
            with patch('gis_mesh_workbench.gmsh_meshing.inspect_domain',return_value=evidence.copy()), \
                 patch('gis_mesh_workbench.gmsh_meshing._digest',return_value='unchanged'), \
                 patch('gis_mesh_workbench.gmsh_meshing.subprocess.run',
                    **({'side_effect':result} if isinstance(result,Exception) else {'return_value':result})):
                with self.assertRaises((ValueError,subprocess.TimeoutExpired,FileNotFoundError)):
                    generate(self.config,output)
            self.assertTrue((output/'meshing/domain.geo').is_file())
            self.assertTrue((output/'meshing/gmsh.log').is_file())
        output=self.root/'mutation'; output.mkdir()
        with patch('gis_mesh_workbench.gmsh_meshing.inspect_domain',return_value=evidence.copy()), \
             patch('gis_mesh_workbench.gmsh_meshing._digest',return_value='changed'), \
             patch('gis_mesh_workbench.gmsh_meshing.subprocess.run',return_value=subprocess.CompletedProcess([],0)):
            with self.assertRaisesRegex(ValueError,'Executable thay đổi'): generate(self.config,output)

    def test_oversized_msh(self):
        msh=self.root/'large.msh'
        with msh.open('wb') as stream: stream.truncate(64*1024*1024+1)
        with self.assertRaisesRegex(ValueError,'64 MiB'): read_triangles(msh,None,1000)

    def test_domain_validation_before_executable(self):
        variants=[dict(extent=[0,0,0,1]),dict(extent=[0,0,float('inf'),1]),
            dict(h_min_m=0),dict(h_min_m=300),dict(h_min_m=1),dict(sizing='relief')]
        for changed in variants:
            config=copy.deepcopy(self.config); config['cell_source'].update(changed)
            if changed.get('sizing')=='relief': config['semantics']='categorical'
            with self.subTest(changed=changed), patch('gis_mesh_workbench.gmsh_meshing.inspect_executable') as execute:
                with self.assertRaises(ValueError): inspect_domain(config)
                execute.assert_not_called()
        config=copy.deepcopy(self.config); config['analysis_crs']='EPSG:4326'
        with self.assertRaises(ValueError): inspect_domain(config)

    def test_relief_scale_offset_and_size_guards(self):
        self.config['cell_source']['sizing']='relief'
        with rasterio.open(self.raster,'r+') as dst: dst.scales=(2,)
        with self.assertRaisesRegex(ValueError,'scale/offset'): inspect_domain(self.config)
        from types import SimpleNamespace
        from gis_mesh_workbench.gmsh_meshing import inspect_relief_source
        source=SimpleNamespace(crs='EPSG:32649',count=1,dtypes=['float64'],width=10000001,height=1)
        with self.assertRaisesRegex(ValueError,'10M'): inspect_relief_source(source,1)

    def test_runner_retains_failed_meshing_stage(self):
        if not EXE.is_file(): self.skipTest('External Gmsh is not installed')
        evidence=inspect_executable(EXE)
        config=self.root/'failed.json'; write_json(config,self.config)
        with patch('gis_mesh_workbench.gmsh_meshing.inspect_executable',return_value=evidence), \
             patch('gis_mesh_workbench.gmsh_meshing.subprocess.run',return_value=subprocess.CompletedProcess([],1)):
            result=run(config)
        self.assertEqual(result['status'],'fail')
        output=Path(result['output']); manifest=json.loads((output/'manifest.json').read_text())
        self.assertEqual(manifest['failed_stage'],'meshing')
        for name in ('preflight.json','qa.json','meshing/domain.geo','meshing/gmsh.log'):
            self.assertTrue((output/name).is_file(),name)

    def test_incomplete_domain_rejected(self):
        if not EXE.is_file(): self.skipTest('External Gmsh is not installed')
        from gis_mesh_workbench.extraction import Cell
        triangle=Cell('hole','triangle',((500000,1000000),(501000,1000000),(500000,1001000)))
        evidence=inspect_executable(EXE); output=self.root/'hole'; output.mkdir()
        with patch('gis_mesh_workbench.gmsh_meshing.inspect_domain',return_value=evidence), \
             patch('gis_mesh_workbench.gmsh_meshing.subprocess.run',return_value=subprocess.CompletedProcess([],0)), \
             patch('gis_mesh_workbench.gmsh_meshing.read_triangles',return_value=((triangle,),{})):
            with self.assertRaisesRegex(ValueError,'phủ đúng miền'): generate(self.config,output)
