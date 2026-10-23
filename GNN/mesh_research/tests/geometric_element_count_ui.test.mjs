import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const app=await readFile(new URL('../src/gis_mesh_workbench/webui/app.js',import.meta.url),'utf8');
const html=await readFile(new URL('../src/gis_mesh_workbench/webui/index.html',import.meta.url),'utf8');
const handlers=app.slice(app.indexOf('function comparisonMeshCount()'),app.indexOf('async function showResearch()'));
function harness(){
  const elements={};
  const element=id=>elements[id]??={value:'',textContent:'',setAttribute(){},removeAttribute(){}};
  element('comparison-mode').value='native_raster';element('comparison-raster').value='dem.tif';element('comparison-band').value='1';
  const context={$:element,number:id=>Number(element(id).value),meshSnapshot:{jobId:'mesh-1',cell_count:10},comparisonInventory:null,comparisonInventoryKey:'',stageNames:{},duration:()=>''};
  vm.runInNewContext(handlers+'\nthis.actions={comparisonCountConfig,comparisonInventoryIdentity,renderComparisonCounts,comparisonReductionText};',context);
  return {context,element,actions:context.actions};
}
test('geometric count request and identity are independent of raster band values',()=>{
  const h=harness(),a=h.actions.comparisonCountConfig();assert.equal(a.band,undefined);
  h.element('comparison-band').value='2';assert.equal(h.actions.comparisonInventoryIdentity(a),h.actions.comparisonInventoryIdentity(h.actions.comparisonCountConfig()));
  assert.deepEqual(Object.keys(a).sort(),['baseline_mode','mesh_job_id','raster_path']);
});
test('primary baseline and reduction stay geometric even when valid-data diagnostics exist',()=>{
  const h=harness();h.context.comparisonInventory={mesh_count:{count:10},baseline:{mode:'native_raster',count:100,definition:'source_pixels_with_positive_area_intersection_with_mesh_union'},difference:{signed_mesh_minus_baseline:-90,reduction_percent:90},provenance:{valid_data_count:{status:'computed',value:20,band:1}}};
  h.context.comparisonInventoryKey=h.actions.comparisonInventoryIdentity(h.actions.comparisonCountConfig());h.actions.renderComparisonCounts();
  assert.equal(h.element('comparison-baseline-count').textContent,'100');assert.equal(h.element('comparison-count-delta').textContent,'-90');assert.equal(h.element('comparison-count-reduction').textContent,'Giảm 90%');assert.match(h.element('comparison-baseline-label').textContent,/đếm hình học/);
});
test('percentage communicates decrease, increase, unchanged and unavailable explicitly',()=>{
  const {actions}=harness();assert.equal(actions.comparisonReductionText(98.397),'Giảm 98,4%');assert.equal(actions.comparisonReductionText(-25),'Tăng 25%');assert.equal(actions.comparisonReductionText(0),'Không đổi 0%');assert.equal(actions.comparisonReductionText(null),'—');
});
test('signed difference and change wording handle more, equal, and zero baseline elements',()=>{
  for(const [baseline,delta,reduction,expectedDelta,expectedChange] of [[5,5,-100,'+5','Tăng 100%'],[10,0,0,'0','Không đổi 0%'],[0,10,null,'+10','—']]){
    const h=harness();h.context.comparisonInventory={mesh_count:{count:10},baseline:{mode:'native_raster',count:baseline,definition:'source_pixels_with_positive_area_intersection_with_mesh_union'},difference:{signed_mesh_minus_baseline:delta,reduction_percent:reduction}};h.context.comparisonInventoryKey=h.actions.comparisonInventoryIdentity(h.actions.comparisonCountConfig());h.actions.renderComparisonCounts();
    assert.equal(h.element('comparison-baseline-count').textContent,String(baseline));assert.equal(h.element('comparison-count-delta').textContent,expectedDelta);assert.equal(h.element('comparison-count-reduction').textContent,expectedChange);
  }
});
test('cards distinguish triangle elements from mesh vertices and clarify signed difference',()=>{
  assert.match(html,/<span>Phần tử tam giác · lưới đã chấp nhận<\/span>/);assert.match(html,/Chênh lệch phần tử · tam giác − đối chứng/);assert.doesNotMatch(html,/Node tam giác · lưới đã chấp nhận/);assert.match(app,/Đây là số ô, không phải số đỉnh hình học/);
});
test('native count help declares NoData independent geometric scope, not full TIFF bounds',()=>{
  assert.match(app,/bất kể giá trị hoặc NoData/);assert.match(app,/Không đọc band\/mask/);assert.match(app,/không đếm toàn bộ khung TIFF ngoài miền lưới/);
  assert.doesNotMatch(app,/chưa loại NoData/);
});
test('detailed comparison labels preserve geometric count and separate valid support diagnostics',()=>{
  const render=app.slice(app.indexOf('function renderComparison(report)'),app.indexOf('function finishComparisonCount()'));
  assert.match(render,/Pixel giao miền lưới · đếm hình học/);assert.match(render,/Phần tử tam giác · lưới hiện tại/);assert.match(render,/Pixel nguồn hợp lệ trong hỗ trợ/);assert.match(render,/100 × \(đối chứng − tam giác\) \/ đối chứng/);
});
