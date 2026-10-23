# MeshCherry 2.9.1 — source candidate

This source update includes the mesh-first work developed since v2.0.0 and the
seven confirmed regressions repaired after the 2026-10-16 source audit.

## Repairs

- Run expensive preflight operations without holding the Stop/progress lock;
  validate concurrent state before committing preflight results.
- Recover a main run by its own stable request identity, including a completed
  run whose initial HTTP response was lost; reject cross-operation adoption.
- Preserve structured comparison provenance and cancellation stage in diagnostic
  manifests when publication is cancelled.
- Reject consecutive duplicate boundary vertices before invoking Gmsh and report
  record/ring/vertex location. Source coordinates are not silently removed.
- Preserve large integer category IDs in the reference extractor.
- Use stable arithmetic for representable reference means/std and comparison RMSE
  when direct intermediate sums or squared differences would overflow.

## Verification

- Research workspace Python suite: **310 tests run, 308 passed, 2 skipped**.
- Latest JavaScript UI/recovery suite: **67/67 passed**, including real stage
  guards and startup recovery with stale (404) or temporarily unavailable (503)
  accepted-mesh artifacts.
- Standalone exported application suite: **222 tests run, 206 passed, 16
  skipped**. Optional external-Gmsh/system-privilege checks remain explicit skips;
  the repository does not contain the external executable or private ROI data.
- A further **28/28 tests passed** after making the SHP isolation fixture and
  three Gmsh integration-test paths checkout-relative. Real Gmsh in the research
  workspace was exercised without redistributing its binary.
- The numeric regression/extraction/comparison group passed **46/46**. Boundary
  regression/integration tests passed **21/21**; lifecycle API regressions **6/6**.

Read-only ChatGPT critique was applied separately to boundary validation,
arithmetic and job/UI lifecycle, with regression tests added for findings from
successive reviews. Counts describe distinct suites, not additive independent
test totals. JavaScript tests use a controlled DOM; they do not certify native
WebView2 rendering. The full-workspace run preceded the final JS-only recovery
corrections, which were verified with the final complete JavaScript suite.

The exported source omits personal data, Python environments, generated run
folders and vendor executables. The user's existing MIT LICENSE is preserved.

## Distribution boundary

This commit is an authored-source update, not a newly published Windows binary.
Existing release assets and version folders are preserved. Native-window visual
acceptance and clean-machine compatibility remain separate from backend tests,
JavaScript logic tests and frozen self-tests. Gmsh is installed externally; its
binary is not redistributed. Phase 1 routing and personal GIS data are excluded.
