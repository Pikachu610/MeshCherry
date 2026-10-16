# MeshCherry — Windows desktop

## Source candidate 2.9.1

Nhánh nguồn hiện tại tiếp nối bản portable v2.0.0 đã phát hành. Bản 2.9.1 bổ sung
quy trình mesh-first, gọi Gmsh cài riêng, ROI SHP/GeoTIFF/ASC, kiểm tra chất lượng,
extraction trên ô bất biến, so sánh lưới vuông–tam giác và hồ sơ địa hình SF-1.
Xem [hướng dẫn hiện tại](GNN/mesh_research/src/gis_mesh_workbench/README.md) và
[ghi chú bản nguồn 2.9.1](RELEASE_V2.9.1.md).

Các sửa lỗi trong 2.9.1 tập trung vào Stop/preflight, phục hồi tác vụ theo request
ID, metadata khi hủy comparison, cạnh ranh giới dài bằng 0 và số học ổn định.
Hồ sơ kiểm thử được ghi trong release notes. Việc cập nhật source không thay thế
kiểm tra trực quan cửa sổ Windows hoặc xác nhận trên máy sạch.

**Binary trong Releases vẫn là v2.0.0 cho đến khi có bản phát hành mới được ghi
rõ.** Không dùng kết quả kiểm thử nguồn để suy ra EXE cũ đã có chức năng mới.

## Chạy bản nguồn hiện tại

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\GNN\mesh_research[desktop]"
.\.venv\Scripts\python.exe .\launch_meshcherry.py
```

Gmsh được cài riêng và chọn trong Thiết lập Gmsh. Tệp nghiên cứu cá nhân, môi trường
Python, kết quả chạy và Gmsh executable không thuộc source repository.

Giấy phép mã nguồn: [MIT](LICENSE). Giữ các thông báo giấy phép riêng của font
và thư viện phụ thuộc khi đóng gói.

## Hướng dẫn lưu trữ cho bản portable v2.0.0

Phần dưới mô tả đúng chức năng và giới hạn của bản binary v2.0.0, được giữ lại
để người tải bản cũ có thể đối chiếu.

Ứng dụng GIS chạy cục bộ trong cửa sổ riêng, giao diện đỏ–trắng, font Be Vietnam Pro offline và hướng dẫn từng bước. Không cần Python để chạy bản portable.

## Tải và chạy

Tải `MeshCherry-v2.0.0-windows-x64.zip` trong [Releases](https://github.com/Pikachu610/MeshCherry/releases/tag/v2.0.0), giải nén toàn bộ rồi mở **MeshCherry.exe**. Không tách EXE khỏi thư mục `_internal`.

Yêu cầu: Windows x64, .NET Framework và Microsoft Edge WebView2 Evergreen Runtime. Gói chưa ký số; không tắt bảo vệ Windows để chạy. Giải nén ở thư mục có quyền ghi. Không cài Gmsh binary trong gói này.

## Dữ liệu và thao tác cơ bản

1. Chọn **Bắt đầu với mẫu**. Mẫu nhân tạo hai pixel 10/30 đã tích hợp.
2. Giữ lưới 1 × 1 m, kiểm tra cấu hình rồi chạy.
3. Bấm hai tâm ô: giá trị tương ứng 10 và 30, coverage 100%.
4. Tải CSV/JSON hoặc sao chép đường dẫn hồ sơ.

Để dùng dữ liệu riêng, chép GeoTIFF và mesh JSON vào `workspace/` cạnh EXE. Có thể chọn thư mục riêng bằng:
`MeshCherry.exe --workspace D:\\MyMeshData`.
Ứng dụng chỉ đọc trong thư mục được cho phép. Kết quả lưu trong `workspace/.scratch/gis-mesh-workbench/ui-runs/`. Không ghi đè raster nguồn; dữ liệu mẫu tích hợp chỉ đọc.

## Phạm vi v2

- Lưới vuông/chữ nhật trong projected CRS có đơn vị mét; strict/clip cho ô biên.
- Nhập mesh-artifact-v0 JSON XY/XYZ; tính diện tích theo XY.
- Trích intensive theo trung bình diện tích, extensive theo phân bổ bảo toàn, categorical theo tỷ lệ lớp.
- Hiển thị cell, tâm ô, coverage, NoData, min/max/std và hồ sơ CSV/JSON.
- Cửa sổ desktop WebView2; server chỉ trên loopback, có Host/Origin/token guard và không có JS/Python bridge mới.
- Font tiếng Việt offline; bo góc nhất quán; tutorial và metadata dễ đọc hơn v1.
- Tối đa 1.000 cell/run và các giới hạn workload hiện có.

EXE **không** tạo mesh thích nghi từ DEM/polygon bất kỳ; không có basemap hay drag/drop. Gmsh demo chỉ chạy ở bản source khi cài Gmsh 4.15.2 bên ngoài; EXE báo thiếu chức năng rõ ràng, không tự đổi thuật toán. Chấm đỏ là tâm cell, không tự chứng minh graph/routing/GNN hợp lệ. Coverage 100% không phải độ chính xác thủy văn. Mesh research vẫn độc lập với Phase 1.

## Chạy từ source / build EXE

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\GNN\mesh_research[desktop]"
.\.venv\Scripts\python.exe .\launch_meshcherry.py
```

Xem [hướng dẫn build](GNN/mesh_research/packaging/README.md) và requirements-v2.lock.txt. Build phát hành dùng Python 3.12.14, pywebview 6.2.1 và PyInstaller 6.22.3.

## Kiểm chứng và giới hạn

Riêng mốc portable v2.0.0: 97 test được tìm thấy, 96 đạt, 1 skip quyền symlink trên Windows. Frozen EXE của mốc đó tự kiểm tra tài nguyên, CLR, ô vuông 10/30 và tam giác 25/15. Cửa sổ native khởi chạy và phản hồi trên máy build; đây không phải chứng nhận trên máy sạch hay toàn bộ tương tác native. Gói portable chưa ký số. Kết quả ứng viên nguồn 2.9.1 được ghi riêng trong RELEASE_V2.9.1.md.

Bản v1 được giữ trong lịch sử Git. Binary phân phối qua Release asset, không commit thư viện/binary hay dữ liệu cá nhân vào source Git.

Tests tích hợp Gmsh được đánh dấu skip nếu executable cài ngoài chưa có, không được tính là đã kiểm chứng Gmsh trong EXE.
