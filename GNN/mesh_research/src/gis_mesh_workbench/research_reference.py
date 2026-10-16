"""Portable historical facts from the retained GNN study, never live-run results."""
from copy import deepcopy

SF1 = {
    'schema_version': 'gnn-sf1-reference-v1',
    'title': 'GNN · SF-1: thích nghi theo biến thiên địa hình',
    'run_id': '20260929T083541306216Z-1a1decf5',
    'source': {
        'summary_path': 'GNN/mesh_runs/20260929T083541306216Z-1a1decf5/summary.json',
        'summary_sha256': 'ede9d9d2df28c8f45bd0a3d96c19a7ec32753bdca290883718db1e7f6efd85f2',
        'evidence_kind': 'historical retained GNN experiment; snapshot checked 2026-10-15',
    },
    'method': {
        'input': 'DEM nguồn 30 m; chỉ báo là biến thiên lân cận',
        'predictor': 'max(relief150 chuẩn hóa, relief450 chuẩn hóa)',
        'windows_m': [150, 450], 'normalization_percentiles': [25, 95],
        'h_bounds_m': [250, 1000], 'gamma': 2, 'gradation_ratio': 1.5,
        'gradation_reference_distance_m': 250,
        'formula': 'R = max(z) − min(z) trong mỗi cửa sổ; C = max(C150, C450); h_raw = 1000 − 750 × C² (m)',
        'native_pixel_m': 30, 'control_spacing_m': 120,
        'historical_calibrated_scale': 1.0905077326652577,
    },
    'baseline': {'case_id': 'uniform-500m', 'cell_count': 13515, 'node_count': 13515},
    'selected': {'case_id': 'relief-g2p0-r1p5', 'cell_count': 13543, 'node_count': 13543},
    'metrics': {
        'high_elevation_rmse_m': {'baseline': 25.316442208815346, 'selected': 18.325179466386494},
        'high_slope_rmse_degrees': {'baseline': 6.073299846133363, 'selected': 5.4872838464970535},
    },
    'interpretation': [
        'Kết quả lịch sử so sánh tam giác đều và tam giác thích nghi trên ROI4, gần cùng ngân sách node.',
        'DEM cung cấp dữ liệu; local relief đo mức biến thiên không gian để phân bổ độ mịn.',
        'SF-1 dùng thêm 28 node; cải thiện ở đây là biểu diễn địa hình trên vùng phức tạp.',
        'So sánh với lưới vuông được tính riêng trong công cụ So sánh lưới.',
        'Các chỉ số này chưa xác nhận chất lượng dự báo, routing hay hiệu quả huấn luyện GNN.',
        'Hồ sơ chia mới trong app cần cấu hình và kiểm chứng riêng; hệ số hiệu chỉnh cũ chỉ thuộc thí nghiệm ROI4 đã lưu.',
    ],
    'app_method': {
        'legacy_relief': 'one-window exploratory 33×33 control field; no physical gradation limiter',
        'sf1_profile': 'native30m two-window relief; fixed h250–1000/gamma2/r1.5; explicit scale; no budget calibration',
        'roi_policy': 'preserve the original selected ROI and holes; no boundary simplification',
    },
}


def sf1_reference():
    report = deepcopy(SF1)
    for metric in report['metrics'].values():
        metric['reduction_percent'] = 100 * (1 - metric['selected'] / metric['baseline'])
    return report
