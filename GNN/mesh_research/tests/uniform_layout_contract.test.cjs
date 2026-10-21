const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const webui=path.join(__dirname,'../src/gis_mesh_workbench/webui');
const html=fs.readFileSync(path.join(webui,'index.html'),'utf8');
const css=fs.readFileSync(path.join(webui,'workspace_layout.css'),'utf8');

test('Gmsh layout supports all sizing modes without reserving an empty preview column',()=>{
  for(const sizing of ['uniform','relief','sf1_relief']){
    assert.match(html,new RegExp(`<option value="${sizing}">`),`${sizing} sizing option remains available`);
  }

  assert.ok(/#gmsh-options:has\(#gmsh-sizing option\[value="uniform"\]:checked\)\s*>\s*\.configuration-group/.test(css),
    'uniform mode places the executable panel beside its controls at desktop widths');
  assert.ok(/#gmsh-options:has\(#gmsh-sizing option\[value="uniform"\]:checked\)\s*>\s*\.dem-workbench-columns/.test(css),
    'uniform mode places the controls wrapper beside the executable panel');
  const compactBreakpoint=css.slice(css.indexOf('@media (max-width: 1000px)'));
  assert.ok(/#gmsh-options:has\(#gmsh-sizing option\[value="uniform"\]:checked\)\s*>\s*\.configuration-group,\s*body\[data-workspace="mesh"\] #gmsh-options:has\(#gmsh-sizing option\[value="uniform"\]:checked\)\s*>\s*\.dem-workbench-columns\s*\{\s*grid-column:\s*1\s*\/\s*-1/s.test(compactBreakpoint),
    'uniform mode stacks its two panels together below the desktop breakpoint');
  assert.ok(/\.dem-workbench-columns:has\(>\s*#dem-preview\[hidden\]\)\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/s.test(css),
    'any sizing mode with no visible DEM preview uses one controls column');
  assert.ok(/#gmsh-options:has\(#gmsh-sizing option\[value="uniform"\]:checked\)\s+#parameter-preview\s*\{\s*display:\s*none\s*;/s.test(css),
    'only uniform sizing hides the redundant parameter explanation');
  assert.ok(html.includes('id="h-max-help"')&&html.includes('Giảm kích thước thường làm tăng số ô và thời gian chạy.'),
    'the direct edge-size guidance and final cell-count warning remain available');
});
