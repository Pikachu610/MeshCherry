"""Safe, actionable descriptions for known workbench run failures."""

from __future__ import annotations


_MAX_MESSAGE = 240
_MAX_ACTION = 180


def _bounded(value: str, limit: int) -> str:
    value = ' '.join(str(value).split())
    if len(value) <= limit:
        return value
    return value[:limit - 1].rstrip() + '…'


def describe_failure(error) -> dict:
    """Return a small user-facing report without exposing arbitrary exception text.

    When a run artifact exists, the original exception remains in its manifest.
    Unknown failures deliberately use generic copy because exception strings can
    contain local paths, credentials, or other user supplied values.
    """
    text = str(error or '').strip().lower()

    if 'cells overlap in positive area' in text:
        code = 'cell_overlap'
        title = 'Các ô lưới bị chồng lấn'
        message = 'Các ô lưới giao nhau trên phần diện tích dương; bước xử lý yêu cầu các ô không chồng lấn.'
        actions = [
            'Mở chi tiết QA và kiểm tra các ô có phần giao diện tích dương.',
            'Với lưới Gmsh, thử cấu hình khác cho smoothing hoặc thuật toán rồi chạy QA lại; với lưới nhập, kiểm tra hình học nguồn.',
        ]
    elif 'positive area' in text or 'non-positive area' in text or 'zero-area' in text:
        code = 'invalid_cell_area'
        title = 'Ô lưới có diện tích không hợp lệ'
        message = 'Có ô lưới suy biến hoặc không có diện tích dương.'
        actions = [
            'Kiểm tra tọa độ và thứ tự đỉnh của các ô; loại bỏ hoặc sửa các hình học suy biến.',
            'Xác nhận CRS và đơn vị tọa độ của dữ liệu nguồn trước khi chạy lại.',
        ]
    elif 'missing crs' in text or 'crs missing' in text or 'thiếu crs' in text:
        code = 'missing_crs'
        title = 'Thiếu hệ tọa độ'
        message = 'Không xác định được CRS của raster hoặc lưới đầu vào.'
        actions = ['Gán đúng CRS cho dữ liệu nguồn rồi chạy lại; không chỉ đổi nhãn CRS nếu chưa chuyển tọa độ.']
    elif 'scale/offset' in text:
        code = 'unsupported_raster_scale'
        title = 'Raster có scale hoặc offset chưa được hỗ trợ'
        message = 'Raster đầu vào có scale/offset khác mặc định nên giá trị đọc được chưa thể diễn giải an toàn.'
        actions = ['Kiểm tra scale/offset và tạo raster đầu vào có giá trị đã được áp dụng đúng.']
    elif 'gmsh' in text and any(marker in text for marker in ('not found', 'not configured', 'không tìm thấy', 'chưa cấu hình')):
        code = 'gmsh_unavailable'
        title = 'Chưa sẵn sàng Gmsh'
        message = 'Không thể khởi chạy Gmsh với cấu hình hiện tại.'
        actions = ['Kiểm tra đường dẫn executable và quyền chạy Gmsh trong cấu hình.']
    else:
        code = 'unknown_failure'
        title = 'Lần chạy chưa hoàn tất'
        message = 'Đã xảy ra lỗi trong quá trình xử lý. Xem chi tiết kỹ thuật của tác vụ để biết thông tin hiện có.'
        actions = ['Mở Chi tiết kỹ thuật trong giao diện để xem thông tin lỗi hiện có.']

    return {
        'code': code,
        'title': _bounded(title, 80),
        'message': _bounded(message, _MAX_MESSAGE),
        'actions': [_bounded(action, _MAX_ACTION) for action in actions[:3]],
    }
