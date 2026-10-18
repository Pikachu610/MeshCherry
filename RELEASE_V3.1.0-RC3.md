# MeshCherry 3.1.0 RC3

This source snapshot matches the reviewed desktop RC3 candidate. Stable v3.0.0
and its published assets are unchanged.

## Included

- Separate ROI, mesh, extraction and log workspaces; explicit single/multiple
  raster extraction modes and visible per-feature metadata validation.
- Sequential, bounded multi-raster extraction to SQLite; paged attribute table;
  wide/long CSV ZIP exports with metadata and JSON export.
- Native Save As with destination and transfer status, cancellation and atomic
  completion; protections against overwriting retained inputs and results.
- Validated optional Gmsh 2D algorithm, smoothing and relocation controls.
- Actionable failure guidance without needing a manifest to load; cancellation
  preserves the accepted mesh. Technical diagnostics remain available separately.

## Verification and limitations

The final failure/lifecycle regression selection ran 46 Python tests (one external
Gmsh integration test skipped); all 136 Node tests passed. Frozen source/resource
parity checked 59 entries. The final Windows EXE self-test passed, including real
external Gmsh, GeoTIFF/ASC, mesh-first extraction and batch extraction.

Independent ChatGPT source review accepted the scoped corrections. Native window
visual acceptance and clean-machine validation remain pending. Large-scale batch
throughput is not accepted: a 100-feature x 1,024-cell synthetic run exceeded its
approximately 313-second timeout while other build/test jobs were active.

The QA overlap rejection is retained. This update explains invalid geometry; it
does not repair the overlapping triangles observed in the earlier ROI4 run.

`.mcy` currently stores the accepted mesh and single-source results. Multi-raster
results are exported through ZIP/JSON rather than fully persisted as batch state.
No flood-prediction or GNN-accuracy improvement is claimed.
