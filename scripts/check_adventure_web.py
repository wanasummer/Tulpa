"""Human playtest API checks, isolated fixture model and SQLite."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app_adventure import create_app
from chatlocal.adventure import Adventure
from check_adventure import FakeModel, FixedRandom


class Checks(unittest.TestCase):
    def test_human_action_persistence_privacy_and_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            model=FakeModel()
            game=Adventure(Path(folder)/'test.sqlite3',model,rng=FixedRandom())
            app=create_app(game)
            headers={'X-Playtest-Action':'play','Origin':'http://testserver'}
            with TestClient(app) as client:
                self.assertEqual(client.get('/').status_code,200)
                first=client.get('/api/state').json()
                self.assertEqual(first['remaining'],3)
                self.assertEqual(model.calls,[])
                self.assertEqual(client.post('/api/start',json={'request_id':'a'}).status_code,403)
                self.assertEqual(client.post('/api/start',headers={**headers,'Origin':'https://evil.example'},json={'request_id':'a'}).status_code,403)
                started=client.post('/api/start',headers=headers,json={'request_id':'a'}).json()
                self.assertEqual(started['current']['phase'],'ready')
                self.assertNotIn('roll',started['current'])
                self.assertNotIn('before',started['current'])
                self.assertEqual(started['remaining'],2)
                done=client.post('/api/act',headers=headers,json={'request_id':'b','action':'用热水融冰。'}).json()
                self.assertEqual(done['state']['level'],1)
                self.assertEqual(done['history'][0]['action'],'用热水融冰。')
                self.assertNotIn('roll',done['history'][0]['result'])
                calls=len(model.calls)
                duplicate=client.post('/api/act',headers=headers,json={'request_id':'b','action':'用热水融冰。'}).json()
                self.assertEqual(duplicate['state']['level'],1)
                self.assertEqual(len(model.calls),calls)
                self.assertEqual(client.post('/api/act',headers=headers,json={'request_id':'c','action':'行动','player':'victim'}).status_code,422)
                self.assertEqual(client.get('/api/state').json()['state']['level'],1)
                self.assertEqual(client.get('/api/state',headers={'Host':'evil.example'}).status_code,400)
                with TestClient(app) as other:
                    self.assertEqual(other.get('/api/state').json()['state']['level'],0)


if __name__=='__main__':
    unittest.main(verbosity=2)
