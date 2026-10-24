"""Local authenticated packet receiver; keeps repository schema v1 unchanged."""
import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import hmac
import http.server
import json
import math
from pathlib import Path
import sqlite3
import sys
import time
import urllib.parse
import uuid
from runtime_config import REPO_ROOT, load_config

FORMAT = 'uav-radio-mapping.radio.v1'
TABLE_FIELDS = {
    'sites': ('site_id','name','origin_lon_deg','origin_lat_deg','origin_alt_m','vertical_datum','frame','created_at_utc'),
    'flights': ('flight_id','site_id','device_id','started_at_ns','ended_at_ns','metadata_json'),
    'poses': ('pose_id','flight_id','measured_at_ns','x_m','y_m','z_m','lon_deg','lat_deg','ellipsoid_alt_m','quality_json'),
    'radio_observations': ('observation_id','pose_id','device_id','sequence','query_started_at_ns','query_finished_at_ns','received_at_ns','pose_time_offset_ns','raw_response','quality_json'),
    'cell_measurements': ('observation_id','role','rat','mcc','mnc','cell_id','tac','pci','arfcn','rsrp_dbm','rsrq_db','rssi_dbm','sinr_db','extras_json'),
}


class Conflict(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def text(value, label):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError('Invalid ' + label)
    return value


def ns(value, label, nullable=False, signed=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.lstrip('-').isdecimal():
        raise ValueError(label + ' must be a decimal nanosecond string')
    result = int(value)
    if not (-(2**63) if signed else 0) <= result <= 2**63-1:
        raise ValueError('Out-of-range ' + label)
    return result


def validate(packet):
    if not isinstance(packet, dict) or packet.get('format') != FORMAT or packet.get('database_schema_version') != 1:
        raise ValueError('Expected ' + FORMAT)
    text(packet.get('packet_id'), 'packet_id')
    ns(packet.get('created_at_ns'), 'created_at_ns')
    flight = packet.get('flight')
    if not isinstance(flight, dict):
        raise ValueError('Missing flight')
    text(flight.get('flight_id'), 'flight_id')
    text(flight.get('device_id'), 'device_id')
    entries = packet.get('observations')
    if not isinstance(entries, list) or not 1 <= len(entries) <= 200:
        raise ValueError('Expected 1..200 observations')
    identities = set()
    for item in entries:
        if not isinstance(item, dict) or type(item.get('ready_for_database')) is not bool:
            raise ValueError('Missing readiness flag')
        row = item.get('radio_observation')
        if not isinstance(row, dict):
            raise ValueError('Missing radio_observation')
        text(row.get('observation_id'), 'observation_id')
        if row.get('device_id') != flight['device_id']:
            raise ValueError('Device identity mismatch')
        sequence = row.get('sequence')
        if type(sequence) is not int or not 0 <= sequence < 2**63:
            raise ValueError('Invalid sequence')
        identity = (row['device_id'], sequence)
        if identity in identities:
            raise ValueError('Duplicate observation in packet')
        identities.add(identity)
        ready = item['ready_for_database']
        start = ns(row.get('query_started_at_ns'), 'query_started_at_ns', not ready)
        end = ns(row.get('query_finished_at_ns'), 'query_finished_at_ns', not ready)
        ns(row.get('received_at_ns'), 'received_at_ns', not ready)
        ns(row.get('pose_time_offset_ns'), 'pose_time_offset_ns', not ready, True)
        if start is not None and end is not None and start > end:
            raise ValueError('Query time order')
        if not isinstance(row.get('quality_json'), dict) or not isinstance(row.get('raw_response'), str):
            raise ValueError('Missing raw response or quality')
        cells = item.get('cell_measurements')
        if not isinstance(cells, list) or len(cells) > 5:
            raise ValueError('Expected at most one serving and four neighbors')
        roles = [cell.get('role') for cell in cells]
        if roles.count('serving') > 1 or roles.count('neighbor') > 4:
            raise ValueError('Cell quota exceeded')
        for cell in cells:
            if cell.get('observation_id') != row['observation_id'] or cell.get('role') not in ('serving', 'neighbor'):
                raise ValueError('Cell association mismatch')
            text(cell.get('rat'), 'rat')
            for name in ('mcc', 'mnc', 'cell_id', 'tac'):
                if cell.get(name) is not None and not isinstance(cell[name], str):
                    raise ValueError(name + ' must preserve its string encoding')
            for name in ('pci', 'arfcn'):
                if cell.get(name) is not None and (type(cell[name]) is not int or cell[name] < 0):
                    raise ValueError('Invalid ' + name)
            for name in ('rsrp_dbm', 'rsrq_db', 'rssi_dbm', 'sinr_db'):
                v = cell.get(name)
                if v is not None and (isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)):
                    raise ValueError('Invalid ' + name)
            if not isinstance(cell.get('extras_json'), dict):
                raise ValueError('Missing cell extras')
        if ready:
            site, pose = packet.get('site'), item.get('pose')
            if not isinstance(site, dict) or not isinstance(pose, dict):
                raise ValueError('Ready observation needs site and pose')
            if flight.get('site_id') != site.get('site_id') or pose.get('flight_id') != flight['flight_id'] or row.get('pose_id') != pose.get('pose_id'):
                raise ValueError('Position foreign keys mismatch')
            if roles.count('serving') != 1:
                raise ValueError('Ready observation requires one serving cell')
            for name in ('x_m', 'y_m', 'z_m', 'lon_deg', 'lat_deg', 'ellipsoid_alt_m'):
                v = pose.get(name)
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    raise ValueError('Missing valid ' + name)
            ns(pose.get('measured_at_ns'), 'pose.measured_at_ns')
            ns(flight.get('started_at_ns'), 'flight.started_at_ns')
    canonical(packet)


class Receiver:
    def __init__(self, directory, repo):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        sys.path.insert(0, str(Path(repo) / 'src'))
        from uav_radio_mapping import store
        self.store = store
        self.core = Path(repo) / 'data/geometry/maps.sqlite3'
        self.synthetic = Path(repo) / 'data/geometry/network-test.synthetic.sqlite3'
        store.initialize(self.core)
        store.initialize(self.synthetic)
        self.inbox = self.directory / 'inbox.sqlite3'
        with self.connect() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS packets (
                  packet_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
                  received_at_ns INTEGER NOT NULL, payload_json TEXT NOT NULL,
                  import_state TEXT NOT NULL DEFAULT 'received', import_error TEXT);
                CREATE TABLE IF NOT EXISTS observations (
                  device_id TEXT NOT NULL, sequence INTEGER NOT NULL, packet_id TEXT NOT NULL REFERENCES packets,
                  observation_id TEXT NOT NULL, ready INTEGER NOT NULL, sha256 TEXT NOT NULL,
                  payload_json TEXT NOT NULL, PRIMARY KEY(device_id,sequence));
                CREATE TABLE IF NOT EXISTS cells (
                  device_id TEXT NOT NULL, sequence INTEGER NOT NULL, slot INTEGER NOT NULL,
                  role TEXT NOT NULL, pci INTEGER, arfcn INTEGER, rsrp_dbm REAL, rsrq_db REAL, rssi_dbm REAL, sinr_db REAL,
                  payload_json TEXT NOT NULL, PRIMARY KEY(device_id,sequence,slot),
                  FOREIGN KEY(device_id,sequence) REFERENCES observations(device_id,sequence));
                CREATE TABLE IF NOT EXISTS sync_metadata (
                  singleton INTEGER PRIMARY KEY CHECK(singleton=1), server_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS packet_sync_index (
                  sync_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  packet_id TEXT UNIQUE NOT NULL REFERENCES packets(packet_id), observation_count INTEGER NOT NULL);
            ''')
            conn.execute('INSERT OR IGNORE INTO sync_metadata VALUES (1,?)',(str(uuid.uuid4()),))
            existing=conn.execute('SELECT p.packet_id,p.payload_json FROM packets p LEFT JOIN packet_sync_index i ON i.packet_id=p.packet_id WHERE i.packet_id IS NULL ORDER BY p.received_at_ns,p.packet_id').fetchall()
            for ident,payload in existing:
                conn.execute('INSERT INTO packet_sync_index(packet_id,observation_count) VALUES (?,?)',(ident,len(json.loads(payload)['observations'])))

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.inbox, timeout=10)
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def receive(self, packet):
        validate(packet)
        inserted = 0
        with self.connect() as conn:
            try:
                conn.execute('BEGIN IMMEDIATE')
                old = conn.execute('SELECT sha256 FROM packets WHERE packet_id=?', (packet['packet_id'],)).fetchone()
                if old and old[0] != digest(packet):
                    raise Conflict('Packet ID reused with different content')
                conn.execute('INSERT OR IGNORE INTO packets(packet_id,sha256,received_at_ns,payload_json) VALUES (?,?,?,?)',
                    (packet['packet_id'], digest(packet), time.time_ns(), canonical(packet)))
                conn.execute('INSERT OR IGNORE INTO packet_sync_index(packet_id,observation_count) VALUES (?,?)',
                    (packet['packet_id'],len(packet['observations'])))
                for item in packet['observations']:
                    row = item['radio_observation']
                    identity = (row['device_id'], row['sequence'])
                    old = conn.execute('SELECT sha256 FROM observations WHERE device_id=? AND sequence=?', identity).fetchone()
                    if old:
                        if old[0] != digest(item):
                            raise Conflict('Device sequence reused with different content')
                        continue
                    conn.execute('INSERT INTO observations VALUES (?,?,?,?,?,?,?)',
                        (*identity,packet['packet_id'],row['observation_id'],int(item['ready_for_database']),digest(item),canonical(item)))
                    for index, cell in enumerate(item['cell_measurements']):
                        conn.execute('INSERT INTO cells VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                            (*identity,index,cell['role'],cell['pci'],cell['arfcn'],cell['rsrp_dbm'],cell['rsrq_db'],cell['rssi_dbm'],cell['sinr_db'],canonical(cell)))
                    inserted += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        # Durable reception is separate from importing position-ready rows. A failed import remains buffered.
        state, error = 'pending_pose', None
        ready = [entry for entry in packet['observations'] if entry['ready_for_database']]
        if ready:
            try:
                self.import_ready(packet, ready)
                state = 'imported' if len(ready) == len(packet['observations']) else 'partially_imported'
            except (ValueError, sqlite3.Error, KeyError) as exc:
                state, error = 'import_error', type(exc).__name__ + ': ' + str(exc)
        with self.connect() as conn:
            conn.execute('UPDATE packets SET import_state=?,import_error=? WHERE packet_id=?',(state,error,packet['packet_id']))
        receipt = dict(accepted=True, packet_id=packet['packet_id'], inserted_observations=inserted,
            duplicate=inserted==0, import_state=state, ready_observations=len(ready),
            pending_observations=len(packet['observations'])-len(ready))
        if error:
            receipt['import_error'] = error
        return receipt

    def import_ready(self, packet, ready):
        synthetic = bool((packet.get('provenance') or {}).get('synthetic'))
        database = self.synthetic if synthetic else self.core
        with self.store.connection(database) as conn, conn:
            def insert(table, row):
                converted = {k:canonical(row[k]) if k.endswith('_json') else (int(row[k]) if k.endswith('_ns') and row[k] is not None else row[k]) for k in TABLE_FIELDS[table]}
                conn.execute('INSERT INTO '+table+' ('+','.join(converted)+') VALUES ('+','.join('?' for _ in converted)+')',list(converted.values()))
            site = packet['site']
            current = conn.execute('SELECT * FROM sites WHERE site_id=?',(site['site_id'],)).fetchone()
            if current:
                if any(current[name] != site.get(name) for name in self.store.SITE_FIELDS):
                    raise Conflict('Site origin cannot change')
            else:
                insert('sites',{**{name:site[name] for name in self.store.SITE_FIELDS},'created_at_utc':self.store.utc_now()})
            flight = packet['flight']
            current = conn.execute('SELECT * FROM flights WHERE flight_id=?',(flight['flight_id'],)).fetchone()
            if current:
                if current['site_id'] != flight['site_id'] or current['device_id'] != flight['device_id'] or current['started_at_ns'] != int(flight['started_at_ns']):
                    raise Conflict('Flight identity cannot change')
            else:
                insert('flights',flight)
            for item in ready:
                row = item['radio_observation']
                current = conn.execute('SELECT * FROM radio_observations WHERE device_id=? AND sequence=?',(row['device_id'],row['sequence'])).fetchone()
                if current:
                    if current['observation_id'] != row['observation_id'] or current['raw_response'] != row['raw_response']:
                        raise Conflict('Existing observation identity mismatch')
                    continue
                insert('poses',item['pose'])
                insert('radio_observations',row)
                for cell in item['cell_measurements']:
                    insert('cell_measurements',cell)

    def status(self):
        with self.connect() as conn:
            counts = {table:conn.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('packets','observations','cells')}
            pending = conn.execute('SELECT count(*) FROM observations WHERE ready=0').fetchone()[0]
            states = dict(conn.execute('SELECT import_state,count(*) FROM packets GROUP BY import_state'))
        return dict(inbox=counts,pending_observations=pending,packet_states=states,
                    standard_database=self.store.status(self.core)['counts'],synthetic_database=self.store.status(self.synthetic)['counts'])

    def sync_status(self,after=0):
        if type(after) is not int or not 0<=after<2**63:raise ValueError('Invalid sync cursor')
        with self.connect() as conn:
            conn.execute('BEGIN')
            server_id=conn.execute('SELECT server_id FROM sync_metadata WHERE singleton=1').fetchone()[0]
            total,observations=conn.execute('SELECT COUNT(*),COALESCE(SUM(observation_count),0) FROM packet_sync_index').fetchone()
            pending=conn.execute('SELECT COUNT(*),COALESCE(SUM(i.observation_count),0),COALESCE(SUM(length(CAST(p.payload_json AS BLOB))),0) FROM packet_sync_index i JOIN packets p ON p.packet_id=i.packet_id WHERE i.sync_id>?',(after,)).fetchone()
        return dict(storage_mode='permanent',server_id=server_id,after=after,stored_packets=total,total_observations=observations,
                    pending_packets=pending[0],pending_observations=pending[1],pending_bytes=pending[2])

    def sync_packets(self,after=0,limit=5,server_id=None):
        if type(after) is not int or not 0<=after<2**63 or type(limit) is not int or not 1<=limit<=5:raise ValueError('Invalid sync page')
        with self.connect() as conn:
            conn.execute('BEGIN')
            current=conn.execute('SELECT server_id FROM sync_metadata WHERE singleton=1').fetchone()[0]
            if server_id is not None and server_id!=current:raise Conflict('Sync server identity changed')
            rows=conn.execute('SELECT i.sync_id,p.payload_json FROM packet_sync_index i JOIN packets p ON p.packet_id=i.packet_id WHERE i.sync_id>? ORDER BY i.sync_id LIMIT ?',(after,limit)).fetchall()
        return dict(server_id=current,packets=[dict(sync_id=r[0],packet=json.loads(r[1])) for r in rows])


def make_server(receiver, token, host, port):
    class Handler(http.server.BaseHTTPRequestHandler):
        def reply(self, code, value):
            body=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def authorized(self):
            return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)
        def do_GET(self):
            if self.path=='/health':
                self.reply(200,dict(service='uav-radio-local-receiver',format=FORMAT));return
            if not self.authorized():self.reply(401,{'error':'authentication required'});return
            parsed=urllib.parse.urlsplit(self.path)
            if parsed.path in ('/api/v1/sync/status','/api/v1/sync/packets'):
                try:
                    query=urllib.parse.parse_qs(parsed.query,keep_blank_values=True)
                    if any(len(v)!=1 for v in query.values()):raise ValueError('Repeated sync parameter')
                    after=int(query.get('after',['0'])[0])
                    if parsed.path.endswith('/status'):self.reply(200,receiver.sync_status(after))
                    else:self.reply(200,receiver.sync_packets(after,int(query.get('limit',['5'])[0]),query.get('server_id',[None])[0]))
                except Conflict as error:self.reply(409,{'error':str(error)})
                except ValueError as error:self.reply(400,{'error':str(error)})
                except sqlite3.Error:self.reply(503,{'error':'receiver temporarily unavailable'})
            elif self.path=='/status':self.reply(200,receiver.status())
            else:self.reply(404,{'error':'not found'})
        def do_POST(self):
            if not self.authorized():self.reply(401,{'error':'authentication required'});return
            if self.path!='/api/v1/radio/packets':self.reply(404,{'error':'not found'});return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 1<=length<=2*1024*1024:raise ValueError('Body size limit')
                self.connection.settimeout(10)
                packet=json.loads(self.rfile.read(length))
                if self.headers.get('Idempotency-Key') not in (None,packet.get('packet_id')):
                    raise ValueError('Idempotency key mismatch')
                result=receiver.receive(packet)
                self.reply(200 if result['duplicate'] else 202,result)
            except Conflict as exc:self.reply(409,{'error':str(exc)})
            except (ValueError,KeyError,TypeError) as exc:
                print('REJECTED_PACKET '+str(exc),flush=True)
                try:
                    rejection=receiver.directory/'rejected'
                    rejection.mkdir(exist_ok=True)
                    value=canonical(packet)
                    (rejection/(hashlib.sha256(value.encode()).hexdigest()+'.json')).write_text(value,encoding='utf-8')
                except (UnboundLocalError,ValueError,TypeError,OSError):
                    pass
                self.reply(400,{'error':str(exc)})
            except (OSError,sqlite3.Error):self.reply(503,{'error':'receiver temporarily unavailable'})
        def log_message(self,fmt,*args):
            print(dt.datetime.now(dt.timezone.utc).isoformat(),self.client_address[0],fmt%args,flush=True)
    return http.server.ThreadingHTTPServer((host,port),Handler)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    args=parser.parse_args()
    config=load_config(args.config, path_fields=('data_directory','repository'), tokens=('token',))
    receiver=Receiver(config.get('data_directory',REPO_ROOT/'data/radio_receiver'),config.get('repository',REPO_ROOT))
    server=make_server(receiver,config['token'],config.get('host','127.0.0.1'),config.get('port',8787))
    print('Receiver ready at %s:%s'%server.server_address,flush=True)
    try:server.serve_forever()
    finally:server.server_close()
