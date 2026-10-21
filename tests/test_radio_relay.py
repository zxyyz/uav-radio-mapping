from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tools/radio_receiver'))
from radio_fixtures import make_packet
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import server
from relay_server import Queue

class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'queue.sqlite3'
        self.queue=Queue(self.path)
        self.packet=make_packet(ready=False)
    def test_offline_queue_survives_restart_and_ack_deletes_payload(self):
        result=self.queue.receive(self.packet)
        restarted=Queue(self.path)
        self.assertEqual(restarted.pending()[0]['packet'],self.packet)
        self.assertEqual(restarted.status()['pending_packets'],1)
        restarted.ack([result['relay_id']])
        self.assertEqual(Queue(self.path).status()['stored_packets'],0)
        self.assertEqual(Queue(self.path).status()['pending_observations'],0)
        self.assertEqual(Queue(self.path).pending(),[])
    def test_same_packet_duplicate_and_changed_content_rejected(self):
        self.queue.receive(self.packet)
        self.assertTrue(self.queue.receive(self.packet)['duplicate'])
        changed=copy.deepcopy(self.packet);changed['observations'][0]['cell_measurements'][0]['rsrp_dbm']=-130
        with self.assertRaises(ValueError):self.queue.receive(changed)
        self.assertEqual(len(self.queue.pending()),1)
    def test_invalid_batch_never_partially_enqueues(self):
        self.packet['observations'][-1]['radio_observation']['device_id']='wrong'
        with self.assertRaises(ValueError):self.queue.receive(self.packet)
        self.assertEqual(self.queue.status()['stored_packets'],0)
    def test_invalid_ack_is_atomic_and_does_not_discard_data(self):
        result=self.queue.receive(self.packet)
        with self.assertRaises(ValueError):self.queue.ack([result['relay_id'],999])
        self.assertEqual(self.queue.status()['pending_packets'],1)
    def test_ack_retry_and_aircraft_replay_do_not_recreate_payload(self):
        result=self.queue.receive(self.packet)
        self.queue.ack([result['relay_id']])
        self.queue.ack([result['relay_id']])
        self.assertTrue(self.queue.receive(self.packet)['duplicate'])
        self.assertEqual(self.queue.status()['stored_packets'],0)
        changed=copy.deepcopy(self.packet);changed['observations'][0]['cell_measurements'][0]['rsrp_dbm']=-130
        with self.assertRaises(ValueError):self.queue.receive(changed)
    def test_existing_delivered_payload_migrated_and_pending_preserved(self):
        first=self.queue.receive(self.packet)
        second=copy.deepcopy(self.packet);second['packet_id']+='-pending'
        self.queue.receive(second)
        with self.queue.connect() as conn:
            conn.execute('UPDATE packets SET delivered_at_ns=123 WHERE id=?',(first['relay_id'],))
        migrated=Queue(self.path)
        self.assertEqual(migrated.status()['stored_packets'],1)
        self.assertEqual(migrated.pending()[0]['packet'],second)
    def test_large_payload_disk_space_reclaimed_after_ack(self):
        self.packet['observations'][0]['radio_observation']['raw_response']='X'*400000
        result=self.queue.receive(self.packet)
        before=self.path.stat().st_size
        self.queue.ack([result['relay_id']])
        self.assertLess(self.path.stat().st_size,before//4)
        with self.queue.connect() as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM packets').fetchone()[0],0)
    def test_receipt_metadata_is_bounded(self):
        self.queue.MAX_RECEIPTS=2
        for index in range(4):
            packet=copy.deepcopy(self.packet);packet['packet_id']+='-'+str(index)
            result=self.queue.receive(packet);self.queue.ack([result['relay_id']])
        self.assertEqual(self.queue.status()['receipt_entries'],2)
    def test_status_counts_actual_observations_and_payload_bytes(self):
        result=self.queue.receive(self.packet)
        status=self.queue.status()
        self.assertEqual(status['pending_packets'],1)
        self.assertEqual(status['pending_observations'],len(self.packet['observations']))
        self.assertGreater(status['pending_bytes'],0)
        self.queue.ack([result['relay_id']])
        self.assertEqual(self.queue.status()['pending_bytes'],0)

if __name__=='__main__':unittest.main()
