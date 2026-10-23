# MeshCherry 3.1.0 RC4 — extraction table planning and comparison

## Features

- Review a paged table before extracting one or multiple rasters.
- Rename, reorder and remove raster columns; add typed manual columns/values.
- Select accepted cells for extraction; reinclude excluded cells.
- Keep supplementary manual rows distinct from geometry and GNN nodes.
- Validate the canonical plan against immutable parent mesh geometry before run.
- Stop extraction cooperatively, with authoritative stopping/terminal state and
  retry when a stop request is unconfirmed; retain completed raster transactions.
- Preview and export wide CSV+metadata ZIP, long CSV+metadata ZIP, or JSON with
  stable column IDs, annotations and explicit value/NoData/error states.
- Recover completed tables, including renamed/manual columns beyond column20.
- Compare accepted triangular cells with native raster pixels, a target square
  grid, or area-only resolution estimates. Report signed node differences,
  common-support reconstruction metrics, cell-size distribution and spatial bins.

## Evidence and limits

93 focused Python tests and158 full workbench Node tests passed. Independent
ChatGPT source/regression review accepted issues41 and42 after four iterations.
The new RC4 executable passed64 frozen-source/resource parity checks and its
self-test including real external Gmsh, draft previews and CSV/JSON exports.

Native Windows visual acceptance and large-field throughput are still pending.
Area-only estimates have no measured RMSE; native self-comparison errors are0
by definition. Spatial node density is distinct from measured relief alignment
or GNN prediction accuracy. The .mcy project format does not yet bundle the full
batch/draft database: export CSV+metadata or JSON to preserve those results.

This is an RC source update, not a replacement for stable3.0.0 release assets.
The existing MIT license is unchanged.
