from html.parser import HTMLParser
from pathlib import Path
import unittest


WEBUI = Path(__file__).parents[1] / 'src' / 'gis_mesh_workbench' / 'webui'


class UIContractParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.inputs = {}
        self.select_options = {}
        self.math_ids = set()
        self.math_depth = 0
        self.math_tokens = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'input' and attrs.get('id'):
            self.inputs[attrs['id']] = attrs
        if tag == 'select' and attrs.get('id'):
            self.select_options[attrs['id']] = []
        if tag == 'option' and self.select_options:
            current = next(reversed(self.select_options))
            self.select_options[current].append(attrs.get('value'))
        if tag == 'math':
            self.math_depth += 1
            if attrs.get('id'):
                self.math_ids.add(attrs['id'])
        elif self.math_depth:
            self.math_tokens.append(tag)

    def handle_endtag(self, tag):
        if tag == 'math' and self.math_depth:
            self.math_depth -= 1


class WorkbenchUIClarityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (WEBUI / 'index.html').read_text(encoding='utf-8')
        cls.coach = (WEBUI / 'coach.js').read_text(encoding='utf-8')
        cls.css = (WEBUI / 'wizard.css').read_text(encoding='utf-8')
        cls.parser = UIContractParser()
        cls.parser.feed(cls.html)
        cls.parser.close()

    def test_help_is_explicit_and_has_no_hover_or_focus_trigger(self):
        for token in ('mouseenter', 'mouseleave', "addEventListener('focus'"):
            self.assertNotIn(token, self.coach)
        for token in ('hover-help', 'help-tooltip', 'role="tooltip"'):
            self.assertNotIn(token, self.html)
        self.assertNotIn('.help-tooltip', self.css)
        self.assertIn('id="tutorial-help"', self.html)
        app = (WEBUI / 'app.js').read_text(encoding='utf-8')
        self.assertIn("$('tutorial-help').onclick", app)

    def test_required_legend_is_separate_from_conditional_marker_hook(self):
        self.assertIn('class="required-note"', self.html)
        self.assertIn('label[for][data-required="true"]::after', self.css)
        app = (WEBUI / 'app.js').read_text(encoding='utf-8')
        self.assertIn('input.required=!!value', app)
        self.assertIn('label.dataset.required=String(!!value)', app)

    def test_equations_use_native_mathml_not_literal_formula_text(self):
        self.assertTrue({'relief-formula', 'sf1-formula'}.issubset(self.parser.math_ids))
        self.assertIn('<mfrac>', self.html)
        self.assertIn('<msup>', self.html)
        self.assertRegex(self.html, r'<math id="relief-formula"[^>]*>.*?</math>')
        self.assertNotIn('C=clip((relief-P25)/(P95-P25),0,1)', self.html)
        self.assertNotIn('h=hmax-(hmax-hmin)C^2', self.html)

    def test_export_choices_and_project_file_extension_are_explicit(self):
        self.assertEqual(
            self.parser.select_options['export-format'],
            ['geojson', 'geotiff', 'asc'],
        )
        self.assertEqual(self.parser.inputs['project-file'].get('accept'), '.mcy,application/zip')
        nodata = self.parser.inputs['export-nodata']
        self.assertEqual(nodata.get('type'), 'number')
        self.assertEqual(nodata.get('value'), '-9999')
        self.assertEqual(nodata.get('step'), 'any')
        self.assertEqual(self.html.count('id="export-nodata-control"'), 1)
        self.assertIn('for="export-nodata"', self.html)

    def test_saved_project_history_has_structured_render_target(self):
        self.assertEqual(self.html.count('id="project-history"'), 1)
        self.assertEqual(self.html.count('id="project-history-content"'), 1)
        self.assertIn('<summary>Hồ sơ kết quả đã lưu</summary>', self.html)

    def test_ids_are_unique_and_html_is_well_formed_for_parser(self):
        duplicates = sorted({value for value in self.parser.ids if self.parser.ids.count(value) > 1})
        self.assertEqual(duplicates, [])
        self.assertGreaterEqual(len(self.parser.math_ids), 2)


if __name__ == '__main__':
    unittest.main()
