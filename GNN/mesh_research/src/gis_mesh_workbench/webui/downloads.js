(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.MeshCherryDownloads = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const MAX_FALLBACK_BYTES = 64 * 1024 * 1024;

  function safeFilename(value) {
    const name = String(value || 'mesh-export.dat').normalize('NFKC').replace(/[\\/:*?"<>|\x00-\x1f]/g, '_').replace(/^\.+/, '').trim();
    return (name || 'mesh-export.dat').slice(0, 180);
  }

  function mount(container, options) {
    if (!container || !container.ownerDocument) throw new TypeError('A DOM container is required');
    if (!options) throw new TypeError('options are required');
    const doc = container.ownerDocument;
    const fetchImpl = options.fetch || (typeof fetch === 'function' ? fetch.bind(globalThis) : null);
    const URLImpl = options.URL || (typeof URL !== 'undefined' ? URL : null);
    const filename = safeFilename(options.filename);
    const state = { phase: 'idle', path: null, controller: null, running: false, destroyed: false };
    const panel = doc.createElement('section'); panel.className = 'mc-download';
    const title = doc.createElement('h3'); title.textContent = options.title || 'Xuất tệp';
    const message = doc.createElement('p'); message.className = 'mc-download__status'; message.setAttribute('role', 'status'); message.setAttribute('aria-live', 'polite'); message.textContent = 'Sẵn sàng xuất tệp.';
    const progress = doc.createElement('progress'); progress.className = 'mc-download__progress'; progress.max = 100; progress.removeAttribute('value'); progress.setAttribute('aria-label', 'Tiến độ truyền tệp');
    const buttons = doc.createElement('div'); buttons.className = 'mc-download__actions';
    const start = doc.createElement('button'); start.type = 'button'; start.textContent = 'Xuất'; start.hidden = !!options.externalOnly;
    const cancel = doc.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Hủy'; cancel.hidden = true;
    buttons.append(start, cancel); panel.append(title, message, progress, buttons); container.replaceChildren(panel);

    function setProgress(loaded, total) {
      const known = Number.isFinite(total) && total > 0;
      if (known) { progress.value = Math.min(100, loaded / total * 100); message.textContent = `Đang truyền ${formatBytes(loaded)} / ${formatBytes(total)} (${Math.min(100, Math.round(loaded / total * 100))}%).`; }
      else { progress.removeAttribute('value'); message.textContent = `Đang truyền ${formatBytes(loaded)} · chưa biết tổng dung lượng`; }
    }
    function formatBytes(value) {
      if (!Number.isFinite(value) || value < 0) return '0 B';
      if (value < 1024) return `${Math.round(value)} B`;
      const units = ['KB', 'MB', 'GB', 'TB']; let size = value / 1024, unit = 0;
      while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
      return `${size.toFixed(size < 10 ? 1 : 0)} ${units[unit]}`;
    }
  async function fallbackSave(request) {
      if (!fetchImpl) throw new Error('Fetch is unavailable in this browser.');
      const fetchOptions = { ...(request.fetchOptions || {}), signal: state.controller.signal };
      if (request.method || !fetchOptions.method) fetchOptions.method = request.method || 'GET';
      if (request.headers !== undefined) fetchOptions.headers = request.headers;
      if (request.body !== undefined) fetchOptions.body = request.body;
      const response = await fetchImpl(request.url, fetchOptions);
      if (!response || !response.ok) throw new Error(`Export request failed${response && response.status ? ` (${response.status})` : ''}.`);
      const length = Number(response.headers && response.headers.get && response.headers.get('content-length'));
      const total = Number.isFinite(length) && length > 0 ? length : null;
      if (total !== null && total > MAX_FALLBACK_BYTES) { try { await response.body?.cancel?.(); } catch (_) {} throw new Error('Tệp vượt quá 64 MiB cho tải qua trình duyệt. Hãy dùng bản desktop để chọn nơi lưu trực tiếp.'); }
      let blob;
      if (response.body && typeof response.body.getReader === 'function') {
        const reader = response.body.getReader(), chunks = []; let loaded = 0;
        try {
          while (true) {
            const part = await reader.read(); if (part.done) break;
            loaded += part.value.byteLength;
            if (loaded > MAX_FALLBACK_BYTES) { await reader.cancel(); throw new Error('Tệp vượt quá 64 MiB cho tải qua trình duyệt. Hãy dùng bản desktop để chọn nơi lưu trực tiếp.'); }
            chunks.push(part.value); setProgress(loaded, total);
          }
        } catch (error) { try { await reader.cancel(); } catch (_) {} throw error; }
        blob = new Blob(chunks, { type: response.headers?.get?.('content-type') || 'application/octet-stream' });
      } else {
        throw new Error('Không thể tải an toàn vì trình duyệt không hỗ trợ truyền từng phần. Hãy dùng bản desktop để chọn nơi lưu trực tiếp.');
      }
      state.phase = 'exporting'; message.textContent = 'Đã nhận tệp. Đang khởi động tải xuống trong trình duyệt…';
      const objectUrl = URLImpl.createObjectURL(blob), anchor = doc.createElement('a');
      anchor.href = objectUrl; anchor.download = request.filename; anchor.hidden = true;
      doc.body.appendChild(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URLImpl.revokeObjectURL(objectUrl), 1000);
      state.phase = 'exported'; message.textContent = 'Đã yêu cầu trình duyệt tải xuống. Trình duyệt không cung cấp đường dẫn đã lưu.';
      return { phase: state.phase, path: null, filename };
    }
    async function run(requestOverrides) {
      if (state.running || state.destroyed) return null;
      const request = { url: options.url, filename, nativeSave: options.nativeSave, method: options.method, headers: options.headers, body: options.body, fetchOptions: options.fetchOptions, ...(requestOverrides || {}) };
      request.filename = safeFilename(request.filename);
      if (!request.url && typeof request.nativeSave !== 'function') throw new TypeError('url or nativeSave is required');
      state.running = true; state.phase = 'transferring'; state.path = null;
      state.controller = typeof AbortController === 'function' ? new AbortController() : null;
      start.disabled = true; cancel.hidden = false; progress.removeAttribute('value'); message.textContent = 'Đang chuẩn bị xuất…';
      try {
        let result;
        if (typeof request.nativeSave === 'function') {
          result = await request.nativeSave({ url: request.url, filename: request.filename, fetchOptions: request.fetchOptions, signal: state.controller && state.controller.signal, onProgress: setProgress });
          state.path = result && typeof result.path === 'string' ? result.path : null;
          const resultState = result && result.state;
          state.phase = resultState === 'cancelled' ? 'cancelled' : resultState === 'error' ? 'error' : resultState === 'saved' && state.path ? 'saved' : 'error';
          const nativeError=state.phase==='error'?String(result.error||(resultState==='error'&&result.message)||'Trình lưu không xác nhận đã lưu tệp và đường dẫn đích.'):'';
          message.textContent = state.phase === 'saved' ? `Đã lưu tại ${state.path}` : state.phase === 'cancelled' ? 'Đã hủy thao tác lưu.' : nativeError;
          if (state.phase === 'error') return { phase: state.phase, error: nativeError };
          return { phase: state.phase, path: state.path, filename: request.filename };
        } else result = await fallbackSave(request);
        return result;
      } catch (error) {
        if (state.controller && state.controller.signal.aborted || (error && error.name === 'AbortError')) { state.phase = 'cancelled'; message.textContent = 'Đã hủy thao tác xuất.'; return { phase: 'cancelled', path: null }; }
        state.phase = 'error'; message.textContent = error && error.message ? error.message : 'Xuất tệp thất bại.'; return { phase: 'error', error };
      } finally {
        state.running = false; state.controller = null; start.disabled = false; cancel.hidden = true;
      }
    }
    start.addEventListener('click', run);
    cancel.addEventListener('click', () => { if (state.controller) state.controller.abort(); });
    return { start: run, cancel() { if (state.controller) state.controller.abort(); }, getState() { return { phase: state.phase, path: state.path, filename }; }, destroy() { state.destroyed = true; if (state.controller) state.controller.abort(); container.replaceChildren(); } };
  }

  return { mount, safeFilename, MAX_FALLBACK_BYTES };
});
