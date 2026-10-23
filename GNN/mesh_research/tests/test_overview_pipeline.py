"""Optional figure failure cannot discard a valid mesh; cancellation propagates."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gis_mesh_workbench.runner import run,write_json
from gis_mesh_workbench.job_control import Cancelled


class OverviewPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'config.json'
        write_json(self.path,dict(schema_version='gis-workbench-run-config-v2',operation='mesh_only',
            extraction_engine='indexed_windowed_v1',run_name='overview-test',analysis_crs='EPSG:32649',
            output_root=str(self.root/'runs'),cell_source=dict(kind='structured_grid',extent=[0,0,2,1],
                cell_width_m=1,cell_height_m=1,boundary_policy='strict',max_cells=1000)))

    def test_figure_failure_preserves_valid_mesh_with_warning(self):
        with patch('gis_mesh_workbench.run_overview.render_overview',side_effect=ValueError('plot failed')):
            result=run(self.path)
        self.assertEqual(result['status'],'pass',result)
        output=Path(result['output']);qa=json.loads((output/'qa.json').read_text())
        self.assertEqual(qa['run_overview']['status'],'warning')
        self.assertEqual(qa['mesh_quality']['cell_count'],2)
        self.assertTrue((output/'cells.json').is_file())

    def test_figure_cancellation_is_not_swallowed(self):
        with patch('gis_mesh_workbench.run_overview.render_overview',side_effect=Cancelled('stopped')):
            result=run(self.path)
        self.assertEqual(result['status'],'cancelled',result)


if __name__=='__main__':unittest.main()
