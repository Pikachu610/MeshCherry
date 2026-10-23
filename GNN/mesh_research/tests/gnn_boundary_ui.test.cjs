const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..', 'src', 'gis_mesh_workbench', 'webui');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'workspace_layout.css'), 'utf8');

assert.match(html, /<section id="boundary-preparation-options"[^>]*hidden/,
  'boundary preparation is a visible section for SHP, not collapsed details');
assert.doesNotMatch(html, /<details id="boundary-preparation-options"/);
assert.match(html, /id="boundary-preparation-enabled" type="checkbox"/,
  'raw SHP remains the unchecked default');
assert.match(html, /<select id="boundary-method">[\s\S]*value="topology_preserving"[\s\S]*value="gnn_closed_rdp"/);
assert.match(html, /id="apply-gnn-boundary"/);
const formStart = html.indexOf('<form id="form">');
const formEnd = html.indexOf('</form>', formStart);
for (const id of ['boundary-method', 'apply-gnn-boundary']) {
  const position = html.indexOf(`id="${id}"`);
  assert.ok(position > formStart && position < formEnd, `${id} stays inside the form busy lock`);
}
const recommendation = html.match(/<div class="boundary-method-callout">([\s\S]*?)<\/div>/)?.[1];
assert.ok(recommendation);
assert.doesNotMatch(recommendation, /ROI4|lịch sử|0,5%/);
assert.match(recommendation, /Cấu hình khuyến nghị/);
assert.match(recommendation, /cần kiểm tra phù hợp với từng ranh giới/);
assert.match(app, /Pixel giao miền lưới · đếm hình học/);
assert.match(html, /không sửa SHP gốc/);
assert.match(html, /chuẩn hóa DEM vẫn theo ROI gốc/);
assert.match(html, /ô biên rất nhỏ/);
assert.match(css, /#boundary-preparation-options[\s\S]*width: 100%/);

assert.match(app, /restorePreparation&&preparation\.method==='gnn_closed_rdp'\?'gnn_closed_rdp':'topology_preserving'/,
  'old saved configs without a method restore to topology-preserving behavior only when their SHP is available');
assert.match(app, /boundary_preparation=\{tolerance_m:tolerance,max_boundary_distance_m:distance,max_area_change_fraction:area\/100,\.\.\.\(\$\('boundary-method'\)\.value==='gnn_closed_rdp'\?\{method:'gnn_closed_rdp'\}:\{\}\)\}/,
  'the optional method is serialized only for the GNN closed-RDP method');
assert.match(app, /if\(busy\|\|\(typeof qualityBusy!=='undefined'&&qualityBusy\)\|\|\$\('gmsh-domain'\)\.value!=='shapefile'\)return;[\s\S]*boundary-preparation-enabled'\)\.checked=true;[\s\S]*boundary-method'\)\.value='gnn_closed_rdp'[\s\S]*boundary-tolerance'\)\.value='200'[\s\S]*boundary-distance'\)\.value='250'[\s\S]*boundary-area-percent'\)\.value='1'/,
  'the explicit button is guarded and applies the historical ROI4 values');
assert.match(app, /\$\('boundary-method'\)\.disabled=!enabled\|\|busy/);
assert.match(app, /\$\('apply-gnn-boundary'\)\.disabled=busy\|\|\(typeof qualityBusy/);
assert.match(app, /for\(const input of \$\('form'\)\.querySelectorAll\('input,select,button'\)\)input\.disabled=value/,
  'the explicit selector and action button inherit the form busy lock');

const buttonHandler = app.match(/\$\('apply-gnn-boundary'\)\.onclick=\(\)=>\{([\s\S]*?)\n\};/);
assert.ok(buttonHandler, 'explicit GNN apply handler exists');
assert.doesNotMatch(buttonHandler[1], /gmsh-sizing|sf1-scale/,
  'GNN boundary values are not coupled to mesh sizing or SF-1 scale');

function behaviorHarness() {
  const elements = new Map();
  const context = vm.createContext({ console, encodeURIComponent });
  vm.runInContext(`
    const elements = new Map();
    function $(id) {
      if (!elements.has(id)) elements.set(id, {value:'',checked:false,disabled:false,hidden:false,className:'',textContent:'',dataset:{}});
      return elements.get(id);
    }
  `, context);
  const get = id => vm.runInContext(`$('${id}')`, context);
  const extract = (start, end) => app.slice(app.indexOf(start), app.indexOf(end, app.indexOf(start)));

  vm.runInContext(`
    requireDomainSelection=()=>{}; extractMode=false; meshSnapshot=null; source=null;
    gmshAdvancedEnabled=false; busy=false; qualityBusy=false; revision=3; preflightId='ready-token';
    preserveAcceptedDuringRun=false; bootstrap={mesh_limits:{max_cells:100000}};
    number=id=>Number($(id).value); clearComparison=()=>{}; clearAcceptedMesh=()=>{};
    setQualityMesh=()=>{}; resetResult=()=>{}; refreshCoach=()=>{}; mode=()=>{};
    concise=value=>String(value);
    showRemeshDraftButton=()=>{}; stage=()=>{}; toast=()=>{};
    plannedSource=null; plannedRequest=0;
  `, context);
  vm.runInContext(extract('function config(){', 'function acceptedExtractionCellLimit()'), context);
  vm.runInContext(extract('function requestRows(q){', 'function detailRows('), context);
  vm.runInContext(extract('function invalidate(){', 'function mode(){'), context);
  vm.runInContext(extract('$(' + "'apply-gnn-boundary').onclick=()=>{" , "\n};") + '\n};', context);
  vm.runInContext(extract('async function restoreRemeshDraft(){', 'function mountSingleAttributeTable(){'), context);
  vm.runInContext(`
    api=async()=>({});
    $('mode').value='gmsh_generated'; $('gmsh-domain').value='shapefile';
    $('gmsh-sizing').value='uniform'; $('boundary-path').value='roi.shp';
    $('gmsh-exe').value='gmsh.exe'; $('max-cells').value='1000'; $('h-max').value='500';
    $('h-min').value='250'; $('crs').value='EPSG:32649'; $('output').value='runs';
    $('subdivisions').value='8'; $('sf1-scale').value='1';
  `, context);
  return {context, get};
}

async function restoreConfig(boundaryPreparation, boundaryPath = 'roi.shp') {
  const t = behaviorHarness();
  const source = {
    kind: 'gmsh_generated', boundary_path: boundaryPath, executable: 'gmsh.exe', sizing: 'uniform',
    h_min_m: 250, h_max_m: 500, max_cells: 1000,
    ...(boundaryPreparation === undefined ? {} : {boundary_preparation: boundaryPreparation})
  };
  t.context.meshSnapshot = {jobId:'accepted-123',crs:'EPSG:32649'};
  t.context.revision = 3;
  t.context.api = async () => ({config:{analysis_crs:'EPSG:32649',output_root:'runs',cell_source:source}});
  await vm.runInContext('restoreRemeshDraft()', t.context);
  return t;
}

async function behaviorChecks() {
  const raw = behaviorHarness();
  raw.get('boundary-preparation-enabled').checked = false;
  assert.equal(vm.runInContext("'boundary_preparation' in config().cell_source", raw.context), false,
    'the raw-SHP default stays disabled at configuration time');

  const gnn = behaviorHarness();
  for (const [id, value] of Object.entries({
    'boundary-preparation-enabled':true,'boundary-method':'gnn_closed_rdp',
    'boundary-tolerance':'200','boundary-distance':'250','boundary-area-percent':'1'
  })) {
    if (id === 'boundary-preparation-enabled') gnn.get(id).checked = value;
    else gnn.get(id).value = value;
  }
  const gnnConfig = vm.runInContext('config()', gnn.context);
  assert.deepEqual(JSON.parse(JSON.stringify(gnnConfig.cell_source.boundary_preparation)), {
    tolerance_m:200,max_boundary_distance_m:250,max_area_change_fraction:.01,method:'gnn_closed_rdp'
  });
  const gnnMethodRow = vm.runInContext(`requestRows(${JSON.stringify({mode:'gmsh_generated',roi_type:'shapefile',roi_source:'roi.shp',analysis_crs:'EPSG:32649',max_cells:1000,boundary_preparation:gnnConfig.cell_source.boundary_preparation})})`, gnn.context)
    .find(([label]) => label === 'Phương pháp biên');
  assert.deepEqual(JSON.parse(JSON.stringify(gnnMethodRow)), ['Phương pháp biên','GNN · RDP vòng kín']);

  const legacy = await restoreConfig({tolerance_m:180,max_boundary_distance_m:220,max_area_change_fraction:.005});
  assert.equal(legacy.get('boundary-preparation-enabled').checked, true);
  assert.equal(legacy.get('boundary-method').value, 'topology_preserving');
  assert.equal(legacy.get('boundary-tolerance').value, 180);
  assert.equal(legacy.get('boundary-distance').value, 220);
  assert.equal(legacy.get('boundary-area-percent').value, .5);
  const legacyConfig = vm.runInContext('config()', legacy.context).cell_source.boundary_preparation;
  assert.equal('method' in legacyConfig, false, 'legacy preparation without a method omits the method key');
  const legacyMethodRow = vm.runInContext(`requestRows(${JSON.stringify({mode:'gmsh_generated',roi_type:'shapefile',roi_source:'roi.shp',analysis_crs:'EPSG:32649',max_cells:1000,boundary_preparation:legacyConfig})})`, legacy.context)
    .find(([label]) => label === 'Phương pháp biên');
  assert.deepEqual(JSON.parse(JSON.stringify(legacyMethodRow)), ['Phương pháp biên','Giản lược giữ topology']);

  const savedGnn = await restoreConfig({
    tolerance_m:200,max_boundary_distance_m:250,max_area_change_fraction:.01,method:'gnn_closed_rdp'
  });
  assert.equal(savedGnn.get('boundary-method').value, 'gnn_closed_rdp');
  assert.deepEqual(JSON.parse(JSON.stringify(vm.runInContext('config()', savedGnn.context).cell_source.boundary_preparation)), {
    tolerance_m:200,max_boundary_distance_m:250,max_area_change_fraction:.01,method:'gnn_closed_rdp'
  });

  const orphaned = await restoreConfig({
    tolerance_m:200,max_boundary_distance_m:250,max_area_change_fraction:.01,method:'gnn_closed_rdp'
  }, null);
  assert.equal(orphaned.get('boundary-preparation-enabled').checked, false);
  assert.equal(orphaned.get('boundary-method').value, 'topology_preserving');
  assert.equal(orphaned.get('boundary-tolerance').value, '');
  assert.equal(orphaned.get('boundary-distance').value, '');
  assert.equal(orphaned.get('boundary-area-percent').value, 1);
  assert.match(orphaned.get('validation').textContent, /SHP gốc không còn.*tùy chọn biên dẫn xuất đã được bỏ/);
  orphaned.get('boundary-path').value='new-roi.shp'; orphaned.get('gmsh-domain').value='shapefile';
  assert.equal(vm.runInContext("'boundary_preparation' in config().cell_source", orphaned.context), false,
    'an orphaned saved preset cannot transfer silently to a newly selected SHP');

  const apply = behaviorHarness();
  apply.get('sf1-scale').value = '1.75';
  apply.get('boundary-method').value = 'topology_preserving';
  vm.runInContext("$('apply-gnn-boundary').onclick()", apply.context);
  assert.equal(apply.get('boundary-preparation-enabled').checked, true);
  assert.equal(apply.get('boundary-method').value, 'gnn_closed_rdp');
  assert.equal(apply.get('boundary-tolerance').value, '200');
  assert.equal(apply.get('boundary-distance').value, '250');
  assert.equal(apply.get('boundary-area-percent').value, '1');
  assert.equal(apply.get('sf1-scale').value, '1.75');
  assert.equal(vm.runInContext('preflightId', apply.context), null, 'applying the preset invalidates the previous preflight');
  assert.equal(vm.runInContext('revision', apply.context), 4);

  const blocked = behaviorHarness();
  blocked.get('boundary-tolerance').value='raw';
  blocked.context.busy = true;
  vm.runInContext("$('apply-gnn-boundary').onclick()", blocked.context);
  assert.equal(blocked.get('boundary-preparation-enabled').checked, false);
  assert.equal(blocked.get('boundary-tolerance').value, 'raw');
  assert.equal(vm.runInContext('preflightId', blocked.context), 'ready-token');
  blocked.context.busy = false; blocked.context.qualityBusy = true;
  vm.runInContext("$('apply-gnn-boundary').onclick()", blocked.context);
  assert.equal(blocked.get('boundary-preparation-enabled').checked, false);
  assert.equal(vm.runInContext('preflightId', blocked.context), 'ready-token');
}

behaviorChecks().then(() => console.log('GNN boundary UI behavior checks passed')).catch(error => {
  console.error(error);
  process.exitCode = 1;
});

console.log('GNN boundary UI contracts passed');
