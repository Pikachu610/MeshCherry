# MeshCherry 3.2.0rc1 — source checkpoint before long-term audit

This checkpoint preserves the existing QA implementation before a full
read-only performance, architecture and UI assessment. The stable 3.0.0
release and MIT license are unchanged. No new stable release is declared.

## Added capabilities

- On-demand geometry/topology/overlap assessment over immutable accepted meshes.
- Triangle Q, angle and aspect metrics; statistics, histograms and issue tables.
- Retained original ROI and verified target-field diagnostics when available.
- Bounded SVG heatmaps, cell focus and configurable warning thresholds.
- Cancellable background QA and atomic report publication.
- JSON, CSV bundle and offline HTML reports, plus optional `.mcy` QA cache.
- Explicit repeated geometry-only QA benchmark; sampled process-tree RSS with psutil.

## Evidence and limits

59 focused QA Python tests and 166 Node tests passed. Frozen parity checked
73 Python/resource components with zero differences, and the Windows EXE
self-test with real Gmsh passed. A synthetic 1,250-triangle, five-run benchmark
reported median QA 0.808 seconds and sampled process-tree RSS peak 76,955,648
bytes on the development machine; this is not a large-basin performance claim.

The final independent ChatGPT review was interrupted by source-access failure.
Native Windows visual acceptance and large real-mesh throughput remain unverified.
The forthcoming audit must measure production read/export/map paths as well as
the evaluator, not extrapolate from the synthetic benchmark.

Original node tags are unavailable in cell-coordinate imports. Unverified SF-1
Structured field interpolation is reported unavailable. Restored cache metrics
are historical file data, not independently authenticated calculations.

Definitions and supported scope: [Mesh quality guide](GNN/mesh_research/docs/MESH_QUALITY.md).
