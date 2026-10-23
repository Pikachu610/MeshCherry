const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const webui = path.join(__dirname, '../src/gis_mesh_workbench/webui');
const html = fs.readFileSync(path.join(webui, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(webui, 'workspace_layout.css'), 'utf8');
const source = fs.readFileSync(path.join(webui, 'window_chrome.js'), 'utf8');

class Element {
  constructor(dataset = {}) {
    this.dataset = dataset;
    this.attributes = {};
    this.disabled = false;
    this.listeners = new Map();
  }
  addEventListener(type, handler) {
    const handlers = this.listeners.get(type) || [];
    handlers.push(handler);
    this.listeners.set(type, handlers);
  }
  removeEventListener(type, handler) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item !== handler));
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  fire(type, event = {}) { for (const handler of this.listeners.get(type) || []) handler(event); }
}

function harness({ desktop = true } = {}) {
  const buttonByAction = new Map(['minimize', 'toggle_maximize', 'close'].map(action => [action, new Element({ windowAction: action })]));
  const resizeByEdge = new Map(['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw'].map(edge => [edge, new Element({ windowResize: edge })]));
  const dragRegions = [new Element(), new Element()];
  const document = {
    body: { dataset: { desktopChrome: String(desktop) } },
    querySelectorAll(selector) {
      if (selector === '[data-window-action]') return [...buttonByAction.values()];
      if (selector === '[data-window-resize]') return [...resizeByEdge.values()];
      if (selector === '.window-chrome__drag-region') return dragRegions;
      return [];
    },
  };
  const window = { document };
  vm.runInNewContext(source, { window, globalThis: window });
  return { chrome: window.MeshCherryChrome, buttonByAction, resizeByEdge, dragRegions };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test('desktop template keeps the original five menus in one menubar and adds no brand title', () => {
  assert.match(html, /data-desktop-chrome="\{\{DESKTOP_CHROME\}\}"/);
  assert.match(html, /<script src="\/window_chrome\.js" defer><\/script>/);
  const menubar = html.match(/<nav class="app-menubar"[\s\S]*?<\/nav>/)?.[0];
  assert.ok(menubar);
  assert.equal((menubar.match(/<details>/g) || []).length, 5);
  assert.match(menubar, /id="project-open"/);
  assert.match(menubar, /id="project-save"/);
  assert.match(menubar, /id="tutorial-help"/);
  assert.match(menubar, /class="window-chrome__icon pywebview-drag-region"/);
  assert.match(menubar, /data-window-action="minimize"[\s\S]*data-window-action="toggle_maximize"[\s\S]*data-window-action="close"/);
  assert.doesNotMatch(menubar, /Mesh Cherry|GIS Workbench|titlebar/i);
  assert.equal((html.match(/data-window-resize=/g) || []).length, 8);
});

test('browser fallback leaves native controls inert and does not require a pywebview JavaScript API', async () => {
  const { chrome } = harness({ desktop: false });
  let called = false;
  chrome.init({ request: async () => { called = true; } });
  assert.equal(called, false);
  assert.doesNotMatch(source, /pywebview\.api|js_api/);
  assert.match(css, /\.window-chrome__icon,[\s\S]*?display: none;/);
  assert.match(css, /body\[data-desktop-chrome="true"\][\s\S]*?\.app-menubar\s*\{/);
});

test('window actions use the authenticated request callback and update maximize state', async () => {
  const { chrome, buttonByAction } = harness();
  const pending = deferred();
  const calls = [];
  const errors = [];
  chrome.init({ request(path, body) { calls.push([path, body]); return pending.promise; }, onError: error => errors.push(error) });

  buttonByAction.get('toggle_maximize').fire('click');
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/api/window/control');
  assert.equal(calls[0][1].action, 'toggle_maximize');
  assert.equal(buttonByAction.get('toggle_maximize').disabled, true);
  assert.equal(buttonByAction.get('close').disabled, false);
  pending.resolve({ maximized: true });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(buttonByAction.get('toggle_maximize').disabled, false);
  assert.equal(buttonByAction.get('toggle_maximize').attributes['aria-pressed'], 'true');
  assert.equal(buttonByAction.get('toggle_maximize').attributes['aria-label'], 'Khôi phục cửa sổ');
  assert.deepEqual(errors, []);
});

test('a failed close reports the error and restores the button for retry', async () => {
  const { chrome, buttonByAction } = harness();
  const errors = [];
  chrome.init({ request: async (_path, { action }) => { throw new Error(`failed: ${action}`); }, onError: error => errors.push(error.message) });
  buttonByAction.get('close').fire('click');
  assert.equal(buttonByAction.get('close').disabled, true);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(buttonByAction.get('close').disabled, false);
  assert.deepEqual(errors, ['failed: close']);
});

test('newer maximize responses win and all eight resize edges use the native allowlist', async () => {
  const { chrome, buttonByAction, resizeByEdge, dragRegions } = harness();
  const pending = [];
  const calls = [];
  chrome.init({ request(_path, body) { calls.push(body.action); const next = deferred(); pending.push(next); return next.promise; } });

  buttonByAction.get('toggle_maximize').fire('click');
  dragRegions[0].fire('dblclick', { preventDefault() {} });
  pending[1].resolve({ maximized: false });
  pending[0].resolve({ maximized: true });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(buttonByAction.get('toggle_maximize').attributes['aria-pressed'], 'false');

  for (const edge of ['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw']) {
    resizeByEdge.get(edge).fire('pointerdown', { button: 0, preventDefault() {} });
  }
  assert.deepEqual(calls, ['toggle_maximize', 'toggle_maximize', 'resize-n', 'resize-ne', 'resize-e', 'resize-se', 'resize-s', 'resize-sw', 'resize-w', 'resize-nw']);
});
