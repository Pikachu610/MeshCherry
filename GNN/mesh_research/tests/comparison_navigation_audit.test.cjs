// Independent, narrowly scoped audit of the production comparison renderer
// and navigation wiring. Run with: node --test tests/comparison_navigation_audit.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const webui = path.join(__dirname, '../src/gis_mesh_workbench/webui');
const appSource = fs.readFileSync(path.join(webui, 'app.js'), 'utf8');
const comparisonSource = fs.readFileSync(path.join(webui, 'comparison_view.js'), 'utf8');
const html = fs.readFileSync(path.join(webui, 'index.html'), 'utf8');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag;
    this.children = [];
    this.attributes = {};
    this.hidden = false;
    this.textContent = '';
    this.className = '';
    this.classList = {
      add: name => { this.className = `${this.className} ${name}`.trim(); },
      remove: name => { this.className = this.className.split(/\s+/).filter(x => x !== name).join(' '); },
    };
  }
  append(...items) { this.children.push(...items); }
  insertBefore(item, before) {
    const index = this.children.indexOf(before);
    this.children.splice(index < 0 ? this.children.length : index, 0, item);
  }
  replaceChildren(...items) { this.children = items; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getBoundingClientRect() { return { width: 0, height: 0 }; }
  getContext() { return null; }
}

function comparisonRenderer() {
  const context = vm.createContext({
    document: { createElement: tag => new Element(tag) },
    window: {},
    URL: { createObjectURL() { return 'blob:source'; }, revokeObjectURL() {} },
    Blob,
  });
  vm.runInContext(comparisonSource, context);
  return context.window.ComparisonView;
}

function sectionCards(root) {
  const grid = root.children.find(child => child.className === 'comparison-visual-grid');
  return grid ? grid.children : [];
}

test('measured report renders four truthful comparison cards and no size-field claim', () => {
  const view = comparisonRenderer();
  const root = new Element();
  view.render(root, {
    baseline: { mode: 'native_raster' },
    structured: { node_count: 4 },
    unstructured: { node_count: 2 },
    graph_node_delta: -2,
    node_reduction_percent: 50,
    visualization: {
      bounds: [0, 0, 2, 1],
      roi: { type: 'Polygon', coordinates: [[[0, 0], [2, 0], [2, 1], [0, 1], [0, 0]]] },
      source: { available: false, reason: 'Preview unavailable' },
      structured: { available: true, total_cell_count: 4, geometries: [] },
      unstructured: { available: true, total_cell_count: 2, geometries: [] },
    },
  });

  const cards = sectionCards(root);
  assert.equal(cards.length, 4);
  assert.deepEqual(cards.map(card => card.children[0].textContent), [
    'Raster nguồn', 'Lưới có cấu trúc', 'Lưới tam giác', 'Căn chỉnh chung · cùng mức thu phóng',
  ]);
  assert.match(cards[0].className, /is-unavailable/);
  assert.doesNotMatch(cards[1].className, /is-unavailable/);
  assert.doesNotMatch(cards[2].className, /is-unavailable/);
  assert.doesNotMatch(cards[3].className, /is-unavailable/);
  const allText = JSON.stringify(root, (key, value) => key === 'classList' ? undefined : value);
  assert.doesNotMatch(allText, /target.?size|size.?field|variation field/i);
});

test('resolution-only estimate does not fabricate any spatial panels', () => {
  const view = comparisonRenderer();
  const root = new Element();
  view.render(root, { baseline: { mode: 'resolution_estimate' }, structured: { cell_count: 10 }, unstructured: { cell_count: 8 } });
  assert.equal(sectionCards(root).length, 0);
  assert.ok(root.children.some(child => /Ước tính theo diện tích/.test(child.textContent)));
});

test('Compare workspace remains reachable from direct navigation and research tools before extraction', () => {
  assert.match(html, /<button[^>]*data-workspace="compare"[^>]*>So sánh<\/button>/);
  assert.match(appSource, /for\(const button of document\.querySelectorAll\('button\[data-workspace\]'\)\)button\.addEventListener\('click',\(\)=>setWorkspace\(button\.dataset\.workspace\)\)/);
  assert.match(html, /id="tool-compare"/);
  assert.match(appSource, /\$\('tool-compare'\)\.onclick=\(\)=>\{\$\('tools-dialog'\)\.close\(\);setWorkspace\('compare'\);\}/);

  const start = appSource.indexOf('function setWorkspace(value){');
  const end = appSource.indexOf('\nfunction setupWorkspaceNavigation()', start);
  assert.ok(start >= 0 && end > start, 'production setWorkspace function is present');
  const panels = ['1', '2', '3', '4'].map(panel => {
    const element = new Element(); element.dataset = { panel }; return element;
  });
  const comparisonPanel = new Element();
  const body = { dataset: {} };
  const elements = new Map([['comparison-workspace', comparisonPanel]]);
  const context = vm.createContext({
    document: {
      body,
      querySelectorAll: () => panels,
      querySelector: selector => selector === '[data-panel="4"]'
        ? panels.find(panel => panel.dataset.panel === '4') : null,
    },
    $: id => elements.get(id) || new Element(),
    busy: false,
    comparisonOwnsBusy: false,
    meshSnapshot: null,
    jobId: null,
    displayCellCount: () => 0,
    markWorkspace: value => { body.dataset.workspace = value; },
    prepareComparison() { context.prepared = true; },
    stage(value) { context.stageValue = value; },
    showResultTab(value) { context.resultTab = value; },
    setExtractMode() {},
  });
  vm.runInContext(appSource.slice(start, end), context);
  vm.runInContext("setWorkspace('compare')", context);
  assert.equal(body.dataset.workspace, 'compare');
  assert.equal(context.prepared, true);
  assert.equal(comparisonPanel.hidden, false);
  assert.ok(panels.every(panel => panel.hidden));
  vm.runInContext("setWorkspace('extract')", context);
  assert.equal(body.dataset.workspace, 'extract');
  assert.equal(context.resultTab, 'extract');
  assert.equal(panels.find(panel => panel.dataset.panel === '4').hidden, false);
});
