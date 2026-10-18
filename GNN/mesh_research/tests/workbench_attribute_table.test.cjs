const { test } = require('node:test');
const assert = require('node:assert/strict');
const { mount, MAX_PAGE_SIZE } = require('../src/gis_mesh_workbench/webui/attribute_table.js');

class Element {
  constructor(doc, tagName = '') { this.ownerDocument = doc; this.tagName = tagName; this.children = []; this.attributes = {}; this.handlers = {}; this.value = ''; this.dataset = {}; }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.children.push(item); return item; }
  replaceChildren(...items) { this.children = items; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(key, fn) { this.handlers[key] = fn; }
  text(value) { this.textContent = value; }
}
function fixture() {
  const doc = { createElement(tagName) { return new Element(doc, tagName); }, createTextNode(textContent) { return { textContent }; } };
  const container = new Element(doc);
  return { doc, container };
}
function find(el, predicate) { if (predicate(el)) return el; for (const child of el.children || []) { const match = find(child, predicate); if (match) return match; } return null; }
function descendants(el) { return (el.children || []).flatMap((child) => [child, ...descendants(child)]); }

test('table caps page size and keeps null distinct from numeric zero', async () => {
  const { container } = fixture(); let call;
  const table = mount(container, { pageSize: 100000, featureColumns: ['elevation'], fetchPage: async (params) => { call = params; return { rows: [{ cell_id: 'f-1', values: { elevation: 0 } }, { cell_id: 'f-2', values: { elevation: null } }], total_cells: 100000, page: 1 }; } });
  await Promise.resolve(); await Promise.resolve();
  assert.equal(call.pageSize, MAX_PAGE_SIZE);
  const cells = descendants(container).filter((node) => node.tagName === 'td');
  assert.ok(find(container, (node) => node.textContent === '0'));
  assert.ok(find(container, (node) => node.textContent === '—'));
  assert.ok(find(container, (node) => node.textContent === 'f-1'));
  assert.equal(call.columns[0], 'elevation');
  table.destroy();
});

test('ID search resets to page one and forwards only the matching search value', async () => {
  const { container } = fixture(), calls = [];
  const table = mount(container, { pageSize: 2, featureColumns: [], fetchPage: async (params) => { calls.push(params); return { rows: [], totalRows: 0, page: params.page }; } });
  await Promise.resolve(); await Promise.resolve();
  const search = find(container, (node) => node.type === 'search'); search.value = '  0012  '; search.handlers.input();
  await new Promise((resolve) => setTimeout(resolve, 280)); await Promise.resolve();
  assert.equal(calls.at(-1).search, '0012'); assert.equal(calls.at(-1).page, 1);
  table.destroy();
});

test('stale page responses cannot replace the latest request', async () => {
  const { container } = fixture(); let resolveFirst, count = 0;
  const table = mount(container, { featureColumns: [], fetchPage: () => (++count === 1 ? new Promise((resolve) => { resolveFirst = resolve; }) : Promise.resolve({ rows: [{ id: 'new' }], totalRows: 1, page: 1 })) });
  await table.refresh(); resolveFirst({ rows: [{ id: 'old' }], totalRows: 1, page: 1 }); await Promise.resolve();
  assert.ok(find(container, (node) => node.textContent === 'new'));
  assert.equal(find(container, (node) => node.textContent === 'old'), null);
  table.destroy();
});

test('typing a new search invalidates a response while the debounce is pending', async () => {
  const { container } = fixture(); let resolveOld;
  const table = mount(container, { featureColumns: [], fetchPage: () => new Promise((resolve) => { resolveOld = resolve; }) });
  const search = find(container, (node) => node.type === 'search'); search.value = 'new-id'; search.handlers.input();
  resolveOld({ rows: [{ id: 'stale-id' }], totalRows: 1, page: 1 }); await Promise.resolve(); await Promise.resolve();
  assert.equal(find(container, (node) => node.textContent === 'stale-id'), null);
  table.destroy();
});
