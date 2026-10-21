import json
from runtime_config import CONFIG_DIR, load_config
from pathlib import Path
import urllib.request

config=load_config(CONFIG_DIR/'receiver.private.json', tokens=('token',))
request=urllib.request.Request('http://'+config.get('host','127.0.0.1')+':'+str(config.get('port',8787))+'/status',headers={'Authorization':'Bearer '+config['token']})
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(request,timeout=5) as response:
    result=json.load(response)
CONFIG_DIR/'latest-status.json'.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
