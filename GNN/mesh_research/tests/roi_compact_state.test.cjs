const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const webui=path.join(__dirname,'../src/gis_mesh_workbench/webui');
const html=fs.readFileSync(path.join(webui,'index.html'),'utf8');
const script=fs.readFileSync(path.join(webui,'app.js'),'utf8');
const layout=fs.readFileSync(path.join(webui,'workspace_layout.css'),'utf8');

function rendererContext(){
  const elements=new Map();
  class Element{
    constructor(tagName='div'){this.tagName=tagName.toUpperCase();this.children=[];this.attributes={};this.dataset={};this.className='';this.hidden=false;this.open=false;this.value='';this.textContent='';}
    append(...items){this.children.push(...items);}
    replaceChildren(...items){this.children=[...items];this.textContent='';}
    setAttribute(name,value){this.attributes[name]=String(value);}
    getAttribute(name){return this.attributes[name]??null;}
    removeAttribute(name){delete this.attributes[name];}
  }
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const context=vm.createContext({document:{getElementById:get,createElement:tag=>new Element(tag)},$:get});
  const detailStart=script.indexOf('function detailRows('),detailEnd=script.indexOf('function sameCRS(',detailStart);
  const rendererStart=script.indexOf('function renderBoundaryInfo('),rendererEnd=script.indexOf('async function inspectBoundary(',rendererStart);
  assert.ok(detailStart>=0&&detailEnd>detailStart,'production detailRows helper exists');
  assert.ok(rendererStart>=0&&rendererEnd>rendererStart,'production boundary renderer exists');
  vm.runInContext(script.slice(detailStart,detailEnd)+script.slice(rendererStart,rendererEnd),context);
  return{context,get,run:code=>vm.runInContext(code,context)};
}

const boundaryReport=(overrides={})=>({
  source_crs:'EPSG:32649',source_vertices:124,transformed_vertices:124,
  area_m2:9876543.25,components:2,holes:1,transform_subdivisions:8,
  bounds:[500000,1000000,503000,1004000],warnings:[],...overrides
});
function allText(node){return[node.textContent||'',...node.children.map(allText)].join(' ');}

test('SHP boundary summary groups core metadata and keeps technical diagnostics expandable',()=>{
  const t=rendererContext(),report=boundaryReport();t.context.report=report;
  t.run("renderBoundaryInfo(report,'EPSG:32649')");
  const host=t.get('boundary-info'),status=host.children.find(node=>node.className==='boundary-status');
  const rows=host.children.find(node=>node.className==='detail-rows');
  const diagnostics=host.children.find(node=>node.className==='boundary-diagnostics');
  assert.ok(status,'a concise validation status remains visible');
  assert.equal(rows.children.length,4,'equal CRS/vertex values are grouped; area and region counts stay distinct');
  const summaries=rows.children.map(allText);
  assert.match(summaries[0],/EPSG:32649/);
  assert.equal((summaries[1].match(/124/g)||[]).length,1,'equal source/transformed vertex counts appear once');
  assert.match(summaries[2],/9[.,]876[.,]543/);assert.match(summaries[3],/2\s*\/\s*1/);
  assert.ok(diagnostics,'technical details remain available separately');
  assert.equal(diagnostics.open,false,'technical details start collapsed');
  const diagnosticText=allText(diagnostics);
  assert.match(diagnosticText,/500000/);assert.match(diagnosticText,/503000/);
  assert.match(diagnosticText,/8 đoạn\/cạnh/);assert.match(diagnosticText,/không giản lược|Giữ nguyên/i);
});

test('SHP summary preserves separate source and transformed values when they differ',()=>{
  const t=rendererContext(),report=boundaryReport({source_crs:'EPSG:4326',source_vertices:120,transformed_vertices:124});t.context.report=report;
  t.run("renderBoundaryInfo(report,'EPSG:32649')");
  const rows=t.get('boundary-info').children.find(node=>node.className==='detail-rows');
  assert.equal(rows.children.length,6,'different CRS and vertex counts remain distinct');
  const summaryText=rows.children.map(allText).join(' ');
  assert.match(summaryText,/EPSG:4326/);assert.match(summaryText,/EPSG:32649/);
  assert.match(summaryText,/120/);assert.match(summaryText,/124/);
});

test('startup defaults to Gmsh and removes mesh choice cards',()=>{
  const mode=html.match(/<select id="mode"[^>]*>(.*?)<\/select>/)?.[1]||'';
  assert.match(mode,/<option value="gmsh_generated" selected>/);
  assert.doesNotMatch(html,/class="mode-choices"/);
});

test('Gmsh DEM controls form an intrinsic-height vertical column',()=>{
  const controls=layout.match(/body\[data-workspace="mesh"\] \.dem-controls-column\s*\{([^}]*)\}/)?.[1]||'';
  const children=layout.match(/body\[data-workspace="mesh"\] \.dem-controls-column > \*\s*\{([^}]*)\}/)?.[1]||'';
  assert.match(controls,/display:\s*flex/);assert.match(controls,/flex-direction:\s*column/);
  assert.match(children,/flex:\s*0 0 auto/);assert.match(children,/width:\s*100%/);assert.match(children,/align-self:\s*start/);
});

test('mesh import menu command is created after project save and opens the existing mesh picker',()=>{
  const start=script.indexOf('function setupWorkspaceNavigation(){'),end=script.indexOf('\n  for(const button of document.querySelectorAll',start);
  assert.ok(start>=0&&end>start,'workspace navigation setup exists');
  const elements=new Map();
  let openedPickerTarget=null;
  class Element{
    constructor(){this.open=true;}
    after(...items){this.afterItems=items;}
  }
  const get=selector=>{if(!elements.has(selector))elements.set(selector,new Element());return elements.get(selector);};
  const context=vm.createContext({
    document:{createElement:()=>new Element(),querySelector:get},
    $:get,busy:false,qualityBusy:false,batchJobId:null,batchStartUncertain:false,batchRecoveryBlocked:false,extractChecking:false,
    openPicker(button){openedPickerTarget=button;},
    setExtractMode(){},markWorkspace(){}
  });
  vm.runInContext(script.slice(start,end)+'\n}',context);
  vm.runInContext('setupWorkspaceNavigation()',context);
  const button=get('project-save').afterItems[0];
  assert.equal(button.id,'menu-import-mesh');assert.match(button.textContent,/mesh/i);
  button.onclick();
  assert.equal(get('.app-menubar details').open,false);
  assert.equal(openedPickerTarget,get('[data-pick="mesh"]'));

  openedPickerTarget=null;
  get('.app-menubar details').open=true;
  context.qualityBusy=true;
  button.onclick();
  assert.equal(get('.app-menubar details').open,true,'menu remains open when quality work blocks import');
  assert.equal(openedPickerTarget,null,'mesh picker is not opened while QA is busy');
});
