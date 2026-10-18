"""Bounded, dependency-free exports for mesh quality assessment snapshots."""
from __future__ import annotations

import csv
import html
import io
import json
import math
import zipfile

SCHEMA_VERSION = "meshcherry-quality-v1"
ELEMENT_COLUMNS = (
    "element_id", "validity_status", "area", "perimeter", "min_angle_deg",
    "max_angle_deg", "aspect_ratio", "quality_q", "longest_edge",
    "shortest_edge", "target_size", "size_ratio", "issue_codes",
)
MAX_EXPORT_BYTES = 64 * 1024 * 1024
MAX_HTML_ROWS = 1000


def _finite_json(value):
    """Normalize a report tree to strict JSON, replacing nonfinite numbers with null."""
    if isinstance(value, dict):
        return {str(key): _finite_json(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_json(child) for child in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        return _finite_json(value.item())
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _validated_result(result):
    if not isinstance(result, dict):
        raise ValueError("Quality result must be an object")
    value = _finite_json(result)
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported quality result schema")
    if not isinstance(value.get("elements"), list) or not isinstance(value.get("issues"), list):
        raise ValueError("Quality result must include element and issue arrays")
    return value


def _json_bytes(result):
    value = _validated_result(result)
    data = (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                       indent=2) + "\n").encode("utf-8")
    if len(data) > MAX_EXPORT_BYTES:
        raise ValueError("Quality JSON exceeds the 64 MiB export limit")
    return data


def _csv_bytes(headers, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(headers)
    def safe(value):
        if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
            return "'" + value
        return value
    writer.writerows([safe(value) for value in row] for row in rows)
    return stream.getvalue().encode("utf-8-sig")


def _element_csv(result):
    rows = []
    for element in result["elements"]:
        if not isinstance(element, dict):
            continue
        values = dict(element)
        values["validity_status"] = "VALID" if values.get("valid") is True else (
            "INVALID" if values.get("valid") is False else "NOT_AVAILABLE")
        values["issue_codes"] = ";".join(map(str, values.get("issue_codes", [])))
        rows.append([values.get(name) for name in ELEMENT_COLUMNS])
    return _csv_bytes(ELEMENT_COLUMNS, rows)


def _summary_rows(result):
    rows = []
    summary = result.get("summary", {})
    for key, value in sorted(summary.items()):
        rows.append(("summary", key, "AVAILABLE" if value is not None else "NOT_AVAILABLE",
                     json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)))
    for section in ("statistics", "validation", "boundary", "size", "performance"):
        value = result.get(section)
        if value is not None:
            rows.append(("section", section, "AVAILABLE", json.dumps(
                value, ensure_ascii=False, allow_nan=False, sort_keys=True)))
    return rows


def _issues_csv(result):
    headers = ("code", "severity", "category", "message", "element_ids", "node_ids",
               "location", "details")
    rows = []
    for issue in result["issues"]:
        if not isinstance(issue, dict):
            continue
        rows.append([issue.get("code"), issue.get("severity"), issue.get("category"),
                     issue.get("message"), json.dumps(issue.get("element_ids", []), ensure_ascii=False),
                     json.dumps(issue.get("node_ids", []), ensure_ascii=False),
                     json.dumps(issue.get("location"), ensure_ascii=False, allow_nan=False),
                     json.dumps(issue.get("details", {}), ensure_ascii=False, allow_nan=False,
                                sort_keys=True)])
    return _csv_bytes(headers, rows)


def _csv_bundle(result):
    files = {
        "elements.csv": _element_csv(result),
        "summary.csv": _csv_bytes(("section", "metric", "status", "value"), _summary_rows(result)),
        "issues.csv": _issues_csv(result),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    encoded = output.getvalue()
    if len(encoded) > MAX_EXPORT_BYTES:
        raise ValueError("Quality CSV bundle exceeds the 64 MiB export limit")
    return encoded


def _svg_histogram(name, histogram):
    if not isinstance(histogram, dict) or histogram.get("status") != "AVAILABLE":
        reason = html.escape(str((histogram or {}).get("reason", "No observations")))
        return f"<p class=muted>{html.escape(name)}: {reason}</p>"
    counts = histogram.get("counts", [])
    edges = histogram.get("edges", [])
    if len(edges) != len(counts) + 1 or not counts:
        return f"<p class=muted>{html.escape(name)}: histogram unavailable</p>"
    maximum = max(counts) or 1
    width, height, gap = 640, 180, 2
    bar_width = max(1, (width - gap * len(counts)) / len(counts))
    bars = []
    for index, count in enumerate(counts):
        x = index * (bar_width + gap)
        bar_height = (height - 24) * count / maximum
        label = f"{edges[index]}–{edges[index + 1]}"
        bars.append(f'<rect x="{x:.2f}" y="{height - 20 - bar_height:.2f}" '
                    f'width="{bar_width:.2f}" height="{bar_height:.2f}" '
                    f'title="{html.escape(label)}: {count}"/>')
    return (f"<figure><figcaption>{html.escape(name)} · valid {histogram.get('valid_count', 0)}, "
            f"excluded {histogram.get('excluded_count', 0)}</figcaption>"
            f'<svg role="img" aria-label="{html.escape(name)} histogram" '
            f'viewBox="0 0 {width} {height}">{"".join(bars)}</svg></figure>')


def _html_report(result):
    data = _validated_result(result)
    title = "Mesh quality report"
    identity = data.get("mesh_hash") or "NOT_AVAILABLE"
    summary = data.get("summary", {})
    statistics = data.get("statistics", {})
    definitions = data.get("definitions", {})
    worst = data.get("worst", {})
    worst_rows = worst if isinstance(worst, list) else worst.get("lowest_quality_q", worst.get("elements", [])) if isinstance(worst, dict) else []
    issue_rows = data["issues"]
    rows = []
    for item in worst_rows[:MAX_HTML_ROWS]:
        if isinstance(item, dict):
            rows.append("<tr>" + "".join(f"<td>{html.escape(str(item.get(key, '')))}</td>"
                                         for key in ("element_id", "quality_q", "min_angle_deg", "aspect_ratio", "valid")) + "</tr>")
    issue_html = []
    for item in issue_rows[:MAX_HTML_ROWS]:
        if isinstance(item, dict):
            ids = ", ".join(map(str, item.get("element_ids", [])))
            issue_html.append("<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in
                (item.get("code", ""), item.get("severity", ""), ids, item.get("message", ""))) + "</tr>")
    charts = "".join(_svg_histogram(key, value) for key, value in data.get("histograms", {}).items())
    stat_rows = []
    for name, record in sorted(statistics.items()):
        if isinstance(record, dict):
            stat_rows.append("<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in
                (name, record.get("status", ""), record.get("valid_count", ""),
                 record.get("excluded_count", ""), record.get("min", ""),
                 record.get("mean", ""), record.get("median", ""), record.get("p95", ""),
                 record.get("max", ""))) + "</tr>")
    def pretty(value):
        return html.escape(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2))
    # JSON embedded in a script element must escape '<' to prevent a data string
    # containing </script> from terminating the element.
    embedded = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")
    document = f'''<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{font:15px system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#172033;background:#fff}}
h1,h2{{color:#102c46}}table{{border-collapse:collapse;width:100%;margin:1rem 0}}th,td{{border:1px solid #ccd5df;padding:.4rem;text-align:left;vertical-align:top}}th{{background:#edf3f8}}
.muted{{color:#5b6570}}.status{{font-weight:700}}figure{{display:inline-block;width:min(100%,650px);margin:1rem .7rem 1rem 0}}svg{{width:100%;height:auto;fill:#477ca6;background:#f3f6f8}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7f9;padding:.8rem}}
</style><h1>{title}</h1><p class="status">Status: {html.escape(str(data.get('status', 'NOT_AVAILABLE')))}</p>
<h2>Project and mesh identity</h2><p>Mesh hash: <code>{html.escape(str(identity))}</code></p><p>Config hash: <code>{html.escape(str(data.get('config_hash', 'NOT_AVAILABLE')))}</code></p>
<h2>Summary</h2><pre>{pretty(summary)}</pre><h2>Validation</h2><pre>{pretty(data.get('validation', {}))}</pre>
<h2>Statistics and charts</h2><table><thead><tr><th>Metric</th><th>Status</th><th>Valid</th><th>Excluded</th><th>Min</th><th>Mean</th><th>Median</th><th>P95</th><th>Max</th></tr></thead><tbody>{''.join(stat_rows)}</tbody></table>{charts}
<h2>Worst elements</h2><table><thead><tr><th>Element ID</th><th>Q</th><th>Min angle (deg)</th><th>Aspect ratio</th><th>Valid</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>Issues</h2><table><thead><tr><th>Code</th><th>Severity</th><th>Element IDs</th><th>Reason</th></tr></thead><tbody>{''.join(issue_html)}</tbody></table>
<h2>Boundary fidelity</h2><pre>{pretty(data.get('boundary', {}))}</pre><h2>Size conformity</h2><pre>{pretty(data.get('size', {}))}</pre>
<h2>Runtime and memory</h2><pre>{pretty(data.get('performance', {}))}</pre><h2>Metric definitions and limitations</h2><pre>{pretty(definitions)}</pre>
<p class="muted">This report describes mesh geometry and configured checks. It does not establish physical-solver accuracy.</p>
<script type="application/json" id="quality-data">{embedded}</script></html>'''
    encoded = document.encode("utf-8")
    if len(encoded) > MAX_EXPORT_BYTES:
        raise ValueError("Quality HTML report exceeds the 64 MiB export limit")
    return encoded


def export_quality(result, format):
    """Return report bytes, MIME type and a safe suggested filename."""
    value = _validated_result(result)
    if format == "json":
        return _json_bytes(value), "application/json; charset=utf-8", "mesh-quality.json"
    if format == "csv":
        return _csv_bundle(value), "application/zip", "mesh-quality-csv.zip"
    if format == "html":
        return _html_report(value), "text/html; charset=utf-8", "mesh-quality.html"
    raise ValueError("Quality export format must be json, csv, or html")

