'use strict';
const $=id=>document.getElementById(id);
// Keep the main result surface quiet; detailed diagnostics remain available.
const jobDetails=document.createElement('details'),jobDetailsTitle=document.createElement('summary'),jobHeadline=document.createElement('p');
jobDetailsTitle.textContent='Chi tiết kỹ thuật';jobHeadline.id='job-headline';jobHeadline.setAttribute('role','status');jobHeadline.textContent='Chưa có lượt chạy.';
$('job-status').before(jobHeadline,jobDetails);jobDetails.append(jobDetailsTitle,$('job-status'));
$('monitor-headline').append(jobHeadline);$('monitor-details').append(jobDetails);$('monitor-progress-slot').append($('progress'));
const fragment=new URLSearchParams(location.hash.slice(1));
const token=fragment.get('token')||sessionStorage.getItem('mesh-cherry-token')||'';
if(fragment.has('token')){sessionStorage.setItem('mesh-cherry-token',token);history.replaceState(null,'',location.pathname);}
let roots=[],source=null,preflightId=null,revision=0,busy=false,jobId=null,resultPath='',geometry=[],attrs=new Map(),fitBox=null,mapUnits='',mapCRS='';
const RUN_REQUEST_STORAGE_KEY='mesh-cherry-run-request';
const ACCEPTED_MESH_STORAGE_KEY='mesh-cherry-accepted-mesh';
let runRequest=null;
let pickerTarget='raster',pickerPath='';
let gmshVerified=null,meshSnapshot=null,extractMode=false;
let plannedSource=null,plannedRequest=0;
let sourceRequest=0,boundaryRequest=0,gmshRequest=0,toastTimer=null,selectedCellId=null;
let mapDrag=null,mapSuppressClick=false;
let batchedMap=null;
let previewRequest=0,previewKey='',previewURL=null,savedMap=null,resultTab='mesh';
let jobLogCursor=0,monitorJobId=null,cancelPending=false;
let comparisonReport=null,comparisonJobId=null,comparisonRequest=0,comparisonRequestKey='',comparisonReadFailures=0;
function releaseInputPreview(){
  const image=$('input-preview-image');image.onload=null;image.onerror=null;image.hidden=true;image.removeAttribute('src');
  if(previewURL){URL.revokeObjectURL(previewURL);previewURL=null;}
}
function previewBlob(data){
  const prefix='data:image/png;base64,';
  if(typeof data!=='string'||!data.startsWith(prefix)||data.length>4*1024*1024)throw Error('Ảnh PNG xem trước không hợp lệ');
  const bytes=Uint8Array.from(atob(data.slice(prefix.length)),c=>c.charCodeAt(0));
  if([137,80,78,71,13,10,26,10].some((value,i)=>bytes[i]!==value))throw Error('Ảnh PNG xem trước không hợp lệ');
  return new Blob([bytes],{type:'image/png'});
}
function previewIdentity(){return [$('gmsh-domain').value,$('raster').value,$('boundary-path').value,$('band').value,$('crs').value].join('|');}
function clearInputPreview(){
  previewRequest++;previewKey='';releaseInputPreview();
  $('input-preview-status').textContent='Chọn ROI hoặc raster để xem trước.';$('input-preview-caption').textContent='';
}
async function loadInputPreview(){
  const domain=$('gmsh-domain').value;if(!['raster','shapefile'].includes(domain)){clearInputPreview();return;}
  const key=previewIdentity(),request=++previewRequest;previewKey=key;
  const path=domain==='shapefile'?$('boundary-path').value.trim():$('raster').value.trim();
  if(!path){clearInputPreview();return;}
  releaseInputPreview();$('input-preview-status').textContent='Đang dựng hình nguồn…';
  try{
    const report=await api('/api/input-preview',{path,kind:domain==='shapefile'?'boundary':'raster',band:number('band'),analysis_crs:$('crs').value.trim()});
    if(request!==previewRequest||key!==previewIdentity())return;
    const image=$('input-preview-image'),url=URL.createObjectURL(previewBlob(report.image));previewURL=url;
    image.onload=()=>{if(previewURL!==url||request!==previewRequest)return;image.hidden=false;$('input-preview-status').textContent='';};
    image.onerror=()=>{if(previewURL!==url||request!==previewRequest)return;releaseInputPreview();$('input-preview-status').textContent='Không hiển thị được ảnh xem trước. Hãy thử lại.';};
    image.src=url;
    $('input-preview-title').textContent=domain==='shapefile'?'Ranh giới ROI':'Raster đầu vào';
    $('input-preview-caption').textContent=`${path.split(/[\\/]/).pop()} · ${report.crs}`+(domain==='raster'?` · Band ${report.band} · Màu P2–P98 chỉ để hiển thị${report.sampled?' · Xem trước giảm mẫu':''}${report.no_valid_samples?' · Không có pixel hợp lệ trong ảnh xem trước':''}`:' · Giữ nguyên biên và lỗ');
  }catch(error){if(request!==previewRequest||key!==previewIdentity())return;$('input-preview-status').textContent='Không dựng được xem trước: '+error.message;}
}
function showResultTab(value){
  if(busy||!['mesh','extract'].includes(value))return;
  resultTab=value;document.body.dataset.result=value;
  for(const tab of ['mesh','extract'])$('result-'+tab+'-tab').setAttribute('aria-selected',String(tab===value));
  $('result-map-title').textContent=value==='mesh'?'Lưới đã tạo':'Thuộc tính trên lưới';
  if(savedMap){const view=mapView();draw(savedMap.cells,value==='extract'?savedMap.records:[],{...savedMap.contract,footprint:value==='extract'?savedMap.contract.footprint:null,attributeSummary:value==='extract'?savedMap.contract.attributeSummary:null},false);if(view)$('map').setAttribute('viewBox',view.join(' '));}
}
function mapView(){return $('map').getAttribute('viewBox')?.split(/\s+/).map(Number);}
function stopMapDrag(){
  const id=mapDrag?.id;mapDrag=null;$('map').classList.remove('dragging');
  if(id!==undefined&&$('map').hasPointerCapture?.(id))$('map').releasePointerCapture(id);
}
function beginMapDrag(event){
  if(!fitBox||event.button!==0||event.isPrimary===false||mapDrag)return;
  const matrix=$('map').getScreenCTM();if(!matrix)return;
  mapSuppressClick=false;
  mapDrag={id:event.pointerId,x:event.clientX,y:event.clientY,view:mapView(),inverse:matrix.inverse(),moved:false};
}
function moveMapDrag(event){
  if(!mapDrag||event.pointerId!==mapDrag.id)return;
  const dx=event.clientX-mapDrag.x,dy=event.clientY-mapDrag.y;
  if(!mapDrag.moved&&Math.hypot(dx,dy)<4)return;
  if(!mapDrag.moved)$('map').setPointerCapture(event.pointerId);
  mapDrag.moved=true;$('map').classList.add('dragging');event.preventDefault();
  const m=mapDrag.inverse,[x,y,w,h]=mapDrag.view;
  $('map').setAttribute('viewBox',[x-m.a*dx-m.c*dy,y-m.b*dx-m.d*dy,w,h].join(' '));
}
function endMapDrag(event){
  if(!mapDrag||event.pointerId!==mapDrag.id)return;
  mapSuppressClick=event.type==='pointerup'&&mapDrag.moved;stopMapDrag();
}
function mapClick(event){
  if(mapSuppressClick){mapSuppressClick=false;event.preventDefault();event.stopImmediatePropagation();return;}
  if(batchedMap){
    const m=$('map').getScreenCTM()?.inverse();if(!m)return;
    const x=m.a*event.clientX+m.c*event.clientY+m.e+batchedMap.xmin;
    const y=batchedMap.ymax-(m.b*event.clientX+m.d*event.clientY+m.f);
    const cell=pickBatchedCell(x,y);if(cell)selectCell(cell);
  }
}
function zoom(factor,anchor){
  if(!fitBox||mapDrag)return;
  const [x,y,w,h]=mapView(),nextWidth=w*factor;
  if(nextWidth<fitBox[2]/20||nextWidth>fitBox[2]*20)return;
  const [ax,ay]=anchor||[x+w/2,y+h/2];
  $('map').setAttribute('viewBox',[ax+(x-ax)*factor,ay+(y-ay)*factor,nextWidth,h*factor].join(' '));
}
function wheelMap(event){
  if(!fitBox)return;event.preventDefault();if(mapDrag)return;
  const m=$('map').getScreenCTM()?.inverse();if(!m)return;
  const ax=m.a*event.clientX+m.c*event.clientY+m.e,ay=m.b*event.clientX+m.d*event.clientY+m.f;
  const pixels=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?$('map').clientHeight:1);
  zoom(Math.exp(Math.max(-200,Math.min(200,pixels))*.002),[ax,ay]);
}
let bootstrap=null,currentStage=1,tutorialActive=false,tutorialClicks=new Set(),visitedStages=new Set();
function stage(value,moveFocus=true,internalRestore=false){
  if(busy&&!internalRestore)return;
  if(value===4&&(!jobId||!displayCellCount()))return;
  currentStage=value;
  visitedStages.add(value);document.body.dataset.stage=String(value);
  for(const panel of document.querySelectorAll('[data-panel]'))panel.hidden=Number(panel.dataset.panel)!==value;
  for(const button of document.querySelectorAll('button[data-stage]')){button.setAttribute('aria-current',Number(button.dataset.stage)===value?'step':'false');button.classList.toggle('visited',visitedStages.has(Number(button.dataset.stage)));}
  const sourcePath=$('raster').value;$('check-summary').title=sourcePath;
  $('check-summary').textContent=`Nguồn: ${sourcePath.split(/[\\/]/).pop()||'Chưa chọn'}\nHệ tọa độ tính: ${$('crs').value}\nCách trích: ${$('semantics').selectedOptions[0].textContent}\nĐơn vị: ${$('units').value}\nLưới: ${$('mode').selectedOptions[0].textContent}\nKiểm tra cấu hình để biết số ô và lượng dữ liệu cần đọc.`;
  if(!tutorialActive)$('check-summary').textContent=extractMode?'Extract trên lưới đã lưu · '+(meshSnapshot?.display?.cell_count??meshSnapshot?.cells.length??0)+' ô':`${$('mode').value==='mesh_artifact'?'Mesh nhập: '+$('mesh').value.split(/[\\/]/).pop():'ROI: '+($('gmsh-domain').value==='shapefile'?$('boundary-path').value.split(/[\\/]/).pop():$('gmsh-domain').value==='raster'?sourcePath.split(/[\\/]/).pop():'Miền chữ nhật')}\nCRS tính toán: ${$('crs').value}\nLưới: ${$('mode').selectedOptions[0].textContent}`;
  tutorialHint();
  const heading=document.querySelector(`[data-panel="${value}"] h2`);
  if(heading&&moveFocus){heading.tabIndex=-1;heading.focus({preventScroll:true});}
}
function tutorialHint(){
  $('try-sample').hidden=!tutorialActive;
  $('tutorial-banner').hidden=!tutorialActive;
  const hints={1:'Bước 1: mẫu 10/30 đã được chọn. Hệ tọa độ EPSG:32649 có đơn vị mét.',2:'Bước 2: giữ ô vuông 1 × 1 m. Miền mẫu 2 × 1 m sẽ tạo đúng hai ô.',3:'Bước 3: kiểm tra cấu hình rồi bấm Tạo lưới.',4:'Bước 4: bấm Kiểm tra & extract, rồi chọn cả hai ô để xem 10 và 30. Coverage 100% nghĩa raster phủ đủ ô. Sau đó tải CSV/JSON và kết thúc hướng dẫn.'};
  $('tutorial-banner').textContent=hints[currentStage];
  refreshCoach();
}
function refreshCoach(){coach.update({active:tutorialActive,stage:currentStage,busy,validated:!!preflightId,extracted:!!attrs.size,inspected:tutorialClicks.size,ids:tutorialClicks});}
async function useSample(){
  if(!bootstrap||busy)return;
  resetResult();
  $('gmsh-domain').value='raster';$('dem-raster').value='';$('raster').value=bootstrap.sample_raster;$('mesh').value=bootstrap.sample_mesh;
  $('band').value=1;$('crs').value='EPSG:32649';$('semantics').value='intensive';$('units').value='synthetic-value';
  $('mode').value='structured_grid';$('width').value=1;$('height').value=1;$('policy').value='strict';
  coach.start();tutorialClicks.clear();$('tutorial-finish').disabled=true;$('tutorial-finish').hidden=false;tutorialActive=true;
  invalidate();mode();await inspectSource();
  ['west','south','east','north'].forEach((id,i)=>$(id).value=source.bounds[i]);
  stage(1);
}
async function finishTutorial(){
  await api('/api/tutorial',{action:'acknowledge'});
  tutorialActive=false;$('tutorial-finish').hidden=true;tutorialHint();
}
const stageNames={config:'Đọc cấu hình',preflight:'Kiểm tra',meshing:'Gmsh đang tạo lưới',cells:'Kiểm tra cell',extraction:'Đang trích thuộc tính raster',serialization:'Xuất artifacts',done:'Hoàn thành',failed:'Thất bại'};
Object.assign(stageNames,{domain:'Chuẩn bị miền',size_field:'Tạo trường kích thước',gmsh_1d:'Gmsh · chia lưới 1D',gmsh_2d:'Gmsh · chia lưới 2D',gmsh_3d:'Gmsh · chia lưới 3D',mesh_read:'Đọc tam giác từ Gmsh',quality:'Kiểm tra chất lượng lưới',cancelled:'Đã dừng',stopping:'Đang dừng'});
function duration(seconds){seconds=Math.max(0,Math.round(seconds||0));return seconds<60?seconds+' giây':Math.floor(seconds/60)+' phút '+seconds%60+' giây';}
function renderMonitor(job){
  const m=job.monitor||{},active=['running','stopping'].includes(job.status),gmshPercent=m.phase?.startsWith('gmsh_'),phase=stageNames[m.phase]||stageNames[job.stage]||job.stage;
  $('job-monitor').hidden=false;monitorJobId=jobId;
  $('monitor-phase').textContent=job.status==='stopping'?'Đang dừng…':job.status==='pass'?'Hoàn thành':job.status==='cancelled'?'Đã dừng':job.status==='fail'?'Thất bại':phase;
  $('monitor-elapsed').textContent=duration(m.elapsed_seconds);
  const known=Number.isFinite(m.phase_percent);
  $('monitor-percent').textContent=!active?'—':known?`${m.phase_percent.toFixed(0)}% · ${gmshPercent?'trong log Gmsh':'chỉ bước này'}`:gmshPercent||m.phase==='meshing'?'Gmsh chưa báo phần trăm':'Bước này chưa có phần trăm';
  $('phase-progress').hidden=!active||!known;if(known)$('phase-progress').value=m.phase_percent;
  $('progress').hidden=!active||known;
  $('cancel-run').disabled=!active||job.status==='stopping'||m.cancel_requested||cancelPending;
  $('cancel-run').textContent=job.status==='cancelled'?'Đã dừng':active&&(job.status==='stopping'||m.cancel_requested)?'Đang dừng…':'Dừng tác vụ';
  $('monitor-eta').textContent=m.eta&&active&&!gmshPercent?`${duration(m.eta.low_seconds)} – ${duration(m.eta.high_seconds)} · ${stageNames[m.eta.scope]||m.eta.scope}`:active?'Chưa đủ dữ liệu để ước tính':'—';
  $('job-log-download').hidden=active||!m.has_gmsh_log;
  $('job-console-panel').hidden=!m.has_gmsh_log&&!m.log_line_count;
}
async function syncJobLog(expectedJobId){
  try{
    const cursor=jobLogCursor,data=await api('/api/job-log?id='+encodeURIComponent(expectedJobId)+'&cursor='+cursor,undefined,4000);
    if(expectedJobId!==jobId||cursor!==jobLogCursor)return;
    const consoleBox=$('job-log'),atBottom=(consoleBox.scrollHeight||0)-(consoleBox.scrollTop||0)-(consoleBox.clientHeight||0)<30;
    const previous=consoleBox.textContent||'';consoleBox.textContent=(previous+data.text).slice(-65536);jobLogCursor=data.next_cursor;
    $('log-notice').textContent=data.dropped||data.truncated_lines||previous.length+(data.text||'').length>65536?'Giao diện giữ phần log gần nhất. Tệp log đầy đủ được giữ trong lượt chạy.':'';
    if($('log-follow').checked&&atBottom)consoleBox.scrollTop=consoleBox.scrollHeight;
  }catch(error){if(expectedJobId===jobId)$('log-notice').textContent='Chưa đọc được log mới; tác vụ vẫn được theo dõi.';}
}
async function requestStop(){
  if(!busy||!jobId||cancelPending)return;
  const expected=jobId;cancelPending=true;$('cancel-run').disabled=true;
  try{
    const result=await api('/api/job/stop',{job_id:expected},8000);if(expected!==jobId)return;
    jobHeadline.textContent=result.accepted?'Đang dừng… chờ tiến trình kết thúc và giữ lại log.':'Tác vụ đã kết thúc; đang tải trạng thái cuối.';
  }catch(error){if(expected===jobId)jobHeadline.textContent='Chưa xác nhận được yêu cầu dừng; đang đọc lại trạng thái. Bạn có thể thử Dừng lần nữa.';}
  finally{cancelPending=false;}
}
async function api(path,data,timeout=0){
  const controller=timeout&&typeof AbortController!=='undefined'?new AbortController():null;
  const timer=controller?setTimeout(()=>controller.abort(),timeout):null;
  try{const response=await fetch(path,{method:data===undefined?'GET':'POST',headers:{'X-Workbench-Token':token,...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data),...(controller?{signal:controller.signal}:{})});
    const value=await response.json();if(!response.ok){const error=new Error(value.error||`HTTP ${response.status}`);error.status=response.status;throw error;}return value;
  }finally{if(timer!==null)clearTimeout(timer);}
}
function toast(message){clearTimeout(toastTimer);$('toast').textContent=message;$('toast').hidden=false;toastTimer=setTimeout(()=>{$('toast').hidden=true;},7000);}
function resetResult(){
  jobLogCursor=0;monitorJobId=null;cancelPending=false;$('job-log').textContent='';$('log-notice').textContent='';$('job-monitor').hidden=true;$('job-log-download').hidden=true;$('cancel-run').disabled=true;
  savedMap=null;resultTab='mesh';document.body.dataset.result='mesh';
  jobId=null;resultPath='';selectedCellId=null;fitBox=null;mapUnits='';mapCRS='';
  $('exports').hidden=true;$('msh').hidden=true;$('mesh-json').hidden=true;
  $('progress').hidden=true;jobHeadline.textContent='Chưa có lượt chạy.';
  $('job-status').className='info muted';$('job-status').textContent='Chưa có lượt chạy.';
  draw([]);$('map').removeAttribute('viewBox');$('map-crs').textContent='X,Y · mét';
  document.querySelector('button[data-stage="4"]').disabled=true;
  $('coverage-summary').classList.remove('coverage-warning');$('cell-info').className='info muted';
}
function startAnother(){
  if(busy)return;
  clearInputPreview();
  sourceRequest++;boundaryRequest++;exitTutorial();tutorialClicks.clear();visitedStages.clear();
  source=null;plannedSource=null;plannedRequest++;$('planned-raster').value='';meshSnapshot=null;clearAcceptedMesh();extractMode=false;invalidate();resetResult();
  $('raster').value='';$('mesh').value='';$('boundary-path').value='';$('gmsh-domain').value='rectangle';
  $('source-info').className='info muted';$('boundary-info').className='info';
  $('source-info').textContent='Chọn GeoTIFF / ASC của bạn.';$('boundary-info').textContent='Chưa đọc ranh giới.';
  mode();helpers();stage(1);
}
function exitTutorial(){tutorialActive=false;$('tutorial-finish').hidden=true;tutorialHint();}
async function refreshRecent(){
  const {items}=await api('/api/recent');$('recent-list').replaceChildren();$('recent-files').hidden=!items.length;
  for(const item of items){const button=document.createElement('button');button.type='button';button.className='recent-file';button.title=item.name;
    const name=document.createElement('strong'),path=document.createElement('small');name.textContent=({raster:'GeoTIFF / ASC',mesh:'Mesh',boundary:'Ranh giới SHP'})[item.kind]+' · '+item.name;const date=new Date(item.opened);path.textContent='Mở gần nhất: '+(Number.isNaN(date.getTime())?'Không rõ thời gian':new Intl.DateTimeFormat('vi-VN',{dateStyle:'short',timeStyle:'short'}).format(date));button.append(name,path);
    button.onclick=async()=>{if(busy)return;try{const selected=await api('/api/recent',{id:item.id});exitTutorial();const target=selected.kind==='mesh'?'mesh':selected.kind==='boundary'?'boundary-path':'raster';acceptPickerPath(target,selected.path);if(selected.kind==='raster')await inspectSource();if(selected.kind==='mesh'){$('mode').value='mesh_artifact';mode();stage(2);}if(selected.kind==='boundary'){$('mode').value='gmsh_generated';$('gmsh-domain').value='shapefile';mode();stage(2);await inspectBoundary();}await refreshRecent();}catch(error){toast('Không mở được tệp gần đây: '+error.message);}};$('recent-list').append(button);
  }
}
$('recent-clear').onclick=async()=>{if(busy)return;try{await api('/api/recent',{action:'clear'});await refreshRecent();}catch(error){toast(error.message);}};
function invalidate(){clearComparison();meshSnapshot=null;clearAcceptedMesh();extractMode=false;$('extract-options').hidden=true;$('mesh-quality').hidden=true;revision++;preflightId=null;if(!busy)resetResult();$('run').disabled=true;$('validation').className='info muted';$('validation').textContent='Cấu hình đã thay đổi. Hãy kiểm tra lại trước khi chạy.';refreshCoach();}
function mode(){
  if(previewKey&&previewKey!==previewIdentity())clearInputPreview();
  const domain=$('gmsh-domain').value,shp=domain==='shapefile';
  if(shp)$('mode').value='gmsh_generated';
  const value=$('mode').value,sf1=value==='gmsh_generated'&&$('gmsh-sizing').value==='sf1_relief',relief=value==='gmsh_generated'&&['relief','sf1_relief'].includes($('gmsh-sizing').value);
  const previousCap=Number($('max-cells').max||1000),cap=value==='structured_grid'?1000:(bootstrap?.mesh_limits?.max_cells||100000);
  $('max-cells').max=String(cap);
  if(Number($('max-cells').value)===previousCap||Number($('max-cells').value)>cap)$('max-cells').value=String(cap);
  if(tutorialActive&&value!=='structured_grid')exitTutorial();
  $('raster-label').textContent='GeoTIFF / ASC · biên ROI';$('roi-raster-options').hidden=domain!=='raster';$('run').textContent='Tạo lưới →';
  for(const [id,kind] of [['grid-options','structured_grid'],['mesh-options','mesh_artifact'],['gmsh-options','gmsh_generated']])$(id).hidden=value!==kind;
  $('domain-options').hidden=value==='mesh_artifact'||shp;$('boundary-options').hidden=!shp;
  for(const id of ['west','south','east','north'])$(id).readOnly=domain==='raster';
  for(const button of document.querySelectorAll('[data-mode]')){button.setAttribute('aria-pressed',String(button.dataset.mode===value));button.disabled=busy||(shp&&button.dataset.mode!=='gmsh_generated');}
  for(const option of $('mode').querySelectorAll('option'))option.disabled=shp&&option.value!=='gmsh_generated';
  for(const id of ['dem-options','h-min-control','relief-controls'])$(id).hidden=!relief;
  $('sf1-controls').hidden=!sf1;$('relief-controls').hidden=!relief||sf1;
  $('h-min').readOnly=sf1;$('h-max').readOnly=sf1;if(sf1){$('h-min').value='250';$('h-max').value='1000';}
  $('h-min-help').textContent=sf1?'Hồ sơ SF-1 cố định 250 m trước scale và gradation.':'Nhập số dương, không lớn hơn h_max. Nhỏ hơn cho phép chia mịn hơn ở nơi relief cao.';
  $('h-max-label').textContent=relief?'h_max · kích thước mục tiêu lớn nhất (m)':'h · kích thước mục tiêu (m)';
  $('h-max-help').textContent=sf1?'Hồ sơ SF-1 cố định 1000 m. h_scaled = clip(scale × h_raw, 250, 1000), sau đó giới hạn gradation.':relief?'Nhập số dương ≥ h_min. Vùng ít relief, thiếu DEM hoặc relief không đổi dùng h_max.':'Nhập kích thước mục tiêu theo mét, ví dụ 250. Kích thước cạnh thực tế được thống kê sau khi tạo lưới.';
  const subdivisionsRelevant=extractMode||(domain==='raster'&&!sameCRS());
  $('subdivisions').hidden=!subdivisionsRelevant;$('subdivisions-label').hidden=!subdivisionsRelevant;
  $('subdivisions-help').textContent=shp?'SHP đổi CRS: cố định 8 đoạn/cạnh nguồn. Giá trị footprint GeoTIFF / ASC không điều khiển SHP.':subdivisionsRelevant?'1–64, mặc định 8: xấp xỉ cạnh footprint GeoTIFF / ASC qua CRS; không trực tiếp tăng mật độ mesh.':'Miền cùng CRS hoặc chữ nhật: không cần xấp xỉ cạnh qua CRS.';
  $('resolution-options').hidden=value==='mesh_artifact';$('planned-options').hidden=domain==='raster';
  renderFixedPolicy();parameterPreview();renderResolution();
}
function concise(value){return Number.isFinite(value)?Number(value.toPrecision(4)).toLocaleString('vi-VN',{maximumSignificantDigits:4}):'—';}
function fieldError(id,errorId,message){$(id).setAttribute('aria-invalid',String(!!message));$(errorId).textContent=message;$(errorId).hidden=!message;}
function checkParameterFields(){
  let valid=true;
  const check=(id,errorId,label,integer=false)=>{const v=Number($(id).value),bad=!String($(id).value).trim()||!Number.isFinite(v)||v<=0||(integer&&!Number.isInteger(v));fieldError(id,errorId,bad?label+' phải là số '+(integer?'nguyên ':'')+'dương.':'');valid=valid&&!bad;};
  for(const [id,error] of [['h-min','h-min-error'],['h-max','h-max-error'],['relief-radius','radius-error'],['dem-band','dem-band-error'],['dem-raster','dem-error'],['width','width-error'],['height','height-error']])fieldError(id,error,'');
  fieldError('sf1-scale','sf1-error','');
  if($('mode').value==='structured_grid'){check('width','width-error','Chiều rộng ô');check('height','height-error','Chiều cao ô');}
  if($('mode').value==='gmsh_generated'){
    check('h-max','h-max-error','Kích thước h');
    if(['relief','sf1_relief'].includes($('gmsh-sizing').value)){
      check('h-min','h-min-error','h_min');if($('gmsh-sizing').value==='relief')check('relief-radius','radius-error','R');check('dem-band','dem-band-error','Band DEM',true);
      if($('gmsh-sizing').value==='sf1_relief'){const scale=Number($('sf1-scale').value);if(!$('sf1-scale').value.trim()||!Number.isFinite(scale)||scale<.25||scale>4){fieldError('sf1-scale','sf1-error','Scale SF-1 phải nằm trong khoảng 0,25–4.');valid=false;}}
      if(Number($('h-min').value)>Number($('h-max').value)){fieldError('h-min','h-min-error','h_min không được lớn hơn h_max.');valid=false;}
      if(!$('dem-raster').value.trim()){fieldError('dem-raster','dem-error','Chọn DEM cao độ để tính local relief.');valid=false;}
    }
  }
  return valid;
}
function renderFixedPolicy(){
  const p=bootstrap?.meshing_parameters;if(!p)return;
  $('fixed-parameters').replaceChildren();
  detailRows($('fixed-parameters'),[['Lưới điều khiển',p.control_shape.join(' × ')+' · cố định'],['Chuẩn hóa',p.percentile_positions.map(x=>'P'+x).join(' / ')+' · vị trí cố định, ngưỡng tính từ relief'],['Gamma',p.gamma+' · cố định'],['Lấy mẫu DEM','Nearest'],['Thiếu / relief không đổi','h_max'],['Ngoài ROI / trong lỗ','Loại khỏi chuẩn hóa relief'],['Giới hạn gradation','Chưa có']]);
}
function parameterPreview(){
  if($('gmsh-sizing').value==='sf1_relief'){$('parameter-preview').textContent='C = max(C_150m, C_450m). h_raw = 1000 − 750 × C² (m).\nh_scaled = clip(scale × h_raw, 250, 1000); gradation ≤ 1,5 mỗi 250 m.\nLưới điều khiển 120 m; DEM gốc 30 m. Ngân sách ô được ghi nhận sau mỗi lượt chạy.';return;}
  const relief=$('gmsh-sizing').value==='relief',lo=Number($('h-min').value),hi=Number($('h-max').value),gamma=bootstrap?.meshing_parameters?.gamma;
  $('parameter-preview').textContent=relief&&gamma&&lo>0&&hi>=lo?
    'C = clip((relief − P25)/(P95 − P25), 0, 1). h = h_max − (h_max − h_min) × C^'+gamma+'.\nC = 0 → '+concise(hi)+' m; C = 0,5 → '+concise(hi-(hi-lo)*Math.pow(.5,gamma))+' m; C = 1 → '+concise(lo)+' m.\nGmsh dùng trường kích thước mục tiêu này để tạo lưới.':
    'Gmsh nhận một kích thước mục tiêu h; kích thước cạnh thực được đo sau khi tạo lưới.';
}
function resolutionRows(r){
  if(!r?.representative_pixel_size_m)return [['Raster tham chiếu','Chưa có. ROI vector/chữ nhật không có kích thước pixel.']];
  const unit=r.native_units==='degree'?'°':r.native_units,approx=r.approximate?'≈ ':'';
  const rows=[['Nguồn / band',r.path.split(/[\\/]/).pop()+' / '+r.band],['Pixel nguồn · cột × hàng',r.native_pixel_size.map(concise).join(' × ')+' '+unit],['CRS nguồn → tính toán',r.source_crs+' → '+r.analysis_crs],['Pixel trong CRS tính · cột × hàng',approx+r.representative_pixel_size_m.map(concise).join(' × ')+' m'],['Xoay / shear',r.rotated_or_sheared?'Có; tính theo hai vector pixel':'Không']];
  if(r.approximate)rows.push(['Khoảng lấy mẫu · cột / hàng',r.projected_spacing.map(v=>concise(v.minimum_m)+'–'+concise(v.maximum_m)).join(' / ')+' m · 3 × 3 vị trí'],['Cách đo','Khoảng cách XY trong hệ tọa độ tính toán']);
  return rows;
}
function activeResolution(){
  if($('gmsh-domain').value==='raster')return source?.resolution;
  return plannedSource&&plannedSource.request_path===$('planned-raster').value.trim()&&plannedSource.band===Number($('planned-band').value)&&plannedSource.analysis_crs===$('crs').value.trim()?plannedSource.resolution:null;
}
function renderResolution(){
  const r=activeResolution();$('resolution-info').replaceChildren();detailRows($('resolution-info'),resolutionRows(r));
  $('apply-resolution').disabled=busy||!r;$('resolution-warning').textContent='';
  if(!r){$('resolution-warning').textContent='Chưa có raster tham chiếu. Với ROI vector/chữ nhật, chọn kích thước theo bài toán; raster không bắt buộc cho chia đều.';return;}
  const [column,row]=r.representative_pixel_size_m,kind=$('mode').value,relief=['relief','sf1_relief'].includes($('gmsh-sizing').value);
  const sizes=kind==='structured_grid'?[Number($('width').value),Number($('height').value)]:relief?[Number($('h-min').value),Number($('h-max').value)]:[Number($('h-max').value)];
  const ratios=kind==='structured_grid'?'Rộng/cột = '+concise(sizes[0]/column)+'×; cao/hàng = '+concise(sizes[1]/row)+'×.':sizes.map((v,i)=>(relief?(i?'h_max':'h_min'):'h')+'/pixel cột, hàng = '+concise(v/column)+'×, '+concise(v/row)+'×.').join(' ');
  const smaller=kind==='structured_grid'?sizes[0]<column||sizes[1]<row:sizes.some(v=>v<Math.max(column,row));
  $('resolution-warning').textContent=ratios+'\n'+(smaller?'Raster giữ nguyên độ phân giải gốc khi ô lưới nhỏ hơn pixel.':'Một ô có thể tổng hợp nhiều pixel.')+'\nGợi ý tham khảo: uniform 1× bước pixel lớn hơn; relief 1×–4×; lưới chữ nhật theo hai bước pixel. Bấm Áp dụng để cập nhật kích thước.';
}
async function inspectPlanned(){
  const request=++plannedRequest,path=$('planned-raster').value.trim(),band=number('planned-band'),crs=$('crs').value.trim();
  plannedSource=null;renderResolution();
  const current=()=>request===plannedRequest&&path===$('planned-raster').value.trim()&&band===Number($('planned-band').value)&&crs===$('crs').value.trim();
  if(!path)return null;
  try{const meta=await api('/api/source',{path,band,analysis_crs:crs});if(!current())return null;plannedSource={...meta,request_path:path,analysis_crs:crs};renderResolution();return meta;}
  catch(error){if(!current())return null;$('resolution-info').textContent=error.message;throw error;}
}
function applyResolution(){
  const r=activeResolution();if(!r||busy)return;
  if($('mode').value==='gmsh_generated'&&$('gmsh-sizing').value==='sf1_relief'){toast('Hồ sơ SF-1 giữ h 250–1000 m; điều chỉnh ngân sách bằng Scale SF-1.');return;}
  const v=r.suggestions;
  if($('mode').value==='structured_grid'){$('width').value=v.structured_width_m;$('height').value=v.structured_height_m;}
  else if($('mode').value==='gmsh_generated'){
    $('h-max').value=$('gmsh-sizing').value==='relief'?v.relief_h_max_m:v.uniform_h_m;
    if($('gmsh-sizing').value==='relief')$('h-min').value=v.relief_h_min_m;
  }
  invalidate();mode();
}
function requestRows(q){
  const rows=[['Phương pháp',q.mode],['ROI',(q.roi_type||'Miền của mesh nhập')+' · '+(Array.isArray(q.roi_source)?q.roi_source.join(', '):q.roi_source)],['CRS tính toán',q.analysis_crs],['Giới hạn an toàn',q.max_cells+' ô']];
  if(q.boundary_vertices)rows.push(['Điểm biên nguồn / sau đổi CRS',q.boundary_vertices.source+' / '+q.boundary_vertices.transformed],['Đường biên','Giữ nguyên · không giản lược']);
  for(const warning of q.boundary_warnings||[])rows.push(['Thời gian xử lý',warning]);
  if(q.source_crs)rows.push(['CRS nguồn → tính toán',q.source_crs+' → '+q.analysis_crs]);
  if(q.requested_target_size_m!==undefined)rows.push(['h mục tiêu',concise(q.requested_target_size_m)+' m']);
  else if(q.requested_target_min_m!==undefined){rows.push(['h_min → h_max yêu cầu',[q.requested_target_min_m,q.requested_target_max_m].map(concise).join(' → ')+' m'],['DEM / band',q.dem_source+' / '+q.dem_band]);if(q.radius_m!==undefined)rows.push(['R yêu cầu',concise(q.radius_m)+' m · cửa sổ điều khiển chữ nhật']);}
  if(q.mode==='sf1_relief')rows.push(['Scale SF-1',Number.isFinite(q.sf1_scale)?concise(q.sf1_scale)+'×':'—'],['Hồ sơ','Hai cửa sổ 150 / 450 m; DEM gốc 30 m; lưới điều khiển 120 m'],['Gradation','1,5 mỗi 250 m'],['Ngân sách ô','Đo sau chạy; scale do người dùng khai báo']);
  if(q.cell_width_m!==undefined)rows.push(['Rộng × cao ô',[q.cell_width_m,q.cell_height_m].map(concise).join(' × ')+' m'],['Ô biên',q.boundary_policy==='strict'?'Strict · miền phải chia hết':'Clip · ô cuối có thể nhỏ hơn']);
  if(q.geometry_authority)rows.push(['Hình học','Từ mesh nhập; không tái tạo']);
  if(q.roi_subdivisions)rows.push(['Xấp xỉ cạnh ROI khi đổi CRS',q.roi_subdivisions+' đoạn/cạnh · '+(q.roi_type==='shapefile'?'cố định':'footprint GeoTIFF / ASC')]);
  if(q.fixed_policy)rows.push(['Phương pháp cố định',(q.fixed_policy.control_shape?q.fixed_policy.control_shape.join(' × ')+'; ':'')+'P'+q.fixed_policy.percentile_positions.join('/P')+'; gamma '+q.fixed_policy.gamma]);
  if(q.resolution_reference)rows.push(['Tham chiếu pixel',q.resolution_reference.role==='planned_extraction'?'Raster dự kiến extract · chỉ tham khảo':['roi_geotiff','roi_ascii_grid'].includes(q.resolution_reference.role)?'GeoTIFF / ASC ROI':'Chưa có'],...resolutionRows(q.resolution_reference));
  if(q.dem_resolution)rows.push(['Pixel DEM tính relief',q.dem_resolution.representative_pixel_size_m.map(concise).join(' × ')+' m']);
  return rows;
}

function detailRows(container,rows){const list=document.createElement('dl');list.className='detail-rows';for(const [label,value] of rows){const pair=document.createElement('div'),key=document.createElement('dt'),item=document.createElement('dd');pair.className='detail-item';key.textContent=label;item.textContent=String(value);pair.append(key,item);list.append(pair);}container.append(list);}
function sameCRS(){return source&&source.projected_metre&&$('crs').value.trim()===source.crs;}
function helpers(){ $('use-crs').disabled=!source?.projected_metre;$('use-bounds').disabled=!sameCRS();$('use-bounds').title=sameCRS()?'':'Chỉ dùng khi raster và CRS tính toán có cùng khai báo; không đổi nhãn CRS.'; }
function number(id){if(!$(id).value.trim())throw new Error(`Thiếu ${id}`);const value=Number($(id).value);if(!Number.isFinite(value))throw new Error(`Giá trị ${id} không hữu hạn`);return value;}
function config(){
  if(extractMode){
    if(!meshSnapshot)throw new Error('Chưa có lưới');
    return {schema_version:'gis-workbench-run-config-v2',operation:'extract',extraction_engine:'indexed_windowed_v1',run_name:'mesh-cherry-extract',analysis_crs:meshSnapshot.crs,raster_path:$('extract-raster').value.trim(),band:number('extract-band'),semantics:$('extract-semantics').value,value_units:$('extract-units').value.trim(),output_root:$('output').value,edge_subdivisions:number('subdivisions'),cell_source:{kind:'frozen_cells',path:meshSnapshot.path,geometry_digest:meshSnapshot.digest,max_cells:number('max-cells')}};
  }
  const kind=$('mode').value;const cell_source={kind,max_cells:number('max-cells')};
  if(kind==='structured_grid')Object.assign(cell_source,{extent:['west','south','east','north'].map(number),cell_width_m:number('width'),cell_height_m:number('height'),boundary_policy:$('policy').value});
  if(kind==='mesh_artifact')cell_source.path=$('mesh').value.trim();
  if(kind==='gmsh_reference')cell_source.preset='inclined_plane_250m_v0';
  if(kind==='gmsh_generated')Object.assign(cell_source,{executable:$('gmsh-exe').value.trim(),
    ...($('gmsh-domain').value==='rectangle'?{extent:['west','south','east','north'].map(number)}:{}),sizing:$('gmsh-sizing').value,
    h_min_m:$('gmsh-sizing').value==='uniform'?number('h-max'):number('h-min'),
    h_max_m:number('h-max')});
  if(kind==='gmsh_generated'&&$('gmsh-domain').value==='shapefile'){
    if(!$('boundary-path').value.trim())throw new Error('Chưa chọn ranh giới SHP');
    cell_source.boundary_path=$('boundary-path').value.trim();
  }
  if(['gmsh_generated','structured_grid'].includes(kind)&&$('gmsh-domain').value==='raster')cell_source.roi_raster_path=$('raster').value.trim();
  if(kind==='gmsh_generated'){
    if($('gmsh-domain').value!=='rectangle')delete cell_source.extent;
    if(['relief','sf1_relief'].includes(cell_source.sizing))Object.assign(cell_source,{dem_raster_path:$('dem-raster').value.trim(),dem_band:number('dem-band')});
    if(cell_source.sizing==='relief')cell_source.relief_radius_m=number('relief-radius');
    if(cell_source.sizing==='sf1_relief')Object.assign(cell_source,{h_min_m:250,h_max_m:1000,sf1_scale:number('sf1-scale'),dem_value_units:$('dem-value-units').value});
  }
  const meshOnly=true;
  if(meshOnly&&kind==='structured_grid'&&$('gmsh-domain').value==='raster'&&source?.display_footprint){const f=source.display_footprint;if(f.approximate||f.xy.length!==4||f.xy.some(p=>![f.bounds[0],f.bounds[2]].includes(p[0])||![f.bounds[1],f.bounds[3]].includes(p[1])))throw new Error('ROI raster xoay hoặc đổi CRS: chọn Gmsh để giữ đúng biên ROI.');}
  const resolution_reference=$('gmsh-domain').value!=='raster'&&$('planned-raster').value.trim()?{path:$('planned-raster').value.trim(),band:number('planned-band')}:null;
  return {...(resolution_reference?{resolution_reference}:{}),schema_version:'gis-workbench-run-config-v2',operation:'mesh_only',extraction_engine:'indexed_windowed_v1',run_name:'mesh-cherry-ui',analysis_crs:$('crs').value.trim(),output_root:$('output').value,cell_source,edge_subdivisions:number('subdivisions')};
}
async function inspectSource(){
  const request=++sourceRequest,crs=$('crs').value.trim();
  source=null;helpers();$('source-info').textContent='Đọc metadata…';
  const path=$('raster').value.trim(),band=number('band');
  const current=()=>request===sourceRequest&&path===$('raster').value.trim()&&band===Number($('band').value)&&crs===$('crs').value.trim();
  try{const meta=await api('/api/source',{path,band,analysis_crs:crs});if(!current())return null;
    source=meta;$('source-info').className='info';$('source-info').replaceChildren();
    const filename=document.createElement('span');filename.className='file-path';filename.textContent=path;filename.title=path;$('raster').title=path;$('source-info').append(filename);
    detailRows($('source-info'),[['Định dạng',meta.driver==='AAIGrid'?'ESRI ASCII Grid (.asc)':'GeoTIFF'],['CRS nguồn',meta.crs||'THIẾU CRS'],['Kích thước',`${meta.shape[1]} × ${meta.shape[0]} pixel`],['Miền nguồn · X',`${meta.bounds[0]} → ${meta.bounds[2]}`],['Miền nguồn · Y',`${meta.bounds[1]} → ${meta.bounds[3]}`],['Band / kiểu',`${meta.band}/${meta.band_count} · ${meta.dtype}`],['NoData / mask',`${meta.nodata} / ${meta.mask.join(', ')}`],['Scale / offset',`${meta.scale} / ${meta.offset}`]]);
    if(!meta.crs){const warning=document.createElement('p');warning.textContent='Raster chưa có CRS. Với ASC, đặt file .prj cùng tên cạnh .asc. Nhập CRS tính toán không sửa được lỗi này.';$('source-info').append(warning);$('source-info').className='info error';}
    if(meta.resolution)detailRows($('source-info'),resolutionRows(meta.resolution).slice(1));
    helpers();renderResolution();await refreshRecent();await loadInputPreview();return meta;
  }catch(error){if(!current())return null;$('source-info').className='info error';$('source-info').textContent=error.message;throw error;}
}
async function inspectBoundary(){
  const request=++boundaryRequest;
  const path=$('boundary-path').value.trim(),crs=$('crs').value.trim();$('boundary-info').textContent='Đang đọc bộ SHP và chuyển CRS…';
  const current=()=>request===boundaryRequest&&path===$('boundary-path').value.trim()&&crs===$('crs').value.trim();
  try{const report=await api('/api/boundary',{path,analysis_crs:crs});if(!current())return null;
    $('boundary-info').replaceChildren();detailRows($('boundary-info'),[['Trạng thái','✓ Hình học hợp lệ'],['CRS nguồn',report.source_crs],['CRS tính toán',crs],['Số điểm biên nguồn',report.source_vertices.toLocaleString('vi-VN')],['Số điểm sau đổi CRS',report.transformed_vertices.toLocaleString('vi-VN')],['Xử lý đường biên','Giữ nguyên · không giản lược'],['Diện tích',report.area_m2.toLocaleString('vi-VN')+' m²'],['Số vùng / lỗ',`${report.components} / ${report.holes}`],['Đổi CRS',`${report.transform_subdivisions} đoạn/cạnh; không đổi nhãn CRS`],['X min → max',`${report.bounds[0]} → ${report.bounds[2]} m`],['Y min → max',`${report.bounds[1]} → ${report.bounds[3]} m`]]);
    for(const message of report.warnings||[]){const note=document.createElement('p');note.className='boundary-warning';note.setAttribute('role','status');note.textContent=message;$('boundary-info').append(note);}
    ['west','south','east','north'].forEach((id,i)=>$(id).value=report.bounds[i]);invalidate();await refreshRecent();await loadInputPreview();return report;
  }catch(error){if(!current())return null;$('boundary-info').textContent=error.message;throw error;}
}
function svgElement(name,attributes){const element=document.createElementNS('http://www.w3.org/2000/svg',name);for(const [key,value] of Object.entries(attributes))element.setAttribute(key,String(value));return element;}
function cellArea(cell){const points=cell.xy.map(p=>[p[0]-cell.xy[0][0],p[1]-cell.xy[0][1]]);return Math.abs(points.reduce((a,p,i)=>{const q=points[(i+1)%points.length];return a+p[0]*q[1]-q[0]*p[1];},0))/2;}
function coverageState(record){
  return {outside:record.footprint_coverage===0,partial:record.footprint_coverage>0&&record.footprint_coverage<1-1e-8,
    nodata:record.valid_coverage<record.footprint_coverage-1e-8,
    full:record.value!==null&&record.valid_coverage>=1-1e-8};
}
function selectCell(cell){
  selectedCellId=cell.cell_id;
  if(batchedMap){batchedMap.focusIndex=geometry.indexOf(cell);highlightBatched(cell,true);}
  for(const item of $('map').querySelectorAll('.cell-poly'))item.setAttribute('aria-pressed',String(item.dataset.id===cell.cell_id));
  $('cell-search').value=cell.cell_id;
  for(const poly of $('map').querySelectorAll('.cell-poly'))poly.classList.toggle('selected',poly.dataset.id===cell.cell_id);
  const record=attrs.get(cell.cell_id);let text=`ID: ${cell.cell_id}\nKiểu: ${cell.kind}\nDiện tích: ${(record?.area_m2??cellArea(cell)).toLocaleString('vi-VN',{maximumFractionDigits:3})} m²`;
  if(record){text+=`\nGiá trị: ${record.value===null?'null · không có dữ liệu hợp lệ':record.value.toLocaleString('vi-VN',{maximumFractionDigits:6})} ${mapUnits}\nFootprint coverage: ${(record.footprint_coverage*100).toFixed(2)}%\nValid coverage: ${(record.valid_coverage*100).toFixed(2)}%`;
    if('minimum' in record)text+=`\nMin / Max: ${record.minimum} / ${record.maximum}\nStd: ${record.std}`;
    if(record.class_fractions)text+=`\nTỷ lệ lớp: ${JSON.stringify(record.class_fractions)}`;
  }else text+='\nChưa trích thuộc tính.';
  $('cell-info').className='info';$('cell-info').replaceChildren();
  $('cell-announcement').textContent=`Ô ${cell.cell_id}: ${record?record.value===null?'không có dữ liệu hợp lệ':record.value.toLocaleString('vi-VN')+' '+mapUnits:'chưa trích thuộc tính'}`;
  if(record){const value=document.createElement('strong');value.className='cell-value';value.textContent=record.value===null?'—':record.value.toLocaleString('vi-VN',{maximumFractionDigits:6});const unit=document.createElement('span');unit.className='cell-unit';unit.textContent=record.value===null?'Không có giá trị hợp lệ':mapUnits;$('cell-info').append(value,unit);}
  const details=document.createElement('div');details.className='cell-detail';details.textContent=text;$('cell-info').append(details);
  const xy=cell.xy,extent=document.createElement('p');extent.className='tiny';extent.textContent=`Miền ô · X: ${Math.min(...xy.map(p=>p[0]))} → ${Math.max(...xy.map(p=>p[0]))}; Y: ${Math.min(...xy.map(p=>p[1]))} → ${Math.max(...xy.map(p=>p[1]))} (m)`;$('cell-info').append(extent);
  if(record){const state=coverageState(record),note=document.createElement('p');note.className='tiny';note.textContent=state.outside?'Ô nằm ngoài phạm vi raster. null: chưa có dữ liệu hợp lệ.':state.full?'Dữ liệu hợp lệ phủ toàn bộ ô.':`${state.partial?'Raster phủ một phần ô. ':''}${state.nodata?'Có NoData / mask trong phần raster giao ô. ':''}Giá trị được tính trên diện tích có dữ liệu hợp lệ.`;$('cell-info').append(note);}
  if(tutorialActive&&record&&attrs.size===2&&[...attrs.values()].every(r=>r.valid_coverage===1)&&[...attrs.values()].map(r=>r.value).sort((a,b)=>a-b).join(',')==='10,30'){
    tutorialClicks.add(cell.cell_id);$('tutorial-finish').disabled=tutorialClicks.size!==2;
    refreshCoach();
  }
}
function highlightBatched(cell,selected=false){
  const b=batchedMap;if(!b)return;
  b.overlay.setAttribute('points',cell.xy.map(([x,y])=>`${x-b.xmin},${b.ymax-y}`).join(' '));
  b.overlay.setAttribute('visibility','visible');b.overlay.setAttribute('class',selected?'batch-selected':'batch-selected batch-focus');
}
function pickBatchedCell(x,y){
  if(!batchedMap)return null;
  const epsilon=batchedMap.span*1e-10;
  for(let index=0;index<geometry.length;index++){
    const [left,bottom,right,top]=batchedMap.boxes[index];
    if(x<left-epsilon||x>right+epsilon||y<bottom-epsilon||y>top+epsilon)continue;
    const cell=geometry[index];let inside=false;
    for(let i=0,j=cell.xy.length-1;i<cell.xy.length;j=i++){
      const [ax,ay]=cell.xy[j],[bx,by]=cell.xy[i],dx=bx-ax,dy=by-ay;
      if(Math.abs(dx*(y-ay)-dy*(x-ax))<=epsilon*Math.max(1,Math.hypot(dx,dy))&&
          x>=Math.min(ax,bx)-epsilon&&x<=Math.max(ax,bx)+epsilon&&y>=Math.min(ay,by)-epsilon&&y<=Math.max(ay,by)+epsilon)return cell;
      if((ay>y)!==(by>y)&&x<(bx-ax)*(y-ay)/(by-ay)+ax)inside=!inside;
    }
    if(inside)return cell;
  }
  return null;
}
function renderBatched(cells,xmin,ymax,span){
  // A handful of SVG paths retain every cell; source XY/IDs remain untouched.
  const groups=new Map(),dots=[],boxes=[],format=value=>String(Number(value.toPrecision(10)));
  for(const cell of cells){
    const record=attrs.get(cell.cell_id),state=record?coverageState(record):null;
    const key=state?`${state.outside?' outside-cell':''}${state.partial?' partial-cell':''}${state.nodata?' nodata-cell':''}${state.full?' valid-cell':''}`:'';
    if(!groups.has(key))groups.set(key,[]);
    groups.get(key).push(cell.xy.map(([x,y],i)=>(i?'L':'M')+format(x-xmin)+' '+format(ymax-y)).join('')+'Z');
    let cx=0,cy=0,left=Infinity,bottom=Infinity,right=-Infinity,top=-Infinity;
    for(const [x,y] of cell.xy){cx+=x;cy+=y;left=Math.min(left,x);right=Math.max(right,x);bottom=Math.min(bottom,y);top=Math.max(top,y);}
    boxes.push([left,bottom,right,top]);
    dots.push('M'+format(cx/cell.xy.length-xmin)+' '+format(ymax-cy/cell.xy.length)+'h0');
  }
  const paths=svgElement('g',{'aria-hidden':'true'});
  for(const [key,parts] of groups)paths.append(svgElement('path',{d:parts.join(''),class:'mesh-batch'+key}));
  const nodes=svgElement('path',{d:dots.join(''),class:'batch-nodes','aria-hidden':'true'});
  const overlay=svgElement('polygon',{class:'batch-selected',visibility:'hidden','aria-hidden':'true'});
  $('map').append(paths,nodes,overlay);$('map').setAttribute('tabindex','0');
  batchedMap={xmin,ymax,span,boxes,overlay,focusIndex:0};
}
function meshBounds(cells,boundary){
  let xmin=Infinity,ymin=Infinity,xmax=-Infinity,ymax=-Infinity;
  const visit=([x,y])=>{xmin=Math.min(xmin,x);xmax=Math.max(xmax,x);ymin=Math.min(ymin,y);ymax=Math.max(ymax,y);};
  for(const cell of cells)for(const point of cell.xy)visit(point);
  for(const point of boundary?.xy||[])visit(point);
  return [xmin,ymin,xmax,ymax];
}
function draw(cells,records=[],contract={crs:$('crs').value,units:$('units').value,footprint:source?.display_footprint},remember=true){
  const limit=bootstrap?.mesh_limits?.interactive_cells||2000;
  const batchedLimit=contract.display?.batched_cell_limit||100000;
  if(cells.length>batchedLimit){contract={...contract,display:{mode:'summary',cell_count:cells.length,interactive_cell_limit:limit}};cells=[];records=[];}
  const summaryOnly=!cells.length&&contract.display?.mode==='summary';
  for(const id of ['fit','zoom-in','zoom-out','show-edges','show-nodes'])if($(id))$(id).disabled=summaryOnly||!cells.length;
  if(remember)savedMap=cells.length||summaryOnly?{cells,records,contract}:null;
  stopMapDrag();mapSuppressClick=false;
  batchedMap=null;$('map').removeAttribute('tabindex');
  mapUnits=contract.units;mapCRS=contract.crs;
  geometry=cells;attrs=new Map(records.map(record=>[record.cell_id,record]));$('map').replaceChildren();$('empty-map').hidden=!!cells.length;
  selectedCellId=null;$('cell-search').value='';$('cell-search').disabled=!cells.length;$('cell-find').disabled=!cells.length;$('cell-announcement').textContent='';
  $('result-metrics').hidden=!records.length;
  if(records.length){$('metric-cells').textContent=String(cells.length);$('metric-valid').textContent=String(records.filter(r=>r.value!==null).length);$('metric-missing').textContent=String(records.filter(r=>r.value===null).length);}
  $('cell-info').textContent='Chưa chọn cell.';$('map-state').textContent=cells.length?`${cells.length} cell`:'Chưa có cell';
  document.querySelector('.coverage-legend').hidden=!records.length;$('coverage-summary').hidden=!records.length;$('coverage-pending').hidden=!!records.length;$('raster-extent').hidden=true;
  if(records.length){const states=records.map(coverageState),count=key=>states.filter(s=>s[key]).length;$('coverage-summary').textContent=`${count('full')} ô phủ đủ dữ liệu · ${count('outside')} ô ngoài raster · ${count('partial')} ô raster phủ một phần · ${count('nodata')} ô có NoData / mask. `;$('coverage-summary').classList.toggle('coverage-warning',count('full')!==records.length);}
  $('empty-map').textContent=summaryOnly?'Lưới vượt giới hạn xem trực tiếp. Tải kết quả để xem đầy đủ.':'Chưa có lưới để hiển thị.';
  if(summaryOnly){
    $('map-state').textContent=contract.display.cell_count.toLocaleString('vi-VN')+' ô · tổng quan';
    $('map-crs').textContent='CRS tính toán · m';$('map-crs').title=mapCRS;
    if(contract.attributeSummary){const s=contract.attributeSummary;$('result-metrics').hidden=false;$('metric-cells').textContent=s.cell_count;$('metric-valid').textContent=s.valid_count;$('metric-missing').textContent=s.missing_count;}
  }
  if(!cells.length){fitBox=null;$('map').removeAttribute('viewBox');return;}
  const boundary=contract.footprint;
  const [xmin,ymin,xmax,ymax]=meshBounds(cells,boundary),span=Math.max(xmax-xmin,ymax-ymin),pad=span*.07;
  fitBox=[-pad,-pad,xmax-xmin+2*pad,ymax-ymin+2*pad];$('map').setAttribute('viewBox',fitBox.join(' '));
  const backdrop=svgElement('g',{}),polygons=svgElement('g',{}),nodes=svgElement('g',{});
  if(boundary){const coords=boundary.xy.map(p=>`${p[0]-xmin},${ymax-p[1]}`).join(' ');backdrop.append(svgElement('polygon',{points:coords,class:'raster-footprint-fill'}));$('map').append(svgElement('polygon',{points:coords,class:'raster-footprint-edge'}));$('raster-extent').hidden=false;const b=boundary.bounds;$('raster-extent').textContent=`Raster trong CRS tính toán · X: ${b[0]} → ${b[2]} m · Y: ${b[1]} → ${b[3]} m${boundary.approximate?' · Biên chuyển CRS xấp xỉ; không thể hiện mask NoData.':''}`;}
  if(cells.length>limit){
    $('map').append(backdrop);renderBatched(cells,xmin,ymax,span);
    $('map-state').textContent=cells.length.toLocaleString('vi-VN')+' ô';
    $('map-crs').textContent=mapCRS.startsWith('PROJCS')?'CRS tính toán · m':`${mapCRS} · X,Y (m)`;
    $('map-crs').title=mapCRS;updateMapControls();return;
  }
  for(const cell of cells){
    const r=attrs.get(cell.cell_id),coverage=r?coverageState(r):null,state=coverage?`${coverage.outside?' outside-cell':''}${coverage.partial?' partial-cell':''}${coverage.nodata?' nodata-cell':''}${coverage.full?' valid-cell':''}`:'';
    const poly=svgElement('polygon',{points:cell.xy.map(p=>`${p[0]-xmin},${ymax-p[1]}`).join(' '),class:'cell-poly'+state,tabindex:-1,role:'button','aria-pressed':'false','aria-label':`Ô ${cell.cell_id}`,'data-id':cell.cell_id});
    const title=svgElement('title',{});title.textContent=cell.cell_id;poly.append(title);poly.addEventListener('click',()=>selectCell(cell));poly.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();selectCell(cell);}});polygons.append(poly);
    const cx=cell.xy.reduce((a,p)=>a+p[0],0)/cell.xy.length,cy=cell.xy.reduce((a,p)=>a+p[1],0)/cell.xy.length;
    const node=svgElement('circle',{cx:cx-xmin,cy:ymax-cy,r:span/230,class:'cell-node',tabindex:-1,'aria-hidden':'true','data-id':cell.cell_id});node.addEventListener('click',()=>selectCell(cell));nodes.append(node);
  }
  $('map').append(backdrop,polygons,nodes);
  $('map-crs').textContent=mapCRS.startsWith('PROJCS')?'CRS tính toán · m':`${mapCRS} · X,Y (m)`;
  $('map-crs').title=mapCRS;
  updateMapControls();
}
function displayCellCount(){return savedMap?.contract.display?.cell_count||geometry.length;}
function updateMapControls(){
  $('map').classList.toggle('no-edges',!$('show-edges').checked);$('map').classList.toggle('no-nodes',!$('show-nodes').checked);
  // One keyboard stop per cell, even with nodes hidden; arrows move within map.
  const items=[...$('map').querySelectorAll('.cell-poly')];
  for(const [i,item] of items.entries())item.setAttribute('tabindex',i===0?0:-1);
}
function moveMapFocus(event){
  if(batchedMap&&geometry.length){
    if(event.key==='Enter'||event.key===' '){event.preventDefault();selectCell(geometry[batchedMap.focusIndex]);return;}
    if(!['ArrowRight','ArrowDown','ArrowLeft','ArrowUp','Home','End'].includes(event.key))return;
    event.preventDefault();const i=batchedMap.focusIndex;
    batchedMap.focusIndex=event.key==='Home'?0:event.key==='End'?geometry.length-1:(i+(['ArrowRight','ArrowDown'].includes(event.key)?1:-1)+geometry.length)%geometry.length;
    const cell=geometry[batchedMap.focusIndex];highlightBatched(cell);
    $('cell-announcement').textContent='Ô '+cell.cell_id+'. Nhấn Enter để xem thuộc tính.';return;
  }
  if(!['ArrowRight','ArrowDown','ArrowLeft','ArrowUp','Home','End'].includes(event.key))return;
  const items=[...$('map').querySelectorAll('.cell-poly')],index=items.indexOf(event.target);if(index<0)return;
  event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(['ArrowRight','ArrowDown'].includes(event.key)?1:-1)+items.length)%items.length;
  items.forEach((item,i)=>item.setAttribute('tabindex',i===next?0:-1));items[next].focus();
}
async function validate(){
  const requestRevision=revision;let cfg;$('validate').disabled=true;$('run').disabled=true;preflightId=null;$('validation').textContent='Đang kiểm tra metadata, hình học và workload…';
  try{if(!extractMode&&!checkParameterFields())throw new Error('Sửa các ô được đánh dấu trước khi kiểm tra.');cfg=config();const data=await api('/api/preflight',cfg);if(requestRevision!==revision){toast('Cấu hình thay đổi trong khi kiểm tra. Hãy kiểm tra lại.');return;}
    const report=data.report;$('validation').className='info '+(report.status==='pass'?'pass':'error');
    const estimate=report.workload_estimate;
    $('validation').textContent=report.status==='pass'?`✓ Preflight đạt\nEngine: indexed_windowed_v1\nCell: ${report.cell_count===null?'tạo khi meshing':report.cell_count}\nCRS cell / analysis: ${$('crs').value} · metre\nRaster: ${source?.crs||'WKT ghi trong hồ sơ'}\nSource: ${report.pixel_count.toLocaleString('vi-VN')} pixel\nDự kiến đọc: ${estimate?estimate.estimated_pixels_read.toLocaleString('vi-VN')+' pixel / '+estimate.estimated_windows_read+' window':'kiểm tra sau khi sinh mesh'}\nCeilings: source 10M · đọc 2M · pairs 5M\nGiới hạn cell: ${number('max-cells')} · window ≤ 65.536`:report.errors.join('\n');
    if(report.status==='pass'){$('validation').replaceChildren();const heading=document.createElement('strong');heading.textContent='✓ Cấu hình hợp lệ trong giới hạn v1.';$('validation').append(heading);detailRows($('validation'),[['Nguồn',`${report.pixel_count.toLocaleString('vi-VN')} pixel`],['CRS tính toán',$('crs').value],['Raster',cfg.raster_path||'—'],['Band',cfg.band??'—'],['Loại thuộc tính',cfg.semantics||'—'],['Đơn vị',cfg.value_units||'—'],['Mã hình học',report.geometry_digest||'—'],['Số ô',report.cell_count??'Tạo khi meshing'],['Dự kiến đọc',estimate?`${estimate.estimated_pixels_read.toLocaleString('vi-VN')} pixel / ${estimate.estimated_windows_read} cửa sổ`:'Kiểm tra sau khi sinh mesh'],['Giới hạn',`${number('max-cells')} ô; nguồn 10M / đọc 2M pixel; 5M cặp; 65.536 pixel/cửa sổ`]]);}
    if(report.status==='pass'&&extractMode&&report.raster_resolution){$('extract-resolution').replaceChildren();detailRows($('extract-resolution'),resolutionRows(report.raster_resolution));const note=document.createElement('p');note.textContent='Đây là độ phân giải raster extract thực tế. Giữ nguyên ô và node của lưới đã lưu, không chia lại.';$('extract-resolution').append(note);}
    let footprint=null;
    if(report.status==='pass'&&config().raster_path){const cfg=config();const metadata=await api('/api/source',{path:cfg.raster_path,band:cfg.band,analysis_crs:cfg.analysis_crs});footprint=metadata.display_footprint;if(requestRevision!==revision)return;}
    if(report.status==='pass'&&report.operation==='mesh_only'){
      $('validation').replaceChildren();detailRows($('validation'),[['Trạng thái','Cấu hình tạo lưới hợp lệ'],...requestRows(report.meshing_request),['Số ô',report.cell_count??'Tính khi Gmsh tạo lưới']]);
    }
    preflightId=data.preflight_id;$('run').disabled=!preflightId||busy;if(!extractMode)draw(data.cells,[],{crs:$('crs').value,units:$('units').value,footprint,display:data.display});refreshCoach();
  }catch(error){if(requestRevision!==revision)return;$('validation').className='info error';$('validation').textContent=error.message;}finally{$('validate').disabled=busy;}
}
function setBusy(value){busy=value;for(const el of $('extract-options').querySelectorAll('input,select,button'))el.disabled=value;for(const input of $('form').querySelectorAll('input,select,button'))input.disabled=value;for(const control of document.querySelectorAll('button[data-stage],#gmsh-setup-open,#tutorial-help'))control.disabled=value;if(!value){mode();helpers();document.querySelector('button[data-stage="4"]').disabled=!jobId||!displayCellCount();$('run').disabled=!preflightId;$('tutorial-finish').disabled=!tutorialActive||tutorialClicks.size!==2;}$('progress').hidden=!value;$('cancel-run').disabled=!value||!jobId||cancelPending;refreshCoach();}
async function startRun(){
  if(!preflightId||busy)return;const id=preflightId;preflightId=null;resetResult();
  if(extractMode&&meshSnapshot){draw(meshSnapshot.cells,[],{crs:meshSnapshot.crs,units:'',display:meshSnapshot.display});showResultTab('extract');}
  setBusy(true);$('exports').hidden=true;$('job-status').className='info';$('job-status').textContent='Đang khởi chạy…';jobHeadline.textContent='Đang khởi chạy…';
  $('job-monitor').hidden=false;$('monitor-phase').textContent='Đang khởi chạy…';$('monitor-elapsed').textContent='0 giây';$('monitor-eta').textContent='Chưa đủ dữ liệu để ước tính';$('monitor-percent').textContent='Chưa có phần trăm';$('phase-progress').hidden=true;
  runRequest={preflightId:id,requestId:newRunRequestId(),operation:extractMode?'extract':'main'};saveRunRequest();
  try{const response=await api('/api/run',{preflight_id:id,request_id:runRequest.requestId});
    if(typeof response.job_id!=='string'||!response.job_id||
       (response.request_id!==undefined&&response.request_id!==runRequest.requestId)||
       (response.operation!==undefined&&!validMainOperation(response.operation)))
      throw Object.assign(Error('Run start returned a different operation or request.'),{status:409});
    jobId=response.job_id;await pollJob();}
  catch(error){$('job-status').className='info error';$('job-status').textContent=error.message;await recoverStart();}
}
function newRunRequestId(){return typeof crypto!=='undefined'&&crypto.randomUUID?crypto.randomUUID():`run-${Date.now()}-${Math.random().toString(36).slice(2)}`;}
function saveRunRequest(){try{if(runRequest&&typeof sessionStorage.setItem==='function')sessionStorage.setItem(RUN_REQUEST_STORAGE_KEY,JSON.stringify(runRequest));}catch{}}
function readRunRequest(){
  try{const value=JSON.parse(typeof sessionStorage.getItem==='function'?sessionStorage.getItem(RUN_REQUEST_STORAGE_KEY)||'null':'null');
    return value&&typeof value==='object'&&typeof value.preflightId==='string'&&
      typeof value.requestId==='string'&&/^[A-Za-z0-9_-]{8,80}$/.test(value.requestId)?
      {...value,operation:value.operation==='extract'?'extract':'main'}:null;
  }catch{return null;}
}
function clearRunRequest(requestId){
  if(requestId&&runRequest?.requestId!==requestId)return;
  runRequest=null;try{if(typeof sessionStorage.removeItem==='function')sessionStorage.removeItem(RUN_REQUEST_STORAGE_KEY);}catch{}
}
function saveAcceptedMesh(){try{if(meshSnapshot&&typeof sessionStorage.setItem==='function')sessionStorage.setItem(ACCEPTED_MESH_STORAGE_KEY,meshSnapshot.jobId);}catch{}}
function clearAcceptedMesh(){try{if(typeof sessionStorage.removeItem==='function')sessionStorage.removeItem(ACCEPTED_MESH_STORAGE_KEY);}catch{}}
function isDefinitivelyStaleAcceptedMesh(error){return [400,403,404,410].includes(error?.status);}
async function restoreSessionMesh(){
  let acceptedId;try{acceptedId=typeof sessionStorage.getItem==='function'?sessionStorage.getItem(ACCEPTED_MESH_STORAGE_KEY):null;}catch{return false;}
  if(!acceptedId||!/^[A-Za-z0-9_-]{8,80}$/.test(acceptedId))return false;
  const [job,manifest,cells,qa]=await Promise.all([api(`/api/job?id=${encodeURIComponent(acceptedId)}`),...['manifest','cells','qa'].map(name=>api(`/api/artifact?id=${encodeURIComponent(acceptedId)}&name=${name}${name==='cells'?'&view=1':''}`))]);
  if(job.status!=='pass'||manifest.status!=='pass'||manifest.operation!=='mesh_only'||typeof manifest.geometry_digest!=='string')throw Error('Lưới đã chấp nhận không còn xác thực được.');
  const output=job.result?.output||'';meshSnapshot={output,digest:manifest.geometry_digest,path:output?output+'/cells.json':'',hasMsh:!!manifest.gmsh_generated,jobId:acceptedId,cells:cells.cells,display:cells.display,crs:cells.crs_wkt,qa};jobId=acceptedId;resultPath=output;
  draw(cells.cells,[],{crs:cells.crs_wkt,units:'',display:cells.display});showMeshQuality(qa.mesh_quality);
  $('exports').hidden=false;$('csv').hidden=true;$('json').hidden=true;$('msh').hidden=!meshSnapshot.hasMsh;$('mesh-json').hidden=false;
  $('extract-options').hidden=false;showResultTab('mesh');stage(4,false,true);return true;
}
function validMainOperation(operation){return operation==='extract'||operation==='mesh_only';}
async function recoverStart(){
  jobHeadline.textContent='Không xác nhận được phản hồi khởi chạy; đang kiểm tra lượt chạy hiện tại…';
  const pending=runRequest;if(!pending){setBusy(false);return;}
  try{
    const status=await api('/api/run/status?request_id='+encodeURIComponent(pending.requestId),undefined,8000);
    if(runRequest?.requestId!==pending.requestId)return;
    if(status.job_id){
      if(status.request_id!==pending.requestId||!validMainOperation(status.operation))
        throw Object.assign(Error('Run recovery returned a different operation or request.'),{status:409});
      jobId=status.job_id;await pollJob(jobId);return;
    }
    const started=await api('/api/run',{preflight_id:pending.preflightId,request_id:pending.requestId},8000);
    if(runRequest?.requestId!==pending.requestId)return;
    if(started.request_id!==pending.requestId||!validMainOperation(started.operation))
      throw Object.assign(Error('Run start returned a different operation or request.'),{status:409});
    jobId=started.job_id;await pollJob(jobId);
  }catch(error){
    if([400,403,404,409,410].includes(error.status)){
      clearRunRequest(pending.requestId);setBusy(false);
      jobHeadline.textContent='Không thể khôi phục đúng lượt chạy. Hãy kiểm tra cấu hình rồi chạy lại.';
      return;
    }
    jobHeadline.textContent='Chưa kết nối lại được; giữ nguyên cấu hình và đang thử khôi phục lượt chạy…';
    setTimeout(()=>{if(runRequest?.requestId===pending.requestId)recoverStart();},2000);
  }
}
async function pollJob(expectedJobId=jobId){
  let terminal=false;
  try{const job=await api('/api/job?id='+encodeURIComponent(expectedJobId));if(expectedJobId!==jobId)return;renderMonitor(job);await syncJobLog(expectedJobId);if(expectedJobId!==jobId)return;$('job-status').textContent=stageNames[job.stage]||job.stage;jobHeadline.textContent=job.status==='stopping'?'Đang dừng… chờ bước hiện tại kết thúc.':job.status==='running'?`🍒 ${stageNames[job.monitor?.phase]||stageNames[job.stage]||job.stage}…`:job.status==='pass'?'✓ Đã tạo kết quả. Bạn có thể xuất CSV / JSON.':'Không hoàn tất. Mở chi tiết kỹ thuật để xem lỗi.';
    if(['running','stopping'].includes(job.status)){setTimeout(()=>{if(jobId===expectedJobId)pollJob(expectedJobId);},650);return;}
    terminal=true;resultPath=job.result.output||'';const manifest=resultPath?await api(`/api/artifact?id=${jobId}&name=manifest`):null;
    if(job.status==='cancelled'){if(extractMode&&meshSnapshot){restoreAcceptedMeshPresentation();extractMode=false;}else{$('exports').hidden=true;}$('job-status').className='info';$('job-status').textContent='Đã dừng theo yêu cầu. Kết quả dang dở không được dùng để xuất.';jobHeadline.textContent=meshSnapshot?'Đã dừng extract. Lưới đã tạo trước đó vẫn được giữ nguyên.':'Đã dừng tác vụ. Log được giữ để xem lại.';clearRunRequest();setBusy(false);if(meshSnapshot)stage(4,false);return;}
    if(job.status==='fail'){const diagnosticPath=resultPath;if(extractMode&&meshSnapshot)restoreAcceptedMeshPresentation();$('job-status').className='info error';$('job-status').textContent=`Thất bại · ${manifest?.failed_stage||job.stage}\n${job.result.error}\nDiagnostic: ${diagnosticPath||'chưa tạo run'}`;clearRunRequest();setBusy(false);if(meshSnapshot)stage(4,false);return;}
    if(manifest.operation==='mesh_only'){
      const [cells,qa]=await Promise.all(['cells','qa'].map(name=>api(`/api/artifact?id=${jobId}&name=${name}&view=1`)));
      if(expectedJobId!==jobId)return;
      meshSnapshot={output:resultPath,digest:manifest.geometry_digest,path:resultPath+'/cells.json',hasMsh:!!manifest.gmsh_generated,jobId,cells:cells.cells,display:cells.display,crs:cells.crs_wkt,qa};saveAcceptedMesh();
      draw(cells.cells,[],{crs:cells.crs_wkt,units:'',footprint:source?.display_footprint,display:cells.display});
      showMeshQuality(qa.mesh_quality);
      $('job-status').textContent='Lưới đã lưu: '+resultPath;jobHeadline.textContent='✓ Đã tạo '+(cells.display?.cell_count??cells.cells.length)+' ô lưới.';
      $('exports').hidden=false;$('csv').hidden=true;$('json').hidden=true;
      $('msh').hidden=!manifest.gmsh_generated;$('mesh-json').hidden=false;
      $('extract-options').hidden=false;if(tutorialActive){$('extract-raster').value=$('raster').value;$('extract-units').value='synthetic-value';}clearRunRequest();setBusy(false);stage(4);showResultTab(tutorialActive?'extract':'mesh');return;
    }
    const [cells,attributes,qa]=await Promise.all(['cells','attributes','qa'].map(name=>api(`/api/artifact?id=${jobId}&name=${name}&view=1`)));
    const metadata=await api('/api/source',{path:attributes.provenance.source_path,band:attributes.provenance.band,analysis_crs:attributes.provenance.analysis_crs_wkt});
    if(expectedJobId!==jobId)return;
    draw(cells.cells,attributes.cells,{crs:attributes.provenance.analysis_crs_wkt,units:attributes.provenance.value_units,footprint:metadata.display_footprint,display:cells.display,attributeSummary:attributes.summary});const evidence=manifest.engine_evidence;
    const cellCount=cells.display?.cell_count??cells.cells.length,fullCount=attributes.summary?.full_count??attributes.cells.filter(r=>coverageState(r).full).length;
    $('job-status').className='info pass';$('job-status').textContent=`✓ Hoàn thành · ${cellCount} cell\nValid coverage (mean): ${(qa.coverage.valid_coverage.mean*100).toFixed(2)}%\nFootprint coverage: ${(qa.coverage.footprint_coverage.mean*100).toFixed(2)}%\nThời gian run: ${manifest.runtime_seconds.toFixed(2)} s\nĐọc: ${evidence.pixels_read.toLocaleString('vi-VN')} pixel / ${evidence.windows_read} window\nCandidate pairs: ${evidence.candidate_pairs.toLocaleString('vi-VN')}\nPeak RSS lifetime: ${evidence.memory_metric.value===null?'unavailable':(evidence.memory_metric.value/1048576).toFixed(1)+' MiB'}\nBộ raster và file đi kèm: không đổi${attributes.qa.residual===undefined?'':'\nExtensive residual: '+attributes.qa.residual}\nOutput: ${resultPath}`;
    jobHeadline.textContent=`✓ ${cellCount} ô đã trích thuộc tính. ${fullCount} ô phủ đủ dữ liệu.`;
    $('exports').hidden=false;$('csv').hidden=false;$('json').hidden=false;clearRunRequest();setBusy(false);stage(4);showResultTab('extract');
    $('msh').hidden=!(meshSnapshot?.hasMsh||manifest.gmsh_generated);$('mesh-json').hidden=!(meshSnapshot||manifest.gmsh_generated);
  }catch(error){if(expectedJobId!==jobId)return;$('job-status').className='info error';$('job-status').textContent=`Không đọc được trạng thái/kết quả: ${error.message}.`;
    if(terminal){jobHeadline.textContent='Lượt chạy đã kết thúc nhưng chưa tải đủ kết quả. Đang thử tải lại…';setBusy(false);}
    else{jobHeadline.textContent='Mất kết nối UI — đang thử lại. Lượt chạy có thể vẫn tiếp tục; giữ nguyên cấu hình.';}
    setTimeout(()=>{if(jobId===expectedJobId)pollJob(expectedJobId);},2000);
  }
}
async function download(name){
  const artifactJob=name==='gmsh_log'?monitorJobId:['msh','mesh','cells'].includes(name)&&meshSnapshot?meshSnapshot.jobId:jobId;
  const response=await fetch(`/api/artifact?id=${artifactJob}&name=${name}&download=1`,{headers:{'X-Workbench-Token':token}});
  if(!response.ok)throw new Error('Không tải được artifact');const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=({csv:'attributes.csv',msh:'mesh.msh',mesh:'mesh.json',cells:'cells.json',attributes:'attributes.json',gmsh_log:'gmsh.log'})[name];a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);
}
async function browse(path){
  $('picker-error').textContent='';try{const data=await api('/api/fs/list',{path});pickerPath=data.path;$('browse-path').value=pickerPath;$('breadcrumbs').textContent=pickerPath;$('entries').replaceChildren();
    const parts=pickerPath.replaceAll('\\','/').split('/');if(parts.length>1){const parent=parts.slice(0,-1).join('/');const up=document.createElement('button');up.textContent='↖ Thư mục cha';up.onclick=()=>browse(parent);$('entries').append(up);}
    const extensions=['raster','extract-raster','dem-raster','planned-raster','comparison-raster'].includes(pickerTarget)?['.tif','.tiff','.asc']:pickerTarget==='boundary-path'?['.shp']:pickerTarget==='gmsh-exe'?['.exe']:['.json'];
    for(const entry of data.entries){if(entry.kind!=='directory'&&(pickerTarget==='output'||!extensions.includes(entry.extension)))continue;
      const button=document.createElement('button'),name=document.createElement('span'),size=document.createElement('small');name.textContent=(entry.kind==='directory'?'▸ ':'')+entry.name;size.textContent=entry.kind==='directory'?'Thư mục':(entry.size/1024).toFixed(1)+' KB';button.append(name,size);
      button.onclick=async()=>{if(entry.kind==='directory'){await browse(entry.path);return;}acceptPickerPath(pickerTarget,entry.path);$('picker').close();try{if(pickerTarget==='raster'){exitTutorial();await inspectSource();}if(pickerTarget==='boundary-path')await inspectBoundary();if(pickerTarget==='planned-raster')await inspectPlanned();await refreshRecent();}catch(error){toast(error.message);}};$('entries').append(button);
    }
  }catch(error){$('picker-error').textContent=error.message;}
}
function restoreAcceptedMeshPresentation(){
  if(!meshSnapshot)return;
  jobId=meshSnapshot.jobId;resultPath=meshSnapshot.output||meshSnapshot.path.slice(0,-11);
  draw(meshSnapshot.cells,[],{crs:meshSnapshot.crs,units:'',display:meshSnapshot.display});showMeshQuality(meshSnapshot.qa.mesh_quality);
  $('exports').hidden=false;$('csv').hidden=true;$('json').hidden=true;$('msh').hidden=!meshSnapshot.hasMsh;$('mesh-json').hidden=false;
  $('job-status').className='info';$('job-status').textContent='Lưới đã lưu: '+resultPath;
  jobHeadline.textContent='Lưới đã sẵn sàng; chưa extract nguồn mới.';
}
function invalidateExtraction(){
  if(busy)return;
  revision++;preflightId=null;extractMode=false;
  restoreAcceptedMeshPresentation();
}

function acceptPickerPath(target,path){$(target).value=path;if(target==='comparison-raster'){clearComparison();return;}if(target==='extract-raster')invalidateExtraction();else invalidate();}

function clearComparison(){comparisonReport=null;$('comparison-result').hidden=true;$('comparison-download').hidden=true;}
async function showResearch(){
  $('comparison-panel').hidden=true;$('research-panel').hidden=false;$('tool-compare').setAttribute('aria-pressed','false');$('tool-research').setAttribute('aria-pressed','true');
  const box=$('research-result');box.textContent='Đang đọc bằng chứng SF-1…';
  try{const report=await api('/api/research/sf1');box.replaceChildren();const m=report.method||{};
    detailRows(box,[['Chỉ báo thích nghi',m.predictor||'Local relief'],['Thang cửa sổ',(m.windows_m||[]).join(' / ')+' m'],['Chuẩn hóa',(m.normalization_percentiles||[]).map(v=>'P'+v).join(' / ')],['Khoảng kích thước',(m.h_bounds_m||[]).join('–')+' m'],['Gamma',m.gamma],['Giới hạn gradation',m.gradation_ratio+' tại '+m.gradation_reference_distance_m+' m']]);
    const formula=document.createElement('p');formula.className='parameter-preview';formula.textContent=m.formula||'h = h_max − (h_max − h_min) × C^gamma';box.append(formula);
    const title=document.createElement('h3');title.textContent='Bằng chứng đã lưu · tam giác đều và thích nghi';box.append(title);
    const rows=[['Số ô / graph node',report.baseline?.node_count??report.baseline?.cell_count,report.selected?.node_count??report.selected?.cell_count]];
    const elevation=report.metrics?.high_elevation_rmse_m,slope=report.metrics?.high_slope_rmse_degrees;
    if(elevation)rows.push(['RMSE cao độ · vùng phức tạp (m)',concise(elevation.baseline),concise(elevation.selected)]);
    if(slope)rows.push(['RMSE slope · vùng phức tạp (°)',concise(slope.baseline),concise(slope.selected)]);
    comparisonTable(box,['Chỉ số','Tam giác đều','Tam giác thích nghi'],rows);
    for(const note of report.interpretation||[]){const p=document.createElement('p');p.textContent=note;box.append(p);}
    const provenance=document.createElement('details'),label=document.createElement('summary');label.textContent='Nguồn bằng chứng';provenance.append(label);detailRows(provenance,[['Run ID',report.run_id],['Tệp kết quả',report.source?.summary_path],['SHA256',report.source?.summary_sha256]]);box.append(provenance);
  }
  catch(error){box.textContent='Chưa đọc được bằng chứng: '+error.message;}
}
function openTools(){
  if(busy&&!comparisonJobId)return;
  if($('welcome').open)$('welcome').close();
  const count=meshSnapshot?.display?.cell_count??meshSnapshot?.cells.length;
  $('comparison-mesh').textContent=meshSnapshot?`Lưới mốc: ${count.toLocaleString('vi-VN')} ô. Mỗi ô tương ứng một graph node.`:'Tạo lưới ở bước 01–04, rồi mở lại công cụ so sánh.';$('comparison-mesh').title=meshSnapshot?.crs||'';
  $('comparison-run').disabled=busy||!meshSnapshot;
  if(!$('comparison-raster').value){const extracted=!!$('extract-raster').value;$('comparison-raster').value=$('extract-raster').value||$('raster').value;$('comparison-band').value=extracted?$('extract-band').value:$('band').value||'1';$('comparison-units').value=extracted?$('extract-units').value:$('units').value;$('comparison-semantics').value=extracted?$('extract-semantics').value:$('semantics').value||'intensive';}
  $('tools-dialog').showModal();
}
function comparisonConfig(){
  if(!meshSnapshot)throw Error('Tạo lưới trước khi so sánh.');
  const cellSize=number('comparison-size'),band=number('comparison-band'),path=$('comparison-raster').value.trim(),units=$('comparison-units').value.trim();
  if(cellSize<=0)throw Error('Cạnh ô vuông phải là số dương.');
  if(band<=0||!Number.isInteger(band))throw Error('Band phải là số nguyên dương.');
  if(!path)throw Error('Chọn raster đối chiếu.');if(!units)throw Error('Khai báo đơn vị của raster đối chiếu.');
  return {mesh_job_id:meshSnapshot.jobId,raster_path:path,band,semantics:$('comparison-semantics').value,value_units:units,cell_size_m:cellSize};
}
function comparisonTable(panel,headers,rows){const table=document.createElement('table');table.className='comparison-table';const head=document.createElement('thead'),header=document.createElement('tr'),body=document.createElement('tbody');for(const text of headers){const cell=document.createElement('th');cell.setAttribute('scope','col');cell.textContent=text;header.append(cell);}head.append(header);for(const row of rows){const tr=document.createElement('tr');row.forEach((value,index)=>{const td=document.createElement(index?'td':'th');if(!index)td.setAttribute('scope','row');td.textContent=typeof value==='number'?(Number.isInteger(value)?value.toLocaleString('vi-VN'):concise(value)):value??'—';tr.append(td);});body.append(tr);}table.append(head,body);panel.append(table);}
function renderComparison(report){
  comparisonReport=report;const panel=$('comparison-result');panel.replaceChildren();panel.hidden=false;
  const addHeading=text=>{const node=document.createElement('h3');node.textContent=text;panel.append(node);};
  addHeading('Ngân sách graph và miền đối chiếu');
  const structured=report.structured||{},unstructured=report.unstructured||{},countA=structured.node_count??structured.cell_count,countB=unstructured.node_count??unstructured.cell_count;
  const metrics=document.createElement('div');metrics.className='comparison-metrics';
  const reduction=report.node_reduction_percent;
  for(const [value,label] of [[countA,'Node · lưới vuông'],[countB,'Node · lưới hiện tại'],[Number.isFinite(reduction)?concise(reduction)+'%':'—','Mức giảm node · (vuông − hiện tại) / vuông']]){const box=document.createElement('div'),strong=document.createElement('strong'),span=document.createElement('span');box.className='comparison-metric';strong.textContent=typeof value==='number'?value.toLocaleString('vi-VN'):value??'—';span.textContent=label;box.append(strong,span);metrics.append(box);}panel.append(metrics);
  const delta=report.graph_node_delta;
  detailRows(panel,[['Miền chung hợp lệ (m²)',concise(report.common_support_area_m2)],['Đơn vị raster',report.value_units||report.provenance?.value_units||report.metrics?.structured?.units||'Theo nguồn đã khai báo'],['Graph node','Một ô → một node tại tâm ô'],['Chênh lệch node · hiện tại − vuông',Number.isInteger(delta)?(delta>0?'+':'')+delta.toLocaleString('vi-VN'):'—'],['Giá trị giảm node','Dương: ít node hơn; âm: nhiều node hơn']]);
  const boundaryNote=document.createElement('p');boundaryNote.className='field-help';boundaryNote.textContent='Lưới vuông tham chiếu'+(Number.isFinite(report.square_grid?.cell_size_m)?' · cạnh yêu cầu '+concise(report.square_grid.cell_size_m)+' m':'')+': ô biên được cắt theo miền tam giác, giữ các lỗ; các phần rời được tách thành ô riêng. Ngân sách ô phụ thuộc cạnh lưới vuông bạn chọn. Mỗi ô là một node ứng viên; số cạnh graph được đánh giá ở bước xây graph.';panel.append(boundaryNote);
  addHeading('Trích thuộc tính và sai số biểu diễn');
  const method=document.createElement('p');method.textContent=report.semantics==='categorical'?'Tỷ lệ diện tích từng lớp được trích cho mỗi ô. Đối chiếu thành phần lớp trên cùng miền hợp lệ.':report.semantics==='extensive'?'Tổng trên pixel được phân bổ theo diện tích giao. Đối chiếu tổng lượng và phần dư bảo toàn trên cùng miền hợp lệ.':'Giá trị mỗi ô là trung bình theo diện tích giao. RMSE và MAE cộng sai lệch ô − pixel trên từng phần giao, có trọng số diện tích.';panel.append(method);
  const a=report.metrics?.structured||{},b=report.metrics?.unstructured||{},rows=[];
  const metric=(label,key)=>rows.push([label,concise(a[key]),concise(b[key])]);
  if(report.semantics==='categorical'){
    const percentage=value=>Number.isFinite(value)?concise(value*100)+'%':'—';
    rows.push(['Khớp lớp phổ biến · trọng số diện tích',percentage(a.area_weighted_modal_agreement),percentage(b.area_weighted_modal_agreement)],['Sai khác lớp phổ biến · trọng số diện tích',percentage(a.area_weighted_modal_disagreement),percentage(b.area_weighted_modal_disagreement)]);
    metric('Phần dư bảo toàn tỷ lệ lớp · lớn nhất','max_fraction_error');
    const classes=new Set([...Object.keys(a.source_class_fractions||{}),...Object.keys(b.source_class_fractions||{})]);
    const limit=100;let shown=0;
    for(const name of classes){if(shown===limit)break;rows.push(['Tỷ lệ lớp '+name+' · nguồn '+concise(a.source_class_fractions?.[name]??b.source_class_fractions?.[name]),concise(a.remapped_class_fractions?.[name]),concise(b.remapped_class_fractions?.[name])]);shown++;}
    const explanation=document.createElement('p');explanation.textContent='Mỗi ô dùng lớp có diện tích lớn nhất để đối chiếu vị trí với raster nguồn. Chỉ số khớp lớp và phần dư bảo toàn tỷ lệ lớp được báo riêng.';panel.append(explanation);
    const count=document.createElement('p');count.className='field-help';count.textContent='Hiển thị '+shown.toLocaleString('vi-VN')+' / '+classes.size.toLocaleString('vi-VN')+' lớp. Tải JSON để xem toàn bộ tỷ lệ lớp.';panel.append(count);
  }else if(report.semantics==='extensive'){
    metric('Tổng nguồn · cùng miền hợp lệ','source_total');metric('Tổng sau remap','remapped_total');metric('Phần dư bảo toàn · sau − nguồn','conservation_residual');
  }else{const units=a.units||b.units||report.value_units||'';metric('RMSE '+units,'rmse');metric('MAE '+units,'mae');metric('Bias '+units,'bias');}
  comparisonTable(panel,['Chỉ số','Lưới vuông','Lưới hiện tại'],rows);
  // Keep class maps in the downloadable report; avoid duplicating them in the DOM.
  const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre'),detailText=JSON.stringify({semantics:report.semantics,method:report.method,error_definition:report.error_definition,square_grid:report.square_grid,workload:report.workload,provenance:report.provenance},null,2);summary.textContent='Phương pháp và nguồn đối chiếu';pre.textContent=detailText.slice(0,20000)+(detailText.length>20000?'\n… Xem đầy đủ trong JSON tải xuống.':'');details.append(summary,pre);panel.append(details);
  $('comparison-download').hidden=false;
}
async function startComparison(){
  if(busy)return;let payload;try{payload=comparisonConfig();}catch(error){$('comparison-status').textContent=error.message;return;}
  clearComparison();const request=++comparisonRequest;comparisonRequestKey=typeof crypto!=='undefined'&&crypto.randomUUID?crypto.randomUUID():'comparison-'+Date.now()+'-'+Math.random().toString(36).slice(2);comparisonReadFailures=0;payload.request_id=comparisonRequestKey;setBusy(true);$('tools-close').disabled=true;for(const el of $('comparison-panel').querySelectorAll('input,select,button'))el.disabled=true;$('comparison-stop').hidden=false;$('comparison-stop').disabled=true;$('comparison-status').textContent='Đang chuẩn bị hai lưới và miền raster chung…';
  try{const response=await api('/api/comparison',payload);if(request!==comparisonRequest)return;if(typeof response.job_id!=='string'||!response.job_id)throw Error('Chưa nhận được định danh lượt so sánh.');comparisonJobId=response.job_id;$('comparison-stop').disabled=false;await pollComparison(request);}
  catch(error){if(request!==comparisonRequest)return;$('comparison-status').textContent='Đang xác nhận lượt so sánh đã khởi chạy…';await recoverComparison(request,error.message);}
}
async function recoverComparison(request,reason=''){
  if(request!==comparisonRequest)return;
  try{let active;if(comparisonRequestKey){active=await api('/api/comparison/status?request_id='+encodeURIComponent(comparisonRequestKey),undefined,8000);if(active.job_id&&active.operation==='comparison'){if(request!==comparisonRequest)return;comparisonJobId=active.job_id;$('comparison-stop').disabled=false;await pollComparison(request);return;}}
    active=await api('/api/active-job',undefined,8000);if(request!==comparisonRequest)return;
    if(active.job_id&&active.operation==='comparison'&&(!comparisonRequestKey||active.request_id===comparisonRequestKey)){comparisonJobId=active.job_id;$('comparison-stop').disabled=false;await pollComparison(request);}
    else if(!active.job_id){$('comparison-status').textContent='Chưa có lượt so sánh đang chạy. '+reason;finishComparison();}
    else{$('comparison-status').textContent='Đang chờ tác vụ hiện tại kết thúc để xác nhận trạng thái so sánh…';setTimeout(()=>recoverComparison(request,reason),2000);}
  }catch(error){if(request!==comparisonRequest)return;if([400,401,403,404,410].includes(error.status)){$('comparison-status').textContent='Chưa khôi phục được lượt so sánh: '+error.message+'. Lưới mốc được giữ nguyên.';finishComparison();return;}$('comparison-status').textContent='Đang khôi phục kết nối; cấu hình so sánh được giữ nguyên…';setTimeout(()=>recoverComparison(request,reason),2000);}
}
function finishComparison(){comparisonJobId=null;$('comparison-stop').hidden=true;$('tools-close').disabled=false;for(const el of $('comparison-panel').querySelectorAll('input,select,button'))el.disabled=false;setBusy(false);$('comparison-run').disabled=!meshSnapshot;}
async function pollComparison(request){
  if(request!==comparisonRequest||!comparisonJobId)return;
  let terminal=false;
  try{const job=await api('/api/job?id='+encodeURIComponent(comparisonJobId),undefined,8000);if(request!==comparisonRequest)return;
    const monitor=job.monitor||{};$('comparison-status').textContent=(job.status==='stopping'?'Đang dừng so sánh…':stageNames[monitor.phase]||stageNames[job.stage]||'Đang so sánh')+' · '+duration(monitor.elapsed_seconds)+(Number.isFinite(monitor.phase_percent)?' · '+concise(monitor.phase_percent)+'%':'');
    if(['queued','running','stopping'].includes(job.status)){comparisonReadFailures=0;$('comparison-stop').disabled=job.status==='stopping';setTimeout(()=>pollComparison(request),750);return;}
    terminal=true;if(job.status==='pass'){const report=await api('/api/artifact?id='+encodeURIComponent(comparisonJobId)+'&name=comparison',undefined,8000);if(request!==comparisonRequest)return;renderComparison(report);$('comparison-status').textContent='✓ Đã so sánh hai lưới trên cùng nguồn và miền hợp lệ.';}
    else $('comparison-status').textContent=job.status==='cancelled'?'Đã dừng so sánh. Lưới mốc được giữ nguyên.':'So sánh chưa hoàn thành: '+(job.result?.error||job.stage);
    finishComparison();
  }catch(error){if(request!==comparisonRequest)return;comparisonReadFailures++;
    if([400,401,403,404,410].includes(error.status)||(terminal&&comparisonReadFailures>=3)){$('comparison-status').textContent=(terminal?'Tác vụ đã kết thúc; chưa tải được báo cáo. ':'Chưa đọc được lượt so sánh. ')+error.message+' Lưới mốc được giữ nguyên.';finishComparison();return;}
    $('comparison-status').textContent=terminal?'Đang thử tải lại báo cáo đã hoàn thành…':'Đang thử đọc lại tiến trình so sánh…';setTimeout(()=>pollComparison(request),2000);
  }
}
async function stopComparison(){if(!comparisonJobId)return;$('comparison-stop').disabled=true;try{await api('/api/job/stop',{job_id:comparisonJobId},8000);$('comparison-status').textContent='Đang dừng so sánh…';}catch(error){$('comparison-status').textContent='Chưa xác nhận yêu cầu dừng: '+error.message;$('comparison-stop').disabled=false;}}
function downloadComparison(){if(!comparisonReport)return;const blob=new Blob([JSON.stringify(comparisonReport,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='mesh-comparison.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),5000);}

$('form').addEventListener('submit',e=>e.preventDefault());$('form').addEventListener('input',e=>{invalidate();if(previewKey&&previewKey!==previewIdentity())clearInputPreview();if(['raster','band','crs'].includes(e.target.id)){sourceRequest++;source=null;helpers();}if(['planned-raster','planned-band','crs'].includes(e.target.id)){plannedRequest++;plannedSource=null;}parameterPreview();renderResolution();});$('form').addEventListener('change',()=>{invalidate();mode();helpers();});
$('tools-open').onclick=openTools;$('tools-close').onclick=()=>{if(!comparisonJobId&&!busy)$('tools-dialog').close();};$('tools-dialog').addEventListener('cancel',event=>{if(busy)event.preventDefault();});$('tool-compare').onclick=()=>{$('comparison-panel').hidden=false;$('research-panel').hidden=true;$('tool-compare').setAttribute('aria-pressed','true');$('tool-research').setAttribute('aria-pressed','false');$('comparison-size').focus();};$('tool-research').onclick=()=>{if(!busy)showResearch();};
$('tool-variation').onclick=()=>{if(busy)return;$('tools-dialog').close();$('mode').value='gmsh_generated';$('gmsh-sizing').value='sf1_relief';invalidate();mode();stage(2);$('dem-raster').focus();};
$('comparison-run').onclick=startComparison;$('comparison-stop').onclick=stopComparison;$('comparison-download').onclick=downloadComparison;
for(const id of ['comparison-raster','comparison-band','comparison-size','comparison-semantics','comparison-units'])$(id).addEventListener('input',clearComparison);
$('refresh-input-preview').onclick=loadInputPreview;
$('result-mesh-tab').onclick=()=>showResultTab('mesh');
$('result-extract-tab').onclick=()=>showResultTab('extract');
for(const button of document.querySelectorAll('[data-pick]'))button.onclick=async()=>{
  if(busy||!bootstrap)return;
  pickerTarget=button.dataset.pick;
  if(bootstrap.native_picker){
    button.disabled=true;
    try{
      const target=pickerTarget,kind=['extract-raster','dem-raster','planned-raster','comparison-raster'].includes(target)?'raster':target==='boundary-path'?'boundary':target.startsWith('gmsh-')?'gmsh':target;
      const selected=await api('/api/picker',{kind});
      if(selected.cancelled)return;
      acceptPickerPath(target,selected.path);
      if(kind==='gmsh'){gmshRequest++;gmshVerified=null;gmshFeedback('Đã chọn gmsh.exe. Chưa kiểm tra phiên bản. Bấm Kiểm tra & lưu để xác nhận.');}
      if(target==='raster'){exitTutorial();await inspectSource();}
      if(target==='boundary-path')await inspectBoundary();if(target==='planned-raster')await inspectPlanned();
      await refreshRecent();
    }catch(error){toast(error.message);}finally{button.disabled=busy;}
    return;
  }
  if(pickerTarget==='gmsh-setup-path'){toast('Bản trình duyệt: hãy dán đường dẫn tuyệt đối tới gmsh.exe. Hộp chọn Windows có trong bản desktop.');return;}
  $('choose-directory').hidden=pickerTarget!=='output';const allowed=pickerTarget==='output'?bootstrap.output_roots:roots;$('roots').replaceChildren();for(const root of allowed){const choice=document.createElement('button');choice.textContent=root;choice.onclick=()=>browse(root);$('roots').append(choice);}$('picker').showModal();browse(allowed[0]);
};
$('close-picker').onclick=()=>$('picker').close();$('browse-go').onclick=()=>browse($('browse-path').value);$('choose-directory').onclick=()=>{$('output').value=pickerPath;invalidate();$('picker').close();};
$('inspect-planned').onclick=()=>inspectPlanned().catch(error=>toast(error.message));$('apply-resolution').onclick=applyResolution;
$('inspect-source').onclick=()=>inspectSource().catch(error=>toast(error.message));$('use-crs').onclick=()=>{if(source?.projected_metre){$('crs').value=source.crs;invalidate();helpers();}};
$('inspect-boundary').onclick=()=>inspectBoundary().catch(error=>toast(error.message));
$('use-bounds').onclick=()=>{if(sameCRS()){['west','south','east','north'].forEach((id,i)=>$(id).value=source.bounds[i]);invalidate();}};
$('validate').onclick=validate;$('run').onclick=startRun;$('csv').onclick=()=>download('csv').catch(error=>toast(error.message));$('json').onclick=()=>download('attributes').catch(error=>toast(error.message));$('copy-output').onclick=()=>navigator.clipboard.writeText(resultPath).then(()=>toast('Đã sao chép đường dẫn')).catch(()=>toast(resultPath));
$('cancel-run').onclick=requestStop;$('job-log-download').onclick=()=>download('gmsh_log').catch(error=>toast(error.message));
$('log-follow').onchange=()=>{if($('log-follow').checked)$('job-log').scrollTop=$('job-log').scrollHeight;};
$('msh').onclick=()=>download('msh').catch(error=>toast(error.message));$('mesh-json').onclick=()=>download(meshSnapshot?'cells':'mesh').catch(error=>toast(error.message));
function gmshFeedback(message,ok=false){for(const id of ['gmsh-status','gmsh-setup-status']){$(id).textContent=message;$(id).className=ok?'info success':'info';}}
async function checkGmsh(path,button,status){
  if(busy)return;
  if(!path.trim()){gmshFeedback('Chọn gmsh.exe hoặc dán đường dẫn tới tệp trên máy.');return;}
  const request=++gmshRequest;button.disabled=true;gmshFeedback('Đang kiểm tra phiên bản Gmsh…');
  try{const data=await api('/api/gmsh',{path:path.trim()});if(request!==gmshRequest)return;gmshVerified=data;bootstrap.gmsh_saved=data;$('gmsh-exe').value=data.executable;$('gmsh-setup-path').value=data.executable;gmshFeedback(`✓ Gmsh phiên bản ${data.version} — Kiểm tra thành công. Sẵn sàng tạo lưới. Đã lưu đường dẫn.`,true);invalidate();}
  catch(error){if(request!==gmshRequest)return;gmshVerified=null;gmshFeedback(`Không xác nhận được Gmsh — ${error.message}. Chưa sẵn sàng với đường dẫn này.`);}
  finally{button.disabled=busy;}
}
for(const id of ['gmsh-exe','gmsh-setup-path'])$(id).addEventListener('input',()=>{gmshRequest++;gmshVerified=null;gmshFeedback('Đường dẫn đã thay đổi. Hãy kiểm tra lại phiên bản trước khi dùng.');});
$('check-gmsh').onclick=()=>checkGmsh($('gmsh-exe').value,$('check-gmsh'),$('gmsh-status'));
function savedGmshFeedback(){const saved=bootstrap?.gmsh_saved;if(gmshVerified&&gmshVerified.executable===$('gmsh-exe').value){gmshFeedback(`✓ Gmsh phiên bản ${gmshVerified.version} — Kiểm tra thành công. Sẵn sàng tạo lưới. Đã lưu đường dẫn.`,true);}else if(saved?.version&&saved.executable===$('gmsh-exe').value){gmshFeedback(`Gmsh ${saved.version} — đã kiểm tra trước đây; chưa kiểm tra lại trong phiên này. Bấm kiểm tra để xác nhận lại.`);}else gmshFeedback('Chưa kiểm tra Gmsh. Chọn gmsh.exe hoặc dán đường dẫn rồi bấm kiểm tra.');}
function openGmshSetup(){if(busy)return;if($('welcome').open)$('welcome').close();$('gmsh-setup-path').value=$('gmsh-exe').value;savedGmshFeedback();$('gmsh-setup').showModal();}
$('gmsh-setup-open').onclick=openGmshSetup;$('gmsh-setup-close').onclick=()=>$('gmsh-setup').close();
$('gmsh-setup-save').onclick=()=>checkGmsh($('gmsh-setup-path').value,$('gmsh-setup-save'),$('gmsh-setup-status'));
const welcomeGmsh=document.createElement('button');welcomeGmsh.type='button';welcomeGmsh.id='welcome-gmsh-setup';welcomeGmsh.textContent='⚙ Thiết lập Gmsh trước';welcomeGmsh.onclick=openGmshSetup;$('welcome').append(welcomeGmsh);
$('show-edges').onchange=updateMapControls;$('show-nodes').onchange=updateMapControls;$('fit').onclick=()=>{stopMapDrag();if(fitBox)$('map').setAttribute('viewBox',fitBox.join(' '));};
$('map').addEventListener('keydown',moveMapFocus);
$('map').addEventListener('pointerdown',beginMapDrag);
$('map').addEventListener('pointermove',moveMapDrag);
for(const name of ['pointerup','pointercancel','lostpointercapture'])$('map').addEventListener(name,endMapDrag);
// A tiny motion can leave the SVG before capture starts: always release state.
window.addEventListener('pointerup',endMapDrag);window.addEventListener('pointercancel',endMapDrag);
$('map').addEventListener('click',mapClick,true);
$('map').addEventListener('wheel',wheelMap,{passive:false});
function findCell(){const cell=geometry.find(c=>c.cell_id===$('cell-search').value.trim());if(cell)selectCell(cell);else toast('Không tìm thấy ID ô trong lưới hiện tại.');}
$('cell-find').onclick=findCell;$('cell-search').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();findCell();}});
$('zoom-in').onclick=()=>zoom(1/1.4);$('zoom-out').onclick=()=>zoom(1.4);
for(const button of document.querySelectorAll('button[data-stage],button[data-go]'))button.onclick=()=>stage(Number(button.dataset.stage||button.dataset.go));
for(const button of document.querySelectorAll('[data-mode]'))button.onclick=()=>{if(busy)return;$('mode').value=button.dataset.mode;invalidate();mode();};
$('source-next').onclick=async()=>{try{
  if($('gmsh-domain').value==='shapefile'){const roi=await inspectBoundary();if(!roi)return;mode();stage(2);return;}
  if($('gmsh-domain').value==='rectangle'&&!$('raster').value.trim()){stage(2);return;}
  const meta=await inspectSource();if(!meta)return;
  if(!source.crs)throw new Error('Raster ROI thiếu CRS. Với ASC, đặt .prj cùng tên cạnh .asc; không tự gán CRS nguồn.');
  if(source.display_footprint){['west','south','east','north'].forEach((id,i)=>$(id).value=source.display_footprint.bounds[i]);}
  stage(2);
}catch(error){toast(error.message);}};
function showMeshQuality(q){
  $('mesh-quality').hidden=false;$('mesh-quality').replaceChildren();
  const heading=document.createElement('h3');heading.textContent='Chất lượng lưới';$('mesh-quality').append(heading);
  if(q.sizing_summary){
    const method=document.createElement('h4');method.textContent='Cấu hình yêu cầu và phương pháp';$('mesh-quality').append(method);detailRows($('mesh-quality'),requestRows(q.sizing_summary));
    const f=q.sizing_summary.size_field;
    if(f?.target_size_field_min_m!==undefined){
      const fieldRows=[['Trường mục tiêu thực sinh · min / max',[f.target_size_field_min_m,f.target_size_field_max_m].map(concise).join(' / ')+' m']];
      if(Array.isArray(f.control_spacing_m))fieldRows.push(['Bước lưới điều khiển',f.control_spacing_m.map(concise).join(' × ')+' m']);
      if(f.method==='gnn_sf1_two_window_relief'){
        for(const width of [150,450]){const limits=f.normalization?.['relief'+width];if(Array.isArray(limits))fieldRows.push(['P25 / P95 relief '+width+' m',limits.map(concise).join(' / ')+' m']);}
        for(const [label,gradation] of [['Gradation trên DEM',f.native_gradation],['Gradation lưới điều khiển',f.control_gradation]])if(gradation){fieldRows.push([label,gradation.pass===true?'Đạt':gradation.pass===false?'Chưa đạt':'Chưa có kết quả'],[label+' · log(h)/m đo được / giới hạn',concise(gradation.measured_bound_per_m)+' / '+concise(gradation.allowed_log_gradient_per_m)]);}
      }else{
        fieldRows.push(['P25 / P95 relief · ngưỡng từ DEM',[f.p25,f.p95].map(concise).join(' / ')]);
        if(Array.isArray(f.effective_half_window_m))fieldRows.push(['Nửa cửa sổ relief thực',f.effective_half_window_m.map(concise).join(' × ')+' m']);
      }
      detailRows($('mesh-quality'),fieldRows);
    }
  }
  const observed=document.createElement('h4');observed.textContent='Chỉ số hình học của lưới';$('mesh-quality').append(observed);
  const rows=[['Ô / đỉnh XY duy nhất',q.cell_count+' / '+q.vertex_count],['Diện tích nhỏ nhất / lớn nhất (m²)',q.area_min_m2.toFixed(3)+' / '+q.area_max_m2.toFixed(3)],['Cạnh nhỏ nhất / lớn nhất (m)',q.edge_min_m.toFixed(3)+' / '+q.edge_max_m.toFixed(3)],['Diện tích chồng lấn (m²)',q.overlap_area_m2.toFixed(6)]];
  if(q.triangle_min_angle_deg!==null)rows.push(['Góc tam giác nhỏ nhất (°)',q.triangle_min_angle_deg.toFixed(3)],['Aspect lớn nhất · cạnh dài nhất / đường cao',q.triangle_max_aspect.toFixed(3)]);
  if(q.domain_symmetric_difference_m2!==undefined)rows.push(['Sai lệch diện tích với ROI (m²)',q.domain_symmetric_difference_m2.toFixed(6)]);
  if(q.domain_coverage)rows.push(['Phủ ROI',q.domain_coverage],['Số vùng / lỗ',q.component_count+' / '+q.hole_count],['Diện tích ROI (m²)',q.domain_area_m2.toFixed(3)]);
  detailRows($('mesh-quality'),rows);const technical=document.createElement('details'),label=document.createElement('summary');label.textContent='Định danh hình học';technical.append(label);detailRows(technical,[['SHA256',q.geometry_digest||'—']]);$('mesh-quality').append(technical);
}
$('extract-run').onclick=async()=>{if(busy||!meshSnapshot)return;extractMode=true;await validate();if(preflightId)await startRun();};
for(const id of ['extract-raster','extract-band','extract-semantics','extract-units'])$(id).addEventListener('input',invalidateExtraction);

$('try-sample').onclick=()=>useSample().catch(error=>toast(error.message));
$('tutorial-help').onclick=()=>{if(busy)return;if(tutorialActive){coach.start();refreshCoach();}else $('welcome').showModal();};
$('welcome-start').onclick=()=>{$('welcome').close();$('tutorial-finish').hidden=false;useSample().catch(error=>toast(error.message));};
$('welcome-skip').onclick=async()=>{try{await finishTutorial();$('welcome').close();}catch(error){toast(error.message);}};
$('tutorial-finish').onclick=()=>finishTutorial().then(()=>toast('Bạn đã hoàn thành! Chọn Bắt đầu lượt khác để dùng dữ liệu của mình.')).catch(error=>toast(error.message));
$('start-another').onclick=startAnother;
api('/api/bootstrap').then(async data=>{bootstrap=data;roots=data.roots;$('gmsh-exe').value=data.gmsh_path||'';document.querySelector('[data-pick="gmsh-exe"]').textContent='Chọn gmsh.exe…';savedGmshFeedback();$('output').value=data.output_root;for(const root of roots){const button=document.createElement('button');button.textContent=root;button.onclick=()=>browse(root);$('roots').append(button);}mode();const pending=readRunRequest();if(pending){runRequest=pending;setBusy(true);$('job-status').className='info';$('job-status').textContent='Đang khôi phục lưới và lượt chạy…';$('job-monitor').hidden=false;if(pending.operation==='extract'){extractMode=true;try{if(!await restoreSessionMesh())throw Error('Không tìm thấy lưới đã chấp nhận để khôi phục extract.');}catch(error){if(isDefinitivelyStaleAcceptedMesh(error))clearAcceptedMesh();const warning='Không khôi phục được lưới nền: '+error.message+' Lượt chạy vẫn được theo dõi và có thể dừng.';$('job-status').className='info error';$('job-status').textContent=warning;toast(warning);}}recoverStart();}else{try{await restoreSessionMesh();}catch(error){if(isDefinitivelyStaleAcceptedMesh(error))clearAcceptedMesh();toast(error.message);}if(!data.tutorial_seen&&!meshSnapshot)$('welcome').showModal();}}).catch(error=>{toast(token?error.message:'Thiếu token phiên. Hãy mở URL đầy đủ được in khi chạy UI.');$('validate').disabled=true;});
coach.setup();mode();stage(1,false);document.querySelector('button[data-stage="4"]').disabled=true;refreshRecent().catch(error=>toast(error.message));
