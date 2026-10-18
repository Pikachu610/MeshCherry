/* Connects the quality explorer to MeshCherry's authenticated QA endpoints. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.MeshCherryQualityBridge = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const TERMINAL = new Set(['pass', 'fail', 'cancelled']);
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

  function create(options = {}) {
    if (typeof options.api !== 'function') throw new TypeError('Quality bridge needs an API request function.');
    let meshJobId = null;
    let activeJobId = null;
    let lastJobId = null;
    let requestVersion = 0;

    async function run(config, benchmark = false) {
      if (!meshJobId) throw new Error('Chưa có lưới đã chấp nhận để kiểm tra.');
      const version = ++requestVersion;
      const started = await options.api('/api/quality/start', {
        mesh_job_id: meshJobId,
        config,
        benchmark: Boolean(benchmark),
        repetitions: benchmark ? config.repetitions : 1,
        warmup: 0
      });
      const jobId = started.job_id;
      if (!jobId) throw new Error('Máy chủ không trả mã lượt QA.');
      if (version !== requestVersion) {
        await options.api('/api/job/stop', { job_id: jobId }).catch(() => {});
        throw new Error('Lưới đã thay đổi trong khi khởi chạy QA.');
      }
      activeJobId = jobId;
      lastJobId = jobId;
      while (version === requestVersion) {
        const status = await options.api(`/api/job?id=${encodeURIComponent(jobId)}`);
        const state = status.status || status.state;
        options.onStatus?.(status);
        if (TERMINAL.has(state)) {
          activeJobId = null;
          if (state !== 'pass') throw new Error(status.result?.error || status.result?.message || `Lượt QA kết thúc: ${state}.`);
          const result = await options.api('/api/quality/result', { job_id: jobId });
          if (version !== requestVersion) throw new Error('Lưới đã thay đổi trước khi tải xong kết quả QA.');
          return result;
        }
        if (!['running', 'stopping', 'queued', 'pending'].includes(state)) throw new Error(`Trạng thái lượt QA không được hỗ trợ: ${state || 'không có'}.`);
        await wait(400);
      }
      throw new Error('Lượt QA đã thay đổi hoặc bị dừng.');
    }

    async function fetchPage(args) {
      const jobId = options.resultJobId?.() || lastJobId;
      if (!jobId) throw new Error('Thiếu mã lượt QA để tải trang.');
      return options.api('/api/quality/page', { job_id: jobId, ...args, kind: args.kind === 'worst' ? 'elements' : args.kind });
    }

    async function exportResult(format) {
      const jobId = options.resultJobId?.() || lastJobId;
      if (!jobId) throw new Error('Thiếu mã lượt QA để xuất kết quả.');
      return options.download('/api/quality/export', { job_id: jobId, format });
    }

    async function stop() {
      if (!activeJobId) return { accepted: false };
      const result = await options.api('/api/job/stop', { job_id: activeJobId });
      return result;
    }

    function setMesh(id) {
      const next = id || null;
      if (next !== meshJobId) {
        const previousJob = activeJobId;
        requestVersion++;
        meshJobId = next;
        activeJobId = null;
        lastJobId = null;
        if (previousJob) options.api('/api/job/stop', { job_id: previousJob }).catch(() => {});
      }
      return meshJobId;
    }

    async function restore(meshId) {
      const expected = meshId || meshJobId, version = requestVersion;
      if (!expected || expected !== meshJobId) return null;
      let response;
      try { response = await options.api('/api/quality/latest', { mesh_job_id: expected }); }
      catch (error) { if (error?.status === 404) return null; throw error; }
      if (version !== requestVersion || expected !== meshJobId || response?.status === 'not_available') return null;
      const report = response?.report || response;
      if (!report || report.schema_version !== 'meshcherry-quality-v1') return null;
      lastJobId = response.job_id || report.job_id || expected;
      return report;
    }

    return { run, fetchPage, exportResult, stop, setMesh, restore, get jobId() { return lastJobId; } };
  }

  return { create };
});
