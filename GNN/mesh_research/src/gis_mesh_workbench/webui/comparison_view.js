(() => {
  'use strict';

  const MAX_FEATURES = 1200;
  const MAX_SOURCE_PNG_LENGTH = 4 * 1024 * 1024;
  const redrawStates = new WeakMap();
  const number = value => Number.isSafeInteger(value)
    ? value.toLocaleString('vi-VN')
    : '—';

  function polygonRings(geometry) {
    if (!geometry || !Array.isArray(geometry.coordinates)) return [];
    if (geometry.type === 'Polygon') return [geometry.coordinates];
    if (geometry.type === 'MultiPolygon') return geometry.coordinates;
    return [];
  }

  function drawGeometry(ctx, geometry, transform, fill, stroke, width) {
    for (const polygon of polygonRings(geometry)) {
      if (!Array.isArray(polygon)) continue;
      ctx.beginPath();
      for (const ring of polygon) {
        if (!Array.isArray(ring) || ring.length < 4) continue;
        const first = transform(ring[0]);
        ctx.moveTo(first[0], first[1]);
        for (let i = 1; i < ring.length; i++) {
          const point = transform(ring[i]);
          ctx.lineTo(point[0], point[1]);
        }
        ctx.closePath();
      }
      if (fill) {
        ctx.fillStyle = fill;
        ctx.fill('evenodd');
      }
      if (stroke) {
        ctx.strokeStyle = stroke;
        ctx.lineWidth = width;
        ctx.stroke();
      }
    }
  }

  function drawPanel(canvas, view, layers) {
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    const rect = canvas.getBoundingClientRect();
    if (!(rect.width > 0 && rect.height > 0)) return false;
    const width = Math.floor(rect.width * ratio);
    const height = Math.floor(rect.height * ratio);
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return false;
    ctx.clearRect(0, 0, width, height);
    const bounds = view.bounds;
    if (!Array.isArray(bounds) || bounds.length !== 4) return true;
    const [xmin, ymin, xmax, ymax] = bounds.map(Number);
    if (![xmin, ymin, xmax, ymax].every(Number.isFinite) || xmax <= xmin || ymax <= ymin) return true;
    const pad = 10 * ratio;
    const scale = Math.min((width - 2 * pad) / (xmax - xmin), (height - 2 * pad) / (ymax - ymin));
    const map = ([x, y]) => [pad + (x - xmin) * scale, height - pad - (y - ymin) * scale];

    drawGeometry(ctx, view.roi, map, '#f7f8f6', '#65736c', 1.2 * ratio);
    for (const layer of layers) {
      const geometries = Array.isArray(layer.data?.geometries)
        ? layer.data.geometries.slice(0, MAX_FEATURES) : [];
      for (const geometry of geometries) {
        drawGeometry(ctx, geometry, map, layer.fill, layer.stroke, layer.lineWidth * ratio);
      }
    }
    return true;
  }

  function dispose(container) {
    const previous = redrawStates.get(container);
    if (previous?.observer) previous.observer.disconnect();
    if (previous?.frameId !== undefined && typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(previous.frameId);
    }
    for (const url of previous?.objectUrls || []) URL.revokeObjectURL(url);
    redrawStates.delete(container);
  }

  function sourcePngBlob(data) {
    const prefix = 'data:image/png;base64,';
    if (typeof data !== 'string' || !data.startsWith(prefix) || data.length > MAX_SOURCE_PNG_LENGTH
        || !/^[A-Za-z0-9+/]*={0,2}$/.test(data.slice(prefix.length))) {
      throw new Error('PNG source preview is invalid or exceeds the size limit.');
    }
    const bytes = Uint8Array.from(atob(data.slice(prefix.length)), character => character.charCodeAt(0));
    const signature = [137, 80, 78, 71, 13, 10, 26, 10];
    if (bytes.length < signature.length || signature.some((byte, index) => bytes[index] !== byte)) {
      throw new Error('PNG source preview signature is invalid.');
    }
    return new Blob([bytes], { type: 'image/png' });
  }

  function card(title, note, className) {
    const section = document.createElement('section');
    section.className = `comparison-visual-card ${className || ''}`.trim();
    const heading = document.createElement('h3');
    heading.textContent = title;
    const canvas = document.createElement('canvas');
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', title);
    const caption = document.createElement('p');
    caption.className = 'comparison-visual-note';
    caption.textContent = note;
    section.append(heading, canvas, caption);
    return { section, canvas, caption };
  }

  function render(container, report) {
    if (!container || !report || typeof report !== 'object') return;
    dispose(container);
    const resources = { observer: null, frameId: undefined, objectUrls: [] };
    container.replaceChildren();
    const estimated = report.baseline?.mode === 'resolution_estimate';
    const structured = report.structured || report.square_grid || {};
    const unstructured = report.unstructured || report.mesh || {};
    const structuredCount = structured.node_count ?? structured.cell_count;
    const unstructuredCount = unstructured.node_count ?? unstructured.cell_count;
    const metrics = document.createElement('div');
    metrics.className = 'comparison-visual-metrics';
    const values = [
      [`Lưới có cấu trúc · phần tử${estimated ? ' (ước tính)' : ''}`, number(structuredCount)],
      ['Lưới tam giác · phần tử', number(unstructuredCount)],
      ['Chênh lệch · tam giác − mốc', Number.isSafeInteger(report.graph_node_delta)
        ? `${report.graph_node_delta > 0 ? '+' : ''}${number(report.graph_node_delta)}` : '—'],
      [report.node_reduction_percent < 0 ? 'Tăng phần tử so với mốc' : 'Giảm phần tử so với mốc', Number.isFinite(report.node_reduction_percent)
        ? `${Math.abs(report.node_reduction_percent).toLocaleString('vi-VN', { maximumFractionDigits: 2 })}%` : '—'],
    ];
    for (const [label, value] of values) {
      const item = document.createElement('div');
      item.className = 'comparison-visual-metric';
      const strong = document.createElement('strong');
      strong.textContent = value;
      const caption = document.createElement('span');
      caption.textContent = label;
      item.append(strong, caption);
      metrics.append(item);
    }
    const vertexCount = report.geometry_counts?.unstructured_unique_vertex_count;
    const vertexNote = document.createElement('p');
    vertexNote.className = 'comparison-vertex-count';
    vertexNote.textContent = 'Graph lấy tâm ô: 1 phần tử = 1 node.' + (Number.isSafeInteger(vertexCount) ? ` Đỉnh hình học của lưới tam giác: ${number(vertexCount)}.` : '');
    container.append(metrics, vertexNote);

    if (estimated) {
      const note = document.createElement('p');
      note.textContent = 'Ước tính theo diện tích và độ phân giải khai báo. Chọn raster đối chiếu để xem hình hai lưới và đo sai số.';
      container.append(note); return;
    }

    const view = report.visualization;
    const grid = document.createElement('div');
    grid.className = 'comparison-visual-grid';
    let sourceReason = view?.source?.reason || 'Chưa có ảnh raster tham chiếu để hiển thị.';
    const source = card('Raster nguồn', sourceReason, 'is-unavailable');
    const sourceImage = view?.source?.image;
    let sourceUrl = null;
    if (sourceImage !== undefined) {
      try {
        sourceUrl = URL.createObjectURL(sourcePngBlob(sourceImage));
        resources.objectUrls.push(sourceUrl);
      } catch (error) {
        sourceReason = error.message || 'Ảnh raster nguồn không hợp lệ.';
      }
    }
    if (sourceUrl) {
      const image = document.createElement('img');
      image.className = 'comparison-source-image';
      image.alt = 'Raster nguồn, ảnh xem trước độc lập theo CRS và extent của raster';
      image.src = sourceUrl;
      source.canvas.hidden = true;
      source.section.classList.remove('is-unavailable');
      source.section.insertBefore(image, source.caption);
      const metadata = [
        view.source.crs ? `CRS nguồn: ${view.source.crs}` : 'CRS nguồn chưa xác định',
        Array.isArray(view.source.bounds) ? `Extent nguồn: ${view.source.bounds.join(', ')}` : '',
        view.source.sampled ? 'Ảnh raster được lấy mẫu nearest-neighbour để hiển thị.' : 'Ảnh raster ở độ phân giải nguồn.',
      ].filter(Boolean).join(' · ');
      source.caption.textContent = metadata + ' · Khung này dùng CRS/extent riêng, không dùng chung thu phóng với lưới.';
    } else {
      source.canvas.hidden = true;
      source.caption.textContent = sourceReason;
    }
    grid.append(source.section);

    const roiNote = view?.roi_available === false
      ? `${view.roi_reason || 'Đường bao ROI bị lược khỏi ảnh xem trước do giới hạn kích thước.'} ` : '';
    const commonNote = `${roiNote}Hình học được đơn giản hóa để hiển thị và lấy mẫu đều khi số ô lớn; đây là hình xem trước. Số liệu đầy đủ nằm ở các chỉ số phía trên.`;
    const redraw = [];
    for (const [key, title, fill, stroke] of [
      ['structured', 'Lưới có cấu trúc', 'rgba(36, 117, 164, .16)', '#2475a4'],
      ['unstructured', 'Lưới tam giác', 'rgba(191, 112, 44, .14)', '#bf702c'],
    ]) {
      const data = view?.[key];
      const display = card(title, data?.available
        ? `${commonNote}${data.sampled ? ` Đang hiển thị ${number(data.feature_count)} trong ${number(data.total_cell_count)} ô.` : ''}`
        : (data?.reason || 'Không có hình học cho chế độ so sánh này.'), data?.available ? '' : 'is-unavailable');
      if (data?.available && view?.bounds) {
        redraw.push(() => drawPanel(display.canvas, view, [{ data, fill, stroke, lineWidth: .75 }]));
      } else display.canvas.hidden = true;
      grid.append(display.section);
    }

    const overlayData = view && view.structured?.available && view.unstructured?.available;
    const overlay = card('Căn chỉnh chung · cùng mức thu phóng', overlayData
      ? `Hai lớp dùng cùng ROI và tỷ lệ không gian. ${commonNote}`
      : 'Lớp chồng không khả dụng vì thiếu một trong hai hình học.', overlayData ? '' : 'is-unavailable');
    if (overlayData) {
      redraw.push(() => drawPanel(overlay.canvas, view, [
        { data: view.structured, fill: 'rgba(36, 117, 164, .10)', stroke: '#2475a4', lineWidth: .8 },
        { data: view.unstructured, fill: 'rgba(191, 112, 44, .06)', stroke: '#bf702c', lineWidth: .8 },
      ]));
    } else overlay.canvas.hidden = true;
    grid.append(overlay.section);
    container.append(grid);

    // The cards must be connected and visible before canvas dimensions can be
    // measured. Keep observing so hidden workspaces redraw when shown/resized.
    const redrawVisible = () => redraw.forEach(draw => draw());
    let observer = null;
    if (typeof ResizeObserver === 'function') {
      resources.observer = new ResizeObserver(redrawVisible);
      resources.observer.observe(grid);
    }
    redrawStates.set(container, resources);
    if (typeof requestAnimationFrame === 'function') resources.frameId = requestAnimationFrame(redrawVisible);
    else redrawVisible();
  }

  function clear(container) {
    if (!container) return;
    dispose(container);
    container.replaceChildren();
  }

  window.ComparisonView = Object.freeze({ render, clear });
})();
