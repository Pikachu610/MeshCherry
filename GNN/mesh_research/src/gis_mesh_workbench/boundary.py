"""Strict SHP polygon-domain input, independent of research preprocessing."""
from pathlib import Path
import hashlib
import json
import math
import rasterio
import shapefile
from rasterio.crs import CRS
from rasterio.warp import transform_geom
from shapely.geometry import shape, mapping, Polygon
from shapely.ops import unary_union
from .extraction import _digest
from .spatial import metric_crs
from .workload_limits import MAX_BOUNDARY_POINTS, HIGH_DETAIL_NOTICE_POINTS


def _reject_zero_length_edges(coordinates, context):
    for edge_index, (first, last) in enumerate(zip(coordinates, coordinates[1:]), 1):
        if first[0] == last[0] and first[1] == last[1]:
            raise ValueError(
                f'{context}: cạnh độ dài 0 giữa vertex {edge_index} và vertex {edge_index + 1}; '
                'giữ nguyên hình học và sửa tệp nguồn')


def bundle_paths(path):
    path=Path(path)
    if not path.is_absolute() or path.suffix.lower()!='.shp': raise ValueError('Chọn tệp .shp tuyệt đối')
    base=path.parent.resolve(strict=True)
    result=[]
    for suffix in ('.shp','.shx','.dbf','.prj'):
        file=path.with_suffix(suffix)
        if not file.is_file(): raise ValueError('Thiếu tệp cùng tên: '+file.name)
        resolved=file.resolve(strict=True)
        if resolved.parent!=base: raise ValueError('SHP sidecar không được trỏ ra ngoài thư mục bộ tệp')
        limit=65536 if suffix=='.prj' else 16*1024*1024
        if resolved.stat().st_size>limit: raise ValueError('Bộ SHP vượt giới hạn kích thước')
        result.append(resolved)
    return tuple(result)


def read_boundary(path,analysis_crs):
    paths=bundle_paths(path); target=metric_crs(analysis_crs)
    before={p.name:_digest(p) for p in paths}
    try: source=CRS.from_wkt(paths[3].read_text(encoding='utf-8-sig'))
    except Exception as error: raise ValueError('Không đọc được CRS từ .prj; không tự gán CRS') from error
    if not source or not (source.is_projected or source.is_geographic): raise ValueError('SHP cần CRS ngang hợp lệ')
    geometries=[]; vertices=0; ring_count=0
    with shapefile.Reader(shp=str(paths[0]),shx=str(paths[1]),dbf=str(paths[2])) as reader:
        if reader.shapeType!=shapefile.POLYGON: raise ValueError('Chỉ hỗ trợ SHP Polygon 2D; không dùng điểm/đường/Z/M')
        if not 1<=reader.numRecords<=100: raise ValueError('SHP cần 1–100 polygon records')
        for record_index, record in enumerate(reader.iterShapes(), 1):
            vertices+=len(record.points)
            if not all(math.isfinite(v) for point in record.points for v in point): raise ValueError('SHP có tọa độ không hữu hạn')
            starts=list(record.parts)+[len(record.points)]
            rings=[list(record.points[a:b]) for a,b in zip(starts,starts[1:])]
            ring_count+=len(rings)
            projected_count=(vertices-ring_count)*8+ring_count if source!=target else vertices
            if projected_count>MAX_BOUNDARY_POINTS:
                raise ValueError(f'Ranh giới có {vertices:,} điểm nguồn; số điểm xử lý vượt ngân sách {MAX_BOUNDARY_POINTS:,}. Giữ nguyên tệp; lượt này chưa chạy được trong ngân sách hiện tại.')
            if not rings or len(rings)>200: raise ValueError('SHP cần 1–200 rings mỗi record')
            shells=[];holes=[]
            for ring_index, ring in enumerate(rings, 1):
                if len(ring)<4 or ring[0]!=ring[-1]: raise ValueError('Ring SHP chưa đóng')
                _reject_zero_length_edges(ring, f'SHP record {record_index}, ring {ring_index}')
                polygon=Polygon(ring)
                if not polygon.is_valid or polygon.area<=0: raise ValueError('Ring SHP không hợp lệ')
                signed=sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(ring,ring[1:]))
                (shells if signed<0 else holes).append(polygon)
            if not shells or any(not any(s.contains(h) and not s.boundary.intersects(h.boundary) for s in shells) for h in holes):
                raise ValueError('Hướng vòng SHP sai hoặc lỗ nằm ngoài/chạm shell; không tự sửa')
            original=shape(record.__geo_interface__)
            if original.is_empty or not original.is_valid: raise ValueError('Polygon SHP không hợp lệ; không tự sửa hình học')
            if source.is_geographic and original.bounds[2]-original.bounds[0]>180:
                raise ValueError('Không hỗ trợ ranh giới vượt kinh tuyến đổi ngày')
            geom=mapping(original)
            if source!=target:
                def densify(ring):
                    return [(a[0]+(b[0]-a[0])*i/8,a[1]+(b[1]-a[1])*i/8)
                        for a,b in zip(ring,ring[1:]) for i in range(8)]+[ring[-1]]
                coords=geom['coordinates']
                geom=dict(type=geom['type'],coordinates=[densify(r) for r in coords] if geom['type']=='Polygon'
                    else [[densify(r) for r in polygon] for polygon in coords])
            geometry=shape(transform_geom(source,target,geom,precision=-1))
            if geometry.is_empty or not geometry.is_valid or geometry.geom_type not in {'Polygon','MultiPolygon'}:
                raise ValueError('Polygon không hợp lệ sau chuyển CRS')
            if not all(math.isfinite(v) for v in geometry.bounds): raise ValueError('Chuyển CRS tạo tọa độ không hữu hạn')
            geometries.append(geometry)
        if len(geometries)!=reader.numRecords: raise ValueError('Số record SHP/DBF không khớp')
    domain=unary_union(geometries)
    if domain.geom_type not in {'Polygon','MultiPolygon'} or not domain.is_valid or domain.area<=0:
        raise ValueError('Miền SHP không hợp lệ')
    components=[domain] if domain.geom_type=='Polygon' else list(domain.geoms)
    if len(components)>20: raise ValueError('Miền vượt 20 thành phần')
    if {p.name:_digest(p) for p in paths}!=before: raise ValueError('Bộ SHP thay đổi trong khi đọc')
    transformed_vertices=sum(len(p.exterior.coords)+sum(len(r.coords) for r in p.interiors) for p in components)
    high_detail=max(vertices,transformed_vertices)>HIGH_DETAIL_NOTICE_POINTS
    return domain,dict(kind='shapefile',path=str(paths[0]),source_crs=source.to_string(),source_crs_wkt=source.to_wkt(),
        analysis_crs_wkt=target.to_wkt(),files_sha256=before,area_m2=domain.area,
        bundle_sha256=hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
        source_records=len(geometries),source_rings=ring_count,
        transformed_rings=sum(1+len(p.interiors) for p in components),
        library_versions=dict(rasterio=rasterio.__version__,gdal=rasterio.__gdal_version__,proj=rasterio.__proj_version__),
        bounds=list(domain.bounds),components=len(components),holes=sum(len(p.interiors) for p in components),
        source_vertices=vertices,transform_subdivisions=8 if source!=target else 1,
        simplification_applied=False,boundary_policy='all source records read without simplification; meshing domain is their union; internal seams can disappear',
        high_detail=high_detail,
        warnings=([f'Ranh giới có {vertices:,} điểm nguồn, {transformed_vertices:,} điểm sau đổi CRS. Gmsh giữ nguyên đường biên và có thể chạy lâu.'.replace(',','.')]
                  if high_detail else []),
        transformed_vertices=transformed_vertices,
        axis_policy='GIS X,Y; linear source-coordinate subdivisions, not geodesic',
        transformation='linear source edges subdivided 8 times when CRS changes, then transformed',
        physical_boundary_status='UNVERIFIED')


def geometry_lines(domain):
    lines=[]; point_tag=0; line_tag=0; loop_tag=0; surfaces=[]
    polygons=[domain] if domain.geom_type=='Polygon' else list(domain.geoms)
    for surface,polygon in enumerate(polygons,1):
        loops=[]
        for ring_index, ring in enumerate((polygon.exterior,*polygon.interiors), 1):
            coordinates=list(ring.coords)
            _reject_zero_length_edges(coordinates, f'Gmsh surface {surface}, ring {ring_index}')
            tags=[]
            for x,y in coordinates[:-1]:
                point_tag+=1;tags.append(point_tag)
                lines.append(f'Point({point_tag})={{{x:.17g},{y:.17g},0}};')
            edges=[]
            for first,last in zip(tags,tags[1:]+tags[:1]):
                line_tag+=1;edges.append(line_tag);lines.append(f'Line({line_tag})={{{first},{last}}};')
            loop_tag+=1;loops.append(loop_tag)
            lines.append(f'Curve Loop({loop_tag})={{{",".join(map(str,edges))}}};')
        lines.append(f'Plane Surface({surface})={{{",".join(map(str,loops))}}};')
        surfaces.append(surface)
    return lines
