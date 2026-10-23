# MeshCherry 3.0 RC11

Windows x64 portable prerelease. Application identity: `3.0.0rc11`.

## Thay đổi

- Tách Lưới & chất lượng, Extract thuộc tính và Nhật ký thành ba tab.
- Hiện lỗi kiểm tra ngay tại Extract; giữ nguyên hình học và ID của lưới.
- Sửa giới hạn extract khi mở lại dự án .mcy có hơn 1.000 ô.
- Giới hạn số lần tải lại kết quả; lỗi truy cập cố định mở lại điều khiển và
  có nút thử tải lại. Kết quả chưa đủ không được công bố thành dữ liệu extract.
- Nhật ký dùng toàn chiều rộng; thanh trạng thái giữ vị trí khi dừng/phục hồi.
- Chi tiết kiểm tra chia thành các mục có nhãn, đường dẫn dài tự xuống dòng.
- Nhãn Tiến độ công đoạn thể hiện phạm vi phần trăm của công đoạn hiện tại.
- Xem trước DEM GeoTIFF/ASC riêng ở bước tạo lưới, kèm CRS và kích thước pixel.

## Dùng thử

1. Giải nén toàn bộ gói Windows, mở MeshCherry/MeshCherry.exe.
2. Mở dự án → test-data/triangle-example.mcy để xem kết quả mẫu.
3. Chọn ROI, cấu hình Gmsh/lưới, kiểm tra rồi tạo lưới.
4. Trong Extract thuộc tính, chọn GeoTIFF/ASC, band, đơn vị và phương pháp.
5. Xuất CSV/JSON hoặc lưu dự án .mcy sau khi kết quả hoàn tất.

Gmsh tải riêng từ gmsh.info; WebView2 Runtime cần có trên máy. Python được đóng
gói cùng ứng dụng. Không phân phối Gmsh hay dữ liệu nghiên cứu cá nhân.

## Phạm vi và nghiệm thu

Đây là prerelease, chưa phải stable 3.0. Kiểm thử tự động và kiểm tra giao diện
trình duyệt không thay thế nghiệm thu cửa sổ Windows, hộp chọn tệp, Explorer và
máy sạch. EXE chưa ký số; không tắt bảo vệ Windows để chạy. ETA chỉ hiện khi có
đủ số đo của công đoạn; không có ước tính tổng thời gian giả định.

Không có thay đổi thuật toán khoa học trong RC11. Khả năng định tuyến thủy văn
và hiệu quả GNN chưa được xác nhận bởi các kiểm thử giao diện/extract này.
Giấy phép ứng dụng: MIT; thông báo giấy phép phụ thuộc được giữ trong gói.
