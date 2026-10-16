'use strict';
// Guidance never executes scientific actions or changes completion state.
const coach=(()=>{
  let dismissed=false,returnFocus=null;
  const help={
    'source-next':'Đọc thông tin nguồn và kiểm tra hệ tọa độ trước khi tiếp tục.',
    'raster':'GeoTIFF có thể xác định ROI. Với ROI từ SHP, chỉ cần chọn DEM nếu chia thích nghi theo local relief.',
    'extract-run':'Trích thuộc tính lên các ô của lưới đã lưu.',
    'dem-raster':'DEM cao độ dùng để tính local relief và kích thước ô. Raster extract chọn riêng sau khi lưới đã tạo.',
    'dem-band':'Band chứa cao độ. Không sử dụng band mã lớp hoặc tổng lượng làm DEM.',
    'gmsh-domain':'Chọn ranh giới miền chia lưới. Bộ SHP cần đủ .shp, .shx, .dbf và .prj cùng tên.',
    'gmsh-sizing':'Đều: cùng kích thước mục tiêu. Local relief: dùng biến thiên cao độ từ DEM để điều chỉnh kích thước; đây là field thử nghiệm.',
    'h-min':'Kích thước mục tiêu nhỏ nhất của tam giác, tính bằng mét.',
    'h-max':'Kích thước mục tiêu lớn nhất, hoặc kích thước mục tiêu chung khi chia đều.',
    'relief-radius':'Bán kính cửa sổ đo chênh lệch cao độ lớn nhất–nhỏ nhất. Chỉ dùng khi raster nguồn là DEM cao độ.',
    'cell-search':'Nhập ID ô, ví dụ cell-0, rồi nhấn Enter để xem thuộc tính.',
    'crs':'Hệ tọa độ dùng để tính khoảng cách và diện tích: phải projected và có đơn vị mét.',
    'width':'Chiều rộng mỗi ô theo mét trong CRS tính toán. Mẫu dùng 1 m.',
    'height':'Chiều cao mỗi ô theo mét. Mẫu 2 × 1 m sẽ có hai ô 1 × 1 m.',
    'semantics':'Intensive: trung bình diện tích; extensive: phân bổ tổng/pixel; categorical: tỷ lệ lớp.',
    'validate':'Kiểm tra cấu hình trước khi tạo lưới.',
    'run':'Chỉ chạy cấu hình vừa qua preflight. Khi đổi cấu hình, phải kiểm tra lại.',
    'fit':'Hiển thị toàn bộ lưới trong khung nhìn.',
    'show-nodes':'Hiển thị tâm ô bằng chấm đỏ.',
    'show-edges':'Hiện hoặc ẩn đường biên ô.',
    'csv':'Xuất bảng thuộc tính thật của lượt chạy. Bao gồm giá trị, đơn vị và coverage.',
    'json':'Xuất thuộc tính và thông tin nguồn gốc dữ liệu dạng JSON.',
    'tutorial-finish':'Chỉ hoàn thành sau khi đã xem cả hai ô mẫu 10 và 30.',
    'copy-output':'Sao chép đường dẫn hồ sơ chạy trên máy để mở kết quả.'
  };
  function start(){dismissed=false;}
  function update(state){
    document.querySelectorAll('.tour-target').forEach(e=>e.classList.remove('tour-target'));
    const panel=document.getElementById('coach');
    if(!panel)return;
    panel.hidden=!state.active||dismissed;
    if(panel.hidden)return;
    let task;
    if(state.busy)task=[4,'Chờ kết quả','Đang xử lý dữ liệu thật. Không đóng phần mềm trong lúc chạy.','#job-headline'];
    else if(state.stage===1)task=[1,'Kiểm tra dữ liệu mẫu','Metadata đã hiện bên trái. Xem CRS EPSG:32649, hai pixel 10/30 rồi bấm “Tiếp: chọn lưới”.','#source-next'];
    else if(state.stage===2)task=[2,'Chọn kích thước ô','Giữ chiều rộng và chiều cao 1 m, rồi bấm “Tiếp: kiểm tra”. Không cần sửa dữ liệu mẫu.','[data-go="3"]'];
    else if(state.stage===3&&!state.validated)task=[3,'Kiểm tra trước khi chạy','Bấm nút được đánh dấu. Chỉ khi cấu hình hợp lệ mới được chạy.','#validate'];
    else if(state.stage===3)task=[4,'Tạo lưới','Preflight đã đạt. Bấm “Tạo lưới” rồi xem hình học và chất lượng ô.','#run'];
    else if(state.stage===4&&!state.extracted)task=[5,'Trích thuộc tính','Lưới đã tạo. Mẫu GeoTIFF đã được chọn: bấm “Kiểm tra & extract” để trích giá trị 10 và 30 vào các ô.','#extract-run'];
    else if(state.stage===4&&state.inspected<2){
      const nodes=[...document.querySelectorAll('.cell-node')];
      const next=nodes.find(n=>!state.ids.has(n.dataset.id));
      task=[6+state.inspected,'Khám phá ô '+(state.inspected+1)+'/2','Bấm ô chưa xem, hoặc Tab vào bản đồ rồi dùng mũi tên để chuyển ô và Enter / Space để chọn. Giá trị hiện ở bảng “Ô đang chọn”.','#map'];
      if(next)next.classList.add('tour-node');
      document.querySelectorAll('.tour-node').forEach(n=>{if(n!==next)n.classList.remove('tour-node');});
    }else task=[8,'Hoàn thành bài mẫu','Bạn đã xem cả hai ô 10 và 30. Có thể tải CSV/JSON. Bấm “Kết thúc hướng dẫn” khi đã sẵn sàng. Coverage 100% chỉ nghĩa raster phủ đủ ô.','#tutorial-finish'];
    if(state.stage!==4||state.inspected===2)document.querySelectorAll('.tour-node').forEach(n=>n.classList.remove('tour-node'));
    document.getElementById('coach-count').textContent='NHIỆM VỤ '+task[0]+' / 8';
    document.getElementById('coach-title').textContent=task[1];
    document.getElementById('coach-text').textContent=task[2];
    const target=document.querySelector(task[3]);
    if(target)target.classList.add('tour-target');
  }
  function setup(){
    const tooltip=document.getElementById('hover-help');
    for(const [id,text] of Object.entries(help)){
      const el=document.getElementById(id);if(!el)continue;
      el.dataset.help=text;
      const show=()=>{returnFocus=el;tooltip.textContent=text;tooltip.hidden=false;el.setAttribute('aria-describedby','hover-help');};
      const hide=()=>{tooltip.hidden=true;el.removeAttribute('aria-describedby');};
      el.addEventListener('mouseenter',show);el.addEventListener('mouseleave',hide);
      el.addEventListener('focus',show);el.addEventListener('blur',hide);
    }
    document.getElementById('coach-hide').onclick=()=>{dismissed=true;document.getElementById('coach').hidden=true;document.querySelectorAll('.tour-target,.tour-node').forEach(e=>e.classList.remove('tour-target','tour-node'));};
    document.addEventListener('keydown',e=>{if(e.key==='Escape'){tooltip.hidden=true;if(returnFocus)returnFocus.removeAttribute('aria-describedby');}});
  }
  return {start,update,setup};
})();
