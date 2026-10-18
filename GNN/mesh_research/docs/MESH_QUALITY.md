# MeshCherry: kiểm tra chất lượng lưới

## Quy trình

1. Tạo hoặc mở một lưới đã được chấp nhận.
2. Mở **Chất lượng lưới**, điều chỉnh ngưỡng cảnh báo rồi chạy đánh giá.
3. Xem tổng quan, phân bố, các ô xấu nhất và danh sách vấn đề. Chọn chỉ số để tô màu lưới; chọn ô để định vị.
4. Xuất JSON, bộ CSV hoặc báo cáo HTML ngoại tuyến. Lưu `.mcy` để giữ kết quả QA.

Đánh giá dùng bản chụp lưới hiện tại; không gọi Gmsh, không thay đổi tọa độ hoặc ID ô.
Nút Dừng áp dụng cho tác vụ QA đang chạy. Ngưỡng cảnh báo chỉ dùng đánh giá;
không thay đổi kiểm tra chấp nhận hình học trong quá trình tạo lưới.

## Chỉ số

Với cạnh tam giác có độ dài a, b, c; diện tích A; Lmax là cạnh dài nhất:

- `A = abs(cross(B-A,C-A))/2`, đơn vị m² khi CRS dùng mét.
- Góc tính bằng `atan2(abs(cross), dot)`, đơn vị độ.
- `AR = Lmax²/(2A)`, không thứ nguyên; tam giác đều: 2/√3.
- `Q = 16A²/((a+b+c)abc) = 2r/R`, không thứ nguyên; tam giác đều: 1.
- `Lmax/h`: tỷ số cạnh dài nhất với trường kích thước mục tiêu lấy tại tâm ô.

`h` của Gmsh là chiều dài đặc trưng mục tiêu. Vì vậy `Lmax/h` là chẩn đoán;
không diễn giải như bảo đảm cạnh tối đa. Chỉ trường được khai báo rõ là
`target_maximum_edge_length` mới được đọc theo quy ước cạnh tối đa.
Không suy ngược h từ cạnh thực tế. Thiếu bằng chứng trường gốc: N/A.

Ô suy biến/không hợp lệ không được thay bằng Q=0. Giá trị không xác định là
null, có lý do; thống kê loại các quan sát không hợp lệ và báo số loại.
Chỉ số tam giác là N/A đối với ô tứ giác.

## Hình học, topology và ROI

- Kiểm tra ID trùng, tọa độ không hữu hạn, phần tử trùng, cạnh có hơn hai ô,
  T-junction, thành phần liên thông và giao nhau diện tích dương.
- Giao nhau và tìm đỉnh trên cạnh dùng chỉ mục không gian, có giới hạn ứng viên.
  Vượt giới hạn phải báo đánh giá chưa hoàn tất.
- Định dạng ô hiện tại lưu ID ô/tọa độ, không lưu tag node gốc. Topology dựng từ
  tọa độ phải được phân biệt với connectivity gốc; orphan node gốc là N/A.
- ROI chỉ lấy từ bằng chứng miền gốc được giữ và khớp hash. Đánh giá cả lỗ và
  multipart bằng diện tích thiếu, thừa, symmetric difference, coverage.
- Khoảng cách biên là Hausdorff đối xứng **rời rạc, densify** theo GEOS, không
  phải nghiệm khoảng cách liên tục chính xác. Có giới hạn mẫu.
- Độ phù hợp với ROI đầu vào không chứng minh độ chính xác vị trí ngoài thực địa.

## Đo thời gian và bộ nhớ

Đo lặp QA chỉ chạy khi người dùng yêu cầu; mặc định 5 lượt. Đây là benchmark
đánh giá cùng bản chụp lưới, không phải benchmark tạo lại lưới Gmsh. Thời gian
generation trong benchmark này là N/A. Hàm benchmark hỗ trợ callback generator
cho chương trình nghiên cứu riêng, có thời gian generation/QA/pipeline tách biệt.
Thông tin máy, cấu hình, số lượt/warm-up và mesh hash được lưu cùng kết quả.

Bộ nhớ là RSS đồng thời của tiến trình Python và các tiến trình con, lấy mẫu khi
có psutil; không cộng các đỉnh riêng rẽ. Thiếu công cụ đo: N/A. Giá trị lấy mẫu
có thể bỏ lỡ đỉnh rất ngắn. Thời gian tạo lưới đã đo trong manifest được giữ riêng,
không dùng tổng thời gian cũ để giả làm thời gian generation.

Môi trường mã nguồn có thể cài phần đo RSS bằng `pip install -e ".[qa]"`.
Benchmark trong giao diện đo phần QA hình học/topology; đối chiếu ROI và trường
kích thước chỉ chạy một lần trong báo cáo chính, không nằm trong thời gian lặp.

Ví dụ chạy benchmark ngoại tuyến:

```powershell
python scripts/benchmark_mesh_quality.py project.mcy --repetitions 5 --output benchmark.json
```

## Lưu trữ và giới hạn

QA là cache tùy chọn trong `qa.json` của `.mcy`, giữ tương thích dự án cũ.
Cache chỉ hiện là current khi mesh/config/phiên bản định nghĩa/chữ ký tham chiếu
khớp. Cache lịch sử không thay cho tái kiểm tra chấp nhận hình học khi mở dự án.
Giá trị cache do tệp dự án cung cấp không được ký xác thực; giao diện ghi rõ
“Kết quả đã lưu · Chưa chạy lại QA”. Chạy QA mới nếu cần xác nhận lại các metric.
Giới hạn: 100.000 ô, 250.000 node gốc nếu có; tối đa 5 triệu ứng viên;
bảng phân trang tối đa 200 dòng; xuất QA tối đa 64 MiB.

Chưa có bằng chứng thì không kết luận độ chính xác solver, hiệu quả GNN,
throughput cho dữ liệu nghiên cứu lớn hoặc chất lượng hiển thị native Windows.
Định dạng cell-coordinate không cho phép phục hồi tag node gốc bị mất.
Trường SF-1 Structured cần kiểm chứng quy ước nội suy trước khi báo Lmax/h.

## Nguồn định nghĩa

- [Gmsh 4.15.2 manual](https://www.gmsh.info/doc/texinfo/gmsh.html): mesh sizing và radius-ratio quality.
- [Shapely Hausdorff distance](https://shapely.readthedocs.io/en/2.1.2/reference/shapely.hausdorff_distance.html): discrete Hausdorff và densify.
