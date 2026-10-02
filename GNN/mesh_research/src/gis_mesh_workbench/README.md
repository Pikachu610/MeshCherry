# Mesh Cherry — GIS mesh workbench v1

## Desktop và hướng dẫn cho người mới — đang nghiệm thu

`gis-mesh-workbench desktop` mở một cửa sổ MeshCherry riêng qua pywebview,
không mở trình duyệt mặc định. Cài extra `pip install -e ".[desktop]"` từ
thư mục package. Windows cần WebView2 Runtime; trên máy phát triển đã phát
hiện runtime, nhưng chưa có nghiệm thu clean-machine. Tài liệu dependency:
https://github.com/r0x0r/pywebview/blob/master/docs/guide/installation.md

`gis-mesh-workbench ui` vẫn là chế độ browser/debug. Hai chế độ dùng cùng
server và backend có kiểm tra token, Host/Origin và giới hạn đường dẫn.
Không có privileged JS API. Desktop không tự mở browser khi thiếu dependency.
Trong lúc có job chạy, đóng cửa sổ bị chặn; chờ run xong để đóng an toàn.

Giao diện mới chia thành bốn bước: dữ liệu, lưới, kiểm tra, kết quả. Welcome
hướng dẫn mẫu hai pixel 10/30; người dùng tự kiểm tra/chạy và bấm cả hai tâm
ô trước khi kết thúc. Có thể bỏ qua hoặc mở lại bằng Hướng dẫn cơ bản.
Chỉ lưu `tutorial_version_seen` trong `%LOCALAPPDATA%/MeshCherry/tutorial.json`;
không lưu token, dữ liệu hay đường dẫn raster vào preference. Browser/debug
không ghi preference này mặc định và hiển thị welcome mỗi phiên mới.

Chưa có EXE/installer. Desktop source launcher không chứng minh rằng một bản
EXE độc lập đã hoạt động. Các phép tính extraction, CRS và giới hạn v1 giữ nguyên.

Ứng dụng cục bộ có giao diện đỏ/trắng để tạo structured grid, nhập mesh tam
giác đã có, chạy một preset Gmsh tham chiếu và trích thuộc tính cho **ô đã có
hình học**. Không phải công cụ chia thích nghi DEM/polygon GIS tổng quát.
Package riêng nằm dưới mesh_research theo ADR 0006, không thay đổi module SF-1.

Bản v1 bounded/reference được ChatGPT phản biện và chấp nhận ngày 2026-10-02,
C2C c2c_c901 iteration 9 (DONE). Tickets 01–05 resolved. Regression cuối:
84 tests passed, 1 Windows symlink permission skip; JS syntax đạt.

Icon v1: cherry đỏ với lưới tam giác và node trắng, nền trong suốt; đã gắn vào
header/favicon. PNG 512 px và Windows ICO đa kích thước đi kèm package.
Master và prompt tạo ảnh được giữ trong `GNN/mesh_research/branding/README.md`
tại root dự án. Đây là thay đổi branding, không bổ sung installer/executable.

Ngày 2026-10-01: ticket 01 resolved sau hai vòng phản biện ChatGPT
(c2c_84b1, iteration 2). 14 test lõi và 50 test toàn suite pass.

## Đã có

Ticket 02 đã được ChatGPT chấp nhận ngày 2026-10-02 (iteration 3):
`import_mesh` đọc mesh-artifact-v0 với
đỉnh uniform XY/XYZ, dùng XY làm hình học phẳng và ghi rõ chiều nguồn;
không coi Z là thuộc tính DEM. ID `cell-N` lấy theo thứ tự tam giác, không
được mã hóa sẵn trong mesh.json. Nếu có manifest run thì yêu cầu pass và
kiểm tra mesh hash khi có; graph kèm theo phải khớp ID/thứ tự. Artifact đứng
riêng được đánh dấu provenance run UNVERIFIED, không tự suy ra QA topology.

`structured_grid` tạo chữ nhật với extent (west,south,east,north), resolution
hai chiều, CRS metre, policy strict/clip. Strict chia hết trong tolerance
1e-9 theo số ô; clip thu hẹp hàng/cột cuối, không nới extent. Hàng 0 phía nam,
cột tăng về đông; ID grid-rRRR-cCCC. Cả hai adapter giới hạn 1000 ô mặc định
và 1 triệu cặp geometry, chưa dành cho mesh lớn. Ô/tọa độ được giữ immutable.

`extraction.py`: `Cell` và `extract_attributes`. Đọc GeoTIFF/band read-only,
kiểm tra CRS/units/geometry, dùng cùng thuật toán cho triangle và rectangle.
Mean/std dùng diện tích giao, extensive là tổng của pixel nguồn, categorical
giữ class fractions. Báo coverage footprint và valid riêng. Lưu hash nguồn,
WKT/affine, XY policy, phiên bản và cách chia cạnh khi chuyển CRS.
Band có scale/offset khác (1, 0) bị từ chối rõ ràng, chưa tự quy đổi.
Dtype, scale, offset, nodata và mask flags được lưu trong provenance.

Ví dụ đã tính bằng fixture hai pixel 10 và 30, mỗi pixel 1 m²:

| Lưới | Mean intensive | Tổng extensive |
| --- | --- | --- |
| Một chữ nhật 2 m² | 20 | 40 |
| Hai tam giác, mỗi ô 1 m² | 25 và 15 | 25 + 15 = 40 |

Đường chéo cắt pixel: tam giác thứ nhất giao 0.25 m² pixel 10 và 0.75 m²
pixel 30. Mean = (10*0.25 + 30*0.75)/1 = 25. Tam giác thứ hai đảo trọng số.
Không dùng sampling tại tâm. Đây là ví dụ intensive/extensive khác ý nghĩa
nhưng trùng số vì diện tích mỗi pixel và mỗi tam giác đều 1 m².

## Chạy

Từ `GNN/mesh_research` trong PowerShell:

```powershell
$env:PYTHONPATH = "src"
.venv-gis/Scripts/python.exe -m unittest discover -s tests -p test_workbench_extraction.py -v
.venv-gis/Scripts/python.exe scripts/demo_workbench_core.py
```

Demo tạo thư mục mới trong `.scratch/gis-mesh-workbench/demo-*`, gồm JSON
kết quả thật và hình PNG. GeoTIFF mẫu tạm thời được tái tạo từ script;
đường dẫn temp trong provenance không phải dữ liệu thực retained.

## Giới hạn phải giữ rõ

- Reference kernel nhỏ: tối đa 10.000 pixel và 1.000.000 cặp tính toán theo mặc định.
  Không dùng trực tiếp cho toàn DEM ROI4 lớn; chưa kiểm chứng hiệu năng.
- Cell phải đã ở CRS projected metre tính toán. Không tự relabel hoặc sửa nguồn.
- Raster khác CRS: chuyển polygon pixel, không resample giá trị. Chia cạnh
  mặc định 8 đoạn; curved edge là xấp xỉ. Chưa chứng minh convergence cho mọi CRS.
- Không suy ra datum/đơn vị cao độ, không xác nhận accuracy phép chuyển datum.
- Kiểm tra source SHA-256 hiện áp dụng file raster chính; sidecar bên ngoài
  chưa có bộ provenance/hashes riêng. Run/export/manifest đã có ở ticket 03.
  QA chỉ xác nhận `main_raster_unchanged`, hash scope là `main_raster_file_only`;
  không khẳng định cả dataset và sidecar bất biến.
- Không suy ra mesh topology/graph hợp lệ từ kết quả extract này.

Đặc tả và ticket: `.scratch/gis-mesh-workbench/` tại root dự án.

## Runner và CLI — ticket 03 đã được chấp nhận

Chạy từ `D:/Longgggg/SecondPro/GNN/mesh_research`:

```powershell
$env:PYTHONPATH = "src"
.venv-gis/Scripts/python.exe -m gis_mesh_workbench.cli run configs/workbench-grid-demo.json
.venv-gis/Scripts/python.exe -m gis_mesh_workbench.cli run configs/workbench-triangle-demo.json
```

Hai config dùng fixture retained của adapter demo, không phải DEM ROI4.
Đường dẫn tương đối tính theo config, không theo CWD. CLI trả JSON có status và
output; exit 0 thành công, 2 diagnostic run lỗi, 3 không đọc được config/output.
Mỗi run tạo thư mục mới, giữ config/cells/attributes JSON, CSV, preflight/QA,
manifest hashes và preview PNG. Ô thiếu dữ liệu là null/CSV trống, không phải 0.
Hash raster vẫn chỉ file chính; sidecar và memory peak chưa được đo/kiểm chứng.
Config không được nâng hard limits. Ticket 03 approved c2c_c901 iteration 5.

## Gmsh reference — ticket 04 đã được chấp nhận

`cell_source.kind = gmsh_reference`, `preset = inclined_plane_250m_v0` chỉ hỗ trợ
miền mẫu phẳng 1000 × 1000 m, EPSG:32649, target edge 250 m, Gmsh 4.15.2.
Đây không phải chức năng chia thích nghi DEM hay chia polygon GIS bất kỳ.
Mesh thích nghi đã có vẫn có thể đi qua `mesh_artifact` trong giới hạn reference.

```powershell
$env:PYTHONPATH = "src"
.venv-gis/Scripts/python.exe scripts/demo_workbench_gmsh.py
```

Demo giữ GeoTIFF hằng số 17 trên miền mẫu, config và toàn bộ child run.
Public `python -m mesh_research run` tạo mesh/graph/QA; workbench không parse MSH.
Child manifest/QA/topology/CRS/version/target size/hashes phải đạt trước extract.
Console log, config hiệu lực và child runs nằm trong thư mục `meshing` của run.
Timeout 45 giây chấm dứt cây tiến trình và giữ diagnostic failure; không fallback.
Cell count preflight là null (chưa tạo), sau tạo mới kiểm tra workload.
Path executable trong preset cũ tính theo thư mục package mesh_research, đúng
quy ước CLI nghiên cứu hiện có; workbench chuyển thành absolute trong config con.
Preset nguồn/raster không sửa. Runtime/RSS của child Gmsh khác runtime workbench;
memory peak của reference v0 vẫn chưa đo. C2C iteration 6 approved.

## Indexed/windowed — ticket 05A đã được chấp nhận

Config `gis-workbench-run-config-v1` yêu cầu `extraction_engine=indexed_windowed_v1`.
V0 vẫn dùng kernel reference cũ, không nâng ceiling và không fallback ngầm.
V1 dùng STRtree cell, chia Rasterio blocks thành cửa sổ bounded, đọc masked
chỉ khi cửa sổ cùng CRS có candidate cells. Raster khác CRS không bỏ cửa sổ:
chuyển biên cửa sổ không bảo đảm bao hết footprint pixel bên trong; cách bảo
thủ đọc mọi cửa sổ giữ parity. Mỗi pixel vẫn dựng affine và chia cạnh như v0.
Các thử nghiệm parity dùng tolerance relative/absolute 1e-10, giữ ID/order,
mean/min/max/std, totals, fractions, NoData/zero, coverage, affine xoay và CRS khác.

Hard limits v1 (chỉ được giảm): source 10 triệu pixel, đọc 2 triệu pixel,
5 triệu candidate pairs, 65.536 pixel/window, 1.000 cells. Ngoài ra 100.000
windows scanned và 100.000 categorical accumulator entries. Chưa tối ưu số cell.
Intensive dùng weighted Welford, không giữ mọi sample trong RAM. Categorical
giữ histogram bounded. Memory metric là process-lifetime peak RSS bytes,
bao cả công việc trước đó, không phải allocation riêng extract; nếu không đo
được ghi unavailable, không báo 0. GDAL cache và overhead không có hard RAM cap.

```powershell
$env:PYTHONPATH = "src"
.venv-gis/Scripts/python.exe scripts/demo_workbench_real.py
```

Demo kiểm SHA-256 DEM hiện tại so với nguồn retained trước khi chạy và sau khi
extract; dùng bốn ô 300 m trong ROI vuông 600 m, không phải benchmark toàn ROI4.
Manifest/QA ghi window/pixel/pair counters, thời gian, giới hạn và metric bộ nhớ.
05A approved C2C iteration 7. Chưa arbitrary polygon/Gmsh DEM-adaptive trong ứng dụng.

## Mesh Cherry WebUI — 05B1 đã được chấp nhận, 05B2 đã kiểm thử browser

```powershell
$env:PYTHONPATH = "src"
.venv-gis/Scripts/python.exe -m gis_mesh_workbench.cli ui
```

CLI in URL local có token phiên. Mở URL đầy đủ ấy; JS bỏ token khỏi thanh địa chỉ
và giữ trong sessionStorage của cùng origin/tab session. Không chia sẻ URL token.
`--port` tùy chọn; không có chế độ LAN/public. Server chỉ bind 127.0.0.1,
kiểm tra Host/Origin/token, không bật CORS và không dùng CDN/fonts ngoài.
Mặc định chỉ browse thư mục SecondPro. Có thể opt-in root khác khi tự khởi động
bằng `--allow-root PATH`, không có API cấp thêm quyền trong trình duyệt.
Picker chỉ trả metadata path/size/type; không upload GeoTIFF. Output mặc định
`.scratch/gis-mesh-workbench/ui-runs`, hoặc chọn thư mục hiện có được phép.

UI tạo config v1, gọi effective_config/preflight chung và giữ config server-side
theo preflight ID. Sửa form phải validate lại; server từ chối ID cũ khi có lần
validate mới. Run chỉ nhận ID, không nhận bản form thứ hai. Một job active/session.
Các stage callback là quan sát, không thay đổi CLI/toán học. Không có nút Cancel
vì chưa có cooperative cancellation. Đóng tab không dừng backend; đừng dừng tiến
trình server khi job chưa xong. UI error mạng không có nghĩa run đã thất bại.

Map SVG trực tiếp từ cells, tỷ lệ X/Y bằng nhau, chấm tâm ô và inspector click.
Download CSV/JSON lấy từ job ID + tên artifact allowlisted, không ghi lại nguồn.
Không có endpoint arbitrary command/file contents. Package kèm HTML/JS/CSS.
Tests API security + runner: symlink Windows test được skip nếu hệ thống không
cấp quyền tạo symlink; chưa tuyên bố test junction thực tế.

Ngày 2026-10-02, browser acceptance đã chạy qua picker/metadata/helpers,
preflight, run, cell inspector và export: structured grid 2 ô (10/30), XYZ mesh
2 tam giác (25/15), Gmsh thật 40 tam giác (17 mỗi ô). CRS EPSG:4326 bị chặn,
Run disabled. CSV và JSON tải xuống khớp byte với artifact run. Hash ba fixture
trước/sau không đổi. Có kiểm tra desktop và màn hình hẹp, console không lỗi.
Bằng chứng nằm tại `.scratch/gis-mesh-workbench/ui-e2e-20261002T095225131627Z/`.
Network capture sau phục hồi browser ghi riêng phạm vi preflight + Gmsh run;
không dùng nó để khẳng định đã bắt đầy đủ mọi request của tất cả các luồng.

### Dùng thử

1. Chọn GeoTIFF, kiểm tra CRS/band/NoData và khai báo semantics/đơn vị giá trị.
2. Chọn lưới vuông/chữ nhật hoặc mesh JSON đã có. Gmsh chỉ dùng preset mẫu.
3. Bấm **Kiểm tra cấu hình**, đọc giới hạn/coverage trước khi chạy.
4. Bấm **Chạy & trích thuộc tính**, chọn tâm ô để xem giá trị và coverage.
5. Tải CSV/JSON hoặc sao chép đường dẫn run chứa toàn bộ hồ sơ kiểm chứng.

Một run chỉ được chạy một lần từ preflight ID; muốn chạy lại phải kiểm tra lại.
Map không có basemap trực tuyến và không tự suy ra CRS của mesh thiếu metadata.
Giới hạn 1.000 cell nghĩa là chưa nhập được toàn bộ mesh ROI4 13.515 ô.
