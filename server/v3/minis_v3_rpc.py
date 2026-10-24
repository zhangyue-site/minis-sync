"""Isolated SSH protocol; binary bodies are always length bounded."""
import base64
from contextlib import nullcontext
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import sqlite3
import zipfile
from minis_cloud import CloudStore, CloudConflict
from minis_store import InvalidBackup, MAX_UPLOAD, SnapshotStore

class ExactStream:
    def __init__(self,stream,length):self.stream,self.left=stream,length
    def read(self,size=-1):
        size=min(self.left,65536 if size<0 else size)
        if not size:return b''
        data=self.stream.read(size)
        if len(data)!=size:raise ValueError('short_upload')
        self.left-=len(data);return data

def validate(r):
    if r.get('action') not in ('legacy_put','legacy_delta','v3_commit','v3_ack','head','list','meta','manifest','download'):raise ValueError('unknown_action')
    if not re.fullmatch(r'[0-9]{1,32}',str(r.get('uid',''))):raise ValueError('bad_uid')
    for key in ('sha256','base'):
        if r.get(key) and not re.fullmatch(r'[a-f0-9]{64}',r[key]):raise ValueError('bad_sha')
    if r['action'] in ('meta','manifest','download','v3_ack') and not r.get('sha256'):raise ValueError('sha_required')
    if r['action'] in ('legacy_put','legacy_delta','v3_commit'):
        if type(r.get('length')) is not int or not 0<r['length']<=MAX_UPLOAD:raise ValueError('size_out_of_range')

def old_path(uid,sha):
    root=Path(os.environ.get('MINIS_LEGACY_ROOT','/opt/minis-sync'))
    # Existing legacy store must already exist. Never initialize a second chain.
    if not (root/'store').is_dir():raise FileNotFoundError('snapshot_not_found')
    sys.path.append(str(root))
    from minis_restore_106 import ensure
    class ReadOnlySnapshots(SnapshotStore):
        def __init__(self):
            self.root=root/'store';self.database=self.root/'snapshots.db'
        def _db(self):
            connection=sqlite3.connect(self.database.as_uri()+'?mode=ro',uri=True,timeout=30)
            connection.row_factory=sqlite3.Row
            return connection
    if not (root/'store'/'snapshots.db').is_file():raise FileNotFoundError('snapshot_not_found')
    return ensure(ReadOnlySnapshots(),uid,sha,root)

def execute(r,stream,store=None):
    validate(r);store=store or CloudStore(os.environ.get('MINIS_V3_ROOT','/opt/minis-v3/store'))
    uid=str(r['uid']);action=r['action']
    if action=='head':return {'ok':True,'head':store.head(uid),'protocol':3}
    if action=='list':return store.legacy_list(uid)
    if action=='v3_ack':return dict(ok=True,**store.ack(uid,r.get('device'),r['sha256'],scope=r.get('scope','all')))
    if action in ('legacy_put','legacy_delta','v3_commit'):
        if action=='legacy_delta':
            base=r.get('base')
            if not base:raise ValueError('delta_base_required')
            try:store.snapshots.path(uid,base)
            except FileNotFoundError:
                original=old_path(uid,base)
                # Ownership was checked by the read-only legacy store/restore path.
                # Import into CAS only: no version or head is added.
                with store._lock(uid):
                    with original.open('rb') as source:
                        imported=store.snapshots.put(uid,source,original.stat().st_size,'legacy-base.minisbak',r.get('device','legacy'))
                    if imported['sha256']!=base:raise ValueError('legacy_base_digest_mismatch')
        body=ExactStream(stream,r['length'])
        if action=='v3_commit':result=store.commit(uid,r.get('device'),r.get('base'),r.get('op'),body,r['length'],r.get('name','backup.minisbak'),r.get('mode','full'),scope=r.get('scope','all'))
        else:result=store.legacy_commit(uid,r.get('device'),body,r['length'],r.get('name','backup.minisbak'),mode='delta' if action=='legacy_delta' else 'full',base_sha=r.get('base'))
        if body.left:raise ValueError('short_upload')
        if action in ('legacy_put','legacy_delta'):
            result=dict(result,head_sha256=result['sha256'],sha256=result['legacy_source_sha'])
        return dict(ok=True,**result)
    sha=r['sha256']
    try:path=store.path(uid,sha)
    except FileNotFoundError:
        try:path=store.legacy_path(uid,sha)
        except FileNotFoundError:path=old_path(uid,sha)
    if action=='meta':return {'bytes':path.stat().st_size,'sha256':sha}
    if action=='manifest':
        if not r.get('legacy'):
            try:return dict(ok=True,**store.manifest(uid,sha))
            except FileNotFoundError:
                with zipfile.ZipFile(path) as z:
                    entries=[{'path':i.filename,'bytes':i.file_size,'compressed_bytes':i.compress_size,'crc32':i.CRC} for i in z.infolist() if not i.is_dir()]
                    member=next(n for n in z.namelist() if n=='manifest.json' or n.endswith('/manifest.json'))
                    return {'ok':True,'sha256':sha,'bytes':path.stat().st_size,'entries':entries,'manifest':json.loads(z.read(member))}
        with zipfile.ZipFile(path) as z:return {'ok':True,'base':sha,'entries':[[i.filename,i.file_size,i.CRC] for i in z.infolist() if not i.is_dir() and i.filename!='__delta__.json']}
    with path.open('rb') as source:
        for block in iter(lambda:source.read(65536),b''):sys.stdout.buffer.write(block)
    return None

def main():
    r={}
    try:
        if len(sys.argv)!=2 or len(sys.argv[1])>16384:raise ValueError('invalid_request')
        r=json.loads(base64.urlsafe_b64decode(sys.argv[1]));validate(r)
        root=Path(os.environ.get('MINIS_V3_ROOT','/opt/minis-v3/store'));root.mkdir(parents=True,exist_ok=True,mode=0o700)
        capacity=r['action'] in ('legacy_put','legacy_delta','v3_commit','list')
        with (root/'rpc-capacity.lock').open('a+b') if capacity else nullcontext() as lock:
            if capacity:
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:raise RuntimeError('busy')
            result=execute(r,sys.stdin.buffer)
        if result is not None:print(json.dumps({'status':200,'data':result}))
    except Exception as e:
        if r.get('action')=='download':raise
        status=409 if isinstance(e,CloudConflict) else 404 if isinstance(e,FileNotFoundError) else 400 if isinstance(e,(ValueError,InvalidBackup)) else 503
        if 'capacity' in str(e):status=503
        data={'ok':False,'error':str(e)[:160] if status!=503 else 'storage_temporarily_unavailable'}
        if status==503:data['retry_after']=5
        print(json.dumps({'status':status,'data':data}))
if __name__=='__main__':main()
