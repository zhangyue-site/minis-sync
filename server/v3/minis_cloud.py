"""Independent versioned cloud. No legacy snapshot directory is read or migrated."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from minis_store import SnapshotStore, InvalidBackup, MAX_UPLOAD
from minis_merge import merge_backups, iter_record_keys
from minis_delta import merge_delta_backup
from minis_chat_projection import project_chats

class CloudConflict(InvalidBackup):
    status = 409
    status_code = 409

class CloudStore:
    def __init__(self, root):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.snapshots=SnapshotStore(self.root/'snapshots')
        self.database=self.root/'heads.sqlite3'
        self.locks=self.root/'locks'; self.locks.mkdir(exist_ok=True,mode=0o700)
        with self._db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS legacy_projections(uid TEXT,head_sha TEXT,sha TEXT,PRIMARY KEY(uid,head_sha))')
            c.execute('CREATE TABLE IF NOT EXISTS known_records(uid TEXT,path TEXT,k TEXT,deleted INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(uid,path,k)) WITHOUT ROWID')
            c.execute('CREATE TABLE IF NOT EXISTS inventory_heads(uid TEXT PRIMARY KEY,sha TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS legacy_devices(uid TEXT,device TEXT,source_sha TEXT,PRIMARY KEY(uid,device))')
            c.execute('CREATE TABLE IF NOT EXISTS device_profile_baselines(uid TEXT,device TEXT,sha TEXT,applied_at INTEGER,PRIMARY KEY(uid,device,sha))')
            c.execute('CREATE TABLE IF NOT EXISTS versions(uid TEXT, version INTEGER, sha TEXT, receipt TEXT, PRIMARY KEY(uid,version))')
            c.execute('CREATE TABLE IF NOT EXISTS heads(uid TEXT PRIMARY KEY, version INTEGER)')
            c.execute('CREATE TABLE IF NOT EXISTS operations(uid TEXT, device TEXT, op TEXT, signature TEXT, receipt TEXT, PRIMARY KEY(uid,device,op))')
            c.execute('CREATE TABLE IF NOT EXISTS devices(uid TEXT, device TEXT, sha TEXT, applied_at INTEGER, PRIMARY KEY(uid,device))')
            c.execute('CREATE TABLE IF NOT EXISTS device_baselines(uid TEXT, device TEXT, sha TEXT, applied_at INTEGER, PRIMARY KEY(uid,device,sha))')
            c.execute('CREATE TABLE IF NOT EXISTS device_scoped_baselines(uid TEXT, device TEXT, sha TEXT, scope TEXT, applied_at INTEGER, PRIMARY KEY(uid,device,sha,scope))')
            c.execute("INSERT OR IGNORE INTO device_scoped_baselines SELECT uid,device,sha,'all',applied_at FROM device_baselines")
            if 'applied_scope' not in [r['name'] for r in c.execute('PRAGMA table_info(devices)')]:
                c.execute("ALTER TABLE devices ADD COLUMN applied_scope TEXT NOT NULL DEFAULT 'all'")
            c.execute('CREATE TABLE IF NOT EXISTS device_applied_baselines(uid TEXT,device TEXT,sha TEXT,scope TEXT,applied_at INTEGER,PRIMARY KEY(uid,device,sha,scope))')
            c.execute('INSERT OR IGNORE INTO device_applied_baselines SELECT uid,device,sha,applied_scope,applied_at FROM devices')
        os.chmod(self.database,0o600)
    @contextmanager
    def _db(self):
        c=sqlite3.connect(self.database,timeout=30); c.row_factory=sqlite3.Row
        c.execute('PRAGMA synchronous=FULL')
        try:
            with c:yield c
        finally:c.close()
    @staticmethod
    def _key(value,kind):
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value):raise InvalidBackup('bad_'+kind)
        return value
    @contextmanager
    def _lock(self,uid):
        with open(self.locks/(uid+'.lock'),'a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(lock,fcntl.LOCK_UN)
    def _known(self,uid,sha):
        with self._db() as c:
            return c.execute('SELECT 1 FROM versions WHERE uid=? AND sha=?',(uid,sha)).fetchone() is not None
    def head(self,uid):
        uid=self.snapshots._uid(uid)
        with self._db() as c:
            row=c.execute('SELECT receipt FROM versions JOIN heads USING(uid,version) WHERE uid=?',(uid,)).fetchone()
        return json.loads(row[0]) if row else None
    def path(self,uid,sha):
        uid=self.snapshots._uid(uid)
        if not self._known(uid,sha):raise FileNotFoundError('cloud_version_not_found')
        return self.snapshots.path(uid,sha)
    def manifest(self,uid,sha):
        path=self.path(uid,sha)
        with zipfile.ZipFile(path) as z:
            entries=[{'path':i.filename,'bytes':i.file_size,'compressed_bytes':i.compress_size,'crc32':i.CRC} for i in z.infolist() if not i.is_dir()]
            member=next(n for n in z.namelist() if n=='manifest.json' or n.endswith('/manifest.json'))
            manifest=json.loads(z.read(member))
        return {'sha256':sha,'bytes':path.stat().st_size,'entries':entries,'manifest':manifest}
    @staticmethod
    def _scope(scope):
        if scope not in ('all','chats','profile'):raise InvalidBackup('bad_scope')
        return scope
    def ack(self,uid,device,sha,scope='all'):
        uid=self.snapshots._uid(uid); device=self._key(device,'device'); scope=self._scope(scope)
        with self._lock(uid):
            if not self._known(uid,sha):raise CloudConflict('applied_version_not_found')
            if scope=='profile':
                with self._db() as c:c.execute('INSERT OR IGNORE INTO device_profile_baselines VALUES(?,?,?,?)',(uid,device,sha,int(time.time())))
                return {'device':device,'sha256':sha,'applied':True,'applied_scope':'profile'}
            with self._db() as c:
                row=c.execute('SELECT receipt FROM versions WHERE uid=? AND sha=? ORDER BY version DESC LIMIT 1',(uid,sha)).fetchone()
            if scope=='all' and json.loads(row['receipt']).get('scope','all')=='chats':raise CloudConflict('version_scope_mismatch')
            applied_at=int(time.time())
            with self._db() as c:
                c.execute('INSERT OR REPLACE INTO devices(uid,device,sha,applied_at,applied_scope) VALUES(?,?,?,?,?)',(uid,device,sha,applied_at,scope))
                c.execute('INSERT OR IGNORE INTO device_scoped_baselines VALUES(?,?,?,?,?)',(uid,device,sha,scope,applied_at))
                c.execute('INSERT OR IGNORE INTO device_applied_baselines VALUES(?,?,?,?,?)',(uid,device,sha,scope,applied_at))
        return {'device':device,'sha256':sha,'applied_at':applied_at,'applied':True,'applied_scope':scope}
    def commit(self,uid,device,base_sha,op_id,stream,length,name,mode='full',scope='all'):
        uid=self.snapshots._uid(uid); device=self._key(device,'device'); op_id=self._key(op_id,'operation')
        scope=self._scope(scope)
        if scope=='profile':raise InvalidBackup('profile_is_ack_only')
        if mode not in ('full','delta'):raise InvalidBackup('bad_mode')
        if base_sha == '':base_sha=None
        if base_sha is not None and (not isinstance(base_sha,str) or not re.fullmatch('[a-f0-9]{64}',base_sha)):raise CloudConflict('base_version_not_found')
        if type(length) is not int or length<=0 or length>MAX_UPLOAD:raise InvalidBackup('size_out_of_range')
        if shutil.disk_usage(self.root).free < length+2*1024**3:raise InvalidBackup('storage_capacity_exhausted_originals_preserved')
        with tempfile.TemporaryDirectory(prefix='.commit-',dir=self.root) as tmp:
            body=Path(tmp)/'body'; digest=hashlib.sha256(); left=length
            with body.open('wb') as output:
                while left:
                    chunk=stream.read(min(left,1024*1024))
                    if not chunk or len(chunk)>left:raise InvalidBackup('short_upload')
                    output.write(chunk); digest.update(chunk); left-=len(chunk)
                output.flush(); os.fsync(output.fileno())
            source_sha=digest.hexdigest(); signature=json.dumps([base_sha,mode,source_sha,scope])
            with self._lock(uid):
                with self._db() as c:
                    old=c.execute('SELECT signature,receipt FROM operations WHERE uid=? AND device=? AND op=?',(uid,device,op_id)).fetchone()
                if old:
                    if old['signature']!=signature:raise CloudConflict('operation_payload_mismatch')
                    return json.loads(old['receipt'])
                if base_sha is not None and not self._known(uid,base_sha):raise CloudConflict('base_version_not_found')
                if base_sha is not None:
                    with self._db() as c:
                        applied=c.execute('SELECT scope FROM device_applied_baselines WHERE uid=? AND device=? AND sha=?',(uid,device,base_sha)).fetchall()
                    if not applied:raise CloudConflict('base_not_applied_by_device')
                    if scope=='all' and not any(r['scope']=='all' for r in applied):raise CloudConflict('base_scope_not_applied_by_device')
                if mode=='delta' and base_sha is None:raise CloudConflict('delta_base_required')
                current=self.head(uid)
                self._ensure_inventory(uid,current)
                with body.open('rb') as source:
                    if mode=='delta':local=merge_delta_backup(self.snapshots,uid,base_sha,source,length,name,device)
                    else:local=self.snapshots.put(uid,source,length,name,device)
                merged=Path(tmp)/'merged.minisbak'
                local_path=self.snapshots.path(uid,local['sha256'])
                with self._db() as inventory:
                    def deleted(path,key):
                        r=inventory.execute('SELECT deleted FROM known_records WHERE uid=? AND path=? AND k=?',(uid,path,key)).fetchone()
                        return bool(r and r['deleted'])
                    profile_applied=bool(base_sha and inventory.execute('SELECT 1 FROM device_profile_baselines WHERE uid=? AND device=? AND sha=?',(uid,device,base_sha)).fetchone())
                    stats=merge_backups(self.path(uid,base_sha) if base_sha else None,local_path,self.path(uid,current['sha256']) if current else local_path,merged,scope=scope,legacy_deleted=deleted if base_sha is None else None,profile_base_applied=profile_applied)
                with merged.open('rb') as source:meta=self.snapshots.put(uid,source,merged.stat().st_size,'cloud.minisbak',device)
                receipt={'sha256':meta['sha256'],'source_sha':source_sha,'bytes':meta['bytes'],'version':current['version']+1 if current else 1,'conflicts':stats['conflicts'],'deleted':stats['deleted'],'base_sha':base_sha,'mode':mode,'scope':scope,'device':device,'op_id':op_id}
                encoded=json.dumps(receipt)
                with self._db() as c:
                    c.execute('INSERT INTO versions VALUES(?,?,?,?)',(uid,receipt['version'],receipt['sha256'],encoded))
                    c.execute('INSERT OR REPLACE INTO heads VALUES(?,?)',(uid,receipt['version']))
                    c.execute('INSERT INTO operations VALUES(?,?,?,?,?)',(uid,device,op_id,signature,encoded))
                    self._inventory(c,uid,receipt['sha256'])
                return receipt

    def _inventory(self,c,uid,sha):
        c.execute('CREATE TEMP TABLE IF NOT EXISTS inventory_current(path TEXT,k TEXT,PRIMARY KEY(path,k)) WITHOUT ROWID')
        c.execute('DELETE FROM inventory_current')
        c.executemany('INSERT OR IGNORE INTO inventory_current VALUES(?,?)',iter_record_keys(self.snapshots.path(uid,sha),validated=True))
        c.execute('INSERT OR IGNORE INTO known_records SELECT ?,path,k,0 FROM inventory_current',(uid,))
        c.execute('UPDATE known_records SET deleted=NOT EXISTS(SELECT 1 FROM inventory_current x WHERE x.path=known_records.path AND x.k=known_records.k) WHERE uid=?',(uid,))
        c.execute('INSERT OR REPLACE INTO inventory_heads VALUES(?,?)',(uid,sha))
    def _ensure_inventory(self,uid,current):
        if not current:return
        with self._db() as c:
            row=c.execute('SELECT sha FROM inventory_heads WHERE uid=?',(uid,)).fetchone()
            if row and row['sha']==current['sha256']:return
            # Upgrading an existing v3 store: preserve deleted IDs from ALL known heads.
            versions=c.execute('SELECT sha FROM versions WHERE uid=? ORDER BY version',(uid,)).fetchall()
            for version in versions:
                c.executemany('INSERT OR IGNORE INTO known_records VALUES(?,?,?,1)',((uid,p,k) for p,k in iter_record_keys(self.snapshots.path(uid,version['sha']),validated=True)))
            self._inventory(c,uid,current['sha256'])
    @staticmethod
    def _same_content(left,right):
        def manifest(path):
            with zipfile.ZipFile(path) as z:
                n=next(n for n in z.namelist() if n=='manifest.json' or n.endswith('/manifest.json'))
                m=json.loads(z.read(n))
                return {k:v for k,v in m.items() if k not in ('created_at','snapshot_at','backup_id','device_name')}
        return manifest(left)==manifest(right)
    def legacy_path(self,uid,sha):
        uid=self.snapshots._uid(uid)
        with self._db() as c:
            if not c.execute('SELECT 1 FROM legacy_projections WHERE uid=? AND sha=?',(uid,sha)).fetchone():raise FileNotFoundError('legacy_projection_not_found')
        return self.snapshots.path(uid,sha)
    def legacy_list(self,uid):
        uid=self.snapshots._uid(uid)
        with self._lock(uid):
            head=self.head(uid)
            if not head:return {'ok':True,'items':[],'total':0,'protocol':3}
            with self._db() as c:row=c.execute('SELECT sha FROM legacy_projections WHERE uid=? AND head_sha=?',(uid,head['sha256'])).fetchone()
            if row:sha=row['sha']
            else:
                with tempfile.TemporaryDirectory(prefix='.projection-',dir=self.root) as tmp:
                    output=Path(tmp)/'chats.minisbak';project_chats(self.path(uid,head['sha256']),output)
                    with output.open('rb') as f:meta=self.snapshots.put(uid,f,output.stat().st_size,'cloud-chats.minisbak','legacy-projection')
                sha=meta['sha256']
                with self._db() as c:c.execute('INSERT INTO legacy_projections VALUES(?,?,?)',(uid,head['sha256'],sha))
            with self.snapshots._db() as c:row=c.execute('SELECT * FROM snapshots WHERE user_id=? AND sha256=?',(uid,sha)).fetchone()
            if row is None:raise FileNotFoundError('legacy_projection_not_found')
            meta=self.snapshots._meta(row);meta['head_sha256']=head['sha256']
            return {'ok':True,'items':[meta],'total':1,'protocol':3,'legacy_deletions':'upgrade_required'}
    def legacy_commit(self,uid,device,stream,length,name,mode='full',base_sha=None):
        uid=self.snapshots._uid(uid);device=self._key(device,'device')
        if mode not in ('full','delta'):raise InvalidBackup('bad_mode')
        if mode=='delta' and (not isinstance(base_sha,str) or not re.fullmatch('[a-f0-9]{64}',base_sha)):raise InvalidBackup('delta_base_required')
        if type(length) is not int or not 0<length<=MAX_UPLOAD:raise InvalidBackup('size_out_of_range')
        if shutil.disk_usage(self.root).free<length+2*1024**3:raise InvalidBackup('storage_capacity_exhausted_originals_preserved')
        with tempfile.TemporaryDirectory(prefix='.legacy-',dir=self.root) as tmp:
            body=Path(tmp)/'body';digest=hashlib.sha256();remaining=length
            with body.open('wb') as out:
                while remaining:
                    chunk=stream.read(min(remaining,1024*1024))
                    if not chunk or len(chunk)>remaining:raise InvalidBackup('short_upload')
                    out.write(chunk);digest.update(chunk);remaining-=len(chunk)
                out.flush();os.fsync(out.fileno())
            source_sha=digest.hexdigest();signature=json.dumps([mode,base_sha,source_sha])
            internal='legacy-'+hashlib.sha256(device.encode()).hexdigest()[:40]
            operation='legacy-'+hashlib.sha256(signature.encode()).hexdigest()
            with self._lock(uid):
                with self._db() as c:
                    old=c.execute('SELECT receipt FROM operations WHERE uid=? AND device=? AND op=?',(uid,internal,operation)).fetchone()
                    previous=c.execute('SELECT source_sha FROM legacy_devices WHERE uid=? AND device=?',(uid,device)).fetchone()
                if old:return json.loads(old['receipt'])
                current=self.head(uid);self._ensure_inventory(uid,current)
                with body.open('rb') as source:
                    if mode=='delta':local=merge_delta_backup(self.snapshots,uid,base_sha,source,length,name,internal)
                    else:local=self.snapshots.put(uid,source,length,name,internal)
                local_path=self.snapshots.path(uid,local['sha256'])
                baseline=self.snapshots.path(uid,previous['source_sha']) if previous else None
                merged=Path(tmp)/'merged.minisbak'
                with self._db() as inventory:
                    def deleted(path,key):
                        r=inventory.execute('SELECT deleted FROM known_records WHERE uid=? AND path=? AND k=?',(uid,path,key)).fetchone()
                        return bool(r and r['deleted'])
                    stats=merge_backups(baseline,local_path,self.path(uid,current['sha256']) if current else local_path,merged,legacy=True,legacy_deleted=deleted)
                unchanged=current is not None and self._same_content(merged,self.path(uid,current['sha256']))
                if unchanged:meta=current
                else:
                    with merged.open('rb') as source:meta=self.snapshots.put(uid,source,merged.stat().st_size,'cloud.minisbak',internal)
                receipt={'sha256':meta['sha256'],'source_sha':source_sha,'legacy_source_sha':local['sha256'],'bytes':meta['bytes'],'version':current['version'] if unchanged else current['version']+1 if current else 1,'conflicts':stats['conflicts'],'deleted':0,'mode':mode,'scope':'chats','device':internal,'op_id':operation,'legacy':True,'legacy_deletions':'upgrade_required'}
                encoded=json.dumps(receipt)
                with self._db() as c:
                    if not unchanged:
                        c.execute('INSERT INTO versions VALUES(?,?,?,?)',(uid,receipt['version'],receipt['sha256'],encoded))
                        c.execute('INSERT OR REPLACE INTO heads VALUES(?,?)',(uid,receipt['version']))
                        self._inventory(c,uid,receipt['sha256'])
                    c.execute('INSERT INTO operations VALUES(?,?,?,?,?)',(uid,internal,operation,signature,encoded))
                    c.execute('INSERT OR REPLACE INTO legacy_devices VALUES(?,?,?)',(uid,device,local['sha256']))
                return receipt
