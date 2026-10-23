import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const app=await readFile(new URL('../src/gis_mesh_workbench/webui/app.js',import.meta.url),'utf8');
const html=await readFile(new URL('../src/gis_mesh_workbench/webui/index.html',import.meta.url),'utf8');
const start=app.indexOf('function comparisonConfig()'),end=app.indexOf('function comparisonTable(',start);
function config(mode='native_raster',workload='research'){
  const values={'comparison-mode':mode,'comparison-size':'30','comparison-source-resolution':'60','comparison-band':'1','comparison-raster':'dem.tif','comparison-units':'m','comparison-semantics':'intensive','comparison-workload-mode':workload};
  const scope={$:id=>({value:values[id]}),number:id=>Number(values[id]),meshSnapshot:{jobId:'mesh1'}};
  vm.runInNewContext(app.slice(start,end)+';this.result=comparisonConfig()',scope);
  return scope.result;
}
test('research is default in measured comparison UI and request',()=>{
  assert.match(html,/<option value="research" selected>/);
  assert.equal(config().workload_mode,'research');
  assert.equal(config('target_grid','bounded').workload_mode,'bounded');
  assert.notEqual(JSON.stringify(config()),JSON.stringify(config('native_raster','bounded')));
});
test('resolution estimate does not send raster workload policy',()=>{
  assert.equal(Object.hasOwn(config('resolution_estimate'),'workload_mode'),false);
});
test('invalid comparison workload mode is rejected',()=>{
  assert.throws(()=>config('native_raster','unlimited_bad'),/chế độ khối lượng/);
});
test('workload change invalidates evaluation only, not geometry inventory',()=>{
  assert.match(app,/\$\('comparison-workload-mode'\)\.addEventListener\('change',clearComparison\)/);
  const invStart=app.indexOf('function comparisonInventoryIdentity('),invEnd=app.indexOf('\nfunction ',invStart+1);
  assert.doesNotMatch(app.slice(invStart,invEnd),/workload/);
});
test('help distinguishes provisional RAM guidance and remaining safety scope',()=>{
  assert.match(html,/id="system-requirements-open"/);
  assert.match(html,/id="system-requirements"/);
  assert.match(html,/Chưa xác lập RAM tối thiểu/);
  assert.match(html,/không gỡ giới hạn của tạo lưới, SF-1 hoặc Extract/);
  assert.match(html,/learn\.microsoft\.com\/en-us\/microsoft-edge\/webview2/);
  assert.match(app,/\$\('system-requirements-open'\)\.onclick=.*showModal\(\)/);
});
test('report capacity is authoritative and retains null ceilings and memory guards',()=>{
  const begin=app.indexOf('function renderComparisonCapacity('),finish=app.indexOf('function renderSpatialAllocation(',begin);
  const rows=[];
  const scope={document:{createElement:()=>({append(){}})},detailRows:(_,items)=>rows.push(...items),countText:String};
  vm.runInNewContext(app.slice(begin,finish)+';this.render=renderComparisonCapacity',scope);
  scope.render({append(){}},{workload_mode:'research',hard_limits:{max_source_pixels:null,max_pixels_read:null,max_candidate_pairs:null,max_window_pixels:65536},max_cells:100000,max_class_entries:100000});
  assert.equal(rows[0][1],'Nghiên cứu · dữ liệu lớn');
  assert.equal(rows.filter(row=>row[1]==='Không đặt trần số lượng').length,3);
  assert.ok(rows.some(row=>row[0]==='Pixel mỗi cửa sổ'&&row[1]==='65536'));
  assert.ok(rows.some(row=>row[0]==='Mục tích lũy phân loại tối đa'&&row[1]==='100000'));
});
