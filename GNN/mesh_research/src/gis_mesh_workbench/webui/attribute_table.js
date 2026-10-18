(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.MeshCherryAttributeTable = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const MAX_PAGE_SIZE = 200;
  const DEFAULT_PAGE_SIZE = 50;
  const MAX_FEATURE_COLUMNS = 20;

  function mount(container, options) {
    if (!container || !container.ownerDocument) throw new TypeError('Thiếu vùng hiển thị bảng.');
    if (!options || typeof options.fetchPage !== 'function') throw new TypeError('Cần cung cấp hàm tải từng trang.');
    const doc = container.ownerDocument;
    const columns = (options.featureColumns || []).slice(0, 500).map((column) => typeof column === 'string'
      ? { key: column, label: column } : { key: String(column.key), label: String(column.label || column.key) });
    const pageSize = Math.max(1, Math.min(MAX_PAGE_SIZE, Math.floor(Number(options.pageSize) || DEFAULT_PAGE_SIZE)));
    const state = { page: 1, search: '', visible: new Set(columns.slice(0, 10).map((column) => column.key)), rows: [], totalRows: 0, request: 0, abort: null, timer: null, destroyed: false };
    const shell = doc.createElement('section'); shell.className = 'mc-attribute-table';
    const toolbar = doc.createElement('div'); toolbar.className = 'mc-attribute-table__toolbar';
    const searchLabel = doc.createElement('label'); searchLabel.textContent = 'Tìm ID ô';
    const search = doc.createElement('input'); search.type = 'search'; search.placeholder = 'Nhập ID ô'; search.setAttribute('aria-label', 'Tìm ID ô');
    searchLabel.appendChild(search);
    const columnLabel = doc.createElement('label'); columnLabel.textContent = 'Cột thuộc tính';
    const columnPicker = doc.createElement('details'); columnPicker.className = 'mc-attribute-table__columns';
    const columnSummary = doc.createElement('summary'); columnSummary.textContent = 'Chọn cột'; columnPicker.appendChild(columnSummary);
    const columnOptions = doc.createElement('div'); columnOptions.className = 'mc-attribute-table__column-options'; columnPicker.appendChild(columnOptions);
    columns.forEach((column) => {
      const label = doc.createElement('label'); const input = doc.createElement('input');
      input.type = 'checkbox'; input.checked = state.visible.has(column.key); input.dataset.column = column.key;
      input.addEventListener('change', () => {
        if (input.checked && !state.visible.has(column.key) && state.visible.size >= MAX_FEATURE_COLUMNS) { input.checked = false; error.textContent = `Chỉ chọn tối đa ${MAX_FEATURE_COLUMNS} cột thuộc tính.`; return; }
        error.textContent = ''; input.checked ? state.visible.add(column.key) : state.visible.delete(column.key); state.page = 1; load();
      });
      label.append(input, doc.createTextNode(column.label)); columnOptions.appendChild(label);
    });
    columnLabel.appendChild(columnPicker); toolbar.append(searchLabel, columnLabel);
    const status = doc.createElement('p'); status.className = 'mc-attribute-table__status'; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const loading = doc.createElement('progress'); loading.className = 'mc-attribute-table__loading'; loading.removeAttribute('value'); loading.setAttribute('aria-label', 'Đang tải các dòng'); loading.hidden = true;
    const error = doc.createElement('p'); error.className = 'mc-attribute-table__error'; error.setAttribute('role', 'alert');
    const scroller = doc.createElement('div'); scroller.className = 'mc-attribute-table__scroll'; scroller.setAttribute('tabindex', '0'); scroller.setAttribute('role', 'region'); scroller.setAttribute('aria-label', 'Bảng thuộc tính các ô');
    const table = doc.createElement('table'); table.className = 'mc-attribute-table__grid';
    const thead = doc.createElement('thead'), tbody = doc.createElement('tbody'); table.append(thead, tbody); scroller.appendChild(table);
    const pager = doc.createElement('nav'); pager.className = 'mc-attribute-table__pager'; pager.setAttribute('aria-label', 'Các trang của bảng');
    const previous = doc.createElement('button'); previous.type = 'button'; previous.textContent = 'Trước';
    const pageInfo = doc.createElement('span');
    const next = doc.createElement('button'); next.type = 'button'; next.textContent = 'Sau'; pager.append(previous, pageInfo, next);
    shell.append(toolbar, loading, status, error, scroller, pager); container.replaceChildren(shell);

    function visibleColumns() { return columns.filter((column) => state.visible.has(column.key)); }
    function render() {
      const shown = visibleColumns(); thead.replaceChildren(); tbody.replaceChildren();
      const header = doc.createElement('tr');
      const idHeader = doc.createElement('th'); idHeader.scope = 'col'; idHeader.textContent = 'ID ô'; idHeader.className = 'mc-attribute-table__id'; header.appendChild(idHeader);
      const geometryColumns = options.geometryColumns === false ? [] : [{ key: 'area_m2', label: 'Diện tích (m²)' }, { key: 'centroid_x', label: 'Tâm X' }, { key: 'centroid_y', label: 'Tâm Y' }];
      [...geometryColumns, ...shown].forEach((column) => { const th = doc.createElement('th'); th.scope = 'col'; th.textContent = column.label; header.appendChild(th); });
      thead.appendChild(header);
      state.rows.forEach((row) => {
        const tr = doc.createElement('tr'); const id = doc.createElement('th'); id.scope = 'row'; id.className = 'mc-attribute-table__id';
        const cellId = row && (row.id ?? row.cell_id); id.textContent = cellId == null ? '—' : String(cellId); tr.appendChild(id);
        const geometryColumns = options.geometryColumns === false ? [] : [{ key: 'area_m2' }, { key: 'centroid_x' }, { key: 'centroid_y' }];
        [...geometryColumns, ...shown].forEach((column) => { const td = doc.createElement('td'); const value = row ? (column.key in row ? row[column.key] : row.values?.[column.key]) : null; td.textContent = value == null ? '—' : String(value); tr.appendChild(td); }); tbody.appendChild(tr);
      });
      const pages = Math.max(1, Math.ceil(state.totalRows / pageSize));
      pageInfo.textContent = `Trang ${state.page} / ${pages} · ${state.totalRows.toLocaleString('vi-VN')} ô`;
      previous.disabled = state.page <= 1; next.disabled = state.page >= pages;
    }
    async function load() {
      const request = ++state.request;
      if (state.abort) state.abort.abort();
      state.abort = typeof AbortController === 'function' ? new AbortController() : null;
      error.textContent = ''; loading.hidden = false; status.textContent = 'Đang tải các dòng…'; previous.disabled = next.disabled = true;
      try {
        const result = await options.fetchPage({ page: state.page, pageSize, search: state.search, columns: visibleColumns().map((column) => column.key), signal: state.abort && state.abort.signal });
        if (state.destroyed || request !== state.request) return false;
        if (!result || !Array.isArray(result.rows) || !Number.isFinite(Number(result.totalRows ?? result.total_cells))) throw new Error('Phản hồi bảng chưa đầy đủ.');
        state.rows = result.rows.slice(0, pageSize); state.totalRows = Math.max(0, Number(result.totalRows ?? result.total_cells));
        const pages = Math.max(1, Math.ceil(state.totalRows / pageSize));
        state.page = Math.max(1, Math.min(pages, Number(result.page) || state.page));
        render(); loading.hidden = true; status.textContent = state.rows.length ? `Đang xem ${state.rows.length} / ${state.totalRows.toLocaleString('vi-VN')} ô` : (state.totalRows ? 'Trang này chưa có ô.' : 'Không tìm thấy ô phù hợp.'); return true;
      } catch (cause) {
        if (state.destroyed || request !== state.request || (cause && cause.name === 'AbortError')) return false;
        state.rows = []; state.totalRows = 0; render(); loading.hidden = true; status.textContent = 'Không tải được bảng thuộc tính.'; error.textContent = cause && cause.message ? cause.message : 'Yêu cầu thất bại.'; return false;
      }
    }
    search.addEventListener('input', () => {
      state.search = search.value.trim(); state.page = 1;
      state.request += 1;
      if (state.abort) state.abort.abort();
      status.textContent = 'Chuẩn bị tìm kiếm…';
      if (state.timer) clearTimeout(state.timer);
      state.timer = setTimeout(load, 250);
    });
    previous.addEventListener('click', () => { if (state.page > 1) { state.page -= 1; load(); } });
    next.addEventListener('click', () => { if (state.page < Math.ceil(state.totalRows / pageSize)) { state.page += 1; load(); } });
    const ready = load();
    const api = { ready, refresh: load, destroy() { state.destroyed = true; state.request += 1; if (state.abort) state.abort.abort(); if (state.timer) clearTimeout(state.timer); container.replaceChildren(); } };
    return api;
  }

  return { mount, MAX_PAGE_SIZE, MAX_FEATURE_COLUMNS };
});
