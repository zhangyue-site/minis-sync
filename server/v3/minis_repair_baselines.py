"""Explicit administrator repair of a referenced baseline from a verified source.

No cross-account discovery is exposed to the HTTP/RPC interface. The operator
must supply the account, expected digest, and trusted source file explicitly.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from minis_retention import digest, locked
from minis_store import validate_package


def restore(root, uid, sha, source):
    root = Path(root).resolve(); uid = str(uid); source = Path(source)
    import re
    if not re.fullmatch('[a-f0-9]{64}', sha): raise ValueError('invalid_digest')
    with locked(root, uid):
        c = sqlite3.connect((root / 'heads.sqlite3').as_uri()+'?mode=ro', uri=True)
        try:
            if not c.execute('SELECT 1 FROM legacy_devices WHERE uid=? AND source_sha=?', (uid, sha)).fetchone():
                raise ValueError('not_referenced_by_account')
        finally: c.close()
        if source.is_symlink() or digest(source) != sha:
            raise ValueError('source_digest_mismatch')
        metadata = validate_package(source)
        folder = root / 'snapshots' / uid; folder.mkdir(mode=0o700, exist_ok=True)
        target = folder / (sha + '.minisbak')
        fd, name = tempfile.mkstemp(prefix='.baseline-repair-', dir=folder)
        try:
            with os.fdopen(fd, 'wb') as out, source.open('rb') as src:
                import shutil
                shutil.copyfileobj(src, out, 1024*1024); out.flush(); os.fsync(out.fileno())
            temporary = Path(name)
            if digest(temporary) != sha: raise ValueError('source_changed_during_copy')
            try: os.link(temporary, target)
            except FileExistsError:
                if target.is_symlink() or digest(target) != sha:
                    raise ValueError('existing_target_preserved')
            with sqlite3.connect(root / 'snapshots/snapshots.db') as s:
                row = s.execute('SELECT bytes FROM snapshots WHERE user_id=? AND sha256=?', (uid, sha)).fetchone()
                if row and row[0] != target.stat().st_size:
                    raise ValueError('existing_index_preserved')
                s.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?,?,?)',
                          (uid,sha,'repaired-baseline.minisbak',target.stat().st_size,
                           'baseline-repair',int(time.time()),json.dumps(metadata)))
            d = os.open(folder, os.O_RDONLY)
            try: os.fsync(d)
            finally: os.close(d)
            return {'uid':uid, 'sha256':sha, 'bytes':target.stat().st_size, 'verified':True}
        finally: Path(name).unlink(missing_ok=True)


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',default='/opt/minis-v3/store');p.add_argument('--uid',required=True)
    p.add_argument('--sha',required=True);p.add_argument('--source',required=True)
    a=p.parse_args();print(json.dumps(restore(a.root,a.uid,a.sha,a.source)))
