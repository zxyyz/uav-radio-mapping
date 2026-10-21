from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tools/radio_receiver'))
from radio_fixtures import make_packet
import copy
import json
from pathlib import Path
import tempfile
import unittest

from server import Conflict, Receiver, validate





class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.repo.mkdir()
        # Isolated databases; use the existing package and SQL unchanged.
        (self.repo / 'src').mkdir()
        self.receiver = Receiver(Path(self.temp.name)/'inbox', self.repo)
        from uav_radio_mapping import store
        self.receiver.core=self.repo/'data/geometry/maps.sqlite3'
        self.receiver.synthetic=self.repo/'data/geometry/synthetic.sqlite3'
        store.initialize(self.receiver.core);store.initialize(self.receiver.synthetic)
        self.real=make_packet(ready=False)
        self.synthetic=make_packet(ready=True)

    def test_synthetic_unpositioned_data_durably_received_without_fake_poses(self):
        result=self.receiver.receive(self.real)
        self.assertEqual(result['pending_observations'],5)
        status=self.receiver.status()
        self.assertEqual(status['inbox'],dict(packets=1,observations=5,cells=10))
        self.assertEqual(status['standard_database']['poses'],0)
        self.assertEqual(status['standard_database']['radio_observations'],0)

    def test_same_packet_retry_and_changed_packet_conflict(self):
        self.receiver.receive(self.real)
        result=self.receiver.receive(self.real)
        self.assertTrue(result['duplicate'])
        self.assertEqual(self.receiver.status()['inbox']['observations'],5)
        altered=copy.deepcopy(self.real)
        altered['observations'][0]['cell_measurements'][0]['rsrp_dbm']=-120
        with self.assertRaises(Conflict):self.receiver.receive(altered)
        self.assertEqual(self.receiver.status()['inbox']['cells'],10)

    def test_synthetic_ready_packet_imports_to_separate_schema_v1_database(self):
        result=self.receiver.receive(self.synthetic)
        self.assertEqual(result['import_state'],'imported')
        status=self.receiver.status()
        self.assertEqual(status['synthetic_database']['poses'],5)
        self.assertEqual(status['synthetic_database']['cell_measurements'],25)
        self.assertEqual(status['standard_database']['sites'],0)
        self.receiver.receive(self.synthetic)
        self.assertEqual(self.receiver.status()['synthetic_database']['cell_measurements'],25)

    def test_invalid_batch_does_not_partially_commit(self):
        altered=copy.deepcopy(self.real)
        altered['observations'][1]['radio_observation']['device_id']='wrong-device'
        with self.assertRaises(ValueError):self.receiver.receive(altered)
        self.assertEqual(self.receiver.status()['inbox']['packets'],0)

    def test_missing_pose_cannot_be_marked_ready(self):
        altered=copy.deepcopy(self.real)
        altered['observations'][0]['ready_for_database']=True
        with self.assertRaises(ValueError):validate(altered)


if __name__=='__main__':
    unittest.main(verbosity=2)
