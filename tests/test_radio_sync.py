import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tools/radio_receiver'))
from radio_fixtures import make_packet
from server import Conflict,Receiver,make_server
from relay_client import RelayConnection

class PermanentSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.remote=Receiver(root/'remote-inbox',root/'remote-repo')
        self.local=Receiver(root/'local-inbox',root/'local-repo')
        self.packet=make_packet(ready=False);self.remote.receive(self.packet)
        self.servers=[make_server(self.remote,'remote-token','127.0.0.1',0),make_server(self.local,'local-token','127.0.0.1',0)]
        self.threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in self.servers]
        for thread in self.threads:thread.start()
        self.addCleanup(self.shutdown)
        self.cursor=root/'cursor.private.json'
        self.config=dict(mode='sync',relay_url='http://127.0.0.1:'+str(self.servers[0].server_port),relay_token='remote-token',
                         local_url='http://127.0.0.1:'+str(self.servers[1].server_port),local_token='local-token',sync_cursor_file=str(self.cursor))
    def shutdown(self):
        for server in self.servers:server.shutdown();server.server_close()
        for thread in self.threads:thread.join()
    def test_sync_preserves_server_payload_and_resumes_from_local_cursor(self):
        client=RelayConnection(self.config)
        self.assertEqual(client.status()['pending_observations'],5)
        self.assertEqual(client.transfer(),1)
        self.assertEqual(self.remote.status()['inbox']['packets'],1)
        self.assertEqual(self.local.status()['inbox']['observations'],5)
        self.assertEqual(client.status()['pending_packets'],0)
        self.assertEqual(RelayConnection(self.config).transfer(),0)
        next_packet=copy.deepcopy(self.packet);next_packet['packet_id']+='-second'
        self.remote.receive(next_packet)
        self.assertEqual(client.transfer(),1)
        self.assertEqual(self.remote.status()['inbox']['packets'],2)
        self.assertEqual(self.local.status()['inbox']['packets'],2)
    def test_local_failure_does_not_advance_cursor_or_remove_server_data(self):
        client=RelayConnection(dict(self.config,local_token='wrong'))
        with self.assertRaises(urllib.error.HTTPError):client.transfer()
        self.assertFalse(self.cursor.exists())
        self.assertEqual(self.remote.sync_status()['pending_packets'],1)
    def test_different_database_identity_resets_stale_cursor(self):
        self.cursor.write_text(json.dumps(dict(server_id='previous-database',after=100)),encoding='utf-8')
        client=RelayConnection(self.config)
        self.assertEqual(client.status()['pending_packets'],1)
        self.assertEqual(client.transfer(),1)
        self.assertEqual(json.loads(self.cursor.read_text())['server_id'],self.remote.sync_status()['server_id'])
    def test_export_is_read_only_and_rejects_changed_server_identity(self):
        first=self.remote.sync_packets()
        self.assertEqual(first['packets'][0]['packet'],self.packet)
        self.assertEqual(self.remote.sync_packets(),first)
        with self.assertRaises(Conflict):self.remote.sync_packets(server_id='wrong-database')
        with self.assertRaises(ValueError):self.remote.sync_packets(after=-1)
        with self.assertRaises(ValueError):self.remote.sync_packets(limit=6)
        # No receiver route implements relay ACK deletion.
        client=RelayConnection(self.config)
        with self.assertRaises(urllib.error.HTTPError):client.remote_call('/api/v1/relay/ack',{'relay_ids':[1]})
        self.assertEqual(self.remote.status()['inbox']['packets'],1)

if __name__=='__main__':unittest.main()
