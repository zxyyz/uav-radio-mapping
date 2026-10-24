import json
from runtime_config import CONFIG_DIR, load_config
from pathlib import Path
import argparse
import time
from relay_client import RelayConnection
parser=argparse.ArgumentParser();parser.add_argument('--output');args=parser.parse_args()
base=Path(__file__).resolve().parent
config=load_config(CONFIG_DIR/'relay-client.private.json', path_fields=('ca_file','sync_cursor_file'), tokens=('relay_token','local_token'))
try:
    status=RelayConnection(config).status()
    result={'ok':True,'queried_at_ns':str(time.time_ns()),'server':status} if args.output else status
    code=0
except Exception as error:
    result={'ok':False,'queried_at_ns':str(time.time_ns()),'server':None,'error':type(error).__name__}
    code=1
path=Path(args.output) if args.output else CONFIG_DIR/'latest-relay-status.json'
temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(result,indent=2),encoding='utf-8');temporary.replace(path)
print(json.dumps(result));raise SystemExit(code)
