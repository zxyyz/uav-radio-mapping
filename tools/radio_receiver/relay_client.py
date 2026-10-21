"""Pull server queue into the local database, then acknowledge durable receipt."""
import argparse
import json
import os
from pathlib import Path
import ssl
import subprocess
import time
import urllib.request
from runtime_config import CONFIG_DIR, load_config

class RelayConnection:
    def __init__(self, config):
        self.config=config
        context=ssl.create_default_context(cafile=config.get('ca_file'))
        self.remote=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context))
        self.local=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def call(self, opener,url,token,payload=None):
        data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode()
        req=urllib.request.Request(url,data=data,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with opener.open(req,timeout=12) as response:return json.load(response)
    def remote_call(self,path,payload=None):
        return self.call(self.remote,self.config['relay_url']+path,self.config['relay_token'],payload)
    def status(self):
        result=self.remote_call('/status')
        if type(result.get('pending_packets')) is not int or result['pending_packets']<0:
            raise ValueError('Server did not return a valid pending count')
        return result
    def transfer(self):
        items=self.remote_call('/api/v1/relay/pending')['packets']
        count=0
        for item in items:
            result=self.call(self.local,self.config['local_url']+'/api/v1/radio/packets',self.config['local_token'],item['packet'])
            if not result.get('accepted'):raise ValueError('Local receiver did not accept packet')
            self.remote_call('/api/v1/relay/ack',{'relay_ids':[item['relay_id']]})
            count+=1
            print('DELIVERED '+item['packet']['packet_id']+' duplicate='+str(bool(result.get('duplicate'))),flush=True)
        return count

def transfer(config):
    return RelayConnection(config).transfer()

class IdleStop:
    def __init__(self,seconds=10):
        self.seconds=seconds;self.empty_since=None
    def observe(self,pending,now,received=0):
        if received>0:self.empty_since=None
        if pending is None or pending>0:
            self.empty_since=None;return False
        if self.empty_since is None:self.empty_since=now
        return now-self.empty_since>=self.seconds
    def elapsed(self,now):
        return 0 if self.empty_since is None else max(0,now-self.empty_since)

def write_state(path,**state):
    state.update(pid=os.getpid(),updated_at_ns=str(time.time_ns()))
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8');temporary.replace(path)

def stop_local_reception():
    if os.name!='nt':return  # Non-Windows CLI exits; the receiver is managed separately.
    script=Path(__file__).with_name('Stop-RadioRelay.ps1')
    subprocess.Popen([str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'),
        '-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-ExpectedClientPid',str(os.getpid()),'-Reason','server_empty'],
        creationflags=subprocess.CREATE_NO_WINDOW)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--once',action='store_true');args=parser.parse_args()
    config=load_config(args.config, path_fields=('ca_file',), tokens=('relay_token','local_token'))
    connection=RelayConnection(config);idle=IdleStop()
    state_path=CONFIG_DIR/'relay-client-status.private.json'
    state_path.parent.mkdir(parents=True,exist_ok=True)
    print('RELAY_CLIENT ready; auto-stop after 10 seconds of confirmed empty queue',flush=True)
    while True:
        try:
            count=connection.transfer()
            if args.once:print('Transferred packets: '+str(count),flush=True);break
            status=connection.status();now=time.monotonic()
            complete=idle.observe(status['pending_packets'],now,received=count)
            write_state(state_path,phase='auto_stopped' if complete else 'receiving',server=status,
                        empty_seconds=round(idle.elapsed(now),1),stop_after_seconds=idle.seconds,error=None)
            if complete:
                print('AUTO_STOP server queue confirmed empty',flush=True)
                stop_local_reception();break
        except Exception as error:
            print('RETRY '+type(error).__name__,flush=True)
            if args.once:raise SystemExit(1)
            idle.observe(None,time.monotonic())
            write_state(state_path,phase='retrying',server=None,empty_seconds=0,stop_after_seconds=idle.seconds,error=type(error).__name__)
        time.sleep(2)
