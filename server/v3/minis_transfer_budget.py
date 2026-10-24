"""Persistent rolling transfer reservations shared by all direct-front requests.

Reservations are deliberately not refunded on disconnect. Configure the cap for
the serving host, not a different server's prepaid package. No paid API calls.
"""
import json
import os
from pathlib import Path
import sqlite3
import time

GIB = 1024**3


class TransferBudget:
    def __init__(self, database, limit=150*GIB, window=31*86400):
        self.database = Path(database); self.limit = limit; self.window = window
        self.database.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS reservations(day INTEGER PRIMARY KEY, bytes INTEGER NOT NULL)')
        self.database.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.database, timeout=10)

    def reserve(self, size, now=None):
        if type(size) is not int or size < 0: raise ValueError('invalid_transfer_size')
        now = time.time() if now is None else now
        day = int(now) // 86400
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            used = c.execute('SELECT COALESCE(SUM(bytes),0) FROM reservations WHERE day>=?',
                             ((int(now)-self.window)//86400,)).fetchone()[0]
            if used + size > self.limit: return False
            c.execute('INSERT INTO reservations VALUES(?,?) ON CONFLICT(day) DO UPDATE SET bytes=bytes+excluded.bytes', (day,size))
        return True


def configured_budget():
    root = Path(os.environ.get('MINIS_FRONT_STATE', '/opt/minis-v3/front'))
    settings = root / 'transfer-policy.json'
    config = json.loads(settings.read_text()) if settings.exists() else {}
    limit = config.get('max_transfer_bytes_31_days', 150*GIB)
    if type(limit) is not int or limit <= 0: raise ValueError('invalid_transfer_policy')
    return TransferBudget(root / 'transfer-budget.sqlite3', limit)
