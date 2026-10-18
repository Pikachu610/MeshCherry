// Run with node --test. Covers the imported-mesh -> raster-picker -> real
// Extract button -> preflight -> run handoff using the production UI code.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const script=fs.readFileSync(process.env.MESH_UI_SCRIPT||path.join(__dirname,'../src/gis_mesh_workbench/webui/app.js'),'utf8');
const attributeTableScript=fs.readFileSync(path.join(__dirname,'../src/gis_mesh_workbench/webui/attribute_table.js'),'utf8');

function setup(){
  const elements=new Map(),storage=new Map(),calls=[];
  class Element{
    constructor(){this.children=[];this.value='';this.checked=true;this.hidden=false;this.disabled=false;this.attributes={};this.dataset={};this.selectedOptions=[{textContent:'test'}];this.classes=new Set();this.classList={toggle:(name,on)=>{on?this.classes.add(name):this.classes.delete(name);},remove:name=>this.classes.delete(name),add:name=>this.classes.add(name)};}
    append(...items){this.children.push(...items);} appendChild(item){this.children.push(item);return item;} before(){} replaceChildren(...items){this.children=items;}
    setAttribute(name,value){this.attributes[name]=String(value);if(name==='data-id')this.dataset.id=String(value);}
    getAttribute(name){return this.attributes[name]??null;} removeAttribute(name){delete this.attributes[name];}
    addEventListener(name,callback){this.handlers??={};this.handlers[name]=callback;} focus(){this.focused=true;} scrollIntoView(){} click(){this.clicked=true;}
    querySelectorAll(selector){const descendants=this.children.flatMap(c=>[c,...c.querySelectorAll(selector)]);return selector==='input,select,button'?descendants:descendants.filter(c=>selector==='[data-id]'?c.dataset.id:(c.attributes.class||'').split(' ').includes(selector.slice(1)));}
  }
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  Object.assign(get('crs'),{value:'EPSG:32649'});get('band').value='1';get('extract-band').value='1';get('extract-semantics').value='intensive';get('subdivisions').value='8';get('max-cells').value='1000';get('dem-band').value='1';get('gmsh-sizing').value='uniform';get('raster').value='a.tif';get('mode').value='structured_grid';
  get('extract-options').children.push(get('extract-raster'),get('extract-band'),get('extract-units'),get('extract-semantics'),get('extract-run'));
  const draftState={confirmed:false};const window={matchMedia:()=>({matches:false})};window.MeshCherryExtractionDraft={mount:(_container,options)=>{draftState.options=options;const ready=Promise.resolve(options.previewPage({page:1,pageSize:50,mesh_job_id:options.meshJobId,draft:null})).catch(error=>{draftState.error=error.message;});const instance={ready,setRunning(){},destroy(){},getPlan(){return{};},async confirm(draft){try{await options.onConfirm(draft);draftState.confirmed=true;return true;}catch(error){draftState.confirmed=false;draftState.confirmError=error;throw error;}}};draftState.instance=instance;return instance;}};
  const context=vm.createContext({document:{getElementById:get,createElement:()=>new Element(),createElementNS:()=>new Element(),querySelector:selector=>get(selector),querySelectorAll:()=>[],body:{dataset:{}}},window,location:{hash:'',pathname:'/',reload(){}},sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,String(value)),removeItem:key=>storage.delete(key)},URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},Blob,atob,Uint8Array,URLSearchParams,history:{},coach:{update(){}},setTimeout:()=>1,clearTimeout(){},console,fetch(){throw Error('mock API not configured');}});context.draftState=draftState;
  vm.runInContext(script.split("$('form').addEventListener")[0],context);
  vm.runInContext(script.slice(script.indexOf('function showMeshQuality('),script.indexOf("$('extract-run').onclick")),context);
  vm.runInContext(script.slice(script.indexOf("$('extract-run').onclick"),script.indexOf("$('try-sample').onclick")),context);
  context.calls=calls;
  return {get,context,calls,storage,run:code=>vm.runInContext(code,context)};
}

function seedSingleDraft(t, startHandler=async(_path,body)=>({job_id:'batch-draft-123',request_id:body.request_id,operation:'batch_extract'})){
  t.context.startHandler=startHandler;
  t.run(`meshSnapshot={jobId:'draft-mesh-123',path:'runs/draft-mesh/cells.json',digest:'draft-digest',crs:'EPSG:32649',cells:[{cell_id:'a'}],display:{cell_count:1},qa:{mesh_quality:{}}};
    $('extract-raster').value='D:/inputs/elevation.tif';$('extract-band').value='1';$('extract-units').value='m';
    calls.length=0;api=async(path,body)=>{calls.push({path,body});if(path==='/api/extraction-draft/preview')return{draft:{mesh_job_id:'draft-mesh-123',geometry_digest:'draft-digest',columns:[{id:'raster-1',type:'raster',source_name:'elevation',name:'elevation'}],rows:[],excluded_cell_ids:[],manual_rows:[]},plan_hash:'draft-plan-hash',columns:[{id:'raster-1',type:'raster',source_name:'elevation',name:'elevation'}],rows:[],accepted_cells:1,total_rows:1,offset:0,limit:50};if(path==='/api/batch/start')return startHandler(path,body);if(path.startsWith('/api/job?id=batch-draft-123'))return{status:'running',stage:'extract'};throw Error('unexpected API '+path)};
    stage=value=>{currentStage=value;};draw=()=>{};showMeshQuality=()=>{};refreshCoach=()=>{};toast=()=>{};showResultTab=()=>{};`);
  return t.get('extract-run').onclick();
}
const validDraft={mesh_job_id:'draft-mesh-123',geometry_digest:'draft-digest',columns:[{id:'raster-1',type:'raster',source_name:'elevation',name:'elevation'}],rows:[],excluded_cell_ids:[],manual_rows:[]};

test('stable draft states render with the real table string-key callback contract',()=>{
  const t=setup();const rows=t.run("displayDraftRows([{cell_id:'c1',values:{a:{state:'computed',value:12},b:{state:'no_data',value:null},c:{state:'not_computed',status:'queued'}}},{row_kind:'manual',row_id:'note1',values:{a:null}}],['a','b','c'])");
  assert.equal(rows[0].values.a,12);assert.equal(rows[0].values.b,'NoData');assert.equal(rows[0].values.c,'Chưa tính');assert.equal(rows[1].id,'Nhập tay · note1');
});
test('single extraction previews a canonical draft and starts only after confirmation',async()=>{
  const t=setup();
  t.context.fetch=async(url,options)=>{
    if(url==='/api/project/open')return{ok:true,json:async()=>({job_id:'saved-mesh-123',has_attributes:false})};
    throw Error('unexpected fetch '+url);
  };
  t.run(`sourceReads=0;api=async(path,body)=>{
    calls.push({path,body});
    if(path==='/api/extraction-draft/preview'){const draft=body.draft||{mesh_job_id:'saved-mesh-123',geometry_digest:'saved-digest',columns:[{id:'raster-1',type:'raster',source_name:'fresh',name:'fresh'}],rows:[],excluded_cell_ids:[],manual_rows:[]};return{draft,plan_hash:'draft-hash',columns:draft.columns,rows:[],total_rows:1,total_cells:1,offset:0,limit:50};}
    if(path==='/api/batch/start')return{job_id:'batch-extract-123',request_id:body.request_id,operation:'batch_extract'};
    if(path.startsWith('/api/job?id=batch-extract-123'))return{status:'running',stage:'extract'};
    if(path==='/api/job?id=saved-mesh-123')return{status:'pass',result:{output:'runs/saved-mesh'}};
    if(path.includes('name=manifest'))return{status:'pass',operation:'mesh_only',geometry_digest:'saved-digest'};
    if(path.includes('name=cells'))return{status:'pass',crs_wkt:'EPSG:32649',cells:[{cell_id:'cell-1',kind:'rectangle',xy:[[0,0],[1,0],[1,1],[0,1]]}],display:{cell_count:1001}};
    if(path.includes('name=qa'))return{status:'pass',mesh_quality:{cell_count:1}};
    if(path==='/api/recent')return{items:[]};
    if(path==='/api/source'){sourceReads++;if(sourceReads===2)throw Object.assign(Error('raster source metadata not found'),{status:404});return{display_footprint:{xy:[[0,0],[1,0],[1,1],[0,1]],bounds:[0,0,1,1]}};}
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
  const preview=t.calls.find(call=>call.path==='/api/extraction-draft/preview');
  assert.ok(preview,'extract click must open the draft preview');
  assert.equal(preview.body.draft,undefined,'the first preview initializes from the selected raster');
  assert.deepEqual(JSON.parse(JSON.stringify(preview.body.features)),[{name:'fresh',path:'D:/inputs/fresh.asc',band:1,semantics:'intensive',value_units:'m'}]);
  assert.equal(t.calls.some(call=>call.path==='/api/batch/start'),false,'preview must not compute raster values');
  const draft=t.context.draftState.options;
  const plan=await draft.onChange({mesh_job_id:'saved-mesh-123',geometry_digest:'saved-digest',columns:[{id:'raster-1',type:'raster',source_name:'fresh',name:'renamed'}],rows:[],excluded_cell_ids:[],manual_rows:[]});
  await t.context.draftState.instance.confirm(plan.draft);
  assert.equal(t.context.draftState.confirmed,true);
  const started=t.calls.find(call=>call.path==='/api/batch/start');
  assert.ok(started,'confirmation should start the one-feature batch engine');
  assert.equal(started.body.plan_hash,'draft-hash');
  assert.equal(started.body.draft.columns[0].name,'renamed');
  assert.equal(t.get('batch-panel').hidden,false,'single-mode run controls and results stay visible');
});

test('single extraction draft preview errors do not start a batch',async()=>{
  const t=setup();
  t.run(`meshSnapshot={jobId:'saved-mesh-456',path:'runs/saved-mesh/cells.json',digest:'saved-digest',crs:'EPSG:32649',cells:[{cell_id:'a'}],display:{cell_count:1},qa:{mesh_quality:{}}};
    $('extract-raster').value='D:/inputs/bad.asc';$('extract-units').value='m';extractMode=false;
    calls.length=0;api=async(path,body)=>{calls.push({path,body});if(path==='/api/extraction-draft/preview')throw Error('Raster không phủ ô lưới.');throw Error('unexpected request '+path)};
    stage=value=>{currentStage=value;};draw=()=>{};showMeshQuality=()=>{};refreshCoach=()=>{};toast=()=>{};showResultTab=()=>{};`);
  await t.get('extract-run').onclick();
  assert.equal(t.run('extractMode'),true);
  assert.equal(t.calls.filter(call=>call.path==='/api/extraction-draft/preview').length,1);
  assert.equal(t.calls.some(call=>call.path==='/api/batch/start'),false);
  assert.equal(t.run('meshSnapshot.jobId'),'saved-mesh-456');
  assert.equal(t.context.draftState.error,'Raster không phủ ô lưới.');
});

test('source or accepted mesh changes before confirmation reject the captured draft',async()=>{
  for(const mutation of ['source','mesh']){
    const t=setup();await seedSingleDraft(t);
    if(mutation==='source')t.get('extract-raster').value='D:/inputs/other.tif';
    else t.run(`meshSnapshot={...meshSnapshot,jobId:'replacement-mesh',digest:'replacement-digest'};`);
    await assert.rejects(t.context.draftState.instance.confirm({...validDraft}),/đã thay đổi lưới|đã thay đổi/);
    assert.equal(t.context.draftState.confirmed,false);
    assert.equal(t.calls.some(call=>call.path==='/api/batch/start'),false);
  }
});

test('source or mesh changes while final confirmation preview is pending prevent batch start',async()=>{
  for(const mutation of ['source','mesh']){
    const t=setup();await seedSingleDraft(t);
    let releasePreview;
    t.context.confirmPreview=new Promise(resolve=>{releasePreview=resolve;});
    t.context.releaseConfirmPreview=releasePreview;
    t.run(`confirmPreviewActive=false;api=async(path,body)=>{calls.push({path,body});if(path==='/api/extraction-draft/preview'&&confirmPreviewActive)return confirmPreview;if(path==='/api/extraction-draft/preview')return{draft:${JSON.stringify(validDraft)},plan_hash:'draft-plan-hash',columns:${JSON.stringify(validDraft.columns)},rows:[],accepted_cells:1,total_rows:1,offset:0,limit:50};throw Error('unexpected API '+path)};`);
    t.context.confirmPreviewActive=true;
    const confirmation=t.context.draftState.instance.confirm({...validDraft});
    await new Promise(resolve=>setTimeout(resolve,0));
    if(mutation==='source')t.get('extract-raster').value='D:/inputs/changed.tif';
    else t.run(`meshSnapshot={...meshSnapshot,jobId:'replacement-mesh',digest:'replacement-digest'};`);
    t.context.releaseConfirmPreview({draft:validDraft,plan_hash:'draft-plan-hash',columns:validDraft.columns,rows:[],accepted_cells:1,total_rows:1,offset:0,limit:50});
    await assert.rejects(confirmation,/đã thay đổi lưới|đã thay đổi/);
    assert.equal(t.calls.some(call=>call.path==='/api/batch/start'),false);
  }
});

test('HTTP 400 on confirmation propagates and leaves the draft unconfirmed',async()=>{
  const t=setup();await seedSingleDraft(t,async()=>{throw Object.assign(Error('draft rejected'),{status:400});});
  await assert.rejects(t.context.draftState.instance.confirm({...validDraft}),/draft rejected/);
  assert.equal(t.context.draftState.confirmed,false);
  assert.equal(t.run('batchStartUncertain'),false);
  assert.equal(t.run('batchJobId'),null);
  assert.equal(t.run('batchRequestId'),null);
  assert.match(t.get('batch-status').textContent,/HTTP 400/);
});

test('batch start retains the accepted mesh identity if the global mesh changes while awaiting response',async()=>{
  const t=setup();let finish;
  t.context.deferredBatchStart=new Promise(resolve=>{finish=resolve;});t.context.finishBatchStart=finish;
  await seedSingleDraft(t,(_path)=>t.context.deferredBatchStart);
  const confirm=t.context.draftState.instance.confirm({...validDraft});
  await new Promise(resolve=>setTimeout(resolve,0));
  const request=t.calls.find(call=>call.path==='/api/batch/start');assert.ok(request,t.context.draftState.confirmError?.message||'batch start request was not sent');
  assert.equal(request.body.mesh_job_id,'draft-mesh-123');
  t.run(`meshSnapshot={...meshSnapshot,jobId:'replacement-mesh',digest:'replacement-digest'};`);
  t.context.finishBatchStart({job_id:'batch-draft-123',request_id:request.body.request_id,operation:'batch_extract'});
  await confirm;
  assert.equal(t.run('batchMeshId'),'draft-mesh-123');
  const recovered=JSON.parse(t.storage.get('mesh-cherry-batch-recovery'));
  assert.equal(recovered.mesh_job_id,'draft-mesh-123');
  assert.equal(recovered.draft.geometry_digest,'draft-digest');
});

test('recovered result discovers canonical raster and manual columns before paging values by stable IDs',async()=>{
  const t=setup(),columns=Array.from({length:24},(_,index)=>({id:`raster-${index+1}`,type:'raster',source_name:`source-${index+1}`,name:`renamed-${index+1}`}));
  columns.push({id:'manual-1',type:'manual',name:'field notes',value_type:'text'});
  const feature={name:'original',path:'D:/inputs/original.tif',band:1,semantics:'intensive',value_units:'m'};
  t.storage.set('mesh-cherry-batch-recovery',JSON.stringify({version:1,state:'completed',job_id:'batch-reload-123',mesh_job_id:'mesh-reload-123',features:[feature],terminal_status:'pass'}));
  let tableOptions;
  t.context.MeshCherryAttributeTable={mount:(_container,options)=>{tableOptions=options;return{ready:Promise.resolve(true),destroy(){}};}};
  t.context.canonicalColumns=columns;
  t.run(`meshSnapshot={jobId:'mesh-reload-123',path:'runs/mesh/cells.json',digest:'mesh-digest',crs:'EPSG:32649',cells:[],display:{cell_count:1},qa:{mesh_quality:{}}};
    api=async(path,body)=>{calls.push({path,body});if(path==='/api/batch/preview'&&body.offset===0&&!Object.hasOwn(body,'visible_columns'))return{batch:{status:'pass',cell_count:1,draft:{columns:canonicalColumns}},columns:canonicalColumns,rows:[{row_kind:'cell',cell_id:'cell-1',values:{'raster-1':{state:'computed',value:12.5},'manual-1':'checked'}}],total_cells:1,total_rows:1,offset:0};if(path==='/api/batch/preview')return{batch:{status:'pass',cell_count:1},columns:canonicalColumns,rows:[{row_kind:'cell',cell_id:'cell-2',values:{'raster-1':{state:'computed',value:9},'manual-1':'second'}}],total_cells:1,total_rows:1,offset:50};throw Error('unexpected request '+path)};`);
  t.run("restorePendingBatchForMesh('mesh-reload-123')");
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(tableOptions.featureColumns.length,25,'the chooser receives all canonical columns, including manual columns');
  const first=await tableOptions.fetchPage({page:1,pageSize:50,search:'',columns:['raster-1','manual-1'],signal:null});
  assert.equal(first.rows[0].values['raster-1'],12.5,'computed raster values remain keyed by stable column ID after reload');
  assert.equal(first.rows[0].values['manual-1'],'checked');
  await tableOptions.fetchPage({page:2,pageSize:50,search:'',columns:['raster-1','manual-1'],signal:null});
  const pageRequest=t.calls.find(call=>call.path==='/api/batch/preview'&&Object.hasOwn(call.body,'visible_columns'));
  assert.deepEqual(JSON.parse(JSON.stringify(pageRequest.body.visible_columns)),['raster-1','manual-1']);
});

test('real table keeps recovered canonical columns and projects page and search requests by stable IDs',async()=>{
  const t=setup(),columns=Array.from({length:24},(_,index)=>({id:`stable-${index+1}`,type:'raster',source_name:`source-${index+1}`,name:`canonical-${index+1}`}));
  columns.push({id:'manual-stable',type:'manual',name:'manual notes',value_type:'text'});
  const feature={name:'old-feature-name',path:'D:/inputs/old.tif',band:1,semantics:'intensive',value_units:'m'};
  t.storage.set('mesh-cherry-batch-recovery',JSON.stringify({version:1,state:'completed',job_id:'batch-reload-456',mesh_job_id:'mesh-reload-456',features:[feature],terminal_status:'pass'}));
  t.context.document.createTextNode=text=>{const node=new (t.get('batch-table').constructor)();node.textContent=String(text);return node;};
  t.get('batch-table').ownerDocument=t.context.document;
  t.context.setTimeout=callback=>{callback();return 1;};
  t.run(attributeTableScript);
  t.context.canonicalColumns=columns;
  t.run(`meshSnapshot={jobId:'mesh-reload-456',path:'runs/mesh/cells.json',digest:'mesh-digest',crs:'EPSG:32649',cells:[],display:{cell_count:2},qa:{mesh_quality:{}}};
    api=async(path,body)=>{calls.push({path,body});if(path==='/api/batch/preview'&&!Object.hasOwn(body,'visible_columns'))return{batch:{status:'pass',cell_count:100,draft:{columns:canonicalColumns}},columns:canonicalColumns,rows:[{row_kind:'cell',cell_id:'cell-1',values:{'stable-1':{state:'computed',value:12.5},'manual-stable':'checked'}}],total_cells:100,total_rows:100,offset:0};if(path==='/api/batch/preview')return{batch:{status:'pass',cell_count:100},columns:canonicalColumns,rows:[{row_kind:'cell',cell_id:body.cell_id||'cell-2',values:{'stable-1':{state:'computed',value:9},'manual-stable':'second'}}],total_cells:100,total_rows:100,offset:body.offset||0};throw Error('unexpected request '+path)};`);
  t.run("restorePendingBatchForMesh('mesh-reload-456')");
  for(let i=0;i<8;i++)await Promise.resolve();
  const all=[];const visit=node=>{for(const child of node.children||[]){all.push(child);visit(child);}};visit(t.get('batch-table'));
  assert.equal(all.filter(node=>node.type==='checkbox').length,25,'the real picker retains every server-canonical column after reload');
  assert.ok(all.some(node=>node.textContent==='canonical-24'));
  assert.ok(all.some(node=>node.textContent==='manual notes'));
  assert.ok(all.some(node=>node.textContent==='12.5'),'computed values render using stable IDs rather than stale feature names');
  const next=all.find(node=>node.textContent==='Sau');assert.ok(next);next.handlers.click();
  for(let i=0;i<8;i++)await Promise.resolve();
  const pageTwo=t.calls.find(call=>call.path==='/api/batch/preview'&&Object.hasOwn(call.body,'visible_columns'));
  assert.equal(pageTwo.body.offset,50);
  assert.deepEqual(JSON.parse(JSON.stringify(pageTwo.body.visible_columns)),columns.slice(0,10).map(column=>column.id));
  all.length=0;visit(t.get('batch-table'));
  const manualPicker=all.find(node=>node.dataset.column==='manual-stable');assert.ok(manualPicker);manualPicker.checked=true;manualPicker.handlers.change();
  for(let i=0;i<8;i++)await Promise.resolve();
  const withManual=t.calls.filter(call=>call.path==='/api/batch/preview'&&Object.hasOwn(call.body,'visible_columns')).at(-1);
  assert.ok(withManual,JSON.stringify(t.calls));assert.ok(withManual.body.visible_columns.includes('manual-stable'));
  all.length=0;visit(t.get('batch-table'));
  assert.ok(all.some(node=>node.textContent==='second'),'the newly visible manual column renders its value by stable ID');
  const search=all.find(node=>node.type==='search');assert.ok(search);search.value='cell-1';search.handlers.input();
  for(let i=0;i<8;i++)await Promise.resolve();
  const searchRequest=t.calls.filter(call=>call.path==='/api/batch/preview'&&Object.hasOwn(call.body,'visible_columns')).at(-1);
  assert.equal(searchRequest.body.cell_id,'cell-1');
  assert.equal(searchRequest.body.cell_id_mode,'substring');
  assert.deepEqual(JSON.parse(JSON.stringify(searchRequest.body.visible_columns)),[...columns.slice(0,10).map(column=>column.id),'manual-stable']);
});

test('terminal polling preserves the extraction draft and plan hash in completed recovery metadata',async()=>{
  const t=setup(),draft={mesh_job_id:'mesh-poll-123',geometry_digest:'digest-poll',columns:[{id:'renamed-id',type:'raster',source_name:'source',name:'renamed'}],rows:[],excluded_cell_ids:[],manual_rows:[]};
  const feature={name:'source',path:'D:/inputs/source.tif',band:1,semantics:'intensive',value_units:'m'};
  t.context.draftToRecover=draft;
  t.run(`batchJobId='batch-poll-123';batchMeshId='mesh-poll-123';activeBatchFeatures=[${JSON.stringify(feature)}];activeBatchDraft=draftToRecover;activeBatchPlanHash='saved-plan-hash';
    api=async(path,body)=>{calls.push({path,body});if(path==='/api/job?id=batch-poll-123')return{status:'pass',stage:'complete',feature_index:0,feature_total:1};if(path==='/api/batch/preview')return{batch:{status:'pass',cell_count:0,draft:{columns:draftToRecover.columns}},columns:draftToRecover.columns,rows:[],total_cells:0,total_rows:0,offset:0};throw Error('unexpected request '+path)};
    MeshCherryAttributeTable={mount:()=>({ready:Promise.resolve(true),destroy(){}})};`);
  await t.run("pollBatch('batch-poll-123')");
  const stored=JSON.parse(t.storage.get('mesh-cherry-batch-recovery'));
  assert.equal(stored.state,'completed');
  assert.equal(stored.plan_hash,'saved-plan-hash');
  assert.deepEqual(JSON.parse(JSON.stringify(stored.draft)),draft);
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

test('draft preview made stale by an input revision cannot start extraction',async()=>{
  const t=setup();let finish;
  const pending=new Promise(resolve=>{finish=resolve;});t.context.deferredDraft=pending;t.context.finishDraft=finish;
  t.run(`meshSnapshot={jobId:'saved-mesh-stale',path:'runs/saved-mesh/cells.json',digest:'saved-digest',crs:'EPSG:32649',cells:[{cell_id:'a'}],display:{cell_count:1},qa:{mesh_quality:{}}};
    $('extract-raster').value='D:/inputs/fresh.asc';$('extract-units').value='m';calls.length=0;
    api=async(path,body)=>{calls.push({path,body});if(path==='/api/extraction-draft/preview')return deferredDraft;throw Error('unexpected request '+path)};
    stage=value=>{currentStage=value;};draw=()=>{};showMeshQuality=()=>{};refreshCoach=()=>{};toast=()=>{};showResultTab=()=>{};`);
  const running=t.get('extract-run').onclick();
  for(let i=0;i<4;i++)await Promise.resolve();
  assert.ok(t.calls.some(call=>call.path==='/api/extraction-draft/preview'));
  t.run('revision++');
  t.context.finishDraft({draft:{mesh_job_id:'saved-mesh-stale',geometry_digest:'saved-digest',columns:[],rows:[],excluded_cell_ids:[],manual_rows:[]},plan_hash:'stale',columns:[],rows:[],total_rows:1,offset:0,limit:50});
  await running;
  assert.equal(t.calls.some(call=>call.path==='/api/batch/start'),false);
  assert.match(t.context.draftState.error,/đã thay đổi/);
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
