# MeshCherry 3.0.0rc8 — source candidate

## Thay đổi

- Chọn nguồn và xem trước ở bước 1; người dùng bấm Tiếp để chọn lưới.
- Khi chuyển bước chủ động, cuộn tới đầu mục mới và đặt focus tại tiêu đề.
  Tôn trọng tùy chọn giảm chuyển động; phục hồi nền giữ nguyên vị trí xem.
- Sửa bộ lọc hộp thoại Windows cho DEM GeoTIFF/ASC; thông báo lỗi cấu hình
  chỉ rõ trường cần sửa, giữ nguyên miền chữ nhật khi tiếp tục.
- Công thức bằng MathML, dấu sao cho trường bắt buộc theo cấu hình;
  hướng dẫn theo yêu cầu thay cho các ghi chú tự bật khi hover.
- Lưu/mở dự án `.mcy` chứa kết quả, thuộc tính, CRS và trạng thái xem.
  Dự án mở lại không tự chạy Gmsh. Liên kết Explorer là lựa chọn chủ động
  qua các script đăng ký/gỡ liên kết trong thư mục packaging.
- Xuất GeoJSON WGS84, GeoTIFF và gói ASC/PRJ, bên cạnh CSV/JSON/MSH.
  Kết quả raster hóa dùng lưới pixel xuất do người dùng chọn.

## Kiểm chứng

- JavaScript: 83/83 kiểm thử đạt, gồm handler Tiếp thực tế, cuộn/focus,
  reduced motion, phục hồi nền và chặn chuyển bước khi đang chạy.
- Python trong workspace nghiên cứu có Gmsh: 280 kiểm thử, 278 đạt và 2 bị
  bỏ qua theo điều kiện môi trường. Checkout nguồn độc lập: 274 kiểm thử,
  258 đạt và 16 bị bỏ qua; một số lớp tích hợp cần Gmsh/hiện vật nghiên cứu
  ngoài repository. Hai số lượng khác nhau do lớp tích hợp được bỏ qua khi
  thiếu phụ thuộc ngoài. Gmsh không được đưa vào repository.
- Giao diện trình duyệt: từ cuối bước 1, bấm Tiếp đưa tiêu đề bước 2 lên
  gần đầu viewport (khoảng 22 px) và đặt focus đúng tiêu đề.
- Bản đóng gói Windows RC8 được tạo riêng để thử nghiệm cục bộ.

## Phạm vi và giới hạn

- Đây là cập nhật mã nguồn; các binary Releases cũ giữ nguyên.
- Kiểm thử nguồn/trình duyệt chưa xác nhận hoàn toàn hộp thoại Windows,
  biểu tượng và thao tác nhấp đúp Explorer hoặc khả năng chạy trên máy sạch.
- Gmsh cài riêng. Đăng ký `.mcy` chỉ áp dụng tài khoản Windows hiện tại và
  không ghi đè liên kết của ứng dụng khác.
- Không thay đổi phương pháp khoa học SF-1, kết quả nghiên cứu hay Phase 1.
- Giữ nguyên giấy phép MIT hiện có của repository.
