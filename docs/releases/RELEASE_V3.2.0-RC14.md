# MeshCherry 3.2 RC14 — Cherry Horizon 🍒

**Mở rộng không gian cho dữ liệu nghiên cứu.**

## Mới: Nghiên cứu dữ liệu lớn

Trong **So sánh → Đánh giá sai số raster**, chế độ **Nghiên cứu** được chọn mặc định:

- Không đặt trần tổng pixel nguồn, tổng pixel đọc cộng dồn, tổng cặp giao hoặc số cửa sổ quét của tác vụ đánh giá sai số.
- Vẫn đọc theo cửa sổ tối đa 65.536 pixel; giữ kiểm tra dữ liệu, toàn vẹn đầu vào, tiến độ và nút Dừng.
- Tiền kiểm tra dùng metadata, không quét toàn bộ raster hay dựng chỉ mục hình học trong lúc giữ khóa giao diện.
- Có thể chọn lại chế độ giới hạn công việc cũ. Cách tính sai số và cách đếm phần tử hình học không đổi.

## Mới: Yêu cầu hệ thống ngay trong app

Mở **Trợ giúp → Yêu cầu hệ thống & dữ liệu lớn** để xem điều kiện chạy bản Windows x64, WebView2, quyền ghi workspace, Gmsh riêng và khuyến nghị tài nguyên.

RAM 8 / 16 / 32 GiB trở lên là hướng dẫn tham khảo theo quy mô, **chưa phải cấu hình RAM tối thiểu được chứng nhận bằng benchmark**. CPU/GPU, RAM và dung lượng đĩa cần thiết phụ thuộc bài toán; không bảo đảm mọi kích thước dữ liệu sẽ hoàn thành.

## Giữ các cải tiến RC13

- DEM tham chiếu tùy chọn khi chia đều, không làm đổi thuật toán chia đều.
- Bốn khung hình tổng kết thẳng hàng; thanh màu có cột riêng.
- So sánh số phần tử tam giác với pixel hình học; nhãn Giảm/Tăng tách khỏi số đỉnh và độ chính xác thuộc tính.
- Trích một/nhiều raster, bảng xem trước, xuất dữ liệu và dự án .mcy tiếp tục được giữ.

## Giới hạn cần biết

**Đây không phải “toàn app không giới hạn” hoặc một tuyên bố tăng tốc.** Giới hạn phần tử lưới/ô mục tiêu 100.000, tích lũy phân loại, lô hình học và cache vẫn còn. Lựa chọn Nghiên cứu chỉ áp dụng đánh giá sai số so sánh, không tự gỡ giới hạn của tạo lưới, SF-1, Extract hoặc đếm hình học. Target-grid và dữ liệu tích lũy vẫn dùng RAM; tác vụ lớn có thể chậm hoặc hết tài nguyên. Dừng là hợp tác, chưa chứng nhận độ trễ tối đa.

## Kiểm chứng và tải về

- 72 kiểm tra tích hợp Python và 154 kiểm tra Node đạt; ChatGPT đã phản biện mã và gói trong phạm vi ghi nhận.
- 88/88 thành phần đóng gói khớp mã nguồn; chính EXE RC14 chạy self-test đạt, gồm chế độ nghiên cứu mới và bảo vệ theo cửa sổ.
- Raster PET thực tế đã được chấp nhận ở bước metadata với cận trên 3.807.990 pixel đọc qua hai lượt. **Chưa chạy đánh giá sai số PET hoàn chỉnh**, chưa chứng nhận hiệu năng dữ liệu cực lớn, máy RAM thấp, nghiệm thu Windows/máy sạch hoặc độ chính xác thủy văn/GNN.

Tải ZIP Windows x64, giải nén **toàn bộ** rồi mở `MeshCherry.exe`. Giữ `_internal` cạnh EXE. Cần WebView2 Runtime; Gmsh 4.x cài riêng. EXE chưa ký số: không tắt bảo vệ Windows.

Xem SHA256SUMS.txt và VALIDATION.json để đối chiếu gói. Đây là bản RC dành cho tải thử; các bản phát hành trước được giữ nguyên.
