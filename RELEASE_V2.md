# Release v2.0.0

- Windows x64 portable desktop, native WebView2 window, Python included.
- Be Vietnam Pro offline, consolidated typography and corner scale, tutorial.
- Removed "Bé" from shipped interface.
- Frozen workspace follows the EXE, not a development source path.
- Gmsh intentionally absent from binary and rejected explicitly.
- No personal DEM, project outputs, credentials or C2C integration bundled.

## Validation

SecondPro full suite before final packaging harness additions: 97 discovered, 96 passed, 1 Windows permission skip.
Exported source workbench suite: 56 discovered, 53 passed, 3 skips (external Gmsh class and Windows permission cases). Gmsh is not included in the export; this is not evidence that Gmsh worked in the binary.
Frozen EXE self-test passed local HTML/JS/CSS/font serving, CLR import, grid [10,30], triangles [25,15]. Native process reported a responding MeshCherry window on the build machine. Visual native end-to-end interaction and clean-machine testing remain unverified.

## Archive integrity

MeshCherry-v2.0.0-windows-x64.zip: 76,494,990 bytes.
SHA256: 80651cbb8bc9ad264c903bd6e2f883a18910c0efb11ade5582146de019cd8221

MeshCherry.exe SHA256: 57e70c7b6df3f2921a46d1816ffe0857e96ad3b51c1747a831aab4881a6b7d17

The full extracted folder is required. Unsigned binary; WebView2 Runtime remains a target-machine prerequisite. Do not disable operating-system security checks.
