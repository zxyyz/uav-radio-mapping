from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tools/radio_receiver'))
from radio_fixtures import make_packet
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import server
from relay_server import Queue,make_server as make_relay
from relay_client import IdleStop,RelayConnection

class ClientTests(unittest.TestCase):
    def test_freshly_drained_batch_resets_empty_timer(self):
        policy=IdleStop(10)
        self.assertFalse(policy.observe(0,0))
        self.assertFalse(policy.observe(0,9,received=1))
        self.assertEqual(policy.elapsed(9),0)
        self.assertFalse(policy.observe(0,18))
        self.assertTrue(policy.observe(0,19))
    def test_empty_confirmation_resets_on_new_packet_or_query_failure(self):
        policy=IdleStop(10)
        self.assertFalse(policy.observe(0,0))
        self.assertFalse(policy.observe(0,9))
        self.assertFalse(policy.observe(None,10))
        self.assertFalse(policy.observe(0,20))
        self.assertFalse(policy.observe(1,29))
        self.assertFalse(policy.observe(0,30))
        self.assertFalse(policy.observe(0,39.9))
        self.assertTrue(policy.observe(0,40))
    def test_real_http_drain_persists_before_ack_and_counts_reach_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            queue=Queue(Path(folder)/'queue.sqlite3')
            packet=make_packet(ready=False)
            queue.receive(packet)
            receiver=server.Receiver(Path(folder)/'local',Path(folder)/'repo')
            local=server.make_server(receiver,'local-token','127.0.0.1',0)
            relay=make_relay(queue,'relay-token','127.0.0.1',0)
            threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (local,relay)]
            for thread in threads:thread.start()
            try:
                config={'relay_url':'http://127.0.0.1:'+str(relay.server_port),'relay_token':'relay-token','local_url':'http://127.0.0.1:'+str(local.server_port),'local_token':'local-token'}
                connection=RelayConnection(config)
                self.assertEqual(connection.status()['pending_observations'],5)
                self.assertEqual(connection.transfer(),1)
                self.assertEqual(connection.status()['pending_packets'],0)
                self.assertEqual(receiver.status()['inbox']['observations'],5)
                # A rejected local write must leave a new server packet pending.
                packet['packet_id']+='-retry';queue.receive(packet)
                config['local_token']='wrong-token'
                with self.assertRaises(urllib.error.HTTPError):RelayConnection(config).transfer()
                self.assertEqual(queue.status()['pending_packets'],1)
            finally:
                for s in (local,relay):s.shutdown();s.server_close()
                for thread in threads:thread.join()

if __name__=='__main__':unittest.main()
