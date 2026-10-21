// Run with node --test. Exercises the production monitor and stop functions.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const app=fs.readFileSync(path.join(__dirname,'../src/gis_mesh_workbench/webui/app.js'),'utf8');
const start=(name)=>{const at=app.indexOf(`function ${name}(`);assert.ok(at>=0,`${name} exists`);return at;};
const asyncStart=(name)=>{const at=app.indexOf(`async function ${name}(`);assert.ok(at>=0,`${name} exists`);return at;};

function harness(){
  const elements=new Map(),make=id=>({id,value:'',hidden:false,disabled:false,textContent:'',dataset:{},attributes:{},className:'',setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];},querySelectorAll(){return [];}}),get=id=>{if(!elements.has(id))elements.set(id,make(id));return elements.get(id);};
  const document={body:{dataset:{}},getElementById:get,querySelectorAll:()=>[]};
  const calls=[];
  const context=vm.createContext({document,encodeURIComponent,setTimeout(){},api:async(...args)=>{calls.push(args);return {accepted:true};},syncDocumentControls(){},updateExtractReadiness(){},updateBatchReadiness(){},refreshCoach(){},mode(){},helpers(){},tutorialActive:false,tutorialClicks:new Set(),meshSnapshot:{jobId:'mesh-immutable-1234'},preflightId:null,runRequest:null,jobId:'mesh-immutable-1234',cancelPending:false,comparisonOwnsBusy:true});
  vm.runInContext(`const $=id=>document.getElementById(id);let busy=false;let preflightId=null,runRequest=null,jobId='mesh-immutable-1234',mainActiveJobId=null,cancelPending=false,comparisonOwnsBusy=true;let comparisonJobId=null,comparisonRequest=0,comparisonRequestKey='',comparisonEvaluationKey='',comparisonReadFailures=0,comparisonStopRequested=false,comparisonProgressSamples=[];`,context);
  return {context,get,calls};
}

test('comparison ownership cannot animate the main mesh monitor or enable stale mesh Stop',async()=>{
  const {context,get,calls}=harness();
  const setBusyEnd=asyncStart('startRun'),stopEnd=asyncStart('api');
  vm.runInContext(app.slice(start('setBusy'),setBusyEnd)+app.slice(asyncStart('requestStop'),stopEnd),context);
  vm.runInContext('setBusy(true)',context);
  assert.equal(get('progress').hidden,true,'comparison must not show the generic main-job spinner');
  assert.equal(get('cancel-run').disabled,true,'accepted mesh identity is not an active stoppable operation');
  await vm.runInContext('requestStop()',context);
  assert.equal(calls.length,0,'main Stop must not send the accepted mesh job id while comparison owns activity');
  assert.equal(vm.runInContext('jobId',context),'mesh-immutable-1234','accepted mesh identity remains intact');
});

test('comparison Stop requested before its job id is adopted stays pending for that comparison',async()=>{
  const {context,get,calls}=harness();
  const end=asyncStart('startComparison');
  const stopStart=asyncStart('stopComparison'),stopEnd=start('downloadComparison');
  vm.runInContext(app.slice(asyncStart('startComparison'),app.indexOf('async function recoverComparison(',end))+app.slice(stopStart,stopEnd),context);
  context.comparisonConfig=()=>({baseline_mode:'mesh'});
  context.setBusy=()=>{};context.comparisonOwnsBusy=false;
  let resolveStart;
  context.api=(url,payload)=>{calls.push([url,payload]);if(url==='/api/comparison')return new Promise(resolve=>{resolveStart=resolve;});return Promise.resolve({});};
  context.pollComparison=()=>{};
  const running=vm.runInContext('startComparison()',context);
  await vm.runInContext('stopComparison()',context);
  assert.equal(calls.filter(([url])=>url==='/api/job/stop').length,0,'no unrelated or missing id is sent before adoption');
  resolveStart({job_id:'comparison-job-1234'});
  await running;
  assert.deepEqual(calls.filter(([url])=>url==='/api/job/stop').map(([,body])=>body.job_id),['comparison-job-1234']);
  assert.equal(vm.runInContext('jobId',context),'mesh-immutable-1234');
});

test('main Stop uses the adopted active run id, never the accepted mesh id',async()=>{
  const {context,get,calls}=harness();
  vm.runInContext(`mainActiveJobId='new-run-5678';busy=true;comparisonOwnsBusy=false`,context);
  vm.runInContext(app.slice(asyncStart('requestStop'),asyncStart('api')),context);
  await vm.runInContext('requestStop()',context);
  assert.deepEqual(calls.map(([url,body])=>[url,body.job_id]),[['/api/job/stop','new-run-5678']]);
  assert.equal(vm.runInContext('jobId',context),'mesh-immutable-1234','the mesh artifact identity remains unchanged');
  assert.equal(get('cancel-run').disabled,true,'the repeated stop is locked while the request is outstanding');
});

test('the QA menu opens the compact panel without toggling an already open panel closed',()=>{
  const {context,get}=harness();let workspace='compare';
  vm.runInContext(`let qualityBusy=false;const meshSnapshot={jobId:'mesh-immutable-1234'};function setWorkspace(value){document.body.dataset.workspace=value;}`,context);
  vm.runInContext(app.slice(start('openQualityFromMenu'),start('prepareComparison')),context);
  vm.runInContext('openQualityFromMenu()',context);
  assert.equal(workspace,'compare');
  assert.equal(get('quality-panel-host').hidden,false);
  assert.equal(get('quality-open').textContent,'Thu gọn kiểm định');
  assert.equal(context.document.body.dataset.workspace,'mesh');
});

test('remesh menu and async restore cannot change configuration while QA owns the mesh',async()=>{
  let calls=0,resolveConfig;
  const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,{value:'unchanged',hidden:false,checked:false,className:'',textContent:'',disabled:false,setAttribute(){}});return elements.get(id);};
  const context=vm.createContext({document:{getElementById:get},encodeURIComponent,api:()=>{calls++;return new Promise(resolve=>{resolveConfig=resolve;});},toast(){},restoreAcceptedMeshPresentation(){},showRemeshDraftButton(){},stage(){},setWorkspace(){},mode(){}});
  vm.runInContext(`const $=id=>document.getElementById(id);let busy=false,qualityBusy=true,meshSnapshot={jobId:'accepted-mesh-1234',crs:'EPSG:32649'},revision=8,preserveAcceptedDuringRun=false,preflightId='keep',gmshAdvancedEnabled=false,plannedSource=null,plannedRequest=0;`,context);
  const restoreStart=asyncStart('restoreRemeshDraft'),restoreEnd=start('mountSingleAttributeTable');
  vm.runInContext(app.slice(restoreStart,restoreEnd)+app.slice(start('openRemeshFromMenu'),start('openVariationFromTools'))+app.slice(start('openVariationFromTools'),restoreStart),context);
  await vm.runInContext('restoreRemeshDraft()',context);
  vm.runInContext('openRemeshFromMenu()',context);
  vm.runInContext('openVariationFromTools()',context);
  assert.equal(calls,0,'QA precondition blocks starting any remesh-config read');
  assert.equal(get('mode').value,'unchanged');
  vm.runInContext('qualityBusy=false',context);
  const pending=vm.runInContext('restoreRemeshDraft()',context);
  assert.equal(calls,1);
  vm.runInContext('qualityBusy=true',context);
  resolveConfig({config:{analysis_crs:'EPSG:32649',cell_source:{kind:'structured_grid',extent:[1,2,3,4]}}});
  await pending;
  assert.equal(get('mode').value,'unchanged','a late response must not apply config after QA starts');
  assert.equal(vm.runInContext('preserveAcceptedDuringRun',context),false);
  assert.equal(vm.runInContext('meshSnapshot.jobId',context),'accepted-mesh-1234');
});

test('QA ownership blocks other comparison jobs before they can claim busy or call the API',async()=>{
  const calls=[],context=vm.createContext({api:async(...args)=>{calls.push(args);return {};},setTimeout(){}});
  vm.runInContext(`let busy=false,qualityBusy=true,comparisonCountRequest=0,comparisonRequest=0;`,context);
  const countStart=asyncStart('startComparisonCount'),countEnd=app.indexOf('async function recoverComparisonCount(',countStart);
  const evalStart=asyncStart('startComparison'),evalEnd=app.indexOf('async function recoverComparison(',evalStart);
  vm.runInContext(app.slice(countStart,countEnd)+app.slice(evalStart,evalEnd),context);
  await vm.runInContext('startComparisonCount()',context);
  await vm.runInContext('startComparison()',context);
  assert.equal(calls.length,0);
  assert.equal(vm.runInContext('busy',context),false,'comparison cannot steal QA ownership of the shared busy state');
});
