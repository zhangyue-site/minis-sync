import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from minis_store import SnapshotStore, InvalidBackup, validate_package
from minis_delta import merge_delta_backup

ROOT = Path(sys.argv[1])
ROOT.mkdir(parents=True, exist_ok=True)
STORE = SnapshotStore(ROOT / 'store')
TOKEN = 'minis-isolated-acceptance-20261018'
UNCHANGED = os.urandom(2 * 1024 * 1024)


def make_backup(name, changed, obsolete):
    entries = {'data/chats.jsonl': changed, 'blobs/unchanged': UNCHANGED}
    if obsolete:
        entries['blobs/obsolete'] = b'remove this blob'
    else:
        entries['blobs/added'] = b'new attachment'
    entries['manifest.json'] = json.dumps({'categories': {'chats': True}, 'integrity': {
        key: hashlib.sha256(value).hexdigest() for key, value in entries.items()
    }}).encode()
    path = ROOT / name
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for key, value in entries.items():
            archive.writestr(key, value)
    return path


make_backup('base.minisbak', b'{"message":"old"}\n', True)
make_backup('changed.minisbak', b'{"message":"new"}\n', False)


class Handler(BaseHTTPRequestHandler):
    def respond(self, code, data):
        payload = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.headers.get('Authorization') != 'Bearer ' + TOKEN:
            return self.respond(401, {'error': 'unauthorized'})
        path = urllib.parse.urlparse(self.path).path
        missing = path.startswith('/missing')
        path = path.removeprefix('/missing').removeprefix('/reject')
        items = STORE.list('1')
        if path == '/api/minis/snapshots':
            return self.respond(200, {'ok': True, 'items': items})
        if path.endswith('/manifest'):
            if missing:
                return self.respond(404, {'error': 'base_not_found'})
            snapshot = STORE.path('1', path.split('/')[-2])
            with zipfile.ZipFile(snapshot) as archive:
                entries = [[entry.filename, entry.file_size, entry.CRC] for entry in archive.infolist() if not entry.is_dir()]
            return self.respond(200, {'ok': True, 'entries': entries})
        if path == '/assert':
            latest = max(STORE.list('1'), key=lambda item: item['created_at'])
            snapshot = STORE.path('1', latest['sha256'])
            validate_package(snapshot)
            with zipfile.ZipFile(snapshot) as actual, zipfile.ZipFile(ROOT / 'changed.minisbak') as expected:
                equal = set(actual.namelist()) == set(expected.namelist()) and all(actual.read(name) == expected.read(name) for name in expected.namelist())
            return self.respond(200, {'equal': equal})
        return self.respond(404, {'error': 'not_found'})

    def do_POST(self):
        if self.headers.get('Authorization') != 'Bearer ' + TOKEN:
            return self.respond(401, {'error': 'unauthorized'})
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        reject = path.startswith('/reject')
        path = path.removeprefix('/missing').removeprefix('/reject')
        query = urllib.parse.parse_qs(parsed.query)
        length = int(self.headers['Content-Length'])
        try:
            if path == '/api/minis/delta':
                if reject:
                    self.rfile.read(length)
                    return self.respond(503, {'error': 'injected_failure'})
                meta = merge_delta_backup(STORE, '1', query['base'][0], self.rfile, length, query['name'][0], query['device'][0])
            elif path == '/api/minis/snapshots':
                meta = STORE.put('1', self.rfile, length, query['name'][0], query['device'][0])
            else:
                return self.respond(404, {'error': 'not_found'})
            return self.respond(200, dict(meta, ok=True))
        except (InvalidBackup, FileNotFoundError) as error:
            return self.respond(400, {'error': str(error)})


print('ready', ROOT, flush=True)
ThreadingHTTPServer(('172.17.0.1', 18321), Handler).serve_forever()
