const { test } = require('node:test');
const assert = require('node:assert/strict');
const { mount, safeFilename } = require('../src/gis_mesh_workbench/webui/downloads.js');

class Element {
  constructor(doc, tagName = '') { this.ownerDocument = doc; this.tagName = tagName; this.children = []; this.attributes = {}; this.handlers = {}; }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.children.push(item); return item; }
  remove() { this.removed = true; }
  replaceChildren(...items) { this.children = items; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(key, fn) { this.handlers[key] = fn; }
  click() { this.clicked = true; }
}
function fixture() {
  const doc = { body: new Element(null, 'body'), createElement(tagName) { return new Element(doc, tagName); } };
  const container = new Element(doc);
  return { container, doc };
}

test('filename sanitation removes path segments and forbidden characters', () => {
  assert.equal(safeFilename('../folder/a:b.csv'), '_folder_a_b.csv');
});

test('native save adapter runs before any browser fetch and reports the exact path', async () => {
  const { container } = fixture(); let fetched = false, input;
  const downloads = mount(container, { nativeSave: async (request) => { input = request; return { state: 'saved', path: 'D:\\exports\\mesh.csv' }; }, fetch: async () => { fetched = true; } });
  const result = await downloads.start({ url: '/api/export', filename: 'mesh.csv' });
  assert.equal(fetched, false); assert.equal(input.url, '/api/export');
  assert.equal(result.path, 'D:\\exports\\mesh.csv');
  assert.equal(downloads.getState().phase, 'saved');
});

test('fallback transfer keeps progress indeterminate without Content-Length and never claims a path', async () => {
  const { container, doc } = fixture();
  const response = {
    ok: true,
    headers: { get: () => null },
    body: { getReader() {
      let index = 0;
      return { async read() { return index++ ? { done: true } : { done: false, value: new Uint8Array([1, 2]) }; }, async cancel() {} };
    } }
  };
  let requestOptions;
  const downloads = mount(container, { url: '/api/export', filename: 'default.csv', fetchOptions: { method: 'POST', headers: { 'X-Export': 'yes' }, body: 'selection' }, URL: { createObjectURL: () => 'blob:export', revokeObjectURL() {} }, fetch: async (_url, options) => { requestOptions = options; return response; } });
  const result = await downloads.start({ filename: 'custom-export.csv' });
  const progress = container.children[0].children.find((node) => node.tagName === 'progress');
  assert.equal(progress.attributes.value, undefined);
  assert.equal(result.phase, 'exported'); assert.equal(result.path, null);
  assert.equal(requestOptions.method, 'POST'); assert.equal(requestOptions.headers['X-Export'], 'yes'); assert.equal(requestOptions.body, 'selection');
  const anchor = doc.body.children.find((node) => node.download === 'custom-export.csv');
  assert.ok(anchor.clicked);
});

test('browser fallback refuses oversized files before reading them', async () => {
  const { container } = fixture(); let cancelled = false, readerCreated = false;
  const response = { ok: true, headers: { get: (name) => name === 'content-length' ? String(64 * 1024 * 1024 + 1) : null }, body: { async cancel() { cancelled = true; }, getReader() { readerCreated = true; return {}; } } };
  const downloads = mount(container, { url: '/large', fetch: async () => response });
  const result = await downloads.start();
  assert.equal(result.phase, 'error'); assert.match(result.error.message, /64 MiB/);
  assert.equal(cancelled, true); assert.equal(readerCreated, false);
});

test('native picker cancellation is not reported as a saved export and retry remains available', async () => {
  const { container } = fixture(); let attempt = 0;
  const downloads = mount(container, { nativeSave: async () => ++attempt === 1 ? { state: 'cancelled' } : { state: 'saved', path: 'D:\\Exports\\ok.csv' } });
  assert.equal((await downloads.start()).phase, 'cancelled');
  const retried = await downloads.start();
  assert.equal(retried.phase, 'saved'); assert.equal(retried.path, 'D:\\Exports\\ok.csv');
});

test('native adapter cannot claim a successful save without an explicit saved path', async () => {
  const { container } = fixture();
  const downloads = mount(container, { nativeSave: async () => ({ message: 'done' }) });
  const result = await downloads.start();
  assert.equal(result.phase, 'error'); assert.match(result.error, /không xác nhận/i);
});
