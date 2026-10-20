/* MeshCherry QA explorer. Browser global: MeshCherryQualityPanel. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.MeshCherryQualityPanel = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const SCHEMA = 'meshcherry-quality-v1';
  const PAGE_SIZE = 50;
  const MAX_BINS = 60;
  const TYPES = ['STRUCTURE', 'TOPOLOGY', 'OVERLAP', 'BOUNDARY', 'SHAPE', 'SIZE'];
  const METRICS = [
    ['quality_q', 'Q chuẩn hóa', ''], ['min_angle_deg', 'Góc nhỏ nhất', '°'],
    ['aspect_ratio', 'Tỷ số hình dạng AR', ''], ['area', 'Diện tích', ''],
    ['size_ratio', 'Size ratio', ''], ['longest_edge', 'Cạnh dài nhất', '']
  ];
  const isNumber = n => typeof n === 'number' && Number.isFinite(n);
  const fmt = n => isNumber(n) ? new Intl.NumberFormat('vi-VN', { maximumSignificantDigits: 6 }).format(n) : 'N/A';
  const safeJSON = value => JSON.stringify(value, (_, v) => typeof v === 'number' && !Number.isFinite(v) ? null : v);
  const text = value => String(value ?? '');
  const statusText = value => ({ PASS: 'ĐẠT', WARNING: 'CẢNH BÁO', FAIL: 'KHÔNG ĐẠT', ERROR: 'LỖI', PARTIAL: 'MỘT PHẦN', NOT_AVAILABLE: 'CHƯA CÓ DỮ LIỆU', NOT_MEASURED: 'CHƯA ĐO' })[text(value).toUpperCase()] || text(value || 'CHƯA CÓ DỮ LIỆU');
  const reasonText = value => {
    const reason = text(value);
    if (reason.toUpperCase() === 'NOT_AVAILABLE' || reason.toUpperCase() === 'NOT_MEASURED') return 'Chưa có số liệu đo.';
    if (/^original roi was not retained for this mesh$/i.test(reason)) return 'Miền ROI ban đầu không được lưu kèm lưới này.';
    if (/^original target-size field is unavailable; no target inferred from actual edges$/i.test(reason)) return 'Không có trường kích thước mục tiêu ban đầu; không suy ra mục tiêu từ cạnh lưới thực tế.';
    if (/^this qa job reuses/i.test(reason)) return 'Lượt QA này dùng lại lưới hiện có; không tạo lại lưới.';
    if (/^boundary assessment is enriched by the workbench integration layer$/i.test(reason)) return 'Chưa có đối chiếu biên trong lượt đánh giá hình học này.';
    if (/psutil unavailable or no process-tree RSS sample was collected/i.test(reason)) return 'Không có mẫu bộ nhớ tiến trình để báo cáo.';
    if (/sampling can miss short-lived peaks/i.test(reason)) return 'Phép lấy mẫu có thể bỏ sót đỉnh bộ nhớ ngắn; nhóm tiến trình được quan sát theo thời điểm lấy mẫu.';
    if (reason === 'sampled_process_tree_rss') return 'Lấy mẫu RSS của tiến trình và tiến trình con';
    if (/not available|not measured|not provided|not applicable|not computed/i.test(reason)) return 'Chưa có số liệu được đo hoặc cung cấp.';
    return reason;
  };
  const metricStats = (result, key) => result.statistics?.[key] || result.size?.statistics?.[key] || result.summary?.[key] || null;
  const missingReason = (result, key, item) => reasonText(item?.reason || result.definitions?.[`${key}_reason`] || `Chưa có giá trị hợp lệ cho ${key}.`);

  function validateResult(result) {
    if (!result || typeof result !== 'object' || Array.isArray(result)) throw new TypeError('Kết quả QA phải là một đối tượng.');
    if (result.schema_version !== SCHEMA) throw new TypeError(`Schema QA không được hỗ trợ: ${text(result.schema_version || 'không có')}`);
    for (const key of ['mesh_hash', 'config_hash', 'metric_definition_version', 'status', 'summary', 'validation', 'elements', 'issues', 'definitions', 'histograms', 'statistics', 'worst', 'boundary', 'size', 'performance']) {
      if (!(key in result)) throw new TypeError(`Kết quả QA thiếu trường bắt buộc: ${key}`);
    }
    if (!Array.isArray(result.elements) || !Array.isArray(result.issues)) throw new TypeError('elements và issues phải là danh sách.');
    return result;
  }

  function histogramSVG(document, histogram, label) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 600 150'); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', label);
    const counts = Array.isArray(histogram?.counts) ? histogram.counts.slice(0, MAX_BINS) : [];
    if (!counts.length) return svg;
    const max = Math.max(1, ...counts.map(n => isNumber(n) && n > 0 ? n : 0));
    const edges = Array.isArray(histogram.edges) ? histogram.edges : [];
    const width = 560 / counts.length;
    counts.forEach((count, index) => {
      const value = isNumber(count) && count >= 0 ? count : 0, height = value ? Math.max(2, 110 * value / max) : 0;
      const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      rect.setAttribute('x', String(28 + index * width)); rect.setAttribute('y', String(125 - height));
      rect.setAttribute('width', String(Math.max(1, width - 2))); rect.setAttribute('height', String(height)); rect.setAttribute('rx', '3'); rect.setAttribute('class', 'mcp-hist-bar');
      const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      title.textContent = `${fmt(edges[index])}–${edges[index+1]===null?'∞':fmt(edges[index + 1])}: ${fmt(value)} phần tử`; rect.append(title); svg.append(rect);
    });
    const axis = document.createElementNS('http://www.w3.org/2000/svg', 'path'); axis.setAttribute('d', 'M28 125H588'); axis.setAttribute('class', 'mcp-hist-axis'); svg.append(axis);
    return svg;
  }

  function mount(container, options = {}) {
    if (!container?.ownerDocument) throw new TypeError('Cần phần tử DOM để gắn bảng QA.');
    for (const key of ['evaluate', 'fetchPage', 'onMetric', 'onFocus', 'onExport', 'onStop', 'benchmark']) {
      if (options[key] != null && typeof options[key] !== 'function') throw new TypeError(`${key} phải là hàm.`);
    }
    const doc = container.ownerDocument;
    const state = { result: null, running: false, stopping: false, destroyed: false, tab: 'overview', page: 0, issueType: 'ALL', sort: 'quality_q', order: 'asc', metric: 'quality_q', request: 0 };
    const make = (tag, cls, label) => { const n = doc.createElement(tag); if (cls) n.className = cls; if (label != null) n.textContent = label; return n; };
    const btn = (label, fn, cls = '') => { const n = make('button', cls, label); n.type = 'button'; n.addEventListener('click', fn); return n; };
    const box = (title, note) => { const n = make('section', 'mcp-section'), head = make('div', 'mcp-section-head'); head.append(make('h3', '', title)); if (note) head.append(make('p', '', note)); n.append(head); return n; };
    const root = make('div', 'meshcherry-quality-panel'); root.dataset.state = 'empty';
    const header = make('header', 'mcp-top'), intro = make('div');
    intro.append(make('h2', '', 'Chất lượng lưới'), make('p', 'mcp-subtitle', 'Đánh giá lưới hiện tại trước mô phỏng.'));
    const actions = make('div', 'mcp-actions'); const runButton = btn('Chạy QA', run, 'mcp-primary'), stopButton = btn('Dừng', stop); stopButton.hidden = true;
    const clearOverlayButton = btn('Bỏ tô màu, xem toàn lưới', () => selectMetric(null));
    actions.append(runButton, stopButton, clearOverlayButton); header.append(intro, actions);
    const live = make('p', 'mcp-live', 'Chọn “Chạy QA” để đánh giá lưới hiện tại.'); live.setAttribute('role', 'status'); live.setAttribute('aria-live', 'polite');
    const form = make('form', 'mcp-config'); form.addEventListener('submit', e => { e.preventDefault(); run(); });
    const settings = make('details', 'mcp-settings'); settings.append(make('summary', '', 'Ngưỡng cảnh báo và benchmark'), form);
    const fields = [
      ['warning_min_angle_deg', 'Góc nhỏ nhất', '°', 20], ['warning_quality_q', 'Q chuẩn hóa', '', 0.2],
      ['warning_aspect_ratio', 'Tỷ số hình dạng AR', '', 10], ['relative_geometry_epsilon', 'Dung sai hình học tương đối', 'tỉ lệ', 1e-12],
      ['boundary_relative_tolerance', 'Dung sai diện tích biên tương đối', 'tỉ lệ', 1e-8],
      ['boundary_distance_tolerance_m', 'Dung sai khoảng cách biên', 'm', 0.01],
      ['boundary_densify', 'Mật độ lấy mẫu biên', 'tỉ lệ cạnh', 0.25],
      ['size_tolerance', 'Dung sai kích thước', 'tỉ lệ', 0.25]
    ];
    fields.forEach(([name, label, unit, initial]) => {
      const field = make('label', 'mcp-field'); field.append(make('span', '', label));
      const input = make('input'); input.type = 'number'; input.name = name; input.step = 'any'; input.min = '0'; input.value = String(initial);
      field.append(input, make('small', '', unit)); form.append(field);
    });
    const repetitionsField = make('label', 'mcp-field'); repetitionsField.append(make('span', '', 'Số lần benchmark'));
    const repetitionsInput = make('input'); repetitionsInput.type = 'number'; repetitionsInput.name = 'repetitions'; repetitionsInput.min = '1'; repetitionsInput.max = '30'; repetitionsInput.step = '1'; repetitionsInput.value = '5';
    repetitionsField.append(repetitionsInput, make('small', '', 'lần')); form.append(repetitionsField);
    const tabs = make('nav', 'mcp-tabs'); tabs.setAttribute('aria-label', 'Các mục đánh giá chất lượng');
    const tabDefs = [['overview', 'Tổng quan'], ['histograms', 'Phân bố'], ['worst', 'Phần tử yếu'], ['issues', 'Vấn đề']];
    tabDefs.forEach(([id, label]) => tabs.append(btn(label, () => { state.tab = id; state.page = 0; render(); }, 'mcp-tab')));
    const content = make('div', 'mcp-content'), exports = make('div', 'mcp-export');
    [['JSON', 'json'], ['CSV', 'csv'], ['HTML offline', 'html']].forEach(([label, kind]) => exports.append(btn(`Xuất ${label}`, () => exportAs(kind))));
    actions.append(btn('Benchmark', runBenchmark));
    root.append(header, live, settings, tabs, content, exports); container.replaceChildren(root);

    function readConfig() {
      const config = {};
      for (const [name] of fields) {
        const value = Number(form.elements.namedItem(name).value);
        if (!isNumber(value) || value < 0) throw new Error('Các ngưỡng phải là số hữu hạn không âm.');
        config[name] = value;
      }
      if (config.relative_geometry_epsilon >= 1e-2) throw new Error('Dung sai hình học tương đối phải nhỏ hơn 1e-2.');
      if (config.boundary_densify < 0.05 || config.boundary_densify > 1) throw new Error('Mật độ lấy mẫu biên phải nằm trong khoảng 0,05 đến 1.');
      const repetitions = Number(form.elements.namedItem('repetitions').value);
      if (!Number.isInteger(repetitions) || repetitions < 1 || repetitions > 30) throw new Error('Số lần benchmark phải là số nguyên từ 1 đến 30.');
      config.repetitions = repetitions;
      return config;
    }
    async function run() {
      if (!options.evaluate || state.running) return;
      let config; try { config = readConfig(); } catch (e) { live.textContent = e.message; return; }
      const request = ++state.request; setRunning(true); state.stopping = false; live.textContent = 'Đang kiểm tra lưới hiện tại…';
      try { const result = await options.evaluate(config); if (!state.destroyed && request === state.request) load(result); }
      catch (e) { if (!state.destroyed && request === state.request) { root.dataset.state = 'error'; live.textContent = `Không hoàn tất QA: ${e.message || e}`; } }
      finally { if (!state.destroyed && request === state.request) setRunning(false); }
    }
    async function runBenchmark() {
      if (!options.benchmark || state.running) return;
      let config; try { config = readConfig(); } catch (e) { live.textContent = e.message; return; }
      const request = ++state.request; setRunning(true); live.textContent = `Đang benchmark ${config.repetitions} lần…`;
      try { const result = await options.benchmark(config); if (!state.destroyed && request === state.request) load(result); }
      catch (e) { if (!state.destroyed && request === state.request) live.textContent = `Benchmark chưa hoàn tất: ${e.message || e}`; }
      finally { if (!state.destroyed && request === state.request) setRunning(false); }
    }
    async function stop() {
      if (!state.running || state.stopping) return;
      state.stopping = true; stopButton.disabled = true; stopButton.textContent = 'Đang dừng…'; live.textContent = 'Đang dừng; chờ bộ tính toán xác nhận trạng thái cuối.';
      try { await options.onStop?.(); } catch (e) { live.textContent = `Đang dừng; chưa nhận được xác nhận: ${e.message || e}`; }
    }
    function setRunning(value) {
      state.running = Boolean(value); state.stopping = false; root.dataset.running = String(state.running);
      runButton.disabled = state.running; stopButton.hidden = !state.running; stopButton.disabled = false; stopButton.textContent = 'Dừng';
      form.querySelectorAll('input').forEach(input => { input.disabled = state.running; });
      if (!state.running && state.result) live.textContent = `QA ${statusText(state.result.status)}: ${fmt(state.result.summary.element_count)} phần tử.`;
    }
    function card(label, stat, unit, metric, prefer = 'min') {
      const node = metric ? btn('', () => selectMetric(metric), 'mcp-metric') : make('div', 'mcp-metric');
      const value = stat && isNumber(stat[prefer] ?? stat.min ?? stat.max) ? (stat[prefer] ?? stat.min ?? stat.max) : null;
      if (value == null) { node.hidden = true; return node; }
      node.append(make('span', '', label), make('strong', '', `${fmt(value)}${unit}`));
      return node;
    }
    function renderOverview() {
      const result = state.result, summary = result.summary, metrics = make('div', 'mcp-metrics');
      metrics.append(card('Q thấp nhất', metricStats(result, 'quality_q'), '', 'quality_q'), card('Góc nhỏ nhất', metricStats(result, 'min_angle_deg'), '°', 'min_angle_deg'), card('AR lớn nhất', metricStats(result, 'aspect_ratio'), '', 'aspect_ratio', 'max'));
      const areaUnits = result.definitions?.area_units || (result.definitions?.coordinate_units ? `${result.definitions.coordinate_units}²` : '');
      const lengthUnits = result.definitions?.coordinate_units || '';
      metrics.append(card('Diện tích nhỏ nhất', metricStats(result, 'area'), areaUnits ? ` ${areaUnits}` : '', 'area'));
      metrics.append(card('Cạnh dài nhất lớn nhất', metricStats(result, 'longest_edge'), lengthUnits ? ` ${lengthUnits}` : '', 'longest_edge', 'max'));
      metrics.append(card('Size ratio lớn nhất', metricStats(result, 'size_ratio'), '', 'size_ratio', 'max'));
      [['Phần tử', summary.element_count], ['Hợp lệ', summary.valid_element_count], ['Loại khỏi thống kê', result.statistics?.quality_q?.excluded_count]].forEach(([label, value]) => metrics.append(card(label, isNumber(value) ? { min: value } : null, '', null)));
      const overview = box('Tóm tắt', `${statusText(result.status)} · phiên bản định nghĩa chỉ số ${result.metric_definition_version}`);
      if (result.restored_from_project === true || result.provenance?.restored_from_project === true) overview.append(make('p', 'mcp-cache-note', 'Kết quả đã lưu · Chưa chạy lại QA'));
      overview.append(metrics);
      const valid = box('Tính hợp lệ', 'Trạng thái hard validity, topology và overlap từ bộ kiểm tra.');
      const validationLabels = { structurally_valid: 'Hợp lệ về cấu trúc', topologically_conforming: 'Phù hợp tô-pô', non_overlapping: 'Không chồng lấn', matches_input_domain: 'Khớp miền đầu vào' };
      Object.entries(result.validation).forEach(([key, value]) => { const line = make('p', `mcp-status-line mcp-${text(value?.status).toLowerCase()}`, `${validationLabels[key] || key.replaceAll('_', ' ')}: ${statusText(value?.status)}${value?.reason ? ` · ${reasonText(value.reason)}` : ''}`); valid.append(line); });
      const boundary = box('Biên ROI', reasonText(result.boundary?.reason || 'Đối chiếu miền và lỗ biên.')); boundary.append(make('p', `mcp-status-line mcp-${text(result.boundary?.status).toLowerCase()}`, statusText(result.boundary?.status)));
      const boundaryUnits = result.boundary?.area_units || 'đơn vị diện tích chưa khai báo';
      [['Thiếu diện tích', 'missing_area', boundaryUnits], ['Diện tích ngoài biên', 'extra_area', boundaryUnits], ['Sai khác đối xứng', 'symmetric_difference_area', boundaryUnits], ['Sai số diện tích tương đối', 'relative_area_error', 'tỉ lệ'], ['Tỷ lệ phủ', 'coverage_ratio', 'tỉ lệ'], ['Hausdorff', 'hausdorff_distance', result.boundary?.coordinate_units || 'đơn vị chưa khai báo']].filter(([, key]) => isNumber(result.boundary?.[key])).forEach(([label, key, unit]) => boundary.append(make('p', 'mcp-caption', `${label}: ${fmt(result.boundary[key])} ${unit}`)));
      const size = box('Phù hợp kích thước', reasonText(result.size?.reason || 'Đối chiếu kích thước phần tử với trường kích thước mục tiêu.')); size.append(make('p', `mcp-status-line mcp-${text(result.size?.status).toLowerCase()}`, statusText(result.size?.status)));
      if (result.size?.definition) size.append(make('p', 'mcp-caption', `Định nghĩa: ${result.size.definition}`));
      if (isNumber(result.size?.exceed_fraction)) size.append(make('p', 'mcp-caption', `Tỷ lệ vượt dung sai: ${fmt(result.size.exceed_fraction)} tỉ lệ`));
      const statisticLabels = { min: 'Nhỏ nhất', p5: 'P5', median: 'Trung vị', mean: 'Trung bình', p95: 'P95', p99: 'P99', max: 'Lớn nhất' };
      const sizeStats = result.size?.statistics || {};
      const measured = stat => stat && typeof stat === 'object' && Object.keys(statisticLabels).some(key => isNumber(stat[key]));
      // Integration returns one flat statistics object; allow named groups from historical reports too.
      const groups = measured(sizeStats) ? [['Tỷ số kích thước', sizeStats]] : Object.entries(sizeStats).filter(([, stat]) => measured(stat));
      groups.forEach(([key, stat]) => {
        const values = Object.entries(statisticLabels).filter(([name]) => isNumber(stat[name])).map(([name, label]) => `${label}: ${fmt(stat[name])}`);
        if (isNumber(stat.valid_count)) values.push(`${fmt(stat.valid_count)} hợp lệ`);
        if (isNumber(stat.excluded_count) && stat.excluded_count > 0) values.push(`${fmt(stat.excluded_count)} loại trừ`);
        size.append(make('p', 'mcp-caption', `${key === 'size_ratio' ? 'Tỷ số kích thước' : key.replaceAll('_', ' ')} · ${values.join(' · ')}`));
      });
      const defs = box('Định nghĩa và đơn vị', 'Các chỉ số hình học được tính từ tọa độ lưới; giá trị thiếu không được thay bằng 0.'); const dl = make('dl', 'mcp-definitions');
      const definitionLabels = { area: 'Diện tích tam giác', perimeter: 'Chu vi tam giác', min_angle_deg: 'Góc nhỏ nhất', max_angle_deg: 'Góc lớn nhất', aspect_ratio: 'Tỷ số hình dạng AR', quality_q: 'Q chuẩn hóa', signed_area: 'Diện tích có dấu', relative_geometry_epsilon: 'Dung sai hình học', overlap_relative_tolerance: 'Dung sai chồng lấn', statistical_policy: 'Quy tắc thống kê', coordinate_units: 'Đơn vị tọa độ' };
      const definitionValues = { 'absolute signed triangle area; coordinate_units squared': 'Diện tích tuyệt đối của tam giác; đơn vị tọa độ bình phương.', 'sum of triangle edge lengths; coordinate_units': 'Tổng độ dài ba cạnh tam giác.', 'minimum of atan2(abs(cross), dot) vertex angles, degrees': 'Góc nhỏ nhất tại các đỉnh, tính bằng độ.', 'maximum of atan2(abs(cross), dot) vertex angles, degrees': 'Góc lớn nhất tại các đỉnh, tính bằng độ.', 'Lmax^2/(2*A) = Lmax/minimum altitude; unitless': 'Cạnh dài nhất bình phương chia hai lần diện tích; không đơn vị.', '16*A^2/((a+b+c)*a*b*c) = 2r/R; unitless, equilateral=1': 'Chỉ số không đơn vị; tam giác đều có Q = 1.', 'Q normalized, unitless': 'Chỉ số không đơn vị; tam giác đều có Q = 1.', '0.5*cross2(B-A,C-A); positive CCW, negative CW': 'Dấu cho biết hướng đỉnh ngược hay cùng chiều kim đồng hồ.', '1e-12 default; reject if |cross|/Lmax^2 <= epsilon; scale-relative, unitless': 'Mặc định 1e-12; dung sai tương đối theo tỷ lệ hình học.', '1e-10 default times smaller element area': 'Mặc định 1e-10 nhân với diện tích phần tử nhỏ hơn.', 'Only valid triangle observations enter triangle statistics; excluded_count counts invalid triangle rows.': 'Chỉ tam giác hợp lệ được đưa vào thống kê; số bị loại là số tam giác không hợp lệ.' };
      Object.entries(result.definitions || {}).slice(0, 40).forEach(([key, value]) => { dl.append(make('dt', '', definitionLabels[key] || key.replaceAll('_', ' ')), make('dd', '', typeof value === 'string' ? (definitionValues[value] || value) : (value?.definition || safeJSON(value)))); }); defs.append(dl);
      const perf = box('Hiệu năng', 'Chỉ số thời gian và bộ nhớ theo phạm vi được backend đo.');
      [['Thời gian QA', result.performance?.qa_time_seconds, ' s', 'qa_time_seconds_reason'], ['Thời gian tạo mesh', result.performance?.generation_time_seconds, ' s', 'generation_time_seconds_reason'], ['Bộ nhớ đỉnh', result.performance?.peak_memory_bytes, ' byte', 'peak_memory_reason']].forEach(([label, value, unit, reasonKey]) => {
        const reason = result.performance?.[reasonKey] || (label === 'Thời gian tạo mesh' && result.provenance?.mesh_job_id ? 'Lưới đã có sẵn; lượt QA không tạo lại mesh.' : label === 'Bộ nhớ đỉnh' ? result.performance?.memory_method || result.performance?.peak_memory_status : 'Chưa có số liệu đo.');
        perf.append(make('p', 'mcp-caption', `${label}: ${isNumber(value) ? fmt(value) + unit : reasonText(reason)}`));
      });
      let benchmarkSummary = null;
      if (result.benchmark && typeof result.benchmark === 'object') {
        const benchmark = result.benchmark, aggregate = benchmark.aggregate || {}, summary = box('Đo lặp QA hình học', `${fmt(benchmark.successful_runs)} / ${fmt(benchmark.requested_repetitions)} lượt thành công · trên lưới hiện tại, không tạo lại mesh. Biên ROI và kích thước không nằm trong phép đo lặp.`);
        const metricLabels = { qa_time_seconds: ['Thời gian QA', ' s'], pipeline_time_seconds: ['Tổng thời gian lượt', ' s'], q_mean: ['Q trung bình', ''], q_p5: ['Q phân vị 5%', ''], ar_p95: ['Aspect ratio phân vị 95%', ''], peak_rss_bytes: ['Bộ nhớ đỉnh', ' byte'] };
        Object.entries(metricLabels).forEach(([key, [label, unit]]) => { const stat = aggregate[key]; if (!stat) return; summary.append(make('p', 'mcp-caption', `${label} · trung vị / min / max: ${isNumber(stat.median) ? fmt(stat.median) + unit : 'N/A'} / ${isNumber(stat.min) ? fmt(stat.min) + unit : 'N/A'} / ${isNumber(stat.max) ? fmt(stat.max) + unit : 'N/A'} · ${fmt(stat.count)} lượt`)); });
        if (benchmark.memory?.method) summary.append(make('p', 'mcp-caption', `Phương pháp đo bộ nhớ: ${reasonText(benchmark.memory.method)}${benchmark.memory.scope ? ` · ${benchmark.memory.scope}` : ''}`));
        if (benchmark.memory?.limitations) summary.append(make('p', 'mcp-caption', reasonText(benchmark.memory.limitations)));
        benchmarkSummary = summary;
      }
      const definitionDetails = make('details', 'mcp-definition-details'); definitionDetails.append(make('summary', '', 'Định nghĩa và đơn vị'), defs);
      content.append(overview, valid, boundary, size, perf, ...(benchmarkSummary ? [benchmarkSummary] : []), definitionDetails);
    }
    function renderHistograms() {
      const sectionNode = box('Phân bố chỉ số', 'Biểu đồ có tối đa 60 cột; số hợp lệ và bị loại trừ lấy trực tiếp từ QA.');
      const unavailable = [];
      METRICS.slice(0, 5).forEach(([key, label, unit]) => {
        const histogram = state.result.histograms?.[key];
        if (histogram?.status !== 'AVAILABLE' || !Array.isArray(histogram.counts) || !histogram.counts.length) {
          unavailable.push([label, missingReason(state.result, key, histogram)]); return;
        }
        const chart = make('article', 'mcp-chart');
        chart.append(make('h4', '', `${label}${unit ? ` (${unit})` : ''}`));
        const counts = [isNumber(histogram.valid_count) ? `${fmt(histogram.valid_count)} hợp lệ` : '', isNumber(histogram.excluded_count) && histogram.excluded_count > 0 ? `${fmt(histogram.excluded_count)} loại trừ` : '', `${histogram.counts.length} khoảng`].filter(Boolean);
        chart.append(make('p', 'mcp-caption', counts.join(' · ')));
        chart.append(histogramSVG(doc, histogram, `${label} histogram`));
        chart.append(btn(`Tô ${label} trên bản đồ`, () => selectMetric(key))); sectionNode.append(chart);
      });
      if (unavailable.length) {
        const note = make('details', 'mcp-unavailable-metrics');
        note.append(make('summary', '', `Chưa có phân bố cho ${unavailable.map(([label]) => label).join(', ')}`));
        for (const reason of new Set(unavailable.map(([, reason]) => reason))) note.append(make('p', 'mcp-caption', reason));
        sectionNode.append(note);
      }
      content.append(sectionNode);
    }
    function rowTable(rows, columns, focus) {
      const wrap = make('div', 'mcp-table-wrap'), table = make('table'), head = make('thead'), tr = make('tr');
      columns.forEach(([key, label]) => { const th = make('th'); th.append(btn(label, () => { state.sort = key; state.order = state.order === 'asc' ? 'desc' : 'asc'; showPage(); }, 'mcp-sort')); tr.append(th); }); head.append(tr); table.append(head);
      const body = make('tbody'); rows.slice(0, PAGE_SIZE).forEach(row => {
        const line = make('tr'); line.tabIndex = 0;
        columns.forEach(([key]) => { let value = row[key]; if (key === 'issue_codes' && Array.isArray(value)) value = value.join(', '); line.append(make('td', '', value == null ? 'N/A' : (isNumber(value) ? fmt(value) : text(value)))); });
        if (focus) { const activate = () => focus(row); line.addEventListener('click', activate); line.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); } }); }
        body.append(line);
      }); table.append(body); wrap.append(table); return wrap;
    }
    function renderWorst(rows) {
      const area = box('Phần tử cần xem trước', 'Tối đa 50 dòng mỗi trang. Chọn một dòng để định vị và làm nổi bật trên bản đồ.');
      area.append(rowTable(rows, [['element_id', 'ID'], ['quality_q', 'Q'], ['min_angle_deg', 'Góc nhỏ nhất'], ['aspect_ratio', 'AR'], ['area', 'Diện tích'], ['longest_edge', 'Cạnh dài nhất'], ['validity_status', 'Hợp lệ']], row => options.onFocus?.(row.element_id, row))); return area;
    }
    function renderIssues(rows) {
      const area = box('Vấn đề QA', 'Mã ổn định, mức độ, lý do, phần tử/nút liên quan và vị trí khi có.');
      const label = make('label', 'mcp-field'); label.append(make('span', '', 'Loại issue')); const select = make('select');
      [['ALL', 'Tất cả'], ...TYPES.map(type => [type, type])].forEach(([value, name]) => { const option = make('option', '', name); option.value = value; option.selected = state.issueType === value; select.append(option); });
      select.addEventListener('change', () => { state.issueType = select.value; state.page = 0; showPage(); }); label.append(select); area.append(label);
      const list = rows.filter(issue => state.issueType === 'ALL' || issue.category === state.issueType).map(issue => ({ ...issue,
        issue_type: issue.category, element_ids: Array.isArray(issue.element_ids) ? issue.element_ids.join(', ') : '',
        node_ids: Array.isArray(issue.node_ids) ? issue.node_ids.join(', ') : '', message: issue.message || issue.reason || '' }));
      area.append(rowTable(list, [['code', 'Mã'], ['severity', 'Mức độ'], ['issue_type', 'Loại'], ['message', 'Lý do'], ['element_ids', 'ID phần tử'], ['node_ids', 'ID nút']], issue => options.onFocus?.(issue.element_ids?.split(', ')[0], issue)));
      return area;
    }
    function sourceRows() {
      if (state.tab === 'issues') return state.result.issues;
      const groups = state.result.worst || {};
      let rows = state.sort === 'quality_q' ? (groups.lowest_quality_q || state.result.elements)
        : state.sort === 'min_angle_deg' ? (groups.lowest_min_angle || state.result.elements)
          : state.sort === 'aspect_ratio' ? (groups.highest_aspect_ratio || state.result.elements) : state.result.elements;
      if (rows.length <= 10000) rows = rows.slice().sort((a, b) => {
        const av = a[state.sort], bv = b[state.sort];
        if (!isNumber(av) || !isNumber(bv)) return 0;
        return (av - bv) * (state.order === 'asc' ? 1 : -1);
      });
      return rows;
    }
    async function showPage() {
      const request = ++state.request, args = { kind: state.tab, offset: state.page * PAGE_SIZE, limit: PAGE_SIZE, sort_by: state.sort, sort_order: state.order, issue_type: state.issueType };
      try {
        let response = options.fetchPage ? await options.fetchPage(args) : null;
        if (!response) {
          const rows = sourceRows();
          let items = rows.slice(args.offset, args.offset + PAGE_SIZE);
          if (rows.length > 10000) items.sort((a, b) => {
            const av = a[state.sort], bv = b[state.sort];
            return !isNumber(av) || !isNumber(bv) ? 0 : (av - bv) * (state.order === 'asc' ? 1 : -1);
          });
          response = { items, total: rows.length };
        }
        if (state.destroyed || request !== state.request) return;
        const rows = Array.isArray(response) ? response : response.items || [];
        const total = Number(response.total ?? rows.length);
        content.replaceChildren(state.tab === 'issues' ? renderIssues(rows) : renderWorst(rows));
        const pager = make('div', 'mcp-pager');
        const previous = btn('Trang trước', () => { state.page = Math.max(0, state.page - 1); showPage(); });
        const next = btn('Trang sau', () => { state.page++; showPage(); });
        previous.disabled = state.page === 0; next.disabled = (state.page + 1) * PAGE_SIZE >= total;
        pager.append(previous, make('span', '', `Trang ${state.page + 1} · ${fmt(total)} mục`), next); content.append(pager);
      } catch (e) { if (!state.destroyed && request === state.request) content.replaceChildren(make('p', 'mcp-error', `Không tải được danh sách: ${e.message || e}`)); }
    }
    function selectMetric(key) { state.metric = key; options.onMetric?.(key); render(); }
    function exportAs(kind) { if (!state.result) { live.textContent = 'Chưa có kết quả QA để xuất.'; return; } options.onExport?.(kind, state.result); }
    function render() {
      if (!state.result) { content.replaceChildren(make('p', 'mcp-empty-card', 'Chưa có kết quả QA. Chọn “Chạy QA” để bắt đầu.')); return; }
      tabs.querySelectorAll('.mcp-tab').forEach((tab, index) => tab.setAttribute('aria-current', tabDefs[index][0] === state.tab ? 'page' : 'false'));
      content.replaceChildren();
      if (state.tab === 'overview') renderOverview();
      else if (state.tab === 'histograms') renderHistograms();
      else showPage();
    }
    function load(result) {
      if (state.destroyed) return;
      state.result = validateResult(result); state.page = 0; state.tab = 'overview'; root.dataset.state = text(result.status).toLowerCase(); render();
      if (!state.running) live.textContent = `QA ${statusText(result.status)}: ${fmt(result.summary.element_count)} phần tử.`;
    }
    function clear() {
      state.request++; state.result = null; state.page = 0; state.tab = 'overview'; state.issueType = 'ALL';
      root.dataset.state = 'empty'; setRunning(false); render(); live.textContent = 'Chọn “Chạy QA” để đánh giá lưới hiện tại.';
    }
    function setStatus(status) {
      if (!state.running || state.stopping) return;
      const stage = text(status?.stage || status?.operation || 'Đang xử lý').replaceAll('_', ' ');
      const elapsed = Number(status?.elapsed_seconds ?? status?.elapsed);
      live.textContent = `Đang QA · ${stage}${Number.isFinite(elapsed) ? ` · ${fmt(elapsed)} s đã trôi qua` : ''}.`;
    }
    function destroy() { state.destroyed = true; state.request++; root.remove(); }
    return { load, clear, setRunning, setStatus, destroy, run, benchmark: runBenchmark };
  }

  return { SCHEMA, PAGE_SIZE, MAX_BINS, TYPES, METRICS, validateResult, safeJSON, histogramSVG, mount };
});
