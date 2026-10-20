# MeshCherry 3.2.0rc5 — source checkpoint

This updates source only; it does not replace the existing stable Windows release.

## Included

- Compact ROI form with visible CRS, pixel X/Y resolution, dimensions, Band and
  NoData. Preview remains visible; recent history and advanced diagnostics no
  longer precede the primary action.
- Independent DEM preview/control columns; h_min, h_max and relief controls use
  the available left-hand space instead of following an oversized shared row.
- Offline Source Sans 3, Vietnamese glyph coverage and bundled OFL license;
  shared dark-navy text including QA controls.
- Direct Open/Save commands in File, with one workspace order:
  ROI → Mesh → Compare → Extract → Log.
- Structured/unstructured comparison uses full measured counts, signed deltas
  and percentages separately from bounded geometry previews. The optional source
  raster preview retains its own source CRS and extent.
- Safer comparison Stop/navigation, modal guards, stale remesh-restore response
  rejection, batch result identity and document-operation lifecycle guards.
- Compact QA statistics and omitted unavailable metric cards.

## Evidence and limitations

The source checkpoint receives targeted Node/UI, Python HTTP/font and browser
checks, followed by independent ChatGPT source review before push. Exact results
are recorded in the workspace review evidence, not inferred from build success.

No mesh/extraction algorithm is changed by the compact-layout pass. Accepted
geometry, IDs, source hashes and existing project compatibility remain guarded.
The larger-capacity QA performance work remains deferred (Issue 45). This is not
an LTS certification or native Windows visual acceptance. Existing downloadable
binaries do not acquire these source changes automatically.
