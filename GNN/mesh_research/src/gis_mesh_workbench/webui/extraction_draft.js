(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.MeshCherryExtractionDraft = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const MAX_PAGE_SIZE = 200;
  const MAX_VISIBLE_COLUMNS = 20;
  const MAX_FEATURE_COLUMNS = 500;
  const TERMINAL = new Set(['complete', 'completed', 'pass', 'partial', 'failed', 'error', 'cancelled', 'canceled']);
  function mount(container, options) {
    if (!container || !container.ownerDocument) throw new TypeError('Thiếu vùng hiển thị bản nháp.');
    if (!options || typeof options.previewPage !== 'function') throw new TypeError('Cần hàm previewPage.');
    const doc = container.ownerDocument, adapter = options.adapter || {};
    const pageSize = Math.max(1, Math.min(MAX_PAGE_SIZE, Math.floor(Number(options.pageSize) || 50)));
    let nextId = 1;
    const state = { page: 1, manualPage: 1, rows: [], totalRows: 0, columns: [], order: [], systemColumns: (options.systemColumns || []).map((c) => ({ key: String(c.key), name: String(c.name || c.label || c.key) })), manual: [], manualRows: [], visible: new Set(), excluded: new Set(), values: new Map(), initialized: false, reviewed: false, confirmed: false, syncing: false, syncRevision: 0, canonicalDraft: null, busy: false, runStatus: '', stopRequested: false, stopError: false, terminal: false, request: 0, destroyed: false };
    const shell = doc.createElement('section'); shell.className = 'mc-extraction-draft';
    const title = doc.createElement('h2'); title.textContent = 'Rà soát bảng trích xuất';
    const summary = doc.createElement('p'); summary.className = 'mc-extraction-draft__summary';
    const toolbar = doc.createElement('div'); toolbar.className = 'mc-extraction-draft__toolbar';
    const picker = doc.createElement('details'); picker.className = 'mc-extraction-draft__picker';
    const pickerSummary = doc.createElement('summary'); pickerSummary.textContent = 'Chọn tối đa 20 cột hiển thị';
    const pickerItems = doc.createElement('div'); pickerItems.className = 'mc-extraction-draft__picker-items'; picker.append(pickerSummary, pickerItems);
    const addName = doc.createElement('input'); addName.type = 'text'; addName.maxLength = 80; addName.placeholder = 'Tên cột thủ công'; addName.setAttribute('aria-label', 'Tên cột thủ công');
    const addType = doc.createElement('select'); addType.setAttribute('aria-label', 'Kiểu cột thủ công');
    [['text', 'Văn bản'], ['number', 'Số']].forEach(([v, label]) => { const opt = doc.createElement('option'); opt.value = v; opt.textContent = label; addType.appendChild(opt); });
    const addColumn = button(doc, 'Thêm cột');
    toolbar.append(picker, addName, addType, addColumn);
    const notice = doc.createElement('p'); notice.className = 'mc-extraction-draft__notice'; notice.setAttribute('role', 'status'); notice.setAttribute('aria-live', 'polite');
    const error = doc.createElement('p'); error.className = 'mc-extraction-draft__error'; error.setAttribute('role', 'alert');
    const scroller = doc.createElement('div'); scroller.className = 'mc-extraction-draft__scroll'; scroller.setAttribute('role', 'region'); scroller.setAttribute('aria-label', 'Bản xem trước bảng trích xuất'); scroller.setAttribute('tabindex', '0');
    const table = doc.createElement('table'); table.className = 'mc-extraction-draft__table';
    const thead = doc.createElement('thead'), tbody = doc.createElement('tbody'); table.append(thead, tbody); scroller.appendChild(table);
    const manualSection = doc.createElement('section'); manualSection.className = 'mc-extraction-draft__manual-rows';
    const manualTitle = doc.createElement('h3'); manualTitle.textContent = 'Dòng nhập tay · bảng bổ sung';
    const addManualRow = button(doc, 'Thêm dòng thủ công');
    const manualTable = doc.createElement('table'); manualTable.className = 'mc-extraction-draft__table mc-extraction-draft__manual-table';
    const manualHead = doc.createElement('thead'), manualBody = doc.createElement('tbody'); manualTable.append(manualHead, manualBody);
    const manualScroll = doc.createElement('div'); manualScroll.className = 'mc-extraction-draft__scroll'; manualScroll.appendChild(manualTable);
    const manualPager = doc.createElement('nav'); manualPager.className = 'mc-extraction-draft__pager'; manualPager.setAttribute('aria-label', 'Trang các dòng nhập tay');
    const manualPrevious = button(doc, 'Trước'), manualPageInfo = doc.createElement('span'), manualNext = button(doc, 'Sau'); manualPager.append(manualPrevious, manualPageInfo, manualNext);
    manualSection.append(manualTitle, addManualRow, manualScroll, manualPager);
    const pager = doc.createElement('nav'); pager.className = 'mc-extraction-draft__pager'; pager.setAttribute('aria-label', 'Trang bản xem trước');
    const previous = button(doc, 'Trước'), pageInfo = doc.createElement('span'), next = button(doc, 'Sau'); pager.append(previous, pageInfo, next);
    const reviewLabel = doc.createElement('label'); reviewLabel.className = 'mc-extraction-draft__review';
    const review = doc.createElement('input'); review.type = 'checkbox'; review.className = 'mc-extraction-draft__review-check'; review.disabled = true;
    reviewLabel.append(review, doc.createTextNode('Tôi đã rà soát bảng và xác nhận các cột, giá trị thủ công, cùng các dòng bị loại.'));
    const actions = doc.createElement('div'); actions.className = 'mc-extraction-draft__actions';
    const confirm = button(doc, 'Xác nhận bảng & extract'); confirm.className = 'primary'; confirm.disabled = true;
    const stop = button(doc, 'Dừng trích xuất'); stop.hidden = true;
    actions.append(confirm, stop);
    shell.append(title, summary, toolbar, notice, error, scroller, pager, manualSection, reviewLabel, actions); container.replaceChildren(shell);

    function allFeatureColumns() { const byKey = new Map([...state.columns, ...state.manual].filter((c) => !c.removed).map((c) => [c.key, c])); return state.order.map((key) => byKey.get(key)).filter(Boolean).slice(0, MAX_FEATURE_COLUMNS); }
    function visibleFeatureColumns() { return allFeatureColumns().filter((c) => state.visible.has(c.key)); }
    function canEdit() { return state.initialized && !state.busy && !state.syncing && !state.confirmed; }
    function adoptDraft(draft) {
      if (!draft || !Array.isArray(draft.columns)) return;
      state.canonicalDraft = draft;
      state.columns = draft.columns.filter((c) => c.type === 'raster').map((c) => ({ key: String(c.id), name: String(c.name), kind: 'raster', source: String(c.source_name), excluded: false }));
      state.manual = draft.columns.filter((c) => c.type === 'manual').map((c) => ({ key: String(c.id), name: String(c.name), kind: 'manual', valueType: c.value_type, removed: false }));
      state.order = draft.columns.map((c) => String(c.id));
      state.visible.clear(); [...state.columns, ...state.manual].slice(0, MAX_VISIBLE_COLUMNS).forEach((c) => state.visible.add(c.key));
      state.excluded = new Set(Array.isArray(draft.excluded_cell_ids) ? draft.excluded_cell_ids : []);
      state.values.clear();
      (draft.rows || []).forEach((row) => Object.entries(row.values || {}).forEach(([columnId, value]) => state.values.set(`${row.cell_id}\u0000${columnId}`, value)));
      state.manualRows = (draft.manual_rows || []).map((row) => ({ id: row.row_id, values: { ...(row.values || {}) } }));
    }
    function plan() {
      const editedRows = new Map();
      const manualIds = new Set(state.manual.filter((col) => !col.removed).map((col) => col.key));
      for (const [key, value] of state.values.entries()) { const [cellId, columnId] = key.split('\u0000'); if (!manualIds.has(columnId)) continue; if (!editedRows.has(cellId)) editedRows.set(cellId, {}); editedRows.get(cellId)[columnId] = value; }
      return {
        mesh_job_id: options.meshJobId || (options.draft && options.draft.mesh_job_id) || '',
        geometry_digest: options.geometryDigest || (options.draft && options.draft.geometry_digest) || '',
        columns: allFeatureColumns().filter((c) => !c.excluded).map((c) => c.kind === 'manual'
          ? { id: c.key, type: 'manual', name: c.name, value_type: c.valueType === 'number' ? 'number' : 'text' }
          : { id: c.key, type: 'raster', source_name: c.source, name: c.name }),
        rows: [...editedRows.entries()].map(([cell_id, values]) => ({ cell_id, values })),
        excluded_cell_ids: [...state.excluded],
        manual_rows: state.manualRows.map((row) => ({ row_id: row.id, values: Object.fromEntries(Object.entries(row.values).filter(([columnId]) => manualIds.has(columnId))) })),
      };
    }
    async function syncCanonical(payload, revision) {
      if (typeof options.onChange !== 'function') return payload;
      const result = await options.onChange(payload);
      if (revision !== state.syncRevision || state.destroyed) return null;
      const canonical = result && result.draft ? result.draft : (result && Array.isArray(result.columns) && 'mesh_job_id' in result ? result : payload);
      state.canonicalDraft = canonical;
      return canonical;
    }
    function invalidate(message) {
      state.confirmed = false; state.reviewed = false; review.checked = false;
      reviewLabel.hidden = false; confirm.textContent = 'Xác nhận bảng & extract'; confirm.disabled = true; review.disabled = true;
      notice.textContent = message || 'Bảng đã thay đổi; cần rà soát và xác nhận lại trước khi chạy.';
      const revision = ++state.syncRevision; state.syncing = true; state.canonicalDraft = null; error.textContent = '';
      render();
      syncCanonical(plan(), revision).then((canonical) => {
        if (revision !== state.syncRevision || state.destroyed) return;
        state.syncing = false; review.disabled = !state.initialized || state.busy; confirm.disabled = !state.reviewed || state.busy || state.confirmed;
        notice.textContent = canonical ? 'Bản nháp đã được máy chủ rà soát; hãy kiểm tra lại bảng.' : 'Không nhận được bản nháp chuẩn hóa.';
      }).catch((cause) => {
        if (revision !== state.syncRevision || state.destroyed) return;
        state.syncing = false; review.disabled = true; confirm.disabled = true; error.textContent = cause && cause.message ? cause.message : 'Không thể đồng bộ bản nháp với máy chủ.'; notice.textContent = 'Sửa đổi chưa được xác nhận với máy chủ.';
      });
    }
    function render() {
      pickerItems.replaceChildren();
      allFeatureColumns().forEach((col) => {
        const label = doc.createElement('label'), toggle = doc.createElement('input'); toggle.type = 'checkbox'; toggle.checked = state.visible.has(col.key); toggle.disabled = !canEdit() || (!toggle.checked && state.visible.size >= MAX_VISIBLE_COLUMNS);
        toggle.addEventListener('change', () => { if (!canEdit()) return; if (toggle.checked && state.visible.size >= MAX_VISIBLE_COLUMNS) { toggle.checked = false; return; } toggle.checked ? state.visible.add(col.key) : state.visible.delete(col.key); render(); });
        label.append(toggle, doc.createTextNode(col.name)); pickerItems.appendChild(label);
      });
      thead.replaceChildren(); tbody.replaceChildren();
      const header = doc.createElement('tr');
      const rowHead = doc.createElement('th'); rowHead.scope = 'col'; rowHead.textContent = 'Dòng lưới'; rowHead.className = 'mc-extraction-draft__frozen'; header.appendChild(rowHead);
      state.systemColumns.forEach((col) => { const th = doc.createElement('th'); th.scope = 'col'; th.textContent = col.name; th.setAttribute('aria-readonly', 'true'); header.appendChild(th); });
      visibleFeatureColumns().forEach((col, index) => {
        const th = doc.createElement('th'); th.scope = 'col';
        const caption = doc.createElement('div'); caption.className = 'mc-extraction-draft__column-title';
        const name = doc.createElement('input'); name.type = 'text'; name.value = col.name; name.maxLength = 80; name.setAttribute('aria-label', `Tên cột ${col.name}`); name.disabled = col.kind === 'computed' || !canEdit();
        name.addEventListener('change', () => { if (!canEdit()) return; const value = name.value.trim(); if (!value) { name.value = col.name; return; } if (col.name !== value) { col.name = value; invalidate(); render(); } });
        caption.appendChild(name);
        if (col.kind === 'raster' || col.kind === 'manual') {
          const controls = doc.createElement('div'); controls.className = 'mc-extraction-draft__column-controls';
          const orderIndex = allFeatureColumns().indexOf(col);
          const moveLeft = button(doc, '←'); moveLeft.setAttribute('aria-label', `Chuyển ${col.name} sang trái`); moveLeft.disabled = orderIndex === 0 || !canEdit();
          moveLeft.addEventListener('click', () => { if (canEdit()) moveColumn(col, -1); });
          const moveRight = button(doc, '→'); moveRight.setAttribute('aria-label', `Chuyển ${col.name} sang phải`); moveRight.disabled = orderIndex >= allFeatureColumns().length - 1 || !canEdit();
          moveRight.addEventListener('click', () => { if (canEdit()) moveColumn(col, 1); });
          controls.append(moveLeft, moveRight);
          if (col.kind === 'raster') {
            const remove = button(doc, col.excluded ? 'Thêm lại' : 'Loại cột'); remove.disabled = !canEdit(); remove.addEventListener('click', () => { if (!canEdit()) return; col.excluded = !col.excluded; invalidate(); render(); }); controls.appendChild(remove);
          } else {
            const remove = button(doc, 'Xóa cột'); remove.disabled = !canEdit(); remove.addEventListener('click', () => { if (!canEdit()) return; col.removed = true; state.order = state.order.filter((key) => key !== col.key); for (const key of [...state.values.keys()]) if (key.endsWith(`\u0000${col.key}`)) state.values.delete(key); state.manualRows.forEach((row) => { delete row.values[col.key]; }); invalidate(); render(); }); controls.appendChild(remove);
          }
          caption.appendChild(controls);
        }
        th.appendChild(caption); header.appendChild(th);
      });
      const actionsHead = doc.createElement('th'); actionsHead.scope = 'col'; actionsHead.textContent = 'Dòng'; header.appendChild(actionsHead); thead.appendChild(header);
      state.rows.forEach((row, rowIndex) => {
        const tr = doc.createElement('tr'), rowId = String(row.id ?? row.cell_id ?? row.row_id ?? `row-${(state.page - 1) * pageSize + rowIndex + 1}`);
        const rowCell = doc.createElement('th'); rowCell.scope = 'row'; rowCell.className = 'mc-extraction-draft__frozen'; rowCell.textContent = rowId; tr.appendChild(rowCell);
        state.systemColumns.forEach((col) => { const td = doc.createElement('td'); const value = row.values && Object.prototype.hasOwnProperty.call(row.values, col.key) ? row.values[col.key] : row[col.key]; td.textContent = value == null ? '—' : String(value); td.setAttribute('aria-readonly', 'true'); tr.appendChild(td); });
        visibleFeatureColumns().forEach((col) => {
          const td = doc.createElement('td');
          if (col.kind === 'manual') {
            const input = doc.createElement('input'); input.type = col.valueType === 'number' ? 'number' : 'text'; input.disabled = !canEdit(); input.value = state.values.get(`${rowId}\u0000${col.key}`) ?? '';
            input.setAttribute('aria-label', `${col.name}, dòng ${rowId}`);
            input.addEventListener('change', () => { if (!canEdit()) return; const rawValue = input.value; if (col.valueType === 'number' && rawValue !== '' && !Number.isFinite(Number(rawValue))) { error.textContent = 'Giá trị số không hợp lệ.'; return; } error.textContent = ''; state.values.set(`${rowId}\u0000${col.key}`, rawValue === '' ? null : (col.valueType === 'number' ? Number(rawValue) : rawValue)); invalidate(); }); td.appendChild(input);
          } else {
            const raw = row.values && Object.prototype.hasOwnProperty.call(row.values, col.key) ? row.values[col.key] : row[col.key];
            td.textContent = col.kind === 'raster' ? 'Chưa tính' : (raw == null ? '—' : String(raw));
            if (col.kind === 'computed') td.setAttribute('aria-readonly', 'true');
          }
          tr.appendChild(td);
        });
        const rowActions = doc.createElement('td'); const exclude = button(doc, state.excluded.has(rowId) ? 'Đưa lại' : 'Loại dòng'); exclude.disabled = !canEdit();
        exclude.addEventListener('click', () => { if (!canEdit()) return; state.excluded.has(rowId) ? state.excluded.delete(rowId) : state.excluded.add(rowId); invalidate(); render(); }); rowActions.appendChild(exclude); tr.appendChild(rowActions); tbody.appendChild(tr);
      });
      const pages = Math.max(1, Math.ceil(state.totalRows / pageSize)); pageInfo.textContent = `Trang ${state.page} / ${pages} · ${state.totalRows.toLocaleString('vi-VN')} ô`;
      previous.disabled = state.page <= 1; next.disabled = state.page >= pages;
      summary.textContent = `${state.totalRows.toLocaleString('vi-VN')} ô lưới · tối đa ${MAX_VISIBLE_COLUMNS} cột hiển thị đồng thời.`;
      renderManualRows();
      review.disabled = !state.initialized || state.syncing || Boolean(error.textContent);
      confirm.disabled = !state.initialized || !state.reviewed || state.busy || state.confirmed || state.syncing || Boolean(error.textContent);
      stop.hidden = !state.busy || state.terminal;
      addColumn.disabled = !canEdit() || allFeatureColumns().length >= MAX_FEATURE_COLUMNS;
      addName.disabled = !canEdit(); addType.disabled = !canEdit(); addManualRow.disabled = !canEdit();
    }
    function renderManualRows() {
      manualHead.replaceChildren(); manualBody.replaceChildren();
      const manualColumns = visibleFeatureColumns().filter((col) => col.kind === 'manual').slice(0, MAX_VISIBLE_COLUMNS);
      const head = doc.createElement('tr'); const idHead = doc.createElement('th'); idHead.scope = 'col'; idHead.textContent = 'Mã dòng'; head.appendChild(idHead);
      manualColumns.forEach((col) => { const th = doc.createElement('th'); th.scope = 'col'; th.textContent = col.name; head.appendChild(th); });
      const actionHead = doc.createElement('th'); actionHead.scope = 'col'; actionHead.textContent = 'Thao tác'; head.appendChild(actionHead); manualHead.appendChild(head);
      const pages = Math.max(1, Math.ceil(state.manualRows.length / pageSize));
      state.manualPage = Math.max(1, Math.min(state.manualPage, pages));
      const start = (state.manualPage - 1) * pageSize;
      state.manualRows.slice(start, start + pageSize).forEach((row) => {
        const tr = doc.createElement('tr'), id = doc.createElement('th'); id.scope = 'row'; id.textContent = row.id; tr.appendChild(id);
        manualColumns.forEach((col) => { const td = doc.createElement('td'), input = doc.createElement('input'); input.type = col.valueType === 'number' ? 'number' : 'text'; input.disabled = !canEdit(); input.value = row.values[col.key] ?? ''; input.setAttribute('aria-label', `${col.name}, dòng thủ công ${row.id}`); input.addEventListener('change', () => { if (!canEdit()) return; const rawValue = input.value; if (col.valueType === 'number' && rawValue !== '' && !Number.isFinite(Number(rawValue))) { error.textContent = 'Giá trị số không hợp lệ.'; return; } error.textContent = ''; row.values[col.key] = rawValue === '' ? null : (col.valueType === 'number' ? Number(rawValue) : rawValue); invalidate(); }); td.appendChild(input); tr.appendChild(td); });
        const td = doc.createElement('td'), remove = button(doc, 'Xóa dòng'); remove.disabled = !canEdit(); remove.addEventListener('click', () => { if (!canEdit()) return; state.manualRows = state.manualRows.filter((item) => item !== row); invalidate(); render(); }); td.appendChild(remove); tr.appendChild(td); manualBody.appendChild(tr);
      });
      manualPageInfo.textContent = `Trang ${state.manualPage} / ${pages} · ${state.manualRows.length.toLocaleString('vi-VN')} dòng`;
      manualPrevious.disabled = state.manualPage <= 1; manualNext.disabled = state.manualPage >= pages;
      manualSection.hidden = state.manual.filter((col) => !col.removed).length === 0;
    }
    function moveColumn(col, delta) { const i = state.order.indexOf(col.key), j = i + delta; if (i < 0 || j < 0 || j >= state.order.length) return; [state.order[i], state.order[j]] = [state.order[j], state.order[i]]; invalidate(); render(); }
    async function load() {
      const request = ++state.request; error.textContent = ''; notice.textContent = 'Đang tải bản xem trước…'; previous.disabled = next.disabled = true;
      try {
        const currentDraft = state.initialized ? (state.canonicalDraft || plan()) : (options.initialDraft ?? null);
        const result = await options.previewPage({ page: state.page, pageSize, mesh_job_id: options.meshJobId || '', visible_columns: state.initialized ? visibleFeatureColumns().map((c) => c.key) : undefined, draft: currentDraft });
        if (state.destroyed || request !== state.request) return false;
        if (!result || !Array.isArray(result.rows)) throw new Error('Adapter chưa trả về rows.');
        state.rows = result.rows.filter((row) => row.row_kind !== 'manual').slice(0, pageSize); state.totalRows = Math.max(0, Number(result.accepted_cells ?? result.totalCells ?? result.total_cells ?? result.totalRows) || 0);
        if (!state.initialized) adoptDraft(result.draft || currentDraft);
        else if (result.rows.some((row) => Object.prototype.hasOwnProperty.call(row, 'excluded'))) result.rows.forEach((row) => { if (row.row_kind !== 'manual') { row.excluded ? state.excluded.add(String(row.cell_id)) : state.excluded.delete(String(row.cell_id)); } });
        if (result.columns && !state.columns.length && !state.manual.length) { state.columns = result.columns.filter((c) => (c.type || c.kind) === 'raster').slice(0, MAX_FEATURE_COLUMNS).map((c) => ({ key: String(c.id || c.key), name: String(c.name || c.label || c.key), kind: 'raster', source: String(c.source_name || c.source || c.key), excluded: false })); state.order = state.columns.map((c) => c.key); state.columns.slice(0, MAX_VISIBLE_COLUMNS).forEach((c) => state.visible.add(c.key)); }
        state.initialized = true;
        state.page = Math.max(1, Math.min(Math.max(1, Math.ceil(state.totalRows / pageSize)), Number(result.page) || state.page));
        render(); notice.textContent = state.rows.length ? 'Giá trị raster chưa được tính cho đến khi xác nhận.' : 'Không có dòng trên trang này.'; return true;
      } catch (cause) { if (state.destroyed || request !== state.request) return false; error.textContent = cause && cause.message ? cause.message : 'Không tải được bản xem trước.'; notice.textContent = ''; return false; }
    }
    addColumn.addEventListener('click', () => { if (!canEdit()) return; const name = addName.value.trim(); if (!name) { error.textContent = 'Nhập tên cột trước khi thêm.'; return; } if (allFeatureColumns().some((c) => c.name.toLocaleLowerCase() === name.toLocaleLowerCase())) { error.textContent = 'Tên cột đã được dùng.'; return; } const key = `manual-${nextId++}`; state.manual.push({ key, name, kind: 'manual', valueType: addType.value, removed: false }); state.order.push(key); if (state.visible.size >= MAX_VISIBLE_COLUMNS) state.visible.delete(state.visible.values().next().value); state.visible.add(key); addName.value = ''; error.textContent = ''; invalidate(); render(); });
    addManualRow.addEventListener('click', () => { if (!canEdit()) return; const id = `manual-row-${nextId++}`; state.manualRows.push({ id, values: {} }); invalidate(); render(); });
    review.addEventListener('change', () => { if (!state.initialized || state.syncing || error.textContent) { review.checked = false; return; } state.reviewed = review.checked; confirm.disabled = !state.reviewed || state.busy || state.confirmed || state.syncing || Boolean(error.textContent); });
    confirm.addEventListener('click', async () => {
      if (!state.initialized || !state.reviewed || state.confirmed || state.busy || state.syncing || error.textContent) return;
      const revision = ++state.syncRevision; state.syncing = true; confirm.disabled = true; review.disabled = true; notice.textContent = 'Đang xác nhận bản nháp chuẩn hóa…'; render();
      try {
        const canonical = await syncCanonical(plan(), revision);
        if (revision !== state.syncRevision || state.destroyed) return;
        if (!canonical) throw new Error('Máy chủ chưa trả về bản nháp chuẩn hóa.');
        state.canonicalDraft = canonical;
        const callback = options.onConfirm || adapter.onConfirm;
        if (typeof callback === 'function') await callback(canonical);
        if (revision !== state.syncRevision || state.destroyed) return;
        state.confirmed = true; reviewLabel.hidden = true; confirm.textContent = 'Đã xác nhận'; notice.textContent = 'Bảng đã được xác nhận để trích xuất.';
      } catch (cause) { error.textContent = cause && cause.message ? cause.message : 'Không thể xác nhận bản nháp.'; notice.textContent = 'Bản nháp cần được sửa hoặc đồng bộ lại.'; review.disabled = false; }
      finally { if (revision === state.syncRevision) state.syncing = false; render(); }
    });
    stop.addEventListener('click', async () => { if (!state.busy || state.terminal || state.stopRequested) return; state.stopRequested = true; state.stopError = false; stop.disabled = true; stop.textContent = 'Đang yêu cầu dừng…'; notice.textContent = 'Đã gửi yêu cầu dừng; đang chờ trạng thái cuối từ bộ chạy.'; try { const callback = options.onStop || adapter.onStop; if (typeof callback !== 'function') throw new Error('Không có bộ nhận yêu cầu dừng.'); const result = await callback(); if (result === false || (result && result.accepted === false)) throw new Error((result && result.message) || 'Yêu cầu dừng chưa được chấp nhận.'); } catch (cause) { if (state.terminal || state.runStatus === 'stopping') return; error.textContent = cause && cause.message ? cause.message : 'Không gửi được yêu cầu dừng.'; state.stopRequested = false; state.stopError = true; stop.disabled = false; stop.textContent = 'Thử gửi lại yêu cầu dừng'; } });
    previous.addEventListener('click', () => { if (state.page > 1) { state.page--; load(); } }); next.addEventListener('click', () => { if (state.page < Math.ceil(state.totalRows / pageSize)) { state.page++; load(); } });
    manualPrevious.addEventListener('click', () => { if (state.manualPage > 1) { state.manualPage--; renderManualRows(); } }); manualNext.addEventListener('click', () => { if (state.manualPage < Math.ceil(state.manualRows.length / pageSize)) { state.manualPage++; renderManualRows(); } });
    const ready = load();
    return {
      ready,
      refresh: load,
      setRunning(status) { const label = status && typeof status === 'object' ? (status.status || status.stage || '') : status; const value = String(label || '').toLowerCase(); state.runStatus = value; state.busy = status === true || ['running', 'stopping', 'queued', 'working'].includes(value); state.terminal = TERMINAL.has(value); if (state.terminal) { state.busy = false; state.stopRequested = false; state.stopError = false; stop.textContent = 'Dừng trích xuất'; stop.disabled = false; notice.textContent = `Trạng thái trích xuất: ${label}.`; } else if (value === 'stopping') { state.stopRequested = true; stop.textContent = 'Đang yêu cầu dừng…'; stop.disabled = true; notice.textContent = 'Đang chờ trạng thái cuối từ bộ chạy.'; } render(); },
      getPlan: plan,
      destroy() { state.destroyed = true; state.request++; container.replaceChildren(); },
    };
  }
  function button(doc, text) { const result = doc.createElement('button'); result.type = 'button'; result.textContent = text; return result; }
  return { mount, MAX_PAGE_SIZE, MAX_VISIBLE_COLUMNS, MAX_FEATURE_COLUMNS };
});
