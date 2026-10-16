# MeshCherry 2.9.1 candidate — Windows build and acceptance

The 2.9.1 source candidate repairs the source-audit regressions: job-control
responsiveness during preflight, request-specific main-run recovery, cancelled
comparison provenance, consecutive duplicate boundary vertices, exact reference
categorical IDs and stable reference/comparison arithmetic. See the versioned
source release notes for verified tests and remaining desktop acceptance limits.

The 2.9 candidate adds authenticated square/triangle comparison jobs and an
explicit SF-1 terrain sizing profile. Comparison has separate conservation and
spatial modal-agreement metrics for categorical rasters, signed cell-centred node
counts, fixed common support and an independent JSON report. SF-1 requires an
explicit scale, native 30 m square DEM in the analysis CRS and declared metre
elevations. Its profile recovery does not rerun historical budget calibration.
The frozen self-test exercises both features with actual external Gmsh.

## Managed background jobs in 2.8.0

The job monitor shows actual merged Gmsh stdout/stderr, phase progress and elapsed
monotonic time. Gmsh percentages are labelled observations from its log, not a
verified fraction of the complete phase or job. They never drive remaining-time
estimates. ETA is a phase-local range from multiple stable measured work counters
(for example, source raster traversal); insufficient, stale or changing observations
leave it unavailable. There is no invented global ETA.
The console is a bounded tail; the retained diagnostic log is capped at 16 MiB.

Stop requests cooperative cancellation, terminates the owned Windows Job Object
and waits for cleanup before unlocking input. The Windows child starts suspended,
is assigned to its Job Object, and only then resumes. Assignment/resume failure
terminates and reaps the owned process. A native GIS operation already in
progress may finish before its next checkpoint; the UI remains `Đang dừng` then.
Stop is not Pause/Resume. Interrupted jobs do not publish successful partial exports;
cancelled extraction preserves the previously accepted mesh. Successful publication
and Stop have one serialized commit boundary. The synchronous configuration check
is not a cancellable background job. Windows window/clean-machine acceptance is
still separate from backend tests and browser interaction.

## Gmsh preferences in 2.7.2

Desktop remembers the successfully checked executable path, historical version and
SHA-256 under `%LOCALAPPDATA%/MeshCherry/gmsh.json`, shared by releases for the
same Windows account. Opening a desktop workspace with legacy Gmsh metadata copies
it only when the user preference does not exist; the legacy file remains intact.
No executable runs during restoration/migration. A missing/replaced executable is
still checked before meshing; saved metadata does not mean current readiness.
CLI/debug-server defaults remain workspace-local. Failed explicit checks or failed
atomic writes cannot replace the last good preference. Filesystems without hard-link
support skip optional legacy migration; explicit Check & Save still works.

The current application version is `gis_mesh_workbench/_version.py`. Build from
the authored source using a new Python 3.12 environment and the pinned lock:

Run from the root of the source checkout. Use a new environment and unoccupied
dist/work directories; adjust their names if a previous candidate exists.

```powershell
py -3.12 -m venv .scratch/meshcherry-build-291
.scratch/meshcherry-build-291/Scripts/python.exe -m pip install -r GNN/mesh_research/packaging/requirements-v2.lock.txt
$env:PYTHONPATH = (Resolve-Path './GNN/mesh_research/src').Path
.scratch/meshcherry-build-291/Scripts/python.exe -m unittest discover -s GNN/mesh_research/tests -t GNN/mesh_research -p 'test_workbench*.py'
node --test GNN/mesh_research/tests/workbench_ui_state.test.cjs GNN/mesh_research/tests/workbench_recovery_regressions.test.cjs
.scratch/meshcherry-build-291/Scripts/python.exe -m PyInstaller GNN/mesh_research/packaging/MeshCherry.spec --distpath MeshCherry/MeshCherry-Desktop-v2.9.1-rc1 --workpath .scratch/meshcherry-build-v291-rc1
```

The workbench tests are the application suite. The full `test_*.py` discovery in
the research workspace additionally exercises historical research data and the
separately installed reference Gmsh; those private datasets are not distributed.
Skipped external-dependency tests must be reported explicitly.

The spec embeds deterministic authored-source and build metadata. Run manifests,
bootstrap and frozen self-test report the same application identity, source-manifest
digest, lock digest and build runtime versions. A clean build environment is not
evidence of clean-machine execution or native-window visual acceptance.

Current supported domains are raster outer affine footprints, projected rectangles,
and complete Polygon SHP bundles (including holes/multipolygons). Mesh-only runs
precede optional extraction on immutable saved cells. Config v2 supports up to
100,000 generated/imported/frozen cells; structured grids and legacy configs retain
1,000. Above 2,000 cells the SVG uses batched paths and coordinate picking up to
the 100,000-cell cap; complete cells/mesh/attributes remain available through exports. The PNG
preview uses all cells and is measured separately by `benchmark_display.py`.

Raster identity includes the main file and recognized same-dataset companions;
addition, removal or byte changes invalidate the run. GeoTIFF and ESRI ASCII Grid
are supported; ASC needs a declared source CRS, normally its same-name `.prj`.
ROI, DEM, optional resolution reference and extraction raster have independent roles.

Preserve every previous release directory. Do not reuse an occupied dist/build
directory for an acceptance candidate. Full package/source parity, exact EXE smoke,
native Windows interaction and clean-account/machine tests gate publication.

## Desktop operation and diagnostics

Build on Windows x64 using Python 3.12, the project GIS dependencies, pywebview 6.2.1 and PyInstaller 6.22.3. WebView2 Evergreen Runtime is required on the target machine. Python is bundled; no SecondPro development paths are required.

```powershell
python -m PyInstaller GNN/mesh_research/packaging/MeshCherry.spec --distpath dist --workpath build
```

Distribute the entire `MeshCherry/` directory, not the EXE alone. Keep `_internal/` next to `MeshCherry.exe`. Use a writable extracted directory; the default workspace is `workspace/` next to the executable. Desktop selection uses Windows common file/folder dialogs: a selected external GeoTIFF or mesh JSON grants read access only to that input; selecting a result folder grants access to that folder for this session. Cancellation preserves the current selection and preflight. No arbitrary parent input directory or drive is opened for browsing. Sample data embedded in `_internal` are read-only. The browser/debug UI retains its restricted picker.

In **Thiết lập Gmsh**, click **Chọn gmsh.exe…**, select the executable, then **Kiểm tra phiên bản & lưu**. A successful check displays the detected version, readiness and saved-path confirmation. Empty/invalid selections display an error, never a success badge. This confirms the version probe, not vendor authenticity or all Gmsh functions. The native selector uses a narrow authenticated loopback endpoint and STA common-dialog invocation, not a general JS/Python bridge. Native dialog appearance still requires manual Windows acceptance.

Run frozen diagnostics:

```powershell
$p = Start-Process .\dist\MeshCherry\MeshCherry.exe -ArgumentList '--self-test',"$PWD\smoke.json" -Wait -PassThru
$p.ExitCode
Get-Content smoke.json
```

This checks packaged resources, font HTTP routes, CLR import, true raster-to-grid extraction (10/30) and triangle extraction (25/15). It does not certify visual interaction, clean-machine compatibility or hydrologic accuracy.

Gmsh is deliberately not bundled. The new `gmsh_generated` path calls a user-selected external Gmsh executable directly and works in the frozen build. The older fixed `gmsh_reference` research preset remains a separate source workflow and is not supported by this portable build. There is no silent algorithm fallback. The interface accepts Gmsh 4.x; actual integration tests exercise 4.15.2, not every 4.x version. Version and SHA-256 checks record provenance/stability, not vendor authenticity; obtain the executable from gmsh.info.

The external-Gmsh workflow supports projected-metre raster/rectangle/SHP domains,
uniform or exploratory 33 x 33 local-relief sizing. It exports `mesh.msh` and
normalized `mesh.json`; extraction is a subsequent optional operation. The relief
field is not the calibrated SF-1 field or a validated routing/GNN model.

The frozen self-test can also exercise external Gmsh:

```powershell
$p = Start-Process .\dist\MeshCherry\MeshCherry.exe -ArgumentList '--self-test',"$PWD\gmsh-smoke.json",'--gmsh-test','D:\Tools\gmsh\gmsh.exe' -WindowStyle Hidden -Wait -PassThru
$p.ExitCode
Get-Content gmsh-smoke.json
```

This remains a backend smoke, not native-window interaction or clean-machine acceptance. The portable binary is unsigned; do not disable Windows security protections to run it.

Third-party license notices are retained in the packaged distribution metadata and font OFL file. The application runs a token-authenticated loopback server inside its native WebView2 window, not an externally hosted site. No new JS/Python bridge is introduced.
