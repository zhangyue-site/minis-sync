"""Conservative online retention: quarantine unreferenced files; never purge data.

Share CloudStore's account locks and the RPC capacity lock. Recompute references
inside those locks. Both indexes are retained, so rollback needs no DB rewrite.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

MIN_AGE = 7 * 86400
TABLE_REFS = {
    'versions': ('sha',), 'devices': ('sha',),
    'device_applied_baselines': ('sha',), 'device_baselines': ('sha',), 'device_scoped_baselines': ('sha',),
    'inventory_heads': ('sha',), 'legacy_devices': ('source_sha',),
    'legacy_projections': ('head_sha', 'sha'),
}


@contextmanager
def locked(root, uid):
    if not re.fullmatch(r'[0-9]{1,32}', uid):
        raise ValueError('invalid_account')
    with (root / 'rpc-capacity.lock').open('a+b') as global_lock:
        fcntl.flock(global_lock, fcntl.LOCK_EX)
        with (root / 'locks' / (uid + '.lock')).open('a+b') as account_lock:
            fcntl.flock(account_lock, fcntl.LOCK_EX)
            yield


def read_db(path):
    c = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def references(c, uid):
    refs = set()
    # A schema mismatch is an error, not permission to remove data.
    for table, columns in TABLE_REFS.items():
        for column in columns:
            refs.update(r[0] for r in c.execute(
                f'SELECT {column} FROM {table} WHERE uid=?', (uid,)) if r[0])
    for row in c.execute('SELECT receipt FROM operations WHERE uid=?', (uid,)):
        receipt = json.loads(row[0])
        if not isinstance(receipt, dict): raise ValueError('invalid_receipt_preserved')
        for key in ('sha256', 'source_sha', 'legacy_source_sha', 'base_sha'):
            if receipt.get(key):
                refs.add(receipt[key])
    if any(not isinstance(s, str) or not re.fullmatch('[a-f0-9]{64}', s) for s in refs):
        raise ValueError('invalid_reference_preserved')
    return refs


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def quarantine(root, apply=False, now=None):
    root = Path(root).resolve(); now = time.time() if now is None else now
    result = {'mode': 'quarantine' if apply else 'dry-run', 'quarantined': 0, 'candidates': 0,
              'protected': 0, 'recent': 0, 'errors': [], 'deleted': 0}
    with read_db(root / 'snapshots/snapshots.db') as s:
        users = [str(r[0]) for r in s.execute('SELECT DISTINCT user_id FROM snapshots')]
    for uid in users:
        try:
            with locked(root, uid), read_db(root / 'heads.sqlite3') as h, read_db(root / 'snapshots/snapshots.db') as s:
                refs = references(h, uid)
                # Missing live references must be repaired before any retention.
                required = {r[0] for r in h.execute('SELECT sha FROM versions WHERE uid=?', (uid,))}
                required.update(r[0] for r in h.execute('SELECT source_sha FROM legacy_devices WHERE uid=?', (uid,)))
                if any(not (root / 'snapshots' / uid / (sha + '.minisbak')).is_file() for sha in required):
                    raise RuntimeError('missing_baseline_preserved')
                for row in s.execute('SELECT sha256,bytes,created_at FROM snapshots WHERE user_id=?', (uid,)):
                    sha = row['sha256']
                    if not re.fullmatch('[a-f0-9]{64}', sha):
                        raise ValueError('invalid_snapshot_digest')
                    if sha in refs:
                        result['protected'] += 1; continue
                    source = root / 'snapshots' / uid / (sha + '.minisbak')
                    if not source.exists():
                        continue
                    if source.is_symlink() or source.stat().st_size != row['bytes']:
                        raise RuntimeError('invalid_source_preserved')
                    if now - max(row['created_at'], source.stat().st_mtime) < MIN_AGE:
                        result['recent'] += 1; continue
                    result['candidates'] += 1
                    if not apply:
                        continue
                    if digest(source) != sha:
                        raise RuntimeError('corrupt_source_preserved')
                    folder = root / 'quarantine' / uid
                    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
                    target = folder / source.name
                    if target.exists():
                        # No overwrite and no unlink of an existing original.
                        raise RuntimeError('quarantine_target_exists')
                    # Same-filesystem atomic move. Indexes, heads and receipts stay intact.
                    os.rename(source, target)
                    fd = os.open(folder, os.O_RDONLY)
                    try: os.fsync(fd)
                    finally: os.close(fd)
                    result['quarantined'] += 1
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as e:
            result['errors'].append({'uid': uid, 'error': str(e)})
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', default='/opt/minis-v3/store')
    p.add_argument('--apply', action='store_true', help='Quarantine only; never delete files or DB rows')
    a = p.parse_args(); result = quarantine(a.root, a.apply)
    print(json.dumps(result)); return 1 if result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
