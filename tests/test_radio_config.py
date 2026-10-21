import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools/radio_receiver'))
from runtime_config import load_config

class ReceiverConfigTests(unittest.TestCase):
    def test_relative_paths_use_config_directory_including_spaces(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)/'config with spaces';directory.mkdir()
            config=directory/'receiver.private.json'
            config.write_text(json.dumps({'token':'synthetic-token-for-tests-only-000000','data_directory':'../data with spaces'}),encoding='utf-8-sig')
            result=load_config(config,path_fields=('data_directory',),tokens=('token',))
            self.assertEqual(Path(result['data_directory']),(Path(folder)/'data with spaces').resolve())
    def test_configuration_templates_cannot_start_with_placeholder_tokens(self):
        examples=Path(__file__).resolve().parents[1]/'tools/radio_receiver/examples'
        for name,tokens in [('receiver.example.json',('token',)),('relay-client.example.json',('relay_token','local_token')),('relay-server.example.json',('token',))]:
            with self.subTest(name=name),self.assertRaises(ValueError):load_config(examples/name,tokens=tokens)

if __name__=='__main__':unittest.main()
