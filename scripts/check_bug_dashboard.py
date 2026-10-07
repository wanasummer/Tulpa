"""Dashboard acceptance checks, using an isolated SQLite fixture."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app_bug_dashboard import create_app, snapshot


class Checks(unittest.TestCase):
    def test_counts_join_missing_reply_and_no_write_routes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bugs.sqlite3'
            with sqlite3.connect(path) as db:
                db.executescript('''CREATE TABLE support_bugs(id INTEGER PRIMARY KEY,cid TEXT,mid TEXT,
                    day TEXT,title TEXT,details TEXT,status TEXT);
                    CREATE TABLE support_replies(cid TEXT,mid TEXT,sender TEXT,question TEXT);''')
                db.executemany('INSERT INTO support_bugs VALUES(?,?,?,?,?,?,?)', [
                    (1,'1:group:2','1','2026-10-05','未确认反馈','详情','待确认'),
                    (2,'1:group:3','2','2026-10-06','已解决反馈','详情','已解决')])
                db.execute('INSERT INTO support_replies VALUES(?,?,?,?)', ('1:group:2','1','群友','<script>untrusted</script>'))
            before = path.read_bytes()
            data = snapshot(path)
            self.assertEqual((data['total'],data['open'],data['resolved']), (2,1,1))
            self.assertEqual(len(data['groups']),2)
            self.assertEqual(data['bugs'][0]['sender'],'')
            self.assertEqual(data['bugs'][1]['question'],'<script>untrusted</script>')
            with TestClient(create_app(path)) as client:
                self.assertEqual(client.get('/').status_code,200)
                self.assertEqual(client.get('/api/bugs').json()['total'],2)
                self.assertEqual(client.post('/api/bugs',json={'status':'已解决'}).status_code,405)
                self.assertEqual(client.get('/assets/not-allowed').status_code,404)
                self.assertEqual(client.get('/api/bugs',headers={'Host':'evil.example'}).status_code,400)
            self.assertEqual(path.read_bytes(),before)
            with TestClient(create_app(path)) as client:
                self.assertEqual(client.delete('/api/bugs/1').status_code,403)
                headers = {'X-Dashboard-Action':'delete'}
                self.assertEqual(client.delete('/api/bugs/1',headers={**headers,'Origin':'https://evil.example'}).status_code,403)
                self.assertEqual(client.delete('/api/bugs/1',headers={**headers,'Origin':'http://testserver'}).json(), {'deleted':1})
                self.assertEqual(client.delete('/api/bugs/1',headers=headers).status_code,404)
                data = client.get('/api/bugs').json()
                self.assertEqual((data['total'],data['open'],data['resolved']), (1,0,1))
                self.assertEqual(len(data['groups']),1)
                self.assertEqual(data['bugs'][0]['id'],2)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM support_replies').fetchone()[0],1)

    def test_missing_database_does_not_create_empty_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'missing.sqlite3'
            with TestClient(create_app(path)) as client:
                self.assertEqual(client.get('/api/bugs').status_code,503)
                self.assertEqual(client.get('/healthz').status_code,503)
                self.assertEqual(client.delete('/api/bugs/1',headers={'X-Dashboard-Action':'delete'}).status_code,503)
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
