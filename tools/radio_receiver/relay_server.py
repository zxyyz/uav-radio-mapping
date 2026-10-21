"""Authenticated durable relay. Aircraft uploads; one PC drains and acknowledges."""
import argparse
from contextlib import contextmanager
import hmac
import http.server
import json
from pathlib import Path
import sqlite3
import ssl
import time
from runtime_config import load_config
from server import validate, canonical, digest


class Queue:
    RECEIPT_TTL_NS = 7 * 24 * 60 * 60 * 1_000_000_000
    MAX_RECEIPTS = 10000
    def __init__(self, path):
        self.path = path
        with self.connect() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('CREATE TABLE IF NOT EXISTS packets (id INTEGER PRIMARY KEY AUTOINCREMENT, packet_id TEXT UNIQUE NOT NULL, sha256 TEXT NOT NULL, payload_json TEXT NOT NULL, received_at_ns INTEGER NOT NULL, delivered_at_ns INTEGER)')
            if 'observation_count' not in {row[1] for row in conn.execute('PRAGMA table_info(packets)')}:
                conn.execute('ALTER TABLE packets ADD COLUMN observation_count INTEGER NOT NULL DEFAULT 0')
                for ident,payload in conn.execute('SELECT id,payload_json FROM packets').fetchall():
                    conn.execute('UPDATE packets SET observation_count=? WHERE id=?',(len(json.loads(payload)['observations']),ident))
            conn.execute('CREATE TABLE IF NOT EXISTS receipts (packet_id TEXT PRIMARY KEY, relay_id INTEGER UNIQUE NOT NULL, sha256 TEXT NOT NULL, delivered_at_ns INTEGER NOT NULL)')
            # Existing delivered rows already have a durable PC acknowledgement.
            conn.execute('INSERT OR IGNORE INTO receipts SELECT packet_id,id,sha256,delivered_at_ns FROM packets WHERE delivered_at_ns IS NOT NULL')
            purged=conn.execute('DELETE FROM packets WHERE delivered_at_ns IS NOT NULL').rowcount
            self.prune_receipts(conn)
        with self.connect() as conn:
            if conn.execute('PRAGMA auto_vacuum').fetchone()[0] != 1 or purged:
                conn.execute('PRAGMA auto_vacuum=FULL')
                conn.execute('VACUUM')
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    def prune_receipts(self, conn):
        conn.execute('DELETE FROM receipts WHERE delivered_at_ns<?',(time.time_ns()-self.RECEIPT_TTL_NS,))
        conn.execute('DELETE FROM receipts WHERE packet_id IN (SELECT packet_id FROM receipts ORDER BY delivered_at_ns DESC,relay_id DESC LIMIT -1 OFFSET ?)',(self.MAX_RECEIPTS,))
    @contextmanager
    def connect(self):
        conn=sqlite3.connect(self.path,timeout=10)
        try:
            with conn:yield conn
        finally:conn.close()
    def receive(self, packet):
        validate(packet)
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self.prune_receipts(conn)
            delivered=conn.execute('SELECT relay_id,sha256 FROM receipts WHERE packet_id=?',(packet['packet_id'],)).fetchone()
            if delivered:
                if delivered[1]!=digest(packet):raise ValueError('Packet ID content conflict')
                return {'accepted':True,'duplicate':True,'relay_id':delivered[0]}
            old=conn.execute('SELECT id,sha256 FROM packets WHERE packet_id=?',(packet['packet_id'],)).fetchone()
            if old:
                if old[1]!=digest(packet):raise ValueError('Packet ID content conflict')
                return {'accepted':True,'duplicate':True,'relay_id':old[0]}
            cursor=conn.execute('INSERT INTO packets(packet_id,sha256,payload_json,received_at_ns,observation_count) VALUES(?,?,?,?,?)',
                (packet['packet_id'],digest(packet),canonical(packet),time.time_ns(),len(packet['observations'])))
            return {'accepted':True,'duplicate':False,'relay_id':cursor.lastrowid}
    def pending(self):
        with self.connect() as conn:
            return [{'relay_id':r[0],'packet':json.loads(r[1])} for r in conn.execute('SELECT id,payload_json FROM packets WHERE delivered_at_ns IS NULL ORDER BY id LIMIT 5')]
    def ack(self, ids):
        if not isinstance(ids,list) or not 1<=len(ids)<=5 or any(type(i) is not int or i<=0 for i in ids):raise ValueError('Invalid acknowledgements')
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for ident in ids:
                if not conn.execute('SELECT 1 FROM packets WHERE id=? UNION ALL SELECT 1 FROM receipts WHERE relay_id=?',(ident,ident)).fetchone():raise ValueError('Unknown relay ID')
            now=time.time_ns()
            for ident in ids:
                conn.execute('INSERT OR IGNORE INTO receipts SELECT packet_id,id,sha256,? FROM packets WHERE id=?',(now,ident))
                conn.execute('DELETE FROM packets WHERE id=?',(ident,))
            self.prune_receipts(conn)
        return {'acknowledged':ids}
    def status(self):
        with self.connect() as conn:
            self.prune_receipts(conn)
            total,pending=conn.execute('SELECT COUNT(*),COALESCE(SUM(delivered_at_ns IS NULL),0) FROM packets').fetchone()
            receipts=conn.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]
            observations,size=conn.execute('SELECT COALESCE(SUM(observation_count),0),COALESCE(SUM(length(CAST(payload_json AS BLOB))),0) FROM packets WHERE delivered_at_ns IS NULL').fetchone()
        return {'stored_packets':total,'pending_packets':pending,'pending_observations':observations,'pending_bytes':size,'receipt_entries':receipts}


def make_server(queue,token,host,port):
    class Handler(http.server.BaseHTTPRequestHandler):
        def reply(self,status,value):
            body=json.dumps(value,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def authorized(self):
            return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)
        def do_GET(self):
            if self.path=='/health':self.reply(200,{'service':'radio-durable-relay'});return
            if not self.authorized():self.reply(401,{'error':'authentication required'});return
            if self.path=='/api/v1/relay/pending':self.reply(200,{'packets':queue.pending()})
            elif self.path=='/status':self.reply(200,queue.status())
            else:self.reply(404,{'error':'not found'})
        def do_POST(self):
            if not self.authorized():self.reply(401,{'error':'authentication required'});return
            if self.path not in ('/api/v1/radio/packets','/api/v1/relay/ack'):self.reply(404,{'error':'not found'});return
            try:
                self.connection.settimeout(10)
                length=int(self.headers.get('Content-Length','0'))
                if not 1<=length<=2*1024*1024:raise ValueError('Body size limit')
                raw=self.rfile.read(length)
                if len(raw)!=length:raise ValueError('Incomplete body')
                body=json.loads(raw)
                if self.path=='/api/v1/radio/packets':
                    if self.headers.get('Idempotency-Key') not in (None,body.get('packet_id')):raise ValueError('Idempotency key mismatch')
                    result=queue.receive(body);self.reply(200 if result['duplicate'] else 202,result)
                else:self.reply(200,queue.ack(body['relay_ids']))
            except (ValueError,KeyError,TypeError):self.reply(400,{'error':'invalid packet or acknowledgement'})
            except (OSError,sqlite3.Error):self.reply(503,{'error':'relay temporarily unavailable'})
        def log_message(self,fmt,*args):
            print(time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),fmt%args,flush=True)
    return http.server.ThreadingHTTPServer((host,port),Handler)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    config=load_config(args.config, path_fields=('database','certificate','key'), tokens=('token',))
    Path(config['database']).parent.mkdir(parents=True,exist_ok=True)
    queue=Queue(config['database'])
    server=make_server(queue,config['token'],config.get('host','0.0.0.0'),config.get('port',8878))
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.minimum_version=ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(config['certificate'],config['key'])
    server.socket=tls.wrap_socket(server.socket,server_side=True)
    print('Radio relay ready',flush=True)
    try:server.serve_forever()
    finally:server.server_close()
