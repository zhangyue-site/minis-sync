import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
import os
from unittest.mock import patch
from http.server import ThreadingHTTPServer

sys.path.insert(0, '/opt/notes-site')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import notes_site
from minis_store import SnapshotStore, validate_package
from test_delta import backup, archive


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {'MINIS_ARCHIVE_POLICY': str(root / 'policy.json')})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        (root / 'policy.json').write_text(json.dumps({'state_root': str(root / 'archive'), 'lighthouse_instance_id': None}))
        notes_site.DB = root / 'notes.db'
        self.store = SnapshotStore(root / 'snapshots')
        notes_site.minis_snapshots = lambda: self.store
        notes_site.COS_REDIRECT = False
        notes_site._cos_async_upload = lambda *arguments: None
        with notes_site.db() as connection:
            connection.executescript(notes_site.SCHEMA)
            for uid in (1, 2):
                connection.execute('INSERT INTO users(id,username,key_hash,salt,created_at) VALUES(?,?,?,?,?)',
                                   (uid, 'test' + str(uid), 'unused', 'unused', 'test'))
                connection.execute('INSERT INTO tokens(token,user_id,created_at,expires_at) VALUES(?,?,?,?)',
                                   ('test-token-' + str(uid), uid, int(time.time()), int(time.time()) + 60))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), notes_site.Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.address = 'http://127.0.0.1:' + str(self.server.server_port)

    def request(self, path, body=None, token='test-token-1'):
        request = urllib.request.Request(self.address + path, data=body, headers={'Authorization': 'Bearer ' + token})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_real_handlers_upload_delta_download_and_isolation(self):
        original = backup({'data/chats.jsonl': b'old', 'blobs/old': b'deleted', 'blobs/keep': b'keep'})
        status, data = self.request('/api/minis/snapshots?name=base.minisbak&device=http-test', original)
        self.assertEqual(status, 200)
        base = json.loads(data)['sha256']
        self.assertEqual(self.request('/api/minis/snapshots/' + base + '/manifest')[0], 200)
        desired = backup({'data/chats.jsonl': b'new', 'blobs/new': b'added', 'blobs/keep': b'keep'})
        with zipfile.ZipFile(io.BytesIO(desired)) as package:
            entries = {name: package.read(name) for name in package.namelist() if name != 'blobs/keep'}
        entries['__delta__.json'] = json.dumps({'base': base, 'v': 2, 'deleted': ['blobs/old']}).encode()
        status, response = self.request('/api/minis/delta?base=' + base + '&name=delta.minisbak&device=http-test', archive(entries))
        self.assertEqual(status, 200, response)
        result = json.loads(response)
        self.assertEqual(result['protocol'], 2)
        status, downloaded = self.request('/api/minis/snapshots/' + result['sha256'])
        self.assertEqual(status, 200)
        self.assertEqual(hashlib.sha256(downloaded).hexdigest(), result['sha256'])
        with zipfile.ZipFile(io.BytesIO(downloaded)) as actual, zipfile.ZipFile(io.BytesIO(desired)) as expected:
            self.assertEqual(set(actual.namelist()), set(expected.namelist()))
            for name in expected.namelist():
                self.assertEqual(actual.read(name), expected.read(name))
        self.assertEqual(self.request('/api/minis/snapshots/' + base + '/manifest', token='test-token-2')[0], 404)
        self.assertEqual(self.request('/api/minis/snapshots/' + base, token='test-token-2')[0], 404)
        self.assertEqual(self.request('/api/minis/delta?base=' + base, archive(entries), token='invalid')[0], 401)

    def test_download_budget_denies_without_redirect_or_data_loss(self):
        original = backup({'data/chats.jsonl': b'budget-test'})
        status, data = self.request('/api/minis/snapshots?name=test.minisbak&device=http-test', original)
        self.assertEqual(status, 200)
        sha = json.loads(data)['sha256']
        configuration = Path(os.environ['MINIS_ARCHIVE_POLICY'])
        settings = json.loads(configuration.read_text())
        settings['max_server_download_bytes_month'] = 1
        configuration.write_text(json.dumps(settings))
        status, response = self.request('/api/minis/snapshots/' + sha)
        self.assertEqual(status, 429)
        self.assertEqual(json.loads(response)['error'], 'download_budget_exhausted')
        self.assertEqual(self.store.path('1', sha).read_bytes(), original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
