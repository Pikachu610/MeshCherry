# MeshCherry v2 portable Windows build

Build on Windows x64 using Python 3.12, the project GIS dependencies, pywebview 6.2.1 and PyInstaller 6.22.3. WebView2 Evergreen Runtime is required on the target machine. Python is bundled; no SecondPro development paths are required.

```powershell
python -m PyInstaller GNN/mesh_research/packaging/MeshCherry.spec --distpath dist --workpath build
```

Distribute the entire `MeshCherry/` directory, not the EXE alone. Keep `_internal/` next to `MeshCherry.exe`. Use a writable extracted directory; the default read/write workspace is `workspace/` next to the executable. Copy your GeoTIFF and normalized mesh JSON into that workspace, or launch with `--workspace D:\MyMeshData` to explicitly authorize a different folder. Sample data embedded in `_internal` are read-only.

Run frozen diagnostics:

```powershell
$p = Start-Process .\dist\MeshCherry\MeshCherry.exe -ArgumentList '--self-test',"$PWD\smoke.json" -Wait -PassThru
$p.ExitCode
Get-Content smoke.json
```

This checks packaged resources, font HTTP routes, CLR import, true raster-to-grid extraction (10/30) and triangle extraction (25/15). It does not certify visual interaction, clean-machine compatibility or hydrologic accuracy.

Gmsh is deliberately not bundled. Its demo fails explicitly in the frozen build; source users may install the documented external Gmsh dependency. No silent replacement with a different algorithm. The portable binary is unsigned; no recommendation to disable Windows security protections.

Third-party license notices are retained in the packaged distribution metadata and font OFL file. The application runs a token-authenticated loopback server inside its native WebView2 window, not an externally hosted site. No new JS/Python bridge is introduced.
