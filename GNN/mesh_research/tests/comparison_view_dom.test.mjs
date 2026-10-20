import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentElement = null;
    this.hidden = false;
    this.className = '';
    this.attributes = {};
    this.context = { strokes: 0, beginPath() {}, moveTo() {}, lineTo() {}, closePath() {},
      fill() {}, stroke() { this.strokes++; }, clearRect() {} };
    this.classList = { remove: value => {
      this.className = this.className.split(/\s+/).filter(name => name !== value).join(' ');
    } };
  }

  setAttribute(key, value) { this.attributes[key] = value; }
  append(...children) {
    for (const child of children) {
      child.parentElement = this;
      this.children.push(child);
    }
  }
  insertBefore(child, before) {
    child.parentElement = this;
    const index = this.children.indexOf(before);
    this.children.splice(index < 0 ? this.children.length : index, 0, child);
  }
  replaceChildren(...children) {
    for (const child of this.children) child.parentElement = null;
    this.children = [];
    this.append(...children);
  }
  get isConnected() {
    return this.connected || Boolean(this.parentElement?.isConnected);
  }
  get isHidden() {
    return this.hidden || Boolean(this.parentElement?.isHidden);
  }
  getBoundingClientRect() {
    if (!this.isConnected || this.isHidden) return { width: 0, height: 0 };
    return { width: 640, height: 300 };
  }
  getContext() { return this.context; }
}

class FakeResizeObserver {
  static instances = [];
  constructor(callback) { this.callback = callback; this.disconnected = false; FakeResizeObserver.instances.push(this); }
  observe(target) { this.target = target; }
  disconnect() { this.disconnected = true; }
  trigger() { this.callback([{ target: this.target }]); }
}

function descendants(element, tagName, found = []) {
  if (element.tagName === tagName) found.push(element);
  for (const child of element.children) descendants(child, tagName, found);
  return found;
}

test('comparison canvases wait for a visible connected layout and redraw when shown', async () => {
  const frames = [];
  const document = { createElement: tagName => new FakeElement(tagName) };
  const window = { devicePixelRatio: 1 };
  const context = {
    document, window, ResizeObserver: FakeResizeObserver,
    requestAnimationFrame: callback => frames.push(callback),
  };
  vm.runInNewContext(await readFile(new URL('../src/gis_mesh_workbench/webui/comparison_view.js', import.meta.url), 'utf8'), context);

  const report = {
    structured: { node_count: 1250 }, unstructured: { node_count: 2 },
    graph_node_delta: -1248, node_reduction_percent: 99.84,
    geometry_counts: { unstructured_unique_vertex_count: 4 },
    visualization: {
      bounds: [0, 0, 2, 1],
      roi: { type: 'Polygon', coordinates: [[[0, 0], [2, 0], [2, 1], [0, 1], [0, 0]]] },
      source: { available: false, reason: 'No thumbnail' },
      structured: { available: true, feature_count: 1200, total_cell_count: 1250, sampled: true,
        geometries: [{ type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]] }] },
      unstructured: { available: true, feature_count: 2, total_cell_count: 2, sampled: false,
        geometries: [{ type: 'Polygon', coordinates: [[[1, 0], [2, 0], [2, 1], [1, 0]]] }] },
    },
  };
  const container = new FakeElement('div');
  container.hidden = true;
  context.window.ComparisonView.render(container, report);
  const metricValues = descendants(container, 'STRONG').map(element => element.textContent);
  assert.equal(metricValues[0], '1.250');
  assert.equal(metricValues[1], '2');
  assert.equal(metricValues[2], '-1.248');
  assert.match(metricValues[3], /99,84%/);
  assert.match(descendants(container, 'P').find(element => element.className === 'comparison-vertex-count').textContent, /4/);
  const canvases = descendants(container, 'CANVAS').filter(canvas => !canvas.hidden);
  assert.equal(canvases.length, 3);
  assert.ok(canvases.every(canvas => canvas.width === undefined || canvas.width === 0));

  const root = new FakeElement('main');
  root.connected = true;
  root.append(container);
  for (const frame of frames.splice(0)) frame();
  assert.ok(canvases.every(canvas => canvas.width === undefined || canvas.width === 0));

  container.hidden = false;
  FakeResizeObserver.instances.at(-1).trigger();
  assert.ok(canvases.every(canvas => canvas.width === 640));
  assert.ok(canvases.every(canvas => canvas.context.strokes > 0));

  const observer = FakeResizeObserver.instances.at(-1);
  context.window.ComparisonView.clear(container);
  assert.equal(observer.disconnected, true);
  assert.equal(container.children.length, 0);

  context.window.ComparisonView.render(container, {
    baseline: { mode: 'resolution_estimate' }, square_grid: { node_count: 2 }, mesh: { node_count: 8 },
    graph_node_delta: 6, node_reduction_percent: -300,
  });
  const estimates = descendants(container, 'STRONG').map(element => element.textContent);
  assert.equal(estimates[0], '2'); assert.equal(estimates[1], '8');
  assert.equal(estimates[2], '+6'); assert.equal(estimates[3], '300%');
  assert.ok(descendants(container, 'SPAN').some(element => /ước tính/.test(element.textContent)));
  assert.ok(descendants(container, 'SPAN').some(element => /Tăng phần tử/.test(element.textContent)));
  assert.equal(descendants(container, 'CANVAS').length, 0);
});

test('source PNG becomes a revocable blob URL and hostile data URLs are rejected', async () => {
  const revoked = [];
  let sequence = 0;
  const URLImpl = {
    createObjectURL(blob) {
      assert.equal(blob.type, 'image/png');
      return `blob:comparison-${++sequence}`;
    },
    revokeObjectURL(url) { revoked.push(url); },
  };
  const document = { createElement: tagName => new FakeElement(tagName) };
  const window = { devicePixelRatio: 1 };
  const context = {
    document, window, URL: URLImpl, Blob, ResizeObserver: FakeResizeObserver,
    requestAnimationFrame: () => 1,
    cancelAnimationFrame() {},
    atob: value => Buffer.from(value, 'base64').toString('binary'),
  };
  vm.runInNewContext(await readFile(new URL('../src/gis_mesh_workbench/webui/comparison_view.js', import.meta.url), 'utf8'), context);
  const container = new FakeElement('div');
  const root = new FakeElement('main');
  root.connected = true;
  root.append(container);
  const report = {
    structured: { node_count: 1 }, unstructured: { node_count: 1 },
    graph_node_delta: 0, node_reduction_percent: 0,
    visualization: {
      source: { image: 'data:image/png;base64,iVBORw0KGgo=', crs: 'EPSG:4326', bounds: [0, 0, 1, 1] },
      structured: { available: false }, unstructured: { available: false },
    },
  };

  context.window.ComparisonView.render(container, report);
  assert.equal(descendants(container, 'IMG')[0].src, 'blob:comparison-1');
  context.window.ComparisonView.render(container, report);
  assert.deepEqual(revoked, ['blob:comparison-1']);
  assert.equal(descendants(container, 'IMG')[0].src, 'blob:comparison-2');
  context.window.ComparisonView.clear(container);
  assert.deepEqual(revoked, ['blob:comparison-1', 'blob:comparison-2']);

  report.visualization.source.image = 'data:image/svg+xml,<svg onload=alert(1) />';
  context.window.ComparisonView.render(container, report);
  assert.equal(descendants(container, 'IMG').length, 0);
  assert.match(descendants(container, 'P').find(element => element.className === 'comparison-visual-note').textContent,
    /invalid|limit/i);
  assert.ok(!descendants(container, 'IMG').some(image => image.src.startsWith('data:')));
  context.window.ComparisonView.clear(container);
});
