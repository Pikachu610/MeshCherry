# MeshCherry 3.2.0 — Cherry Horizon 🍒

**Từ bản đồ, đến từng ô lưới.**

Cherry Horizon là bản phát hành chính thức tiếp nối RC14. Người dùng đã kiểm thử
và xác nhận các luồng sử dụng trên máy của mình hoạt động ổn trước khi chốt bản này.
Ứng dụng và gói Windows dùng phiên bản 3.2.0; các bản RC và bản 3.0.0 được giữ trong lịch sử.

## Bộ công cụ trong bản này

- **ROI:** Shapefile Polygon, biên GeoTIFF/ASC hoặc miền chữ nhật; xem trước và kiểm tra CRS/hình học.
- **Lưới:** tam giác Gmsh chia đều, Local relief hoặc hồ sơ DEM SF-1; biên dẫn xuất có kiểm định, không sửa Shapefile gốc; quay lại chỉnh cấu hình và chia lại.
- **Chất lượng:** xem/chọn ô, báo cáo lưới và hình tổng kết bốn khung khi có dữ liệu tương ứng. DEM tham chiếu tùy chọn cho chia đều; các khung và thanh màu được căn hàng.
- **So sánh:** số phần tử tam giác so với pixel raster gốc hoặc lưới vuông mục tiêu, mức giảm/tăng và hình đối chiếu; tách phép đếm hình học khỏi đánh giá sai số thuộc tính.
- **Extract:** một/nhiều raster trên cùng lưới; rà soát bản nháp trước khi chạy, xem bảng kết quả theo trang, tiến độ và Dừng.
- **Xuất/lưu:** CSV rộng/dài và JSON kèm metadata; `.mcy`, mesh JSON/MSH khi có, GeoJSON và GeoTIFF/ASC cho loại thuộc tính phù hợp.
- **Điều hướng:** ROI → Lưới → So sánh → Extract → Nhật ký; menu Tệp quản lý mở/lưu dự án.

## Nghiên cứu dữ liệu lớn

**So sánh → Đánh giá sai số raster** mặc định dùng chế độ **Nghiên cứu**:

- Bỏ trần tổng pixel nguồn, tổng pixel đọc, tổng cặp giao và số cửa sổ quét của tác vụ đánh giá sai số.
- Tiền kiểm tra dựa trên metadata; không quét toàn raster hoặc dựng chỉ mục hình học khi giữ khóa giao diện.
- Vẫn đọc từng cửa sổ tối đa 65.536 pixel, giữ kiểm tra toàn vẹn, tiến độ và Dừng; có thể chọn lại chế độ giới hạn công việc.

Không gỡ bảo vệ của toàn ứng dụng: lưới tam giác/ô mục tiêu tối đa 100.000 phần tử,
phân loại tối đa 100.000 mục tích lũy; lô hình học, cache, SF-1 và Extract vẫn có giới hạn.
Dừng là hợp tác, có thể phải chờ thao tác hiện tại kết thúc. Máy vẫn có thể hết RAM/đĩa.

## Tải và chạy

1. Tải `MeshCherry-v3.2.0-windows-x64.zip`, giải nén **toàn bộ**.
2. Mở `MeshCherry.exe`, giữ thư mục `_internal` bên cạnh.
3. Cần Windows 10/11 x64 và WebView2 Runtime. Gmsh 4.x cài riêng, không kèm trong gói.

Python đã đóng gói; không cần GPU rời. EXE chưa ký số: không tắt bảo vệ Windows.
Đối chiếu `SHA256SUMS.txt` và `VALIDATION.json` đính kèm để kiểm tra gói.

Mục **Trợ giúp → Yêu cầu hệ thống & dữ liệu lớn** giải thích điều kiện chạy và tài nguyên.
RAM 8/16/32 GiB trở lên là hướng dẫn theo quy mô, chưa phải cấu hình tối thiểu được
chứng nhận. Không cam kết hoàn thành mọi kích thước dữ liệu hoặc một mức tăng tốc cố định.

## Kiểm chứng và phạm vi

Người dùng đã nghiệm thu các luồng sử dụng của RC14 trên máy của mình; đây là cơ sở
chốt bản 3.2.0. Kiểm tra tự động và đối chiếu gói được ghi trong `VALIDATION.json`.
Xác nhận này không thay thế benchmark tài nguyên, chứng nhận máy Windows sạch hay
kiểm chứng mọi tập dữ liệu.

Các chỉ số so sánh đo biểu diễn raster, không phải độ chính xác thủy văn/routing/GNN.
Số phần tử khác số đỉnh; phép đếm hình học không lọc theo giá trị raster. Khi đánh giá
sai số, mask/NoData vẫn được xử lý theo ý nghĩa dữ liệu. Xuất raster lấy mẫu tâm pixel,
tối đa một triệu pixel, không hỗ trợ tổng lượng extensive.

[Danh sách đầy đủ chức năng và cách dùng](README.md)
· [Lịch sử phát hành](https://github.com/Pikachu610/MeshCherry/releases)
· [MIT License](LICENSE)
