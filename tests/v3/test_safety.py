import concurrent.futures
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'server/v3'))
from minis_cloud import CloudStore
from minis_retention import quarantine, MIN_AGE, references, digest
from minis_repair_baselines import restore
from minis_transfer_budget import TransferBudget
from sync_notes_db import publish
import front


def package(rows):
    raw = b''.join((json.dumps({'v':1,'t':'session','d':r})+'\n').encode() for r in rows)
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('data/sessions.jsonl',raw)
        z.writestr('manifest.json',json.dumps({'categories':{'chats':True},'integrity':{'data/sessions.jsonl':hashlib.sha256(raw).hexdigest()}}))
    return out.getvalue()


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=CloudStore(self.root)
    def legacy(self,rows,device='old',uid='1'):
        b=package(rows)
        return self.store.legacy_commit(uid,device,io.BytesIO(b),len(b),'old.minisbak')
    def test_legacy_reference_is_preserved_and_second_sync_keeps_remote_additions(self):
        first=self.legacy([{'id':'a','x':1}])
        self.legacy([{'id':'b'}],device='other')
        self.assertEqual(quarantine(self.root,True)['quarantined'],0)
        result=self.legacy([{'id':'a','x':2}])
        with zipfile.ZipFile(self.store.path('1',result['sha256'])) as z:
            ids={json.loads(x)['d']['id'] for x in z.read('data/sessions.jsonl').splitlines()}
        self.assertEqual(ids,{'a','b'})
    def test_missing_baseline_repaired_without_changing_head_or_records(self):
        self.legacy([{'id':'a'}]);second=self.legacy([{'id':'a'},{'id':'b'}],device='other')
        with self.store._db() as c:
            sha=c.execute("SELECT source_sha FROM legacy_devices WHERE device='old'").fetchone()[0]
            old_records=[tuple(r) for r in c.execute('SELECT * FROM known_records')]
        target=self.root/'snapshots/1'/(sha+'.minisbak');backup=self.root/'source';backup.write_bytes(target.read_bytes())
        target.unlink()
        with self.store.snapshots._db() as c:c.execute('DELETE FROM snapshots WHERE user_id=? AND sha256=?',('1',sha))
        self.assertTrue(quarantine(self.root,True)['errors'])
        result=restore(self.root,'1',sha,backup)
        self.assertTrue(result['verified']);self.assertEqual(digest(target),sha)
        self.assertEqual(self.store.head('1'),second)
        with self.store._db() as c:self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM known_records')],old_records)
        self.legacy([{'id':'a','x':2}])
    def test_invalid_source_and_existing_data_not_overwritten(self):
        first=self.legacy([{'id':'a'}]);sha=first['legacy_source_sha']
        source=self.root/'bad';source.write_bytes(b'bad')
        original=self.store.snapshots.path('1',sha).read_bytes()
        with self.assertRaises(ValueError):restore(self.root,'1',sha,source)
        self.assertEqual(self.store.snapshots.path('1',sha).read_bytes(),original)
        with self.assertRaises(ValueError):restore(self.root,'2',sha,source)
    def orphan(self):
        b=package([{'id':'orphan'}]);meta=self.store.snapshots.put('1',io.BytesIO(b),len(b),'orphan.minisbak','test')
        p=self.store.snapshots.path('1',meta['sha256']);old=int(time.time())-MIN_AGE-100
        os.utime(p,(old,old))
        with self.store.snapshots._db() as c:c.execute('UPDATE snapshots SET created_at=?',(old,))
        return p,meta
    def test_quarantine_never_deletes_rows_or_bytes_and_recovery_is_exact(self):
        p,meta=self.orphan();original=p.read_bytes()
        self.assertEqual(quarantine(self.root)['candidates'],1);self.assertTrue(p.exists())
        self.assertEqual(quarantine(self.root,True)['quarantined'],1)
        q=self.root/'quarantine/1'/p.name
        self.assertEqual(q.read_bytes(),original)
        with self.store.snapshots._db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0],1)
        self.assertEqual(quarantine(self.root,True)['quarantined'],0)
    def test_quarantined_file_is_restored_on_demand_for_same_account_only(self):
        p,meta=self.orphan();original=p.read_bytes();quarantine(self.root,True)
        with self.assertRaises(FileNotFoundError):self.store.snapshots.path('2',meta['sha256'])
        self.assertEqual(self.store.snapshots.path('1',meta['sha256']).read_bytes(),original)
        self.assertEqual(quarantine(self.root,True)['quarantined'],0)
    def test_old_implicit_ack_rows_do_not_authorize_deletion(self):
        b=package([{'id':'a'}]);r=self.store.commit('1','a',None,'first',io.BytesIO(b),len(b),'test.minisbak')
        with self.store._db() as c:
            c.execute('INSERT INTO device_scoped_baselines VALUES(?,?,?,?,?)',('1','a',r['sha256'],'all',int(time.time())))
        restarted=CloudStore(self.root)
        empty=package([])
        from minis_cloud import CloudConflict
        with self.assertRaisesRegex(CloudConflict,'base_not_applied_by_device'):
            restarted.commit('1','a',r['sha256'],'bad',io.BytesIO(empty),len(empty),'test.minisbak')
        self.assertEqual(restarted.head('1'),r)
    def test_recent_snapshot_not_moved(self):
        p,meta=self.orphan();os.utime(p,None)
        self.assertEqual(quarantine(self.root,True)['quarantined'],0);self.assertTrue(p.exists())
    def test_cleanup_waits_for_rpc_lock_and_rechecks_new_reference(self):
        import fcntl
        p,meta=self.orphan()
        with (self.root/'rpc-capacity.lock').open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future=pool.submit(quarantine,self.root,True)
                time.sleep(.1);self.assertFalse(future.done())
                with self.store._db() as c:c.execute('INSERT INTO legacy_devices VALUES(?,?,?)',('1','new',meta['sha256']))
                fcntl.flock(lock,fcntl.LOCK_UN)
                self.assertEqual(future.result(timeout=3)['quarantined'],0)
        self.assertTrue(p.exists())
    def test_schema_error_is_fail_closed(self):
        p,_=self.orphan()
        with self.store._db() as c:c.execute('DROP TABLE legacy_devices')
        self.assertTrue(quarantine(self.root,True)['errors']);self.assertTrue(p.exists())
    def test_corrupt_receipt_is_fail_closed(self):
        p,_=self.orphan()
        with self.store._db() as c:c.execute('INSERT INTO operations VALUES(?,?,?,?,?)',('1','a','b','x','bad-json'))
        self.assertTrue(quarantine(self.root,True)['errors']);self.assertTrue(p.exists())


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.budget=TransferBudget(Path(self.tmp.name)/'budget.db',100)
    def test_budget_persisted_and_disconnect_not_refunded(self):
        self.assertTrue(self.budget.reserve(70));self.assertFalse(TransferBudget(self.budget.database,100).reserve(31))
        self.assertTrue(self.budget.reserve(30))
    def test_concurrent_requests_cannot_exceed_cap(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            r=list(pool.map(self.budget.reserve,[30]*8))
        self.assertEqual(sum(r),3)
    def test_old_reservations_expire(self):
        now=time.time();self.assertTrue(self.budget.reserve(100,now))
        self.assertTrue(self.budget.reserve(100,now+33*86400))
    def test_invalid_size(self):
        for v in [-1,True,'100']:
            with self.assertRaises(ValueError):self.budget.reserve(v)


class FrontTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'auth.db'
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE tokens(token TEXT,user_id TEXT,revoked INTEGER,expires_at INTEGER)')
            c.execute('INSERT INTO tokens VALUES(?,?,?,?)',('good','1',0,int(time.time())+3600))
        tmp=self.root/'replica';tmp.write_bytes(self.db.read_bytes());publish(tmp,self.db,time.time())
        self.patches=[patch.object(front,'DB',str(self.db)),patch.object(front,'STORE',str(self.root/'snapshots'))]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.server=front.ThreadingHTTPServer(('127.0.0.1',0),front.H)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close)
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def request(self,path,method='GET',body=None,token='good',headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        h={'Authorization':'Bearer '+token};h.update(headers or {})
        c.request(method,path,body,headers=h);r=c.getresponse()
        # X-Accel is handled by nginx in production. Do not consume its absent body here.
        data=b'' if r.getheader('X-Accel-Redirect') else r.read()
        result=r.status,dict(r.getheaders()),data;c.close();return result
    def snapshot(self):
        raw=package([{'id':'x'}]);sha=hashlib.sha256(raw).hexdigest()
        folder=self.root/'snapshots/1';folder.mkdir(parents=True,exist_ok=True);(folder/(sha+'.minisbak')).write_bytes(raw)
        return sha,len(raw)
    def test_accelerated_download_and_range_both_reserve_budget(self):
        sha,size=self.snapshot()
        with patch.object(front,'reserve_transfer',return_value=True) as reserve:
            status,headers,_=self.request('/api/minis/v3/snapshots/'+sha,headers={'Range':'bytes=10-'})
        self.assertEqual(status,200);reserve.assert_called_once_with(size)
        self.assertEqual(headers['X-Accel-Redirect'],'/__store/1/'+sha+'.minisbak')
        with patch.object(front,'reserve_transfer',return_value=False):
            status,h,_=self.request('/api/minis/v3/snapshots/'+sha)
        self.assertEqual(status,429);self.assertNotIn('X-Accel-Redirect',h)
    def test_accel_account_isolation_and_unknown_token(self):
        sha,_=self.snapshot()
        with patch.object(front,'reserve_transfer') as reserve:
            self.assertEqual(self.request('/api/minis/v3/snapshots/'+sha,token='wrong')[0],401)
        reserve.assert_not_called()
    def test_upload_denied_before_backend_and_connection_closed(self):
        with patch.object(front,'reserve_transfer',return_value=False),patch.object(front.v3,'call') as rpc:
            s,h,_=self.request('/api/minis/v3/commit?device=a&op=b','POST',package([]))
        self.assertEqual(s,429);self.assertEqual(h['Connection'],'close');rpc.assert_not_called()
    def test_fallback_download_uses_same_guard(self):
        with patch.object(front,'reserve_transfer',return_value=False),patch.object(front.v3,'call',return_value={'status':200,'data':{'bytes':10,'sha256':'a'*64}}):
            self.assertEqual(self.request('/api/minis/v3/snapshots/'+'a'*64)[0],429)
    def test_real_front_rpc_ack_merge_delete_and_retry(self):
        store=CloudStore(self.root/'live')
        from urllib.parse import urlencode
        def post(action,data=b'',**args):
            return self.request('/api/minis/v3/'+action+'?'+urlencode(args),'POST',data)
        with patch.object(front,'STORE',str(store.snapshots.root)),patch.object(front,'PY',sys.executable),patch.object(front,'RPC',str(Path(front.__file__).parent/'minis_v3_rpc.py')),patch.object(front,'reserve_transfer',return_value=True),patch.dict(os.environ,{'MINIS_V3_ROOT':str(store.root)}):
            first=package([{'id':'a'},{'id':'delete'}])
            status,_,raw=post('commit',first,device='phone119',op='first',scope='chats')
            self.assertEqual(status,200);one=json.loads(raw)['sha256']
            # Uploading does not prove that the merged cloud version was applied.
            self.assertEqual(post('commit',package([]),device='phone119',op='bad',base=one,scope='chats')[0],409)
            self.assertEqual(post('ack',device='phone119',sha=one,scope='chats')[0],200)
            # Another device's independent addition survives the first device's deletion.
            s,_,raw=post('commit',package([{'id':'b'}]),device='old111',op='other',scope='chats')
            self.assertEqual(s,200)
            s,_,raw=post('commit',package([{'id':'a','x':2}]),device='phone119',op='next',base=one,scope='chats')
            self.assertEqual(s,200);receipt=json.loads(raw)
            with zipfile.ZipFile(store.path('1',receipt['sha256'])) as z:
                self.assertEqual({json.loads(x)['d']['id'] for x in z.read('data/sessions.jsonl').splitlines()},{'a','b'})
            s,_,repeat=post('commit',package([{'id':'a','x':2}]),device='phone119',op='next',base=one,scope='chats')
            self.assertEqual(s,200);self.assertEqual(json.loads(repeat),receipt)
            self.assertEqual(self.request('/api/minis/v3/snapshots/'+receipt['sha256'])[0],200)
            self.assertEqual(quarantine(store.root,True)['quarantined'],0)
    def test_stale_replica_expired_or_revoked_token_fail_closed(self):
        self.assertEqual(front.resolve_uid('good'),'1')
        with sqlite3.connect(self.db) as c:c.execute('UPDATE minis_auth_replica SET synced_at=?',(int(time.time())-601,))
        with self.assertRaises(front.AuthReplicaUnavailable):front.resolve_uid('good')
        self.assertEqual(self.request('/api/minis/v3/head')[0],503)
        with sqlite3.connect(self.db) as c:
            c.execute('UPDATE minis_auth_replica SET synced_at=?',(int(time.time()),));c.execute('UPDATE tokens SET revoked=1')
        self.assertIsNone(front.resolve_uid('good'))
    def test_bad_replica_cannot_replace_working_db(self):
        before=self.db.read_bytes();bad=self.root/'bad';bad.write_bytes(b'bad')
        with self.assertRaises(sqlite3.DatabaseError):publish(bad,self.db,time.time())
        self.assertEqual(self.db.read_bytes(),before)
    def test_database_mtime_cannot_refresh_stale_auth(self):
        with sqlite3.connect(self.db) as c:c.execute('UPDATE minis_auth_replica SET synced_at=1')
        os.utime(self.db,None)
        with self.assertRaises(front.AuthReplicaUnavailable):front.resolve_uid('good')


if __name__=='__main__':unittest.main()
