from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from plura_desktop.routing import ResponsesRoute
class StartupCatalogTests(unittest.TestCase):
    def test_launch_catalog_is_explicit_and_bound_to_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'models.json'
            path.write_text(json.dumps({'models':[{'slug':'native'}]}))
            route = ResponsesRoute.create('http://127.0.0.1:1234/v1','TELA_TOKEN','s'*48,model_catalog_json=str(path))
            self.assertIn('model_catalog_json='+json.dumps(str(path.resolve())),route.codex_config_args())
            old=route.fingerprint
            path.write_text(json.dumps({'models':[{'slug':'changed'}]}))
            self.assertNotEqual(old,ResponsesRoute.create('http://127.0.0.1:1234/v1','TELA_TOKEN','s'*48,model_catalog_json=str(path)).fingerprint)
            self.assertFalse('TELA_TOKEN' in route.thread_config_overlay().values())
    def test_invalid_or_linked_catalog_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'models.json';path.write_text('{}')
            with self.assertRaises(ValueError):
                ResponsesRoute.create('http://127.0.0.1:1234/v1','TELA_TOKEN','s'*48,model_catalog_json=str(path))
            path.write_text(json.dumps({'models':[{'slug':'native'}]}))
            link=Path(directory)/'link.json';link.symlink_to(path)
            with self.assertRaises(ValueError):
                ResponsesRoute.create('http://127.0.0.1:1234/v1','TELA_TOKEN','s'*48,model_catalog_json=str(link))
