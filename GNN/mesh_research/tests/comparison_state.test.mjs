import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(value = '') { this.value = value; this.disabled = false; this.hidden = false; this.textContent = ''; this.title = ''; this.attributes = {}; }
  removeAttribute(name) { delete this.attributes[name]; }
  setAttribute(name, value) { this.attributes[name] = value; }
}

const app = await readFile(new URL('../src/gis_mesh_workbench/webui/app.js', import.meta.url), 'utf8');
const begin = app.indexOf('function comparisonMeshCount()');
const end = app.indexOf('async function showResearch()', begin);
const configBegin = app.indexOf('function comparisonConfig()', end);
const configEnd = app.indexOf('function comparisonTable(', configBegin);
const taskBegin = app.indexOf('function finishComparisonCount()', end);
const taskEnd = app.indexOf("$('project-open').onclick", taskBegin);
assert.ok(begin >= 0 && end > begin && configBegin > end && configEnd > configBegin && taskBegin > configEnd && taskEnd > taskBegin, 'comparison handlers are present in app.js');
const syncBegin=app.indexOf('function syncComparisonMode()');
const handlers = app.slice(begin, end) + app.slice(syncBegin,configBegin) + app.slice(configBegin, configEnd) + app.slice(taskBegin, taskEnd);

function comparisonHarness(api) {
  const ids = [
    'comparison-mode', 'comparison-raster', 'comparison-size', 'comparison-band', 'comparison-units', 'comparison-semantics',
    'comparison-panel', 'comparison-count-run', 'comparison-count-stop', 'comparison-count-task', 'comparison-count-progress',
    'comparison-count-progress-label', 'comparison-count-status', 'comparison-mesh-count', 'comparison-baseline-count',
    'comparison-baseline-label', 'comparison-count-delta', 'comparison-count-reduction', 'tools-close',
    'comparison-run', 'comparison-stop', 'comparison-evaluation-task', 'comparison-evaluation-progress',
    'comparison-evaluation-progress-label', 'comparison-status', 'comparison-result', 'comparison-download', 'comparison-visualization',
  ];
  const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
  elements['comparison-mode'].value = 'native_raster';
  elements['comparison-raster'].value = 'dem.tif';
  elements['comparison-size'].value = '30';
  elements['comparison-band'].value = '1';
  elements['comparison-units'].value = 'm';
  elements['comparison-semantics'].value = 'intensive';
  elements['comparison-panel'].querySelectorAll = () => [
    elements['comparison-count-run'], elements['comparison-count-stop'], elements['comparison-run'], elements['comparison-stop'],
  ];
  const context = {
    $: id => elements[id] || (elements[id] = new Element()),
    api,
    busy: false,
    setBusy(value) { context.busy = value; },
    number(id) { return Number(elements[id]?.value); },
    meshSnapshot: { jobId: 'mesh-1', cell_count: 8, display: { cell_count: 8 } },
    comparisonInventory: null, comparisonInventoryKey: '', comparisonCountJobId: null,
    comparisonCountRequest: 0, comparisonCountRequestKey: '', comparisonCountKey: '', comparisonCountStopRequested: false,
    comparisonCountReadFailures: 0, comparisonOwnsBusy: false, comparisonStopRequested: false,
    comparisonRequest: 0, comparisonRequestKey: '', comparisonEvaluationKey: '', comparisonReadFailures: 0,
    comparisonProgressSamples: [], comparisonJobId: null, stageNames: {},
    setTimeout() {}, formatPhaseProgress() {}, renderComparison() {}, prepareComparison() {}, clearComparison() {}, duration() { return '0s'; },
    window: { ComparisonView: { clear() {} } }, Date, Math, JSON, Number, Object, Array, String, Error,
  };
  vm.runInNewContext(`${handlers}\nthis.actions={startComparisonCount,requestComparisonCountStop,startComparison,stopComparison,renderComparisonCounts,comparisonConfig,formatPhaseProgress,syncComparisonMode};`, context);
  return { context, elements, actions: context.actions };
}

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

test('discarding a stale comparison also clears its completion status', () => {
  const h=comparisonHarness(async()=>({}));
  h.elements['comparison-status'].textContent='Đã ước tính theo cấu hình cũ';
  h.context.clearComparison();
  assert.equal(h.elements['comparison-status'].textContent,'');
  assert.equal(h.elements['comparison-result'].hidden,true);
  assert.equal(h.elements['comparison-download'].hidden,true);
});

test('comparison ETA uses only current backend phase bounds and never prior task samples', () => {
  const h=comparisonHarness(async () => ({}));
  const phase='count_native_pixels';
  const monitor={phase,elapsed_seconds:3,phase_progress:{phase,scope:'phase',completed:4,total:10,unit:'pixels'},
    eta:{scope:phase,basis:'observed_phase_rate',low_seconds:2,high_seconds:8}};
  h.actions.formatPhaseProgress({monitor},'count');
  assert.match(h.elements['comparison-count-progress-label'].textContent,/công đoạn còn khoảng/);
  h.actions.formatPhaseProgress({monitor:{...monitor,eta:null}},'count');
  assert.doesNotMatch(h.elements['comparison-count-progress-label'].textContent,/còn khoảng/);
  h.actions.formatPhaseProgress({monitor:{...monitor,eta:{...monitor.eta,scope:'another_phase'}}},'count');
  assert.doesNotMatch(h.elements['comparison-count-progress-label'].textContent,/còn khoảng/);
});

test('estimate mode retains an enabled labelled action and submits without any raster', async () => {
  let submitted;
  const h=comparisonHarness(async (path,body)=>{
    if(path==='/api/comparison'){submitted=body;return {job_id:'estimate-job'};}
    if(path.startsWith('/api/job?id='))return {status:'running',monitor:{}};
    throw Error(path);
  });
  h.elements['comparison-mode'].value='resolution_estimate';
  h.elements['comparison-raster'].value='';
  h.context.$('comparison-source-resolution').value='60';
  h.actions.syncComparisonMode();
  assert.equal(h.elements['comparison-run'].disabled,false);
  assert.match(h.elements['comparison-run'].textContent,/Ước tính/);
  assert.equal(h.elements['comparison-count-run'].hidden,true);
  await h.actions.startComparison();
  assert.equal(submitted.baseline_mode,'resolution_estimate');
  assert.equal(submitted.source_resolution_m,60);
  assert.equal(submitted.cell_size_m,30);
  assert.equal('raster_path' in submitted,false);
});

test('count stop remains available before job id and sends the recorded intent after start resolves', async () => {
  const start = deferred();
  const stops = [];
  const h = comparisonHarness(async (path, body) => {
    if (path === '/api/comparison/count') return start.promise;
    if (path === '/api/job/stop') { stops.push(body.job_id); return { accepted: true }; }
    if (path.startsWith('/api/job?id=')) return { status: 'running', monitor: {} };
    throw new Error(`Unexpected API call ${path}`);
  });
  const running = h.actions.startComparisonCount();
  assert.equal(h.elements['comparison-count-stop'].hidden, false);
  assert.equal(h.elements['comparison-count-stop'].disabled, false);
  await h.actions.requestComparisonCountStop();
  assert.equal(h.elements['comparison-count-stop'].disabled, true);
  assert.deepEqual(stops, []);
  start.resolve({ job_id: 'count-1', mesh_job_id: 'mesh-1', mesh_count: { count: 8 }, mesh_sha256: 'hash-a' });
  await running;
  assert.deepEqual(stops, ['count-1']);
});

test('lost count POST recovers by request id and retains completed counts for the same mesh hash', async () => {
  const calls = [];
  const h = comparisonHarness(async (path, body) => {
    calls.push(path);
    if (path === '/api/comparison/count') throw new Error('connection lost after submit');
    if (path.startsWith('/api/comparison/count/status?request_id=')) return {
      job_id: 'count-recovered', operation: 'comparison_count', request_id: body?.request_id,
      mesh_job_id: 'mesh-1', mesh_count: { count: 8 }, mesh_sha256: 'hash-a', status: 'running',
    };
    if (path.startsWith('/api/job?id=')) return { status: 'cancelled', stage: 'cancelled', monitor: {} };
    throw new Error(`Unexpected API call ${path}`);
  });
  const key = JSON.stringify(['mesh-1', 'native_raster', 'dem.tif', null]);
  h.context.comparisonCountKey = key;
  h.context.comparisonInventoryKey = key;
  h.context.comparisonInventory = {
    status: 'pass', mesh_job_id: 'mesh-1', mesh_count: { count: 8 },
    baseline: { mode: 'native_raster', count: 21, definition: 'source_pixels_with_positive_area_intersection_with_mesh_union' },
    difference: { signed_mesh_minus_baseline: -13, reduction_percent: 61.9 },
    provenance: { mesh_sha256: 'hash-a' },
  };
  await h.actions.startComparisonCount();
  assert.ok(calls.some(path => path.startsWith('/api/comparison/count/status?request_id=')));
  assert.equal(h.context.comparisonInventory.baseline.count, 21);
  assert.equal(h.context.comparisonInventory.provenance.mesh_sha256, 'hash-a');
  assert.match(h.elements['comparison-count-status'].textContent, /giữ nguyên/);
});

test('evaluation stop is enabled while its start request has not returned a job id', async () => {
  const start = deferred();
  const stops = [];
  const calls = [];
  const h = comparisonHarness(async (path, body) => {
    calls.push(path);
    if (path === '/api/comparison') return start.promise;
    if (path === '/api/job/stop') { stops.push(body.job_id); return { accepted: true }; }
    if (path.startsWith('/api/job?id=')) return { status: 'running', monitor: {} };
    throw new Error(`Unexpected API call ${path}`);
  });
  assert.equal(h.actions.comparisonConfig().mesh_job_id, 'mesh-1');
  const running = h.actions.startComparison();
  assert.equal(h.elements['comparison-stop'].hidden, false);
  assert.equal(h.elements['comparison-stop'].disabled, false);
  await h.actions.stopComparison();
  assert.equal(h.elements['comparison-stop'].disabled, true);
  start.resolve({ job_id: 'evaluation-1' });
  await running;
  assert.deepEqual(stops, ['evaluation-1'], `calls: ${calls.join(', ')}; status: ${h.elements['comparison-status'].textContent}`);
});

test('inventory labels use backend definitions for source pixels and clipped target components', () => {
  const h = comparisonHarness(async () => ({}));
  const key = JSON.stringify(['mesh-1', 'native_raster', 'dem.tif', null]);
  h.context.comparisonCountKey = key;
  h.context.comparisonInventoryKey = key;
  h.context.comparisonInventory = { mesh_count: { count: 8 }, baseline: {
    count: 21, definition: 'source_pixels_with_positive_area_intersection_with_mesh_union',
  } };
  h.actions.renderComparisonCounts();
  assert.equal(h.elements['comparison-baseline-label'].textContent, 'Pixel hình học raster · giao diện tích dương với ROI');
  const gridKey = JSON.stringify(['mesh-1', 'target_grid', null, 30]);
  h.elements['comparison-mode'].value = 'target_grid';
  h.context.comparisonCountKey = gridKey;
  h.context.comparisonInventoryKey = gridKey;
  h.context.comparisonInventory = { mesh_count: { count: 8 }, baseline: {
    count: 5, definition: 'positive_area_clipped_connected_components',
  } };
  h.actions.renderComparisonCounts();
  assert.equal(h.elements['comparison-baseline-label'].textContent, 'Ô vuông · thành phần liên thông sau clip');
});

test('target-grid count completion does not claim raster pixels or NoData were checked', async () => {
  const h = comparisonHarness(async (path, body) => {
    if (path === '/api/comparison/count') {
      assert.equal(body.baseline_mode, 'target_grid');
      assert.equal('raster_path' in body, false);
      return { job_id: 'grid-count', mesh_job_id: 'mesh-1', mesh_count: { count: 8 }, mesh_sha256: 'hash-grid' };
    }
    if (path.startsWith('/api/job?id=')) return { status: 'pass', monitor: {} };
    if (path.startsWith('/api/artifact?')) return {
      status: 'pass', mesh_job_id: 'mesh-1', mesh_count: { count: 8 },
      baseline: { mode: 'target_grid', count: 5, definition: 'positive_area_clipped_connected_components' },
      provenance: { mesh_sha256: 'hash-grid', valid_data_count: { status: 'not_computed' } },
    };
    throw new Error(`Unexpected API call ${path}`);
  });
  h.elements['comparison-mode'].value = 'target_grid';
  h.elements['comparison-raster'].value = '';
  await h.actions.startComparisonCount();
  assert.match(h.elements['comparison-count-status'].textContent, /5 thành phần ô vuông/);
  assert.match(h.elements['comparison-count-status'].textContent, /Không đọc giá trị raster/);
  assert.doesNotMatch(h.elements['comparison-count-status'].textContent, /Pixel.*NoData/);
});
