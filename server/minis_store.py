"""Immutable, account-isolated native Minis backup transport. Never rewrites packages."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
import time
import zipfile

MAX_UPLOAD = 2 * 1024 * 1024 * 1024
MAX_EXPANDED = 4 * 1024 * 1024 * 1024

class InvalidBackup(ValueError):
    pass


def validate_package(path):
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if len(infos) > 100000 or sum(i.file_size for i in infos) > MAX_EXPANDED:
                raise InvalidBackup('backup_too_large')
            names = [i.filename for i in infos]
            if len(names) != len(set(names)):
                raise InvalidBackup('duplicate_archive_member')
            for name in names:
                p = PurePosixPath(name)
                if p.is_absolute() or '..' in p.parts or '\\' in name:
                    raise InvalidBackup('unsafe_archive_path')
            manifests = [n for n in names if n == 'manifest.json' or n.endswith('/manifest.json')]
            if len(manifests) != 1:
                raise InvalidBackup('manifest_required')
            member = manifests[0]
            if z.getinfo(member).file_size > 8 * 1024 * 1024:
                raise InvalidBackup('manifest_too_large')
            m = json.loads(z.read(member))
            if not isinstance(m, dict) or not isinstance(m.get('categories'), dict):
                raise InvalidBackup('bad_manifest')
            integrity = m.get('integrity')
            if not isinstance(integrity, dict) or not integrity:
                raise InvalidBackup('integrity_required')
            prefix = member[:-len('manifest.json')]
            checked = set()
            for name, expected in integrity.items():
                if not isinstance(name, str) or not isinstance(expected, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', expected):
                    raise InvalidBackup('bad_integrity_entry')
                info = z.getinfo(prefix + name)
                h = hashlib.sha256()
                with z.open(info) as src:
                    while chunk := src.read(1024 * 1024):
                        h.update(chunk)
                if h.hexdigest() != expected.lower():
                    raise InvalidBackup('integrity_mismatch')
                checked.add(name)
            # CRC-check all remaining members as well; never extract user files here.
            for info in infos:
                if info.is_dir() or info.filename == member or info.filename.removeprefix(prefix) in checked:
                    continue
                with z.open(info) as src:
                    while src.read(1024 * 1024):
                        pass
            if not m.get('encryption') and prefix + 'files.index.jsonl' in names:
                with z.open(prefix + 'files.index.jsonl') as f:
                    for raw in f:
                        if len(raw) > 1024 * 1024:
                            raise InvalidBackup('index_record_too_large')
                        row = json.loads(raw)
                        sha = row.get('sha256')
                        if sha:
                            if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
                                raise InvalidBackup('bad_blob_hash')
                            blob = 'blobs/' + sha[:2] + '/' + sha
                            if blob not in integrity:
                                raise InvalidBackup('missing_blob_integrity')
                            if integrity[blob].lower() != sha:
                                raise InvalidBackup('blob_content_hash_mismatch')
            return {'encrypted': bool(m.get('encryption')), 'categories': list(m['categories']),
                    'backup_id': str(m.get('backup_id', ''))[:100]}
    except InvalidBackup:
        raise
    except (zipfile.BadZipFile, KeyError, ValueError, TypeError, RuntimeError, OSError) as e:
        raise InvalidBackup('invalid_native_backup') from e


class SnapshotStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database = self.root / 'snapshots.db'
        with self._db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS snapshots (user_id TEXT NOT NULL, sha256 TEXT NOT NULL, name TEXT NOT NULL, bytes INTEGER NOT NULL, device TEXT NOT NULL, created_at INTEGER NOT NULL, metadata TEXT NOT NULL, PRIMARY KEY(user_id,sha256))')
        os.chmod(self.database, 0o600)

    def _db(self):
        c = sqlite3.connect(self.database, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def _uid(uid):
        value = str(uid)
        if not re.fullmatch(r'[0-9]+', value):
            raise InvalidBackup('invalid_account')
        return value

    @staticmethod
    def _meta(row):
        d = dict(row)
        d.pop('user_id', None)
        d.update(json.loads(d.pop('metadata')))
        return d

    def list(self, uid):
        uid = self._uid(uid)
        with self._db() as c:
            return [self._meta(r) for r in c.execute('SELECT * FROM snapshots WHERE user_id=? ORDER BY created_at,sha256', (uid,))]

    def path(self, uid, sha):
        uid = self._uid(uid)
        if not re.fullmatch(r'[a-f0-9]{64}', sha):
            raise InvalidBackup('invalid_digest')
        with self._db() as c:
            row = c.execute('SELECT sha256 FROM snapshots WHERE user_id=? AND sha256=?', (uid,sha)).fetchone()
        p = self.root / uid / (sha + '.minisbak')
        if not row or not p.is_file():
            raise FileNotFoundError('snapshot_not_found')
        return p

    def put(self, uid, stream, length, name, device):
        uid = self._uid(uid)
        if not isinstance(length, int) or length <= 0 or length > MAX_UPLOAD:
            raise InvalidBackup('size_out_of_range')
        import shutil
        if shutil.disk_usage(self.root).free < length + 2 * 1024 ** 3:
            raise InvalidBackup('storage_capacity_exhausted_originals_preserved')
        name = str(name).replace('\\','/').rsplit('/',1)[-1][:180]
        if not name.lower().endswith('.minisbak') or any(ord(x) < 32 for x in name):
            raise InvalidBackup('bad_name')
        device = str(device)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', device):
            raise InvalidBackup('bad_device')
        folder = self.root / uid
        folder.mkdir(exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(prefix='.upload-', dir=folder)
        try:
            h = hashlib.sha256()
            left = length
            with os.fdopen(fd,'wb') as dest:
                while left:
                    chunk = stream.read(min(left,1024*1024))
                    if not chunk:
                        raise InvalidBackup('short_upload')
                    dest.write(chunk); h.update(chunk); left -= len(chunk)
                dest.flush(); os.fsync(dest.fileno())
            metadata = validate_package(tmp)
            sha = h.hexdigest()
            final = folder / (sha + '.minisbak')
            # Atomic immutable create, with concurrent duplicate writers supported.
            try:
                os.link(tmp, final)
            except FileExistsError:
                pass
            with self._db() as c:
                c.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?,?,?)',
                    (uid,sha,name,length,device,int(time.time()),json.dumps(metadata)))
                row = c.execute('SELECT * FROM snapshots WHERE user_id=? AND sha256=?',(uid,sha)).fetchone()
            return self._meta(row)
        finally:
            Path(tmp).unlink(missing_ok=True)
