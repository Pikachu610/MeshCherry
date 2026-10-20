const test = require('node:test');
const assert = require('node:assert/strict');
const QA = require('../src/gis_mesh_workbench/webui/quality_panel.js');

function result(count = 3) {
  const elements = Array.from({ length: count }, (_, i) => ({ element_id: `e${i}`, quality_q: (i + 1) / 10, min_angle_deg: 10 + i, aspect_ratio: i + 1, area: 4, longest_edge: 3, size_ratio: null, validity_status: 'VALID', issue_codes: [] }));
  return {
    schema_version: QA.SCHEMA, mesh_hash: 'mesh', config_hash: 'config', metric_definition_version: 'tri-v1', status: 'WARNING',
    validation: { structurally_valid: { status: 'PASS' }, topologically_conforming: { status: 'PASS' }, non_overlapping: { status: 'PASS' }, matches_input_domain: { status: 'NOT_AVAILABLE', reason: 'ROI chưa có.' } },
    summary: { element_count: count, valid_element_count: count, invalid_element_count: 0 }, elements,
    issues: [{ code: 'SHAPE_WARNING', category: 'SHAPE', severity: 'warning', message: 'Góc nhỏ.', element_ids: ['e0'], node_ids: [] }],
    definitions: { quality_q: 'Q normalized, unitless', coordinate_units: 'm' },
    histograms: { quality_q: { status: 'AVAILABLE', edges: [0, 0.5, 1], counts: [2, 1], valid_count: count, excluded_count: 0 }, min_angle_deg: { status: 'AVAILABLE', edges: [0, 30, 60], counts: [2, 1], valid_count: count, excluded_count: 0 }, aspect_ratio: { status: 'AVAILABLE', edges: [1, 2, 4], counts: [1, 2], valid_count: count, excluded_count: 0 }, area: { status: 'AVAILABLE', edges: [0, 5, 10], counts: [count, 0], valid_count: count, excluded_count: 0 }, size_ratio: { status: 'NOT_AVAILABLE', reason: 'Không có trường kích thước.', valid_count: 0, excluded_count: count } },
    statistics: { quality_q: { status: 'AVAILABLE', min: 0.1, p5: 0.1, median: 0.2, mean: 0.2, p95: 0.3, max: 0.3, valid_count: count, excluded_count: 0 }, min_angle_deg: { status: 'AVAILABLE', min: 10, max: 12, valid_count: count, excluded_count: 0 }, aspect_ratio: { status: 'AVAILABLE', min: 1, max: 3, valid_count: count, excluded_count: 0 } },
    worst: { lowest_quality_q: elements.slice(0, 2), lowest_min_angle: elements.slice(0, 2), highest_aspect_ratio: elements.slice(-2).reverse() },
    boundary: { status: 'NOT_AVAILABLE', reason: 'ROI chưa có.' }, size: { status: 'NOT_AVAILABLE', reason: 'Không có trường kích thước.' },
    performance: { status: 'AVAILABLE', qa_time_seconds: 0.1, generation_time_seconds: null, peak_memory_bytes: null, peak_memory_status: 'NOT_AVAILABLE', memory_method: 'not measured' }
  };
}

class FakeElement {
  constructor(tag, document) { this.tagName = tag.toUpperCase(); this.ownerDocument = document; this.children = []; this.attributes = {}; this.listeners = {}; this.dataset = {}; this.hidden = false; this.disabled = false; this.value = ''; this.textContent = ''; this.className = ''; this.style = {}; }
  append(...items) { for (const item of items) { this.children.push(item); item.parentNode = this; } }
  replaceChildren(...items) { this.children = []; this.append(...items); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(key, fn) { (this.listeners[key] ||= []).push(fn); }
  async dispatch(key, event = {}) { for (const fn of this.listeners[key] || []) await fn({ target: this, preventDefault() {}, key: '', ...event }); }
  async click() { if (!this.disabled) await this.dispatch('click'); }
  get elements() { return { namedItem: name => this.descendants().find(node => node.name === name) || null }; }
  querySelectorAll(selector) {
    const matches = this.descendants();
    if (selector.startsWith('.')) return matches.filter(node => node.className.split(/\s+/).includes(selector.slice(1)));
    if (selector === 'input') return matches.filter(node => node.tagName === 'INPUT');
    return [];
  }
  descendants() { return this.children.flatMap(node => [node, ...node.descendants()]); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(node => node !== this); }
  get text() { return this.textContent + this.children.map(node => node.text).join(''); }
  findText(text) { return this.descendants().find(node => node.textContent === text); }
}
function host() {
  const document = { createElement: tag => new FakeElement(tag, document), createElementNS: (_ns, tag) => new FakeElement(tag, document) };
  const container = new FakeElement('main', document); return { document, container };
}

test('validates the v1 QA contract and rejects mismatched or incomplete results', () => {
  assert.equal(QA.validateResult(result()).status, 'WARNING');
  assert.throws(() => QA.validateResult({ ...result(), schema_version: 'old' }), /không được hỗ trợ/);
  const incomplete = result(); delete incomplete.boundary;
  assert.throws(() => QA.validateResult(incomplete), /boundary/);
});

test('normalizes nonfinite numbers to JSON null without serializing NaN', () => {
  const encoded = QA.safeJSON({ value: NaN, nested: [Infinity, -Infinity, 2] });
  assert.equal(encoded, '{"value":null,"nested":[null,null,2]}');
  assert.doesNotMatch(encoded, /NaN|Infinity/);
});

test('histogram SVG has bounded elements and safe text labels', () => {
  const { document } = host();
  const svg = QA.histogramSVG(document, { edges: Array.from({ length: 102 }, (_, i) => i), counts: Array.from({ length: 101 }, () => 1) }, 'hist');
  assert.equal(svg.children.length, QA.MAX_BINS + 1);
  assert.equal(svg.attributes['aria-label'], 'hist');
});

test('mount does not run QA automatically; Run, focus, metric, export, and stop use callbacks', async () => {
  const { container } = host(); let evaluated = 0, metric = '', focused = '', exported = '', stopped = 0;
  const panel = QA.mount(container, {
    evaluate: async config => { evaluated++; assert.equal(config.warning_min_angle_deg, 20); return result(); },
    onMetric: value => { metric = value; }, onFocus: id => { focused = id; }, onExport: kind => { exported = kind; }, onStop: async () => { stopped++; }
  });
  assert.equal(evaluated, 0);
  await container.findText('Chạy QA').click();
  assert.equal(evaluated, 1);
  await container.findText('Xuất JSON').click(); assert.equal(exported, 'json');
  await container.findText('Phần tử yếu').click();
  const focusRow = container.descendants().find(node => node.tagName === 'TR' && node.children.some(cell => cell.textContent === 'e0'));
  await focusRow.click(); assert.equal(focused, 'e0');
  const histTab = container.findText('Phân bố'); await histTab.click();
  await container.findText('Tô Q chuẩn hóa trên bản đồ').click(); assert.equal(metric, 'quality_q');
  await container.findText('Bỏ tô màu, xem toàn lưới').click(); assert.equal(metric, null);
  panel.setRunning(true); await container.findText('Dừng').click(); assert.equal(stopped, 1);
  panel.destroy(); assert.equal(container.children.length, 0);
});

test('unavailable boundary is explained once, backend reasons and definitions are localized', () => {
  const { container } = host(); const data = result();
  data.boundary.reason = 'Original ROI was not retained for this mesh';
  data.size.reason = 'Original target-size field is unavailable; no target inferred from actual edges';
  data.performance.generation_time_seconds_reason = 'This QA job reuses an accepted mesh; generation was not rerun.';
  data.definitions = { quality_q: 'Q normalized, unitless' };
  const panel = QA.mount(container, {}); panel.load(data);
  const rendered = container.children[0].text;
  assert.match(rendered, /Miền ROI ban đầu không được lưu kèm lưới này/);
  assert.equal((rendered.match(/Miền ROI ban đầu không được lưu kèm lưới này/g) || []).length, 1);
  assert.doesNotMatch(rendered, /Thiếu diện tích:|Diện tích ngoài biên:|Sai khác đối xứng:/);
  assert.match(rendered, /Không có trường kích thước mục tiêu ban đầu/);
  assert.match(rendered, /Lượt QA này dùng lại lưới hiện có/);
  assert.match(rendered, /Chỉ số không đơn vị; tam giác đều có Q = 1/);
  panel.destroy();
});

test('worst-element list renders at most one bounded page of 50 rows', async () => {
  const { container } = host(); const panel = QA.mount(container, {}); const data = result(120);
  data.worst.lowest_quality_q = data.elements; data.worst.lowest_min_angle = data.elements; data.worst.highest_aspect_ratio = data.elements;
  panel.load(data);
  await container.findText('Phần tử yếu').click();
  const rendered = container.descendants().filter(node => node.tagName === 'TR');
  assert.equal(rendered.length, 51); // one header plus 50 body rows
  panel.destroy();
});

test('benchmark runs only on explicit request and passes the configured repetition count', async () => {
  const { container } = host(); let called = 0, repetitions = 0; const data = result();
  data.benchmark = { requested_repetitions: 5, successful_runs: 5, aggregate: { qa_time_seconds: { count: 5, median: 0.4, min: 0.3, max: 0.6 } }, memory: { method: 'sampled_process_tree_rss', limitations: 'Sampling can miss short-lived peaks; process membership is observed, not atomic.' } };
  const panel = QA.mount(container, { benchmark: async config => { called++; repetitions = config.repetitions; return data; } });
  assert.equal(called, 0); await container.findText('Benchmark').click();
  assert.equal(called, 1); assert.equal(repetitions, 5);
  assert.match(container.children[0].text, /Đo lặp QA hình học/);
  assert.match(container.children[0].text, /Biên ROI và kích thước không nằm trong phép đo lặp/);
  assert.match(container.children[0].text, /trung vị \/ min \/ max: 0,4 s \/ 0,3 s \/ 0,6 s/);
  assert.match(container.children[0].text, /Lấy mẫu RSS của tiến trình/);
  panel.clear(); assert.match(container.children[0].text, /Chưa có kết quả QA/);
  panel.destroy();
});

test('flat size statistics render measured values once without scalar N/A rows', () => {
  const { container } = host(); const panel = QA.mount(container, {}); const data = result();
  data.size = { status: 'PASS', statistics: { status: 'AVAILABLE', valid_count: 3, excluded_count: 0, min: 0, p5: 0.1, median: 0.7, mean: 0.6, p95: 1.1, p99: 1.2, max: 1.3 } };
  panel.load(data);
  const size = container.descendants().find(n => n.tagName === 'SECTION' && n.children[0]?.text.includes('Phù hợp kích thước'));
  assert.doesNotMatch(size.text, /N\/A|Chưa được cung cấp|status:|valid count:/);
  assert.match(size.text, /Trung vị: 0,7/);
  assert.match(size.text, /Nhỏ nhất: 0/);
  assert.match(size.text, /3 hợp lệ/);
  panel.destroy();
});

test('missing metric cards are absent and QA settings and definitions start collapsed', () => {
  const { container } = host(); const panel = QA.mount(container, {}); panel.load(result());
  assert.equal(container.findText('Size ratio lớn nhất'), undefined);
  assert.doesNotMatch(container.children[0].text, /N\/A/);
  const settings = container.descendants().find(n => n.tagName === 'DETAILS' && n.children.some(c => c.tagName === 'FORM'));
  assert.ok(settings); assert.notEqual(settings.open, true);
  const definitions = container.descendants().find(n => n.tagName === 'DETAILS' && n.children.some(c => c.tagName === 'SUMMARY' && c.text.includes('Định nghĩa')));
  assert.ok(definitions); assert.notEqual(definitions.open, true);
  panel.destroy();
});

test('all-missing size summary produces no placeholder statistic rows, keeping unavailable check visible', () => {
  const { container } = host(); const panel = QA.mount(container, {}); const data = result();
  data.size.statistics = { status: 'NOT_AVAILABLE', valid_count: 0, excluded_count: 3, min: null, p5: null, median: null, mean: null, p95: null, p99: null, max: null };
  panel.load(data);
  assert.match(container.children[0].text, /Không có trường kích thước/);
  assert.doesNotMatch(container.children[0].text, /Chưa được cung cấp|N\/A hợp lệ|N\/A loại trừ/);
  panel.destroy();
});

test('historical named size statistics and AR maximum keep their measured values', () => {
  const { container } = host(); const panel = QA.mount(container, {}); const data = result();
  data.size = { status: 'PASS', statistics: { size_ratio: { min: 0.2, median: 0.8, max: 1.1, valid_count: 3 }, absent: { min: null, median: null } } };
  panel.load(data);
  const ar = container.descendants().find(n => n.className === 'mcp-metric' && n.children.some(c => c.textContent === 'AR lớn nhất'));
  assert.equal(ar.children[1].textContent, '3');
  const size = container.descendants().find(n => n.tagName === 'SECTION' && n.children[0]?.text.includes('Phù hợp kích thước'));
  assert.match(size.text, /Tỷ số kích thước · Nhỏ nhất: 0,2 · Trung vị: 0,8 · Lớn nhất: 1,1/);
  assert.doesNotMatch(size.text, /absent|N\/A/);
  panel.destroy();
});

test('unavailable histogram groups collapse into one note instead of empty charts', async () => {
  const { container } = host(); const panel = QA.mount(container, {}); const data = result();
  data.histograms = {};
  panel.load(data); await container.findText('Phân bố').click();
  assert.equal(container.descendants().filter(n => n.className === 'mcp-chart').length, 0);
  assert.equal(container.descendants().filter(n => n.className === 'mcp-unavailable-metrics').length, 1);
  assert.doesNotMatch(container.children[0].text, /N\/A hợp lệ|N\/A loại trừ/);
  panel.destroy();
});
