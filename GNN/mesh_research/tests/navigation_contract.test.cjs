const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const ui = path.join(__dirname, '..', 'src', 'gis_mesh_workbench', 'webui');
const html = fs.readFileSync(path.join(ui, 'index.html'), 'utf8');
const layoutCss = fs.readFileSync(path.join(ui, 'workspace_layout.css'), 'utf8');
const wizardCss = fs.readFileSync(path.join(ui, 'wizard.css'), 'utf8');
const scripts = ['app.js', 'coach.js', 'downloads.js', 'extraction_draft.js', 'quality_panel.js', 'quality_bridge.js', 'comparison_view.js', 'attribute_table.js']
  .map((file) => fs.readFileSync(path.join(ui, file), 'utf8'))
  .join('\n');

test('workspace navigation follows the task order and has one target per workspace', () => {
  const nav = html.match(/<nav class="workspace-nav"[\s\S]*?<\/nav>/)?.[0];
  assert.ok(nav, 'workspace navigation exists');

  const workspaces = [...nav.matchAll(/data-workspace="([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(workspaces, ['roi', 'mesh', 'compare', 'extract', 'log']);
  assert.equal(new Set(workspaces).size, workspaces.length);
});

test('static buttons have unique IDs and an explicit destination or action hook', () => {
  const buttons = [...html.matchAll(/<button\b[^>]*>/g)].map((match) => match[0]);
  const ids = buttons.flatMap((button) => [...button.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]));
  assert.equal(new Set(ids).size, ids.length, 'static button IDs are unique');

  const unboundMarkup = buttons.filter((button) =>
    !/\bid="/.test(button) && !/\bdata-(?:workspace|go|pick|extract-mode)=/.test(button),
  );
  assert.deepEqual(unboundMarkup, [], 'every button has an ID or delegated data destination');

  const unreferencedIds = ids.filter((id) => !scripts.includes(id));
  assert.deepEqual(unreferencedIds, [], 'every static button ID is referenced by a loaded UI script');
});

test('comparison assets are included once in the document head', () => {
  const head = html.match(/<head>[\s\S]*?<\/head>/)?.[0] || '';
  assert.equal((html.match(/href="\/comparison_view\.css"/g) || []).length, 1);
  assert.equal((html.match(/src="\/comparison_view\.js"/g) || []).length, 1);
  assert.match(head, /href="\/comparison_view\.css"/);
  assert.match(head, /src="\/comparison_view\.js"/);
});

test('file and tools menus own project and Gmsh actions without duplicate visible controls', () => {
  const fileMenu = html.match(/<details><summary>Tệp<\/summary>([\s\S]*?)<\/details>/)?.[1] || '';
  const toolsMenu = html.match(/<details><summary>Công cụ<\/summary>([\s\S]*?)<\/details>/)?.[1] || '';
  assert.equal((fileMenu.match(/id="project-open"/g) || []).length, 1);
  assert.equal((fileMenu.match(/id="project-save"/g) || []).length, 1);
  assert.equal((html.match(/id="gmsh-setup-open"/g) || []).length, 1);
  assert.match(toolsMenu, /id="gmsh-setup-open"/);
});

test('result switching uses one workspace nav and bounded regions chain scroll to the page', () => {
  assert.doesNotMatch(html, /class="result-tabs"|data-result-tab=/);
  assert.doesNotMatch(wizardCss, /\.result-tabs/);
  assert.match(html, /id="remesh-draft"/);
  assert.match(wizardCss, /body\[data-stage="4"\]\s+#mesh-quality\s*\{\s*max-height:\s*none;\s*overflow:\s*visible/i);
  assert.match(wizardCss, /#job-log\s*\{[^}]*max-height:\s*260px[^}]*overflow:auto[^}]*overscroll-behavior-y:auto/i);
  assert.match(wizardCss, /#monitor-details \.info\s*\{\s*max-height:180px;\s*overflow:auto;\s*overscroll-behavior-y:auto/i);
  assert.match(workspaceCssRule('body[data-workspace="extract"] #batch-queue'), /max-height:\s*min\(48vh, 520px\)[\s\S]*overflow-y:\s*auto[\s\S]*overscroll-behavior-y:\s*auto/);
});

function workspaceCssRule(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return layoutCss.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))?.[1] || '';
}
