"""Bounded user guidance for comparison failures; raw diagnostics stay separate."""

from .failure_details import describe_failure


def describe_comparison_failure(error) -> dict:
    text = str(error or '').strip().lower()
    code = title = message = None
    actions = []
    if 'comparison read pixel limit exceeded' in text or 'comparison window scan limit exceeded' in text:
        code = 'comparison_read_limit'
        title = 'Vượt giới hạn đọc raster của lượt so sánh'
        message = 'Khối lượng đọc raster vượt giới hạn an toàn; đánh giá sai số có thể cần nhiều lượt đọc.'
        actions = ['Chọn Nghiên cứu dữ liệu lớn trong phần Đánh giá sai số để không áp dụng trần tổng lượt đọc; xem hướng dẫn tài nguyên trước khi chạy.',
                   'Hoặc chọn miền nhỏ hơn hoặc tạo một raster đã cắt theo miền cần đánh giá rồi chạy lại.',
                   'Có thể dùng Đếm nhanh để xem số ô mà không chạy đánh giá sai số.']
    elif 'source pixel limit exceeded' in text:
        code = 'comparison_source_limit'
        title = 'Raster nguồn vượt giới hạn an toàn'
        message = 'Raster nguồn có nhiều pixel hơn giới hạn được hỗ trợ cho tác vụ này.'
        actions = ['Tạo raster đã cắt theo miền cần đánh giá, kiểm tra lại CRS và NoData rồi chọn tệp mới.']
    elif any(marker in text for marker in ('comparison square bounding-grid cell limit exceeded', 'comparison connected square cell limit exceeded')):
        code = 'comparison_grid_limit'
        title = 'Lưới vuông đối chứng có quá nhiều ô'
        message = 'Số ô lưới vuông dự kiến vượt giới hạn an toàn.'
        actions = ['Tăng kích thước ô vuông đối chứng hoặc chọn miền nhỏ hơn rồi chạy lại.']
    elif 'comparison candidate pair limit exceeded' in text or 'comparison categorical accumulator limit exceeded' in text:
        code = 'comparison_overlap_limit'
        title = 'Khối lượng giao hình học vượt giới hạn'
        message = 'Số cặp giao hoặc dữ liệu tích lũy của lượt so sánh vượt giới hạn an toàn.'
        actions = ['Chọn miền nhỏ hơn để giảm khối lượng giao hình học; giữ nguyên loại thuộc tính đúng với dữ liệu.']
    elif 'comparison has no finite valid common raster support' in text:
        code = 'comparison_empty_support'
        title = 'Không có dữ liệu hợp lệ trên miền chung'
        message = 'Miền so sánh không có giá trị raster hợp lệ sau khi loại NoData, mask và giá trị không hữu hạn.'
        actions = ['Kiểm tra vị trí giao với lưới, CRS, band và NoData của raster; không thay NoData bằng 0 để ép chạy.']
    elif any(marker in text for marker in ('comparison input changed during computation', 'accepted mesh changed during inventory count', 'raster bundle changed during inventory count', 'lưới mốc đã thay đổi')):
        code = 'comparison_input_changed'
        title = 'Dữ liệu đầu vào đã thay đổi'
        message = 'Raster hoặc lưới mốc đã đổi trong lúc xử lý; kết quả chưa được áp dụng.'
        actions = ['Chọn lại dữ liệu hiện tại và chạy lại; không sửa tệp nguồn khi tác vụ đang chạy.']
    if code is None:
        return describe_failure(error)
    return dict(code=code, title=title, message=message, actions=actions)
