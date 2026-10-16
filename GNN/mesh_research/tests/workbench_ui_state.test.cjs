// Run with node --test. Executable UI logic with a deliberately small fake DOM;
// layout/real focus are separately checked in the browser, not proven here.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const script=fs.readFileSync(process.env.MESH_UI_SCRIPT||path.join(__dirname,'../src/gis_mesh_workbench/webui/app.js'),'utf8');
function setup(){
  const elements=new Map(),storage=new Map();
  class Element{
    constructor(){this.children=[];this.value='';this.checked=true;this.attributes={};this.dataset={};this.selectedOptions=[{textContent:'test'}];this.classes=new Set();this.classList={toggle:(name,on)=>{on?this.classes.add(name):this.classes.delete(name);},remove:(name)=>this.classes.delete(name),add:(name)=>this.classes.add(name)};}
    append(...items){this.children.push(...items);} before(){} replaceChildren(...items){this.children=items;}
    setAttribute(name,value){this.attributes[name]=String(value);if(name==='data-id')this.dataset.id=String(value);}
    getAttribute(name){return this.attributes[name]??null;} removeAttribute(name){delete this.attributes[name];}
    addEventListener(name,callback){this.handlers??={};this.handlers[name]=callback;} focus(){this.focused=true;} click(){this.clicked=true;}
    querySelectorAll(selector){const descendants=this.children.flatMap(c=>[c,...c.querySelectorAll(selector)]);return descendants.filter(c=>selector==='[data-id]'?c.dataset.id:selector==='input,select,button'?false:(c.attributes.class||'').split(' ').includes(selector.slice(1)));}
  }
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  get('crs').value='EPSG:32649';get('band').value='1';get('raster').value='a.tif';get('mode').value='structured_grid';
  const timers=[];
  const urls={created:[],revoked:[],createObjectURL(blob){this.created.push(blob);return 'blob:preview-'+this.created.length;},revokeObjectURL(url){this.revoked.push(url);}};
  const context=vm.createContext({document:{getElementById:get,createElement:()=>new Element(),createElementNS:()=>new Element(),querySelector:selector=>get(selector),querySelectorAll:()=>[],body:{dataset:{}}},location:{hash:'',pathname:'/',reload(){this.reloads=(this.reloads||0)+1;}},sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,String(value)),removeItem:key=>storage.delete(key)},URL:urls,Blob,atob,Uint8Array,URLSearchParams,history:{},coach:{update(){}},setTimeout:callback=>{timers.push(callback);return timers.length;},clearTimeout(){},console,fetch(){throw Error('mock API not configured');}});
  vm.runInContext(script.split("$('form').addEventListener")[0],context);
  // Exercise the production result renderer as part of the completed-job flow.
  vm.runInContext(script.slice(script.indexOf('function showMeshQuality('),script.indexOf("$('extract-run').onclick")),context);
  return {get,context,timers,urls,storage,run:code=>vm.runInContext(code,context)};
}
async function runProductionBootstrap(t){
  const start=script.indexOf("api('/api/bootstrap')"),end=script.indexOf('\ncoach.setup()',start);
  t.run('mode=()=>{};savedGmshFeedback=()=>{};');t.run('bootstrapPromise='+script.slice(start,end));
  await t.run('bootstrapPromise');for(let i=0;i<8;i++)await Promise.resolve();
}

test('comparison and research tables preserve exact integer node counts',()=>{
  const t=setup();t.run("comparisonTable($('counts'),['A','B'],[['Node',13515,13543]])");
  const row=t.get('counts').children[0].children[1].children[0];
  assert.equal(row.children[1].textContent,'13.515');assert.equal(row.children[2].textContent,'13.543');
});

test('comparison configuration uses accepted mesh identity and validates square size',()=>{
  const t=setup();t.run("meshSnapshot={jobId:'accepted-mesh',cells:[{cell_id:'a'}]};jobId='later-extract'");
  for(const [id,value] of Object.entries({'comparison-raster':'field.asc','comparison-band':'2','comparison-size':'50','comparison-semantics':'extensive','comparison-units':'m3'}))t.get(id).value=value;
  const config=JSON.parse(t.run('JSON.stringify(comparisonConfig())'));
  assert.deepEqual(config,{mesh_job_id:'accepted-mesh',raster_path:'field.asc',band:2,semantics:'extensive',value_units:'m3',cell_size_m:50});
  t.get('comparison-size').value='0';assert.throws(()=>t.run('comparisonConfig()'),/số dương/);
  t.get('comparison-size').value='50';t.get('comparison-band').value='1.5';assert.throws(()=>t.run('comparisonConfig()'),/nguyên dương/);
});
test('SF1 profile exposes fixed h and explicit scale without stale single-window parameters',()=>{
  const t=setup();for(const [id,value] of Object.entries({'gmsh-sizing':'sf1_relief','mode':'gmsh_generated','gmsh-domain':'rectangle','max-cells':'1000','west':'0','south':'0','east':'1000','north':'1000','h-min':'7','h-max':'9','sf1-scale':'1.2','dem-raster':'dem.tif','dem-band':'1','dem-value-units':'m','subdivisions':'8'}))t.get(id).value=value;
  t.run('mode()');assert.equal(t.get('h-min').value,'250');assert.equal(t.get('h-max').value,'1000');assert.equal(t.get('h-min').readOnly,true);assert.equal(t.get('sf1-controls').hidden,false);assert.equal(t.get('relief-controls').hidden,true);
  const source=JSON.parse(t.run('JSON.stringify(config().cell_source)'));assert.equal(source.sizing,'sf1_relief');assert.equal(source.sf1_scale,1.2);assert.equal(source.h_min_m,250);assert.equal(source.h_max_m,1000);assert.equal(source.relief_radius_m,undefined);assert.equal(source.dem_raster_path,'dem.tif');assert.equal(source.dem_value_units,'m');
  assert.equal(t.run('checkParameterFields()'),true);t.get('sf1-scale').value='4.1';assert.equal(t.run('checkParameterFields()'),false);assert.match(t.get('sf1-error').textContent,/0,25–4/);
});
test('SF1 request summary accepts fixed profile without legacy control_shape',()=>{
  const t=setup();t.context.q={mode:'sf1_relief',sf1_scale:1,requested_target_min_m:250,requested_target_max_m:1000,fixed_policy:{percentile_positions:[25,95],gamma:2}};
  assert.match(t.run('JSON.stringify(requestRows(q))'),/Scale SF-1/);assert.match(t.run('JSON.stringify(requestRows(q))'),/150 \/ 450/);
});
test('completed SF1 job renders native QA and keeps mesh exports and extraction available',async()=>{
  const t=setup();
  t.context.qa={mesh_quality:{cell_count:2,vertex_count:4,area_min_m2:450,area_max_m2:450,edge_min_m:30,edge_max_m:42.4264,overlap_area_m2:0,triangle_min_angle_deg:45,triangle_max_aspect:2,geometry_digest:'fixture',sizing_summary:{mode:'sf1_relief',analysis_crs:'EPSG:32649',max_cells:1000,sf1_scale:1,requested_target_min_m:250,requested_target_max_m:1000,fixed_policy:{percentile_positions:[25,95],gamma:2},size_field:{method:'gnn_sf1_two_window_relief',normalization:{relief150:[4,67],relief450:[10,178]},native_gradation:{ratio:1.5,reference_distance_m:250,allowed_log_gradient_per_m:0.0016218604324326574,measured_bound_per_m:0.0016,iterations:12,pass:true},control_gradation:{ratio:1.5,reference_distance_m:250,allowed_log_gradient_per_m:0.0016218604324326574,measured_bound_per_m:0.0015,iterations:20,pass:true},control_spacing_m:[120,120],target_size_field_min_m:250,target_size_field_max_m:740,scale:1}}}};
  t.run("jobId='sf1';busy=true;draw=()=>{};stage=()=>{};showResultTab=()=>{};api=async(path)=>path.includes('/api/job-log')?{text:'',next_cursor:0}:path.includes('/api/job?')?{status:'pass',stage:'complete',result:{output:'run/sf1'}}:path.includes('name=manifest')?{operation:'mesh_only',gmsh_generated:true,geometry_digest:'fixture'}:path.includes('name=cells')?{crs_wkt:'EPSG:32649',cells:[{cell_id:'a'},{cell_id:'b'}]}:qa");
  await t.run('pollJob()');
  const text=node=>[node.textContent||'',...node.children.map(text)].join(' ');
  const rendered=text(t.get('mesh-quality'));
  assert.match(rendered,/Scale SF-1.*1×/);
  assert.match(rendered,/P25 \/ P95 relief 150 m.*4 \/ 67/);assert.match(rendered,/P25 \/ P95 relief 450 m.*10 \/ 178/);
  assert.match(rendered,/Gradation.*DEM.*Đạt/);assert.match(rendered,/Gradation.*điều khiển.*Đạt/);assert.doesNotMatch(rendered,/Nửa cửa sổ/);
  assert.equal(t.get('exports').hidden,false);assert.equal(t.get('msh').hidden,false);assert.equal(t.get('extract-options').hidden,false);assert.equal(t.run('busy'),false);
  assert.match(t.run('jobHeadline.textContent'),/Đã tạo 2 ô/);assert.equal(t.timers.length,0);
});
test('legacy relief result keeps its original single-window thresholds',()=>{
  const t=setup();t.run("showMeshQuality({cell_count:1,vertex_count:3,area_min_m2:1,area_max_m2:1,edge_min_m:1,edge_max_m:2,overlap_area_m2:0,triangle_min_angle_deg:30,triangle_max_aspect:2,sizing_summary:{mode:'relief',size_field:{method:'local_relief',target_size_field_min_m:250,target_size_field_max_m:1000,p25:2,p95:20,control_spacing_m:[100,150],effective_half_window_m:[200,300]}}})");
  const text=node=>[node.textContent||'',...node.children.map(text)].join(' ');const rendered=text(t.get('mesh-quality'));
  assert.match(rendered,/P25 \/ P95 relief · ngưỡng từ DEM.*2 \/ 20/);assert.match(rendered,/Nửa cửa sổ relief thực.*200 × 300 m/);
});
test('comparison raster picker preserves mesh and result authority',()=>{
  const t=setup();t.run("meshSnapshot={jobId:'mesh',digest:'immutable'};jobId='extract';comparisonReport={old:true};acceptPickerPath('comparison-raster','new.asc')");
  assert.equal(t.run('meshSnapshot.digest'),'immutable');assert.equal(t.run('jobId'),'extract');assert.equal(t.get('comparison-raster').value,'new.asc');assert.equal(t.run('comparisonReport'),null);
});
test('comparison poll keeps main job identity and handles cancellation without loading report',async()=>{
  const t=setup();t.run("comparisonRequest=1;comparisonJobId='comparison';meshSnapshot={jobId:'mesh'};jobId='extract';busy=true;api=async()=>({status:'cancelled',stage:'cancelled',monitor:{elapsed_seconds:2}})");
  await t.run('pollComparison(1)');assert.equal(t.run('jobId'),'extract');assert.equal(t.run('meshSnapshot.jobId'),'mesh');assert.equal(t.run('busy'),false);assert.equal(t.run('comparisonJobId'),null);assert.match(t.get('comparison-status').textContent,/giữ nguyên/);
});
test('comparison report renders signed node reduction and quantity-specific explanation',()=>{
  const t=setup();t.run("renderComparison({semantics:'categorical',structured:{cell_count:10,node_count:10},unstructured:{cell_count:12,node_count:12},node_reduction_percent:-20,common_support_area_m2:100})");
  const panel=t.get('comparison-result');assert.equal(panel.hidden,false);assert.equal(t.get('comparison-download').hidden,false);
  const values=panel.children[1].children.map(box=>box.children[0].textContent);assert.deepEqual(values,['10','12','-20%']);
  const explanation=panel.children.find(c=>c.textContent?.startsWith('Tỷ lệ diện tích'));assert.match(explanation.textContent,/Tỷ lệ diện tích từng lớp/);assert.doesNotMatch(explanation.textContent,/RMSE/);
});
test('25000-category report bounds DOM rows while JSON download retains every class',async()=>{
  const t=setup(),fractions=Object.fromEntries(Array.from({length:25000},(_,i)=>['class-'+i,1/25000]));
  t.context.report={semantics:'categorical',method:'area-weighted fractions',metrics:{structured:{max_fraction_error:0,source_class_fractions:fractions,remapped_class_fractions:fractions},unstructured:{max_fraction_error:0,source_class_fractions:fractions,remapped_class_fractions:fractions}}};
  t.run('renderComparison(report)');const panel=t.get('comparison-result'),table=panel.children.find(c=>c.className==='comparison-table');
  assert.equal(table.children[1].children.filter(row=>row.children[0].textContent.startsWith('Tỷ lệ lớp')).length,100);
  const text=node=>[node.textContent||'',...node.children.map(text)].join(' '),count=node=>1+node.children.reduce((sum,child)=>sum+count(child),0);
  assert.match(text(panel),/100 \/ 25\.000 lớp/);assert.ok(count(panel)<600);assert.ok(text(panel).length<30000);assert.doesNotMatch(text(panel),/class-24999/);
  t.run('downloadComparison()');const saved=JSON.parse(await t.urls.created.at(-1).text());
  assert.equal(Object.keys(saved.metrics.structured.source_class_fractions).length,25000);assert.equal(saved.metrics.structured.source_class_fractions['class-24999'],1/25000);
});
test('categorical comparison separates spatial modal agreement from conservation and shows signed node delta',()=>{
  const t=setup();t.run("renderComparison({semantics:'categorical',graph_node_delta:-25,square_grid:{cell_size_m:50},metrics:{structured:{area_weighted_modal_agreement:.5,area_weighted_modal_disagreement:.5,max_fraction_error:0},unstructured:{area_weighted_modal_agreement:.875,area_weighted_modal_disagreement:.125,max_fraction_error:0}}})");
  const panel=t.get('comparison-result'),table=panel.children.find(c=>c.className==='comparison-table'),rows=table.children[1].children.map(row=>row.children.map(cell=>cell.textContent));
  assert.deepEqual(rows.slice(0,3),[['Khớp lớp phổ biến · trọng số diện tích','50%','87,5%'],['Sai khác lớp phổ biến · trọng số diện tích','50%','12,5%'],['Phần dư bảo toàn tỷ lệ lớp · lớn nhất','0','0']]);
  const text=node=>[node.textContent||'',...node.children.map(text)].join(' ');assert.match(text(panel),/Chênh lệch node · hiện tại − vuông -25/);assert.match(text(panel),/Ngân sách ô phụ thuộc cạnh lưới vuông bạn chọn/);
  t.run("renderComparison({graph_node_delta:12345})");assert.match(text(panel),/Chênh lệch node · hiện tại − vuông \+12\.345/);
});
test('comparison acknowledgement recovery adopts only comparison operation',async()=>{
  const t=setup();t.run("comparisonRequest=2;busy=true;api=async()=>({job_id:'comp',operation:'comparison'});pollComparison=async()=>{};jobId='main'");await t.run('recoverComparison(2)');assert.equal(t.run('comparisonJobId'),'comp');assert.equal(t.run('jobId'),'main');assert.equal(t.run('busy'),true);
  t.run("comparisonJobId=null;api=async()=>({job_id:'other',operation:'extract'})");await t.run('recoverComparison(2)');assert.equal(t.run('comparisonJobId'),null);assert.equal(t.run('busy'),true);assert.equal(t.timers.length,1);
});
test('comparison acknowledgement recovery keeps lock while connection is ambiguous',async()=>{
  const t=setup();t.run("comparisonRequest=1;busy=true;api=async()=>{throw Error('offline')}");await t.run('recoverComparison(1)');assert.equal(t.run('busy'),true);assert.equal(t.timers.length,1);
  t.run("api=async()=>({job_id:null})");await t.run("recoverComparison(1,'CRS không phù hợp')");assert.equal(t.run('busy'),false);assert.match(t.get('comparison-status').textContent,/CRS/);
});
test('keyed comparison recovery finds fast completed job without adopting unrelated active comparison',async()=>{
  const t=setup();t.context.calls=[];t.run("comparisonRequest=1;comparisonRequestKey='own-key';busy=true;jobId='main';api=async(path)=>{calls.push(path);return {job_id:'completed',operation:'comparison',status:'pass'}};pollComparison=async()=>{}");
  await t.run('recoverComparison(1)');assert.equal(t.run('comparisonJobId'),'completed');assert.equal(t.run('jobId'),'main');assert.match(t.context.calls[0],/comparison\/status\?request_id=own-key/);
  t.run("comparisonJobId=null;api=async(path)=>path.includes('/comparison/status')?{job_id:null}:{job_id:'different',operation:'comparison',request_id:'another-key'}");await t.run('recoverComparison(1)');assert.equal(t.run('comparisonJobId'),null);assert.equal(t.run('busy'),true);
});
test('unknown comparison job releases modal and preserves accepted mesh',async()=>{
  const t=setup();t.run("comparisonRequest=1;comparisonJobId='lost';busy=true;meshSnapshot={jobId:'mesh'};api=async()=>{const e=Error('Unknown job');e.status=400;throw e}");await t.run('pollComparison(1)');assert.equal(t.run('busy'),false);assert.equal(t.get('tools-close').disabled,false);assert.equal(t.run('meshSnapshot.jobId'),'mesh');assert.match(t.get('comparison-status').textContent,/Unknown job/);
});
test('terminal report failure retries bounded times then releases modal without report',async()=>{
  const t=setup();t.run("comparisonRequest=1;comparisonJobId='comp';busy=true;meshSnapshot={jobId:'mesh'};api=async(path)=>{if(path.includes('/api/job?'))return {status:'pass',monitor:{}};throw Error('offline')}");
  await t.run('pollComparison(1)');assert.equal(t.run('busy'),true);await t.run('pollComparison(1)');assert.equal(t.run('busy'),true);await t.run('pollComparison(1)');assert.equal(t.run('busy'),false);assert.equal(t.run('comparisonReport'),null);assert.equal(t.get('tools-close').disabled,false);assert.match(t.get('comparison-status').textContent,/chưa tải được báo cáo/);
});
test('permanent terminal artifact permission error unlocks immediately and never fabricates report',async()=>{
  const t=setup();t.run("comparisonRequest=1;comparisonJobId='comp';busy=true;api=async(path)=>{if(path.includes('/api/job?'))return {status:'pass',monitor:{}};const e=Error('Forbidden');e.status=403;throw e}");await t.run('pollComparison(1)');assert.equal(t.run('busy'),false);assert.equal(t.run('comparisonReport'),null);assert.match(t.get('comparison-status').textContent,/Forbidden/);
});
test('tools prefill retains extraction semantics alongside raster and units',()=>{
  const t=setup();t.get('tools-dialog').showModal=()=>{};t.get('extract-raster').value='volume.asc';t.get('extract-band').value='2';t.get('extract-units').value='m3';t.get('extract-semantics').value='extensive';t.run('openTools()');assert.equal(t.get('comparison-semantics').value,'extensive');assert.equal(t.get('comparison-band').value,'2');assert.equal(t.get('comparison-units').value,'m3');
});
test('intensive comparison presents RMSE while extensive presents conservation separately',()=>{
  const t=setup();t.run("renderComparison({semantics:'extensive',metrics:{structured:{source_total:10,remapped_total:10,conservation_residual:0},unstructured:{source_total:10,remapped_total:10,conservation_residual:0}}})");
  const table=t.get('comparison-result').children.find(c=>c.className==='comparison-table');const labels=table.children[1].children.map(row=>row.children[0].textContent);assert.deepEqual(labels,['Tổng nguồn · cùng miền hợp lệ','Tổng sau remap','Phần dư bảo toàn · sau − nguồn']);
  t.run("renderComparison({semantics:'intensive',metrics:{structured:{rmse:1,mae:.5,bias:0,units:'m'},unstructured:{rmse:.5,mae:.2,bias:0,units:'m'}}})");
  const intensive=t.get('comparison-result').children.find(c=>c.className==='comparison-table');assert.equal(intensive.children[1].children[0].children[0].textContent,'RMSE m');
});

test('monitor uses actual phase percentage and scoped ETA only',()=>{
  const t=setup();t.run("jobId='a';busy=true;renderMonitor({status:'running',stage:'meshing',monitor:{phase:'gmsh_2d',phase_percent:40,elapsed_seconds:12,eta:null}})");
  assert.equal(t.get('job-monitor').hidden,false);assert.match(t.get('monitor-percent').textContent,/40%.*trong log Gmsh/);
  assert.match(t.get('monitor-eta').textContent,/Chưa đủ dữ liệu/);assert.equal(t.get('cancel-run').disabled,false);
  t.run("renderMonitor({status:'running',stage:'meshing',monitor:{phase:'gmsh_2d',phase_percent:40,eta:{scope:'gmsh_2d',low_seconds:1,high_seconds:5}}})");
  assert.match(t.get('monitor-eta').textContent,/Chưa đủ dữ liệu/);
  t.run("renderMonitor({status:'running',stage:'extraction',monitor:{phase:'extraction',elapsed_seconds:30,eta:{scope:'extraction',low_seconds:10,high_seconds:30}}})");
  assert.match(t.get('monitor-eta').textContent,/10 giây.*30 giây.*trích thuộc tính/);
});
test('stopping remains locked and never loads partial result artifacts',async()=>{
  const t=setup();t.context.calls=[];
  t.run("jobId='a';busy=true;api=async(path)=>{calls.push(path);return path.startsWith('/api/job-log')?{text:'',next_cursor:0}:{status:'stopping',stage:'meshing',monitor:{phase:'gmsh_2d',cancel_requested:true}}}");
  await t.run('pollJob()');assert.equal(t.run('busy'),true);assert.equal(t.get('cancel-run').disabled,true);
  assert.equal(t.context.calls.some(p=>p.includes('/api/artifact')),false);assert.equal(t.timers.length,1);
});
test('terminal monitor distinguishes cancelled and failure from still stopping',()=>{
  const t=setup();
  t.run("renderMonitor({status:'cancelled',stage:'cancelled',monitor:{phase:'mesh_read',cancel_requested:true}})");
  assert.equal(t.get('cancel-run').textContent,'Đã dừng');
  assert.equal(t.get('monitor-phase').textContent,'Đã dừng');
  assert.equal(t.get('cancel-run').disabled,true);
  assert.equal(t.get('monitor-percent').textContent,'—');
  t.run("renderMonitor({status:'fail',stage:'failed',monitor:{phase:'mesh_read'}})");
  assert.equal(t.get('monitor-phase').textContent,'Thất bại');
  assert.equal(t.get('job-console-panel').hidden,true);
});
test('cancelled extract restores mesh exports but no stale CSV',async()=>{
  const t=setup();t.run("jobId='extract';busy=true;extractMode=true;meshSnapshot={jobId:'mesh',path:'mesh/output/cells.json',output:'mesh/output',hasMsh:true,cells:[{cell_id:'a',kind:'triangle',xy:[[0,0],[1,0],[0,1]]}],crs:'EPSG:32649',qa:{mesh_quality:{}}};showMeshQuality=()=>{};api=async(path)=>path.startsWith('/api/job-log')?{text:'',next_cursor:0}:{status:'cancelled',stage:'cancelled',result:{output:null,error:'stopped'}}");
  await t.run('pollJob()');assert.equal(t.run('busy'),false);assert.equal(t.run('jobId'),'mesh');assert.equal(t.run('attrs.size'),0);
  assert.equal(t.get('exports').hidden,false);assert.equal(t.get('csv').hidden,true);assert.equal(t.get('msh').hidden,false);
  assert.match(t.run('jobHeadline.textContent'),/Lưới.*giữ nguyên/);
});
test('live console uses bounded plain text and preserves manual scroll',async()=>{
  const t=setup();t.get('job-log').textContent='x'.repeat(65530);t.get('job-log').scrollTop=5;t.get('job-log').scrollHeight=500;t.get('job-log').clientHeight=100;t.get('log-follow').checked=false;
  t.run("jobId='a';api=async()=>({text:'<script>verbatim</script>\\n',next_cursor:3,dropped:2})");await t.run("syncJobLog('a')");
  assert.equal(t.get('job-log').textContent.length,65536);assert.match(t.get('job-log').textContent,/<script>verbatim<\/script>/);
  assert.equal(t.get('job-log').innerHTML,undefined);assert.equal(t.get('job-log').scrollTop,5);assert.match(t.get('log-notice').textContent,/gần nhất/);
});
test('stale log response cannot populate another run',async()=>{
  const t=setup();let resolve;t.context.delayed=new Promise(r=>resolve=r);t.run("jobId='old';api=()=>delayed");
  const pending=t.run("syncJobLog('old')");t.run('resetResult()');resolve({text:'old log',next_cursor:1});await pending;
  assert.equal(t.get('job-log').textContent,'');assert.equal(t.run('jobLogCursor'),0);
});
test('lost stop acknowledgement does not unlock inputs or claim stopped',async()=>{
  const t=setup();t.run("jobId='a';busy=true;api=async()=>{throw Error('offline')}");await t.run('requestStop()');
  assert.equal(t.run('busy'),true);assert.match(t.run('jobHeadline.textContent'),/Chưa xác nhận/);assert.equal(t.run('cancelPending'),false);
});

test('input preview uses CSP-permitted PNG blob and releases it on reset/replacement',async()=>{
  const t=setup();t.get('gmsh-domain').value='raster';
  t.context.report={image:'data:image/png;base64,iVBORw0KGgo=',crs:'EPSG:32649',band:1};
  t.run('api=async()=>report');await t.run('loadInputPreview()');
  assert.equal(t.get('input-preview-image').src,'blob:preview-1');
  assert.equal(t.urls.created[0].type,'image/png');
  await t.run('loadInputPreview()');assert.deepEqual(t.urls.revoked,['blob:preview-1']);
  t.run('clearInputPreview()');assert.deepEqual(t.urls.revoked,['blob:preview-1','blob:preview-2']);
});

test('invalid preview never becomes an image source',async()=>{
  const t=setup();t.get('gmsh-domain').value='raster';
  t.run("api=async()=>({image:'data:image/svg+xml,<svg/>',crs:'EPSG:32649'})");await t.run('loadInputPreview()');
  assert.equal(t.urls.created.length,0);assert.equal(t.get('input-preview-image').hidden,true);
  assert.match(t.get('input-preview-status').textContent,/Không dựng được/);
});

test('2001 cells remain visible and selectable without per-cell SVG allocation',()=>{
  const t=setup();t.context.large=Array.from({length:2001},(_,i)=>({cell_id:String(i),kind:'triangle',xy:[[i,0],[i+.8,0],[i,.8]]}));
  t.run("draw(large,[],{crs:'EPSG:32649',units:'m'})");
  assert.equal(t.run('geometry.length'),2001);assert.equal(t.get('empty-map').hidden,true);
  assert.equal(t.get('fit').disabled,false);assert.equal(t.get('cell-search').disabled,false);
  assert.ok(t.get('map').querySelectorAll('.mesh-batch').length>0);
  assert.equal(t.get('map').querySelectorAll('.cell-poly').length,0);
});
test('large mesh uses batched paths and keeps full geometry accessible',()=>{
  const t=setup();t.context.large=Array.from({length:100000},(_,i)=>({cell_id:String(i),kind:'triangle',xy:[[0,0],[1,0],[0,1]]}));
  t.run("jobId='large';draw(large,[],{crs:'EPSG:32649',units:'m'});setBusy(false);stage(4)");
  assert.equal(t.get('map').querySelectorAll('.cell-poly').length,0);
  assert.equal(t.run('geometry.length'),100000);assert.equal(t.run('currentStage'),4);
  assert.equal(t.get('empty-map').hidden,true);
  assert.equal(t.run('savedMap.cells.length'),100000);
});

function mapSetup(){
  const t=setup(),map=t.get('map');
  t.run("fitBox=[0,0,100,50];$('map').setAttribute('viewBox','0 0 100 50')");
  map.getScreenCTM=()=>({inverse:()=>({a:.5,b:0,c:0,d:.5,e:-10,f:-20})});
  map.setPointerCapture=id=>{map.capture=id;};map.hasPointerCapture=id=>map.capture===id;
  map.releasePointerCapture=()=>{map.capture=null;};
  const event=(x,y,type='pointermove')=>({clientX:x,clientY:y,pointerId:1,button:0,isPrimary:true,type,preventDefault(){}});
  t.context.down=event(100,100,'pointerdown');t.context.move=event(120,110);t.context.up=event(120,110,'pointerup');
  return {...t,map,event};
}

function largeMap(){
  const t=mapSetup();
  t.context.large=Array.from({length:13158},(_,i)=>{
    const x=500000+(i%100)*10,y=1000000+Math.floor(i/100)*10;
    return {cell_id:'tri-'+i,kind:'triangle',xy:[[x,y],[x+8,y],[x,y+8]]};
  });
  t.context.records=t.context.large.map((c,i)=>({cell_id:c.cell_id,value:i===0?null:i,area_m2:32,footprint_coverage:i===0?0:1,valid_coverage:i===0?0:1}));
  t.run("draw(large,records,{crs:'EPSG:32649',units:'m'})");return t;
}

test('13158 cells preserve original XY, attributes, coverage and empty gaps when picking',()=>{
  const t=largeMap(),before=JSON.stringify(t.context.large);
  assert.equal(t.run('pickBatchedCell(500001,1000001).cell_id'),'tri-0');
  assert.equal(t.run('pickBatchedCell(500008,1000000).cell_id'),'tri-0');
  assert.equal(t.run('pickBatchedCell(500007,1000007)'),null);
  assert.equal(t.run('pickBatchedCell(499999,1000000)'),null);
  const last=t.context.large.at(-1).xy[0];t.context.point=last;
  assert.equal(t.run('pickBatchedCell(point[0]+1,point[1]+1).cell_id'),'tri-13157');
  t.run('selectCell(geometry[13157])');
  assert.equal(t.run('selectedCellId'),'tri-13157');
  assert.match(t.get('cell-info').children.at(-3).textContent||t.get('cell-info').children[2].textContent,/13[.\s]?157|13157/);
  assert.equal(t.run('batchedMap.overlay.getAttribute("visibility")'),'visible');
  assert.equal(t.get('map').querySelectorAll('.mesh-batch').length,2);
  assert.equal(t.run('attrs.size'),13158);assert.equal(t.run('savedMap.records.length'),13158);
  assert.equal(JSON.stringify(t.context.large),before);
});

test('batched click maps screen position back into original projected XY',()=>{
  const t=largeMap();
  // CTM inverse from mapSetup: local=(screen*.5-[10,20]); local Y is flipped.
  t.context.click=t.event(22,2662);t.run('mapClick(click)');
  assert.equal(t.run('selectedCellId'),'tri-0');
  t.run('mapSuppressClick=true');t.context.click=t.event(42,2662);t.context.click.stopImmediatePropagation=()=>{};
  t.run('mapClick(click)');assert.equal(t.run('selectedCellId'),'tri-0');
});

test('large-map keyboard, visibility controls and result tabs retain full geometry',()=>{
  const t=largeMap();
  t.context.key={key:'End',preventDefault(){}};t.run('moveMapFocus(key)');
  assert.equal(t.run('selectedCellId'),null);assert.equal(t.run('batchedMap.focusIndex'),13157);
  t.context.key.key='Enter';t.run('moveMapFocus(key)');assert.equal(t.run('selectedCellId'),'tri-13157');
  t.get('show-edges').checked=false;t.get('show-nodes').checked=false;t.run('updateMapControls()');
  assert.ok(t.map.classes.has('no-edges'));assert.ok(t.map.classes.has('no-nodes'));
  t.run("showResultTab('mesh')");assert.equal(t.run('attrs.size'),0);
  t.run("showResultTab('extract')");assert.equal(t.run('attrs.size'),13158);
  assert.equal(t.run('geometry.length'),13158);assert.equal(t.get('fit').disabled,false);
});
test('drag follows screen scale and suppresses only the post-drag click',()=>{
  const t=mapSetup();t.run('beginMapDrag(down)');assert.equal(t.map.capture,undefined);
  t.run('moveMapDrag(move)');assert.equal(t.map.getAttribute('viewBox'),'-10 -5 100 50');assert.equal(t.map.capture,1);
  t.run('endMapDrag(up)');assert.equal(t.map.capture,null);assert.equal(t.run('mapSuppressClick'),true);
  let stopped=0;t.context.click={preventDefault(){},stopImmediatePropagation(){stopped++;}};
  t.run('mapClick(click);mapClick(click)');assert.equal(stopped,1);
});
test('small motion preserves click selection; cancel/reset clears drag',()=>{
  const t=mapSetup();t.context.move=t.event(102,101);
  t.run('beginMapDrag(down);moveMapDrag(move);endMapDrag(up)');assert.equal(t.run('mapSuppressClick'),false);
  assert.equal(t.map.getAttribute('viewBox'),'0 0 100 50');
  t.run('beginMapDrag(down);resetResult()');assert.equal(t.run('mapDrag'),null);
});
test('wheel zoom holds cursor anchor fixed and enforces zoom limits',()=>{
  const t=mapSetup();t.context.wheel={clientX:100,clientY:100,deltaY:-100,deltaMode:0,preventDefault(){}};
  t.run('wheelMap(wheel)');const v=t.map.getAttribute('viewBox').split(' ').map(Number),f=Math.exp(-.2);
  assert.ok(Math.abs(v[0]-40*(1-f))<1e-10);assert.ok(Math.abs(v[1]-30*(1-f))<1e-10);
  const before=t.map.getAttribute('viewBox');t.run('zoom(.00001);zoom(1000)');assert.equal(t.map.getAttribute('viewBox'),before);
});
test('reset removes old run/export/CRS/map identity',()=>{
  const t=setup();t.run("jobId='old';resultPath='old/output';fitBox=[1,2,3,4];resetResult()");
  assert.equal(t.run('jobId'),null);assert.equal(t.run('resultPath'),'');assert.equal(t.run('fitBox'),null);
  assert.equal(t.get('exports').hidden,true);assert.equal(t.get('map').getAttribute('viewBox'),null);
  assert.equal(t.run('jobHeadline.textContent'),'Chưa có lượt chạy.');
  assert.equal(t.get('button[data-stage="4"]').disabled,true);
});
test('result tabs retain full extraction data and geometry without remeshing',()=>{
  const t=setup();t.run("draw([{cell_id:'a',kind:'triangle',xy:[[0,0],[1,0],[0,1]]}],[{cell_id:'a',value:10,footprint_coverage:1,valid_coverage:1}],{crs:'EPSG:32649',units:'m'});showResultTab('mesh')");
  assert.equal(t.run('attrs.size'),0);assert.equal(t.run('savedMap.records[0].value'),10);
  t.run("showResultTab('extract')");assert.equal(t.run('attrs.get(\"a\").value'),10);
  assert.equal(t.run('geometry[0].cell_id'),'a');assert.equal(t.get('result-extract-tab').getAttribute('aria-selected'),'true');
});
test('stale preview response cannot replace a different source',async()=>{
  const t=setup();t.get('gmsh-domain').value='raster';let resolve;
  t.context.delayed=new Promise(r=>resolve=r);t.run('api=()=>delayed');const pending=t.run('loadInputPreview()');
  t.get('raster').value='other.tif';resolve({image:'old-image',crs:'EPSG:32649',band:1});await pending;
  assert.notEqual(t.get('input-preview-image').src,'old-image');
});
test('config edit invalidates old result and exports',()=>{
  const t=setup();t.run("jobId='old';preflightId='ok';invalidate()");
  assert.equal(t.run('jobId'),null);assert.equal(t.run('preflightId'),null);assert.equal(t.get('run').disabled,true);assert.equal(t.get('exports').hidden,true);
});
test('coverage keeps partial-footprint and NoData independent',()=>{
  const t=setup();t.run('a=coverageState({value:12,footprint_coverage:.5,valid_coverage:.5});b=coverageState({value:12,footprint_coverage:.5,valid_coverage:.25})');
  assert.equal(t.run('a.partial'),true);assert.equal(t.run('a.nodata'),false);assert.equal(t.run('b.partial&&b.nodata'),true);
  assert.equal(t.run('coverageState({value:null,footprint_coverage:0,valid_coverage:0}).outside'),true);
});
test('source response cannot overwrite changed analysis CRS',async()=>{
  const t=setup();let resolve;t.context.delayed=new Promise(r=>resolve=r);
  t.run('api=()=>delayed');const pending=t.run('inspectSource()');t.get('crs').value='EPSG:32648';resolve({crs:'EPSG:32649'});
  assert.equal(await pending,null);assert.equal(t.run('source'),null);
});
test('stale source error cannot overwrite current metadata',async()=>{
  const t=setup();let reject;t.context.delayed=new Promise((_,r)=>reject=r);t.run('api=()=>delayed');
  const pending=t.run('inspectSource()');t.run('sourceRequest++');t.get('source-info').textContent='new source';reject(Error('old error'));
  assert.equal(await pending,null);assert.equal(t.get('source-info').textContent,'new source');
});
test('unknown poll failure retains busy lock and schedules retry',async()=>{
  const t=setup();t.run("jobId='job-1';busy=true;api=async()=>{throw Error('offline')}");await t.run('pollJob()');
  assert.equal(t.run('busy'),true);assert.equal(t.timers.length,1);assert.match(t.run('jobHeadline.textContent'),/đang thử lại/);
});
test('late job response cannot populate a reset workspace',async()=>{
  const t=setup();let resolve;t.context.delayed=new Promise(r=>resolve=r);t.run("jobId='old';api=()=>delayed");
  const pending=t.run('pollJob()');t.run('resetResult()');resolve({status:'running',stage:'meshing'});await pending;
  assert.equal(t.run('jobHeadline.textContent'),'Chưa có lượt chạy.');assert.equal(t.timers.length,0);
});
test('result stage is gated until mesh geometry exists',()=>{
  const t=setup();t.run('stage(4)');assert.equal(t.run('currentStage'),1);
  t.run("jobId='job';stage(4)");assert.equal(t.run('currentStage'),1);
});
test('mesh result is accessible before optional extraction',()=>{
  const t=setup();t.run("jobId='mesh';draw([{cell_id:'a',kind:'triangle',xy:[[0,0],[1,0],[0,1]]}]);stage(4)");
  assert.equal(t.run('currentStage'),4);assert.equal(t.run('attrs.size'),0);
  assert.equal(t.get('.coverage-legend').hidden,true);
});
test('failed extraction restores accepted mesh without attributes',async()=>{
  const t=setup();t.run("jobId='extract';busy=true;extractMode=true;meshSnapshot={jobId:'mesh',path:'mesh/output/cells.json',output:'mesh/output',cells:[{cell_id:'a',kind:'triangle',xy:[[0,0],[1,0],[0,1]]}],crs:'EPSG:32649',qa:{mesh_quality:{}}};showMeshQuality=()=>{};api=async()=>({status:'fail',stage:'extraction',result:{error:'invalid raster'}})");
  await t.run('pollJob()');assert.equal(t.run('geometry.length'),1);assert.equal(t.run('attrs.size'),0);
  assert.equal(t.get('exports').hidden,false);assert.equal(t.run('busy'),false);
});
test('reload restores accepted mesh before a recovered extraction is cancelled',async()=>{
  const t=setup(),meshCells=[{cell_id:'accepted-cell',kind:'triangle',xy:[[0,0],[1,0],[0,1]]}];
  t.storage.set('mesh-cherry-accepted-mesh','accepted-mesh-job-1234');
  t.run('draw=(cells,records=[],contract={})=>{geometry=cells;savedMap={cells,records,contract};};showMeshQuality=()=>{};showResultTab=()=>{}');
  t.context.mockApi=async path=>{
    if(path.includes('/api/job?id=accepted-mesh-job-1234'))return {status:'pass',result:{output:'mesh/output'}};
    if(path.includes('name=manifest'))return {status:'pass',operation:'mesh_only',geometry_digest:'accepted-hash',gmsh_generated:true};
    if(path.includes('name=cells'))return {cells:meshCells,display:{cell_count:1},crs_wkt:'EPSG:32649'};
    if(path.includes('name=qa'))return {mesh_quality:{cell_count:1}};
    if(path.includes('/api/job-log'))return {text:'',next_cursor:0};
    if(path.includes('/api/job?id=extract-job'))return {status:'cancelled',stage:'cancelled',result:{output:null,error:'stopped'}};
    throw Error('unexpected '+path);
  };
  t.run('api=mockApi;busy=true');assert.equal(await t.run('restoreSessionMesh()'),true);
  assert.equal(t.run('currentStage'),4);t.run('stage(1)');assert.equal(t.run('currentStage'),4);assert.equal(t.run('busy'),true);
  t.run("extractMode=true;jobId='extract-job';busy=true");await t.run('pollJob()');
  assert.equal(t.run('meshSnapshot.jobId'),'accepted-mesh-job-1234');assert.equal(t.run('jobId'),'accepted-mesh-job-1234');
  assert.equal(t.run('meshSnapshot.cells[0].cell_id'),'accepted-cell');assert.equal(t.run('extractMode'),false);
  assert.equal(t.get('msh').hidden,false);assert.equal(t.get('csv').hidden,true);assert.equal(t.run('currentStage'),4);
});
test('reload keeps comparison baseline mesh identity after extraction passes',async()=>{
  const t=setup(),meshCells=[{cell_id:'accepted-cell',kind:'triangle',xy:[[0,0],[1,0],[0,1]]}];
  t.storage.set('mesh-cherry-accepted-mesh','accepted-mesh-job-1234');
  t.run('draw=(cells,records=[],contract={})=>{if(records.length)attrs=new Map(records.map(r=>[r.cell_id,r]));geometry=cells;savedMap={cells,records,contract};};showMeshQuality=()=>{};showResultTab=()=>{};coverageState=()=>({full:true})');
  t.context.mockApi=async path=>{
    if(path.includes('/api/job?id=accepted-mesh-job-1234'))return {status:'pass',result:{output:'mesh/output'}};
    if(path.includes('/api/job?id=extract-job'))return {status:'pass',stage:'complete',result:{output:'extract/output'}};
    if(path.includes('/api/job-log'))return {text:'',next_cursor:0};
    if(path.includes('name=manifest'))return path.includes('accepted-mesh-job-1234')?{status:'pass',operation:'mesh_only',geometry_digest:'accepted-hash',gmsh_generated:true}:{status:'pass',operation:'extract',runtime_seconds:1,engine_evidence:{pixels_read:10,windows_read:1,candidate_pairs:1,memory_metric:{value:1024}}};
    if(path.includes('name=cells'))return path.includes('extract-job')?{cells:meshCells,display:{cell_count:1},crs_wkt:'EPSG:32649'}:{cells:meshCells,display:{cell_count:1},crs_wkt:'EPSG:32649'};
    if(path.includes('name=qa'))return {coverage:{valid_coverage:{mean:1},footprint_coverage:{mean:1}}};
    if(path.includes('name=attributes'))return {cells:[{cell_id:'accepted-cell',value:2}],provenance:{source_path:'source.asc',band:1,analysis_crs_wkt:'EPSG:32649',value_units:'m'},summary:{full_count:1},qa:{}};
    if(path==='/api/source')return {display_footprint:null};
    throw Error('unexpected '+path);
  };
  t.run('api=mockApi');await t.run('restoreSessionMesh()');t.run("extractMode=true;jobId='extract-job';busy=true");for(const [id,value] of Object.entries({'comparison-raster':'field.asc','comparison-band':'1','comparison-size':'50','comparison-semantics':'intensive','comparison-units':'m'}))t.get(id).value=value;await t.run('pollJob()');
  assert.equal(t.get('job-status').textContent.includes('Hoàn thành'),true,t.get('job-status').textContent);assert.equal(t.run('meshSnapshot.jobId'),'accepted-mesh-job-1234');assert.equal(t.run('comparisonConfig().mesh_job_id'),'accepted-mesh-job-1234');
  assert.equal(t.run('jobId'),'extract-job');assert.equal(t.run('attrs.get("accepted-cell").value'),2);
});
for(const statusCode of [404,503])test(`bootstrap keeps recovered extract controllable when accepted mesh restoration returns ${statusCode}`,async()=>{
  const t=setup(),pending={preflightId:'preflight-on-disk',requestId:'stable-request-id-1234',operation:'extract'};
  t.storage.set('mesh-cherry-run-request',JSON.stringify(pending));t.storage.set('mesh-cherry-accepted-mesh','accepted-mesh-job-1234');
  const api=async path=>{
    if(path==='/api/bootstrap')return {roots:[],gmsh_path:'',output_root:'output',tutorial_seen:true};
    if(path.includes('accepted-mesh-job-1234'))throw Object.assign(Error('accepted mesh unavailable'),{status:statusCode});
    if(path.startsWith('/api/run/status?'))return {job_id:'active-extract-job',operation:'extract',request_id:pending.requestId,status:'running'};
    if(path==='/api/job?id=active-extract-job')return {status:'running',stage:'extraction',operation:'extract',monitor:{elapsed_seconds:2,phase:'extraction'}};
    if(path.startsWith('/api/job-log'))return {text:'',next_cursor:0};
    throw Error('unexpected '+path);
  };
  t.context.mockApi=api;t.run('api=mockApi');await runProductionBootstrap(t);
  assert.equal(t.context.location.reloads||0,0);assert.equal(t.run('jobId'),'active-extract-job');assert.equal(t.run('busy'),true);
  assert.equal(t.get('cancel-run').disabled,false);assert.match(t.get('toast').textContent,/vẫn được theo dõi và có thể dừng/);
  assert.equal(t.storage.has('mesh-cherry-accepted-mesh'),statusCode!==404);
});
test('native extraction selection preserves mesh and clears old output authority',()=>{
  const t=setup();t.run("meshSnapshot={jobId:'mesh',path:'mesh/output/cells.json',output:'mesh/output',hasMsh:true,cells:[{cell_id:'a',kind:'triangle',xy:[[0,0],[1,0],[0,1]]}],crs:'EPSG:32649',qa:{mesh_quality:{}}};showMeshQuality=()=>{};jobId='extract-old';resultPath='extract/old';acceptPickerPath('extract-raster','new.tif')");
  assert.equal(t.run('meshSnapshot.cells.length'),1);assert.equal(t.run('jobId'),'mesh');assert.equal(t.run('resultPath'),'mesh/output');
  assert.equal(t.get('extract-raster').value,'new.tif');assert.equal(t.get('csv').hidden,true);assert.equal(t.get('msh').hidden,false);
});
test('arrows focus only; Enter/Space select; centroid is decorative',()=>{
  const t=setup();t.run("draw([{cell_id:'a',kind:'triangle',xy:[[0,0],[1,0],[0,1]]},{cell_id:'b',kind:'triangle',xy:[[1,0],[1,1],[0,1]]}])");
  const polygons=t.get('map').querySelectorAll('.cell-poly');t.context.event={key:'ArrowRight',target:polygons[0],preventDefault(){}};t.run('moveMapFocus(event)');
  assert.equal(polygons[1].focused,true);assert.equal(polygons[1].getAttribute('tabindex'),'0');assert.equal(t.run('selectedCellId'),null);
  polygons[1].handlers.keydown({key:'Enter',preventDefault(){}});assert.equal(t.run('selectedCellId'),'b');
  polygons[0].handlers.keydown({key:' ',preventDefault(){}});assert.equal(t.run('selectedCellId'),'a');
  const centroid=t.get('map').querySelectorAll('.cell-node')[0];assert.equal(centroid.getAttribute('aria-hidden'),'true');assert.equal(centroid.getAttribute('aria-pressed'),null);
});
test('lost run response recovers running job and polls it',async()=>{
  const t=setup();t.run("preflightId='ok';api=async(path)=>{if(path==='/api/run')throw Error('lost');if(path.startsWith('/api/run/status?'))return {job_id:'recovered',operation:'extract',request_id:runRequest.requestId,status:'running'};throw Error('bad')};pollJob=async(id)=>{polled=id}");
  await t.run('startRun()');assert.equal(t.run('jobId'),'recovered');assert.equal(t.run('polled'),'recovered');assert.equal(t.run('busy'),true);
});
test('lost run with confirmed idle unlocks',async()=>{
  const t=setup();t.run("preflightId='ok';api=async(path)=>{if(path.startsWith('/api/run/status?'))return {job_id:null};if(path==='/api/run'){const error=Error('preflight expired');error.status=410;throw error;}throw Error('bad')}");await t.run('startRun()');assert.equal(t.run('busy'),false);assert.equal(t.run('jobId'),null);
});
test('lost run with unavailable recovery stays locked',async()=>{
  const t=setup();t.run("preflightId='ok';api=async()=>{throw Error('offline')}");await t.run('startRun()');assert.equal(t.run('busy'),true);assert.equal(t.timers.length,1);
});

test('uniform hides relief fields and emits no stale DEM/radius/minimum',()=>{
  const t=setup();for(const [id,v] of Object.entries({mode:'gmsh_generated','gmsh-domain':'shapefile','gmsh-sizing':'uniform','boundary-path':'roi.shp','gmsh-exe':'gmsh.exe','max-cells':'1000','h-max':'250','h-min':'bad','relief-radius':'bad',subdivisions:'8'}))t.get(id).value=v;
  t.run('mode();cfg=config()');assert.equal(t.get('h-min-control').hidden,true);assert.equal(t.get('relief-controls').hidden,true);assert.equal(t.get('dem-options').hidden,true);
  assert.equal(t.run('cfg.cell_source.h_min_m'),250);assert.equal(t.run('cfg.cell_source.h_max_m'),250);
  assert.equal(t.run("'relief_radius_m' in cfg.cell_source"),false);assert.equal(t.run("'extent' in cfg.cell_source"),false);
  assert.equal(t.get('subdivisions').hidden,true);assert.match(t.get('subdivisions-help').textContent,/cố định 8/);
});

test('SHP inspection reports source/transformed counts and long-runtime notice',async()=>{
  const t=setup();t.get('boundary-path').value='dense.shp';
  t.run("refreshRecent=async()=>{};api=async()=>({source_crs:'EPSG:32649',source_vertices:2206,transformed_vertices:2206,area_m2:10,components:1,holes:1,transform_subdivisions:1,bounds:[0,0,10,10],warnings:['Ranh giới có 2.206 điểm. Gmsh có thể chạy lâu.']})");
  await t.run('inspectBoundary()');
  const allText=node=>[node.textContent||'',...node.children.map(allText)].join(' ');
  const text=allText(t.get('boundary-info'));
  assert.match(text,/Số điểm biên nguồn/);assert.match(text,/Số điểm sau đổi CRS/);
  assert.match(t.get('boundary-info').children[1].textContent,/chạy lâu/);
});

test('generated mesh capacity follows bootstrap; structured retains 1000',()=>{
  const t=setup();t.get('max-cells').value='1000';t.get('max-cells').max='1000';t.get('mode').value='gmsh_generated';
  t.run('bootstrap={mesh_limits:{max_cells:100000}};mode()');assert.equal(t.get('max-cells').max,'100000');assert.equal(t.get('max-cells').value,'100000');
  t.get('mode').value='structured_grid';t.run('mode()');assert.equal(t.get('max-cells').max,'1000');assert.equal(t.get('max-cells').value,'1000');
});

test('large mesh bounds avoid spreading hundreds of thousands of arguments',()=>{
  const t=setup();t.run('many=Array.from({length:100000},(_,i)=>({xy:[[i,0],[i+1,0],[i,1]]}));bounds=meshBounds(many,null)');
  assert.equal(t.run('bounds.join()'),'0,0,100000,1');
});

test('relief exposes fields and reports adjacent range and missing DEM errors',()=>{
  const t=setup();for(const [id,v] of Object.entries({mode:'gmsh_generated','gmsh-sizing':'relief','h-min':'500','h-max':'100','relief-radius':'200','dem-band':'1'}))t.get(id).value=v;
  t.run('mode()');assert.equal(t.get('h-min-control').hidden,false);assert.equal(t.get('dem-options').hidden,false);
  assert.equal(t.run('checkParameterFields()'),false);assert.match(t.get('h-min-error').textContent,/h_max/);assert.match(t.get('dem-error').textContent,/DEM/);
});

test('resolution inspection changes guidance only; explicit action changes h',async()=>{
  const t=setup();for(const [id,v] of Object.entries({mode:'gmsh_generated','gmsh-sizing':'uniform','gmsh-domain':'shapefile','h-max':'250','planned-raster':'ref.tif','planned-band':'1'}))t.get(id).value=v;
  t.run("api=async()=>({path:'C:\\\\normalized\\\\ref.tif',band:1,resolution:{path:'ref.tif',band:1,source_crs:'EPSG:32649',analysis_crs:'EPSG:32649',native_units:'metre',native_pixel_size:[10,30],representative_pixel_size_m:[10,30],suggestions:{uniform_h_m:30}}})");
  await t.run('inspectPlanned()');assert.equal(t.get('h-max').value,'250');assert.equal(t.get('dem-raster').value,'');assert.equal(t.get('extract-raster').value,'');
  t.run('applyResolution()');assert.equal(Number(t.get('h-max').value),30);
});

test('stale planned raster response is discarded and finer h only warns',async()=>{
  const t=setup();t.get('planned-raster').value='ref.tif';t.get('planned-band').value='1';let resolve;t.context.delayed=new Promise(r=>resolve=r);t.run('api=()=>delayed');
  const pending=t.run('inspectPlanned()');t.get('crs').value='EPSG:32648';resolve({path:'ref.tif'});assert.equal(await pending,null);assert.equal(t.run('plannedSource'),null);
  for(const [id,v] of Object.entries({mode:'gmsh_generated','gmsh-domain':'raster','gmsh-sizing':'uniform','h-max':'1'}))t.get(id).value=v;
  t.run("source={resolution:{path:'ref.tif',band:1,native_pixel_size:[30,30],native_units:'metre',source_crs:'EPSG:32649',analysis_crs:'EPSG:32649',representative_pixel_size_m:[30,30]}};renderResolution()");
  assert.match(t.get('resolution-warning').textContent,/giữ nguyên độ phân giải gốc/);assert.equal(t.run('checkParameterFields()'),true);assert.equal(t.get('h-max').value,'1');
});
