import contextlib
import copy
from importlib.resources import files
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from uav_radio_mapping import store
from uav_radio_mapping.cli import main


class GeometryStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "geometry.sqlite3"
        self.demo = json.loads(files("uav_radio_mapping").joinpath("demo-buildings.json").read_text())
        store.initialize(self.db)

    def test_initialize_preserves_existing_data(self):
        self.assertEqual(store.status(self.db)["counts"]["buildings"], 0)
        store.import_map(self.db, self.demo)
        store.initialize(self.db)
        self.assertEqual(store.status(self.db)["counts"]["buildings"], 3)

    def test_bbox_filter_and_export_reimport(self):
        store.import_map(self.db, self.demo)
        rows = store.list_buildings(self.db, "synthetic-v1", [0, 0, 40, 40])
        self.assertEqual([b["building_id"] for b in rows], ["A"])
        exported = store.export_map(self.db, "synthetic-v1")
        other = Path(self.directory.name) / "roundtrip.sqlite3"
        store.initialize(other)
        store.import_map(other, exported)
        self.assertEqual(exported, store.export_map(other, "synthetic-v1"))
        self.assertEqual(exported["buildings"][2]["base_z_m"], 1.5)

    def test_idempotent_import_and_immutable_map(self):
        store.import_map(self.db, self.demo)
        result = store.import_map(self.db, self.demo)
        self.assertTrue(result["already_present"])
        self.demo["buildings"][0]["height_m"] = 21
        with self.assertRaisesRegex(ValueError, "immutable"):
            store.import_map(self.db, self.demo)
        self.assertEqual(store.export_map(self.db, "synthetic-v1")["buildings"][0]["height_m"], 20)

    def test_changed_site_origin_rejected(self):
        store.import_map(self.db, self.demo)
        self.demo["map"]["map_id"] = "synthetic-v2"
        self.demo["site"]["origin_alt_m"] = 100
        with self.assertRaisesRegex(ValueError, "immutable"):
            store.import_map(self.db, self.demo)
        self.assertEqual(store.status(self.db)["counts"]["map_versions"], 1)

    def test_failed_asset_reference_rolls_back_new_site(self):
        self.demo["map"]["source_asset_id"] = "missing-asset"
        with self.assertRaisesRegex(ValueError, "same site"):
            store.import_map(self.db, self.demo)
        counts = store.status(self.db)["counts"]
        self.assertEqual([counts[name] for name in ("sites", "map_versions", "buildings")], [0, 0, 0])

    def test_invalid_building_does_not_partially_import(self):
        self.demo["buildings"][1]["height_m"] = -1
        with self.assertRaises(ValueError):
            store.import_map(self.db, self.demo)
        self.assertEqual(store.status(self.db)["counts"]["sites"], 0)

    def test_reject_invalid_rings_and_accept_concave_polygon(self):
        invalid = [
            [[0, 0], [10, 10], [0, 10], [10, 0], [0, 0]],
            [[0, 0], [10, 0], [0, 0], [0, 10], [0, 0]],
            [[0, 0], [10, 0], [5, 0], [5, 10], [0, 10], [0, 0]],
            [[0, 0], [5, 0], [10, 0], [0, 0]],
            [[0, 0], [10, 0], [10, 10], [0, 10]],
        ]
        for ring in invalid:
            with self.subTest(ring=ring), self.assertRaises(ValueError):
                store.validate_ring(ring)
        ring = [[0, 0], [10, 0], [10, 5], [5, 5], [5, 10], [0, 10], [0, 0]]
        self.assertEqual(store.validate_ring(ring), ring)

    def test_prior_does_not_accept_truth_heights(self):
        self.demo["map"]["role"] = "prior_2d"
        with self.assertRaisesRegex(ValueError, "height_m=null"):
            store.import_map(self.db, self.demo)
        for building in self.demo["buildings"]:
            building["height_m"] = None
        store.import_map(self.db, self.demo)
        self.assertTrue(all(b["height_m"] is None for b in store.list_buildings(self.db, "synthetic-v1")))

    def test_truth_requires_height_and_values_are_finite(self):
        invalid = copy.deepcopy(self.demo)
        invalid["map"]["role"] = "lidar_truth"
        invalid["buildings"][0]["height_m"] = None
        with self.assertRaises(ValueError):
            store.import_map(self.db, invalid)
        for value in (float("nan"), float("inf"), True):
            invalid = copy.deepcopy(self.demo)
            invalid["site"]["origin_alt_m"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                store.import_map(self.db, invalid)

    def test_rtree_rounding_does_not_return_false_bbox_hits(self):
        self.demo["buildings"] = [{"building_id": "precise", "base_z_m": 0, "height_m": 1,
                                  "footprint_xy_m": [[0, 0], [0.1, 0], [0.1, 1], [0, 1], [0, 0]]}]
        store.import_map(self.db, self.demo)
        rows = store.list_buildings(self.db, "synthetic-v1", [0.10000000001, 0, 0.2, 1])
        self.assertEqual(rows, [])

    def test_schema_enforces_observation_identity_and_foreign_keys(self):
        store.import_map(self.db, self.demo)
        with store.connection(self.db) as conn, conn:
            conn.execute("INSERT INTO flights VALUES ('flight','synthetic-site','device',1,NULL,'{}')")
            conn.execute("INSERT INTO poses VALUES ('pose','flight',2,0,0,10,NULL,NULL,NULL,'{}')")
            conn.execute("INSERT INTO radio_observations VALUES ('obs','pose','device',1,1,3,4,0,'OK','{}')")
            for i in range(8):
                conn.execute("""INSERT INTO cell_measurements
                    (observation_id,role,rat,pci,arfcn,rsrp_dbm,extras_json)
                    VALUES ('obs','neighbor','LTE',?,100,-90,'{}')""", (i,))
            self.assertEqual(conn.execute("SELECT count(*) FROM cell_measurements").fetchone()[0], 8)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO radio_observations VALUES ('retry','pose','device',1,1,3,4,0,'OK','{}')")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO radio_observations VALUES ('invalid','missing-pose','device',2,1,3,4,0,'OK','{}')")

    def test_missing_database_is_not_created_by_query(self):
        missing = Path(self.directory.name) / "missing.sqlite3"
        with self.assertRaises(ValueError):
            store.status(missing)
        self.assertFalse(missing.exists())

    def test_foreign_database_is_not_reinitialized(self):
        other = Path(self.directory.name) / "other.sqlite3"
        with sqlite3.connect(other) as conn:
            conn.execute("CREATE TABLE original (value TEXT)")
        with self.assertRaisesRegex(ValueError, "version"):
            store.initialize(other)
        with sqlite3.connect(other) as conn:
            names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        self.assertEqual(names, ["original"])

    def test_cli_demo_export_and_invalid_input(self):
        output = Path(self.directory.name) / "export.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--db", str(self.db), "demo"]), 0)
            self.assertEqual(main(["--db", str(self.db), "export-buildings", "synthetic-v1", str(output)]), 0)
        self.assertEqual(len(json.loads(output.read_text())["buildings"]), 3)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--db", str(self.db), "import-buildings", str(output.parent / "missing.json")]), 1)
            self.assertEqual(main(["--db", str(self.db), "export-buildings", "synthetic-v1", str(self.db)]), 1)
        self.assertEqual(store.status(self.db)["counts"]["buildings"], 3)


if __name__ == "__main__":
    unittest.main()
