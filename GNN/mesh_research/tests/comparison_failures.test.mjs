import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const app=await readFile(new URL('../src/gis_mesh_workbench/webui/app.js',import.meta.url),'utf8');
const handlers=app.slice(app.indexOf('function finishComparisonCount()'),app.indexOf("$('project-open').onclick"));
class Element {
  constructor(){this.textContent='';this.hidden=false;this.disabled=false;this.children=[];this.dataset={};this.nextElementSibling=null;this.value='native_raster';}
  append(...nodes){this.children.push(...nodes);}
  after(node){this.nextElementSibling=node;node.owner=this;}
  remove(){if(this.owner)this.owner.nextElementSibling=null;}
  removeAttribute(){}
  querySelectorAll(){return [];}
}
function harness(api=async()=>({})){const elements={};const context={
  $:id=>elements[id]||(elements[id]=new Element()),document:{createElement:()=>new Element()},api,
  busy:false,qualityBusy:false,comparisonRequest:1,comparisonRequestKey:'r1',comparisonJobId:'j1',comparisonReadFailures:0,
  comparisonStopRequested:false,comparisonOwnsBusy:false,comparisonEvaluationKey:'{}',comparisonProgressSamples:[],
  comparisonConfig:()=>({}),setBusy(value){context.busy=value;},prepareComparison(){},formatPhaseProgress(){},setTimeout(){},
};vm.runInNewContext(`${handlers};this.actions={renderComparisonFailure,clearComparisonFailure,pollComparison,startComparison};`,context);return {elements,context,actions:context.actions};}

test('structured failure renders actionable headline and raw error only inside collapsed text details',()=>{
  const h=harness(),raw='<script>secret path</script>';
  h.actions.renderComparisonFailure({error:raw,failure:{title:'Vượt giới hạn',message:'Không thể đọc hết raster.',actions:['Chọn miền nhỏ hơn.']}});
  const status=h.elements['comparison-status'];assert.equal(status.textContent,'Vượt giới hạn Không thể đọc hết raster.');
  assert.ok(!status.textContent.includes(raw));const panel=status.nextElementSibling;
  assert.equal(panel.children[0].children[0].textContent,'Chọn miền nhỏ hơn.');const details=panel.children[1];
  assert.equal(details.children[0].textContent,'Chi tiết kỹ thuật của lượt so sánh');
  assert.equal(details.children[1].textContent,raw);assert.equal(details.open,undefined);
  h.actions.clearComparisonFailure();assert.equal(status.nextElementSibling,null);
});

test('legacy unknown failures never become raw headline; known read cap remains actionable',()=>{
  const h=harness();h.actions.renderComparisonFailure({error:'unknown secret C:/private.tif'});
  assert.ok(!h.elements['comparison-status'].textContent.includes('secret'));
  h.actions.renderComparisonFailure({error:'comparison read pixel limit exceeded'});
  const action=h.elements['comparison-status'].nextElementSibling.children[0].children[0].textContent;
  assert.match(action,/miền nhỏ hơn/);assert.doesNotMatch(action,/ô nhỏ hơn/);
});

test('failed poll shows guidance and finishes without changing cancellation into failure',async()=>{
  const h=harness(async()=>({status:'fail',result:{error:'comparison read pixel limit exceeded'}}));
  await h.actions.pollComparison(1);assert.match(h.elements['comparison-status'].textContent,/Vượt giới hạn đọc raster/);
  assert.equal(h.context.comparisonJobId,null);assert.equal(h.elements['comparison-stop'].hidden,true);
  const cancelled=harness(async()=>({status:'cancelled',result:{error:'Cancelled'}}));
  await cancelled.actions.pollComparison(1);assert.match(cancelled.elements['comparison-status'].textContent,/Đã dừng/);
  assert.equal(cancelled.elements['comparison-status'].nextElementSibling,null);
});

test('definite admission rejection preserves structured guidance and does not poll recovery',async()=>{
  const calls=[];const h=harness(async path=>{calls.push(path);const error=new Error('comparison read pixel limit exceeded');
    error.status=400;error.failure={title:'Raster vượt giới hạn',message:'Cần giảm miền đọc.',actions:['Chọn miền nhỏ hơn.']};throw error;});
  await h.actions.startComparison();assert.deepEqual(calls,['/api/comparison']);
  assert.match(h.elements['comparison-status'].textContent,/Raster vượt giới hạn/);assert.equal(h.context.busy,false);
  assert.equal(h.elements['comparison-evaluation-task'].hidden,true);
});

test('api preserves structured failure metadata on rejected responses',async()=>{
  const begin=app.indexOf('async function api('),end=app.indexOf('function downloadQuality(',begin);
  const failure={code:'comparison_read_limit',message:'Thông báo an toàn'};
  const context={fetch:async()=>({ok:false,status:400,json:async()=>({error:'raw detail',failure})}),token:'test-token'};
  vm.runInNewContext(`${app.slice(begin,end)};this.request=api;`,context);
  await assert.rejects(context.request('/api/comparison',{}),error=>error.status===400&&error.failure===failure);
});
