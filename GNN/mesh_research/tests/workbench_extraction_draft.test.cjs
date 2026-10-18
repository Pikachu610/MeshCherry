const { test } = require('node:test');
const assert = require('node:assert/strict');
const { mount, MAX_PAGE_SIZE, MAX_VISIBLE_COLUMNS, MAX_FEATURE_COLUMNS } = require('../src/gis_mesh_workbench/webui/extraction_draft.js');

class Element {
  constructor(doc, tagName = '') { this.ownerDocument = doc; this.tagName = tagName; this.children = []; this.attributes = {}; this.handlers = {}; this.dataset = {}; this.value = ''; this.hidden = false; this.disabled = false; this.checked = false; }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.children.push(item); return item; }
  replaceChildren(...items) { this.children = items; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(key, fn) { this.handlers[key] = fn; }
}
function fixture() { const doc = { createElement(tag) { return new Element(doc, tag); }, createTextNode(textContent) { return { textContent }; } }; return { doc, container: new Element(doc) }; }
function all(el) { return [el, ...(el.children || []).flatMap(all)]; }
function find(el, predicate) { return all(el).find(predicate) || null; }
function byText(el, text) { return find(el, (node) => node.textContent === text); }
function inputFor(el, label) { return find(el, (node) => node.tagName === 'input' && node.attributes['aria-label'] === label); }
async function flush() { for (let i = 0; i < 8; i++) await Promise.resolve(); }

test('preview is paged, capped, and raster values stay explicit placeholders until extraction', async () => {
  const { container } = fixture(); let received;
  const cols = Array.from({ length: 510 }, (_, i) => ({ key: `r${i}`, name: `Raster ${i}`, kind: 'raster' }));
  const ui = mount(container, { pageSize: 900, previewPage: async (args) => { received = args; return { rows: [{ id: 'cell-1', values: { r0: { state: 'not_computed' } } }], totalRows: 201, columns: cols }; } });
  await ui.ready;
  assert.equal(received.draft, null, 'the first preview must let the server seed columns from selected rasters');
  assert.equal(received.visible_columns, undefined, 'initial preview uses the server default visible columns');
  assert.equal(received.pageSize, MAX_PAGE_SIZE);
  assert.equal(all(container).filter((n) => n.tagName === 'tr').length, 3, 'mesh page plus the separate manual table header');
  assert.equal(all(container).filter((n) => n.tagName === 'th').length, 1 + MAX_VISIBLE_COLUMNS + 1 + 1 + 2);
  assert.equal(byText(container, 'Chưa tính') !== null, true);
  assert.equal(byText(container, '=1+1') !== null, false);
  assert.equal(ui.getPlan().columns.length, MAX_FEATURE_COLUMNS);
  ui.destroy();
});

test('initial preview adopts the server seeded draft and keeps manual rows outside mesh-cell actions', async () => {
  const { container } = fixture(); let initialRequest;
  const seeded = { mesh_job_id: 'mesh-seeded', geometry_digest: 'abc',
    columns: [{ id: 'r0', type: 'raster', source_name: 'rain', name: 'Rain' }, { id: 'note', type: 'manual', name: 'Note', value_type: 'text' }],
    rows: [{ cell_id: 'cell-1', values: { note: 'cell note' } }], excluded_cell_ids: [],
    manual_rows: [{ row_id: 'manual-row-existing', values: { note: 'field note' } }] };
  const ui = mount(container, { meshJobId: 'mesh-seeded', geometryDigest: 'abc', previewPage: async (args) => {
    initialRequest = args;
    return { draft: seeded, columns: seeded.columns, rows: [
      { row_kind: 'cell', cell_id: 'cell-1', values: { r0: { state: 'not_computed' }, note: 'cell note' } },
      { row_kind: 'manual', row_id: 'manual-row-existing', values: { note: 'field note' } },
    ], total_rows: 2, accepted_cells: 1, totalCells: 1, page: 1 };
  } });
  await ui.ready;
  assert.equal(initialRequest.draft, null);
  assert.deepEqual(ui.getPlan(), seeded);
  const tables = all(container).filter((node) => node.tagName === 'table');
  const bodyRows = tables.map((table) => table.children[1].children.filter((node) => node.tagName === 'tr').length);
  assert.deepEqual(bodyRows, [1, 1], 'one mesh cell and one separately edited manual row');
  const cellRowActions = all(tables[0]).filter((node) => node.tagName === 'button' && node.textContent === 'Loại dòng');
  assert.equal(cellRowActions.length, 1);
  assert.equal(ui.getPlan().excluded_cell_ids.includes('manual-row-existing'), false);
  ui.destroy();
});

test('manual cells, raster edits, row exclusion invalidate review and produce a plan', async () => {
  const { container } = fixture(); let changed, confirmed;
  const ui = mount(container, { previewPage: async () => ({ rows: [{ id: 'c1', values: { elev: { state: 'not_computed' } } }], totalRows: 1, columns: [{ key: 'elev', name: 'Elevation', kind: 'raster' }, { key: 'id', name: 'Feature ID', kind: 'computed' }] }), onChange: (plan) => { changed = plan; }, onConfirm: (plan) => { confirmed = plan; } });
  await ui.ready;
  assert.equal(inputFor(container, 'Feature ID'), null, 'computed field names cannot be edited');
  const add = byText(container, 'Thêm cột');
  const name = find(container, (n) => n.tagName === 'input' && n.placeholder === 'Tên cột thủ công'); name.value = 'Note'; add.handlers.click();
  await flush();
  const cell = inputFor(container, 'Note, dòng c1'); assert.ok(cell);
  cell.value = 'safe <text>'; cell.handlers.change(); await flush();
  const exclude = byText(container, 'Loại dòng'); exclude.handlers.click();
  await flush();
  assert.deepEqual(changed.excluded_cell_ids, ['c1']);
  assert.equal(changed.rows[0].values['manual-1'], 'safe <text>');
  const review = find(container, (n) => n.className === 'mc-extraction-draft__review-check'); review.checked = true; review.handlers.change();
  byText(container, 'Xác nhận bảng & extract').handlers.click();
  await flush();
  assert.equal(confirmed.rows[0].values['manual-1'], 'safe <text>');
  ui.destroy();
});

test('manual rows are serialized separately from mesh cell rows and canonical sync gates confirmation', async () => {
  const { container } = fixture(); const previewDrafts = []; let changeCalls = 0, confirmed;
  const ui = mount(container, {
    meshJobId: 'mesh-1', geometryDigest: 'digest-1',
    previewPage: async (args) => { previewDrafts.push(args.draft); return { rows: [{ id: 'cell-1' }], totalRows: 1, columns: [] }; },
    onChange: async (draft) => { changeCalls++; await Promise.resolve(); return { draft: { ...draft, server_canonical: true } }; },
    onConfirm: async (draft) => { confirmed = draft; },
  });
  await ui.ready;
  const manualName = find(container, (n) => n.tagName === 'input' && n.placeholder === 'Tên cột thủ công');
  manualName.value = 'Count'; find(container, (n) => n.tagName === 'select').value = 'number'; byText(container, 'Thêm cột').handlers.click();
  await flush();
  byText(container, 'Thêm dòng thủ công').handlers.click();
  await flush();
  const numeric = find(container, (n) => n.tagName === 'input' && String(n.attributes['aria-label'] || '').startsWith('Count, dòng thủ công '));
  assert.ok(numeric, `manual row field should be available; row input found=${Boolean(numeric)}`);
  numeric.value = '3.5'; numeric.handlers.change();
  await flush();
  assert.equal(changeCalls, 3);
  assert.deepEqual(ui.getPlan().rows, []);
  const manualColumnId = ui.getPlan().columns.find((column) => column.name === 'Count').id;
  assert.equal(ui.getPlan().manual_rows[0].values[manualColumnId], 3.5);
  const request = await ui.refresh();
  assert.equal(request, true);
  assert.match(previewDrafts.at(-1).manual_rows[0].row_id, /^manual-row-/);
  await Promise.resolve();
  const review = find(container, (n) => n.className === 'mc-extraction-draft__review-check'); review.checked = true; review.handlers.change();
  const confirm = byText(container, 'Xác nhận bảng & extract');
  confirm.handlers.click(); await flush();
  assert.equal(confirmed.server_canonical, true);
  assert.equal(changeCalls, 4, 'confirmation performs a final canonical roundtrip');
  ui.destroy();
});

test('confirm locks edits until async start settles and unlocks when start fails', async () => {
  const { container } = fixture(); let releaseStart;
  const ui = mount(container, {
    previewPage: async () => ({ rows: [{ cell_id: 'c1' }], totalRows: 1, columns: [] }),
    onConfirm: () => new Promise((resolve, reject) => { releaseStart = { resolve, reject }; }),
  });
  await ui.ready;
  const review = find(container, (n) => n.className === 'mc-extraction-draft__review-check'); review.checked = true; review.handlers.change();
  byText(container, 'Xác nhận bảng & extract').handlers.click();
  await flush();
  assert.equal(byText(container, 'Loại dòng').disabled, true, 'visible controls lock during asynchronous confirmation');
  assert.equal(find(container, (n) => n.placeholder === 'Tên cột thủ công').disabled, true);
  releaseStart.reject(new Error('start failed'));
  for (let i = 0; i < 6; i++) await Promise.resolve();
  assert.equal(byText(container, 'Loại dòng').disabled, false, 'failed start restores editing controls');
  assert.equal(ui.getPlan().excluded_cell_ids.length, 0);
  ui.destroy();
});

test('removed manual columns are pruned and invalid manual-row numbers are rejected', async () => {
  const { container } = fixture();
  const seeded = { mesh_job_id: 'm', geometry_digest: 'g', columns: [{ id: 'n', type: 'manual', name: 'Count', value_type: 'number' }], rows: [{ cell_id: 'c1', values: { n: 7 } }], excluded_cell_ids: [], manual_rows: [{ row_id: 'mr1', values: { n: 8, stale: 99 } }] };
  const ui = mount(container, { previewPage: async () => ({ draft: seeded, rows: [{ cell_id: 'c1', values: { n: 7 } }], totalRows: 1 }) });
  await ui.ready;
  const manualInput = inputFor(container, 'Count, dòng thủ công mr1');
  manualInput.value = 'not-a-number'; manualInput.handlers.change();
  assert.match(find(container, (n) => n.className === 'mc-extraction-draft__error').textContent, /không hợp lệ/);
  assert.equal(ui.getPlan().manual_rows[0].values.n, 8, 'invalid numeric edit does not enter the plan');
  byText(container, 'Xóa cột').handlers.click();
  const plan = ui.getPlan();
  assert.deepEqual(plan.columns, []);
  assert.deepEqual(plan.rows, []);
  assert.deepEqual(plan.manual_rows, [{ row_id: 'mr1', values: {} }]);
  ui.destroy();
});

test('manual-entry grid bounds visible columns and rows while preserving off-page edits', async () => {
  const { container } = fixture();
  const columns = Array.from({ length: 25 }, (_, index) => ({ id: `m${index}`, type: 'manual', name: `Field ${index}`, value_type: 'text' }));
  const manualRows = Array.from({ length: 201 }, (_, index) => ({ row_id: `row-${index}`, values: index === 0 ? { m0: 'old' } : {} }));
  const ui = mount(container, { pageSize: 200, previewPage: async () => ({ draft: { mesh_job_id: 'm', geometry_digest: 'g', columns, rows: [], excluded_cell_ids: [], manual_rows: manualRows }, rows: [], totalRows: 0 }) });
  await ui.ready;
  const table = all(container).find((node) => node.tagName === 'table' && node.className.includes('manual-table'));
  assert.equal(table.children[0].children[0].children.length, 22, 'manual grid has row ID, at most 20 visible manual fields, and actions');
  assert.equal(table.children[1].children.filter((node) => node.tagName === 'tr').length, 200, 'manual grid renders only one bounded page');
  const edited = inputFor(container, 'Field 0, dòng thủ công row-0');
  edited.value = 'kept'; edited.handlers.change(); await flush();
  const manualPager = find(container, (node) => node.tagName === 'nav' && node.attributes['aria-label'] === 'Trang các dòng nhập tay');
  byText(manualPager, 'Sau').handlers.click();
  assert.equal(table.children[1].children.filter((node) => node.tagName === 'tr').length, 1, 'last page is bounded');
  byText(manualPager, 'Trước').handlers.click();
  assert.equal(inputFor(container, 'Field 0, dòng thủ công row-0').value, 'kept', 'off-page values survive pagination');
  assert.equal(ui.getPlan().manual_rows[0].values.m0, 'kept');
  ui.destroy();
});

test('stop remains pending until an explicit terminal status arrives', async () => {
  const { container } = fixture(); let stopCalls = 0;
  const ui = mount(container, { previewPage: async () => ({ rows: [{ cell_id: 'c1' }], totalRows: 1, columns: [] }), onStop: async () => (++stopCalls === 1 ? { accepted: false, message: 'not accepted' } : { accepted: true }) });
  await ui.ready; ui.setRunning(true);
  const rowAction = byText(container, 'Loại dòng');
  assert.equal(rowAction.disabled, true, 'row edits lock while extraction runs');
  rowAction.handlers.click();
  assert.deepEqual(ui.getPlan().excluded_cell_ids, [], 'event guards also reject edits while locked');
  const stop = byText(container, 'Dừng trích xuất');
  await stop.handlers.click();
  assert.equal(stopCalls, 1);
  assert.equal(stop.textContent, 'Thử gửi lại yêu cầu dừng');
  assert.equal(stop.disabled, false);
  await stop.handlers.click(); assert.equal(stopCalls, 2);
  ui.setRunning('stopping');
  assert.equal(stop.textContent, 'Đang yêu cầu dừng…');
  assert.equal(stop.disabled, true);
  assert.equal(stop.hidden, false);
  ui.setRunning('cancelled');
  assert.equal(stop.hidden, true);
  assert.match(find(container, (n) => n.className === 'mc-extraction-draft__notice').textContent, /cancelled/);
  ui.destroy();
});

test('late stop rejection cannot override authoritative stopping or terminal status', async () => {
  async function scenario(authoritativeStatus) {
    const { container } = fixture(); let rejectStop;
    const ui = mount(container, { previewPage: async () => ({ rows: [], totalRows: 0, columns: [] }), onStop: () => new Promise((resolve, reject) => { rejectStop = reject; }) });
    await ui.ready; ui.setRunning('running');
    const stop = byText(container, 'Dừng trích xuất');
    const pending = stop.handlers.click();
    ui.setRunning(authoritativeStatus);
    rejectStop(new Error('late network rejection'));
    await pending;
    assert.equal(find(container, (node) => node.className === 'mc-extraction-draft__error').textContent, '', `no stale stop error after ${authoritativeStatus}`);
    if (authoritativeStatus === 'stopping') {
      assert.equal(stop.disabled, true);
      assert.equal(stop.textContent, 'Đang yêu cầu dừng…');
      assert.equal(stop.hidden, false);
    } else {
      assert.equal(stop.hidden, true);
    }
    ui.destroy();
  }
  await scenario('stopping');
  await scenario('cancelled');
});
