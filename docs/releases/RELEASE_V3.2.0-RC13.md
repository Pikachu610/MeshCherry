# MeshCherry 3.2.0 RC13 · Windows x64

## Sửa trong RC13

- **DEM cho chia đều:** có thể chọn DEM tham chiếu tùy chọn, chỉ dùng cho hình tổng kết, không tác động thuật toán tạo lưới.
- **Hình tổng kết:** căn thẳng bốn khung DEM / kích thước mục tiêu / toàn cảnh lưới / phóng to; thanh màu nằm ở cột riêng.
- **Trường kích thước chia đều:** hiển thị h đã chọn thay vì báo không khả dụng. Nếu không chọn DEM thì hiển thị thông báo rõ; không tự coi PET, rainfall hay TIFF ROI là DEM.

## Tính năng từ các bản phát triển 3.2

- Tạo lưới Gmsh chia đều, Local relief hoặc hồ sơ DEM SF-1; biên dẫn xuất có kiểm định mà không ghi đè SHP gốc.
- Kiểm định chất lượng lưới, hình tổng kết và báo cáo; quay lại cấu hình để tạo lượt lưới khác.
- So sánh **số phần tử** tam giác với pixel raster hoặc lưới mục tiêu. Đếm hình học không phụ thuộc giá trị/NoData; không gọi số tam giác là số đỉnh. Mức Giảm/Tăng không đồng nghĩa tăng tốc hay độ chính xác.
- Đánh giá sai số raster là tác vụ riêng trên miền dữ liệu hợp lệ; có tiến độ, dừng và kiểm tra trước khối lượng đọc.
- Trích một hoặc nhiều raster, xem bảng trước khi xuất, chỉnh bản nháp và xuất CSV/JSON cùng các định dạng GIS được hỗ trợ.
- Lưu/mở dự án .mcy; GeoTIFF/ASC; menu tệp gọn và phân khu ROI → Lưới → So sánh → Extract → Nhật ký.

## Tải và chạy

Tải ZIP Windows x64 bên dưới, giải nén **toàn bộ** thư mục rồi mở `MeshCherry.exe`. Không chép riêng EXE khỏi `_internal`. Cần Microsoft WebView2 Runtime. Gmsh 4.x không kèm trong ZIP; chọn bản cài riêng của bạn trong ứng dụng. EXE chưa ký số; không tắt bảo vệ Windows.

Với dự án chia đều cũ chưa lưu DEM tham chiếu, chọn DEM rồi tạo lượt mới. Các hình tổng kết cũ không tự thay đổi.

## Tình trạng kiểm chứng

RC13 đã được rà soát mã và xem ảnh dựng; build và kiểm tra đối chiếu nguồn/gói được ghi trong VALIDATION.json. **Không chạy lại kiểm thử chức năng hoặc EXE self-test cho RC13 theo yêu cầu người dùng.** Kiểm chứng 27 Python +151 Node và self-test của RC12 là bằng chứng của RC12, không phải RC13. Nghiệm thu giao diện Windows/máy sạch và ổn định dài hạn vẫn chưa hoàn tất.

Người dùng đã chạy RC13; nhật ký lưu xác nhận tạo lưới và đếm ô đối chứng hoàn thành. Khi **Đánh giá sai số raster**, người dùng gặp thông báo “Vượt giới hạn đọc raster của lượt so sánh”: đây là hạn mức khối lượng đọc (đánh giá có thể cần nhiều lượt đọc), không phải lỗi đếm số phần tử. Đánh giá raster lớn vẫn có hạn chế; không tự nới giới hạn an toàn. Nếu chỉ cần so số phần tử, dùng **Đếm ô đối chứng**, không cần chạy đánh giá sai số. Phản hồi này chưa thay thế nghiệm thu đầy đủ cửa sổ/menu/hộp thoại hay máy sạch.

Bản RC này dành cho tải thử. Bản ổn định 3.0.0 và các tài sản phát hành trước được giữ nguyên. Không tuyên bố kết quả thủy văn/GNN đã được kiểm chứng chỉ từ hình lưới.
