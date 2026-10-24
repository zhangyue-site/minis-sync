#!/usr/bin/env python3
"""Publish a verified auth replica atomically; failures never refresh freshness.

Only the receiving replica gets metadata. The source application DB is read only.
"""
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import tempfile
import time

REMOTE = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', 'root@124.222.88.195']
LOCAL = Path('/opt/minis-v3/front/notes.db')


def publish(temporary, destination, started_at):
    temporary, destination = Path(temporary), Path(destination)
    temporary.chmod(0o600)
    with sqlite3.connect(temporary) as c:
        if c.execute('PRAGMA quick_check').fetchone()[0] != 'ok': raise ValueError('invalid_auth_replica')
        c.execute('SELECT user_id,token,revoked,expires_at FROM tokens LIMIT 0')
        c.execute('CREATE TABLE IF NOT EXISTS minis_auth_replica(singleton INTEGER PRIMARY KEY CHECK(singleton=1), synced_at INTEGER NOT NULL)')
        c.execute('INSERT OR REPLACE INTO minis_auth_replica VALUES(1,?)', (int(started_at),))
    with temporary.open('rb') as f: os.fsync(f.fileno())
    os.replace(temporary, destination)
    fd = os.open(destination.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def main():
    started = int(time.time()); remote_path = None
    LOCAL.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.auth-replica-', dir=LOCAL.parent); os.close(fd)
    # mkstemp runs under root, never a shared predictable /tmp path.
    code = """import os,sqlite3,tempfile
from pathlib import Path
root=Path('/root/.local/state/minis-auth-export');root.mkdir(parents=True,exist_ok=True,mode=0o700)
fd,path=tempfile.mkstemp(prefix='notes-',suffix='.db',dir=root);os.close(fd)
try:
 s=sqlite3.connect('file:/opt/notes-site/notes.db?mode=ro',uri=True);d=sqlite3.connect(path)
 s.backup(d);d.close();s.close();print(path)
except BaseException:
 os.unlink(path);raise
"""
    try:
        r = subprocess.run(REMOTE+[shlex.join(['python3','-c',code])], capture_output=True, text=True, timeout=60, check=True)
        remote_path = r.stdout.strip()
        import re
        if not re.fullmatch(r'/root/\.local/state/minis-auth-export/notes-[A-Za-z0-9_]+\.db', remote_path):
            raise ValueError('unexpected_remote_export')
        subprocess.run(['scp','-q','-o','BatchMode=yes','-o','ConnectTimeout=8',
                        'root@124.222.88.195:'+remote_path,name], timeout=120, check=True, capture_output=True)
        publish(name,LOCAL,started)
        print('auth_replica_verified synced_at='+str(started))
    finally:
        Path(name).unlink(missing_ok=True)
        if remote_path and remote_path.startswith('/root/.local/state/minis-auth-export/notes-'):
            subprocess.run(REMOTE+[shlex.join(['rm','--',remote_path])],timeout=15,capture_output=True)


if __name__ == '__main__': main()
