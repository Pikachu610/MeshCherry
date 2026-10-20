from pathlib import Path
import re
import unittest


WEBUI = Path(__file__).parents[1] / "src" / "gis_mesh_workbench" / "webui"


def rule_block(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css, re.S)
    if not match:
        raise AssertionError(f"Missing CSS rule for {selector}")
    return match.group(1)


def declarations_for_selector(css: str, selector: str) -> str:
    start = css.find(selector)
    if start < 0:
        raise AssertionError(f"Missing CSS selector {selector}")
    opening = css.find("{", start)
    closing = css.find("}", opening)
    if opening < 0 or closing < 0:
        raise AssertionError(f"Incomplete CSS rule for {selector}")
    return css[opening + 1 : closing]


class WorkbenchResultLayoutCssTests(unittest.TestCase):
    def test_quality_report_stays_in_page_scroll_flow(self):
        layout = (WEBUI / "workspace_layout.css").read_text(encoding="utf-8")
        quality = (WEBUI / "quality_panel.css").read_text(encoding="utf-8")

        quality_flow = declarations_for_selector(layout, 'body[data-stage="4"] #mesh-quality')
        self.assertRegex(quality_flow, r"max-height:\s*none;")
        self.assertRegex(quality_flow, r"overflow:\s*visible;")
        self.assertIn("body[data-stage=\"4\"] #quality-entry", layout)
        self.assertIn("padding: 10px 14px", layout)
        table_scroll = rule_block(quality, ".meshcherry-quality-panel .mcp-table-wrap")
        self.assertRegex(table_scroll, r"overflow-x:\s*auto;")
        self.assertRegex(table_scroll, r"overflow-y:\s*visible;")
        self.assertRegex(table_scroll, r"overscroll-behavior:\s*auto;")

    def test_scrollable_results_chain_and_layout_keeps_accessibility_hooks(self):
        layout = (WEBUI / "workspace_layout.css").read_text(encoding="utf-8")
        attributes = (WEBUI / "attribute_table.css").read_text(encoding="utf-8")
        batch_table = rule_block(layout, 'body[data-workspace="extract"] #batch-table')
        attribute_scroll = rule_block(attributes, ".mc-attribute-table__scroll")
        menu_button = rule_block(layout, ".app-menubar details > [role=\"group\"] button")
        qa_summary = declarations_for_selector(
            (WEBUI / "quality_panel.css").read_text(encoding="utf-8"),
            ".meshcherry-quality-panel .mcp-settings > summary",
        )

        self.assertIn("overscroll-behavior: auto;", layout)
        self.assertNotRegex(batch_table, r"max-height|overflow:\s*auto")
        self.assertNotRegex(attribute_scroll, r"max-height|overflow:\s*auto")
        self.assertRegex(attribute_scroll, r"overscroll-behavior:\s*auto;")
        self.assertIn("position: absolute", layout)
        self.assertRegex(menu_button, r"min-height:\s*44px;")
        self.assertRegex(qa_summary, r"min-height:\s*40px;")
        remesh_heading = rule_block(layout, ".result-heading")
        self.assertRegex(remesh_heading, r"display:\s*flex;")
        self.assertIn("@media (max-width: 640px)", layout)
        self.assertIn("min-height: 44px", layout)
        self.assertIn("@media (prefers-reduced-motion: reduce)", layout)
        base_style = (WEBUI / "style.css").read_text(encoding="utf-8")
        self.assertIn(":focus-visible", base_style)


if __name__ == "__main__":
    unittest.main()
