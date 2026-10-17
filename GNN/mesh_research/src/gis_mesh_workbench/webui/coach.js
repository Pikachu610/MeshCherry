'use strict';
// Guidance never executes scientific actions or changes completion state.
const coach=(()=>{
  let dismissed=false;
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
    document.getElementById('coach-hide').onclick=()=>{dismissed=true;document.getElementById('coach').hidden=true;document.querySelectorAll('.tour-target,.tour-node').forEach(e=>e.classList.remove('tour-target','tour-node'));};
  }
  return {start,update,setup};
})();
