# MeshCherry 3.0.0 — tính năng và quy trình sử dụng

MeshCherry là ứng dụng desktop Windows để tạo, kiểm tra và sử dụng lưới GIS
tam giác hoặc lưới có cấu trúc. Ứng dụng xử lý dữ liệu cục bộ; Gmsh là chương
trình riêng do người dùng cài đặt. Bản 3.0 mở rộng workbench mesh-first từ các
chức năng của dòng 2.x bằng quy trình tạo lưới, kiểm tra, trích thuộc tính,
lưu dự án và xuất dữ liệu.

## Điểm mới của 3.0

- **Quy trình theo bước:** chọn dữ liệu và miền, cấu hình lưới, tạo và xem QA,
  sau đó tùy chọn trích raster lên hình học ô đã chấp nhận. Các tab Lưới, Extract
  và Nhật ký giúp theo dõi riêng hình học, thuộc tính và hoạt động nền.
- **Xem trước nguồn:** DEM GeoTIFF/ASC có thể được xem tại bước cấu hình relief;
  giao diện hiển thị CRS và kích thước pixel, đồng thời nêu rõ khi nguồn thiếu
  CRS. Ảnh xem trước được giảm mẫu và kéo giãn để hiển thị, không sửa raster.
- **Dự án `.mcy`:** lưu và mở hình học, thuộc tính, CRS, QA lịch sử và trạng thái
  xem. Dự án mở được ngoại tuyến để xem; không chạy lại Gmsh, không tự khôi phục
  dữ liệu đầu vào hay đường dẫn trên máy đã tạo dự án.
- **Xuất dữ liệu:** polygon ô cùng thuộc tính sang GeoJSON, hoặc raster hóa sang
  GeoTIFF / ASC kèm PRJ với độ phân giải do người dùng khai báo. ASC được đóng
  trong ZIP cùng tệp PRJ.
- **Thao tác và phục hồi:** preflight có lỗi hiển thị tại nơi cần xử lý; công
  việc nền có thể dừng và phục hồi kết quả theo request. Kết quả chưa hoàn tất
  không được trình bày như một lượt extract thành công.

## Dữ liệu và hình học được hỗ trợ

- **Miền tạo lưới:** footprint affine ngoài của raster GeoTIFF hoặc ESRI ASCII
  Grid; hình chữ nhật trong CRS projected có đơn vị mét; hoặc bộ shapefile
  Polygon 2D gồm `.shp`, `.shx`, `.dbf`, `.prj`. Polygon có thể có lỗ hoặc nhiều
  phần. Bộ shapefile được dùng để xác định miền, không tự sửa hình học hay xác
  nhận đó là ranh giới lưu vực.
- **Raster:** GeoTIFF và ASC. Vai trò ROI, DEM dùng cho sizing, raster tham chiếu
  độ phân giải và raster dùng để trích thuộc tính được cấu hình riêng. ASC cần
  CRS được khai báo, thường qua `.prj` cùng tên. CRS nguồn và đơn vị phải rõ;
  ứng dụng không thay phép chuyển tọa độ bằng thao tác gán nhãn CRS.
- **Structured grid:** tạo các ô vuông/chữ nhật theo extent và kích thước ô;
  có chính sách strict hoặc clip cho hàng/cột biên. Giới hạn lịch sử cho dạng
  lưới này và config cũ là 1.000 ô.
- **Unstructured triangular mesh:** tạo qua Gmsh cài ngoài hoặc nhập mesh JSON
  đã có. Mesh nhập dùng XY làm hình học phẳng; Z nếu có không được diễn giải
  thành cao độ DEM. Config v2 cho mesh tạo, nhập và ô đã lưu hỗ trợ đến 100.000
  ô trong giới hạn tài nguyên. Giới hạn này không phải cam kết tốc độ.
- **Kiểm tra mesh:** kiểm tra cấu trúc và chất lượng hình học, đối chiếu miền,
  vùng phủ, lỗ và tính nhất quán dữ liệu theo loại đầu vào. Chấm QA đạt chỉ xác
  nhận các kiểm tra đã thực hiện; không chứng minh graph routing, thủy văn hoặc
  độ chính xác GNN.

Gmsh hỗ trợ qua executable 4.x do người dùng chọn; kiểm thử tích hợp tập trung
vào 4.15.2, nên không khẳng định mọi bản 4.x có hành vi giống nhau. Ứng dụng
không âm thầm chuyển sang thuật toán khác khi Gmsh thiếu hoặc lỗi.

## Trích thuộc tính lên ô

Extract dùng raster và hình học ô bất biến đã tạo hoặc nhập; đây là bước tùy
chọn sau khi có mesh, không phải chức năng tạo mesh từ raster. Giá trị được tổng
hợp bằng diện tích giao giữa pixel nguồn và ô, với phương pháp tùy theo ý nghĩa
dữ liệu:

- **Intensive / liên tục:** trung bình theo diện tích giao; kết quả kèm min, max
  và độ lệch chuẩn trên phần dữ liệu hợp lệ.
- **Extensive / tổng lượng:** phân bổ theo phần diện tích giao và bảo toàn tổng
  trên phần hỗ trợ hợp lệ.
- **Categorical / phân loại:** lưu tỷ lệ diện tích của từng lớp trong mỗi ô.

Coverage của footprint và coverage hợp lệ được báo riêng. NoData không được
đổi thành zero. Raster được đọc mà không ghi đè; CRS, band và metadata nguồn
được kiểm tra. Phép chuyển CRS có chia đoạn cạnh để xấp xỉ hình học pixel ở CRS
khác; đây không phải bảo đảm hội tụ hay chứng nhận độ chính xác datum. Kết quả
extract mô tả phép biểu diễn raster trên các ô, không phải độ chính xác thủy
văn.

## Hai phương pháp relief có ý nghĩa khác nhau

- **Local relief:** tùy chọn exploratory trên raster điều khiển 33×33, đo biên
  độ max–min quanh mỗi điểm điều khiển để tạo size field trong quy trình Gmsh.
  Field này không có bộ giới hạn gradation của SF-1.
- **SF-1:** hồ sơ nghiên cứu DEM-only với DEM vuông north-up 30 m, CRS mét tương
  thích ROI, cao độ khai báo theo mét và tối đa 2 triệu pixel. Hồ sơ sử dụng
  hai predictor relief 5×5 và 15×15 pixel (150 m và 450 m), chuẩn hóa riêng
  theo P25/P95 rồi lấy giá trị lớn hơn để tạo target size 250–1.000 m. Slope
  không phải predictor sizing của profile app này. Scale phải được khai báo;
  giá trị giao diện điền sẵn không phải hiệu chuẩn tự động. Hồ sơ không dùng
  dòng chảy, outlet, hydrography hay routing để điều khiển mesh.

SF-1 và local relief là các cách tạo kích thước lưới, không phải bằng chứng rằng
lưới thích nghi cải thiện mô phỏng lũ, watershed delineation hoặc kỹ năng GNN.

## So sánh lưới và chỉ số

Công cụ comparison đối chiếu mesh tam giác đã chấp nhận với lưới vuông được cắt
theo đúng hợp hình học tam giác, giữ lỗ và các phần rời. Hai phía dùng cùng
raster, band và miền hỗ trợ hợp lệ. Báo cáo gồm số ô (được dùng như số node cho
phép so sánh), chênh lệch có dấu, phần trăm thay đổi, cùng chỉ số tùy loại dữ
liệu:

- **Intensive:** RMSE, MAE và bias tích phân theo diện tích giao. Các chỉ số mô
  tả sai lệch trường giá trị từng ô trên cùng miền; chúng không phải RMSE của
  raster được dựng lại bằng trung bình pixel.
- **Extensive:** đối chiếu bảo toàn tổng.
- **Categorical:** đối chiếu tỷ phần diện tích lớp trên toàn miền và mức đồng
  thuận giữa lớp đại diện của ô với raster theo diện tích. Đây là khả năng biểu
  diễn, không phải độ chính xác phân loại thực địa.

Kích thước ô vuông là lựa chọn của người dùng nên số ô vuông là một ngân sách
đối chiếu, không phải so sánh chi phí tính toán được cân bằng tự động. Các chỉ
số này không chứng minh chất lượng mô hình thủy văn.

## Lưu dự án và xuất

- `.mcy` lưu snapshot có giới hạn kích thước, gồm hình học, thuộc tính, cấu hình,
  QA lịch sử và trạng thái xem. Khi mở, hình học/CRS được kiểm tra lại; QA cũ
  vẫn được ghi là lịch sử, không biến thành QA mới. Để chạy extract mới, chọn
  lại raster nguồn.
- **GeoJSON:** polygon của từng ô và thuộc tính; hình học chuyển sang WGS 84
  theo RFC 7946.
- **GeoTIFF / ASC:** raster một band trong CRS phân tích. Người dùng nhập độ
  phân giải dương theo mét; mỗi pixel lấy mẫu bằng phép kiểm tra tâm pixel
  thuộc ô, có giới hạn một triệu pixel. Đây là raster hóa xấp xỉ, không bảo
  toàn diện tích hay tổng lượng. Giá trị null được ghi thành NoData; sentinel
  xung đột với giá trị dữ liệu sẽ bị từ chối hoặc cần chọn giá trị thay thế.
  Extensive totals không được
  raster hóa để tránh nhân lặp tổng.
- **CSV / JSON, MSH và mesh JSON:** có sẵn khi lượt chạy đã tạo các artifact
  tương ứng. MSH/mesh JSON giữ mesh; export GeoJSON biểu diễn polygon ô và
  thuộc tính.

## Quy trình gợi ý

1. Chọn ROI hoặc miền hình chữ nhật; chọn DEM riêng nếu dùng relief/SF-1.
2. Chọn structured grid, mesh tam giác nhập, hoặc tạo mesh qua Gmsh; khai báo
   CRS, đơn vị và tham số cần thiết.
3. Chạy kiểm tra cấu hình, tạo lưới và đọc QA/nhật ký trước khi tiếp tục.
4. Tùy chọn chạy extract raster trên các ô đã chấp nhận, chọn band và kiểu dữ
   liệu phù hợp.
5. Lưu `.mcy` để chia sẻ snapshot, hoặc xuất vector/raster theo mục đích.

## Windows và giấy phép

- Bản portable dành cho Windows x64; cần Microsoft Edge WebView2 Evergreen
  Runtime. Python được đóng gói trong ứng dụng.
- Giải nén toàn bộ gói và giữ `MeshCherry.exe` cùng thư mục `_internal`; chạy
  từ thư mục có quyền ghi. Không chỉ chép riêng EXE.
- Gmsh không nằm trong gói; cài riêng từ nguồn Gmsh chính thức và chọn trong
  Thiết lập Gmsh. Dữ liệu nghiên cứu cá nhân, raster đầu vào và executable
  Gmsh không được đóng gói cùng ứng dụng.
- Ứng dụng dùng MIT License; giữ các thông báo giấy phép của thư viện phụ thuộc
  và font trong gói phát hành.
- EXE Windows chưa ký số. Không tắt cơ chế bảo vệ Windows để chạy ứng dụng.

## Giới hạn xác minh phát hành

Kiểm thử mã nguồn, kiểm tra gói và smoke test executable không thay thế nghiệm
thu tương tác trong cửa sổ Windows thực tế, hộp thoại native, Explorer hoặc
kiểm tra trên máy sạch. Những cổng này chưa được ghi nhận là đã đạt trong bằng
chứng RC11 hiện có. Vì vậy tài liệu mô tả phạm vi tính năng, không tự thân xác
nhận các cổng nghiệm thu đó. Không có tuyên bố về lợi ích định tuyến thủy văn,
độ chính xác lưu vực hay hiệu quả GNN. MIT là giấy phép phần mềm; tài liệu này
không khẳng định đáp ứng thời hạn, điều kiện hay tiêu chí riêng của chương trình
ưu đãi OSS bên thứ ba.
