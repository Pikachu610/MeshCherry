import http.client
from pathlib import Path
import tempfile
import threading
import unicodedata
import unittest

from fontTools.ttLib import TTFont

from gis_mesh_workbench.webui import WorkbenchServer


PACKAGE = Path(__file__).parents[1] / 'src' / 'gis_mesh_workbench'
WEBUI = PACKAGE / 'webui'


class CompactVisualSystemTests(unittest.TestCase):
    def test_shared_font_is_offline_variable_vietnamese_source_sans(self):
        css = (WEBUI / 'workspace_layout.css').read_text(encoding='utf-8')
        font = WEBUI / 'fonts' / 'SourceSans3-VF.ttf'
        license_text = (WEBUI / 'fonts' / 'SourceSans3-OFL.txt').read_text(encoding='utf-8')
        self.assertTrue(font.is_file())
        self.assertGreater(font.stat().st_size, 500_000)
        self.assertIn('Source Sans 3', css)
        self.assertIn('SourceSans3-VF.ttf', css)
        self.assertIn('font-weight: 200 900', css)
        self.assertIn('Open Font License', license_text)
        parsed_font = TTFont(font)
        cmap = set().union(*(table.cmap.keys() for table in parsed_font['cmap'].tables))
        specimen = 'Tiếng Việt: ắ ấ ồ ệ ỹ Đ; Ω μ ° Δ ✓ ± →'
        missing = {ord(char) for char in unicodedata.normalize('NFD', specimen)
                   if not char.isspace() and ord(char) not in cmap}
        self.assertEqual(missing, set(), 'The bundled font must cover decomposed Vietnamese and GIS symbols')
        axes = {axis.axisTag: (axis.minValue, axis.maxValue) for axis in parsed_font['fvar'].axes}
        self.assertEqual(axes.get('wght'), (200, 900))
        self.assertNotIn('Be Vietnam Pro', ''.join(path.read_text(encoding='utf-8')
                                                   for path in WEBUI.glob('*.css')))

    def test_roi_and_dem_compact_layout_hooks_and_queue_chaining(self):
        css = (WEBUI / 'workspace_layout.css').read_text(encoding='utf-8')
        for selector in (
            '.roi-source-summary', '#source-diagnostics', '.roi-recent-history',
            '#recent-history', '.roi-primary-actions', '.dem-workbench-columns',
            '.dem-controls-column', '.dem-preview-column',
        ):
            self.assertIn(selector, css)
        self.assertIn('#recent-list > :nth-child(n + 4)', css)
        self.assertIn('#source-summary .detail-rows', css)
        self.assertIn('grid-template-columns: repeat(2, minmax(0, 1fr))', css)
        self.assertIn('#source-summary .file-path', css)
        self.assertIn('#form .wizard-panel[data-panel="1"] > details', css)
        self.assertIn('margin-top: 6px;\n  padding-top: 5px', css)
        self.assertIn('grid-template-columns: minmax(360px, 420px) minmax(0, 1fr)', css)
        self.assertIn('max-height: 340px', css)
        self.assertIn('#gmsh-options > .dem-workbench-columns', css)
        self.assertIn('grid-column: 1 / -1;\n  width: 100%;\n  min-width: 0;', css)
        self.assertIn('grid-template-columns: minmax(0, 1fr) minmax(280px, .9fr)', css)
        self.assertIn('.dem-workbench-columns > #dem-preview.dem-preview-column', css)
        self.assertRegex(css, r'\.dem-workbench-columns > #dem-preview\.dem-preview-column\s*\{\s*grid-column: 2;\s*grid-row: 1;')
        self.assertRegex(css, r'@media \(max-width: 1000px\)[\s\S]*?\.dem-workbench-columns > #dem-preview\.dem-preview-column\s*\{\s*grid-column: 1;\s*grid-row: auto;')
        self.assertIn('body[data-workspace="extract"] #batch-queue', css)
        self.assertIn('overscroll-behavior: auto', css)
        self.assertNotRegex(css, r'body[^}]*height:\s*100vh')
        self.assertRegex(
            css,
            r'body\[data-workspace="extract"\] #batch-queue\s*\{[^}]*overscroll-behavior: auto',
        )

    def test_font_static_route_serves_bundled_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = WorkbenchServer(root)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(timeout=3)))
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            connection.request('GET', '/fonts/SourceSans3-VF.ttf')
            response = connection.getresponse()
            body = response.read()
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertIn('font/ttf', response.getheader('Content-Type'))
            self.assertEqual(body, (WEBUI / 'fonts' / 'SourceSans3-VF.ttf').read_bytes())


if __name__ == '__main__':
    unittest.main()
