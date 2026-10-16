# Mesh research pipeline

This package implements the isolated mesh research track. Its current fixture adapter exists to lock the configuration, artifact and provenance contracts before external mesh generators are introduced.

## Run the fixture

From this directory:

```powershell
$env:PYTHONPATH = "src"
python -m mesh_research run configs/fixture-smoke.json
```

Or install the package in an environment and use the console command:

```powershell
python -m pip install -e .
mesh-research run configs/fixture-smoke.json
```

Generated runs are written outside the source package. Each run contains a manifest plus `mesh`, `graph` and `qa` artifacts. The fixture does not implement routing or claim hydrological validity.

## Run the Gmsh inclined plane

The repository-local Gmsh binary remains an external research dependency; it is invoked in place and is not copied into this package.

```powershell
$env:PYTHONPATH = "src"
python -m mesh_research run configs/gmsh-inclined-plane-250m.json
```

The computational geometry is planar. Synthetic elevation is attached analytically after meshing, preserving the distinction between 2D mesh geometry and terrain attributes. The target edge length is applied both as a Gmsh size constraint and as an explicit boundary subdivision count; the surface itself remains Delaunay meshed rather than transfinite. See the [Gmsh mesh-size documentation](https://gmsh.info/doc/texinfo/#Specifying-mesh-element-sizes).

## Run the synthetic refinement matrix

```powershell
$env:PYTHONPATH = "src"
python -m mesh_research run configs/gmsh-synthetic-matrix.json
```

The matrix contains an inclined plane, V-shaped valley, radial bowl and Y-shaped drainage surface at 500 m, 250 m and 125 m target sizes. Each child run retains its own manifest and artifacts. The parent summary keeps failed cases visible and reports realized cell/interface counts, quality statistics, runtime and peak RSS.

## Run the semantic raster-remapping example

```powershell
$env:PYTHONPATH = "src"
python -m mesh_research run configs/gmsh-semantic-remapping.json
```

This run maps three independent synthetic rasters onto the triangular cells: an intensive rainfall field, an extensive water-volume field and categorical land cover. Intensive values use covered-area means, extensive values are allocated conservatively, and categorical values retain class fractions plus a deterministic majority class. NoData remains missing and reduces the reported cell coverage rather than becoming zero. Each dataset is also transferred back to its source raster grid using overlap weights for comparison.

The run writes one artifact per dataset under `remapping/`. Its `qa/qa.json` records cell coverage and, for extensive quantities, the source total, mesh total, reconstructed-raster total, numerical residual and configured tolerance. The retained example figure is `docs/figures/semantic-raster-remapping.png`; it is synthetic preprocessing evidence, not ROI4 or routing validation.

## Run the ROI4 resolution sweep

```powershell
$env:PYTHONPATH = "src"
python -m mesh_research run configs/gmsh-roi4-resolution-sweep.json
```

The sweep runs nominal 250 m, 500 m and 1000 m cases using the same canonical ROI4 hashes and derived-boundary policy. Its `roi4-resolution-sweep.json` compares realized graph size, mesh quality, boundary evidence, Gmsh wall time and peak RSS. Field-raster coverage and conservation are reported only when an audited input is demonstrably derived for canonical ROI4 and supported by the remapper; otherwise the evidence state is explicitly `not_available`. The sweep does not validate watershed status, outlet location or hydrological routing.

## Test

### MeshCherry large-mesh viewer

The result viewer retains full cell geometry and attributes up to the 100,000-cell
mesh safety limit. At more than 2,000 cells it uses batched SVG paths and one
selection overlay instead of individual per-cell DOM nodes. Fit, pan, zoom,
coverage colors, cell picking, ID lookup and keyboard selection remain available.
This display policy does not change the mesh, scientific values or exported files.
The 2,000-cell threshold selects a renderer; it is not a mesh visibility limit.

```powershell
python -m unittest discover -s tests -v
```

## Run the SF-1 DEM-only adaptive study

Use the GIS environment because this optional research backend reads real GeoTIFFs:

```powershell
$env:PYTHONPATH = "src"
.venv-gis\Scripts\python.exe -m mesh_research run configs/gmsh-roi4-dem-adaptive-sf1.json
```

The study first reproduces the uniform 500 m ROI4 reference, then calibrates three DEM-only ablations and a conditional 3x3 sensitivity matrix to the same realized cell budget. It retains all finite calibration attempts, exact-overlap terrain errors, the actual Gmsh control field, cell-centred graph artifacts and an inspectable node/adjacency figure. `winner` and `no_winner` are both valid outcomes. Neither outcome validates routing, a watershed divide, an outlet or GNN performance.
