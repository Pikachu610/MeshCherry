# Mesh Cherry — GIS mesh workbench

## Hành vi hiện tại — MeshCherry 3.0.0

Bản 3.0.0 chính thức đóng gói quy trình mesh-first, trích thuộc tính, so sánh
lưới, lưu dự án và các sửa lỗi đã phản biện ở RC11. Việc nâng phiên bản không
thay đổi thuật toán khoa học. Nghiệm thu tương tác cửa sổ Windows và máy sạch
vẫn đang chờ; xem hồ sơ VALIDATION.json đi kèm bản phát hành.

Khi bấm Tiếp hoặc điều hướng giữa các bước, giao diện đưa đầu mục đang chọn
vào khung nhìn. Tùy chọn giảm chuyển động của hệ thống được tôn trọng; khôi phục
kết quả nền giữ nguyên vị trí xem. Bản nguồn Git không đồng nghĩa các chức năng
này đã có trong bản binary v2.0.0 được phát hành trước đây.

Local relief/SF-1 có khung xem trước DEM riêng ở bước 2, bên cạnh cấu hình trên
màn hình rộng. Chọn DEM `.tif`, `.tiff` hoặc ESRI ASCII Grid `.asc` để xem band,
CRS và kích thước pixel nguồn; thay band sẽ cập nhật ảnh. ASC dùng `.prj` cùng
tên, cùng thư mục để khai báo CRS. Nguồn chưa có CRS vẫn xem được ảnh và có cảnh
báo; quy trình chia lưới vẫn yêu cầu CRS nguồn. Ảnh giảm mẫu và thang màu P2–P98
chỉ phục vụ hiển thị, không đổi DEM, miền ROI hoặc phép tính relief.

Tệp `.mcy` có thể đăng ký icon và thao tác nhấp đúp bằng
`Dang-ky-tep-MCY.cmd` trong bản portable. Đăng ký/gỡ là thao tác chủ động,
chỉ ghi cho tài khoản Windows hiện tại và giữ nguyên liên kết của ứng dụng khác.

### Dự án .mcy và xuất dữ liệu

- **Lưu dự án**: lưu lưới đã chấp nhận, CRS, ID ô, thuộc tính, QA lịch sử,
  tham số và trạng thái xem thành gói `.mcy`. Giới hạn 64 MiB nén / 128 MiB giải nén.
- **Mở dự án**: xem kết quả ngoại tuyến mà không đọc lại raster/ROI hay chạy Gmsh.
  Các đường dẫn và thông tin xác thực không được mang sang dự án. Muốn extract
  mới, người dùng chọn lại raster. QA hình học được tính lại; hồ sơ QA cũ hiển
  thị riêng dưới nhãn kết quả đã lưu, không coi là một lần chạy nghiên cứu mới.
- **GeoJSON**: polygon ô và thuộc tính, chuyển tọa độ sang WGS84 (kinh độ, vĩ độ).
- **GeoTIFF / ASC + PRJ**: chọn độ phân giải theo mét và giá trị NoData. Lấy mẫu
  giá trị ô tại tâm pixel đầu ra; đây là raster hóa xấp xỉ, không phải chuyển đổi
  bảo toàn hình học. Giới hạn một triệu pixel. Tổng lượng extensive chưa được
  raster hóa; xuất vector/JSON để tránh nhân sai tổng. ASC tải trong ZIP cùng `.prj`.
- CSV/JSON thuộc tính và MSH/mesh JSON vẫn có khi lượt chạy tương ứng đã tạo chúng.
  `.mcy` không đóng gói nguyên bản MSH hay tệp đầu vào.

Quy trình: chọn miền ROI → chọn cách và kích thước lưới → kiểm tra cấu hình →
tạo và xem chất lượng lưới → tùy chọn extract raster lên các ô đã lưu.
ROI nhận biên ngoài GeoTIFF/ASC, miền chữ nhật hoặc bộ `.shp/.shx/.dbf/.prj`.
Lưới đều trên SHP/chữ nhật không cần raster. Relief cần DEM và band cao độ riêng;
raster tham chiếu độ phân giải chỉ cung cấp thông tin cho người chọn kích thước.

Menu **Công cụ** có so sánh lưới vuông–tam giác và hồ sơ phương pháp GNN/SF-1.
Comparison dùng lưới tam giác đã chấp nhận, cắt lưới vuông theo đúng union của
các tam giác (giữ lỗ, tách phần rời thành các ô), cùng raster/band/miền hợp lệ.
Số node là số ô; chênh lệch node = tam giác − vuông. Phần trăm giảm có dấu,
ngân sách lưới vuông phụ thuộc cạnh ô do người dùng chọn.
Intensive báo RMSE/MAE/bias tích phân sai lệch giá trị ô và pixel trên diện tích
giao. Đây là lỗi biểu diễn piecewise-constant, không phải RMSE của raster được
dựng ngược bằng trung bình pixel. Extensive báo bảo toàn tổng; categorical báo
bảo toàn tỷ phần lớp toàn miền và mức đồng thuận lớp đại diện theo diện tích:
tổng diện tích lớp chiếm ưu thế trong mỗi ô / tổng diện tích hợp lệ. Chỉ số này
đo khả năng biểu diễn raster bằng một lớp đại diện cho mỗi ô. Bảng xem trước
giới hạn 100 lớp; báo cáo JSON giữ đủ các lớp.
Comparison chạy nền có Dừng, phục hồi theo request ID và report JSON riêng.

Hồ sơ **GNN · biến thiên địa hình SF-1** dùng DEM north-up vuông 30 m, cùng CRS
mét với ROI, tối đa 2 triệu pixel, cao độ được khai báo theo mét. Hai cửa sổ
5×5 và 15×15 (toàn bề rộng 150/450 m), chuẩn hóa riêng P25/P95 rồi lấy max.
h250–1000 m/gamma2/gradation1,5 tại250m/control120m được giữ theo nghiên cứu.
Scale0,25–4 bắt buộc được khai báo trong cấu hình; UI điền sẵn1 để người dùng
xem và điều chỉnh. Hệ số được gửi nguyên giá trị, không hiệu chỉnh theo ngân sách node.
ROI gốc không giản lược. Trường control khớp artifact GNN khi dùng đúng input,
mask và scale nghiên cứu; điều này không tái thực hiện calibration/mesh cũ.
Relief33×33 một cửa sổ vẫn là phương pháp thử nghiệm riêng, không gắn nhãn SF-1.

Config v2 cho Gmsh, mesh nhập và frozen cells có giới hạn tính toán 100.000 ô;
lưới chữ nhật và config cũ giữ giới hạn 1.000 ô. Riêng lưới vuông đối chiếu trong
Comparison được giới hạn100.000 ô và chịu thêm các trần pixel/cặp giao. Màn hình
SVG dùng path theo nhóm trên2.000 ô, vẫn giữ đầy đủ hình học/tương tác tới trần
mesh100.000 ô. Kiểm thử logic100.000 ô không xác nhận hiệu năng cửa sổ máy sạch.

GeoTIFF/ASC và các file đi kèm được nhận diện bằng SHA-256, kiểm tra trước/sau
cho từng vai trò ROI, DEM, tham chiếu và extract. Thêm/xóa file đi kèm cũng làm
run không đạt. CRS nguồn phải được khai báo; phép chuyển tọa độ khác với gán
nhãn CRS. Cell dùng mặt phẳng projected metre, giữ nguyên ID/thứ tự đỉnh; CW/CCW
đều hợp lệ cho extraction không sử dụng pháp tuyến. Cell suy biến hoặc chồng
lấn diện tích bị từ chối. Union các record SHP có thể bỏ đường ranh nội bộ;
không áp dụng phép đơn giản hóa biên nguồn.

Phiên bản từ `_version.py` được dùng chung cho UI, bootstrap, build và manifest.
Ảnh đầu vào là PNG Blob; giảm mẫu/stretch chỉ phục vụ xem trước. Kiểm thử chuẩn:
`PYTHONPATH=src python -m unittest discover -s tests` và
`node --test tests/workbench_ui_state.test.cjs`. Hướng dẫn build: `packaging/README.md`.

Các mục dưới đây lưu lịch sử phát triển; các giới hạn cũ chỉ áp dụng cho mốc
phiên bản ghi trong mục đó. Việc phát hành ứng viên mới còn phụ thuộc audit 20.

## v2.2.1 — mốc lịch sử sau phản biện

Danh sách gần đây chỉ trả ID, loại, tên và thời điểm; không trả đường dẫn tuyệt đối.
Đường dẫn được trả sau khi người dùng bấm mở ID và backend kiểm tra lại tệp.
Bổ sung test giới hạn 21 thành phần, >2.000 đỉnh, >100 records; mutation ngay
trong Gmsh; và SHP EPSG:4326 → EPSG:32649 → Gmsh thật → extraction.
144 discovered, 143 passed, 1 Windows symlink skip. EXE đã build rồi self-test
chính xác binary đó với SHA trước/sau giống nhau. Giữ v2.2.0 bên cạnh, không ghi đè.
Native Windows picker và clean-machine vẫn UNVERIFIED; chưa push Git.
Đường dẫn hiện tại: `MeshCherry/MeshCherry-Desktop-v2.2.1/MeshCherry/MeshCherry.exe`.

## v2.2.0 — mốc SHP và tệp gần đây (2026-10-03)

Mẫu 10/30 chỉ hiển thị trong hướng dẫn; không có trong màn hình dữ liệu thông thường
hoặc lịch sử. Đây là thay đổi UI, không phải cơ chế thu hồi quyền đọc fixture.
Desktop lưu tối đa 10 đường dẫn GeoTIFF/mesh/SHP trong
`%LOCALAPPDATA%/MeshCherry/recent-files.json`, không lưu nội dung hay token.
Khi khởi động, lịch sử không cấp quyền đọc. Chỉ bấm một ID gần đây mới kiểm tra lại
định dạng/tệp và cấp quyền đúng input/bộ SHP; không cấp quyền thư mục cha.
Mọi lần mở lại làm mất preflight cũ. Xóa lịch sử không xóa dữ liệu gốc.

`gmsh_generated` nhận thêm `boundary_path` tùy chọn. SHP chỉ xác định miền;
GeoTIFF vẫn là nguồn thuộc tính và DEM sizing. Chọn `.shp` bằng hộp Windows;
tự tìm `.shx/.dbf/.prj` cùng tên. Bốn tệp bắt buộc, hash riêng và hash bộ tệp.
Chỉ Polygon 2D, không tự sửa ring/geometry hoặc đơn giản hóa. Tối đa 100 records,
2.000 đỉnh nguồn, 200 rings/record, 20 thành phần, 16 MiB/component (.prj 64 KiB).
Hợp các polygon để tạo miền; giữ lỗ. CRS lấy từ .prj, không relabel.
Đổi CRS bằng GIS X,Y, chia mỗi cạnh nguồn thành 8 đoạn tuyến tính trước chuyển;
cạnh trong cùng CRS không đổi. Đây là xấp xỉ, không chứng minh accuracy datum.
Miền vượt kinh tuyến đổi ngày bị từ chối. CRS tính toán phải projected metre.

Gmsh nhận các Curve Loop/Plane Surface thật, không thay polygon bằng bbox.
QA đối chiếu union tam giác với miền bằng diện tích symmetric difference;
`meshing/domain-boundary.json` giữ hình học đã chuyển CRS. Relief vẫn control
33×33, nhưng loại điểm ngoài polygon/trong lỗ khỏi relief và p25/p95.
Coverage control báo riêng trên miền và toàn control grid; không nhầm hai mẫu số.
Hash bộ SHP được kiểm sau meshing và sau extraction. Raster hash vẫn riêng
`main_raster_file_only`; SHP không biến thành chứng nhận lưu vực/thủy văn.

EXE thử: `MeshCherry/MeshCherry-Desktop-v2.2.0/MeshCherry/MeshCherry.exe` từ root
SecondPro. Bản cũ giữ nguyên. Kiểm thử tự động: 138 discovered, 137 passed,
1 Windows symlink skip; frozen self-test gồm Gmsh relief 38 ô và SHP có lỗ 46 ô.
Chưa nghiệm thu hộp chọn Windows trên EXE; chưa publish Git.

Các mục dưới là lịch sử các mốc trước; giới hạn rectangle-only đã được mở rộng
bởi mục v2.2.0 này, không thay đổi pipeline nghiên cứu SF-1/Phase 1.

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

Đã có bản EXE portable thử cục bộ v2.1 với self-test backend đạt. Điều này chưa
chứng minh tương tác cửa sổ native đầy đủ hoặc tương thích máy Windows sạch.
Các phép tính extraction, CRS và giới hạn v1 giữ nguyên.

Ứng dụng cục bộ có giao diện đỏ/trắng để tạo structured grid, nhập mesh tam
giác đã có, hoặc gọi Gmsh bên ngoài tạo tam giác mới trên miền chữ nhật hoặc
SHP Polygon 2D trong giới hạn v2.2, rồi trích thuộc tính cho **ô đã có hình học**.
Hình học polygon hợp lệ không xác nhận miền đó là lưu vực.
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
Tại mốc 05A: C2C iteration 7 approved; lúc ấy chưa hỗ trợ polygon trong ứng dụng.
SHP bounded được bổ sung ở ticket 11, không thay đổi kernel indexed.

## Gmsh tạo mới trên miền người dùng — mốc v2.1.x, ticket 10

Mục này mô tả phạm vi ban đầu v2.1.x. Ticket 11 mở rộng sang SHP có lỗ;
xem mục v2.2 ở đầu tài liệu cho hợp đồng hiện tại. Nghiệm thu native vẫn chưa đóng.

`cell_source.kind=gmsh_generated` độc lập với preset `gmsh_reference` cũ.
Người dùng cài/giải nén Gmsh, dán đường dẫn tuyệt đối `gmsh.exe`, kiểm tra và
lưu đường dẫn trong workspace. Desktop mở hộp chọn Windows và cấp quyền đúng
tệp đã chọn; browser/debug chỉ duyệt roots được phép. Không cấp quyền đọc toàn
ổ đĩa. Preflight kiểm tra lại executable mỗi lần, không
tin đường dẫn đã lưu. Giao diện nhận 4.x; đã thử thực tế với 4.15.2. Phiên bản
và SHA-256 chỉ ghi provenance/độ ổn định, không chứng minh nguồn chính thức.

Tại v2.1.x, miền chỉ là chữ nhật X/Y trong CRS projected metre. Hai lựa chọn: kích thước đều,
hoặc local relief DEM thử nghiệm. Relief tính trên control raster 33 x 33
(nearest chỉ cho size field), max-min trong cửa sổ, chuẩn hóa p25/p95 và gamma 2,
rồi chuyển sang target size. Thiếu DEM dùng h_max; relief hằng dùng h_max.
Không có gradation limiter hiệu chỉnh SF-1, không gọi đây là cấu hình tối ưu.
DEM số phức, scale/offset khác identity hoặc không có cao độ hợp lệ bị từ chối.

`.geo` đặt rõ `Mesh.MeshSizeFactor=1`, PostView/MathEval background sizing,
MSH2.2 ASCII, tam giác bậc một và một luồng. Hệ số này tránh cấu hình Gmsh
bên ngoài làm lệch kích thước. Heuristic diện tích/h_min chỉ chặn workload
sớm; số ô thật được kiểm tra sau sinh mesh và tối đa 1000. Process timeout
60 giây; không fallback ngầm. Parser kiểm section/count/node references,
hình học dương/không chồng, và union phủ đúng miền chữ nhật của mốc v2.1.x.
V2.2 đối chiếu với polygon thực khi chọn SHP, không đối chiếu bbox.

Run giữ `meshing/domain.geo`, `size.pos` khi dùng relief, `gmsh.log`, actual
`mesh.msh`, normalized `mesh.json`, `generator.json` và hash. Nút xuất MSH/mesh
JSON xuất hiện với lượt Gmsh thành công. Extraction vẫn dùng **raster gốc**,
không dùng control raster 33 x 33. Tâm ô hiển thị không phải đỉnh Gmsh, chưa
phải graph dòng chảy; không suy ra lưu vực, routing hay chất lượng GNN.

Thử địa hình nhân tạo biến thiên hiện cho 89 tam giác; median diện tích vùng
gồ ghề nhỏ hơn vùng phẳng. Đây chỉ là bằng chứng phản ứng kích thước, không
phải benchmark accuracy/calibration. Ticket chưa resolved và chưa phát hành
Git đến khi hoàn tất phản biện và nghiệm thu UI/native.

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
2. Chọn lưới vuông/chữ nhật, tạo tam giác bằng Gmsh hoặc nhập mesh JSON đã có.
3. Bấm **Kiểm tra cấu hình**, đọc giới hạn/coverage trước khi chạy.
4. Bấm **Chạy & trích thuộc tính**, chọn tâm ô để xem giá trị và coverage.
5. Tải CSV/JSON hoặc sao chép đường dẫn run chứa toàn bộ hồ sơ kiểm chứng.

Một run chỉ được chạy một lần từ preflight ID; muốn chạy lại phải kiểm tra lại.
Map không có basemap trực tuyến và không tự suy ra CRS của mesh thiếu metadata.
Giới hạn 1.000 cell là giới hạn lịch sử của các bản v2.4.x.

## v2.5.0: ranh giới gốc nhiều đỉnh

SHP không còn bị chặn ở 2.000 điểm và không áp dụng simplification. UI hiển
thị số điểm nguồn, số điểm sau chuyển CRS và thông báo có thể chạy lâu khi
có hơn 2.000 điểm. Trường hợp đổi CRS vẫn thêm điểm trên từng cạnh theo
chính sách đã công bố; không bỏ điểm nguồn hoặc thay ranh giới bằng bbox.

Config v2 cho Gmsh, mesh JSON và frozen-cell extract hỗ trợ tối đa 100.000 ô;
lưới chữ nhật và các config v1 giữ ngân sách 1.000 ô. Kiểm tra chồng lấn dùng
STRtree với cùng tiêu chí diện tích giao. Ngân sách hiện tại: tối đa 250.000
điểm biên xử lý, MSH/cells JSON ≤ 64 MiB, 250.000 node, 600.000 phần tử MSH,
5M cặp ứng viên kiểm tra chồng lấn; Gmsh tối đa 300 giây. Các ngưỡng này là
giới hạn tài nguyên, không phải cam kết tốc độ hoặc chất lượng lưới.

Chức năng vẫn đối chiếu union mesh với miền polygon thực, kiểm tra lỗ và
giữ hash tệp đầu vào. Không suy ra độ chính xác thủy văn từ ranh giới nhiều
đỉnh. Dữ liệu thử `original_boundary_2206` là miền nhân tạo dùng kiểm thử.
