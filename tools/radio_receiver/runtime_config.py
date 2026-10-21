"""Private runtime configuration, separate from versioned receiver source."""
import json
import os
from pathlib import Path

REPO_ROOT=Path(__file__).resolve().parents[2]
CONFIG_DIR=Path(os.environ.get('UAV_RADIO_CONFIG_DIR',REPO_ROOT/'local/radio_receiver')).expanduser().resolve()

def load_config(path, *, path_fields=(), tokens=()):
    path=Path(path).expanduser().resolve()
    config=json.loads(path.read_text(encoding='utf-8-sig'))
    for field in tokens:
        token=config.get(field)
        if not isinstance(token,str) or len(token)<24 or token.upper().startswith(('CHANGE_ME','REPLACE_ME')):
            raise ValueError('Set a private random token in configuration field: '+field)
    for field in path_fields:
        if config.get(field):
            value=Path(config[field]).expanduser()
            config[field]=str((path.parent/value).resolve() if not value.is_absolute() else value.resolve())
    return config
