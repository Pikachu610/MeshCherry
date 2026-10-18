const assert = require('node:assert/strict');
const { create } = require('../src/gis_mesh_workbench/webui/quality_bridge.js');

async function main() {
  const calls = [];
  const report = { schema_version: 'meshcherry-quality-v1', status: 'PASS', elements: [] };
  const bridge = create({
    api: async (path, body) => {
      calls.push({ path, body });
      if (path === '/api/quality/start') return { job_id: 'qa-job-12345678' };
      if (path.startsWith('/api/job?')) return calls.filter(call => call.path.startsWith('/api/job?')).length === 1
        ? { status: 'running' } : { status: 'pass' };
      if (path === '/api/quality/result') return report;
      if (path === '/api/quality/latest') return { status: 'pass', job_id: 'mesh-job-87654321', report };
      if (path === '/api/quality/page') return { items: [], total: 0 };
      if (path === '/api/job/stop') return { accepted: true };
      throw new Error(`Unexpected endpoint ${path}`);
    },
    download: async (path, payload) => ({ path, payload })
  });

  bridge.setMesh('mesh-job-12345678');
  const result = await bridge.run({ warning_quality_q: 0.2, repetitions: 3 }, true);
  assert.equal(result, report);
  assert.deepEqual(calls[0], {
    path: '/api/quality/start',
    body: { mesh_job_id: 'mesh-job-12345678', config: { warning_quality_q: 0.2, repetitions: 3 }, benchmark: true, repetitions: 3, warmup: 0 }
  });
  assert.equal(calls.filter(call => call.path.startsWith('/api/job?')).length, 2);
  assert.deepEqual(calls.at(-1), { path: '/api/quality/result', body: { job_id: 'qa-job-12345678' } });
  assert.deepEqual(await bridge.fetchPage({ kind: 'elements', offset: 50, limit: 50, sort_by: 'quality_q', sort_order: 'asc' }), { items: [], total: 0 });
  await bridge.fetchPage({ kind: 'worst', offset: 0, limit: 50, sort_by: 'quality_q', sort_order: 'asc' });
  assert.equal(calls.at(-1).body.kind, 'elements', 'panel worst tab maps to the backend elements page');
  const exported = await bridge.exportResult('csv');
  assert.deepEqual(exported, { path: '/api/quality/export', payload: { job_id: 'qa-job-12345678', format: 'csv' } });
  assert.deepEqual(calls.at(-2), { path: '/api/quality/page', body: { job_id: 'qa-job-12345678', kind: 'elements', offset: 50, limit: 50, sort_by: 'quality_q', sort_order: 'asc' } });

  bridge.setMesh('mesh-job-87654321');
  assert.equal(bridge.jobId, null, 'changing accepted mesh clears the prior QA job');
  assert.equal(await bridge.restore('mesh-job-87654321'), report);
  assert.equal(bridge.jobId, 'mesh-job-87654321', 'cached QA uses the accepted mesh id for paging and export');
  console.log('quality bridge contract checks passed');
}

async function staleResultCheck() {
  let finishResult;
  const report = { schema_version: 'meshcherry-quality-v1' };
  const bridge = create({ api: async path => {
    if (path === '/api/quality/start') return { job_id: 'qa-stale-12345678' };
    if (path.startsWith('/api/job?')) return { status: 'pass' };
    if (path === '/api/quality/result') return new Promise(resolve => { finishResult = () => resolve(report); });
    throw new Error(`Unexpected endpoint ${path}`);
  }, download: async () => null });
  bridge.setMesh('mesh-first-12345678');
  const pending = bridge.run({}, false);
  while (!finishResult) await new Promise(resolve => setImmediate(resolve));
  bridge.setMesh('mesh-second-12345678');
  finishResult();
  await assert.rejects(pending, /Lưới đã thay đổi/);
}

async function stopKeepsPollingCheck() {
  let releaseStatus, polls = 0, stopAccepted = false;
  const report = { schema_version: 'meshcherry-quality-v1' };
  const bridge = create({ api: async (path, body) => {
    if (path === '/api/quality/start') return { job_id: 'qa-stop-12345678' };
    if (path.startsWith('/api/job?')) {
      polls++;
      if (polls === 1) return new Promise(resolve => { releaseStatus = resolve; });
      return { status: 'pass' };
    }
    if (path === '/api/job/stop') { stopAccepted = true; return { accepted: true }; }
    if (path === '/api/quality/result') return report;
    throw new Error(`Unexpected endpoint ${path}`);
  }, download: async () => null });
  bridge.setMesh('mesh-stop-12345678');
  const pending = bridge.run({}, false);
  let settled = false;
  const observed = pending.then(value => { settled = true; return value; }, error => { settled = true; throw error; });
  while (!releaseStatus) await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(await bridge.stop(), { accepted: true });
  assert.equal(stopAccepted, true);
  assert.equal(settled, false, 'accepted stop waits for the shared job to reach a terminal state');
  releaseStatus({ status: 'running' });
  assert.equal(await observed, report);
}

Promise.all([main(), staleResultCheck(), stopKeepsPollingCheck()]).catch(error => { console.error(error); process.exitCode = 1; });
