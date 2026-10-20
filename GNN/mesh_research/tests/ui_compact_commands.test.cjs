const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const webui=path.join(__dirname,'../src/gis_mesh_workbench/webui');
const html=fs.readFileSync(path.join(webui,'index.html'),'utf8');
const app=fs.readFileSync(path.join(webui,'app.js'),'utf8');

class Element{
  constructor(){this.children=[];this.parent=null;this.hidden=false;this.dataset={};this.attributes={};this.value='';this.open=false;this.disabled=false;this.className='';}
  append(...items){for(const item of items){item.parent=this;this.children.push(item);}}
  replaceChildren(...items){for(const child of this.children)child.parent=null;this.children=[];this._text='';this.append(...items);}
  removeAttribute(key){delete this.attributes[key];}
  setAttribute(key,value){this.attributes[key]=String(value);}
  getAttribute(key){return this.attributes[key]??null;}
  focus(){this.focused=true;}
  set textContent(value){for(const child of this.children)child.parent=null;this.children=[];this._text=String(value);}
  get textContent(){return this._text||this.children.map(item=>item.textContent||'').join(' ');}
}

function evalFunctions(ranges,globals={}){
  const elements=new Map();
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const context=vm.createContext({document:{getElementById:get,createElement:()=>new Element()},$:get,console,...globals});
  for(const [start,end] of ranges){
    const from=app.indexOf(start),to=app.indexOf(end,from);
    assert.notEqual(from,-1,`missing function start ${start}`);assert.notEqual(to,-1,`missing function end ${end}`);
    vm.runInContext(app.slice(from,to),context);
  }
  return{context,get,run:code=>vm.runInContext(code,context)};
}

function evalFunction(start,end,globals={}){return evalFunctions([[start,end]],globals);}

function parseMarkup(markup){
  const voidTags=new Set(['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']);
  const stack=[],byId=new Map();
  for(const match of markup.matchAll(/<!--[^]*?-->|<![^>]*>|<\/?[a-zA-Z][\w:-]*\b[^>]*>/g)){
    const token=match[0];if(token.startsWith('<!--')||token.startsWith('<!'))continue;
    const closing=/^<\//.test(token),tag=token.match(/^<\/?([a-zA-Z][\w:-]*)/)[1].toLowerCase();
    if(closing){for(let index=stack.length-1;index>=0;index--)if(stack[index].tag===tag){stack.length=index;break;}continue;}
    const attrs={},attrText=token.slice(tag.length+1,-1);
    for(const attr of attrText.matchAll(/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g))attrs[attr[1]]=attr[2]??attr[3]??attr[4]??'';
    const node={tag,attrs,parent:stack.at(-1)||null,children:[]};node.parent?.children.push(node);if(attrs.id)byId.set(attrs.id,node);
    if(!voidTags.has(tag)&&!token.endsWith('/>'))stack.push(node);
  }
  return{byId};
}

function hasClass(node,name){return(node?.attrs.class||'').split(/\s+/).includes(name);}

test('File menu owns one direct Open and Save command; workspace order follows the accepted sequence',()=>{
  const fileMenu=html.match(/<details><summary>Tệp<\/summary>(.*?)<\/details>/)?.[1]||'';
  assert.match(fileMenu,/id="project-open"/);assert.match(fileMenu,/id="project-save"/);
  assert.equal((html.match(/id="project-open"/g)||[]).length,1);
  assert.equal((html.match(/id="project-save"/g)||[]).length,1);
  assert.doesNotMatch(html,/menu-project-(?:open|save)/);
  const nav=html.match(/<nav class="workspace-nav"[^>]*>(.*?)<\/nav>/)?.[1]||'';
  assert.deepEqual([...nav.matchAll(/data-workspace="([^"]+)"/g)].map(match=>match[1]),['roi','mesh','compare','extract','log']);
});

test('ROI Continue precedes recents and full history is disclosed separately',()=>{
  const panel=html.slice(html.indexOf('data-panel="1"'),html.indexOf('data-panel="2"'));
  assert.ok(panel.indexOf('id="crs"')<panel.indexOf('id="source-next"'));
  assert.ok(panel.indexOf('id="source-next"')<panel.indexOf('Metadata GeoTIFF / ASC nâng cao'));
  assert.ok(panel.indexOf('Metadata GeoTIFF / ASC nâng cao')<panel.indexOf('id="recent-files"'));
  assert.match(panel,/class="roi-source-summary"/);
  assert.match(panel,/class="actions roi-primary-actions"/);
  assert.match(panel,/id="source-warning"[^>]*role="alert"/);
  assert.match(panel,/id="source-diagnostics"/);
  assert.match(panel,/id="recent-history-list"/);
});

test('DEM preview and all sizing controls share independent sibling columns',()=>{
  const start=html.indexOf('class="dem-workbench-columns"'),end=html.indexOf('id="resolution-options"',start);
  const columns=html.slice(start,end),left=columns.slice(columns.indexOf('class="dem-controls-column"'),columns.indexOf('class="configuration-group dem-preview'));
  for(const id of ['dem-raster','dem-band','sf1-controls','h-min-control','h-max','relief-controls','parameter-preview','gmsh-sizing'])assert.ok(left.includes(`id="${id}"`),`missing ${id} in controls column`);
  const dom=parseMarkup(html),controls=dom.byId.get('dem-options').parent,preview=dom.byId.get('dem-preview');
  assert.ok(hasClass(controls,'dem-controls-column'));
  assert.strictEqual(preview.parent,controls.parent,'preview must be a sibling of controls inside the two-column wrapper');
  assert.ok(hasClass(preview.parent,'dem-workbench-columns'));
  for(const id of ['gmsh-sizing','dem-raster','dem-band','sf1-controls','h-min-control','h-max','relief-controls','parameter-preview']){
    let node=dom.byId.get(id);while(node&&node!==controls)node=node.parent;
    assert.strictEqual(node,controls,`${id} must remain inside the controls column`);
  }
});

test('recent history caps the initial list at three while retaining all entries',async()=>{
  const t=evalFunction('async function refreshRecent(){','function invalidate(){',{busy:false,api:async()=>({items:Array.from({length:8},(_,index)=>({id:String(index),kind:'raster',name:`r${index}.tif`,opened:'2026-10-19T00:00:00Z'}))}),acceptSelectedSource:async()=>{},toast(){}});
  await t.run('refreshRecent()');
  assert.equal(t.get('recent-list').children.length,3);
  assert.equal(t.get('recent-history-list').children.length,5);
  assert.equal(t.get('recent-history').hidden,false);
  t.context.api=async()=>({items:[{id:'one',kind:'mesh',name:'mesh.json',opened:'2026-10-19T00:00:00Z'}]});
  await t.run('refreshRecent()');
  assert.equal(t.get('recent-list').children.length,1);
  assert.equal(t.get('recent-history').hidden,true);
});

test('source summary keeps pixel XY, CRS, band, and mask visible while full diagnostics stay separate',async()=>{
  const start='async function inspectSource(){',end='async function inspectBoundary(){';
  const calls=[];
  const t=evalFunction(start,end,{sourceRequest:0,source:null,number:()=>1,helpers(){},renderResolution(){},refreshRecent:async()=>{},loadInputPreview:async()=>{},concise:value=>String(value),detailRows(container,rows){const list=new Element();list.textContent=rows.map(([label,value])=>`${label}:${value}`).join(' ');container.append(list);},resolutionRows:()=>[['Full transform','details']],api:async(...args)=>{calls.push(args);return{driver:'GTiff',crs:'EPSG:32649',shape:[20,30],bounds:[1,2,3,4],band:1,band_count:2,dtype:'float32',nodata:-9999,mask:[0,255],scale:1,offset:0,resolution:{native_pixel_size:[30,60],native_units:'metre'}};}});
  t.get('raster').value='dem.tif';t.get('crs').value='EPSG:32649';t.get('band').value='1';
  const inspected=await t.run('inspectSource()');assert.ok(inspected,JSON.stringify({calls,sourceRequest:t.run('sourceRequest'),path:t.get('raster').value,band:t.get('band').value,crs:t.get('crs').value}));
  assert.notEqual(t.get('source-summary').textContent,'Đọc metadata…',JSON.stringify({calls,source:t.run('source'),sourceRequest:t.run('sourceRequest'),crs:t.get('crs').value,band:t.get('band').value}));
  const summary=t.get('source-summary').textContent;
  assert.match(summary,/Pixel X × Y:30 × 60 metre/);assert.match(summary,/CRS nguồn:EPSG:32649/);assert.match(summary,/Band · kích thước:1\/2 · 30 × 20 px/);assert.match(summary,/NoData \/ mask:-9999 \/ 0, 255/);
  assert.match(t.get('source-diagnostics-content').textContent,/Miền nguồn/);
  assert.equal(t.get('source-diagnostics').open,false);
});

test('planned raster ignores metadata returned after its path changes',async()=>{
  let resolveMetadata;
  const t=evalFunction('async function inspectPlanned(){','function applyResolution(){',{
    plannedRequest:0,plannedSource:null,number:()=>1,
    api:()=>new Promise(resolve=>{resolveMetadata=resolve;}),renderResolution(){},
  });
  t.get('planned-raster').value='old-reference.tif';
  t.get('planned-band').value='1';
  t.get('crs').value='EPSG:32649';
  const pending=t.run('inspectPlanned()');
  t.get('planned-raster').value='new-reference.tif';
  t.run('plannedRequest++; plannedSource=null;');
  resolveMetadata({path:'old-reference.tif',band:1,crs:'EPSG:32649',resolution:{native_pixel_size:[30,30]}});
  assert.equal(await pending,null);
  assert.equal(t.run('plannedSource'),null);
});

test('ROI reference strip renders only matching planned metadata and hides across raster ROI changes',()=>{
  const t=evalFunction('function activeResolution(){','async function inspectPlanned(){',{
    plannedSource:null,source:{resolution:{representative_pixel_size_m:[90,90]}},busy:false,
    concise:value=>String(value),
    resolutionRows:resolution=>resolution?[['Pixel in CRS','available']]:[['Raster tham chiếu','Chưa có']],
    detailRows(container,rows){container.replaceChildren();container.textContent=rows.map(([label,value])=>`${label}: ${value}`).join(' | ');},
    renderNotes(){},
  });
  for(const id of ['gmsh-domain','mode','resolution-info','roi-reference-strip','roi-reference-info','apply-resolution','resolution-warning','gmsh-sizing','h-max','width','height'])t.get(id);
  t.get('gmsh-domain').value='shapefile';t.get('mode').value='structured_grid';
  t.get('gmsh-sizing').value='uniform';t.get('h-max').value='300';t.get('width').value='300';t.get('height').value='300';
  t.run('renderResolution()');
  assert.equal(t.get('roi-reference-strip').hidden,false);
  assert.equal(t.get('roi-reference-info').textContent,'Chưa chọn raster tham chiếu');

  t.run(`plannedSource={path:'planned.tif',request_path:'planned.tif',band:2,analysis_crs:'EPSG:32649',crs:'EPSG:4326',shape:[120,240],resolution:{native_units:'degree',native_pixel_size:[0.01,0.02],representative_pixel_size_m:[1000,2000],suggestions:{structured_width_m:1000,structured_height_m:2000},rotated_or_sheared:false}};
    $('planned-raster').value='planned.tif';$('planned-band').value='2';$('crs').value='EPSG:32649';`);
  t.run('renderResolution()');
  const text=t.get('roi-reference-info').textContent;
  for(const expected of ['planned.tif / 2','Pixel X × Y: 0.01 × 0.02 °','Kích thước: 240 × 120 px','CRS nguồn: EPSG:4326'])assert.ok(text.includes(expected),`missing ${expected}: ${text}`);

  t.get('gmsh-domain').value='raster';
  t.run('renderResolution()');
  assert.equal(t.get('roi-reference-strip').hidden,true);
  assert.ok(!t.get('roi-reference-info').textContent.includes('planned.tif'));
});

test('source validation error, picker recovery, inspection, and Continue preserve the fixed source DOM',async()=>{
  const calls=[],stages=[];
  const t=evalFunctions([
    ['function detailRows(', 'function sameCRS()'],
    ['function showSourceFieldError(', 'function config()'],
    ['function acceptPickerPath(', 'async function acceptSelectedSource('],
    ['async function inspectSource(){', 'async function inspectBoundary(){'],
    ["$('source-next').onclick=async()=>{try{", '\nfunction showMeshQuality('],
  ],{sourceRequest:0,source:null,number:()=>1,helpers(){},renderResolution(){},refreshRecent:async()=>{},loadInputPreview:async()=>{},resolutionRows:()=>[['Affine transform','available']],concise:value=>String(value),stage:(value)=>stages.push(value),invalidate(){},toast(){},api:async(...args)=>{calls.push(args);return{driver:'GTiff',crs:'EPSG:32649',projected_metre:true,shape:[20,30],bounds:[1,2,3,4],band:1,band_count:1,dtype:'float32',nodata:-9999,mask:[0,255],scale:1,offset:0,resolution:{native_pixel_size:[30,60],native_units:'metre'}};}});
  const info=t.get('source-info'),summary=t.get('source-summary'),warning=t.get('source-warning'),diagnostics=t.get('source-diagnostics'),diagnosticContent=t.get('source-diagnostics-content');
  info.append(summary,warning,diagnostics);diagnostics.append(diagnosticContent);warning.hidden=true;
  const fixedChildren=[...info.children];
  t.get('crs').value='EPSG:32649';t.get('band').value='1';t.get('mode').value='structured_grid';t.get('gmsh-domain').value='raster';
  t.run("showSourceFieldError({sourceField:'raster',message:'Chọn GeoTIFF / ASC làm ranh giới ROI.'})");
  assert.equal(warning.textContent,'Chọn GeoTIFF / ASC làm ranh giới ROI.');assert.equal(warning.hidden,false);
  assert.deepEqual(info.children,fixedChildren);assert.strictEqual(summary.parent,info);assert.strictEqual(diagnostics.parent,info);assert.strictEqual(diagnosticContent.parent,diagnostics);
  t.run("acceptPickerPath('raster','recovered.tif')");
  assert.equal(warning.textContent,'');assert.equal(warning.hidden,true);assert.equal(t.get('raster').getAttribute('aria-invalid'),null);
  const inspected=await t.run('inspectSource()');assert.equal(inspected.crs,'EPSG:32649');
  assert.deepEqual(info.children,fixedChildren);assert.ok(summary.textContent.includes('Band · kích thước'));
  await t.get('source-next').onclick();
  assert.equal(calls.filter(([endpoint])=>endpoint==='/api/source').length,2);
  assert.equal(stages.at(-1),2);assert.deepEqual(info.children,fixedChildren);
});
