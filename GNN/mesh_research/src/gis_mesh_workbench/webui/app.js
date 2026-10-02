'use strict';
const $=id=>document.getElementById(id);
// Keep the main result surface quiet; detailed diagnostics remain available.
const jobDetails=document.createElement('details'),jobDetailsTitle=document.createElement('summary'),jobHeadline=document.createElement('p');
jobDetailsTitle.textContent='Chi tiết kỹ thuật';jobHeadline.id='job-headline';jobHeadline.setAttribute('role','status');jobHeadline.textContent='Chưa có lượt chạy.';
$('job-status').before(jobHeadline,jobDetails);jobDetails.append(jobDetailsTitle,$('job-status'));
const fragment=new URLSearchParams(location.hash.slice(1));
const token=fragment.get('token')||sessionStorage.getItem('mesh-cherry-token')||'';
if(fragment.has('token')){sessionStorage.setItem('mesh-cherry-token',token);history.replaceState(null,'',location.pathname);}
let roots=[],source=null,preflightId=null,revision=0,busy=false,jobId=null,resultPath='',geometry=[],attrs=new Map(),fitBox=null,mapUnits='',mapCRS='';
let pickerTarget='raster',pickerPath='';
let bootstrap=null,currentStage=1,tutorialActive=false,tutorialClicks=new Set(),visitedStages=new Set();
function stage(value){
  if(busy)return;
  currentStage=value;
  visitedStages.add(value);document.body.dataset.stage=String(value);
  for(const panel of document.querySelectorAll('[data-panel]'))panel.hidden=Number(panel.dataset.panel)!==value;
  for(const button of document.querySelectorAll('[data-stage]')){button.setAttribute('aria-current',Number(button.dataset.stage)===value?'step':'false');button.classList.toggle('visited',visitedStages.has(Number(button.dataset.stage)));}
  $('check-summary').textContent=`Nguồn: ${$('raster').value||'Chưa chọn'}\nHệ tọa độ tính: ${$('crs').value}\nCách trích: ${$('semantics').selectedOptions[0].textContent}\nĐơn vị: ${$('units').value}\nLưới: ${$('mode').selectedOptions[0].textContent}\nKiểm tra cấu hình để biết số ô và lượng dữ liệu cần đọc.`;
  tutorialHint();
}
function tutorialHint(){
  $('tutorial-banner').hidden=!tutorialActive;
  const hints={1:'Bước 1: mẫu 10/30 đã được chọn. CRS EPSG:32649 dùng mét; đây không phải xác nhận datum cao độ.',2:'Bước 2: giữ ô vuông 1 × 1 m. Miền mẫu 2 × 1 m sẽ tạo đúng hai ô.',3:'Bước 3: bấm Kiểm tra cấu hình. Khi đạt, bấm Chạy & trích thuộc tính.',4:'Bước 4: bấm cả hai tâm ô để xem 10 và 30. Coverage 100% nghĩa raster phủ đủ ô. Sau đó tải CSV/JSON và kết thúc hướng dẫn.'};
  $('tutorial-banner').textContent=hints[currentStage];
}
async function useSample(){
  if(!bootstrap||busy)return;
  $('raster').value=bootstrap.sample_raster;$('mesh').value=bootstrap.sample_mesh;
  $('band').value=1;$('crs').value='EPSG:32649';$('semantics').value='intensive';$('units').value='synthetic-value';
  $('mode').value='structured_grid';$('width').value=1;$('height').value=1;$('policy').value='strict';
  tutorialClicks.clear();$('tutorial-finish').disabled=true;$('tutorial-finish').hidden=false;tutorialActive=true;
  invalidate();mode();await inspectSource();
  ['west','south','east','north'].forEach((id,i)=>$(id).value=source.bounds[i]);
  stage(1);
}
async function finishTutorial(){
  await api('/api/tutorial',{action:'acknowledge'});
  tutorialActive=false;$('tutorial-finish').hidden=true;tutorialHint();
}
const stageNames={config:'Đọc cấu hình',preflight:'Kiểm tra',meshing:'Gmsh đang tạo lưới',cells:'Kiểm tra cell',extraction:'Đang trích thuộc tính raster',serialization:'Xuất artifacts',done:'Hoàn thành',failed:'Thất bại'};
async function api(path,data){
  const response=await fetch(path,{method:data===undefined?'GET':'POST',headers:{'X-Workbench-Token':token,...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data)});
  const value=await response.json();if(!response.ok)throw new Error(value.error||`HTTP ${response.status}`);return value;
}
function toast(message){$('toast').textContent=message;$('toast').hidden=false;setTimeout(()=>{$('toast').hidden=true;},5000);}
function invalidate(){revision++;preflightId=null;$('run').disabled=true;$('validation').className='info muted';$('validation').textContent='Cấu hình đã thay đổi. Hãy kiểm tra lại trước khi chạy.';}
function mode(){const value=$('mode').value;for(const [id,kind] of [['grid-options','structured_grid'],['mesh-options','mesh_artifact'],['gmsh-options','gmsh_reference']])$(id).hidden=value!==kind;for(const button of document.querySelectorAll('[data-mode]'))button.setAttribute('aria-pressed',String(button.dataset.mode===value));}
function detailRows(container,rows){const list=document.createElement('dl');list.className='detail-rows';for(const [label,value] of rows){const key=document.createElement('dt'),item=document.createElement('dd');key.textContent=label;item.textContent=String(value);list.append(key,item);}container.append(list);}
function sameCRS(){return source&&source.projected_metre&&$('crs').value.trim()===source.crs;}
function helpers(){ $('use-crs').disabled=!source?.projected_metre;$('use-bounds').disabled=!sameCRS();$('use-bounds').title=sameCRS()?'':'Chỉ dùng khi raster và CRS tính toán có cùng khai báo; không đổi nhãn CRS.'; }
function number(id){if(!$(id).value.trim())throw new Error(`Thiếu ${id}`);const value=Number($(id).value);if(!Number.isFinite(value))throw new Error(`Giá trị ${id} không hữu hạn`);return value;}
function config(){
  const kind=$('mode').value;const cell_source={kind,max_cells:number('max-cells')};
  if(kind==='structured_grid')Object.assign(cell_source,{extent:['west','south','east','north'].map(number),cell_width_m:number('width'),cell_height_m:number('height'),boundary_policy:$('policy').value});
  if(kind==='mesh_artifact')cell_source.path=$('mesh').value.trim();
  if(kind==='gmsh_reference')cell_source.preset='inclined_plane_250m_v0';
  return {schema_version:'gis-workbench-run-config-v1',extraction_engine:'indexed_windowed_v1',run_name:'mesh-cherry-ui',
    analysis_crs:$('crs').value.trim(),raster_path:$('raster').value.trim(),band:number('band'),semantics:$('semantics').value,
    value_units:$('units').value.trim(),output_root:$('output').value,cell_source,edge_subdivisions:number('subdivisions')};
}
async function inspectSource(){
  source=null;helpers();$('source-info').textContent='Đọc metadata…';
  const path=$('raster').value.trim(),band=number('band');
  try{const meta=await api('/api/source',{path,band});if(path!==$('raster').value.trim()||band!==number('band'))return;
    source=meta;$('source-info').className='info';$('source-info').replaceChildren();
    const filename=document.createElement('span');filename.className='file-path';filename.textContent=path;filename.title=path;$('raster').title=path;$('source-info').append(filename);
    detailRows($('source-info'),[['CRS nguồn',meta.crs||'THIẾU CRS'],['Kích thước',`${meta.shape[1]} × ${meta.shape[0]} pixel`],['Band / kiểu',`${meta.band}/${meta.band_count} · ${meta.dtype}`],['NoData / mask',`${meta.nodata} / ${meta.mask.join(', ')}`],['Scale / offset',`${meta.scale} / ${meta.offset}`]]);
    if(!meta.crs){const warning=document.createElement('p');warning.textContent='Raster chưa có CRS. Nhập CRS tính toán không sửa được lỗi này. Hãy gán metadata CRS đúng bằng công cụ GIS rồi mở lại tệp.';$('source-info').append(warning);$('source-info').className='info error';}
    helpers();
  }catch(error){$('source-info').className='info error';$('source-info').textContent=error.message;throw error;}
}
function svgElement(name,attributes){const element=document.createElementNS('http://www.w3.org/2000/svg',name);for(const [key,value] of Object.entries(attributes))element.setAttribute(key,String(value));return element;}
function cellArea(cell){const points=cell.xy.map(p=>[p[0]-cell.xy[0][0],p[1]-cell.xy[0][1]]);return Math.abs(points.reduce((a,p,i)=>{const q=points[(i+1)%points.length];return a+p[0]*q[1]-q[0]*p[1];},0))/2;}
function selectCell(cell){
  for(const poly of $('map').querySelectorAll('.cell-poly'))poly.classList.toggle('selected',poly.dataset.id===cell.cell_id);
  const record=attrs.get(cell.cell_id);let text=`ID: ${cell.cell_id}\nKiểu: ${cell.kind}\nDiện tích: ${(record?.area_m2??cellArea(cell)).toLocaleString('vi-VN',{maximumFractionDigits:3})} m²`;
  if(record){text+=`\nGiá trị: ${record.value===null?'null · không có dữ liệu hợp lệ':record.value.toLocaleString('vi-VN',{maximumFractionDigits:6})} ${mapUnits}\nFootprint coverage: ${(record.footprint_coverage*100).toFixed(2)}%\nValid coverage: ${(record.valid_coverage*100).toFixed(2)}%`;
    if('minimum' in record)text+=`\nMin / Max: ${record.minimum} / ${record.maximum}\nStd: ${record.std}`;
    if(record.class_fractions)text+=`\nTỷ lệ lớp: ${JSON.stringify(record.class_fractions)}`;
  }else text+='\nChưa extract · đây là hình học preflight.';
  $('cell-info').className='info';$('cell-info').replaceChildren();
  if(record){const value=document.createElement('strong');value.className='cell-value';value.textContent=record.value===null?'—':record.value.toLocaleString('vi-VN',{maximumFractionDigits:6});const unit=document.createElement('span');unit.className='cell-unit';unit.textContent=record.value===null?'Không có giá trị hợp lệ':mapUnits;$('cell-info').append(value,unit);}
  const details=document.createElement('div');details.className='cell-detail';details.textContent=text;$('cell-info').append(details);
  if(record){const note=document.createElement('p');note.className='tiny';note.textContent=record.value===null?'null không có nghĩa là 0.':record.valid_coverage<1?'Một phần ô không có dữ liệu hợp lệ.':'Dữ liệu hợp lệ phủ toàn bộ ô.';$('cell-info').append(note);}
  if(tutorialActive&&record&&attrs.size===2&&[...attrs.values()].every(r=>r.valid_coverage===1)&&[...attrs.values()].map(r=>r.value).sort((a,b)=>a-b).join(',')==='10,30'){
    tutorialClicks.add(cell.cell_id);$('tutorial-finish').disabled=tutorialClicks.size!==2;
  }
}
function draw(cells,records=[],contract={crs:$('crs').value,units:$('units').value}){
  mapUnits=contract.units;mapCRS=contract.crs;
  geometry=cells;attrs=new Map(records.map(record=>[record.cell_id,record]));$('map').replaceChildren();$('empty-map').hidden=!!cells.length;
  $('cell-info').textContent='Chưa chọn cell.';$('map-state').textContent=cells.length?`${cells.length} cell`:'Chưa có cell';
  if(!cells.length)return;
  const points=cells.flatMap(c=>c.xy),xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
  const xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys),span=Math.max(xmax-xmin,ymax-ymin),pad=span*.07;
  fitBox=[-pad,-pad,xmax-xmin+2*pad,ymax-ymin+2*pad];$('map').setAttribute('viewBox',fitBox.join(' '));
  const polygons=svgElement('g',{}),nodes=svgElement('g',{});$('map').append(polygons,nodes);
  for(const cell of cells){
    const poly=svgElement('polygon',{points:cell.xy.map(p=>`${p[0]-xmin},${ymax-p[1]}`).join(' '),class:'cell-poly',tabindex:0,role:'button','aria-label':`Ô ${cell.cell_id}`,'data-id':cell.cell_id});
    const title=svgElement('title',{});title.textContent=cell.cell_id;poly.append(title);poly.addEventListener('click',()=>selectCell(cell));poly.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();selectCell(cell);}});polygons.append(poly);
    const cx=cell.xy.reduce((a,p)=>a+p[0],0)/cell.xy.length,cy=cell.xy.reduce((a,p)=>a+p[1],0)/cell.xy.length;
    const node=svgElement('circle',{cx:cx-xmin,cy:ymax-cy,r:span/230,class:'cell-node',tabindex:0,role:'button','aria-label':`Tâm ô ${cell.cell_id}`});node.addEventListener('click',()=>selectCell(cell));node.addEventListener('keydown',e=>{if(e.key==='Enter')selectCell(cell);});nodes.append(node);
  }
  $('map-crs').textContent=mapCRS.startsWith('PROJCS')?'CRS projected metre · WKT trong hồ sơ':`${mapCRS} · X,Y (m)`;
  $('map-crs').title=mapCRS;
}
async function validate(){
  const requestRevision=revision;$('validate').disabled=true;$('run').disabled=true;preflightId=null;$('validation').textContent='Đang kiểm tra metadata, hình học và workload…';
  try{const data=await api('/api/preflight',config());if(requestRevision!==revision){toast('Cấu hình thay đổi trong khi kiểm tra. Hãy kiểm tra lại.');return;}
    const report=data.report;$('validation').className='info '+(report.status==='pass'?'pass':'error');
    const estimate=report.workload_estimate;
    $('validation').textContent=report.status==='pass'?`✓ Preflight đạt\nEngine: indexed_windowed_v1\nCell: ${report.cell_count===null?'tạo khi meshing':report.cell_count}\nCRS cell / analysis: ${$('crs').value} · metre\nRaster: ${source?.crs||'WKT ghi trong hồ sơ'}\nSource: ${report.pixel_count.toLocaleString('vi-VN')} pixel\nDự kiến đọc: ${estimate?estimate.estimated_pixels_read.toLocaleString('vi-VN')+' pixel / '+estimate.estimated_windows_read+' window':'kiểm tra sau khi sinh mesh'}\nCeilings: source 10M · đọc 2M · pairs 5M\nGiới hạn cell: ${number('max-cells')} · window ≤ 65.536`:report.errors.join('\n');
    if(report.status==='pass'){$('validation').replaceChildren();const heading=document.createElement('strong');heading.textContent='✓ Cấu hình hợp lệ trong giới hạn v1.';$('validation').append(heading);detailRows($('validation'),[['Nguồn',`${report.pixel_count.toLocaleString('vi-VN')} pixel`],['CRS tính toán',$('crs').value],['Loại thuộc tính',$('semantics').selectedOptions[0].textContent],['Đơn vị',$('units').value],['Số ô',report.cell_count??'Tạo khi meshing'],['Dự kiến đọc',estimate?`${estimate.estimated_pixels_read.toLocaleString('vi-VN')} pixel / ${estimate.estimated_windows_read} cửa sổ`:'Kiểm tra sau khi sinh mesh'],['Giới hạn',`${number('max-cells')} ô; nguồn 10M / đọc 2M pixel; 5M cặp; 65.536 pixel/cửa sổ`]]);}
    preflightId=data.preflight_id;$('run').disabled=!preflightId||busy;draw(data.cells);
  }catch(error){$('validation').className='info error';$('validation').textContent=error.message;}finally{$('validate').disabled=busy;}
}
function setBusy(value){busy=value;for(const input of $('form').querySelectorAll('input,select,button'))input.disabled=value;if(!value){helpers();$('run').disabled=!preflightId;$('tutorial-finish').disabled=!tutorialActive||tutorialClicks.size!==2;}$('progress').hidden=!value;}
async function startRun(){
  if(!preflightId||busy)return;const id=preflightId;preflightId=null;setBusy(true);$('exports').hidden=true;$('job-status').className='info';$('job-status').textContent='Đang khởi chạy…';
  try{const response=await api('/api/run',{preflight_id:id});jobId=response.job_id;await pollJob();}
  catch(error){setBusy(false);$('job-status').className='info error';$('job-status').textContent=error.message;}
}
async function pollJob(){
  try{const job=await api('/api/job?id='+encodeURIComponent(jobId));$('job-status').textContent=stageNames[job.stage]||job.stage;jobHeadline.textContent=job.status==='running'?`🍒 ${stageNames[job.stage]||job.stage}…`:job.status==='pass'?'✓ Đã tạo kết quả. Bạn có thể xuất CSV / JSON.':'Không hoàn tất. Mở chi tiết kỹ thuật để xem lỗi.';
    if(job.status==='running'){$('job-status').textContent=`🍒 MeshCherry đang xử lý…\n${stageNames[job.stage]||job.stage}\nVui lòng chờ hoàn tất trước khi đóng cửa sổ.`;setTimeout(pollJob,650);return;}
    resultPath=job.result.output||'';const manifest=resultPath?await api(`/api/artifact?id=${jobId}&name=manifest`):null;
    if(job.status==='fail'){$('job-status').className='info error';$('job-status').textContent=`Thất bại · ${manifest?.failed_stage||job.stage}\n${job.result.error}\nDiagnostic: ${resultPath||'chưa tạo run'}`;setBusy(false);return;}
    const [cells,attributes,qa]=await Promise.all(['cells','attributes','qa'].map(name=>api(`/api/artifact?id=${jobId}&name=${name}`)));
    draw(cells.cells,attributes.cells,{crs:attributes.provenance.analysis_crs_wkt,units:attributes.provenance.value_units});const evidence=manifest.engine_evidence;
    $('job-status').className='info pass';$('job-status').textContent=`✓ Hoàn thành · ${cells.cells.length} cell\nValid coverage (mean): ${(qa.coverage.valid_coverage.mean*100).toFixed(2)}%\nFootprint coverage: ${(qa.coverage.footprint_coverage.mean*100).toFixed(2)}%\nThời gian run: ${manifest.runtime_seconds.toFixed(2)} s\nĐọc: ${evidence.pixels_read.toLocaleString('vi-VN')} pixel / ${evidence.windows_read} window\nCandidate pairs: ${evidence.candidate_pairs.toLocaleString('vi-VN')}\nPeak RSS lifetime: ${evidence.memory_metric.value===null?'unavailable':(evidence.memory_metric.value/1048576).toFixed(1)+' MiB'}\nHash raster chính: không đổi${attributes.qa.residual===undefined?'':'\nExtensive residual: '+attributes.qa.residual}\nOutput: ${resultPath}`;
    $('exports').hidden=false;setBusy(false);stage(4);
  }catch(error){$('job-status').className='info error';$('job-status').textContent=`Mất kết nối UI: ${error.message}. Run backend có thể vẫn tiếp tục.`;setBusy(false);}
}
async function download(name){
  const response=await fetch(`/api/artifact?id=${jobId}&name=${name}&download=1`,{headers:{'X-Workbench-Token':token}});
  if(!response.ok)throw new Error('Không tải được artifact');const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name==='csv'?'attributes.csv':'attributes.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);
}
async function browse(path){
  $('picker-error').textContent='';try{const data=await api('/api/fs/list',{path});pickerPath=data.path;$('browse-path').value=pickerPath;$('breadcrumbs').textContent=pickerPath;$('entries').replaceChildren();
    const parts=pickerPath.replaceAll('\\','/').split('/');if(parts.length>1){const parent=parts.slice(0,-1).join('/');const up=document.createElement('button');up.textContent='↖ Thư mục cha';up.onclick=()=>browse(parent);$('entries').append(up);}
    const extensions=pickerTarget==='raster'?['.tif','.tiff']:['.json'];
    for(const entry of data.entries){if(entry.kind!=='directory'&&(pickerTarget==='output'||!extensions.includes(entry.extension)))continue;
      const button=document.createElement('button'),name=document.createElement('span'),size=document.createElement('small');name.textContent=(entry.kind==='directory'?'▸ ':'')+entry.name;size.textContent=entry.kind==='directory'?'Thư mục':(entry.size/1024).toFixed(1)+' KB';button.append(name,size);
      button.onclick=async()=>{if(entry.kind==='directory'){await browse(entry.path);return;}$(pickerTarget).value=entry.path;invalidate();$('picker').close();if(pickerTarget==='raster')try{await inspectSource();}catch(error){toast(error.message);}};$('entries').append(button);
    }
  }catch(error){$('picker-error').textContent=error.message;}
}
$('form').addEventListener('submit',e=>e.preventDefault());$('form').addEventListener('input',e=>{invalidate();if(['raster','band'].includes(e.target.id)){source=null;helpers();}if(e.target.id==='crs')helpers();});$('form').addEventListener('change',()=>{invalidate();mode();helpers();});
for(const button of document.querySelectorAll('[data-pick]'))button.onclick=()=>{pickerTarget=button.dataset.pick;$('choose-directory').hidden=pickerTarget!=='output';const allowed=pickerTarget==='output'?bootstrap.output_roots:roots;$('roots').replaceChildren();for(const root of allowed){const choice=document.createElement('button');choice.textContent=root;choice.onclick=()=>browse(root);$('roots').append(choice);}$('picker').showModal();browse(allowed[0]);};
$('close-picker').onclick=()=>$('picker').close();$('browse-go').onclick=()=>browse($('browse-path').value);$('choose-directory').onclick=()=>{$('output').value=pickerPath;invalidate();$('picker').close();};
$('inspect-source').onclick=()=>inspectSource().catch(error=>toast(error.message));$('use-crs').onclick=()=>{if(source?.projected_metre){$('crs').value=source.crs;invalidate();helpers();}};
$('use-bounds').onclick=()=>{if(sameCRS()){['west','south','east','north'].forEach((id,i)=>$(id).value=source.bounds[i]);invalidate();}};
$('validate').onclick=validate;$('run').onclick=startRun;$('csv').onclick=()=>download('csv').catch(error=>toast(error.message));$('json').onclick=()=>download('attributes').catch(error=>toast(error.message));$('copy-output').onclick=()=>navigator.clipboard.writeText(resultPath).then(()=>toast('Đã sao chép đường dẫn')).catch(()=>toast(resultPath));
$('show-edges').onchange=()=>$('map').classList.toggle('no-edges',!$('show-edges').checked);$('show-nodes').onchange=()=>$('map').classList.toggle('no-nodes',!$('show-nodes').checked);$('fit').onclick=()=>{if(fitBox)$('map').setAttribute('viewBox',fitBox.join(' '));};
function zoom(factor){if(!fitBox)return;let [x,y,w,h]=$('map').getAttribute('viewBox').split(' ').map(Number);if(w*factor<fitBox[2]/20||w*factor>fitBox[2]*20)return;$('map').setAttribute('viewBox',[x+w*(1-factor)/2,y+h*(1-factor)/2,w*factor,h*factor].join(' '));}
$('zoom-in').onclick=()=>zoom(1/1.4);$('zoom-out').onclick=()=>zoom(1.4);
for(const button of document.querySelectorAll('[data-stage],[data-go]'))button.onclick=()=>stage(Number(button.dataset.stage||button.dataset.go));
for(const button of document.querySelectorAll('[data-mode]'))button.onclick=()=>{if(busy)return;$('mode').value=button.dataset.mode;invalidate();mode();};
$('source-next').onclick=async()=>{try{await inspectSource();if(!source.crs){stage(1);toast('Raster chưa có CRS. Hãy gán metadata CRS đúng bằng công cụ GIS rồi mở lại tệp. CRS tính toán không sửa được CRS nguồn.');return;}if(sameCRS()&&$('mode').value==='structured_grid'){['west','south','east','north'].forEach((id,i)=>$(id).value=source.bounds[i]);invalidate();}else if(!sameCRS()){toast('Hệ tọa độ chưa trùng. Dùng CRS nguồn nếu dùng lưới vuông phủ toàn raster; hoặc khai báo miền trong CRS tính toán.');}stage(2);}catch(error){toast(error.message);}};
$('try-sample').onclick=()=>useSample().catch(error=>toast(error.message));
$('tutorial-help').onclick=()=>{if(!busy)$('welcome').showModal();};
$('welcome-start').onclick=()=>{$('welcome').close();$('tutorial-finish').hidden=false;useSample().catch(error=>toast(error.message));};
$('welcome-skip').onclick=async()=>{try{await finishTutorial();$('welcome').close();}catch(error){toast(error.message);}};
$('tutorial-finish').onclick=()=>finishTutorial().then(()=>toast('Bạn đã hoàn thành! Chọn Bắt đầu lượt khác để dùng dữ liệu của mình.')).catch(error=>toast(error.message));
$('start-another').onclick=()=>{if(busy)return;tutorialActive=false;tutorialClicks.clear();$('tutorial-finish').hidden=true;source=null;invalidate();$('raster').value='';$('mesh').value='';geometry=[];attrs.clear();draw([]);$('exports').hidden=true;$('source-info').textContent='Chọn GeoTIFF của bạn.';helpers();stage(1);};
api('/api/bootstrap').then(data=>{bootstrap=data;roots=data.roots;$('output').value=data.output_root;for(const root of roots){const button=document.createElement('button');button.textContent=root;button.onclick=()=>browse(root);$('roots').append(button);}if(!data.tutorial_seen)$('welcome').showModal();}).catch(error=>{toast(token?error.message:'Thiếu token phiên. Hãy mở URL đầy đủ được in khi chạy UI.');$('validate').disabled=true;});
mode();stage(1);
