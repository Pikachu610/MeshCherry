# MeshCherry v1

Ứng dụng GIS cục bộ với giao diện đỏ/trắng: tạo lưới vuông/chữ nhật, nhập mesh tam giác, trích thuộc tính raster theo diện tích giao và xem từng tâm ô.

![MeshCherry](branding/MeshCherry.png)

## Phạm vi bản v1

- Structured grid: extent và kích thước ô theo hệ tọa độ projected metre; strict/clip cho ô biên.
- Nhập mesh-artifact-v0 JSON (XY hoặc XYZ; tính hình học phẳng bằng XY).
- Intensive: trung bình diện tích; extensive: phân bổ tổng/pixel; categorical: tỷ lệ lớp.
- Coverage, NoData, kiểm tra nguồn, CSV/JSON và hồ sơ run.
- Một preset Gmsh tham chiếu cố định, cần Gmsh 4.15.2 cài ngoài.

Đây là **source release**, không phải EXE hay installer. Giới hạn 1.000 cell/run. Chưa có UI tạo mesh thích nghi DEM cho polygon bất kỳ. Chấm đỏ là tâm cell, không tự chứng minh graph/routing/GNN hợp lệ. Phần mesh research đi kèm là backend nghiên cứu độc lập với Phase 1; config ROI4/SF-1 cần dữ liệu ngoài repo.

## Cài và chạy trên Windows

Cần Python >= 3.10. Trong PowerShell tại thư mục repo:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .\GNN\mesh_research
.\.venv\Scripts\python.exe .\launch_meshcherry.py
```

Launcher mở trình duyệt qua URL localhost có token phiên. Giữ cửa sổ dòng lệnh đang chạy; Ctrl+C để dừng. Không đưa token lên Git hoặc chia sẻ URL có token. Dữ liệu được chọn trong phạm vi repo; kết quả ở `results/` và bị Git bỏ qua.

## Dữ liệu thử đi kèm

Không kèm DEM cá nhân, kết quả nghiên cứu, môi trường Python hay Gmsh binary.

1. Chọn `sample-data/sample_10_30.tif`, đọc nguồn, dùng CRS và bounds nguồn. Lưới chữ nhật 1 × 1 m, Intensive: 2 ô, giá trị 10 và 30, coverage 100%.
2. Giữ raster trên, nhập `sample-data/two_triangles.json`: 2 tam giác, giá trị 25 và 15. Trọng số là diện tích giao, không lấy mẫu tại tâm.
3. Preset Gmsh: raster `sample-data/gmsh_constant17.tif`, EPSG:32649, Gmsh reference. Trên môi trường tham chiếu đã có 40 tam giác, giá trị 17. Để chạy, đặt Gmsh 4.15.2 tại `Test_chialuoi/gmsh-4.15.2-Windows64/gmsh.exe` theo config. Binary không được đóng gói trong repo.

## Kiểm tra

```powershell
$env:PYTHONPATH = "$PWD\GNN\mesh_research\src"
.\.venv\Scripts\python.exe -m unittest discover -s GNN/mesh_research/tests -p 'test_workbench*.py' -v
```

Nhóm Gmsh có test thực thi thật và cần dependency nói trên. Test ROI4/SF-1 cần dữ liệu nghiên cứu ngoài repo; không coi thiếu dữ liệu là đã kiểm chứng. Bản làm việc SecondPro trước khi xuất source: 85 test workbench passed, 1 Windows symlink permission skip (bao gồm thay đổi icon). Clone mới cần kiểm tra lại theo môi trường của người dùng.

Kiểm tra bản source xuất ngày 02/10/2026 (không Gmsh): 44 test, 42 pass và 2 skip. Hai config workbench demo đã đổi đường dẫn để dùng `sample-data/` đi kèm và ghi ra `results/`.

Source được giữ theo cấu trúc `GNN/mesh_research` để giữ chính sách đường dẫn của backend. Không thay đổi thuật toán khi xuất bản v1.
