// Run with node --test. Covers the imported-mesh -> raster-picker -> real
// Extract button -> preflight -> run handoff using the production UI code.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const script=fs.readFileSync(process.env.MESH_UI_SCRIPT||path.join(__dirname,'../src/gis_mesh_workbench/webui/app.js'),'utf8');

function setup(){
  const elements=new Map(),storage=new Map(),calls=[];
  class Element{
    constructor(){this.children=[];this.value='';this.checked=true;this.hidden=false;this.disabled=false;this.attributes={};this.dataset={};this.selectedOptions=[{textContent:'test'}];this.classes=new Set();this.classList={toggle:(name,on)=>{on?this.classes.add(name):this.classes.delete(name);},remove:name=>this.classes.delete(name),add:name=>this.classes.add(name)};}
    append(...items){this.children.push(...items);} before(){} replaceChildren(...items){this.children=items;}
    setAttribute(name,value){this.attributes[name]=String(value);if(name==='data-id')this.dataset.id=String(value);}
    getAttribute(name){return this.attributes[name]??null;} removeAttribute(name){delete this.attributes[name];}
    addEventListener(name,callback){this.handlers??={};this.handlers[name]=callback;} focus(){this.focused=true;} scrollIntoView(){} click(){this.clicked=true;}
    querySelectorAll(selector){const descendants=this.children.flatMap(c=>[c,...c.querySelectorAll(selector)]);return selector==='input,select,button'?descendants:descendants.filter(c=>selector==='[data-id]'?c.dataset.id:(c.attributes.class||'').split(' ').includes(selector.slice(1)));}
  }
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  Object.assign(get('crs'),{value:'EPSG:32649'});get('band').value='1';get('extract-band').value='1';get('extract-semantics').value='intensive';get('subdivisions').value='8';get('max-cells').value='1000';get('dem-band').value='1';get('gmsh-sizing').value='uniform';get('raster').value='a.tif';get('mode').value='structured_grid';
  get('extract-options').children.push(get('extract-raster'),get('extract-band'),get('extract-units'),get('extract-semantics'),get('extract-run'));
  const context=vm.createContext({document:{getElementById:get,createElement:()=>new Element(),createElementNS:()=>new Element(),querySelector:selector=>get(selector),querySelectorAll:()=>[],body:{dataset:{}}},window:{matchMedia:()=>({matches:false})},location:{hash:'',pathname:'/',reload(){}},sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,String(value)),removeItem:key=>storage.delete(key)},URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},Blob,atob,Uint8Array,URLSearchParams,history:{},coach:{update(){}},setTimeout:()=>1,clearTimeout(){},console,fetch(){throw Error('mock API not configured');}});
  vm.runInContext(script.split("$('form').addEventListener")[0],context);
  vm.runInContext(script.slice(script.indexOf('function showMeshQuality('),script.indexOf("$('extract-run').onclick")),context);
  vm.runInContext(script.slice(script.indexOf("$('extract-run').onclick"),script.indexOf("$('try-sample').onclick")),context);
  context.calls=calls;
  return {get,context,calls,run:code=>vm.runInContext(code,context)};
}

test('imported .mcy mesh, newly selected extraction raster, and button reach extract preflight and run',async()=>{
  const t=setup();
  t.context.fetch=async(url,options)=>{
    if(url==='/api/project/open')return{ok:true,json:async()=>({job_id:'saved-mesh-123',has_attributes:false})};
    throw Error('unexpected fetch '+url);
  };
  t.run(`api=async(path,body)=>{
    calls.push({path,body});
    if(path.includes('id=extract-job-123&name=manifest'))return{operation:'extract',runtime_seconds:1,engine_evidence:{pixels_read:4,windows_read:1,candidate_pairs:1,memory_metric:{value:1024}}};
    if(path.includes('id=extract-job-123&name=cells'))return{cells:[{cell_id:'cell-1',kind:'rectangle',xy:[[0,0],[1,0],[1,1],[0,1]]}],display:{cell_count:1}};
    if(path.includes('id=extract-job-123&name=attributes'))return{cells:[{cell_id:'cell-1',value:3,coverage:'full'}],summary:{full_count:1},qa:{},provenance:{source_path:'D:/inputs/fresh.asc',band:1,analysis_crs_wkt:'EPSG:32649',value_units:'m'}};
    if(path.includes('id=extract-job-123&name=qa'))return{coverage:{valid_coverage:{mean:1},footprint_coverage:{mean:1}}};
    if(path.startsWith('/api/job?id=extract-job-123'))return{status:'pass',stage:'complete',result:{output:'runs/extract'}};
    if(path.startsWith('/api/job-log?id=extract-job-123'))return{text:'',next_cursor:0};
    if(path==='/api/job?id=saved-mesh-123')return{status:'pass',result:{output:'runs/saved-mesh'}};
    if(path.includes('name=manifest'))return{status:'pass',operation:'mesh_only',geometry_digest:'saved-digest'};
    if(path.includes('name=cells'))return{status:'pass',crs_wkt:'EPSG:32649',cells:[{cell_id:'cell-1',kind:'rectangle',xy:[[0,0],[1,0],[1,1],[0,1]]}],display:{cell_count:1500}};
    if(path.includes('name=qa'))return{status:'pass',mesh_quality:{cell_count:1}};
    if(path==='/api/recent')return{items:[]};
    if(path==='/api/source')return{display_footprint:{xy:[[0,0],[1,0],[1,1],[0,1]],bounds:[0,0,1,1]}};
    if(path==='/api/preflight')return{preflight_id:'preflight-extract-123',report:{status:'pass',operation:'extract',cell_count:1,pixel_count:4,workload_estimate:{estimated_pixels_read:4,estimated_windows_read:1},geometry_digest:'extract-digest'}};
    if(path==='/api/run')return{job_id:'extract-job-123',operation:'extract'};
    throw Error('unexpected api '+path);
  };
  stage=value=>{currentStage=value;};draw=(cells,records=[])=>{geometry=cells;attrs=new Map(records.map(record=>[record.cell_id,record]));};showMeshQuality=()=>{};refreshCoach=()=>{};toast=()=>{};showResultTab=()=>{};`);
  await t.run("openProject({name:'mesh.mcy',size:1024})");
  assert.equal(t.run('meshSnapshot.jobId'),'saved-mesh-123');
  assert.equal(t.get('max-cells').value,'1000','importing a mesh must not raise the structured-grid UI cap');
  assert.equal(t.get('extract-options').hidden,false);
  await t.run("acceptSelectedSource('extract-raster','D:/inputs/fresh.asc')");
  t.get('extract-band').value='1';
  t.get('extract-units').value='m';
  await t.get('extract-run').onclick();
  const preflight=t.calls.find(call=>call.path==='/api/preflight');
  assert.ok(preflight,'Extract click must reach preflight; calls='+JSON.stringify(t.calls)+'; error='+t.get('validation').textContent+'; mesh='+t.run('meshSnapshot?.jobId'));
  assert.deepEqual(JSON.parse(JSON.stringify(preflight.body)),{
    schema_version:'gis-workbench-run-config-v2',operation:'extract',extraction_engine:'indexed_windowed_v1',run_name:'mesh-cherry-extract',analysis_crs:'EPSG:32649',raster_path:'D:/inputs/fresh.asc',band:1,semantics:'intensive',value_units:'m',output_root:'',edge_subdivisions:8,
    cell_source:{kind:'frozen_cells',path:'runs/saved-mesh/cells.json',geometry_digest:'saved-digest',max_cells:1500}
  });
  const run=t.calls.find(call=>call.path==='/api/run');
  assert.ok(run,'passing extract preflight must reach startRun');
  assert.equal(run.body.preflight_id,'preflight-extract-123');
  assert.equal(t.run('busy'),false,'extract poll did not finish: '+t.get('job-status').textContent+'; calls='+JSON.stringify(t.calls));
  assert.equal(t.context.document.body.dataset.busy,'false');
  assert.equal(t.run('jobId'),'extract-job-123');
  assert.equal(t.get('exports').hidden,false);
  assert.equal(t.get('csv').hidden,false,'extract result artifact flow failed: '+t.get('job-status').textContent+'; calls='+JSON.stringify(t.calls));
  assert.equal(t.run('attrs.get("cell-1").value'),3);
  assert.match(t.get('extract-status').textContent,/Hoàn thành|trích|extract/i);
});

test('failed extraction preflight returns a visible result beside the button and never starts a job',async()=>{
  const t=setup();
  t.run(`meshSnapshot={jobId:'saved-mesh-456',path:'runs/saved-mesh/cells.json',digest:'saved-digest',crs:'EPSG:32649',cells:[{cell_id:'a'}],display:{cell_count:1},qa:{mesh_quality:{}}};
    $('extract-raster').value='D:/inputs/bad.asc';$('extract-units').value='m';extractMode=false;
    calls.length=0;api=async(path,body)=>{calls.push({path,body});if(path==='/api/preflight')return{preflight_id:null,report:{status:'fail',operation:'extract',errors:['Raster không phủ ô lưới.']}};throw Error('unexpected request '+path)};
    stage=value=>{currentStage=value;};draw=()=>{};showMeshQuality=()=>{};refreshCoach=()=>{};toast=()=>{};showResultTab=()=>{};`);
  await t.get('extract-run').onclick();
  assert.equal(t.run('extractMode'),true);
  assert.equal(t.run('preflightId'),null);
  assert.equal(t.calls.filter(call=>call.path==='/api/preflight').length,1);
  assert.equal(t.calls.some(call=>call.path==='/api/run'),false);
  assert.equal(t.run('meshSnapshot.jobId'),'saved-mesh-456');
  assert.match(t.get('extract-status').textContent,/Raster không phủ ô lưới/);
});

test('terminal artifact retry keeps extraction inputs locked until the accepted result is hydrated',async()=>{
  const t=setup();
  t.run(`jobId='extract-retry-123';extractMode=true;
    meshSnapshot={jobId:'saved-mesh-456',path:'runs/saved-mesh/cells.json',digest:'saved-digest',crs:'EPSG:32649',cells:[{cell_id:'a'}],display:{cell_count:1},hasMsh:true,qa:{mesh_quality:{}}};
    setBusy(true);
    api=async path=>{if(path.startsWith('/api/job?id=extract-retry-123'))return{status:'pass',stage:'complete',result:{output:'runs/extract'}};if(path.startsWith('/api/job-log'))return{text:'',next_cursor:0};throw Error('temporary artifact read failure')};
    stage=value=>{currentStage=value;};draw=()=>{};showMeshQuality=()=>{};showResultTab=()=>{};`);
  await t.run('pollJob()');
  assert.equal(t.run('busy'),true,'inputs must stay locked while a terminal extract still needs artifact hydration');
  assert.equal(t.get('extract-raster').disabled,true);
  assert.equal(t.run('meshSnapshot.jobId'),'saved-mesh-456');
  assert.equal(t.run('jobId'),'extract-retry-123');
  assert.match(t.get('job-status').textContent,/temporary artifact read failure/);
});

test('permanent terminal artifact 404 unlocks inputs, keeps accepted mesh exports, and offers explicit retry',async()=>{
  const t=setup();
  t.run(`jobReads=0;jobId='extract-missing-123';extractMode=true;
    meshSnapshot={jobId:'saved-mesh-456',output:'runs/saved-mesh',path:'runs/saved-mesh/cells.json',digest:'saved-digest',crs:'EPSG:32649',cells:[{cell_id:'a'}],display:{cell_count:1},hasMsh:true,qa:{mesh_quality:{}}};
    setBusy(true);
    api=async path=>{if(path.startsWith('/api/job?id=extract-missing-123')){jobReads++;return{status:'pass',stage:'complete',result:{output:'runs/extract'}};}if(path.startsWith('/api/job-log'))return{text:'',next_cursor:0};if(path.includes('name=manifest'))throw Object.assign(Error('artifact not found'),{status:404});throw Error('unexpected request '+path)};
    stage=value=>{currentStage=value;};draw=()=>{};showMeshQuality=()=>{};showResultTab=()=>{};`);
  await t.run('pollJob()');
  assert.equal(t.run('busy'),false,'permanent artifact failures must release the UI; status='+t.get('job-status').textContent+'; count='+t.run('terminalArtifactFailureCount'));
  assert.equal(t.context.document.body.dataset.busy,'false');
  assert.equal(t.get('extract-raster').disabled,false);
  assert.equal(t.run('meshSnapshot.jobId'),'saved-mesh-456');
  assert.equal(t.run('jobId'),'extract-missing-123','retain the terminal extract identity for a user retry');
  assert.equal(t.get('exports').hidden,false);
  assert.equal(t.get('csv').hidden,true,'do not expose incomplete extract data');
  assert.equal(t.get('json').hidden,true,'do not expose incomplete extract data');
  assert.equal(t.get('export-run').disabled,true,'do not enable raster export from incomplete extract artifacts');
  const retry=t.get('monitor-headline').children.find(child=>child.textContent==='Thử tải lại kết quả');
  assert.ok(retry,'permanent failures must provide a visible explicit retry');
  await retry.onclick();
  assert.equal(t.run('jobReads'),2,'explicit retry should re-read the terminal job');
  assert.equal(t.run('busy'),false,'failed explicit retry must also return control to the user');
});

test('transient terminal artifact failures stop after a bounded number of automatic reads',async()=>{
  const t=setup();
  t.run(`jobId='extract-exhaust-123';extractMode=true;meshSnapshot={jobId:'saved-mesh-789',path:'runs/saved-mesh/cells.json',digest:'saved-digest',crs:'EPSG:32649',cells:[{cell_id:'a'}],display:{cell_count:1},qa:{mesh_quality:{}}};setBusy(true);
    api=async path=>{if(path.startsWith('/api/job?id=extract-exhaust-123'))return{status:'pass',stage:'complete',result:{output:'runs/extract'}};if(path.startsWith('/api/job-log'))return{text:'',next_cursor:0};if(path.includes('name=manifest'))throw Error('temporary artifact read failure');throw Error('unexpected request '+path)};
    stage=value=>{currentStage=value;};draw=()=>{};showMeshQuality=()=>{};showResultTab=()=>{};`);
  await t.run('pollJob()');
  await t.run('pollJob()');
  await t.run('pollJob()');
  assert.equal(t.run('busy'),false,'retry exhaustion must release the UI');
  assert.equal(t.run('meshSnapshot.jobId'),'saved-mesh-789');
  assert.ok(t.get('monitor-headline').children.some(child=>child.textContent==='Thử tải lại kết quả'),'bounded exhaustion must leave an explicit retry path');
  assert.equal(t.get('csv').hidden,true);
});

test('imported mesh extraction cap never exceeds the hard limit or changes the structured-grid field',()=>{
  const t=setup();
  t.run(`requireDomainSelection=()=>{};extractMode=true;meshSnapshot={jobId:'saved-mesh-cap',path:'runs/mesh/cells.json',digest:'mesh-digest',crs:'EPSG:32649',cells:[{}],display:{cell_count:100001},qa:{mesh_quality:{}}};`);
  assert.equal(t.run('config().cell_source.max_cells'),100000);
  assert.equal(t.get('max-cells').value,'1000');
});

test('preflight made stale by an input revision cannot start a run',async()=>{
  const t=setup();let finish;
  const pending=new Promise(resolve=>{finish=resolve;});t.context.deferredPreflight=pending;t.context.finishPreflight=finish;
  t.run(`meshSnapshot={jobId:'saved-mesh-stale',path:'runs/saved-mesh/cells.json',digest:'saved-digest',crs:'EPSG:32649',cells:[{cell_id:'a'}],display:{cell_count:1},qa:{mesh_quality:{}}};
    $('extract-raster').value='D:/inputs/fresh.asc';$('extract-units').value='m';calls.length=0;
    api=async(path,body)=>{calls.push({path,body});if(path==='/api/preflight')return deferredPreflight;throw Error('unexpected request '+path)};
    stage=value=>{currentStage=value;};draw=()=>{};showMeshQuality=()=>{};refreshCoach=()=>{};toast=()=>{};showResultTab=()=>{};`);
  const running=t.get('extract-run').onclick();
  for(let i=0;i<4;i++)await Promise.resolve();
  assert.ok(t.calls.some(call=>call.path==='/api/preflight'));
  t.run('revision++');
  t.context.finishPreflight({preflight_id:'stale-preflight-123',report:{status:'pass',operation:'extract',cell_count:1,pixel_count:4,workload_estimate:{estimated_pixels_read:4,estimated_windows_read:1},geometry_digest:'stale'}});
  await running;
  assert.equal(t.calls.some(call=>call.path==='/api/run'),false);
  assert.equal(t.run('preflightId'),null);
  assert.equal(t.run('meshSnapshot.jobId'),'saved-mesh-stale');
});

test('temporary terminal artifact failure retries and eventually publishes a completed extract',async()=>{
  const t=setup();t.context.manifestReads=0;
  t.run(`jobId='extract-recover-123';extractMode=true;meshSnapshot={jobId:'saved-mesh-recover',path:'runs/saved-mesh/cells.json',digest:'saved-digest',crs:'EPSG:32649',cells:[{cell_id:'a'}],display:{cell_count:1},hasMsh:true,qa:{mesh_quality:{}}};setBusy(true);
    api=async path=>{
      if(path.startsWith('/api/job?id=extract-recover-123'))return{status:'pass',stage:'complete',result:{output:'runs/extract'}};
      if(path.startsWith('/api/job-log'))return{text:'',next_cursor:0};
      if(path.includes('id=extract-recover-123&name=manifest')){manifestReads++;if(manifestReads===1)throw Error('temporary artifact read failure');return{operation:'extract',runtime_seconds:1,engine_evidence:{pixels_read:4,windows_read:1,candidate_pairs:1,memory_metric:{value:1024}}};}
      if(path.includes('id=extract-recover-123&name=cells'))return{cells:[{cell_id:'a',xy:[[0,0],[1,0],[1,1],[0,1]]}],display:{cell_count:1}};
      if(path.includes('id=extract-recover-123&name=attributes'))return{cells:[{cell_id:'a',value:5,coverage:'full'}],summary:{full_count:1},qa:{},provenance:{source_path:'D:/inputs/fresh.asc',band:1,analysis_crs_wkt:'EPSG:32649',value_units:'m'}};
      if(path.includes('id=extract-recover-123&name=qa'))return{coverage:{valid_coverage:{mean:1},footprint_coverage:{mean:1}}};
      if(path==='/api/source')return{display_footprint:null};
      throw Error('unexpected request '+path);
    };draw=(cells,records=[])=>{geometry=cells;attrs=new Map(records.map(record=>[record.cell_id,record]));};showMeshQuality=()=>{};showResultTab=()=>{};`);
  await t.run('pollJob()');
  assert.equal(t.run('busy'),true);
  assert.equal(t.run('meshSnapshot.jobId'),'saved-mesh-recover');
  await t.run('pollJob()');
  assert.equal(t.run('busy'),false);
  assert.equal(t.run('manifestReads'),2);
  assert.equal(t.get('csv').hidden,false);
  assert.match(t.get('extract-status').textContent,/Sẵn sàng xuất CSV \/ JSON/);
});
