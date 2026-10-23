# MeshCherry 🍒

**Từ bản đồ, đến từng ô lưới.**

MeshCherry là phần mềm GIS desktop cho Windows: tạo lưới tam giác, xem chất lượng,
so sánh với lưới cấu trúc và trích thuộc tính từ một hoặc nhiều raster trên cùng miền.
Giao diện tiếng Việt, xử lý dữ liệu cục bộ, không cần cài Python để chạy bản đóng gói.

## MeshCherry 3.2.0 — Cherry Horizon

[Tải bản chính thức cho Windows x64](https://github.com/Pikachu610/MeshCherry/releases/tag/v3.2.0)
· [Ghi chú phát hành](RELEASE_V3.2.0.md)
· [Các phiên bản trước](https://github.com/Pikachu610/MeshCherry/releases)

## Bạn có thể làm gì?

| Công cụ | Chức năng |
| --- | --- |
| **ROI — miền nghiên cứu** | Nhập Shapefile Polygon, biên GeoTIFF/ASC hoặc miền chữ nhật; xem trước, kiểm tra hình học, CRS và độ phân giải. |
| **Tạo lưới tam giác** | Gmsh chia đều theo kích thước mục tiêu hoặc thích nghi theo địa hình DEM; hồ sơ SF-1 và biến thể Local relief. Quay lại chỉnh thông số, tạo lại mà không phải bắt đầu từ đầu. |
| **Xử lý đường biên** | Tùy chọn tạo biên dẫn xuất, kiểm tra sai lệch diện tích và khoảng cách; giữ nguyên Shapefile nguồn. |
| **Xem và kiểm định** | Pan/zoom, chọn ô, xem thuộc tính và báo cáo chất lượng; hình tổng kết DEM, trường kích thước, toàn mesh và vùng phóng to khi có dữ liệu tương ứng. |
| **So sánh lưới** | Đối chiếu số phần tử tam giác với pixel raster gốc hoặc lưới vuông mục tiêu; hiển thị số lượng và mức giảm/tăng trước, đánh giá sai số raster khi được yêu cầu. Có mốc ước tính theo độ phân giải khi chưa có raster. |
| **Trích thuộc tính** | Trích một hoặc nhiều GeoTIFF/ASC trên cùng lưới; trung bình theo diện tích, phân bổ tổng lượng hoặc tỷ phần lớp tùy ý nghĩa dữ liệu. Theo dõi tiến độ và Dừng tác vụ. |
| **Rà soát bảng** | Xem bản nháp trước khi trích; đổi tên/sắp xếp/chọn cột, loại dòng, thêm cột và dòng nhập tay bổ sung; xem trước kết quả theo trang trước khi xuất. |
| **Lưu và xuất** | Mở/lưu dự án `.mcy`; bảng CSV rộng/dài, JSON và metadata; xuất mesh JSON/MSH khi có, GeoJSON và raster GeoTIFF/ASC cho thuộc tính được hỗ trợ. |
| **Theo dõi công việc** | Nhật ký, trạng thái và tiến độ theo tác vụ; xem cấu hình lượt chạy và kết quả đã lưu. |

### Dữ liệu lớn cho nghiên cứu

Trong **So sánh → Đánh giá sai số raster**, chế độ **Nghiên cứu** không đặt trần
tổng pixel nguồn, pixel đọc cộng dồn, cặp giao hình học hoặc cửa sổ quét.
Tác vụ vẫn đọc theo cửa sổ tối đa 65.536 pixel, kiểm tra dữ liệu và cho phép Dừng.
Chế độ giới hạn công việc vẫn có thể chọn lại.

Đây không phải toàn ứng dụng không giới hạn: lưới tam giác/lưới mục tiêu vẫn tối đa
100.000 phần tử; SF-1, Extract, phân loại, lô hình học và cache có bảo vệ riêng.
Thời gian và bộ nhớ phụ thuộc dữ liệu; máy có thể hết RAM hoặc dung lượng.

## Bắt đầu trong vài phút

1. Tải ZIP của bản 3.2.0, giải nén **toàn bộ** rồi mở `MeshCherry.exe`. Giữ `_internal` cạnh EXE.
2. Trong **ROI**, chọn miền nghiên cứu và kiểm tra dữ liệu.
3. Sang **Lưới**, chọn Gmsh, cấu hình chia đều hoặc DEM thích nghi rồi tạo lưới.
4. Sang **So sánh** để xem số phần tử và, nếu cần, chạy đánh giá sai số với raster đối chiếu.
5. Sang **Extract**, thêm raster, rà soát bảng, trích thuộc tính và xuất kết quả.
6. Dùng **Tệp → Lưu dự án** để tiếp tục công việc ở lần mở sau.

Gmsh cài riêng từ [gmsh.info](https://gmsh.info/); chọn executable và kiểm tra phiên bản
trong ứng dụng. Tệp dữ liệu nguồn không được đưa vào kho mã GitHub này.

## Yêu cầu hệ thống

- Windows 10/11 **64-bit**, Microsoft Edge WebView2 Runtime, workspace có quyền ghi.
- Không cần Python hoặc GPU rời để chạy gói desktop; tạo lưới cần Gmsh 4.x cài riêng.
- CPU 64-bit hiện đại và SSD được khuyến nghị. RAM 8 GiB cho dữ liệu nhỏ, 16 GiB để bắt đầu, 32 GiB trở lên cho dữ liệu nặng là **gợi ý**, không phải mức tối thiểu đã chứng nhận bằng benchmark.
- Xem **Trợ giúp → Yêu cầu hệ thống & dữ liệu lớn** trong app. EXE chưa ký số; không tắt bảo vệ Windows để chạy.

## Hiểu đúng kết quả

Số **phần tử tam giác** khác số **đỉnh hình học**. Khi dùng graph tại tâm ô, mỗi ô
tương ứng một node; không dùng lẫn hai cách đếm. So sánh hình học đếm pixel/ô trên
miền đối chiếu, độc lập với việc đánh giá giá trị và NoData của raster.

RMSE/MAE/bias và các chỉ số bảo toàn/tỷ phần đo cách biểu diễn thuộc tính raster;
không tự chứng minh độ chính xác thủy văn, routing hoặc mô hình GNN.
SF-1 có điều kiện dữ liệu riêng được nêu trong app. Raster xuất dùng lấy mẫu tâm
pixel, tối đa một triệu pixel và không áp dụng cho tổng lượng extensive.
Bản nháp trước extract hiển thị cấu trúc bảng; giá trị raster chỉ có sau khi tính.
Dòng nhập tay là bảng bổ sung, không tự tạo thêm ô lưới; hình học và số liệu tính
toán không phải các ô Excel có thể sửa tùy ý.

## Mã nguồn và giấy phép

[Hướng dẫn module](GNN/mesh_research/src/gis_mesh_workbench/README.md)
· [Hướng dẫn build Windows](GNN/mesh_research/packaging/README.md)
· [MIT License](LICENSE)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\GNN\mesh_research[desktop]"
.\.venv\Scripts\python.exe .\launch_meshcherry.py
```

Các bản cũ được giữ trong [Releases](https://github.com/Pikachu610/MeshCherry/releases); ghi chú cũ được gom trong [kho lưu trữ](docs/releases/).
Giữ thông báo giấy phép của font và thư viện phụ thuộc khi phân phối lại.
