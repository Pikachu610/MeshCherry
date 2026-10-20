'use strict';
const $=id=>document.getElementById(id);
const extractResultPanel=$('extract-result-panel');extractResultPanel?.removeAttribute?.('aria-labelledby');extractResultPanel?.setAttribute?.('role','region');extractResultPanel?.setAttribute?.('aria-label','Extract thuộc tính');
const comparisonPanel=$('comparison-panel'),comparisonWorkspaceContent=$('comparison-workspace-content');
if(comparisonPanel&&comparisonWorkspaceContent)comparisonWorkspaceContent.append(comparisonPanel);
if($('comparison-result')&&!$('comparison-visualization')){const visualization=document.createElement('div');visualization.id='comparison-visualization';$('comparison-result').before(visualization);}
// Keep the main result surface quiet; detailed diagnostics remain available.
const jobDetails=document.createElement('details'),jobDetailsTitle=document.createElement('summary'),jobHeadline=document.createElement('p');
jobDetailsTitle.textContent='Chi tiết kỹ thuật';jobHeadline.id='job-headline';jobHeadline.setAttribute('role','status');jobHeadline.textContent='Chưa có lượt chạy.';
$('job-status').before(jobHeadline,jobDetails);jobDetails.append(jobDetailsTitle,$('job-status'));
$('monitor-headline').append(jobHeadline);$('monitor-details').append(jobDetails);$('monitor-progress-slot').append($('progress'));
const fragment=new URLSearchParams(location.hash.slice(1));
const token=fragment.get('token')||sessionStorage.getItem('mesh-cherry-token')||'';
if(fragment.has('token')){sessionStorage.setItem('mesh-cherry-token',token);history.replaceState(null,'',location.pathname);}
let roots=[],source=null,preflightId=null,revision=0,busy=false,jobId=null,resultPath='',geometry=[],attrs=new Map(),fitBox=null,mapUnits='',mapCRS='',mapOriginX=0,mapOriginY=0;
const RUN_REQUEST_STORAGE_KEY='mesh-cherry-run-request';
const ACCEPTED_MESH_STORAGE_KEY='mesh-cherry-accepted-mesh';
const BATCH_RECOVERY_STORAGE_KEY='mesh-cherry-batch-recovery';
let runRequest=null;
let pickerTarget='raster',pickerPath='';
let gmshVerified=null,meshSnapshot=null,extractMode=false,extractChecking=false;
let plannedSource=null,plannedRequest=0;
let sourceRequest=0,boundaryRequest=0,gmshRequest=0,toastTimer=null,selectedCellId=null;
let mapDrag=null,mapSuppressClick=false;
let batchedMap=null;
let previewRequest=0,previewKey='',previewURL=null,savedMap=null,resultTab='mesh';
let demPreviewRequest=0,demPreviewKey='',demPreviewURL=null;
let jobLogCursor=0,monitorJobId=null,cancelPending=false;
let comparisonReport=null,comparisonJobId=null,comparisonRequest=0,comparisonRequestKey='',comparisonEvaluationKey='',comparisonReadFailures=0,comparisonOwnsBusy=false;
let comparisonInventory=null,comparisonInventoryKey='',comparisonCountJobId=null,comparisonCountRequest=0,comparisonCountRequestKey='',comparisonCountKey='',comparisonCountStopRequested=false,comparisonCountReadFailures=0,comparisonStopRequested=false,comparisonProgressSamples=[];
let comparisonSourceMeta=null,comparisonSourceMetaKey='',comparisonSourceRequest=0;
let terminalArtifactFailureJobId=null,terminalArtifactFailureCount=0,terminalArtifactIncompleteJobId=null;
let terminalArtifactRetryButton=null;
const MAX_TERMINAL_ARTIFACT_ATTEMPTS=3;
let attributeTable=null,batchTable=null,batchTableBinding=null,batchViewGeneration=0,batchQueue=[],batchJobId=null,batchCompletedJobId=null,batchMeshId=null,batchPollTimer=null,batchRequestId=null,batchStartUncertain=false,batchRecoveryBlocked=false,downloadPanel=null,downloadUI=null;
let extractionDraft=null,extractionDraftMode=null,activeBatchFeatures=null,activeBatchPlanHash=null,activeBatchDraft=null;
let preserveAcceptedDuringRun=false,gmshAdvancedEnabled=false,batchOwnsBusy=false;
let qualityBridge=null,qualityPanel=null,qualityReport=null,qualityOverlayMetric=null,qualityMeshId=null,qualityBusy=false,qualityDisabledBefore=[];
function ensureDownloadUI(){
  if(downloadUI)return;
  downloadPanel=document.createElement('section');downloadPanel.id='download-panel';downloadPanel.className='card';downloadPanel.hidden=true;
  $('exports').after(downloadPanel);downloadUI=MeshCherryDownloads.mount(downloadPanel,{title:'Tiến độ xuất tệp',externalOnly:true});
}
async function nativeSave(request){
  const body=request.kind==='artifact'?{kind:'artifact',job_id:request.payload.job_id,name:request.payload.name}:{kind:request.kind,request:request.payload};
  const started=await api('/api/download/start',body,10000);
  const id=started.download_id;if(!id)throw Error('Máy chủ chưa cấp mã lượt lưu.');
  const cancel=()=>api('/api/download/cancel',{id},5000).catch(()=>{});
  const abort=()=>{cancel();};request.signal?.addEventListener('abort',abort,{once:true});if(request.signal?.aborted)abort();
  try{
    while(true){let status;try{status=await api(`/api/download/status?id=${encodeURIComponent(id)}`,undefined,5000);}catch(error){if(request.signal?.aborted)return {state:'error',error:'Chưa xác nhận trạng thái lưu sau khi yêu cầu hủy. Hãy kiểm tra thư mục đích trước khi thử lại.'};throw error;}
      request.onProgress?.(Number(status.bytes_written)||0,status.total_bytes==null?null:Number(status.total_bytes));
      if(status.state==='saved')return {state:'saved',path:status.path||null};
      if(status.state==='cancelled')return {state:'cancelled'};
      if(status.state==='error')return {state:'error',error:status.error||'Không lưu được tệp.'};
      await new Promise(resolve=>setTimeout(resolve,250));
    }
  }finally{request.signal?.removeEventListener('abort',abort);}
}
function managedDownload({url,filename,method='GET',body,kind,payload,fetchOptions={}}){
  ensureDownloadUI();downloadPanel.hidden=false;
  downloadPanel.scrollIntoView?.({block:'nearest',behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
  return downloadUI.start({url,filename,method,body,fetchOptions:{...fetchOptions,headers:{'X-Workbench-Token':token,...(fetchOptions.headers||{})}},
    nativeSave:bootstrap?.native_save?request=>nativeSave({...request,kind,payload}):undefined});
}
function destroyTable(which){if(which==='single'){attributeTable?.destroy();attributeTable=null;$('attribute-preview').hidden=true;}else{batchTable?.destroy();batchTable=null;batchTableBinding=null;$('batch-table').replaceChildren();const retry=$('batch-preview-retry');if(typeof retry?.remove==='function')retry.remove();}}
function clearExtractionDraft(){extractionDraft?.destroy();extractionDraft=null;extractionDraftMode=null;activeBatchPlanHash=null;if($('extraction-draft-panel'))$('extraction-draft-panel').hidden=true;}
function extractionFeatures(mode){return mode==='single'?[{name:$('extract-name')?.value.trim()||$('extract-raster').value.trim().split(/[\\/]/).pop().replace(/\.[^.]+$/,''),path:$('extract-raster').value.trim(),band:Number($('extract-band').value),semantics:$('extract-semantics').value,value_units:$('extract-units').value.trim()}]:batchQueue.map(({name,path,band,semantics,value_units})=>({name,path,band,semantics,value_units}));}
async function previewExtractionDraft(mode){
  if(!meshSnapshot||!window.MeshCherryExtractionDraft)throw Error('Không tải được bộ rà soát bảng trích. Hãy tải lại giao diện.');
  const features=extractionFeatures(mode);if(mode==='single'&&(!features[0].path||!features[0].value_units||!Number.isSafeInteger(features[0].band)||features[0].band<1))throw Error('Kiểm tra raster, band và đơn vị trước khi rà soát.');
  if(mode==='batch'&&!validBatchQueue())throw Error(batchQueueIssue());
  clearExtractionDraft();$('extraction-draft-panel').hidden=false;$('batch-panel').hidden=mode!=='batch';$('batch-queue-controls').hidden=mode!=='batch';
  const mesh=meshSnapshot,sourceRevision=revision,featureKey=JSON.stringify(features),route=async(draft,offset=0,limit=50,visible_columns)=>{if(meshSnapshot!==mesh||revision!==sourceRevision||JSON.stringify(extractionFeatures(mode))!==featureKey)throw Error('Raster hoặc lưới đã thay đổi; hãy mở lại bản xem trước.');const body={mesh_job_id:mesh.jobId,features,offset,limit};if(Array.isArray(visible_columns)&&visible_columns.length)body.visible_columns=visible_columns;if(draft!==undefined&&draft!==null)body.draft=draft;const response=await api('/api/extraction-draft/preview',body);if(meshSnapshot!==mesh||revision!==sourceRevision||JSON.stringify(extractionFeatures(mode))!==featureKey)throw Error('Raster hoặc lưới đã thay đổi; hãy mở lại bản xem trước.');return response;};
  const adapt=async args=>{const response=await route(args.draft,args.offset??(args.page-1)*args.pageSize,args.limit??args.pageSize,args.visible_columns?.length?args.visible_columns:undefined);activeBatchPlanHash=response.plan_hash||activeBatchPlanHash;return{...response,totalRows:response.accepted_cells??response.total_cells??response.total_rows??0,page:Math.floor(Number(response.offset||0)/(Number(response.limit)||args.pageSize))+1};};
  extractionDraftMode=mode;
  extractionDraft=window.MeshCherryExtractionDraft.mount($('extraction-draft'),{meshJobId:mesh.jobId,geometryDigest:mesh.digest,systemColumns:[{key:'kind',name:'Kiểu ô'},{key:'area_m2',name:'Diện tích (m²)'},{key:'centroid_x',name:'Tâm X'},{key:'centroid_y',name:'Tâm Y'}],pageSize:50,
    previewPage:adapt,onChange:async draft=>{const response=await route(draft,0,50,[]);activeBatchPlanHash=response.plan_hash;return response;},
    onConfirm:async draft=>{const confirmed=await route(draft,0,1,[]);return startBatch({features,draft:confirmed.draft,planHash:confirmed.plan_hash,single:mode==='single',mesh});},
    onStop:cancelBatch});
  await extractionDraft.ready;
  $('extract-status').className='info';$('extract-status').textContent='Bản xem trước chưa tính giá trị raster. Rà soát các cột và dòng, sau đó xác nhận để bắt đầu trích.';
  if(mode==='batch'){$('batch-panel-title').textContent='Trích nhiều raster';}
}
function showRemeshDraftButton(){
  let button=$('remesh-draft');if(!button){button=document.createElement('button');button.id='remesh-draft';button.type='button';$('mesh-quality').before(button);}
  button.textContent='Chỉnh thông số / Chia lưới lại';button.setAttribute('aria-label','Chỉnh thông số / Chia lưới lại; kết quả hiện tại được giữ cho đến khi lượt chạy mới hoàn tất');button.onclick=restoreRemeshDraft;
  button.hidden=!meshSnapshot;
  $('menu-quality-open').disabled=busy||!meshSnapshot;
  let discard=$('remesh-draft-discard');if(!discard){discard=document.createElement('button');discard.id='remesh-draft-discard';discard.type='button';$('remesh-draft').after(discard);}
  discard.textContent='Giữ lưới hiện tại';discard.onclick=discardRemeshDraft;
  discard.hidden=!preserveAcceptedDuringRun;
}
function discardRemeshDraft(){preserveAcceptedDuringRun=false;restoreAcceptedMeshPresentation();showRemeshDraftButton();stage(4,false);}
async function restoreRemeshDraft(){
  if(!meshSnapshot||busy)return;
  const requestedMesh=meshSnapshot,requestedRevision=revision;
  try{
    const response=await api(`/api/mesh-config?id=${encodeURIComponent(requestedMesh.jobId)}`);
    if(meshSnapshot!==requestedMesh||revision!==requestedRevision||busy)return;
    const cfg=response.config||response,sourceConfig=cfg.cell_source||{};
    $('mode').value=sourceConfig.kind||'gmsh_generated';$('crs').value=cfg.analysis_crs||meshSnapshot.crs;$('output').value=cfg.output_root||$('output').value;
    $('gmsh-domain').value='rectangle';$('raster').value='';$('boundary-path').value='';$('mesh').value='';$('west').value='500000';$('south').value='1000000';$('east').value='500002';$('north').value='1000001';$('width').value='1';$('height').value='1';$('policy').value='strict';$('max-cells').value=sourceConfig.max_cells??1000;$('subdivisions').value=cfg.edge_subdivisions??8;$('planned-raster').value=cfg.resolution_reference?.path||'';$('planned-band').value=cfg.resolution_reference?.band??1;plannedSource=null;plannedRequest++;
    $('gmsh-sizing').value='uniform';$('h-min').value='250';$('h-max').value='1000';$('dem-raster').value='';$('dem-band').value='1';$('dem-value-units').value='m';$('sf1-scale').value='1';$('relief-radius').value='500';gmshAdvancedEnabled=false;$('gmsh-advanced-enabled').checked=false;$('gmsh-algorithm').value='5';$('gmsh-smoothing').value='0';$('gmsh-optimize').value='0';
    if(sourceConfig.kind==='structured_grid'){
      $('gmsh-domain').value=sourceConfig.roi_raster_path?'raster':'rectangle';
      if(sourceConfig.roi_raster_path)$('raster').value=sourceConfig.roi_raster_path;
      if(sourceConfig.extent) ['west','south','east','north'].forEach((id,index)=>$(id).value=sourceConfig.extent[index]);
      if(sourceConfig.cell_width_m)$('width').value=sourceConfig.cell_width_m;if(sourceConfig.cell_height_m)$('height').value=sourceConfig.cell_height_m;
      $('policy').value=sourceConfig.boundary_policy||'strict';
    }else if(sourceConfig.kind==='mesh_artifact'){
      $('mesh').value=sourceConfig.path||'';
    }else if(sourceConfig.kind==='gmsh_generated'){
      $('gmsh-domain').value=sourceConfig.boundary_path?'shapefile':sourceConfig.roi_raster_path?'raster':'rectangle';
      $('gmsh-exe').value=sourceConfig.executable||$('gmsh-exe').value;$('gmsh-sizing').value=sourceConfig.sizing||'uniform';
      if(sourceConfig.boundary_path)$('boundary-path').value=sourceConfig.boundary_path;if(sourceConfig.roi_raster_path)$('raster').value=sourceConfig.roi_raster_path;
      if(sourceConfig.extent)['west','south','east','north'].forEach((id,index)=>$(id).value=sourceConfig.extent[index]);
      if(sourceConfig.h_max_m)$('h-max').value=sourceConfig.h_max_m;if(sourceConfig.h_min_m)$('h-min').value=sourceConfig.h_min_m;
      if(sourceConfig.dem_raster_path)$('dem-raster').value=sourceConfig.dem_raster_path;if(sourceConfig.dem_band)$('dem-band').value=sourceConfig.dem_band;
      if(sourceConfig.relief_radius_m)$('relief-radius').value=sourceConfig.relief_radius_m;if(sourceConfig.sf1_scale)$('sf1-scale').value=sourceConfig.sf1_scale;
      const advanced=sourceConfig.advanced||{};gmshAdvancedEnabled=!!sourceConfig.advanced;$('gmsh-advanced-enabled').checked=gmshAdvancedEnabled;
      if(advanced.algorithm_2d)$('gmsh-algorithm').value=advanced.algorithm_2d;$('gmsh-smoothing').value=advanced.smoothing??0;$('gmsh-optimize').value=advanced.optimize_2d??0;
    }
    preserveAcceptedDuringRun=true;revision++;preflightId=null;mode();showRemeshDraftButton();stage(2);$('validation').className='info muted';$('validation').textContent='Đã khôi phục cấu hình từ lượt chạy đã lưu. Lưới hiện tại vẫn được giữ đến khi lượt chạy mới hoàn tất.';$('run').disabled=true;
  }catch(error){toast('Không khôi phục được cấu hình: '+error.message);}
}
function mountSingleAttributeTable(){
  if(!geometry.length||!attrs.size)return false;
  const tableHelp=$('attribute-preview').querySelector?.('.field-help');if(tableHelp)tableHelp.textContent='Xem giá trị theo ID ô.';
  if(typeof MeshCherryAttributeTable==='undefined'){$('csv').hidden=false;$('json').hidden=false;$('extract-status').textContent='Sẵn sàng xuất CSV / JSON.';return false;}
  destroyTable('single');$('attribute-preview').hidden=false;
  const columns=[{key:'value',label:'Giá trị'}];
  attributeTable=MeshCherryAttributeTable.mount($('attribute-table'),{featureColumns:columns,geometryColumns:false,pageSize:100,fetchPage:async({page,pageSize,search,signal})=>{
    if(signal?.aborted)throw new DOMException('Đã hủy','AbortError');
    const matches=geometry.filter(cell=>!search||String(cell.cell_id).includes(search));
    const rows=matches.slice((page-1)*pageSize,page*pageSize).map(cell=>({cell_id:cell.cell_id,value:attrs.get(cell.cell_id)?.value??null}));
    return {rows,totalRows:matches.length,page};
  }});
  attributeTable.ready.then(ready=>{if(ready){$('csv').hidden=false;$('json').hidden=false;$('extract-status').textContent='Bảng đã sẵn sàng. Có thể xuất CSV / JSON.';}});
  return true;
}
function renderBatchQueue(){
  const runningBatch=!!batchJobId||batchStartUncertain;
  if(runningBatch&&!busy){batchOwnsBusy=true;setBusy(true);}
  else if(!runningBatch&&batchOwnsBusy){batchOwnsBusy=false;setBusy(false);}
  const list=$('batch-queue');list.replaceChildren();batchQueue.forEach((item,index)=>{
    const li=document.createElement('li'),path=document.createElement('small'),name=document.createElement('input'),band=document.createElement('input'),units=document.createElement('input'),semantics=document.createElement('select'),remove=document.createElement('button');
    const nameField=document.createElement('div'),bandField=document.createElement('div'),unitsField=document.createElement('div'),semanticsField=document.createElement('div');
    for(const [field,labelText,control] of [[nameField,'Tên cột',name],[bandField,'Band',band],[unitsField,'Đơn vị',units],[semanticsField,'Ý nghĩa',semantics]]){field.className='batch-queue-field';const label=document.createElement('label');label.textContent=labelText;label.htmlFor=`batch-${['name','band','units','semantics'][[nameField,bandField,unitsField,semanticsField].indexOf(field)]}-${index}`;control.id=label.htmlFor;field.append(label,control);}
    path.className='batch-queue-path';path.textContent=item.path;name.value=item.name;name.setAttribute('aria-label',`Tên cột raster ${index+1}`);band.type='number';band.min='1';band.step='1';band.value=item.band;band.setAttribute('aria-label',`Band raster ${index+1}`);units.value=item.value_units;units.placeholder='Đơn vị bắt buộc';units.setAttribute('aria-label',`Đơn vị raster ${index+1}`);
    for(const [value,label] of [['intensive','Trung bình theo diện tích (khuyên dùng)'],['extensive','Tổng trên pixel'],['categorical','Tỷ lệ lớp']]){const option=document.createElement('option');option.value=value;option.textContent=label;semantics.append(option);}semantics.value=item.semantics;
    name.addEventListener('change',()=>{clearExtractionDraft();item.name=name.value.trim();renderBatchQueue();});band.addEventListener('change',()=>{clearExtractionDraft();item.band=Number(band.value);updateBatchReadiness();});units.addEventListener('input',()=>{clearExtractionDraft();item.value_units=units.value.trim();updateBatchReadiness();});semantics.addEventListener('change',()=>{clearExtractionDraft();item.semantics=semantics.value;});
    remove.type='button';remove.textContent='Bỏ';remove.setAttribute('aria-label',`Bỏ raster ${index+1}`);remove.disabled=!!batchJobId||batchStartUncertain;remove.onclick=()=>{clearExtractionDraft();batchQueue.splice(index,1);renderBatchQueue();};
    for(const control of [name,band,units,semantics])control.disabled=!!batchJobId||batchStartUncertain;li.append(path,nameField,bandField,unitsField,semanticsField,remove);list.append(li);
  });
  updateBatchReadiness();
  $('batch-add').disabled=!!batchJobId||batchStartUncertain||batchRecoveryBlocked||busy;
  $('batch-progress').hidden=!batchJobId;
  const batchLocked=!!batchJobId||batchStartUncertain||batchRecoveryBlocked;
  $('start-another').disabled=busy||batchLocked;
  if(batchLocked)$('cancel-run').disabled=true;
  $('extract-run').disabled=batchLocked;
  $('run').disabled=batchLocked||!preflightId;
  const remesh=$('remesh-draft');if(remesh)remesh.disabled=busy||batchLocked;
  const qualityMenu=$('menu-quality-open');if(qualityMenu)qualityMenu.disabled=busy||!meshSnapshot;
}
function batchQueueIssue(){
  const reserved=new Set(['cell_id','id','kind','area','area_m2','centroid_x','centroid_y']),names=new Set();
  if(!batchQueue.length)return 'Chọn một hay nhiều raster và thêm vào hàng đợi.';
  if(batchQueue.length>500)return 'Hàng đợi vượt giới hạn 500 raster.';
  for(const [index,item] of batchQueue.entries()){
    const name=String(item.name||'').trim(),key=name.toLocaleLowerCase();
    if(!name)return `Raster ${index+1}: nhập tên cột.`;
    if(name.length>200)return `Raster ${name}: tên cột quá dài (tối đa 200 ký tự).`;
    if(reserved.has(key))return `Raster ${name}: tên cột dành riêng cho dữ liệu ô lưới (ID, kiểu ô, diện tích hoặc tọa độ tâm); hãy chọn tên khác.`;
    if(names.has(key))return `Raster ${name}: tên cột bị trùng.`;
    if(!Number.isInteger(Number(item.band))||Number(item.band)<1)return `Raster ${name}: band phải là số nguyên dương.`;
    if(!String(item.value_units||'').trim())return `Raster ${name}: nhập đơn vị ở dòng này hoặc nhập đơn vị chung rồi bấm “Áp dụng đơn vị cho hàng đợi”.`;
    if(String(item.value_units).length>200)return `Raster ${name}: đơn vị quá dài (tối đa 200 ký tự).`;
    if(!['intensive','extensive','categorical'].includes(item.semantics))return `Raster ${name}: chọn một kiểu tổng hợp hợp lệ.`;
    names.add(key);
  }
  return '';
}
function validBatchQueue(){return !batchQueueIssue();}
function writeBatchRecovery(state){try{sessionStorage.setItem(BATCH_RECOVERY_STORAGE_KEY,JSON.stringify(state));}catch{}}
function readBatchRecovery(){try{const value=JSON.parse(sessionStorage.getItem(BATCH_RECOVERY_STORAGE_KEY)||'null');if(!value||value.version!==1||typeof value.mesh_job_id!=='string'||!Array.isArray(value.features)||value.features.length<1||value.features.length>500)return null;return value;}catch{return null;}}
function validRecoveredFeatures(features){const reserved=new Set(['cell_id','id','kind','area','area_m2','centroid_x','centroid_y']),names=new Set();return features.every(item=>{if(!item||typeof item.name!=='string'||!item.name.trim()||item.name.length>200||reserved.has(item.name.toLocaleLowerCase())||names.has(item.name.toLocaleLowerCase())||typeof item.path!=='string'||!item.path.trim()||!Number.isInteger(Number(item.band))||Number(item.band)<1||typeof item.value_units!=='string'||!item.value_units.trim()||item.value_units.length>200||!['intensive','extensive','categorical'].includes(item.semantics))return false;names.add(item.name.toLocaleLowerCase());return true;});}
function clearBatchRecovery(){try{sessionStorage.removeItem(BATCH_RECOVERY_STORAGE_KEY);}catch{}}
function isCurrentBatchView(id,generation,meshId){return generation===batchViewGeneration&&(!batchMeshId||batchMeshId===meshId)&&(!meshSnapshot||(meshId&&meshSnapshot.jobId===meshId))&&(batchJobId===id||batchCompletedJobId===id||(!batchJobId&&!batchCompletedJobId&&!batchMeshId));}
function resetBatchResultsForMesh(newMeshId){
  const stored=readBatchRecovery(),previousMeshId=batchMeshId||stored?.mesh_job_id;
  if(!previousMeshId||previousMeshId===newMeshId)return;
  batchViewGeneration++;clearTimeout(batchPollTimer);batchPollTimer=null;
  destroyTable('batch');const meta=$('batch-feature-status');if(typeof meta?.remove==='function')meta.remove();
  batchCompletedJobId=null;batchMeshId=null;$('batch-export').disabled=true;$('batch-status').className='info muted';$('batch-status').textContent='Lưới đã thay đổi; xem trước cũ đã được gỡ. Hãy trích lại thuộc tính.';
  if(stored?.mesh_job_id!==newMeshId){if(stored?.state==='completed')clearBatchRecovery();else if(stored){batchRequestId=stored.request_id||null;batchStartUncertain=stored.state==='starting';batchRecoveryBlocked=true;}}
}
function restorePendingBatchForMesh(meshId){
  const pending=readBatchRecovery();if(!pending)return;
  if(pending.mesh_job_id!==meshId){if(pending.state!=='completed'){batchRecoveryBlocked=true;$('batch-status').textContent='Có lượt trích đang được khôi phục cho một lưới khác. Mở đúng lưới để tiếp tục theo dõi; lượt mới chưa được gửi.';renderBatchQueue();}return;}
  batchRecoveryBlocked=false;
  activeBatchFeatures=pending.features?.map(item=>({...item}))||null;activeBatchDraft=pending.draft||null;activeBatchPlanHash=pending.plan_hash||null;
  if(pending.single){document.body.dataset.extractMode='single';$('batch-panel-title').textContent='Kết quả trích thuộc tính';$('batch-queue-controls').hidden=true;$('batch-panel').hidden=false;}
  if(pending.state==='completed'&&typeof pending.job_id==='string'&&/^[A-Za-z0-9_-]{8,100}$/.test(pending.job_id)&&validRecoveredFeatures(pending.features)){
    batchViewGeneration++;batchQueue=pending.features.map(item=>({...item}));batchCompletedJobId=pending.job_id;batchMeshId=meshId;$('batch-panel').hidden=false;renderBatchQueue();mountBatchTable(batchCompletedJobId,pending.terminal_status||'pass',{},batchViewGeneration,meshId);return;
  }
  if(pending.state==='running'&&typeof pending.job_id==='string'&&/^[A-Za-z0-9_-]{8,100}$/.test(pending.job_id)&&validRecoveredFeatures(pending.features)){
    batchViewGeneration++;batchQueue=pending.features.map(item=>({...item}));activeBatchFeatures=pending.features.map(item=>({...item}));activeBatchDraft=pending.draft||null;batchJobId=pending.job_id;batchMeshId=meshId;batchRequestId=pending.request_id||null;
    $('batch-panel').hidden=false;$('batch-status').textContent='Đang khôi phục lượt trích hiện có; không gửi lại yêu cầu.';$('batch-cancel').hidden=false;renderBatchQueue();pollBatch(batchJobId,batchViewGeneration,meshId);return;
  }
  if(pending.state==='starting'&&typeof pending.request_id==='string'){
    if(!validRecoveredFeatures(pending.features)){$('batch-status').textContent='Dữ liệu khôi phục lượt trích không hợp lệ; không gửi lại yêu cầu.';batchRecoveryBlocked=true;renderBatchQueue();return;}
    batchViewGeneration++;const generation=batchViewGeneration;
    batchQueue=pending.features.map(item=>({...item}));activeBatchFeatures=pending.features.map(item=>({...item}));activeBatchDraft=pending.draft||null;batchRequestId=pending.request_id;
    batchMeshId=meshId;batchStartUncertain=true;$('batch-panel').hidden=false;$('batch-run').disabled=true;$('batch-status').textContent='Đang xác minh yêu cầu trích đã gửi trước khi tải lại…';renderBatchQueue();recoverPendingBatchStart(pending,generation,meshId);
  }
}
async function recoverPendingBatchStart(pending,generation=batchViewGeneration,meshId=pending.mesh_job_id){
  try{
    let status=await api('/api/batch/status?request_id='+encodeURIComponent(pending.request_id),undefined,8000);
    if(generation!==batchViewGeneration||(meshSnapshot&&meshSnapshot.jobId!==meshId))return;
    if(!status.job_id){status=await api('/api/batch/start',{mesh_job_id:pending.mesh_job_id,request_id:pending.request_id,features:pending.features.map(({name,path,band,semantics,value_units})=>({name,path,band,semantics,value_units})),...(pending.draft?{draft:pending.draft,plan_hash:pending.plan_hash}:{})},8000);}
    if(generation!==batchViewGeneration||(meshSnapshot&&meshSnapshot.jobId!==meshId))return;
    if(status.request_id!==pending.request_id||status.operation!=='batch_extract'||typeof status.job_id!=='string')throw Error('Máy chủ không xác nhận đúng lượt trích đã gửi.');
    batchStartUncertain=false;batchRequestId=null;batchJobId=status.job_id;batchMeshId=pending.mesh_job_id;activeBatchFeatures=pending.features.map(item=>({...item}));activeBatchDraft=pending.draft||null;writeBatchRecovery({...pending,state:'running',job_id:status.job_id});$('batch-status').textContent='Đã khôi phục đúng lượt trích; không tạo bản chạy thứ hai.';$('batch-cancel').hidden=false;renderBatchQueue();pollBatch(batchJobId,generation,meshId);
  }catch(error){if(generation!==batchViewGeneration||(meshSnapshot&&meshSnapshot.jobId!==meshId))return;if([400,403,404,409].includes(error.status)){clearBatchRecovery();batchStartUncertain=false;batchRequestId=null;batchRecoveryBlocked=false;$('batch-status').className='info error';$('batch-status').textContent='Máy chủ đã từ chối yêu cầu trích (HTTP '+error.status+'). Bạn có thể sửa hàng đợi rồi thử lại.';renderBatchQueue();return;}$('batch-status').textContent='Chưa thể xác minh yêu cầu cũ: '+error.message+'; dữ liệu đã giữ nguyên và chưa gửi lại lần nữa.';}
}
function enqueueBatchPaths(paths){
  const reserved=new Set(['cell_id','id','kind','area','area_m2','centroid_x','centroid_y']);
  for(const path of paths){if(batchQueue.length>=500){toast('Đã đạt giới hạn 500 raster.');break;}
    const stem=String(path).split(/[\\/]/).pop().replace(/\.[^.]+$/,'').slice(0,180)||'feature';let name=stem;
    if(reserved.has(name.toLocaleLowerCase()))name='feature_'+name;
    let suffix=2;while(batchQueue.some(item=>item.name.toLocaleLowerCase()===name.toLocaleLowerCase()))name=`${stem}_${suffix++}`;
    batchQueue.push({name,path,band:1,semantics:'intensive',value_units:$('batch-units').value.trim()});
  }
  clearExtractionDraft();renderBatchQueue();
}
function addBatchRaster(){
  const path=$('batch-raster').value.trim();if(!path){toast('Chọn raster trước khi thêm.');return;}
  const stem=path.split(/[\\/]/).pop().replace(/\.[^.]+$/,'');const name=$('batch-name').value.trim()||stem;
  if(batchQueue.length>=500){toast('Tối đa 500 raster trong một lượt.');return;}
  if(batchQueue.some(item=>item.name.toLocaleLowerCase()===name.toLocaleLowerCase())){toast('Tên cột phải duy nhất (không phân biệt hoa thường).');return;}
  if(['cell_id','id','kind','area','area_m2','centroid_x','centroid_y'].includes(name.toLocaleLowerCase())){toast('Tên này dành riêng cho cột hình học.');return;}
  const band=Number($('batch-band').value);if(!Number.isInteger(band)||band<1){toast('Band phải là số nguyên từ 1 trở lên.');return;}
  batchQueue.push({name,path,band,semantics:$('batch-semantics').value,value_units:$('batch-units').value.trim()});$('batch-raster').value='';$('batch-name').value='';clearExtractionDraft();renderBatchQueue();
}
async function pollBatch(id,generation=batchViewGeneration,meshId=batchMeshId){
  if(batchJobId!==id||!isCurrentBatchView(id,generation,meshId))return;
  try{const job=await api(`/api/job?id=${encodeURIComponent(id)}`);const state=job.status||'running';
    if(!isCurrentBatchView(id,generation,meshId)||batchJobId!==id)return;
    const current=Number.isInteger(job.feature_index)&&Number.isInteger(job.feature_total)?` · ${job.feature_index+1}/${job.feature_total}: ${job.current_feature||''} (${job.feature_status||'đang xử lý'})`:'';
    $('batch-status').textContent=`${activeBatchFeatures?.length===1?'Trích thuộc tính':'Trích nhiều raster'}: ${job.stage||state}${current}`;extractionDraft?.setRunning(state);
    if(['running','stopping','queued'].includes(state)){batchPollTimer=setTimeout(()=>pollBatch(id,generation,meshId),700);return;}
    batchJobId=null;$('batch-cancel').hidden=true;extractionDraft?.setRunning(state);writeBatchRecovery({version:1,state:'completed',job_id:id,mesh_job_id:batchMeshId||readBatchRecovery()?.mesh_job_id,terminal_status:state,features:(activeBatchFeatures||batchQueue).map(item=>({...item})),draft:activeBatchDraft,plan_hash:activeBatchPlanHash,single:document.body.dataset.extractMode==='single'&&$('batch-queue-controls').hidden});renderBatchQueue();
    batchCompletedJobId=id;$('batch-status').className=state==='pass'?'info success':state==='partial'?'info':'info error';
    $('batch-status').textContent=state==='pass'?'Đã trích xong. Đang tải trang xem trước…':state==='partial'?'Có thuộc tính hoàn tất, thiếu dữ liệu hoặc lỗi riêng lẻ. Đang tải xem trước…':state==='cancelled'?'Đã hủy lượt trích; kiểm tra phần đã hoàn tất…':`Lượt trích kết thúc (${state}). Kiểm tra phần kết quả đã lưu…`;
    await mountBatchTable(id,state,job,generation,meshId);
  }catch(error){if(!isCurrentBatchView(id,generation,meshId))return;$('batch-status').className='info error';$('batch-status').textContent='Chưa đọc được trạng thái lượt trích: '+error.message;batchPollTimer=setTimeout(()=>pollBatch(id,generation,meshId),1500);}
}
async function mountBatchTable(id,terminalStatus='pass',jobInfo={},generation=batchViewGeneration,meshId=batchMeshId){
  if(!isCurrentBatchView(id,generation,meshId))return;
  const oldRetry=$('batch-preview-retry');if(typeof oldRetry?.remove==='function')oldRetry.remove();
  destroyTable('batch');const previousMeta=$('batch-feature-status');if(typeof previousMeta?.remove==='function')previousMeta.remove();let columns=Array.isArray(activeBatchDraft?.columns)?activeBatchDraft.columns.map(column=>({key:String(column.id),label:String(column.name||column.source_name||column.id)})):(activeBatchFeatures||batchQueue).map(item=>({key:item.name,label:item.name}));
  $('batch-export').disabled=true;
  let firstPage,tableHasDraft=!!activeBatchDraft;
  try{
    // Bootstrap from durable server metadata, even for older recovery records.
    firstPage=await api('/api/batch/preview',{job_id:id,offset:0,limit:50});
    if(!isCurrentBatchView(id,generation,meshId))return;
    const canonicalColumns=firstPage.batch?.draft?.columns||firstPage.columns;
    tableHasDraft=Array.isArray(canonicalColumns);
    if(tableHasDraft)columns=canonicalColumns.map(column=>({key:String(column.id),label:String(column.name||column.source_name||column.id)}));
    const summary=firstPage.batch||firstPage,meta=document.createElement('div');meta.className='field-help';meta.id='batch-feature-status';
    const features=summary.features||[],counts=features.reduce((all,feature)=>{all[feature.status]=(all[feature.status]||0)+1;return all;},{});
    const countsText=Object.entries(counts).map(([status,count])=>`${status}: ${count}`).join(' · ');
    const heading=document.createElement('p');heading.textContent=`Trạng thái lô: ${summary.status||terminalStatus} · ${summary.cell_count??firstPage.total_cells??0} ô · ${summary.aggregate_candidate_pairs??'—'} cặp ứng viên${countsText?` · ${countsText}`:''}`;meta.append(heading);
    if(features.length){const details=document.createElement('details'),summaryLabel=document.createElement('summary'),list=document.createElement('ul');summaryLabel.textContent=`Trạng thái từng thuộc tính (${features.length})`;list.id='batch-feature-list';
      features.forEach(feature=>{const item=document.createElement('li'),name=document.createElement('strong'),status=document.createElement('span');name.textContent=feature.name;status.textContent=feature.status;item.append(name,status);if(feature.error){const error=document.createElement('small');error.textContent=String(feature.error).slice(0,300);item.append(error);}list.append(item);});details.append(summaryLabel,list);meta.append(details);}
    $('batch-table').before(meta);
  }catch(error){if(!isCurrentBatchView(id,generation,meshId))return;
    $('batch-status').className='info error';$('batch-status').textContent='Không tải được xem trước: '+error.message;
    const retry=document.createElement('button');retry.id='batch-preview-retry';retry.type='button';retry.textContent='Thử tải xem trước lần nữa';retry.onclick=()=>{retry.disabled=true;mountBatchTable(id,terminalStatus,jobInfo,generation,meshId);};$('batch-status').after(retry);return;
  }
  const binding={jobId:id,meshId,generation};
  const table=MeshCherryAttributeTable.mount($('batch-table'),{featureColumns:columns,pageSize:50,fetchPage:async({page,pageSize,search,columns,signal})=>{
    if(!isCurrentBatchView(id,generation,meshId))throw new DOMException('Đã đổi lượt trích','AbortError');
    if(signal?.aborted)throw new DOMException('Đã hủy','AbortError');
    if(firstPage&&!search&&page===1){const cached=firstPage;firstPage=null;return {rows:displayDraftRows(cached.rows||[],columns),totalRows:cached.total_rows??cached.total_cells??cached.batch?.cell_count??0,page:1};}
    const request={job_id:id,offset:(page-1)*pageSize,limit:pageSize,cell_id:search||undefined,cell_id_mode:'substring'};request[tableHasDraft?'visible_columns':'feature_names']=columns.map(column=>typeof column==='string'?column:column.key);const response=await api('/api/batch/preview',request);
    if(!isCurrentBatchView(id,generation,meshId))throw new DOMException('Đã đổi lượt trích','AbortError');
    const summary=response.batch||response;const total=Number(response.total_cells??summary.cell_count??response.cell_count??0);
    return {rows:displayDraftRows(response.rows||[],columns),totalRows:Number(response.total_rows??total),page:Math.floor(Number(response.offset||0)/pageSize)+1};
  }});
  if(!isCurrentBatchView(id,generation,meshId)){table.destroy();return;}
  batchTable=table;batchTableBinding=binding;
  $('batch-panel').hidden=false;
  const ready=await table.ready;if(batchTable!==table||batchTableBinding!==binding||!isCurrentBatchView(id,generation,meshId))return;$('batch-export').disabled=!ready;
  if(ready){$('batch-status').className=terminalStatus==='pass'?'info success':'info';$('batch-status').textContent=`Xem trước đã sẵn sàng · ${columns.length} thuộc tính · chỉ hiển thị tối đa 20 cột.${terminalStatus==='partial'?' Có thuộc tính bị thiếu hoặc lỗi; xem trạng thái từng thuộc tính bên trên.':terminalStatus==='cancelled'?' Lượt trích đã hủy; chỉ dữ liệu đã ghi nhận được hiển thị.':''}`;}
}
async function startBatch(confirmed=null){
  if(!meshSnapshot||batchJobId||batchStartUncertain||batchRecoveryBlocked||busy){if(confirmed)throw Error('Hãy chờ tác vụ hiện tại hoàn tất rồi xác nhận lại bảng.');return;}
  if(!confirmed){const mode=document.body.dataset.extractMode==='batch'?'batch':'single';try{await previewExtractionDraft(mode);}catch(error){$('batch-status').className='info error';$('batch-status').textContent=error.message;}return;}
  const features=confirmed.features||batchQueue.map(({name,path,band,semantics,value_units})=>({name,path,band,semantics,value_units}));
  if(!features.length||features.length>500||!confirmed.draft||!confirmed.planHash)throw Error('Bản nháp thiếu raster hoặc xác nhận của máy chủ.');
  const mesh=confirmed.mesh||meshSnapshot,meshId=mesh.jobId;
  if(meshSnapshot!==mesh||confirmed.draft.mesh_job_id!==meshId||confirmed.draft.geometry_digest!==mesh.digest)throw Error('Bản nháp đã thay đổi lưới; hãy mở lại bảng xem trước.');
  activeBatchFeatures=features.map(item=>({...item}));activeBatchPlanHash=confirmed.planHash;activeBatchDraft=confirmed.draft;
  $('batch-panel-title').textContent=confirmed.single?'Kết quả trích thuộc tính':'Kết quả trích nhiều raster';$('batch-queue-controls').hidden=!!confirmed.single;$('batch-panel').hidden=false;
  const generation=++batchViewGeneration;clearTimeout(batchPollTimer);batchPollTimer=null;batchMeshId=meshId;destroyTable('batch');batchJobId=null;batchCompletedJobId=null;$('batch-export').disabled=true;$('batch-status').className='info';$('batch-status').textContent='Đang khởi chạy lượt trích…';$('batch-run').disabled=true;
  try{if(!batchRequestId)batchRequestId=typeof crypto!=='undefined'&&crypto.randomUUID?crypto.randomUUID():`batch-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const pending={version:1,state:'starting',mesh_job_id:meshId,request_id:batchRequestId,features:activeBatchFeatures,draft:confirmed.draft,plan_hash:confirmed.planHash,single:!!confirmed.single};writeBatchRecovery(pending);batchStartUncertain=true;renderBatchQueue();
    const result=await api('/api/batch/start',{mesh_job_id:meshId,request_id:batchRequestId,features:activeBatchFeatures,draft:confirmed.draft,plan_hash:confirmed.planHash});
    if(generation!==batchViewGeneration||meshSnapshot?.jobId!==meshId)return;
    if(result.request_id!==batchRequestId)throw Error('Máy chủ chưa xác nhận đúng mã yêu cầu trích.');batchJobId=result.job_id;if(!batchJobId)throw Error('Máy chủ không trả mã lượt trích.');batchStartUncertain=false;batchRequestId=null;batchMeshId=meshId;writeBatchRecovery({version:1,state:'running',job_id:batchJobId,mesh_job_id:meshId,features:activeBatchFeatures,draft:confirmed.draft,plan_hash:confirmed.planHash,single:!!confirmed.single});$('batch-cancel').hidden=false;$('batch-status').textContent=activeBatchFeatures.length===1?'Đang trích thuộc tính…':'Đang trích các raster…';extractionDraft?.setRunning('running');renderBatchQueue();pollBatch(batchJobId,generation,meshId);
  }catch(error){
    if(generation!==batchViewGeneration||meshSnapshot?.jobId!==meshId)return;
    if(batchRequestId&&[400,403,404,409].includes(error.status)){
      clearBatchRecovery();batchStartUncertain=false;batchRequestId=null;
      $('batch-status').className='info error';$('batch-status').textContent='Máy chủ đã từ chối yêu cầu trích (HTTP '+error.status+'). Bạn có thể sửa bảng rồi thử lại.';
      renderBatchQueue();throw error;
    }else if(batchRequestId){
      const pending={version:1,state:'starting',mesh_job_id:meshId,request_id:batchRequestId,features:activeBatchFeatures,draft:confirmed.draft,plan_hash:confirmed.planHash,single:!!confirmed.single};
      writeBatchRecovery(pending);batchStartUncertain=true;$('batch-status').className='info';$('batch-status').textContent='Đang kiểm tra mã yêu cầu đã gửi; dữ liệu được giữ để tránh chạy trùng.';
      extractionDraft?.setRunning('queued');renderBatchQueue();recoverPendingBatchStart(pending);
    }else{
      $('batch-status').className='info error';$('batch-status').textContent='Không thể bắt đầu trích: '+error.message;renderBatchQueue();throw error;
    }
  }
}
async function cancelBatch(){if(!batchJobId)throw Error('Không có lượt trích đang chạy để dừng.');$('batch-cancel').disabled=true;try{const result=await api('/api/job/stop',{job_id:batchJobId},8000);if(!result?.accepted)throw Error('Máy chủ chưa nhận yêu cầu dừng.');$('batch-status').textContent='Đang dừng lượt trích…';return result;}catch(error){$('batch-status').textContent='Chưa xác nhận được yêu cầu dừng: '+error.message;throw error;}finally{$('batch-cancel').disabled=false;}}
function displayDraftRows(rows,columns){
  return rows.map(row=>{const values={...(row.values||{})};for(const column of columns){const key=typeof column==='string'?column:column.key;const value=values[key];if(!value||typeof value!=='object'||typeof value.state!=='string')continue;
      values[key]=value.state==='computed'?value.value:value.state==='no_data'?'NoData':value.state==='not_computed'?(value.status==='running'?'Đang tính':'Chưa tính'):value.state==='failed'?'Lỗi trích xuất':value.state==='cancelled'?'Đã hủy':'—';}
    return {...row,id:row.row_kind==='manual'?`Nhập tay · ${row.row_id||''}`:row.id??row.cell_id??row.row_id??'',values};});
}
async function exportBatch(){
  const binding=batchTableBinding;if(!batchTable||!binding||!meshSnapshot||meshSnapshot.jobId!==binding.meshId||!isCurrentBatchView(binding.jobId,binding.generation,binding.meshId)||batchJobId||batchCompletedJobId!==binding.jobId)return;
  const id=binding.jobId;
  const format=$('batch-format').value,request={job_id:id,format},extension=format==='json'?'json':'zip';
  const result=await managedDownload({url:'/api/batch/export',filename:`attributes-${format.replace('_','-')}.${extension}`,method:'POST',body:JSON.stringify(request),kind:'batch',payload:request,fetchOptions:{headers:{'Content-Type':'application/json'}}});
  if(result?.phase==='error')toast(result.error?.message||result.error||'Không xuất được bảng.');
}
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
function demPreviewIdentity(){return [$('dem-raster').value.trim(),$('dem-band').value,$('gmsh-sizing').value,currentStage].join('|');}
function releaseDemPreview(){const image=$('dem-preview-image');image.onload=null;image.onerror=null;image.hidden=true;image.removeAttribute('src');if(demPreviewURL){URL.revokeObjectURL(demPreviewURL);demPreviewURL=null;}}
function clearDemPreview(){demPreviewRequest++;demPreviewKey='';releaseDemPreview();$('dem-preview').hidden=true;$('dem-preview-status').textContent='Chọn DEM để xem trước.';$('dem-preview-meta').replaceChildren();$('dem-preview-caption').textContent='Màu P2–P98 chỉ phục vụ hiển thị.';}
async function loadDemPreview(){
  const path=$('dem-raster').value.trim(),sizing=$('gmsh-sizing').value;
  if(!path||$('mode').value!=='gmsh_generated'||currentStage!==2||!['relief','sf1_relief'].includes(sizing)){clearDemPreview();return;}
  const key=demPreviewIdentity(),request=++demPreviewRequest;demPreviewKey=key;$('dem-preview').hidden=false;releaseDemPreview();$('dem-preview-meta').replaceChildren();$('dem-preview-caption').textContent='';$('dem-preview-status').textContent='Đang dựng hình DEM…';
  const bandValue=$('dem-band').value.trim(),band=Number(bandValue);
  if(!/^\d+$/.test(bandValue)||!Number.isSafeInteger(band)||band<1){fieldError('dem-band','dem-band-error','Nhập band DEM là số nguyên dương.');$('dem-preview-status').textContent='Band DEM không hợp lệ.';return;}
  fieldError('dem-band','dem-band-error','');
  try{
    const report=await api('/api/input-preview',{path,kind:'raster',band,analysis_crs:$('crs').value.trim()});
    if(request!==demPreviewRequest||key!==demPreviewIdentity())return;
    const image=$('dem-preview-image'),url=URL.createObjectURL(previewBlob(report.image));demPreviewURL=url;
    image.onload=()=>{if(demPreviewURL!==url||request!==demPreviewRequest)return;image.hidden=false;$('dem-preview-status').textContent='';};
    image.onerror=()=>{if(demPreviewURL!==url||request!==demPreviewRequest)return;releaseDemPreview();$('dem-preview-status').textContent='Không hiển thị được ảnh xem trước. Hãy thử lại.';};
    image.src=url;
    const shape=Array.isArray(report.source_shape)?`${report.source_shape[1]} × ${report.source_shape[0]} px`:'—';
    const resolution=Array.isArray(report.pixel_size)?report.pixel_size.map(value=>`${value}`).join(' × ')+(report.coordinate_units&&report.coordinate_units!=='unknown'?` ${report.coordinate_units}`:''):'—';
    const crs=report.crs||'Chưa có CRS';
    const rows=[['Tệp',path.split(/[\\/]/).pop()],['CRS',report.crs_defined===false?'Chưa khai báo · cần .prj cùng tên':crs],['Band',report.band??band],['Kích thước',shape],['Kích thước pixel',resolution],['NoData',report.nodata===undefined||report.nodata===null?'—':report.nodata]];
    $('dem-preview-meta').replaceChildren();detailRows($('dem-preview-meta'),rows);
    $('dem-preview-caption').textContent=[report.sampled?'Ảnh xem trước đã giảm mẫu.':'',report.no_valid_samples?'Không có pixel hợp lệ.':'','Màu P2–P98 chỉ phục vụ hiển thị.'].filter(Boolean).join(' ');
  }catch(error){if(request!==demPreviewRequest||key!==demPreviewIdentity())return;$('dem-preview').hidden=false;$('dem-preview-status').textContent='Không dựng được preview: '+error.message;$('dem-preview-meta').replaceChildren();}
}
function showResultTab(value){
  if(!['mesh','extract','log'].includes(value))return;
  resultTab=value;document.body.dataset.result=value;markWorkspace(value==='mesh'?'mesh':value);
  for(const tab of ['mesh','extract','log']){const panel=$(tab==='mesh'?'mesh-result-panel':tab==='extract'?'extract-result-panel':'result-log-panel');panel.hidden=tab!==value;panel.setAttribute('aria-hidden',String(tab!==value));}
  if(value==='log')return;
  $('result-map-title').textContent=value==='mesh'?'Lưới đã tạo':'Thuộc tính trên lưới';
  if(savedMap){const view=mapView();draw(savedMap.cells,value==='extract'?savedMap.records:[],{...savedMap.contract,footprint:value==='extract'?savedMap.contract.footprint:null,attributeSummary:value==='extract'?savedMap.contract.attributeSummary:null},false);if(view)$('map').setAttribute('viewBox',view.join(' '));}
}
function markWorkspace(value){
  document.body.dataset.workspace=value;
  for(const button of document.querySelectorAll('button[data-workspace]')){const selected=button.dataset.workspace===value;button.setAttribute('aria-current',selected?'page':'false');button.setAttribute('aria-pressed',String(selected));}
}
function setExtractMode(value){
  if(!['single','batch'].includes(value))return;
  document.body.dataset.extractMode=value;
  $('extract-options').hidden=value!=='single';$('batch-queue-controls').hidden=value!=='batch';$('batch-panel').hidden=!(value==='batch'||batchJobId||batchCompletedJobId||batchTable);$('attribute-preview').hidden=value==='batch'||!attributeTable;
  if(extractionDraft&&extractionDraftMode!==value)clearExtractionDraft();
  for(const button of document.querySelectorAll('button[data-extract-mode]')){const selected=button.dataset.extractMode===value;button.setAttribute('aria-pressed',String(selected));}
  updateExtractReadiness();updateBatchReadiness();
}
function updateExtractReadiness(){
  const status=$('extract-readiness');if(!status)return;
  const locked=busy||!!batchJobId||batchStartUncertain||batchRecoveryBlocked;
  const missing=!$('extract-raster').value.trim()?'Chọn raster thuộc tính.':!/^\d+$/.test($('extract-band').value.trim())||Number($('extract-band').value)<1?'Nhập band là số nguyên dương.':!$('extract-units').value.trim()?'Nhập đơn vị thuộc tính.':'';
  $('extract-run').disabled=locked||!meshSnapshot||extractChecking;
  status.textContent=!meshSnapshot?'Hãy tạo hoặc mở lưới trước khi trích thuộc tính.':locked?'Đang có tác vụ lưới hoặc batch; đợi tác vụ kết thúc trước khi extract.':missing?`Chưa sẵn sàng: ${missing}`:'Đã có lưới và thông tin nguồn; sẵn sàng kiểm tra extract.';
}
function updateBatchReadiness(){
  const status=$('batch-readiness');if(!status)return;
  const issue=batchQueueIssue();
  status.className='field-help';
  status.textContent=issue?`${issue}${!meshSnapshot?' Cần tạo hoặc mở lưới trước khi trích batch.':''}`:(!meshSnapshot?'Hàng đợi hợp lệ; hãy tạo hoặc mở lưới để bật trích batch.':`${batchQueue.length} raster đủ metadata. Có thể trích sau khi xác nhận đơn vị từng dòng.`);
  const canApply=!!$('batch-units').value.trim()&&batchQueue.some(item=>!String(item.value_units||'').trim());
  const apply=$('batch-apply-units');if(apply)apply.disabled=!canApply||!!batchJobId||batchStartUncertain||batchRecoveryBlocked||busy;
  const blocked=!!batchJobId||batchStartUncertain||batchRecoveryBlocked||busy;
  $('batch-run').disabled=!meshSnapshot||!validBatchQueue()||blocked;
}
function applyBatchUnitsToQueue(){
  if(batchJobId||batchStartUncertain||batchRecoveryBlocked||busy)return false;
  const units=$('batch-units').value.trim();
  if(!units){$('batch-readiness').className='field-help error';$('batch-readiness').textContent='Nhập đơn vị chung trước; thao tác này chỉ điền vào các dòng đang để trống, không ghi đè đơn vị hiện có.';return false;}
  let applied=0;for(const item of batchQueue)if(!String(item.value_units||'').trim()){item.value_units=units;applied++;}if(applied)clearExtractionDraft();
  renderBatchQueue();if(applied){$('batch-readiness').textContent=`Đã áp dụng “${units}” cho ${applied} dòng đang trống; các đơn vị đã nhập trước đó được giữ nguyên.`;return true;}
  $('batch-readiness').textContent='Không có dòng nào thiếu đơn vị; không thay đổi hàng đợi.';return false;
}
function setWorkspace(value){
  if(!['roi','mesh','extract','log','compare'].includes(value))return;
  if(value==='compare'&&busy&&!comparisonOwnsBusy)return;
  markWorkspace(value);
  if(value==='compare'){
    for(const panel of document.querySelectorAll('[data-panel]'))panel.hidden=panel!==$('comparison-workspace');
    document.body.dataset.stage='5';$('comparison-workspace').hidden=false;prepareComparison();markWorkspace(value);return;
  }
  if(value==='roi'){stage(1,false,true);markWorkspace(value);return;}
  if(value==='mesh'){if(meshSnapshot&&jobId&&displayCellCount()){stage(4,false,true);showResultTab('mesh');}else stage(2,false,true);markWorkspace(value);return;}
  if(value==='extract'){document.querySelector('[data-panel="4"]').hidden=false;setExtractMode(document.body.dataset.extractMode||'single');showResultTab('extract');if(meshSnapshot&&jobId&&displayCellCount())stage(4,false,true);markWorkspace(value);return;}
  for(const panel of document.querySelectorAll('[data-panel]'))panel.hidden=panel.dataset.panel!=='4';
  showResultTab('log');markWorkspace(value);
}
function setupWorkspaceNavigation(){
  for(const button of document.querySelectorAll('button[data-workspace]'))button.addEventListener('click',()=>setWorkspace(button.dataset.workspace));
  const modeNav=document.createElement('div');modeNav.className='extract-mode-nav';modeNav.setAttribute('role','group');modeNav.setAttribute('aria-label','Chọn cách trích thuộc tính');
  for(const [mode,label] of [['single','Một raster'],['batch','Nhiều raster']]){const button=document.createElement('button');button.type='button';button.dataset.extractMode=mode;button.textContent=label;button.setAttribute('aria-pressed',String((document.body.dataset.extractMode||'single')===mode));button.onclick=()=>setExtractMode(mode);modeNav.append(button);}
  $('extract-options').before(modeNav);
  const singleStatus=document.createElement('p');singleStatus.id='extract-readiness';singleStatus.className='field-help';singleStatus.setAttribute('role','status');singleStatus.setAttribute('aria-live','polite');$('extract-run').before(singleStatus);
  const queueStatus=document.createElement('p');queueStatus.id='batch-readiness';queueStatus.className='field-help';queueStatus.setAttribute('role','status');queueStatus.setAttribute('aria-live','polite');$('batch-queue').before(queueStatus);
  const applyUnits=document.createElement('button');applyUnits.id='batch-apply-units';applyUnits.type='button';applyUnits.textContent='Áp dụng đơn vị cho hàng đợi';applyUnits.onclick=applyBatchUnitsToQueue;$('batch-run').before(applyUnits);
  $('batch-units').addEventListener('input',updateBatchReadiness);
  setExtractMode(document.body.dataset.extractMode||'single');markWorkspace(document.body.dataset.workspace||'roi');
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
  currentStage=value;markWorkspace(value===1?'roi':value===4?resultTab==='extract'?'extract':resultTab==='log'?'log':'mesh':'mesh');
  if(value===4&&$('job-monitor').hidden){$('job-monitor').hidden=false;$('monitor-phase').textContent='Lưới đã sẵn sàng';$('monitor-elapsed').textContent='—';$('monitor-percent').textContent='Chưa có tác vụ đang chạy';$('monitor-eta').textContent='—';$('cancel-run').disabled=true;}
  if(value!==2)clearDemPreview();else if(['relief','sf1_relief'].includes($('gmsh-sizing').value)&&$('dem-raster').value.trim())loadDemPreview();
  visitedStages.add(value);document.body.dataset.stage=String(value);
  for(const panel of document.querySelectorAll('[data-panel]'))panel.hidden=Number(panel.dataset.panel)!==value;
  for(const button of document.querySelectorAll('button[data-stage]')){button.setAttribute('aria-current',Number(button.dataset.stage)===value?'step':'false');button.classList.toggle('visited',visitedStages.has(Number(button.dataset.stage)));}
  const sourcePath=$('raster').value;$('check-summary').title=sourcePath;
  $('check-summary').textContent=`Nguồn: ${sourcePath.split(/[\\/]/).pop()||'Chưa chọn'}\nHệ tọa độ tính: ${$('crs').value}\nCách trích: ${$('semantics').selectedOptions[0].textContent}\nĐơn vị: ${$('units').value}\nLưới: ${$('mode').selectedOptions[0].textContent}\nKiểm tra cấu hình để biết số ô và lượng dữ liệu cần đọc.`;
  if(!tutorialActive)$('check-summary').textContent=extractMode?'Extract trên lưới đã lưu · '+(meshSnapshot?.display?.cell_count??meshSnapshot?.cells.length??0)+' ô':`${$('mode').value==='mesh_artifact'?'Mesh nhập: '+$('mesh').value.split(/[\\/]/).pop():'ROI: '+($('gmsh-domain').value==='shapefile'?$('boundary-path').value.split(/[\\/]/).pop():$('gmsh-domain').value==='raster'?sourcePath.split(/[\\/]/).pop():'Miền chữ nhật')}\nCRS tính toán: ${$('crs').value}\nLưới: ${$('mode').selectedOptions[0].textContent}`;
  tutorialHint();
  const heading=document.querySelector(`[data-panel="${value}"] h2`);
  if(heading&&moveFocus){
    heading.tabIndex=-1;heading.focus({preventScroll:true});
    const panel=document.querySelector(`[data-panel="${value}"]`);
    const behavior=window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth';
    panel?.scrollIntoView({block:'start',behavior});
  }
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
function failureSummary(job){
  const failure=job.result?.failure||job.failure||null,rawError=typeof job.result?.error==='string'?job.result.error:'';
  let title='Tác vụ chưa hoàn tất',message='Không thể hoàn tất tác vụ. Mở Chi tiết kỹ thuật để xem nguyên nhân đầy đủ.',actions=[];
  if(failure&&typeof failure==='object'){
    if(typeof failure.title==='string'&&failure.title.trim())title=failure.title.trim();
    if(typeof failure.message==='string'&&failure.message.trim())message=failure.message.trim();
    if(Array.isArray(failure.actions))actions=failure.actions.filter(item=>typeof item==='string'&&item.trim()).map(item=>item.trim());
  }else if(/overlap.{0,80}(positive\s+)?area|((positive\s+)?area).{0,80}overlap/i.test(rawError.replace(/[_-]+/g,' '))){
    title='Các ô lưới đang chồng lấn';
    message='Một số ô lưới giao nhau với diện tích dương. Lưới này chưa được chấp nhận để tránh lưu topology không hợp lệ.';
    actions=['Mở Chi tiết kỹ thuật để xem lỗi đầy đủ và kiểm tra dữ liệu hoặc cấu hình tạo lưới.'];
  }else{
    actions=['Mở Chi tiết kỹ thuật để xem lỗi gốc và hướng xử lý.'];
  }
  return {title,message,actions};
}
function renderFailureGuidance(job){
  const panel=$('monitor-failure'),{title,message,actions}=failureSummary(job);
  $('monitor-failure-title').textContent=title;
  $('monitor-failure-message').textContent=message;
  const list=$('monitor-failure-actions');list.replaceChildren();
  for(const action of actions){const item=document.createElement('li');item.textContent=action;list.append(item);}
  list.hidden=actions.length===0;
  panel.hidden=false;
}
function renderMonitor(job){
  const m=job.monitor||{},active=['running','stopping'].includes(job.status),gmshPercent=m.phase?.startsWith('gmsh_'),phase=stageNames[m.phase]||stageNames[job.stage]||job.stage;
  const failure=job.status==='fail'?failureSummary(job):null;
  $('job-monitor').hidden=false;monitorJobId=jobId;
  if(job.status==='fail')renderFailureGuidance(job);else $('monitor-failure').hidden=true;
  $('monitor-phase').textContent=job.status==='stopping'?'Đang dừng…':job.status==='pass'?'Hoàn thành':job.status==='cancelled'?'Đã dừng':job.status==='fail'?'Thất bại':phase;
  $('monitor-elapsed').textContent=duration(m.elapsed_seconds);
  const known=Number.isFinite(m.phase_percent);
  $('monitor-percent').textContent=!active?'—':known?`${m.phase_percent.toFixed(0)}%${gmshPercent?' · trong log Gmsh':''}`:gmshPercent||m.phase==='meshing'?'Gmsh chưa báo phần trăm':'Chưa có số liệu';
  $('phase-progress').hidden=!active||!known;if(known)$('phase-progress').value=m.phase_percent;
  $('progress').hidden=!active||known;
  $('cancel-run').disabled=!active||job.status==='stopping'||m.cancel_requested||cancelPending;
  $('cancel-run').textContent=job.status==='cancelled'?'Đã dừng':active&&(job.status==='stopping'||m.cancel_requested)?'Đang dừng…':'Dừng tác vụ';
  $('monitor-eta').textContent=m.eta&&active&&!gmshPercent?`${duration(m.eta.low_seconds)} – ${duration(m.eta.high_seconds)} · ${stageNames[m.eta.scope]||m.eta.scope}`:active?'Chưa đủ dữ liệu để ước tính':'—';
  $('job-log-identity').textContent=`Tác vụ ${jobId||'—'} · ${extractMode?'Extract thuộc tính':'Tạo lưới'}${m.has_gmsh_log?' · Nhật ký Gmsh':' · Tác vụ này không phát sinh log Gmsh'}`;
  if(extractMode){$('extract-status').className='info '+(job.status==='fail'?'error':job.status==='pass'?'success':'');$('extract-status').textContent=job.status==='pass'?'Đã xử lý xong. Đang tải và kiểm tra kết quả…':job.status==='cancelled'?'Đã dừng extract. Lưới đã tạo vẫn được giữ nguyên.':job.status==='fail'?`${failure.title}: ${failure.message}`:$('monitor-phase').textContent;}
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
function downloadQuality(_path,payload){
  const extension=payload.format==='csv'?'zip':payload.format;
  return managedDownload({url:'/api/quality/export',filename:`meshcherry-quality.${extension}`,method:'POST',body:JSON.stringify(payload),kind:'quality',payload,fetchOptions:{headers:{'Content-Type':'application/json'}}});
}
function setupQualityPanel(){
  if(qualityPanel||!window.MeshCherryQualityPanel||!window.MeshCherryQualityBridge)return;
  qualityBridge=MeshCherryQualityBridge.create({api,download:downloadQuality,onStatus:status=>{setQualityBusy(['running','stopping','queued','pending'].includes(status.status||status.state));qualityPanel?.setStatus?.(status);}});
  qualityPanel=MeshCherryQualityPanel.mount($('quality-panel-host'),{
    evaluate:async config=>{if(busy)throw Error('Hãy chờ tác vụ hiện tại kết thúc trước khi chạy QA.');setQualityBusy(true);try{const result=await qualityBridge.run(config,false);qualityReport=result;qualityOverlayMetric='quality_q';drawQualityOverlay();return result;}finally{setQualityBusy(false);}},benchmark:async config=>{if(busy)throw Error('Hãy chờ tác vụ hiện tại kết thúc trước khi benchmark.');setQualityBusy(true);try{const result=await qualityBridge.run(config,true);qualityReport=result;qualityOverlayMetric='quality_q';drawQualityOverlay();return result;}finally{setQualityBusy(false);}},fetchPage:args=>qualityBridge.fetchPage(args),
    onExport:(format)=>qualityBridge.exportResult(format).then(result=>{if(result?.state==='error')toast('Không xuất được QA: '+result.error);}).catch(error=>toast('Không xuất được QA: '+error.message)),
    onStop:()=>qualityBridge.stop(),onStatus:status=>setQualityBusy(['running','stopping','queued','pending'].includes(status.status||status.state)),onMetric:metric=>{qualityOverlayMetric=metric;if(metric===null&&fitBox)$('map').setAttribute('viewBox',fitBox.join(' '));drawQualityOverlay();},
    onFocus:(id)=>{const cell=geometry.find(item=>String(item.cell_id)===String(id));if(!cell)return;selectCell(cell);const [xmin,ymin,xmax,ymax]=meshBounds([cell]);const extent=Math.max(xmax-xmin,ymax-ymin,1e-9),pad=extent*.75,width=extent+2*pad,height=extent+2*pad,mapX=xmin-mapOriginX-pad,mapY=mapOriginY-ymax-pad;$('map').setAttribute('viewBox',[mapX,mapY,width,height].join(' '));}
  });
  $('quality-open').addEventListener('click',()=>{$('quality-panel-host').hidden=!$('quality-panel-host').hidden;$('quality-open').textContent=$('quality-panel-host').hidden?'Mở kiểm định':'Thu gọn kiểm định';});
}
function qualityControls(){return [...$('form').querySelectorAll('input,select,button'),...$('extract-options').querySelectorAll('input,select,button'),...document.querySelectorAll('button[data-stage],#start-another,#project-open,#project-save,#gmsh-setup-open,#tutorial-help')].filter((node,index,nodes)=>nodes.indexOf(node)===index);}
function setQualityBusy(value){
  if(Boolean(value)===qualityBusy)return;qualityBusy=Boolean(value);
  if(qualityBusy){qualityDisabledBefore=qualityControls().map(node=>[node,node.disabled]);for(const [node] of qualityDisabledBefore)node.disabled=true;}
  else{for(const [node,disabled] of qualityDisabledBefore)if(node.isConnected)node.disabled=disabled;qualityDisabledBefore=[];}
  document.body.dataset.qualityBusy=String(qualityBusy);
}
function setQualityMesh(mesh){
  if(!window.MeshCherryQualityPanel||!window.MeshCherryQualityBridge)return;
  setupQualityPanel();const next=mesh?.jobId||null;
  if(next!==qualityMeshId){qualityReport=null;qualityOverlayMetric=null;qualityPanel?.clear();const map=$('map');map?.querySelector?.('.qa-heatmap')?.remove();}
  qualityMeshId=next;qualityBridge?.setMesh(next);
  if(!mesh){$('quality-entry').hidden=true;return;}
  $('quality-entry').hidden=false;
  const expected=next;
  qualityBridge.restore(expected).then(report=>{
    if(!report||qualityMeshId!==expected||meshSnapshot?.jobId!==expected)return;
    qualityReport=report;qualityOverlayMetric='quality_q';qualityPanel?.load(report);drawQualityOverlay();
  }).catch(error=>{if(qualityMeshId===expected)toast('Không khôi phục được kiểm định đã lưu: '+error.message);});
}
function drawQualityOverlay(){
  if(!qualityReport){const map=$('map');map?.querySelector?.('.qa-heatmap')?.remove();const legend=$('quality-legend');if(legend?.replaceChildren){legend.replaceChildren();legend.hidden=true;}return;}
  const map=$('map');if(!map?.querySelector||!map?.insertBefore)return;
  map.querySelector('.qa-heatmap')?.remove();
  const legend=$('quality-legend');if(!legend?.replaceChildren)return;legend.replaceChildren();legend.hidden=true;
  if(!qualityOverlayMetric||!meshSnapshot?.cells?.length||meshSnapshot.cells.length>100000)return;
  const key=qualityOverlayMetric,values=new Map((qualityReport.elements||[]).map(row=>[String(row.element_id),row[key]]));
  const finite=[...values.values()].filter(value=>typeof value==='number'&&Number.isFinite(value)).sort((a,b)=>a-b);
  if(!finite.length)return;
  const lowIsBad=['quality_q','min_angle_deg'].includes(key),palette=lowIsBad?['#922c58','#d94f5d','#ed845c','#f8bd66','#fff0c2']:['#fff0c2','#f8bd66','#ed845c','#d94f5d','#922c58'],quantile=q=>finite[Math.min(finite.length-1,Math.floor((finite.length-1)*q))],constant=finite[0]===finite[finite.length-1],cuts=constant?[]:[...new Set([.2,.4,.6,.8].map(quantile))].filter(value=>value>finite[0]&&value<finite[finite.length-1]),bucketCount=cuts.length+1,colors=constant?['#b8b0a5']:Array.from({length:bucketCount},(_,index)=>palette[Math.round(index*4/Math.max(1,bucketCount-1))]);
  const group=document.createElementNS('http://www.w3.org/2000/svg','g');group.setAttribute('class','qa-heatmap');group.setAttribute('aria-hidden','true');group.style.pointerEvents='none';
  const xmin=mapOriginX,ymax=mapOriginY;
  const bucketPaths=colors.map(()=>[]),missingPaths=[];
  for(const cell of meshSnapshot.cells){const value=values.get(String(cell.cell_id)),coords=cell.xy.map(([x,y])=>[x-xmin,ymax-y]),path=`M${coords.map(point=>point.join(' ')).join('L')}Z`;
    if(typeof value==='number'&&Number.isFinite(value)){const bucket=constant?0:cuts.findIndex(cut=>value<=cut);bucketPaths[bucket<0?bucketCount-1:bucket].push(path);}
    else missingPaths.push(path);
  }
  colors.forEach((color,index)=>{if(!bucketPaths[index].length)return;const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',bucketPaths[index].join(''));path.setAttribute('fill',color);path.setAttribute('fill-opacity','.68');group.append(path);});
  if(missingPaths.length){const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',missingPaths.join(''));path.setAttribute('fill','#8e8588');path.setAttribute('fill-opacity','.38');path.setAttribute('stroke','#51494c');path.setAttribute('stroke-dasharray','2 2');path.setAttribute('stroke-width','.2');group.append(path);}
  const nodeLayer=map.querySelector('.cell-node')?.parentNode||map.querySelector('.batch-nodes');map.insertBefore(group,nodeLayer||null);
  legend.hidden=false;const title=document.createElement('strong'),label=MeshCherryQualityPanel.METRICS.find(metric=>metric[0]===key)?.[1]||key;title.textContent=`QA · ${label} · phân bố theo phân vị tương đối`;legend.append(title);
  colors.forEach((color,index)=>{const item=document.createElement('span'),swatch=document.createElement('i');swatch.style.backgroundColor=color;const binLabel=constant?`Không đổi: ${concise(finite[0])}`:index===0?`≤ ${concise(cuts[0])}`:index===bucketCount-1?`> ${concise(cuts[index-1])}`:`${concise(cuts[index-1])} – ${concise(cuts[index])}`;item.append(swatch,document.createTextNode(binLabel));legend.append(item);});
  const missing=document.createElement('span'),missingSwatch=document.createElement('i');missingSwatch.style.backgroundColor='#8e8588';missing.append(missingSwatch,document.createTextNode('N/A'));legend.append(missing);
}
function loadQualityReport(result){qualityReport=result;qualityPanel?.load(result);drawQualityOverlay();}
function readableExtractError(message){const text=String(message||'');if(/band.*out of range/i.test(text))return 'Band đã chọn không có trong raster. Chọn band từ 1 đến số band của tệp.';return text;}
function toast(message){clearTimeout(toastTimer);$('toast').textContent=message;$('toast').hidden=false;toastTimer=setTimeout(()=>{$('toast').hidden=true;},7000);}
function resetResult(){
  jobLogCursor=0;monitorJobId=null;cancelPending=false;$('job-log').textContent='';$('log-notice').textContent='';$('job-monitor').hidden=true;$('monitor-failure').hidden=true;$('job-log-download').hidden=true;$('cancel-run').disabled=true;
  terminalArtifactFailureJobId=null;terminalArtifactFailureCount=0;terminalArtifactIncompleteJobId=null;terminalArtifactRetryButton?.remove?.();terminalArtifactRetryButton=null;
  savedMap=null;resultTab='mesh';document.body.dataset.result='mesh';
  jobId=null;resultPath='';selectedCellId=null;fitBox=null;mapUnits='';mapCRS='';
  $('exports').hidden=true;$('msh').hidden=true;$('mesh-json').hidden=true;
  if($('project-history'))$('project-history').hidden=true;
  $('progress').hidden=true;jobHeadline.textContent='Chưa có lượt chạy.';
  $('job-status').className='info muted';$('job-status').textContent='Chưa có lượt chạy.';
  draw([]);$('map').removeAttribute('viewBox');$('map-crs').textContent='X,Y · mét';
  $('coverage-summary').classList.remove('coverage-warning');$('cell-info').className='info muted';
}
function startAnother(){
  if(busy||batchJobId||batchStartUncertain||batchRecoveryBlocked)return;
  resetBatchResultsForMesh(null);
  preserveAcceptedDuringRun=false;
  clearInputPreview();
  sourceRequest++;boundaryRequest++;exitTutorial();tutorialClicks.clear();visitedStages.clear();
  source=null;plannedSource=null;plannedRequest++;$('planned-raster').value='';meshSnapshot=null;clearAcceptedMesh();extractMode=false;invalidate();resetResult();
  $('raster').value='';$('mesh').value='';$('boundary-path').value='';$('gmsh-domain').value='rectangle';
  $('source-info').className='info muted';$('boundary-info').className='info';
  $('source-summary').textContent='Chọn GeoTIFF / ASC của bạn.';$('source-diagnostics-content').replaceChildren();$('source-diagnostics').open=false;$('source-warning').hidden=true;$('source-warning').textContent='';$('boundary-info').textContent='Chưa đọc ranh giới.';
  mode();helpers();stage(1);
}
function exitTutorial(){tutorialActive=false;$('tutorial-finish').hidden=true;tutorialHint();}
async function refreshRecent(){
  const {items}=await api('/api/recent');$('recent-list').replaceChildren();$('recent-history-list').replaceChildren();$('recent-files').hidden=!items.length;$('recent-history').hidden=items.length<=3;
  for(const [index,item] of items.entries()){const button=document.createElement('button');button.type='button';button.className='recent-file';button.title=item.name;
    const name=document.createElement('strong'),path=document.createElement('small');name.textContent=({raster:'GeoTIFF / ASC',mesh:'Mesh',boundary:'Ranh giới SHP'})[item.kind]+' · '+item.name;const date=new Date(item.opened);path.textContent='Mở gần nhất: '+(Number.isNaN(date.getTime())?'Không rõ thời gian':new Intl.DateTimeFormat('vi-VN',{dateStyle:'short',timeStyle:'short'}).format(date));button.append(name,path);
    button.onclick=async()=>{if(busy)return;try{const selected=await api('/api/recent',{id:item.id});const target=selected.kind==='mesh'?'mesh':selected.kind==='boundary'?'boundary-path':'raster';await acceptSelectedSource(target,selected.path);}catch(error){toast('Không mở được tệp gần đây: '+error.message);}};(index<3?$('recent-list'):$('recent-history-list')).append(button);
  }
}
$('recent-clear').onclick=async()=>{if(busy)return;try{await api('/api/recent',{action:'clear'});await refreshRecent();}catch(error){toast(error.message);}};
function invalidate(){clearComparison();if(!preserveAcceptedDuringRun){meshSnapshot=null;clearAcceptedMesh();$('mesh-quality').hidden=true;setQualityMesh(null);}extractMode=false;$('extract-options').hidden=true;revision++;preflightId=null;if(!busy&&!preserveAcceptedDuringRun)resetResult();$('run').disabled=true;$('validation').className='info muted';$('validation').textContent='Cấu hình đã thay đổi. Hãy kiểm tra lại trước khi chạy.';refreshCoach();}
function mode(){
  if(previewKey&&previewKey!==previewIdentity())clearInputPreview();
  const domain=$('gmsh-domain').value,shp=domain==='shapefile';
  if(shp)$('mode').value='gmsh_generated';
  const value=$('mode').value,sf1=value==='gmsh_generated'&&$('gmsh-sizing').value==='sf1_relief',relief=value==='gmsh_generated'&&['relief','sf1_relief'].includes($('gmsh-sizing').value);
  const advancedEligible=value==='gmsh_generated'&&['uniform','relief'].includes($('gmsh-sizing').value);
  $('gmsh-advanced').hidden=!advancedEligible;
  for(const id of ['gmsh-algorithm','gmsh-smoothing','gmsh-optimize'])$(id).disabled=!advancedEligible||!gmshAdvancedEnabled;
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
  if(relief&&currentStage===2&&$('dem-raster').value.trim()){if(demPreviewKey!==demPreviewIdentity())loadDemPreview();}else clearDemPreview();
  $('h-max-help').textContent=sf1?'Hồ sơ SF-1 cố định 1000 m. h_scaled = clip(scale × h_raw, 250, 1000), sau đó giới hạn gradation.':relief?'Nhập số dương ≥ h_min. Vùng ít relief, thiếu DEM hoặc relief không đổi dùng h_max.':'Nhập kích thước mục tiêu theo mét, ví dụ 250. Kích thước cạnh thực tế được thống kê sau khi tạo lưới.';
  const subdivisionsRelevant=extractMode||(domain==='raster'&&!sameCRS());
  $('subdivisions').hidden=!subdivisionsRelevant;$('subdivisions-label').hidden=!subdivisionsRelevant;
  $('subdivisions-help').textContent=shp?'SHP đổi CRS: cố định 8 đoạn/cạnh nguồn. Giá trị footprint GeoTIFF / ASC không điều khiển SHP.':subdivisionsRelevant?'1–64, mặc định 8: xấp xỉ cạnh footprint GeoTIFF / ASC qua CRS; không trực tiếp tăng mật độ mesh.':'Miền cùng CRS hoặc chữ nhật: không cần xấp xỉ cạnh qua CRS.';
  $('resolution-options').hidden=value==='mesh_artifact';$('planned-options').hidden=domain==='raster';
  for(const card of document.querySelectorAll('.formula-relief'))card.hidden=!relief||sf1;
  for(const card of document.querySelectorAll('.formula-sf1'))card.hidden=!sf1;
  const required=new Set(['crs','output']);
  if(value==='mesh_artifact')required.add('mesh');
  else if(shp)required.add('boundary-path');
  else if(domain==='raster'){required.add('raster');required.add('band');}
  else for(const id of ['west','south','east','north'])required.add(id);
  if(value==='structured_grid'){required.add('width');required.add('height');}
  if(value==='gmsh_generated'){required.add('gmsh-exe');required.add('h-max');if(relief)for(const id of ['dem-raster','dem-band','h-min'])required.add(id);if(sf1)required.add('sf1-scale');else if(relief)required.add('relief-radius');}
  for(const input of $('form').querySelectorAll('input,select')){input.required=required.has(input.id);input.setAttribute('aria-required',String(input.required));}
  for(const label of document.querySelectorAll('label[for]'))label.dataset.required=String(required.has(label.getAttribute('for')));
  renderFixedPolicy();parameterPreview();renderResolution();syncDocumentControls();
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
  if($('gmsh-sizing').value==='sf1_relief'){renderNotes('parameter-preview',['Hai cửa sổ relief: 150 m và 450 m.','Kích thước gốc: 250–1.000 m; điều chỉnh bằng Scale SF-1.','Gradation: 1,5 mỗi 250 m · lưới điều khiển: 120 m.']);return;}
  const relief=$('gmsh-sizing').value==='relief',lo=Number($('h-min').value),hi=Number($('h-max').value),gamma=bootstrap?.meshing_parameters?.gamma;
  renderNotes('parameter-preview',relief&&gamma&&lo>0&&hi>=lo?[
    'Relief thấp · C = 0 → '+concise(hi)+' m',
    'Relief trung gian · C = 0,5 → '+concise(hi-(hi-lo)*Math.pow(.5,gamma))+' m',
    'Relief cao · C = 1 → '+concise(lo)+' m']:
    ['Kích thước mục tiêu h theo mét.','Kích thước cạnh thực tế được thống kê sau khi tạo lưới.']);
}
function renderNotes(id,lines){const list=document.createElement('ul');list.className='readable-notes';for(const text of lines){const row=document.createElement('li');row.textContent=text;list.append(row);}$(id).replaceChildren(list);}
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
  const reference=$('roi-reference-strip'),referenceInfo=$('roi-reference-info'),referenceVisible=$('gmsh-domain').value!=='raster'&&$('mode').value!=='mesh_artifact';
  reference.hidden=!referenceVisible;referenceInfo.replaceChildren();
  if(referenceVisible){
    const metadata=activeResolution()&&plannedSource;
    if(metadata){
      const resolution=metadata.resolution,unit=resolution.native_units==='degree'?'°':resolution.native_units;
      detailRows(referenceInfo,[['Nguồn / band',metadata.path.split(/[\\/]/).pop()+' / '+metadata.band],['Pixel X × Y',resolution.native_pixel_size.map(concise).join(' × ')+' '+unit],['Kích thước',metadata.shape?.[1]+' × '+metadata.shape?.[0]+' px'],['CRS nguồn',metadata.crs||resolution.source_crs]]);
    }else referenceInfo.textContent='Chưa chọn raster tham chiếu';
  }
  $('apply-resolution').disabled=busy||!r;$('resolution-warning').textContent='';
  if(!r){renderNotes('resolution-warning',['ROI vector/chữ nhật: chọn kích thước theo bài toán.','Raster tham chiếu độ phân giải là tùy chọn khi chia đều.']);return;}
  const [column,row]=r.representative_pixel_size_m,kind=$('mode').value,relief=['relief','sf1_relief'].includes($('gmsh-sizing').value);
  const sizes=kind==='structured_grid'?[Number($('width').value),Number($('height').value)]:relief?[Number($('h-min').value),Number($('h-max').value)]:[Number($('h-max').value)];
  const ratios=kind==='structured_grid'?'Rộng/cột = '+concise(sizes[0]/column)+'×; cao/hàng = '+concise(sizes[1]/row)+'×.':sizes.map((v,i)=>(relief?(i?'h_max':'h_min'):'h')+'/pixel cột, hàng = '+concise(v/column)+'×, '+concise(v/row)+'×.').join(' ');
  const smaller=kind==='structured_grid'?sizes[0]<column||sizes[1]<row:sizes.some(v=>v<Math.max(column,row));
  renderNotes('resolution-warning',[ratios,smaller?'Raster giữ nguyên độ phân giải gốc khi ô lưới nhỏ hơn pixel.':'Một ô có thể tổng hợp nhiều pixel.','Gợi ý: đều 1× bước pixel lớn hơn; relief 1×–4×. Bấm Áp dụng để cập nhật.']);
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
function requireDomainSelection(){
  if(extractMode)return;
  const domain=$('gmsh-domain').value;
  const fail=(field,message)=>{const error=new Error(message);error.sourceField=field;throw error;};
  if($('mode').value==='mesh_artifact'){
    if(!$('mesh').value.trim())fail('mesh','Chọn mesh JSON đầu vào ở bước 2.');
    return;
  }
  if(!['gmsh_generated','structured_grid'].includes($('mode').value))return;
  if(domain==='raster'&&!$('raster').value.trim())fail('raster','Chọn GeoTIFF hoặc ASC làm ranh giới ROI ở bước 1.');
  if(domain==='shapefile'&&!$('boundary-path').value.trim())fail('boundary-path','Chọn bộ Shapefile ranh giới ROI ở bước 1 (.shp, .shx, .dbf, .prj).');
  if(domain==='rectangle'){
    const ids=['west','south','east','north'];
    const missing=ids.find(id=>!$(id).value.trim()||!Number.isFinite(Number($(id).value)));
    if(missing)fail(missing,'Nhập đủ bốn tọa độ hữu hạn cho miền chữ nhật ở bước 2.');
    if(Number($('east').value)<=Number($('west').value))fail('east','X lớn nhất phải lớn hơn X nhỏ nhất.');
    if(Number($('north').value)<=Number($('south').value))fail('north','Y lớn nhất phải lớn hơn Y nhỏ nhất.');
  }
  if($('mode').value==='gmsh_generated'&&!$('gmsh-exe').value.trim())fail('gmsh-exe','Chọn gmsh.exe và kiểm tra phiên bản trong Thiết lập Gmsh.');
}
function showSourceFieldError(error){
  const id=error.sourceField,isSource=['raster','boundary-path'].includes(id);
  stage(isSource?1:2,false);
  const host=$(id==='raster'?'source-warning':id==='boundary-path'?'boundary-info':'mesh-field-error');
  if(id==='raster')host.className='field-error';else host.className='info error';
  host.textContent=error.message;host.hidden=false;host.dataset.errorField=id;
  $(id).setAttribute('aria-invalid','true');$(id).focus();
}
function clearSourceFieldError(id){
  $(id).removeAttribute('aria-invalid');
  for(const name of ['source-warning','boundary-info','mesh-field-error']){
    const host=$(name);if(host.dataset.errorField!==id)continue;
    delete host.dataset.errorField;host.textContent='';host.className=name==='source-warning'?'field-error':'info muted';if(name!=='boundary-info')host.hidden=true;
  }
}
function config(){
  requireDomainSelection();
  if(extractMode){
    if(!meshSnapshot)throw new Error('Chưa có lưới');
    return {schema_version:'gis-workbench-run-config-v2',operation:'extract',extraction_engine:'indexed_windowed_v1',run_name:'mesh-cherry-extract',analysis_crs:meshSnapshot.crs,raster_path:$('extract-raster').value.trim(),band:number('extract-band'),semantics:$('extract-semantics').value,value_units:$('extract-units').value.trim(),output_root:$('output').value,edge_subdivisions:number('subdivisions'),cell_source:{kind:'frozen_cells',path:meshSnapshot.path,geometry_digest:meshSnapshot.digest,max_cells:acceptedExtractionCellLimit()}};
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
    if(gmshAdvancedEnabled&&['uniform','relief'].includes(cell_source.sizing)){
      const smoothing=Number($('gmsh-smoothing').value),optimize=Number($('gmsh-optimize').value),algorithm=Number($('gmsh-algorithm').value);
      if(!Number.isInteger(smoothing)||smoothing<0||smoothing>10||!Number.isInteger(optimize)||optimize<0||optimize>10||![5,6].includes(algorithm))throw new Error('Tham số Gmsh nâng cao không hợp lệ (thuật toán 5/6; số lần 0–10).');
      cell_source.advanced={algorithm_2d:algorithm,smoothing,optimize_2d:optimize};
    }
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
function acceptedExtractionCellLimit(){
  const count=Number(meshSnapshot?.display?.cell_count??meshSnapshot?.cells?.length??0);
  return Number.isSafeInteger(count)&&count>0?Math.min(count,100000):100000;
}
async function inspectSource(){
  const request=++sourceRequest,crs=$('crs').value.trim();
  source=null;helpers();$('source-info').className='info muted';$('source-summary').textContent='Đọc metadata…';$('source-warning').hidden=true;$('source-warning').textContent='';$('source-diagnostics-content').replaceChildren();
  const path=$('raster').value.trim(),band=number('band');
  const current=()=>request===sourceRequest&&path===$('raster').value.trim()&&band===Number($('band').value)&&crs===$('crs').value.trim();
  try{const meta=await api('/api/source',{path,band,analysis_crs:crs});if(!current())return null;
    source=meta;$('source-info').className='info';$('source-summary').replaceChildren();$('source-diagnostics-content').replaceChildren();
    const filename=document.createElement('span');filename.className='file-path';filename.textContent=path;filename.title=path;$('raster').title=path;$('source-summary').append(filename);
    const pixel=meta.resolution?.native_pixel_size||meta.pixel_size||null,units=meta.resolution?.native_units||meta.coordinate_units||'unknown';
    detailRows($('source-summary'),[['Pixel X × Y',pixel?pixel.map(concise).join(' × ')+' '+units:'Chưa có số liệu'],['CRS nguồn',meta.crs||'THIẾU CRS'],['Band · kích thước',`${meta.band}/${meta.band_count} · ${meta.shape[1]} × ${meta.shape[0]} px`],['NoData / mask',`${meta.nodata} / ${meta.mask.join(', ')}`]]);
    detailRows($('source-diagnostics-content'),[['Định dạng',meta.driver==='AAIGrid'?'ESRI ASCII Grid (.asc)':'GeoTIFF'],['Kích thước',`${meta.shape[1]} × ${meta.shape[0]} pixel`],['Miền nguồn · X',`${meta.bounds[0]} → ${meta.bounds[2]}`],['Miền nguồn · Y',`${meta.bounds[1]} → ${meta.bounds[3]}`],['Band / kiểu',`${meta.band}/${meta.band_count} · ${meta.dtype}`],['NoData / mask',`${meta.nodata} / ${meta.mask.join(', ')}`],['Scale / offset',`${meta.scale} / ${meta.offset}`]]);
    if(!meta.crs){$('source-warning').textContent='Raster chưa có CRS. Với ASC, đặt file .prj cùng tên cạnh .asc. Nhập CRS tính toán không sửa được lỗi này.';$('source-warning').hidden=false;$('source-info').className='info error';}
    if(meta.resolution)detailRows($('source-diagnostics-content'),resolutionRows(meta.resolution));
    helpers();renderResolution();await refreshRecent();await loadInputPreview();return meta;
  }catch(error){if(!current())return null;$('source-info').className='info error';$('source-summary').textContent=error.message;$('source-warning').hidden=true;throw error;}
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
  const [xmin,ymin,xmax,ymax]=meshBounds(cells,boundary);mapOriginX=xmin;mapOriginY=ymax;const span=Math.max(xmax-xmin,ymax-ymin),pad=span*.07;
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
  if(qualityBusy){toast('Hãy chờ kiểm định hiện tại kết thúc trước khi đổi cấu hình.');return;}
  const requestRevision=revision;let cfg;$('validate').disabled=true;$('run').disabled=true;preflightId=null;$('validation').textContent='Đang kiểm tra metadata, hình học và workload…';if(extractMode)$('extract-detail-content').replaceChildren();
  try{requireDomainSelection();if(!extractMode&&!checkParameterFields())throw new Error('Sửa các ô được đánh dấu trước khi kiểm tra.');cfg=config();const data=await api('/api/preflight',cfg);if(requestRevision!==revision){toast('Cấu hình thay đổi trong khi kiểm tra. Hãy kiểm tra lại.');return;}
    const report=data.report;$('validation').className='info '+(report.status==='pass'?'pass':'error');
    const estimate=report.workload_estimate;
    $('validation').textContent=report.status==='pass'?`✓ Preflight đạt\nEngine: indexed_windowed_v1\nCell: ${report.cell_count===null?'tạo khi meshing':report.cell_count}\nCRS cell / analysis: ${$('crs').value} · metre\nRaster: ${source?.crs||'WKT ghi trong hồ sơ'}\nSource: ${report.pixel_count.toLocaleString('vi-VN')} pixel\nDự kiến đọc: ${estimate?estimate.estimated_pixels_read.toLocaleString('vi-VN')+' pixel / '+estimate.estimated_windows_read+' window':'kiểm tra sau khi sinh mesh'}\nCeilings: source 10M · đọc 2M · pairs 5M\nGiới hạn cell: ${cfg.cell_source.max_cells} · window ≤ 65.536`:report.errors.join('\n');
    if(report.status==='pass'){
      const rows=[['Nguồn',`${report.pixel_count.toLocaleString('vi-VN')} pixel`],['CRS tính toán',$('crs').value],['Raster',cfg.raster_path||'—'],['Band',cfg.band??'—'],['Loại thuộc tính',cfg.semantics||'—'],['Đơn vị',cfg.value_units||'—'],['Mã hình học',report.geometry_digest||'—'],['Số ô',report.cell_count??'Tạo khi meshing'],['Dự kiến đọc',estimate?`${estimate.estimated_pixels_read.toLocaleString('vi-VN')} pixel / ${estimate.estimated_windows_read} cửa sổ`:'Kiểm tra sau khi sinh mesh'],['Giới hạn',`${cfg.cell_source.max_cells} ô; nguồn 10M / đọc 2M pixel; 5M cặp; 65.536 pixel/cửa sổ`]];
      $('validation').replaceChildren();const heading=document.createElement('strong');heading.textContent='✓ Kiểm tra cấu hình đạt';$('validation').append(heading);detailRows($('validation'),rows);
      if(extractMode){$('extract-detail-content').replaceChildren();detailRows($('extract-detail-content'),rows);}
    }
    if(report.status==='pass'&&extractMode&&report.raster_resolution){$('extract-resolution').replaceChildren();detailRows($('extract-resolution'),resolutionRows(report.raster_resolution));const note=document.createElement('p');note.textContent='Đây là độ phân giải raster extract thực tế. Giữ nguyên ô và node của lưới đã lưu, không chia lại.';$('extract-resolution').append(note);}
    let footprint=null;
    if(report.status==='pass'&&config().raster_path){const cfg=config();const metadata=await api('/api/source',{path:cfg.raster_path,band:cfg.band,analysis_crs:cfg.analysis_crs});footprint=metadata.display_footprint;if(requestRevision!==revision)return;}
    if(report.status==='pass'&&report.operation==='mesh_only'){
      $('validation').replaceChildren();detailRows($('validation'),[['Trạng thái','Cấu hình tạo lưới hợp lệ'],...requestRows(report.meshing_request),['Số ô',report.cell_count??'Tính khi Gmsh tạo lưới']]);
    }
    preflightId=report.status==='pass'?data.preflight_id:null;$('run').disabled=!preflightId||busy;if(!extractMode)draw(data.cells,[],{crs:$('crs').value,units:$('units').value,footprint,display:data.display});refreshCoach();
    return preflightId?{preflightId,revision:requestRevision}:null;
  }catch(error){if(requestRevision!==revision)return;$('validation').className='info error';$('validation').textContent=error.message;
    if(error.sourceField)showSourceFieldError(error);
  }finally{$('validate').disabled=busy;if(extractMode){const ok=!!preflightId;$('extract-status').className='info '+(ok?'success':'error');$('extract-status').textContent=ok?'✓ Kiểm tra đạt. Đang chuẩn bị extract.':readableExtractError($('validation').textContent)||'Chưa kiểm tra được raster. Hãy thử lại.';if(!ok)$('extract-detail-content').textContent=$('validation').textContent;}}
}
function setBusy(value){busy=value;document.body.dataset.busy=String(value);for(const el of $('extract-options').querySelectorAll('input,select,button'))el.disabled=value;for(const input of $('form').querySelectorAll('input,select,button'))input.disabled=value;for(const control of document.querySelectorAll('button[data-stage],#gmsh-setup-open,#tutorial-help'))control.disabled=value;$('menu-edit-mesh').disabled=value;$('menu-quality-open').disabled=value||!meshSnapshot;if(!value){mode();helpers();$('run').disabled=!preflightId;$('tutorial-finish').disabled=!tutorialActive||tutorialClicks.size!==2;}$('progress').hidden=!value;$('cancel-run').disabled=!value||!jobId||cancelPending;syncDocumentControls();updateExtractReadiness();updateBatchReadiness();refreshCoach();}
async function startRun(){
  if(!preflightId||busy||batchJobId||qualityBusy)return;const id=preflightId;preflightId=null;if(!preserveAcceptedDuringRun)resetResult();
  if(extractMode&&meshSnapshot){draw(meshSnapshot.cells,[],{crs:meshSnapshot.crs,units:'',display:meshSnapshot.display});showResultTab('extract');}
  setBusy(true);if(!preserveAcceptedDuringRun)$('exports').hidden=true;$('job-status').className='info';$('job-status').textContent='Đang khởi chạy…';jobHeadline.textContent='Đang khởi chạy…';
  $('job-monitor').hidden=false;$('monitor-failure').hidden=true;$('monitor-phase').textContent='Đang khởi chạy…';$('monitor-elapsed').textContent='0 giây';$('monitor-eta').textContent='Chưa đủ dữ liệu để ước tính';$('monitor-percent').textContent='Chưa có phần trăm';$('phase-progress').hidden=true;
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
async function restoreSessionMesh(prefetched=null){
  let acceptedId;try{acceptedId=prefetched?.acceptedId||(typeof sessionStorage.getItem==='function'?sessionStorage.getItem(ACCEPTED_MESH_STORAGE_KEY):null);}catch{return false;}
  if(!acceptedId||!/^[A-Za-z0-9_-]{8,80}$/.test(acceptedId))return false;
  const [job,manifest,cells,qa]=prefetched?[prefetched.job,prefetched.manifest,prefetched.cells,prefetched.qa]:await Promise.all([api(`/api/job?id=${encodeURIComponent(acceptedId)}`),...['manifest','cells','qa'].map(name=>api(`/api/artifact?id=${encodeURIComponent(acceptedId)}&name=${name}${name==='cells'?'&view=1':''}`))]);
  if(job.status!=='pass'||manifest.status!=='pass'||manifest.operation!=='mesh_only'||typeof manifest.geometry_digest!=='string')throw Error('Lưới đã chấp nhận không còn xác thực được.');
  const output=job.result?.output||'';meshSnapshot={output,digest:manifest.geometry_digest,path:output?output+'/cells.json':'',hasMsh:!!manifest.gmsh_generated,jobId:acceptedId,cells:cells.cells,display:cells.display,crs:cells.crs_wkt,qa};jobId=acceptedId;resultPath=output;
  draw(cells.cells,[],{crs:cells.crs_wkt,units:'',display:cells.display});showMeshQuality(qa.mesh_quality);showRemeshDraftButton();showProjectHistory(qa);restorePendingBatchForMesh(acceptedId);
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
function offerTerminalArtifactRetry(expectedJobId){
  terminalArtifactRetryButton?.remove?.();
  const retry=document.createElement('button');retry.type='button';retry.textContent='Thử tải lại kết quả';
  retry.onclick=async()=>{
    if(busy||jobId!==expectedJobId)return;
    terminalArtifactFailureJobId=expectedJobId;terminalArtifactFailureCount=0;
    retry.disabled=true;setBusy(true);jobHeadline.textContent='Đang thử tải lại kết quả…';$('job-status').textContent='Đang thử tải lại kết quả…';
    if(extractMode)$('extract-status').textContent='Đang thử tải lại kết quả…';
    await pollJob(expectedJobId);
  };
  terminalArtifactRetryButton=retry;$('monitor-headline').append(retry);
}
async function pollJob(expectedJobId=jobId){
  let terminal=false;
  try{const job=await api('/api/job?id='+encodeURIComponent(expectedJobId));if(expectedJobId!==jobId)return;renderMonitor(job);await syncJobLog(expectedJobId);if(expectedJobId!==jobId)return;$('job-status').textContent=job.status==='fail'?`Thất bại · ${job.result?.failure?.title||job.result?.failed_stage||job.stage}\n${job.result?.error||'Không có thông tin lỗi kỹ thuật.'}\nDiagnostic: ${job.result?.output||'chưa tạo run'}`:stageNames[job.stage]||job.stage;jobHeadline.textContent=job.status==='stopping'?'Đang dừng… chờ bước hiện tại kết thúc.':job.status==='running'?`🍒 ${stageNames[job.monitor?.phase]||stageNames[job.stage]||job.stage}…`:job.status==='pass'?'Đã xử lý xong. Đang tải và kiểm tra kết quả…':job.status==='fail'?'':'Không hoàn tất. Mở chi tiết kỹ thuật để xem lỗi.';
    if(['running','stopping'].includes(job.status)){setTimeout(()=>{if(jobId===expectedJobId)pollJob(expectedJobId);},650);return;}
    terminal=true;resultPath=job.result?.output||'';
    if(job.status==='cancelled'){if((extractMode||preserveAcceptedDuringRun)&&meshSnapshot){restoreAcceptedMeshPresentation();extractMode=false;preserveAcceptedDuringRun=false;}else{$('exports').hidden=true;}$('job-status').className='info';$('job-status').textContent='Đã dừng theo yêu cầu. Kết quả dang dở không được dùng để xuất.';jobHeadline.textContent=meshSnapshot?'Đã dừng tác vụ mới. Lưới đã chấp nhận trước đó vẫn được giữ nguyên.':'Đã dừng tác vụ. Log được giữ để xem lại.';clearRunRequest();setBusy(false);if(meshSnapshot)stage(4,false);return;}
    if(job.status==='fail'){const technicalFailure=$('job-status').textContent,guidance=failureSummary(job);let failureHeadline=`${guidance.title}: ${guidance.message}`;if(extractMode&&meshSnapshot)failureHeadline=`Extract chưa hoàn tất: ${guidance.title}. Lưới đã lưu vẫn được giữ nguyên.`;else if(preserveAcceptedDuringRun&&meshSnapshot)failureHeadline=`Không tạo được lưới mới: ${guidance.title}. Lưới đã chấp nhận trước đó vẫn được giữ nguyên.`;if((extractMode||preserveAcceptedDuringRun)&&meshSnapshot){restoreAcceptedMeshPresentation();preserveAcceptedDuringRun=false;}$('job-status').className='info error';$('job-status').textContent=technicalFailure;jobHeadline.textContent=failureHeadline;clearRunRequest();setBusy(false);if(meshSnapshot)stage(4,false);return;}
    const manifest=resultPath?await api(`/api/artifact?id=${jobId}&name=manifest`):null;
    if(manifest.operation==='mesh_only'){
      const [cells,qa]=await Promise.all(['cells','qa'].map(name=>api(`/api/artifact?id=${jobId}&name=${name}&view=1`)));
      if(expectedJobId!==jobId)return;
      resetBatchResultsForMesh(jobId);meshSnapshot={output:resultPath,digest:manifest.geometry_digest,path:resultPath+'/cells.json',hasMsh:!!manifest.gmsh_generated,jobId,cells:cells.cells,display:cells.display,crs:cells.crs_wkt,qa};preserveAcceptedDuringRun=false;saveAcceptedMesh();
      draw(cells.cells,[],{crs:cells.crs_wkt,units:'',footprint:source?.display_footprint,display:cells.display});
      showMeshQuality(qa.mesh_quality);showProjectHistory(qa);showRemeshDraftButton();$('batch-panel').hidden=false;renderBatchQueue();destroyTable('single');
      $('job-status').textContent='Lưới đã lưu: '+resultPath;jobHeadline.textContent='✓ Đã tạo '+(cells.display?.cell_count??cells.cells.length)+' ô lưới.';
      $('exports').hidden=false;$('csv').hidden=true;$('json').hidden=true;
      $('msh').hidden=!manifest.gmsh_generated;$('mesh-json').hidden=false;
      $('extract-options').hidden=false;if(tutorialActive){$('extract-raster').value=$('raster').value;$('extract-units').value='synthetic-value';}clearRunRequest();setBusy(false);stage(4);showResultTab(tutorialActive?'extract':'mesh');return;
    }
    const [cells,attributes,qa]=await Promise.all(['cells','attributes','qa'].map(name=>api(`/api/artifact?id=${jobId}&name=${name}&view=1`)));
    let footprint=null,metadataWarning='';
    try{const metadata=await api('/api/source',{path:attributes.provenance.source_path,band:attributes.provenance.band,analysis_crs:attributes.provenance.analysis_crs_wkt});footprint=metadata.display_footprint;}
    catch{metadataWarning='\nCảnh báo: không tải được footprint hiển thị của raster; các thuộc tính đã kiểm tra và tệp xuất vẫn khả dụng.';}
    if(expectedJobId!==jobId)return;
    draw(cells.cells,attributes.cells,{crs:attributes.provenance.analysis_crs_wkt,units:attributes.provenance.value_units,footprint,display:cells.display,attributeSummary:attributes.summary});const hasAttributeTable=mountSingleAttributeTable();const evidence=manifest.engine_evidence;
    const cellCount=cells.display?.cell_count??cells.cells.length,fullCount=attributes.summary?.full_count??attributes.cells.filter(r=>coverageState(r).full).length;
    $('job-status').className='info pass';$('job-status').textContent=`✓ Hoàn thành · ${cellCount} cell\nValid coverage (mean): ${(qa.coverage.valid_coverage.mean*100).toFixed(2)}%\nFootprint coverage: ${(qa.coverage.footprint_coverage.mean*100).toFixed(2)}%\nThời gian run: ${manifest.runtime_seconds.toFixed(2)} s\nĐọc: ${evidence.pixels_read.toLocaleString('vi-VN')} pixel / ${evidence.windows_read} window\nCandidate pairs: ${evidence.candidate_pairs.toLocaleString('vi-VN')}\nPeak RSS lifetime: ${evidence.memory_metric.value===null?'unavailable':(evidence.memory_metric.value/1048576).toFixed(1)+' MiB'}\nBộ raster và file đi kèm: không đổi${attributes.qa.residual===undefined?'':'\nExtensive residual: '+attributes.qa.residual}\nOutput: ${resultPath}${metadataWarning}`;
    jobHeadline.textContent=`✓ ${cellCount} ô đã trích thuộc tính. ${fullCount} ô phủ đủ dữ liệu.`;
    terminalArtifactFailureJobId=null;terminalArtifactFailureCount=0;terminalArtifactIncompleteJobId=null;terminalArtifactRetryButton?.remove?.();terminalArtifactRetryButton=null;
    $('extract-status').className='info success';$('extract-status').textContent=jobHeadline.textContent+' Sẵn sàng xuất CSV / JSON.';
    $('exports').hidden=false;$('csv').hidden=hasAttributeTable;$('json').hidden=hasAttributeTable;clearRunRequest();setBusy(false);stage(4);showResultTab('extract');
    $('msh').hidden=!(meshSnapshot?.hasMsh||manifest.gmsh_generated);$('mesh-json').hidden=!(meshSnapshot||manifest.gmsh_generated);
  }catch(error){if(expectedJobId!==jobId)return;$('job-status').className='info error';$('job-status').textContent=`Không đọc được trạng thái/kết quả: ${error.message}.`;
    if(terminal){
      if(terminalArtifactFailureJobId!==expectedJobId){terminalArtifactFailureJobId=expectedJobId;terminalArtifactFailureCount=0;}
      terminalArtifactFailureCount++;
      const permanent=[400,403,404,410].includes(error.status),retryAutomatically=!permanent&&terminalArtifactFailureCount<MAX_TERMINAL_ARTIFACT_ATTEMPTS;
      if(retryAutomatically){
        jobHeadline.textContent='Lượt chạy đã kết thúc nhưng chưa tải đủ kết quả. Đang thử tải lại…';
        if(extractMode){$('extract-status').className='info error';$('extract-status').textContent=jobHeadline.textContent;}
        setTimeout(()=>{if(jobId===expectedJobId)pollJob(expectedJobId);},2000);
        return;
      }
      if((extractMode||preserveAcceptedDuringRun)&&meshSnapshot){restoreAcceptedMeshPresentation();if(extractMode)jobId=expectedJobId;preserveAcceptedDuringRun=false;}
      else $('exports').hidden=true;
      terminalArtifactIncompleteJobId=expectedJobId;
      clearRunRequest();setBusy(false);
      const reason=permanent?'Artifact không còn truy cập được.':'Đã hết số lần tự động thử tải.';
      jobHeadline.textContent='Lượt chạy đã kết thúc nhưng chưa tải đủ kết quả. '+reason+' Bạn có thể thử lại.';
      $('job-status').className='info error';$('job-status').textContent=`Không đọc được trạng thái/kết quả: ${error.message}.\n${reason}`;
      if(extractMode){$('extract-status').className='info error';$('extract-status').textContent=jobHeadline.textContent;}
      offerTerminalArtifactRetry(expectedJobId);if(meshSnapshot)stage(4,false);return;
    }
    jobHeadline.textContent='Mất kết nối UI — đang thử lại. Lượt chạy có thể vẫn tiếp tục; giữ nguyên cấu hình.';
    setTimeout(()=>{if(jobId===expectedJobId)pollJob(expectedJobId);},2000);
  }
}
async function download(name){
  const artifactJob=name==='gmsh_log'?monitorJobId:['msh','mesh','cells'].includes(name)&&meshSnapshot?meshSnapshot.jobId:jobId;
  const filename=({csv:'attributes.csv',msh:'mesh.msh',mesh:'mesh.json',cells:'cells.json',attributes:'attributes.json',gmsh_log:'gmsh.log'})[name]||`${name}.dat`;
  return managedDownload({url:`/api/artifact?id=${encodeURIComponent(artifactJob)}&name=${encodeURIComponent(name)}&download=1`,filename,kind:'artifact',payload:{job_id:artifactJob,name}});
}
async function browse(path){
  $('picker-error').textContent='';try{const data=await api('/api/fs/list',{path});pickerPath=data.path;$('browse-path').value=pickerPath;$('breadcrumbs').textContent=pickerPath;$('entries').replaceChildren();
    const parts=pickerPath.replaceAll('\\','/').split('/');if(parts.length>1){const parent=parts.slice(0,-1).join('/');const up=document.createElement('button');up.textContent='↖ Thư mục cha';up.onclick=()=>browse(parent);$('entries').append(up);}
    const extensions=['raster','extract-raster','batch-raster','dem-raster','planned-raster','comparison-raster'].includes(pickerTarget)?['.tif','.tiff','.asc']:pickerTarget==='boundary-path'?['.shp']:pickerTarget==='gmsh-exe'?['.exe']:['.json'];
    for(const entry of data.entries){if(entry.kind!=='directory'&&(pickerTarget==='output'||!extensions.includes(entry.extension)))continue;
      const button=document.createElement('button'),name=document.createElement('span'),size=document.createElement('small');name.textContent=(entry.kind==='directory'?'▸ ':'')+entry.name;size.textContent=entry.kind==='directory'?'Thư mục':(entry.size/1024).toFixed(1)+' KB';button.append(name,size);
      button.onclick=async()=>{if(entry.kind==='directory'){await browse(entry.path);return;}$('picker').close();try{await acceptSelectedSource(pickerTarget,entry.path);}catch(error){toast(error.message);}};$('entries').append(button);
    }
  }catch(error){$('picker-error').textContent=error.message;}
}
function restoreAcceptedMeshPresentation(){
  if(!meshSnapshot)return;
  jobId=meshSnapshot.jobId;resultPath=meshSnapshot.output||meshSnapshot.path.slice(0,-11);
  draw(meshSnapshot.cells,[],{crs:meshSnapshot.crs,units:'',display:meshSnapshot.display});showMeshQuality(meshSnapshot.qa.mesh_quality);showRemeshDraftButton();
  $('exports').hidden=false;$('csv').hidden=true;$('json').hidden=true;$('msh').hidden=!meshSnapshot.hasMsh;$('mesh-json').hidden=false;
  $('job-status').className='info';$('job-status').textContent='Lưới đã lưu: '+resultPath;$('batch-panel').hidden=false;renderBatchQueue();
  jobHeadline.textContent='Lưới đã sẵn sàng; chưa extract nguồn mới.';
}
function invalidateExtraction(){
  if(busy)return;
  clearExtractionDraft();
  revision++;preflightId=null;extractMode=false;
  restoreAcceptedMeshPresentation();
  $('extract-status').className='info muted';$('extract-status').textContent='Nguồn extract đã thay đổi. Bấm Kiểm tra & extract để chạy trên lưới hiện tại.';
}

function acceptPickerPath(target,path){$(target).value=path;clearSourceFieldError(target);if(target==='comparison-raster'){invalidateComparisonInventory();return;}if(target==='batch-raster')return;if(target==='extract-raster')invalidateExtraction();else invalidate();}
async function acceptSelectedSource(target,path){
  acceptPickerPath(target,path);
  if(target==='dem-raster')await loadDemPreview();
  if(['raster','boundary-path','mesh'].includes(target)){
    exitTutorial();
    if(target==='boundary-path'){$('mode').value='gmsh_generated';$('gmsh-domain').value='shapefile';}
    else if(target==='raster'){$('gmsh-domain').value='raster';}
    else $('mode').value='mesh_artifact';
    mode();stage(1,false);
  }
  if(target==='raster')await inspectSource();
  if(target==='boundary-path')await inspectBoundary();
  if(target==='planned-raster')await inspectPlanned();
  await refreshRecent();
}
function syncDocumentControls(){
  if($('project-open'))$('project-open').disabled=busy;
  if($('project-save'))$('project-save').disabled=busy||!meshSnapshot;
  if($('export-run'))$('export-run').disabled=busy||!jobId||!displayCellCount()||terminalArtifactIncompleteJobId===jobId;
  const raster=['geotiff','asc'].includes($('export-format')?.value);
  if($('export-nodata-control'))$('export-nodata-control').hidden=!raster;
  if($('export-resolution')){$('export-resolution').disabled=busy||!raster;markRequired('export-resolution',raster);}
  if($('export-nodata')){$('export-nodata').disabled=busy||!raster;markRequired('export-nodata',raster);}
  for(const id of ['extract-raster','extract-band','extract-semantics','extract-units'])markRequired(id,!!meshSnapshot);
}
function markRequired(id,value){const input=$(id);if(!input)return;input.required=!!value;input.setAttribute('aria-required',String(!!value));const label=document.querySelector(`label[for="${id}"]`);if(label)label.dataset.required=String(!!value);}
function projectView(){return {show_edges:$('show-edges').checked,show_nodes:$('show-nodes').checked,
  result_tab:resultTab==='log'?'mesh':resultTab,selected_cell_id:selectedCellId,view_box:$('map').getAttribute('viewBox')};}
async function saveProject(){
  if(busy||!meshSnapshot)return;
  const request={mesh_job_id:meshSnapshot.jobId,job_id:jobId,view_settings:projectView()};
  const result=await managedDownload({url:'/api/project/save',filename:'MeshCherry.mcy',method:'POST',body:JSON.stringify(request),kind:'project',payload:request,fetchOptions:{headers:{'Content-Type':'application/json'}}});
  if(result?.phase==='error')toast(result.error?.message||result.error||'Không lưu được dự án.');
}
async function openProject(file,startup=false){
  if(busy||(!startup&&!file))return;
  if(!startup&&(!file.name.toLowerCase().endsWith('.mcy')||file.size>64*1024*1024)){toast('Chọn dự án .mcy không quá 64 MiB.');return;}
  let restoredTab=null,restoredSelection=null;setBusy(true);
  try{
    let saved;
    if(startup){
      const response=await fetch('/api/project/startup',{method:'POST',headers:{'X-Workbench-Token':token}});
      saved=await response.json();if(!response.ok)throw Error(saved.error||'Không mở được dự án.');
    }
    else{
      const response=await fetch('/api/project/open',{method:'POST',headers:{'X-Workbench-Token':token,'Content-Type':'application/octet-stream'},body:file});
      saved=await response.json();if(!response.ok)throw Error(saved.error||'Không mở được dự án.');
    }
    // Fetch all saved artifacts before replacing the current presentation.
    // A failed request leaves the previous accepted result usable.
    const id=encodeURIComponent(saved.job_id),names=['manifest','cells','qa',...(saved.has_attributes?['attributes']:[])];
    const loaded=await Promise.all([api(`/api/job?id=${id}`),...names.map(name=>api(`/api/artifact?id=${id}&name=${name}${['cells','attributes'].includes(name)?'&view=1':''}`))]);
    const presentation={acceptedId:saved.job_id,job:loaded[0],...Object.fromEntries(names.map((name,index)=>[name,loaded[index+1]]))};
    if(presentation.job.status!=='pass'||presentation.manifest.status!=='pass'||presentation.manifest.operation!=='mesh_only'||!presentation.cells.cells?.length)throw Error('Dự án chưa có lưới hợp lệ để xem.');
    resetResult();clearRunRequest();extractMode=false;meshSnapshot=null;clearInputPreview();source=null;plannedSource=null;
    for(const id of ['raster','dem-raster','planned-raster','boundary-path','extract-raster'])$(id).value='';
    sessionStorage.setItem(ACCEPTED_MESH_STORAGE_KEY,saved.job_id);await restoreSessionMesh(presentation);
    $('crs').value=meshSnapshot.crs;
    if(saved.has_attributes){
      const attributes=presentation.attributes;
      draw(meshSnapshot.cells,attributes.cells,{crs:meshSnapshot.crs,units:attributes.provenance?.value_units||'',display:meshSnapshot.display,attributeSummary:attributes.summary});const hasAttributeTable=mountSingleAttributeTable();
      $('csv').hidden=hasAttributeTable;$('json').hidden=hasAttributeTable;
    }
    const view=saved.view_settings||{};
    if(typeof view.show_edges==='boolean')$('show-edges').checked=view.show_edges;
    if(typeof view.show_nodes==='boolean')$('show-nodes').checked=view.show_nodes;
    updateMapControls();
    if(typeof view.view_box==='string'&&view.view_box.split(/[ ,]+/).length===4){const values=view.view_box.split(/[ ,]+/).map(Number);if(values.every(Number.isFinite)&&values[2]>0&&values[3]>0)$('map').setAttribute('viewBox',values.join(' '));}
    restoredSelection=view.selected_cell_id;
    restoredTab=saved.has_attributes&&view.result_tab==='extract'?'extract':'mesh';
    jobHeadline.textContent='✓ Đã mở dự án · '+displayCellCount().toLocaleString('vi-VN')+' ô.';
    $('job-status').textContent='Lưới, thuộc tính và cấu hình xem đã được phục hồi.';
    toast('Đã mở dự án .mcy. Chọn lại raster nếu muốn extract mới.');
  }catch(error){toast(error.message);}finally{setBusy(false);if(meshSnapshot)stage(4,false);if(restoredTab){showResultTab(restoredTab);const selected=geometry.find(cell=>cell.cell_id===restoredSelection);if(selected)selectCell(selected);}$('project-file').value='';}
}
async function exportResult(){
  if(busy||!jobId)return;
  const format=$('export-format').value,resolution=Number($('export-resolution').value);
  if(format!=='geojson'&&(!Number.isFinite(resolution)||resolution<=0)){toast('Nhập độ phân giải raster lớn hơn 0, theo mét.');$('export-resolution').focus();return;}
  const nodata=Number($('export-nodata').value);
  if(format!=='geojson'&&(!$('export-nodata').value.trim()||!Number.isFinite(nodata))){toast('Nhập giá trị NoData hữu hạn.');$('export-nodata').focus();return;}
  const request={job_id:jobId,format,field:'value',...(format==='geojson'?{}:{resolution,nodata})};
  const result=await managedDownload({url:'/api/export',filename:`meshcherry-export.${format==='geojson'?'geojson':format}`,method:'POST',body:JSON.stringify(request),kind:'export',payload:request,fetchOptions:{headers:{'Content-Type':'application/json'}}});
  if(result?.phase==='error')toast(result.error?.message||result.error||'Không xuất được dữ liệu.');
}

function comparisonMeshCount(){const values=[meshSnapshot?.display?.cell_count,meshSnapshot?.cell_count,meshSnapshot?.qa?.mesh_quality?.cell_count];return values.find(Number.isSafeInteger)??null;}
function countText(value){return Number.isSafeInteger(value)?value.toLocaleString('vi-VN'):'—';}
function comparisonCountConfig(){
  if(!meshSnapshot)throw Error('Tạo lưới trước khi đếm.');
  const baselineMode=$('comparison-mode').value||'native_raster';
  if(baselineMode==='resolution_estimate')throw Error('Ước tính theo diện tích chỉ cho giá trị gần đúng. Chọn pixel raster hoặc lưới mục tiêu để đếm chính xác.');
  const payload={mesh_job_id:meshSnapshot.jobId,baseline_mode:baselineMode};
  if(baselineMode==='native_raster'){const path=$('comparison-raster').value.trim();if(!path)throw Error('Chọn raster để xác định pixel hình học giao ROI.');payload.raster_path=path;}
  else {const cellSize=number('comparison-size');if(cellSize<=0)throw Error('Cạnh ô lưới mục tiêu phải là số dương.');payload.cell_size_m=cellSize;}
  return payload;
}
function comparisonInventoryIdentity(payload){return JSON.stringify([payload.mesh_job_id,payload.baseline_mode,payload.raster_path||null,payload.cell_size_m||null]);}
function adoptComparisonCountMetadata(metadata,key){
  const meshHash=metadata?.mesh_sha256;
  if(!meshHash||key!==comparisonCountKey)return;
  const previous=comparisonInventoryKey===key&&comparisonInventory?.provenance?.mesh_sha256===meshHash
    ?comparisonInventory:null;
  comparisonInventory={...(previous||{}),mesh_job_id:metadata.mesh_job_id||previous?.mesh_job_id||meshSnapshot?.jobId,
    mesh_count:metadata.mesh_count||previous?.mesh_count,mesh_sha256:meshHash};
  comparisonInventoryKey=key;
  renderComparisonCounts();
}
function renderComparisonCounts(){
  let currentKey='';try{currentKey=comparisonInventoryIdentity(comparisonCountConfig());}catch{}
  const inventory=currentKey&&currentKey===comparisonInventoryKey?comparisonInventory:null;
  const meshCount=inventory?.mesh_count?.count??comparisonMeshCount();$('comparison-mesh-count').textContent=countText(meshCount);
  const baseline=inventory?.baseline;
  $('comparison-baseline-count').textContent=countText(baseline?.count);
  const baselineLabels={
    source_pixels_with_positive_area_intersection_with_mesh_union:'Pixel hình học raster · giao diện tích dương với ROI',
    geometric_roi_intersecting_source_pixels:'Pixel hình học · giao ROI',
    positive_area_clipped_connected_components:'Ô vuông · thành phần liên thông sau clip',
  };
  $('comparison-baseline-label').textContent=baselineLabels[baseline?.definition]||
    (baseline?'Ô đối chứng · số đếm hoàn tất':'Ô đối chứng · chưa đếm');
  const delta=inventory?.difference?.signed_mesh_minus_baseline;
  $('comparison-count-delta').textContent=Number.isSafeInteger(delta)?`${delta>0?'+':''}${delta.toLocaleString('vi-VN')}`:'—';
  const reduction=inventory?.difference?.reduction_percent;
  $('comparison-count-reduction').textContent=Number.isFinite(reduction)?`${reduction>0?'+':''}${reduction.toLocaleString('vi-VN',{maximumFractionDigits:2})}%`:'—';
}
function clearComparison(){comparisonReport=null;comparisonEvaluationKey='';$('comparison-status').textContent='';$('comparison-result').hidden=true;$('comparison-download').hidden=true;window.ComparisonView?.clear?.($('comparison-visualization'));}
function invalidateComparisonInventory(){
  let next='';try{next=comparisonInventoryIdentity(comparisonCountConfig());}catch{}
  if(next!==comparisonCountKey){const hadInventory=Boolean(comparisonInventory);comparisonInventory=null;comparisonInventoryKey='';comparisonCountKey=next;renderComparisonCounts();if(hadInventory)$('comparison-count-status').textContent='Cấu hình đối chiếu đã đổi; hãy đếm lại để cập nhật số liệu.';}
  clearComparison();
}
function formatPhaseProgress(job,kind){
  const monitor=job?.monitor||{},progress=monitor.phase_progress||job?.phase_progress||null;
  const root=kind==='count'?'comparison-count':'comparison-evaluation',bar=$(root+'-progress'),label=$(root+'-progress-label');
  const comparisonPhases={comparison_prepare:'Xác minh nguồn và chuẩn bị miền',prepare_domain:'Chuẩn bị hình học miền',count_target_grid:'Đếm ô vuông đối chứng',count_native_pixels:'Đếm pixel nguồn giao ROI',values_pass1:'Tổng hợp giá trị · lượt 1/2',error_pass2:'Đo sai số · lượt 2/2',measure_pass1:'Đo thuộc tính raster',comparison_publish:'Tổng hợp và lưu báo cáo'};
  const elapsed=duration(monitor.elapsed_seconds),phase=comparisonPhases[progress?.phase||monitor.phase]||stageNames[progress?.phase||monitor.phase]||stageNames[job?.stage]||'Đang xử lý';
  let work='';
  if(progress&&Number.isFinite(progress.completed)&&Number.isFinite(progress.total)&&progress.total>0){
    const completed=Math.max(0,Math.min(progress.total,progress.completed));bar.max=progress.total;bar.value=completed;bar.removeAttribute('aria-valuetext');
    const units={windows:'cửa sổ',bounding_grid_cells:'ô đã xét',source_pixels_examined:'pixel đã xét',mesh_cells:'ô lưới'};
    work=`${completed.toLocaleString('vi-VN')} / ${progress.total.toLocaleString('vi-VN')} ${units[progress.unit]||progress.unit||'đơn vị'}`;
  }else if(Number.isFinite(monitor.phase_percent)){bar.max=100;bar.value=Math.max(0,Math.min(100,monitor.phase_percent));bar.setAttribute('aria-valuetext',`${concise(bar.value)}%`);work=`${concise(monitor.phase_percent)}%`;}
  else{bar.removeAttribute('value');bar.removeAttribute('aria-valuetext');work='';}
  const eta=monitor.eta;
  if(eta&&eta.scope===monitor.phase&&eta.basis==='observed_phase_rate'&&Number.isFinite(eta.low_seconds)&&Number.isFinite(eta.high_seconds)&&eta.low_seconds>0&&eta.high_seconds>=eta.low_seconds)work+=` · công đoạn còn khoảng ${duration(eta.low_seconds)}–${duration(eta.high_seconds)}`;
  const stopping=monitor.cancel_requested?'Đã yêu cầu dừng; chờ phép xử lý hiện tại kết thúc.':'';
  label.textContent=[phase,work,elapsed,stopping].filter(Boolean).join(' · ');
}
async function showResearch(){
  $('research-panel').hidden=false;$('tool-compare').setAttribute('aria-pressed','false');$('tool-research').setAttribute('aria-pressed','true');
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
  if(busy&&!comparisonOwnsBusy)return;
  if($('welcome').open)$('welcome').close();
  prepareComparison();
  $('research-panel').hidden=false;$('tool-compare').setAttribute('aria-pressed','false');$('tool-research').setAttribute('aria-pressed','true');
  $('tools-dialog').showModal();
}
function closeTools(){if(!busy||comparisonOwnsBusy)$('tools-dialog').close();}
function handleToolsCancel(event){if(busy&&!comparisonOwnsBusy)event.preventDefault();}
function canOpenResearch(){return !busy||comparisonOwnsBusy;}
function prepareComparison(){
  const count=comparisonMeshCount();
  $('comparison-mesh').textContent=meshSnapshot?`Lưới mốc: ${countText(count)} ô đã chấp nhận. Mỗi ô tương ứng một graph node.`:'Tạo lưới ở bước 01–04, rồi mở lại công cụ so sánh.';$('comparison-mesh').title=meshSnapshot?.crs||'';
  renderComparisonCounts();
  $('comparison-run').disabled=busy||!meshSnapshot;
  $('comparison-count-run').disabled=busy||!meshSnapshot||$('comparison-mode').value==='resolution_estimate';
  if(!$('comparison-raster').value){const extracted=!!$('extract-raster').value;$('comparison-raster').value=$('extract-raster').value||$('raster').value;$('comparison-band').value=extracted?$('extract-band').value:$('band').value||'1';$('comparison-units').value=extracted?$('extract-units').value:$('units').value;$('comparison-semantics').value=extracted?$('extract-semantics').value:$('semantics').value||'intensive';}
  syncComparisonMode();
  renderComparisonCounts();
}
function syncComparisonMode(){
  const mode=$('comparison-mode').value||'native_raster',estimate=mode==='resolution_estimate',target=mode==='target_grid'||estimate;
  $('comparison-target-size-wrap').hidden=!target;$('comparison-source-size-wrap').hidden=!estimate;
  $('comparison-band-wrap').hidden=estimate;$('comparison-raster-wrap').hidden=estimate;
  $('comparison-semantics').parentElement?.parentElement?.toggleAttribute?.('hidden',estimate);
  $('comparison-units').parentElement?.parentElement?.toggleAttribute?.('hidden',estimate);
  $('comparison-mode-help').textContent=mode==='native_raster'
    ?'Đếm pixel hình học của raster có giao diện tích dương với ROI; không đọc mask/NoData. Tác vụ này không tính sai số.'
    :mode==='target_grid'
      ?'Đếm các thành phần liên thông có diện tích dương sau khi cắt ô vuông mục tiêu theo ROI. Có thể đếm mà chưa chọn raster.'
      :'Ước tính số ô từ diện tích ROI và hai độ phân giải khai báo. Không đọc raster, không có số pixel thực, mask/NoData, sai số RMSE/MAE hay độ chính xác dữ liệu.';
  $('comparison-run').disabled=busy||!meshSnapshot;
  $('comparison-run').textContent=estimate?'Ước tính quy mô lưới':'Đánh giá sai số raster';
  $('comparison-evaluation-title').textContent=estimate?'Ước tính quy mô lưới':'Đánh giá sai số raster';
  $('comparison-evaluation-help').textContent=estimate?'Ước tính theo diện tích ROI và độ phân giải khai báo; không cần raster.':'Tính sai số trên các giá trị raster. Chỉ chạy khi bạn yêu cầu.';
  $('comparison-count-run').hidden=estimate;
  $('comparison-evaluation-progress').setAttribute('aria-label',estimate?'Tiến độ ước tính quy mô':'Tiến độ đánh giá sai số raster');
  $('comparison-stop').textContent=estimate?'Dừng ước tính':'Dừng đánh giá';
  $('comparison-count-run').disabled=busy||!meshSnapshot||estimate;
}
function comparisonConfig(){
  if(!meshSnapshot)throw Error('Tạo lưới trước khi so sánh.');
  const baselineMode=$('comparison-mode').value||'native_raster',payload={mesh_job_id:meshSnapshot.jobId,baseline_mode:baselineMode};
  if(!['native_raster','target_grid','resolution_estimate'].includes(baselineMode))throw Error('Chọn một cách lập mốc đối chiếu hợp lệ.');
  if(baselineMode!=='native_raster'){
    const cellSize=number('comparison-size');if(cellSize<=0)throw Error('Cạnh ô lưới mục tiêu phải là số dương.');payload.cell_size_m=cellSize;
  }
  if(baselineMode==='resolution_estimate'){
    const sourceResolution=number('comparison-source-resolution');if(sourceResolution<=0)throw Error('Độ phân giải nguồn giả định phải là số dương.');payload.source_resolution_m=sourceResolution;return payload;
  }
  const band=number('comparison-band'),path=$('comparison-raster').value.trim(),units=$('comparison-units').value.trim();
  if(band<=0||!Number.isInteger(band))throw Error('Band phải là số nguyên dương.');
  if(!path)throw Error('Chọn raster đối chiếu.');if(!units)throw Error('Khai báo đơn vị của raster đối chiếu.');
  return {...payload,raster_path:path,band,semantics:$('comparison-semantics').value,value_units:units};
}
function comparisonTable(panel,headers,rows){const table=document.createElement('table');table.className='comparison-table';const head=document.createElement('thead'),header=document.createElement('tr'),body=document.createElement('tbody');for(const text of headers){const cell=document.createElement('th');cell.setAttribute('scope','col');cell.textContent=text;header.append(cell);}head.append(header);for(const row of rows){const tr=document.createElement('tr');row.forEach((value,index)=>{const td=document.createElement(index?'td':'th');if(!index)td.setAttribute('scope','row');td.textContent=typeof value==='number'?(Number.isInteger(value)?value.toLocaleString('vi-VN'):concise(value)):value??'—';tr.append(td);});body.append(tr);}table.append(head,body);panel.append(table);}
function renderSpatialAllocation(panel,allocation){
  if(!allocation)return;
  const heading=document.createElement('h3');heading.textContent='Phân bố diện tích ô · kiểm tra kích thước hình học';panel.append(heading);
  const quantiles=['min','p10','median','p90','max'],areas=allocation.cell_area_quantiles_m2||{},sizes=allocation.equivalent_square_size_m||{};
  const labels={min:'Nhỏ nhất',p10:'P10',median:'Trung vị',p90:'P90',max:'Lớn nhất'};
  comparisonTable(panel,['Phân vị','Diện tích ô (m²)','Cạnh vuông tương đương (m)'],quantiles.map(q=>[labels[q],concise(areas[q]),concise(sizes[q])]));
  const rows=[['Số ô được mô tả',allocation.node_count],['Tổng diện tích ô (m²)',concise(allocation.total_area_m2)],['Kiểu đánh giá',allocation.assessment_status==='cell_size_distribution_only'?'Chỉ phân bố kích thước ô':allocation.assessment_status||'—'],['Đối chiếu local relief',allocation.relief_alignment==='not_assessed'?'Chưa đánh giá':allocation.relief_alignment||'—']];
  const refine=allocation.refinement;
  if(refine){
    const fraction=value=>Number.isFinite(value)?concise(value*100)+'%':'—';
    rows.push(['Ngưỡng diện tích tham chiếu (m²)',concise(refine.reference_cell_area_m2)],['Cạnh vuông tương đương ngưỡng (m)',concise(refine.reference_equivalent_square_size_m)],['Điều kiện phân loại ô',refine.threshold||'—'],['Số node nhỏ hơn ngưỡng',refine.node_count_below_reference],['Tỷ lệ node nhỏ hơn ngưỡng',fraction(refine.node_fraction_below_reference)],['Diện tích ROI dưới ngưỡng (m²)',concise(refine.roi_area_below_reference_m2)],['Tỷ lệ diện tích ROI dưới ngưỡng',fraction(refine.roi_area_fraction_below_reference)]);
  }
  detailRows(panel,rows);
  const note=document.createElement('p');note.className='field-help';note.textContent='Cạnh vuông tương đương là căn bậc hai diện tích ô. Phân vị được nội suy tuyến tính riêng trên diện tích và cạnh tương đương. Độ bám local relief và chất lượng dự đoán GNN cần đánh giá riêng.';panel.append(note);
}
function renderComparisonZones(panel,zones,native){
  if(!Array.isArray(zones?.rows))return;
  const heading=document.createElement('h3');heading.textContent='Mật độ node theo vùng';panel.append(heading);
  comparisonTable(panel,['Vùng · hàng/cột','Diện tích ROI (m²)',native?'Pixel raster gốc':'Ô vuông mục tiêu','Ô tam giác','Mật độ tam giác (node/km²)','Mật độ mốc (node/km²)'],zones.rows.slice(0,9).map(row=>[
    `${row.row+1}/${row.column+1}`,concise(row.roi_area_m2),row.structured_node_count,row.mesh_node_count,concise(row.mesh_nodes_per_km2),concise(row.structured_nodes_per_km2)]));
  const note=document.createElement('p');note.className='field-help';note.textContent='Chia khung ROI thành 3 × 3 vùng. Hàng đếm từ Nam lên Bắc, cột từ Tây sang Đông. Mỗi ô được gán một lần theo điểm đại diện trong phần giao ROI. Phân bố node theo local relief: chưa đánh giá.';panel.append(note);
}
function renderComparison(report){
  if($('comparison-settings'))$('comparison-settings').open=false;
  window.ComparisonView?.render?.($('comparison-visualization'),report);
  comparisonReport=report;const panel=$('comparison-result');panel.replaceChildren();panel.hidden=false;
  const addHeading=text=>{const node=document.createElement('h3');node.textContent=text;panel.append(node);};
  if(report.baseline?.mode==='resolution_estimate'||report.baseline_mode==='resolution_estimate'){
    addHeading('Quy mô ô · ước tính theo diện tích');
    const mesh=report.mesh||{},grid=report.square_grid||{},baseline=report.baseline||{};
    comparisonTable(panel,['Đại lượng','Giá trị','Trạng thái'],[
      ['Node lưới hiện tại',mesh.node_count??mesh.cell_count,'Đếm từ mesh'],
      ['Node lưới mục tiêu',baseline.estimated_node_count??grid.node_count??grid.cell_count,'Ước tính'],
      ['Pixel nguồn giả định',baseline.estimated_source_pixel_count,'Ước tính'],
      ['Diện tích ROI (m²)',mesh.union_area_m2??report.domain_area_m2,'Hình học miền']
    ]);
    const delta=report.graph_node_delta,red=report.node_reduction_percent;
    detailRows(panel,[['Độ phân giải nguồn giả định (m)',baseline.source_resolution_m],['Cạnh lưới mục tiêu (m)',grid.cell_size_m],['Chênh lệch node · hiện tại − mục tiêu',Number.isInteger(delta)?(delta>0?'+':'')+delta.toLocaleString('vi-VN'):'—'],['Mức giảm node · mục tiêu làm mốc',Number.isFinite(red)?concise(red)+'%':'—'],['Node graph','Một ô control volume → một node']]);
    const note=document.createElement('p');note.className='field-help';note.textContent='Đây chỉ là ước tính quy mô từ diện tích và độ phân giải khai báo. Không đọc raster nên không có số pixel thật, căn chỉnh lưới, mask/NoData, RMSE/MAE hoặc đánh giá độ chính xác địa hình. '+(Array.isArray(baseline.limitations)?baseline.limitations.join(' '):'');panel.append(note);
    const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent='Giả định và giới hạn ước tính';pre.textContent=JSON.stringify({baseline,square_grid:grid,method:report.method,limitations:grid.limitations},null,2);details.append(summary,pre);panel.append(details);
    renderSpatialAllocation(panel,report.spatial_allocation);
    $('comparison-download').hidden=false;return;
  }
  addHeading('Ngân sách graph và miền đối chiếu');
  const structured=report.structured||{},unstructured=report.unstructured||{},countA=structured.node_count??structured.cell_count,countB=unstructured.node_count??unstructured.cell_count;
  const baselineMode=report.baseline?.mode||report.baseline_mode||'target_grid',native=baselineMode==='native_raster';
  const metrics=document.createElement('div');metrics.className='comparison-metrics';
  const reduction=report.node_reduction_percent;
  for(const [value,label] of [[countA,native?'Pixel raster nguồn · giao ROI':'Node · lưới mục tiêu'],[countB,'Node · lưới hiện tại'],[Number.isFinite(reduction)?concise(reduction)+'%':'—','Mức giảm node · mốc − hiện tại / mốc']]){const box=document.createElement('div'),strong=document.createElement('strong'),span=document.createElement('span');box.className='comparison-metric';strong.textContent=typeof value==='number'?value.toLocaleString('vi-VN'):value??'—';span.textContent=label;box.append(strong,span);metrics.append(box);}panel.append(metrics);
  const delta=report.graph_node_delta;
  const extra=native?[
    ['Pixel nguồn hợp lệ trong hỗ trợ',report.baseline?.valid_source_pixel_count],
    ['Mảnh control volume sau khi cắt',report.baseline?.clipped_component_count],
    ['Độ phân giải nguồn',report.baseline?.source_resolution?`${concise(report.baseline.source_resolution.x)} × ${concise(report.baseline.source_resolution.y)} ${report.baseline.source_resolution.units||''} · ${report.baseline.source_resolution.crs||''}`:'—']
  ]:[];
  detailRows(panel,[['Miền chung hợp lệ (m²)',concise(report.common_support_area_m2)],...extra,['Đơn vị raster',report.value_units||report.provenance?.value_units||report.metrics?.structured?.units||'Theo nguồn đã khai báo'],['Graph node','Một ô control volume → một node'],['Chênh lệch node · hiện tại − '+(native?'pixel nguồn':'lưới mục tiêu'),Number.isInteger(delta)?(delta>0?'+':'')+delta.toLocaleString('vi-VN'):'—'],['Giá trị giảm node','Dương: lưới hiện tại ít node hơn; âm: nhiều node hơn']]);
  const boundaryNote=document.createElement('p');boundaryNote.className='field-help';boundaryNote.textContent=native?'Pixel raster đếm theo nguồn gốc, không nội suy. Mảnh control volume bị cắt được báo riêng và không được tính thay số pixel.'+(report.baseline?.identity_reference?' RMSE/MAE của tham chiếu pixel với chính nó là 0 theo định nghĩa. Độ chính xác GNN cần đánh giá riêng.':''):'Lưới mục tiêu được cắt theo miền tam giác, giữ các lỗ; các phần rời được tách thành ô riêng. Ô mới tăng số node tính toán, không tạo thêm thông tin đo từ raster.';panel.append(boundaryNote);
  if(!native){
    const resolution=report.baseline?.source_resolution;
    detailRows(panel,[['Độ phân giải raster nguồn',resolution?`${concise(resolution.x)} × ${concise(resolution.y)} ${resolution.units||''} · ${resolution.crs||''}`:'Chưa có thông tin'],['Cạnh ô mục tiêu (m)',report.square_grid?.cell_size_m??report.baseline?.cell_size_m]]);
    const resolutionNote=document.createElement('p');resolutionNote.className='field-help';resolutionNote.textContent='Chỉ đối chiếu kích thước trực tiếp khi hệ tọa độ và đơn vị tương thích. Lưới mịn hơn chia nhỏ miền tính toán; số phép đo của raster nguồn được giữ nguyên.';panel.append(resolutionNote);
  }
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
  comparisonTable(panel,['Chỉ số',native?'Pixel raster gốc':'Lưới vuông mục tiêu','Lưới hiện tại'],rows);
  renderComparisonZones(panel,report.spatial_zones,native);
  renderSpatialAllocation(panel,report.spatial_allocation);
  // Keep class maps in the downloadable report; avoid duplicating them in the DOM.
  const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre'),detailText=JSON.stringify({semantics:report.semantics,method:report.method,error_definition:report.error_definition,square_grid:report.square_grid,workload:report.workload,provenance:report.provenance},null,2);summary.textContent='Phương pháp và nguồn đối chiếu';pre.textContent=detailText.slice(0,20000)+(detailText.length>20000?'\n… Xem đầy đủ trong JSON tải xuống.':'');details.append(summary,pre);panel.append(details);
  $('comparison-download').hidden=false;
}
function finishComparisonCount(){comparisonCountJobId=null;comparisonOwnsBusy=false;comparisonCountStopRequested=false;$('comparison-count-stop').hidden=true;$('comparison-count-task').hidden=true;$('tools-close').disabled=false;for(const el of $('comparison-panel').querySelectorAll('input,select,button'))el.disabled=false;setBusy(false);prepareComparison();}
async function requestComparisonCountStop(){
  comparisonCountStopRequested=true;$('comparison-count-stop').disabled=true;
  if(!comparisonCountJobId){$('comparison-count-status').textContent='Đã ghi nhận yêu cầu dừng; đang xác nhận lượt đếm…';return;}
  try{await api('/api/job/stop',{job_id:comparisonCountJobId},8000);$('comparison-count-status').textContent='Đang xác nhận việc dừng đếm…';}
  catch(error){if(comparisonCountJobId){$('comparison-count-status').textContent='Chưa xác nhận yêu cầu dừng: '+error.message;$('comparison-count-stop').disabled=false;comparisonCountStopRequested=false;}}
}
async function startComparisonCount(){
  if(busy)return;let payload;try{payload=comparisonCountConfig();}catch(error){$('comparison-count-status').textContent=error.message;return;}
  const key=comparisonInventoryIdentity(payload);if(key!==comparisonCountKey){comparisonInventory=null;comparisonInventoryKey='';comparisonCountKey=key;renderComparisonCounts();}
  const request=++comparisonCountRequest;comparisonCountRequestKey=typeof crypto!=='undefined'&&crypto.randomUUID?crypto.randomUUID():'comparison-count-'+Date.now()+'-'+Math.random().toString(36).slice(2);payload.request_id=comparisonCountRequestKey;comparisonCountReadFailures=0;comparisonCountStopRequested=false;comparisonOwnsBusy=true;setBusy(true);$('tools-close').disabled=false;for(const el of $('comparison-panel').querySelectorAll('input,select,button'))el.disabled=true;$('comparison-count-task').hidden=false;$('comparison-count-progress').removeAttribute('value');$('comparison-count-progress-label').textContent='';$('comparison-count-stop').hidden=false;$('comparison-count-stop').disabled=false;$('comparison-count-status').textContent='Đang chuẩn bị đếm ô đối chứng…';
  try{const response=await api('/api/comparison/count',payload);if(request!==comparisonCountRequest)return;adoptComparisonCountMetadata(response,key);if(typeof response.job_id!=='string'||!response.job_id)throw Error('Chưa nhận được định danh lượt đếm.');comparisonCountJobId=response.job_id;if(comparisonCountStopRequested)await requestComparisonCountStop();await pollComparisonCount(request);}
  catch(error){if(request!==comparisonCountRequest)return;$('comparison-count-status').textContent='Đang xác nhận lượt đếm đã khởi chạy…';await recoverComparisonCount(request,error.message);}
}
async function recoverComparisonCount(request,reason=''){
  if(request!==comparisonCountRequest)return;
  try{let active;if(comparisonCountRequestKey){active=await api('/api/comparison/count/status?request_id='+encodeURIComponent(comparisonCountRequestKey),undefined,8000);if(active.job_id&&active.operation==='comparison_count'){if(request!==comparisonCountRequest)return;adoptComparisonCountMetadata(active,comparisonCountKey);comparisonCountJobId=active.job_id;$('comparison-count-stop').disabled=false;if(comparisonCountStopRequested)await requestComparisonCountStop();await pollComparisonCount(request);return;}}
    active=await api('/api/active-job',undefined,8000);if(request!==comparisonCountRequest)return;
    if(active.job_id&&active.operation==='comparison_count'&&(!comparisonCountRequestKey||active.request_id===comparisonCountRequestKey)){adoptComparisonCountMetadata(active,comparisonCountKey);comparisonCountJobId=active.job_id;$('comparison-count-stop').disabled=false;if(comparisonCountStopRequested)await requestComparisonCountStop();await pollComparisonCount(request);}
    else if(!active.job_id){$('comparison-count-status').textContent='Chưa có lượt đếm đang chạy. '+reason;finishComparisonCount();}
    else{$('comparison-count-status').textContent='Đang chờ tác vụ hiện tại để xác nhận trạng thái đếm…';setTimeout(()=>recoverComparisonCount(request,reason),2000);}
  }catch(error){if(request!==comparisonCountRequest)return;if([400,401,403,404,410].includes(error.status)){$('comparison-count-status').textContent='Chưa khôi phục được lượt đếm: '+error.message;finishComparisonCount();return;}$('comparison-count-status').textContent='Đang khôi phục kết nối đếm: '+error.message+' · số liệu hiện có được giữ nguyên…';setTimeout(()=>recoverComparisonCount(request,reason),2000);}
}
async function pollComparisonCount(request){
  if(request!==comparisonCountRequest||!comparisonCountJobId)return;
  let terminal=false;
  try{const job=await api('/api/job?id='+encodeURIComponent(comparisonCountJobId),undefined,8000);if(request!==comparisonCountRequest)return;formatPhaseProgress(job,'count');
    if(['queued','running','stopping'].includes(job.status)){comparisonCountReadFailures=0;$('comparison-count-stop').disabled=job.status==='stopping';setTimeout(()=>pollComparisonCount(request),750);return;}
    terminal=true;if(job.status==='pass'){const result=await api('/api/artifact?id='+encodeURIComponent(comparisonCountJobId)+'&name=comparison_counts',undefined,8000);if(request!==comparisonCountRequest)return;if(result.status!=='pass'||result.mesh_job_id!==meshSnapshot?.jobId||!comparisonInventory?.mesh_sha256||result.provenance?.mesh_sha256!==comparisonInventory.mesh_sha256||comparisonInventoryIdentity(comparisonCountConfig())!==comparisonCountKey)throw Error('Số đếm không còn khớp lưới, hash hoặc cấu hình hiện tại.');comparisonInventory=result;comparisonInventoryKey=comparisonCountKey;renderComparisonCounts();const valid=result.provenance?.valid_data_count;const targetGrid=result.baseline?.mode==='target_grid';$('comparison-count-status').textContent=targetGrid
      ?`✓ Đã đếm ${countText(result.baseline?.count)} thành phần ô vuông có diện tích dương sau khi cắt theo ROI. Không đọc giá trị raster.`
      :valid?.status==='computed'?`✓ Đã đếm xong. Pixel hình học: ${countText(result.baseline?.count)}; pixel có dữ liệu hợp lệ: ${countText(valid.value)}.`:'✓ Đã đếm xong pixel raster có giao diện tích dương với ROI. Pixel có dữ liệu hợp lệ theo mask/NoData: chưa tính.';}
    else $('comparison-count-status').textContent=job.status==='cancelled'?'Đã dừng đếm. Số liệu đếm hoàn chỉnh trước đó được giữ nguyên.':'Đếm chưa hoàn thành: '+(job.result?.error||job.stage);
    finishComparisonCount();
  }catch(error){if(request!==comparisonCountRequest)return;comparisonCountReadFailures++;
    if([400,401,403,404,410].includes(error.status)||(terminal&&comparisonCountReadFailures>=3)){$('comparison-count-status').textContent=(terminal?'Tác vụ đã kết thúc; chưa tải được số đếm. ':'Chưa đọc được lượt đếm. ')+error.message+' Số liệu hoàn chỉnh trước đó được giữ nguyên.';finishComparisonCount();return;}
    $('comparison-count-status').textContent=terminal?'Đang thử tải lại số đếm đã hoàn thành: '+error.message+'…':'Đang thử đọc lại tiến trình đếm: '+error.message+'…';setTimeout(()=>pollComparisonCount(request),2000);
  }
}
async function startComparison(){
  if(busy)return;let payload;try{payload=comparisonConfig();}catch(error){$('comparison-status').textContent=error.message;return;}
  const request=++comparisonRequest;comparisonRequestKey=typeof crypto!=='undefined'&&crypto.randomUUID?crypto.randomUUID():'comparison-'+Date.now()+'-'+Math.random().toString(36).slice(2);comparisonEvaluationKey=JSON.stringify(payload);comparisonReadFailures=0;comparisonStopRequested=false;comparisonProgressSamples=[];payload.request_id=comparisonRequestKey;comparisonOwnsBusy=true;setBusy(true);$('tools-close').disabled=false;for(const el of $('comparison-panel').querySelectorAll('input,select,button'))el.disabled=true;$('comparison-evaluation-task').hidden=false;$('comparison-evaluation-progress').removeAttribute('value');$('comparison-evaluation-progress-label').textContent='';$('comparison-stop').hidden=false;$('comparison-stop').disabled=false;$('comparison-status').textContent='Đang chuẩn bị đánh giá hai lưới trên miền raster chung…';
  if(payload.baseline_mode==='resolution_estimate')$('comparison-status').textContent='Đang ước tính quy mô theo diện tích và độ phân giải khai báo…';
  try{const response=await api('/api/comparison',payload);if(request!==comparisonRequest)return;if(typeof response.job_id!=='string'||!response.job_id)throw Error('Chưa nhận được định danh lượt so sánh.');comparisonJobId=response.job_id;if(comparisonStopRequested)await stopComparison();await pollComparison(request);}
  catch(error){if(request!==comparisonRequest)return;$('comparison-status').textContent='Đang xác nhận lượt so sánh đã khởi chạy…';await recoverComparison(request,error.message);}
}
async function recoverComparison(request,reason=''){
  if(request!==comparisonRequest)return;
  try{let active;if(comparisonRequestKey){active=await api('/api/comparison/status?request_id='+encodeURIComponent(comparisonRequestKey),undefined,8000);if(active.job_id&&active.operation==='comparison'){if(request!==comparisonRequest)return;comparisonJobId=active.job_id;$('comparison-stop').disabled=false;if(comparisonStopRequested)await stopComparison();await pollComparison(request);return;}}
    active=await api('/api/active-job',undefined,8000);if(request!==comparisonRequest)return;
    if(active.job_id&&active.operation==='comparison'&&(!comparisonRequestKey||active.request_id===comparisonRequestKey)){comparisonJobId=active.job_id;$('comparison-stop').disabled=false;if(comparisonStopRequested)await stopComparison();await pollComparison(request);}
    else if(!active.job_id){$('comparison-status').textContent='Chưa có lượt so sánh đang chạy. '+reason;finishComparison();}
    else{$('comparison-status').textContent='Đang chờ tác vụ hiện tại kết thúc để xác nhận trạng thái so sánh…';setTimeout(()=>recoverComparison(request,reason),2000);}
  }catch(error){if(request!==comparisonRequest)return;if([400,401,403,404,410].includes(error.status)){$('comparison-status').textContent='Chưa khôi phục được lượt so sánh: '+error.message+'. Lưới mốc được giữ nguyên.';finishComparison();return;}$('comparison-status').textContent='Đang khôi phục kết nối; cấu hình so sánh được giữ nguyên…';setTimeout(()=>recoverComparison(request,reason),2000);}
}
function finishComparison(){comparisonJobId=null;comparisonOwnsBusy=false;comparisonStopRequested=false;$('comparison-stop').hidden=true;$('comparison-evaluation-task').hidden=true;$('tools-close').disabled=false;for(const el of $('comparison-panel').querySelectorAll('input,select,button'))el.disabled=false;setBusy(false);prepareComparison();}
async function pollComparison(request){
  if(request!==comparisonRequest||!comparisonJobId)return;
  let terminal=false;
  try{const job=await api('/api/job?id='+encodeURIComponent(comparisonJobId),undefined,8000);if(request!==comparisonRequest)return;
    const monitor=job.monitor||{},estimate=$('comparison-mode').value==='resolution_estimate';formatPhaseProgress(job,'evaluation');$('comparison-status').textContent=job.status==='stopping'?'Đang xác nhận việc dừng tác vụ…':estimate?'Đang ước tính quy mô lưới…':'Đang đánh giá sai số raster…';
    if(['queued','running','stopping'].includes(job.status)){comparisonReadFailures=0;$('comparison-stop').disabled=job.status==='stopping';setTimeout(()=>pollComparison(request),750);return;}
    terminal=true;if(job.status==='pass'){const report=await api('/api/artifact?id='+encodeURIComponent(comparisonJobId)+'&name=comparison',undefined,8000);if(request!==comparisonRequest)return;if(JSON.stringify(comparisonConfig())!==comparisonEvaluationKey){$('comparison-status').textContent='Lượt đánh giá đã hoàn thành nhưng cấu hình hiện tại đã đổi; báo cáo cũ chưa được áp dụng.';finishComparison();return;}renderComparison(report);$('comparison-status').textContent=estimate?'✓ Đã ước tính quy mô theo diện tích và độ phân giải khai báo.':'✓ Đã so sánh hai lưới trên cùng nguồn và miền hợp lệ.';}
    else $('comparison-status').textContent=job.status==='cancelled'?(estimate?'Đã dừng ước tính. Lưới mốc được giữ nguyên.':'Đã dừng đánh giá sai số raster. Lưới mốc được giữ nguyên.'):'Tác vụ chưa hoàn thành: '+(job.result?.error||job.stage);
    finishComparison();
  }catch(error){if(request!==comparisonRequest)return;comparisonReadFailures++;
    if([400,401,403,404,410].includes(error.status)||(terminal&&comparisonReadFailures>=3)){$('comparison-status').textContent=(terminal?'Tác vụ đã kết thúc; chưa tải được báo cáo. ':'Chưa đọc được lượt so sánh. ')+error.message+' Lưới mốc được giữ nguyên.';finishComparison();return;}
    $('comparison-status').textContent=terminal?'Đang thử tải lại báo cáo đã hoàn thành…':'Đang thử đọc lại tiến trình so sánh…';setTimeout(()=>pollComparison(request),2000);
  }
}
async function stopComparison(){comparisonStopRequested=true;$('comparison-stop').disabled=true;if(!comparisonJobId){$('comparison-status').textContent='Đã ghi nhận yêu cầu dừng; đang xác nhận lượt đánh giá…';return;}try{await api('/api/job/stop',{job_id:comparisonJobId},8000);$('comparison-status').textContent='Đang xác nhận việc dừng đánh giá…';}catch(error){if(comparisonJobId){$('comparison-status').textContent='Chưa xác nhận yêu cầu dừng: '+error.message;$('comparison-stop').disabled=false;comparisonStopRequested=false;}}}
function downloadComparison(){if(!comparisonReport)return;const blob=new Blob([JSON.stringify(comparisonReport,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='mesh-comparison.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),5000);}

$('project-open').onclick=()=>{if(!busy)$('project-file').click();};
$('project-file').onchange=()=>openProject($('project-file').files?.[0]);
$('project-save').onclick=saveProject;$('export-run').onclick=exportResult;$('export-format').onchange=syncDocumentControls;
$('batch-add').onclick=addBatchRaster;$('batch-run').onclick=startBatch;$('batch-cancel').onclick=cancelBatch;$('batch-export').onclick=exportBatch;
$('gmsh-advanced-enabled').addEventListener('change',()=>{gmshAdvancedEnabled=$('gmsh-advanced-enabled').checked;mode();invalidate();});
$('dem-raster').addEventListener('input',()=>{if(demPreviewKey&&demPreviewKey!==demPreviewIdentity())clearDemPreview();});
$('dem-raster').addEventListener('change',loadDemPreview);
$('dem-band').addEventListener('input',()=>{if(demPreviewKey&&demPreviewKey!==demPreviewIdentity())clearDemPreview();});
$('dem-band').addEventListener('change',loadDemPreview);
$('gmsh-sizing').addEventListener('change',loadDemPreview);
$('form').addEventListener('submit',e=>e.preventDefault());$('form').addEventListener('input',e=>{invalidate();if(previewKey&&previewKey!==previewIdentity())clearInputPreview();if(['raster','band','crs'].includes(e.target.id)){sourceRequest++;source=null;helpers();}if(['planned-raster','planned-band','crs'].includes(e.target.id)){plannedRequest++;plannedSource=null;}parameterPreview();renderResolution();});$('form').addEventListener('change',()=>{invalidate();mode();helpers();});
$('tools-close').onclick=closeTools;$('tools-dialog').addEventListener('cancel',handleToolsCancel);$('tool-research').onclick=()=>{if(canOpenResearch())showResearch();};
$('tool-variation').onclick=()=>{if(busy)return;$('tools-dialog').close();$('mode').value='gmsh_generated';$('gmsh-sizing').value='sf1_relief';invalidate();mode();stage(2);$('dem-raster').focus();};
$('comparison-count-run').onclick=startComparisonCount;$('comparison-count-stop').onclick=requestComparisonCountStop;$('comparison-run').onclick=startComparison;$('comparison-stop').onclick=stopComparison;$('comparison-download').onclick=downloadComparison;
for(const id of ['comparison-raster','comparison-size'])$(id).addEventListener('input',invalidateComparisonInventory);
for(const id of ['comparison-band','comparison-source-resolution','comparison-semantics','comparison-units'])$(id).addEventListener('input',clearComparison);
$('comparison-mode').addEventListener('change',()=>{syncComparisonMode();invalidateComparisonInventory();});
$('refresh-input-preview').onclick=loadInputPreview;
$('refresh-dem-preview').onclick=loadDemPreview;
$('menu-edit-mesh').onclick=()=>{if(meshSnapshot)restoreRemeshDraft();else setWorkspace('mesh');};
$('menu-fit').onclick=()=>$('fit').click();
$('menu-edges').onclick=()=>$('show-edges').click();
$('menu-nodes').onclick=()=>$('show-nodes').click();
$('menu-tools-open').onclick=openTools;
$('menu-quality-open').onclick=()=>{if(!meshSnapshot)return;setWorkspace('mesh');$('quality-open').click();};
$('tool-compare').onclick=()=>{$('tools-dialog').close();setWorkspace('compare');};
const appMenu=document.querySelector('.app-menubar'),appMenuItems=[...document.querySelectorAll('.app-menubar details')];
for(const item of appMenuItems){item.addEventListener('toggle',()=>{if(item.open)for(const other of appMenuItems)if(other!==item)other.open=false;});item.addEventListener('click',event=>{if(event.target.closest('button'))item.open=false;});}
document.addEventListener('pointerdown',event=>{if(!appMenu?.contains(event.target))for(const item of appMenuItems)item.open=false;});
document.addEventListener('keydown',event=>{if(event.key!=='Escape')return;const active=appMenuItems.find(item=>item.open);if(!active)return;event.preventDefault();active.open=false;active.querySelector('summary')?.focus();});
for(const button of document.querySelectorAll('[data-pick]'))button.onclick=async()=>{
  if(busy||!bootstrap)return;
  pickerTarget=button.dataset.pick;
  if(bootstrap.native_picker){
    button.disabled=true;
    try{
      const target=pickerTarget,kind=target==='batch-raster'?'raster_many':['extract-raster','dem-raster','planned-raster','comparison-raster'].includes(target)?'raster':target==='boundary-path'?'boundary':target.startsWith('gmsh-')?'gmsh':target;
      const selected=await api('/api/picker',{kind});
      if(selected.cancelled)return;
      if(target==='batch-raster'){enqueueBatchPaths(selected.paths||[]);return;}
      await acceptSelectedSource(target,selected.path);
      if(kind==='gmsh'){gmshRequest++;gmshVerified=null;gmshFeedback('Đã chọn gmsh.exe. Chưa kiểm tra phiên bản. Bấm Kiểm tra & lưu để xác nhận.');}
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
$('form').addEventListener('input',e=>clearSourceFieldError(e.target.id));
$('source-next').onclick=async()=>{try{
  if($('mode').value==='mesh_artifact'){stage(2);return;}
  if($('gmsh-domain').value==='shapefile'){const roi=await inspectBoundary();if(!roi)return;mode();stage(2);return;}
  if($('gmsh-domain').value==='rectangle'){stage(2);return;}
  const meta=await inspectSource();if(!meta)return;
  if(!source.crs)throw new Error('Raster ROI thiếu CRS. Với ASC, đặt .prj cùng tên cạnh .asc; không tự gán CRS nguồn.');
  if(source.display_footprint){['west','south','east','north'].forEach((id,i)=>$(id).value=source.display_footprint.bounds[i]);}
  stage(2);
}catch(error){toast(error.message);}};
function showMeshQuality(q){
  setQualityMesh(meshSnapshot);drawQualityOverlay();
  $('mesh-quality').hidden=false;$('mesh-quality').replaceChildren();
  const heading=document.createElement('h3');heading.textContent='Chất lượng lưới';$('mesh-quality').append(heading);
  if(q.sizing_summary){
    const configDetails=document.createElement('details'),configSummary=document.createElement('summary');configDetails.className='mesh-run-config';configSummary.textContent='Cấu hình lượt chạy';configDetails.append(configSummary);$('mesh-quality').append(configDetails);detailRows(configDetails,requestRows(q.sizing_summary));
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
      detailRows(configDetails,fieldRows);
    }
  }
  const brief=document.createElement('p');brief.className='mesh-result-brief';brief.textContent=q.cell_count.toLocaleString('vi-VN')+' phần tử · '+q.vertex_count.toLocaleString('vi-VN')+' đỉnh hình học'+(q.domain_area_m2!==undefined?' · '+concise(q.domain_area_m2)+' m²':'');$('mesh-quality').append(brief);
  const observed=document.createElement('details'),observedLabel=document.createElement('summary');observed.className='mesh-observed-details';observedLabel.textContent='Chỉ số hình học của lưới';observed.append(observedLabel);$('mesh-quality').append(observed);
  const rows=[['Ô / đỉnh XY duy nhất',q.cell_count+' / '+q.vertex_count],['Diện tích nhỏ nhất / lớn nhất (m²)',q.area_min_m2.toFixed(3)+' / '+q.area_max_m2.toFixed(3)],['Cạnh nhỏ nhất / lớn nhất (m)',q.edge_min_m.toFixed(3)+' / '+q.edge_max_m.toFixed(3)],['Diện tích chồng lấn (m²)',q.overlap_area_m2.toFixed(6)]];
  if(q.triangle_min_angle_deg!==null)rows.push(['Góc tam giác nhỏ nhất (°)',q.triangle_min_angle_deg.toFixed(3)],['Aspect lớn nhất · cạnh dài nhất / đường cao',q.triangle_max_aspect.toFixed(3)]);
  if(q.domain_symmetric_difference_m2!==undefined)rows.push(['Sai lệch diện tích với ROI (m²)',q.domain_symmetric_difference_m2.toFixed(6)]);
  if(q.domain_coverage)rows.push(['Phủ ROI',q.domain_coverage],['Số vùng / lỗ',q.component_count+' / '+q.hole_count],['Diện tích ROI (m²)',q.domain_area_m2.toFixed(3)]);
  detailRows(observed,rows);const technical=document.createElement('details'),label=document.createElement('summary');label.textContent='Định danh hình học';technical.append(label);detailRows(technical,[['SHA256',q.geometry_digest||'—']]);$('mesh-quality').append(technical);
}
function showProjectHistory(qa){
  const box=$('project-history'),content=$('project-history-content');if(!box||!content)return;
  const history=qa?.historical_qa;box.hidden=!history;content.replaceChildren();if(!history)return;
  const q=history.mesh_quality||{},rows=[['Hồ sơ','Kết quả của lần tạo lưới đã lưu']];
  if(history.historical_status||history.status)rows.push(['Trạng thái đã lưu',history.historical_status||history.status]);
  if(q.cell_count!==undefined)rows.push(['Số ô đã lưu',q.cell_count]);
  if(q.domain_coverage)rows.push(['Phủ ROI · đã lưu',q.domain_coverage]);
  if(q.domain_symmetric_difference_m2!==undefined)rows.push(['Sai lệch diện tích ROI · đã lưu',concise(q.domain_symmetric_difference_m2)+' m²']);
  if(q.sizing_summary)rows.push(...requestRows(q.sizing_summary));
  detailRows(content,rows);
}
async function runExtraction(){
  if(busy||batchJobId||extractChecking||!meshSnapshot)return;
  for(const id of ['extract-raster','extract-band','extract-units'])$(id).setAttribute('aria-invalid','false');
  const bandText=$('extract-band').value.trim(),band=Number(bandText);
  const missing=!$('extract-raster').value.trim()?['extract-raster','Chọn raster GeoTIFF hoặc ASC để extract.']:!/^\d+$/.test(bandText)||!Number.isSafeInteger(band)||band<1?['extract-band','Nhập band raster là số nguyên dương.']:!$('extract-units').value.trim()?['extract-units','Nhập đơn vị thuộc tính, ví dụ m, mm hoặc mã lớp.']:null;
  if(missing){showResultTab('extract');$(missing[0]).setAttribute('aria-invalid','true');$(missing[0]).focus();$('extract-status').className='info error';$('extract-status').textContent=missing[1];return;}
  extractMode=true;showResultTab('extract');
  try{await previewExtractionDraft('single');}
  catch(error){$('extract-status').className='info error';$('extract-status').textContent=error.message;}
}
$('extract-run').onclick=runExtraction;
for(const id of ['extract-raster','extract-band','extract-semantics','extract-units'])$(id).addEventListener('input',invalidateExtraction);

$('try-sample').onclick=()=>useSample().catch(error=>toast(error.message));
$('tutorial-help').onclick=()=>{if(busy)return;if(tutorialActive){coach.start();refreshCoach();}else $('welcome').showModal();};
$('welcome-start').onclick=()=>{$('welcome').close();$('tutorial-finish').hidden=false;useSample().catch(error=>toast(error.message));};
$('welcome-skip').onclick=async()=>{try{await finishTutorial();$('welcome').close();}catch(error){toast(error.message);}};
$('tutorial-finish').onclick=()=>finishTutorial().then(()=>toast('Bạn đã hoàn thành! Chọn Bắt đầu lượt khác để dùng dữ liệu của mình.')).catch(error=>toast(error.message));
$('start-another').onclick=startAnother;
setupWorkspaceNavigation();
for(const button of document.querySelectorAll('button[data-workspace]'))button.disabled=false;
for(const id of ['extract-raster','extract-band','extract-units'])$(id).addEventListener('input',updateExtractReadiness);
for(const id of ['extract-run','batch-run'])$(id).addEventListener('focus',()=>{if(id==='extract-run')updateExtractReadiness();else updateBatchReadiness();});
api('/api/bootstrap').then(async data=>{bootstrap=data;roots=data.roots;for(const id of ['extract-semantics','batch-semantics']){const option=$(id).querySelector?.('option[value="intensive"]');if(option)option.textContent='Trung bình theo diện tích (khuyên dùng)';}document.querySelector('[data-pick="batch-raster"]').textContent=data.native_picker?'Chọn nhiều raster…':'Chọn raster…';$('gmsh-exe').value=data.gmsh_path||'';document.querySelector('[data-pick="gmsh-exe"]').textContent='Chọn gmsh.exe…';savedGmshFeedback();$('output').value=data.output_root;for(const root of roots){const button=document.createElement('button');button.textContent=root;button.onclick=()=>browse(root);$('roots').append(button);}mode();if(data.has_startup_project){await openProject(null,true);return;}const pending=readRunRequest();if(pending){runRequest=pending;setBusy(true);$('job-status').className='info';$('job-status').textContent='Đang khôi phục lưới và lượt chạy…';$('job-monitor').hidden=false;if(pending.operation==='extract'){extractMode=true;try{if(!await restoreSessionMesh())throw Error('Không tìm thấy lưới đã chấp nhận để khôi phục extract.');}catch(error){if(isDefinitivelyStaleAcceptedMesh(error))clearAcceptedMesh();const warning='Không khôi phục được lưới nền: '+error.message+' Lượt chạy vẫn được theo dõi và có thể dừng.';$('job-status').className='info error';$('job-status').textContent=warning;toast(warning);}}recoverStart();}else{try{await restoreSessionMesh();}catch(error){if(isDefinitivelyStaleAcceptedMesh(error))clearAcceptedMesh();toast(error.message);}if(!data.tutorial_seen&&!meshSnapshot)$('welcome').showModal();}}).catch(error=>{toast(token?error.message:'Thiếu token phiên. Hãy mở URL đầy đủ được in khi chạy UI.');$('validate').disabled=true;});
coach.setup();mode();stage(1,false);refreshRecent().catch(error=>toast(error.message));
