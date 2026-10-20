// Run with node --test. Locks the single workspace-navigation contract and
// the visible, non-destructive path from accepted results back to configuration.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const root=path.join(__dirname,'../src/gis_mesh_workbench/webui');
const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
const app=fs.readFileSync(path.join(root,'app.js'),'utf8');

test('one workspace nav owns result views and exposes comparison',()=>{
  const navs=[...html.matchAll(/<nav\b[^>]*class="workspace-nav"[^>]*>([\s\S]*?)<\/nav>/g)];
  assert.equal(navs.length,1,'exactly one workspace navigation');
  for(const name of ['roi','mesh','extract','log','compare'])assert.match(navs[0][1],new RegExp(`data-workspace="${name}"`));
  assert.doesNotMatch(html,/class="result-tabs"|id="result-(?:mesh|extract|log)-tab"/,'there is no nested results tab strip');
  assert.doesNotMatch(app,/result-(?:mesh|extract|log)-tab/,'runtime code does not reference removed tab controls');
  assert.doesNotMatch(app,/button\[data-stage=["']4["']\]/,'workspace setup does not depend on the removed stage navigation');
});

test('accepted mesh offers a discoverable edit and remesh action',()=>{
  assert.ok(app.includes("button.textContent='Chỉnh thông số / Chia lưới lại'"),'the accepted mesh exposes the edit/remesh action');
  assert.match(app,/preserveAcceptedDuringRun\s*=\s*true;[\s\S]{0,100}stage\(2\)/,
    'editing starts from saved config while preserving the accepted mesh');
  assert.match(app,/if\(!preserveAcceptedDuringRun\)resetResult\(\)/,
    'results are cleared only for a fresh run, not when returning to config');
});

test('editing restored inputs and invalidating keeps the accepted mesh',async()=>{
  const elements=new Map(),element=()=>({value:'',hidden:false,checked:false,disabled:false,attributes:{},className:'',setAttribute(k,v){this.attributes[k]=v;},after(...items){for(const item of items)elements.set(item.id,item);},click(){},});
  const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
  const document={createElement:element,getElementById:get,body:{dataset:{}},querySelectorAll:()=>[]};
  const start=(name)=>{const asyncStart=app.indexOf(`async function ${name}(`);return asyncStart>=0?asyncStart:app.indexOf(`function ${name}(`);};
  const slice=(name,next)=>app.slice(start(name),start(next));
  const context=vm.createContext({document,encodeURIComponent,console});
  vm.runInContext(`const $=id=>document.getElementById(id);let meshSnapshot={jobId:'accepted-12345678',digest:'stable',cells:[{cell_id:1}],qa:{}};let preserveAcceptedDuringRun=false,busy=false,revision=0,preflightId='old';let gmshAdvancedEnabled=false,plannedSource=null,plannedRequest=0;let cleared=0,reset=0;const api=async()=>({analysis_crs:'EPSG:32649',output_root:'runs',cell_source:{kind:'structured_grid',extent:[100,200,300,400],cell_width_m:20,cell_height_m:30}});const mode=()=>{};const stage=()=>{};const toast=()=>{};const clearComparison=()=>{};const clearAcceptedMesh=()=>{cleared++;};const setQualityMesh=()=>{};const resetResult=()=>{reset++;};const refreshCoach=()=>{};`,context);
  const invalidation=app.match(/function invalidate\(\)\{[^\n]+\}/)?.[0];
  assert.ok(invalidation,'production invalidation function is present');
  vm.runInContext(slice('restoreRemeshDraft','mountSingleAttributeTable')+slice('showRemeshDraftButton','discardRemeshDraft')+slice('discardRemeshDraft','restoreRemeshDraft')+invalidation,context);
  vm.runInContext('showRemeshDraftButton()',context);
  assert.equal(get('remesh-draft').textContent,'Chỉnh thông số / Chia lưới lại');
  await get('remesh-draft').onclick();
  assert.equal(vm.runInContext('preserveAcceptedDuringRun',context),true);
  assert.equal(get('west').value,100);
  get('width').value='45';
  vm.runInContext('invalidate()',context);
  assert.equal(vm.runInContext('meshSnapshot.digest',context),'stable');
  assert.equal(vm.runInContext('cleared',context),0);
  assert.equal(vm.runInContext('reset',context),0);
});

test('comparison workspace remains reachable during its own active job',()=>{
  const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,{hidden:false,open:false,attributes:{},setAttribute(k,v){this.attributes[k]=v;},showModal(){this.open=true;}});return elements.get(id);};
  const document={body:{dataset:{}},querySelectorAll:()=>[]};
  const context=vm.createContext({document,prepareComparison(){document.body.dataset.prepared=String(Number(document.body.dataset.prepared||0)+1);}});
  vm.runInContext(`const $=id=>elementsGet(id);let busy=true,comparisonJobId=null,comparisonOwnsBusy=true;function markWorkspace(value){document.body.dataset.workspace=value;}function stage(){}function showResultTab(){}function displayCellCount(){return 0;}function setExtractMode(){}const meshSnapshot=null;`,context);
  context.elementsGet=get;
  const start=name=>{const at=app.indexOf(`function ${name}(`);assert.ok(at>=0,`${name} exists`);return at;};
  vm.runInContext(app.slice(start('setWorkspace'),start('setupWorkspaceNavigation')),context);
  vm.runInContext(`setWorkspace('compare')`,context);
  assert.equal(document.body.dataset.workspace,'compare');
  assert.equal(document.body.dataset.stage,'5');
  assert.equal(get('comparison-workspace').hidden,false);
  assert.equal(document.body.dataset.prepared,'1');
  const openStart=app.indexOf('function openTools('),openEnd=app.indexOf('function closeTools(',openStart);
  vm.runInContext(app.slice(openStart,openEnd),context);
  vm.runInContext('openTools()',context);
  assert.equal(get('tools-dialog').open,true,'tools remain reachable before the comparison job id arrives');
  get('tools-dialog').open=false;
  vm.runInContext('comparisonOwnsBusy=false;openTools()',context);
  assert.equal(get('tools-dialog').open,false,'unrelated busy work cannot open the tools dialog');
});

test('closing research tools is allowed during comparison, while unrelated busy work blocks Escape',()=>{
  const dialog={closed:false,close(){this.closed=true;}};
  const document={getElementById:id=>id==='tools-dialog'?dialog:null};
  const context=vm.createContext({document});
  vm.runInContext(`const $=id=>document.getElementById(id);let busy=true,comparisonJobId=null,comparisonOwnsBusy=true;`,context);
  const closeStart=app.indexOf('function closeTools('),closeEnd=app.indexOf('function prepareComparison(',closeStart);
  vm.runInContext(app.slice(closeStart,closeEnd),context);
  vm.runInContext('closeTools()',context);
  assert.equal(dialog.closed,true);
  let prevented=false;
  context.event={preventDefault(){prevented=true;}};
  const cancelStart=app.indexOf('function handleToolsCancel('),cancelEnd=app.indexOf('function prepareComparison(',cancelStart);
  const researchStart=app.indexOf('function canOpenResearch('),researchEnd=app.indexOf('function prepareComparison(',researchStart);
  vm.runInContext(app.slice(cancelStart,cancelEnd)+app.slice(researchStart,researchEnd),context);
  assert.equal(vm.runInContext('canOpenResearch()',context),true,'research controls remain reachable during the owned comparison');
  vm.runInContext('handleToolsCancel(event)',context);
  assert.equal(prevented,false,'Escape can close the tools dialog during comparison');
  vm.runInContext(`comparisonOwnsBusy=false`,context);
  assert.equal(vm.runInContext('canOpenResearch()',context),false,'unrelated busy work cannot enter research tools');
  vm.runInContext('handleToolsCancel(event)',context);
  assert.equal(prevented,true,'Escape remains blocked for other active jobs');
});

test('opening research does not hide the comparison workspace panel',async()=>{
  const elements=new Map(),make=id=>({id,hidden:false,attributes:{},children:[],textContent:'',setAttribute(k,v){this.attributes[k]=v;},replaceChildren(){this.children=[];},append(...items){this.children.push(...items);}}),get=id=>{if(!elements.has(id))elements.set(id,make(id));return elements.get(id);};
  const document={getElementById:get,createElement:tag=>make(tag)};
  const context=vm.createContext({document,api:async()=>({method:{},baseline:{},selected:{},interpretation:[]}),detailRows(){},comparisonTable(){},concise:value=>String(value)});
  vm.runInContext(`const $=id=>document.getElementById(id);`,context);
  const start=app.indexOf('async function showResearch('),end=app.indexOf('function openTools(',start);
  vm.runInContext(app.slice(start,end),context);
  await vm.runInContext('showResearch()',context);
  assert.equal(get('comparison-panel').hidden,false);
  assert.equal(get('research-panel').hidden,false);
});

test('stale remesh configuration cannot overwrite a newer accepted mesh document',async()=>{
  const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,{value:'untouched',hidden:false,checked:false,className:'',textContent:'',setAttribute(){}});return elements.get(id);};
  const document={getElementById:get};
  let resolveConfig;
  const pendingConfig=new Promise(resolve=>{resolveConfig=resolve;});
  const context=vm.createContext({document,encodeURIComponent,api:()=>pendingConfig,toast(){}});
  vm.runInContext(`const $=id=>document.getElementById(id);let meshSnapshot={jobId:'old-mesh',crs:'EPSG:32649'},revision=4,busy=false,preserveAcceptedDuringRun=false,gmshAdvancedEnabled=false;function mode(){}function showRemeshDraftButton(){}function stage(){};`,context);
  const start=app.indexOf('async function restoreRemeshDraft('),end=app.indexOf('function mountSingleAttributeTable(',start);
  vm.runInContext(app.slice(start,end),context);
  const restoring=vm.runInContext('restoreRemeshDraft()',context);
  vm.runInContext(`meshSnapshot={jobId:'new-mesh',crs:'EPSG:32649'};revision=5`,context);
  resolveConfig({config:{analysis_crs:'EPSG:32649',cell_source:{kind:'structured_grid',extent:[1,2,3,4]}}});
  await restoring;
  assert.equal(get('mode').value,'untouched');
  assert.equal(vm.runInContext('preserveAcceptedDuringRun',context),false);
});

