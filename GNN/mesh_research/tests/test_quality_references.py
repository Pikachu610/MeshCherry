import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gis_mesh_workbench.quality_references import load_references


class QualityReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "meshing").mkdir()
        self.cells = [{"cell_id": "cell-0", "kind": "triangle",
                       "xy": [[0.2, 0.2], [0.8, 0.2], [0.2, 0.8]]}]

    def tearDown(self):
        self.temp.cleanup()

    def put(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def manifest(self, hashes):
        return {"status": "pass", "artifact_hashes": hashes,
                "effective_contract": {"analysis_crs_wkt": "EPSG:32649"},
                "gmsh_generated": {"size_field": {"method": "exploratory_local_relief_p25_p95_gamma2"}}}

    def test_returns_original_roi_and_hash_verified_piecewise_linear_target(self):
        boundary = json.dumps({"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
                               "crs_wkt": "EPSG:32649"})
        bhash = self.put("meshing/domain-boundary.json", boundary)
        # One retained control triangle, h(x,y) = 10 + 10*x + 20*y.
        pos = 'View "h" { ST(0,0,0,1,0,0,0,1,0){10,20,30}; };'
        phash = self.put("meshing/size.pos", pos)
        manifest = self.manifest({"meshing/domain-boundary.json": bhash,
                                  "meshing/size.pos": phash})
        roi, field = load_references(self.root, self.cells, manifest)
        self.assertEqual(roi["geometry"]["type"], "Polygon")
        self.assertEqual(field["definition"], "gmsh_characteristic_length")
        self.assertAlmostEqual(field["target_sizes"]["cell-0"], 22.0)
        self.assertEqual(field["source_sha256"], phash)

    def test_unhashed_and_tampered_references_are_not_used(self):
        self.put("meshing/domain-boundary.json", json.dumps({"geometry": {}, "crs_wkt": "x"}))
        self.put("meshing/size.pos", 'View "h" { ST(0,0,0,1,0,0,0,1,0){1,1,1}; };')
        roi, field = load_references(self.root, self.cells, self.manifest({}))
        self.assertIsNone(roi)
        self.assertIsNone(field)

    def test_uniform_target_requires_verified_gmsh_geo_artifact(self):
        geo_hash = self.put("meshing/domain.geo", 'Field[1].F="250";')
        manifest = self.manifest({"meshing/domain.geo": geo_hash})
        manifest["gmsh_generated"]["size_field"] = {"method": "uniform", "requested_target_size_m": 250}
        _, field = load_references(self.root, self.cells, manifest)
        self.assertEqual(field["uniform"], 250.0)
        self.assertEqual(field["source_sha256"], geo_hash)

    def test_sf1_npz_is_not_sampled_without_verified_gmsh_interpolation(self):
        import numpy as np
        path = self.root / "meshing" / "sf1-control-field.npz"
        np.savez_compressed(path, x=np.array([0.0, 1.0]), y=np.array([1.0, 0.0]),
                            h=np.ones((2, 2)))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = self.manifest({"meshing/sf1-control-field.npz": digest})
        manifest["gmsh_generated"]["size_field"] = {"method": "gnn_sf1_two_window_relief"}
        _, field = load_references(self.root, self.cells, manifest)
        self.assertIsNone(field)

    def test_size_file_changed_during_parse_is_rejected(self):
        pos = 'View "h" { ST(0,0,0,1,0,0,0,1,0){10,20,30}; };'
        digest = self.put("meshing/size.pos", pos)
        manifest = self.manifest({"meshing/size.pos": digest})

        def mutate_after_parse(path):
            result = (((((0, 0), (1, 0), (0, 1))),), ((10, 20, 30),))
            path.write_text(pos + "\n// changed during read\n", encoding="utf-8")
            return result

        with patch("gis_mesh_workbench.quality_references._relief_triangles",
                   side_effect=mutate_after_parse):
            _, field = load_references(self.root, self.cells, manifest)
        self.assertIsNone(field)

    def test_path_traversal_hash_entry_is_ignored(self):
        outside = self.root.parent / "outside.json"
        digest = hashlib.sha256(b"{}").hexdigest()
        roi, field = load_references(self.root, self.cells,
                                     self.manifest({"../outside.json": digest}))
        self.assertIsNone(roi)
        self.assertIsNone(field)


if __name__ == "__main__":
    unittest.main()
