'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appPath = path.resolve(__dirname, '../src/gis_mesh_workbench/webui/app.js');
const source = fs.readFileSync(appPath, 'utf8');
function slice(from, to) {
  const start = source.indexOf(from);
  const end = source.indexOf(to, start);
  assert(start >= 0 && end >= 0, `Missing source section: ${from} -> ${to}`);
  return source.slice(start, end);
}

const production = [
  'let batchViewGeneration=0,batchTableBinding=null;',
  slice('function isCurrentBatchView(', 'function resetBatchResultsForMesh('),
  slice('function destroyTable(', 'function clearExtractionDraft('),
  slice('function resetBatchResultsForMesh(', 'function restorePendingBatchForMesh('),
  slice('async function recoverPendingBatchStart(', 'function enqueueBatchPaths('),
  slice('async function pollBatch(', 'async function startBatch('),
  slice('async function startBatch(', 'async function exportBatch('),
  slice('async function exportBatch(', 'function releaseInputPreview('),
].join('\n');

class Element {
  constructor(id = '') { this.id = id; this.children = []; this.hidden = false; this.disabled = false; this.value = ''; this.className = ''; this.textContent = ''; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; this.visibleRows = undefined; }
  before(item) { this.beforeItem = item; }
  after(item) { this.afterItem = item; }
  remove() { this.removed = true; }
}

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};
const tick = () => new Promise(resolve => setImmediate(resolve));

function createHarness({ previewA, previewB, firstJobA = 'pass', meshA } = {}) {
  const elements = new Map();
  const recoveryState = {};
  const $ = id => {
    for (const element of elements.values()) if (element.id === id && !element.removed) return element;
    const statusRetry = elements.get('batch-status')?.afterItem;
    if (statusRetry?.id === id && !statusRetry.removed) return statusRetry;
    if (!elements.has(id)) elements.set(id, new Element(id));
    return elements.get(id);
  };
  let exportRequest;
  const mesh = meshA || { jobId: 'mesh-1', digest: 'digest-1' };
  const draft = id => ({ mesh_job_id: id, geometry_digest: id === mesh.jobId ? mesh.digest : 'digest-2', columns: [{ id: id === 'mesh-2' ? 'fb' : 'fa', name: id === 'mesh-2' ? 'B feature' : 'A feature' }] });
  const context = vm.createContext({
    console,
    $,
    DOMException,
    document: { body: { dataset: { extractMode: 'batch' } }, createElement: () => new Element() },
    sessionStorage: { setItem() {}, getItem() { return null; }, removeItem() {} },
    crypto: { randomUUID: () => 'request-b' },
    meshSnapshot: mesh,
    mesh,
    activeBatchFeatures: [{ name: 'A feature' }], activeBatchDraft: draft(mesh.jobId), activeBatchPlanHash: 'plan-a',
    batchQueue: [{ name: 'B feature', path: 'b.tif', band: 1, semantics: 'intensive', value_units: 'm' }],
    batchJobId: 'A', batchCompletedJobId: null, batchMeshId: mesh.jobId, batchPollTimer: null, batchRequestId: null,
    batchStartUncertain: false, batchRecoveryBlocked: false, busy: false, batchTable: null,
    extractionDraft: null,
    setTimeout: () => 1,
    clearTimeout() {},
    destroyTable(which) { if (which === 'batch') $('batch-table').replaceChildren(); },
    writeBatchRecovery(state) { recoveryState.writtenRecovery = state; recoveryState.pendingRecovery = state; }, renderBatchQueue() {},
    readBatchRecovery() { return recoveryState.pendingRecovery || null; }, clearBatchRecovery() { recoveryState.recoveryCleared = true; },
    managedDownload: async request => { exportRequest = JSON.parse(request.body); return { phase: 'saved' }; },
    api: async (endpoint, body) => {
      if (endpoint === '/api/job?id=A') return { status: firstJobA };
      if (endpoint === '/api/job?id=B') return { status: 'pass' };
      if (endpoint === '/api/batch/preview' && body?.job_id === 'A') return previewA?.promise||previewA;
      if (endpoint === '/api/batch/preview' && body?.job_id === 'B') return previewB || ({
        columns: [{ id: 'fb', name: 'B feature' }], rows: [{ id: 'row-from-B', values: { fb: 20 } }], total_rows: 1,
        batch: { status: 'pass', cell_count: 1, draft: { columns: [{ id: 'fb', name: 'B feature' }] }, features: [] },
      });
      if (endpoint === '/api/batch/start') return { request_id: body.request_id, job_id: 'B' };
      throw new Error(`Unexpected API call: ${endpoint}`);
    },
    MeshCherryAttributeTable: {
      mount(container, options) {
        const ready = options.fetchPage({ page: 1, pageSize: 50, search: '', columns: [{ key: container.id === 'batch-table' ? 'fa' : 'fb' }], signal: undefined })
          .then(page => { container.visibleRows = page.rows; return true; });
        return { ready, destroy() { container.visibleRows = undefined; } };
      },
    },
  });
  vm.runInContext(production, context);
  return { context, $, exportValue: () => exportRequest, draft, recoveryState };
}

const pageA = {
  columns: [{ id: 'fa', name: 'A feature' }], rows: [{ id: 'row-from-A', values: { fa: 10 } }], total_rows: 1,
  batch: { status: 'pass', cell_count: 1, draft: { columns: [{ id: 'fa', name: 'A feature' }] }, features: [] },
};

async function beginB(harness) {
  const { context, draft } = harness;
  await vm.runInContext(`startBatch({ features: [{ name: 'B feature', path: 'b.tif', band: 1, semantics: 'intensive', value_units: 'm' }], draft: ${JSON.stringify(draft('mesh-1'))}, planHash: 'plan-b', mesh: meshSnapshot, single: false })`, context);
  await tick();
}

async function run() {
  {
    const pendingA = deferred();
    const h = createHarness({ previewA: pendingA });
    const pollA = vm.runInContext('pollBatch("A")', h.context);
    await tick();
    assert.equal(h.context.batchJobId, null, 'A becomes terminal before its preview resolves');
    await beginB(h);
    assert.equal(h.context.batchCompletedJobId, 'B');
    pendingA.resolve(pageA);
    await pollA;
    await tick();
    assert.deepEqual((h.$('batch-table').visibleRows || []).map(row => row.id), ['row-from-B']);
    assert.match(h.$('batch-status').textContent, /Xem trước đã sẵn sàng/);
    await vm.runInContext('exportBatch()', h.context);
    assert.equal(h.exportValue()?.job_id, 'B');
  }
  {
    const pendingA = deferred();
    const h = createHarness({ previewA: pendingA });
    const pollA = vm.runInContext('pollBatch("A")', h.context);
    await tick();
    await beginB(h);
    pendingA.reject(new Error('late A failure'));
    await pollA;
    assert.equal(h.$('batch-status').textContent.includes('late A failure'), false, 'late A failure cannot replace B status');
    assert.equal(h.$('batch-export').disabled, false, 'B export remains enabled');
    assert.notEqual(h.$('batch-status').afterItem?.textContent, 'Thử tải xem trước lần nữa', 'late A failure cannot install a retry button');
  }
  {
    const failedA = deferred();
    const h = createHarness({ previewA: failedA });
    const pollA = vm.runInContext('pollBatch("A")', h.context);
    await tick();
    failedA.reject(new Error('first A attempt failed'));
    await pollA;
    const retry = h.$('batch-status').afterItem;
    assert(retry, 'A preview failure exposes retry');
    const delayedRetry = deferred();
    h.context.api = async (endpoint, body) => endpoint === '/api/batch/preview' && body?.job_id === 'A' ? delayedRetry.promise
      : endpoint === '/api/batch/preview' && body?.job_id === 'B' ? { columns: [{ id: 'fb', name: 'B feature' }], rows: [{ id: 'row-from-B', values: { fb: 20 } }], total_rows: 1, batch: { status: 'pass', cell_count: 1, draft: { columns: [{ id: 'fb', name: 'B feature' }] }, features: [] } }
        : endpoint === '/api/batch/start' ? { request_id: body.request_id, job_id: 'B' } : endpoint === '/api/job?id=B' ? { status: 'pass' } : undefined;
    retry.onclick();
    await tick();
    assert.equal(retry.removed, true, 'retry control is removed while its request is in flight');
    await beginB(h);
    delayedRetry.resolve(pageA);
    await tick();
    assert.deepEqual((h.$('batch-table').visibleRows || []).map(row => row.id), ['row-from-B']);
    await vm.runInContext('exportBatch()', h.context);
    assert.equal(h.exportValue()?.job_id, 'B');
  }
  {
    const pendingA = deferred();
    const h = createHarness({ previewA: pendingA });
    const pollA = vm.runInContext('pollBatch("A")', h.context);
    await tick();
    h.context.meshSnapshot = { jobId: 'mesh-2', digest: 'digest-2' };
    vm.runInContext('resetBatchResultsForMesh("mesh-2")', h.context);
    pendingA.resolve(pageA);
    await pollA;
    assert.equal(h.$('batch-table').visibleRows, undefined, 'old mesh preview cannot mount after switching mesh');
    assert.equal(h.$('batch-export').disabled, true);
    assert.equal(h.context.batchCompletedJobId, null);
  }
  {
    const delayedRecovery = deferred();
    const h = createHarness({ previewA: Promise.resolve(pageA) });
    h.context.meshSnapshot = { jobId: 'mesh-1', digest: 'digest-1' };
    vm.runInContext('batchViewGeneration=3;batchJobId=null;batchCompletedJobId=null;batchMeshId="mesh-1";batchRequestId="request-a";batchStartUncertain=true;', h.context);
    h.context.api = async () => delayedRecovery.promise;
    h.recoveryState.pendingRecovery = { version: 1, state: 'starting', request_id: 'request-a', mesh_job_id: 'mesh-1', features: [{ name: 'A feature' }], draft: null };
    h.context.pendingRecovery = h.recoveryState.pendingRecovery;
    const recovery = vm.runInContext('recoverPendingBatchStart(pendingRecovery,3,"mesh-1")', h.context);
    h.context.meshSnapshot = { jobId: 'mesh-2', digest: 'digest-2' };
    vm.runInContext('resetBatchResultsForMesh("mesh-2")', h.context);
    delayedRecovery.resolve({ job_id: 'A', operation: 'batch_extract', request_id: 'request-a' });
    await recovery;
    assert.equal(h.context.batchJobId, null, 'late recovery cannot adopt a job after changing meshes');
    assert.equal(h.$('batch-status').textContent.includes('khôi phục'), false, 'late recovery cannot replace the new mesh status');
    assert.equal(h.recoveryState.writtenRecovery, undefined, 'late recovery cannot rewrite durable recovery state');
    assert.equal(h.recoveryState.recoveryCleared, undefined, 'switching meshes preserves unresolved recovery state');
    assert.equal(h.context.batchStartUncertain, true, 'uncertain work remains blocked against duplicate execution');
    assert.equal(h.context.batchRecoveryBlocked, true);
  }
  {
    const delayedStatusA = deferred(), delayedPreviewA = deferred();
    const h = createHarness({ previewA: delayedPreviewA });
    const baseApi = h.context.api;
    let statusCalls = 0;
    h.context.api = async (endpoint, body) => {
      if (endpoint === '/api/job?id=A') return ++statusCalls === 1 ? delayedStatusA.promise : { status: 'pass' };
      return baseApi(endpoint, body);
    };
    const oldPoll = vm.runInContext('pollBatch("A")', h.context);
    await tick();
    const terminalPoll = vm.runInContext('pollBatch("A")', h.context);
    await tick();
    await beginB(h);
    delayedPreviewA.resolve(pageA);
    await terminalPoll;
    const statusB = h.$('batch-status').textContent;
    delayedStatusA.resolve({ status: 'running', stage: 'late A progress' });
    await oldPoll;
    assert.equal(h.$('batch-status').textContent, statusB, 'late poll status cannot overwrite B');
    assert.equal(h.context.batchCompletedJobId, 'B');
  }
  {
    const delayedStart = deferred();
    const h = createHarness({ previewA: deferred() });
    vm.runInContext('batchJobId=null;batchCompletedJobId=null;', h.context);
    h.context.api = async endpoint => endpoint === '/api/batch/start' ? delayedStart.promise : undefined;
    h.context.confirmedStart = { features: [{ name: 'B feature', path: 'b.tif', band: 1, semantics: 'intensive', value_units: 'm' }], draft: h.draft('mesh-1'), planHash: 'plan-b', mesh: h.context.meshSnapshot, single: false };
    const start = vm.runInContext('startBatch(confirmedStart)', h.context);
    await tick();
    h.context.meshSnapshot = { jobId: 'mesh-2', digest: 'digest-2' };
    vm.runInContext('resetBatchResultsForMesh("mesh-2")', h.context);
    const writesBeforeLateFailure = h.recoveryState.writtenRecovery;
    delayedStart.reject(new Error('late start error'));
    await start;
    assert.equal(h.$('batch-status').textContent.includes('late start error'), false, 'late start failure cannot replace new mesh status');
    assert.equal(h.recoveryState.writtenRecovery, writesBeforeLateFailure, 'late start failure cannot rewrite recovery data');
    assert.equal(h.recoveryState.recoveryCleared, undefined, 'uncertain start record stays recoverable');
  }
  {
    const delayedReadyA = deferred(), delayedPageA = deferred();
    const h = createHarness({ previewA: Promise.resolve(pageA) });
    const baseApi = h.context.api;
    h.context.batchJobId = null;
    h.context.batchCompletedJobId = 'A';
    h.context.batchMeshId = 'mesh-1';
    let aOptions;
    const originalMount = h.context.MeshCherryAttributeTable.mount;
    let first = true;
    h.context.MeshCherryAttributeTable.mount = (container, options) => {
      if (first) { first = false; aOptions = options; return { ready: delayedReadyA.promise, destroy() {} }; }
      return originalMount(container, options);
    };
    h.context.api = async (endpoint, body) => endpoint === '/api/batch/preview' && body?.job_id === 'A' && body.offset > 0 ? delayedPageA.promise : baseApi(endpoint, body);
    const mountA = vm.runInContext('mountBatchTable("A")', h.context);
    await tick();
    const fetchA = aOptions.fetchPage({ page: 2, pageSize: 50, search: '', columns: [{ key: 'fa' }], signal: undefined });
    await tick();
    await beginB(h);
    delayedPageA.resolve({ rows: [{ id: 'late-page-A' }], total_rows: 2, offset: 50 });
    await assert.rejects(fetchA, error => error.name === 'AbortError');
    const statusB = h.$('batch-status').textContent;
    delayedReadyA.resolve(true);
    await mountA;
    assert.equal(h.$('batch-status').textContent, statusB, 'late table.ready cannot overwrite B status');
    assert.deepEqual((h.$('batch-table').visibleRows || []).map(row => row.id), ['row-from-B'], 'late page and readiness cannot replace B rows');
    await vm.runInContext('exportBatch()', h.context);
    assert.equal(h.exportValue()?.job_id, 'B');
  }
  console.log('workbench_batch_preview_race: 8 scenarios passed');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
