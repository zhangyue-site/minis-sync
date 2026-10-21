import hashlib, io, json, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'integrations' / 'cloud-v3'))
from minis_cloud import CloudStore, CloudConflict
from minis_store import InvalidBackup

class CloudTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=CloudStore(self.tmp.name)
    def package(self, rows):
        entries={'data/sessions.jsonl':b''.join((json.dumps({'v':1,'t':'session','d':r})+'\n').encode() for r in rows)}
        entries['manifest.json']=json.dumps({'format':1,'categories':{'chats':True},'integrity':{k:hashlib.sha256(v).hexdigest() for k,v in entries.items()}}).encode()
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:
            for k,v in entries.items(): z.writestr(zipfile.ZipInfo(k,date_time=(2020,1,1,0,0,0)),v)
        return b.getvalue()
    def commit(self,rows,base=None,device='a',op='op',uid='1'):
        data=self.package(rows)
        return self.store.commit(uid,device,base,op,io.BytesIO(data),len(data),'test.minisbak')
    def rows(self,receipt):
        with zipfile.ZipFile(self.store.path('1',receipt['sha256'])) as z:
            return [json.loads(x)['d'] for x in z.read('data/sessions.jsonl').splitlines()]
    def test_stale_baseline_converges_and_deletes(self):
        b=self.commit([{'id':'x','a':0,'b':0},{'id':'delete'}])
        self.store.ack('1','a',b['sha256']);self.store.ack('1','b',b['sha256'])
        self.commit([{'id':'x','a':1,'b':0}],b['sha256'],op='next')
        r=self.commit([{'id':'x','a':0,'b':2},{'id':'delete'}],b['sha256'],device='b')
        self.assertEqual(self.rows(r),[{'id':'x','a':1,'b':2}]); self.assertEqual(r['version'],3)
    def test_new_device_union_and_conflict(self):
        b=self.commit([{'id':'x','v':0}]); self.store.ack('1','a',b['sha256']);self.store.ack('1','b',b['sha256']); self.commit([{'id':'x','v':1}],b['sha256'],op='two')
        r=self.commit([{'id':'x','v':2}],b['sha256'],device='b'); self.assertEqual(r['conflicts'],1)
        r=self.commit([{'id':'new'}],device='c'); self.assertEqual(len(self.rows(r)),2)
    def test_retry_and_different_payload(self):
        r=self.commit([{'id':'x'}]); self.assertEqual(r,self.commit([{'id':'x'}]))
        with self.assertRaises(CloudConflict):self.commit([{'id':'different'}])
        self.assertEqual(self.store.head('1')['version'],1)
    def test_account_isolation_and_missing_base(self):
        r=self.commit([{'id':'x'}])
        with self.assertRaises(FileNotFoundError):self.store.path('2',r['sha256'])
        with self.assertRaises(CloudConflict):self.commit([],r['sha256'],uid='2')
        with self.assertRaises(CloudConflict):self.store.ack('2','b',r['sha256'])
        with self.assertRaises(CloudConflict):self.commit([],'f'*64,op='missing')
        self.assertEqual(self.store.ack('1','a',r['sha256'])['sha256'],r['sha256'])
    def test_failed_merge_preserves_head(self):
        r=self.commit([{'id':'x'}])
        self.store.ack('1','a',r['sha256'])
        with patch('minis_cloud.merge_backups',side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):self.commit([],r['sha256'],op='fail')
        self.assertEqual(self.store.head('1'),r)
    def test_invalid_operation(self):
        with self.assertRaises(InvalidBackup):self.commit([],op='../x')
    def test_delta_and_manifest(self):
        b=self.commit([{'id':'x'}]); self.store.ack('1','a',b['sha256']); target=self.package([{'id':'y'}]); data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z,zipfile.ZipFile(io.BytesIO(target)) as source:
            z.writestr('__delta__.json',json.dumps({'v':2,'base':b['sha256'],'deleted':[]}))
            for name in source.namelist():z.writestr(name,source.read(name))
        raw=data.getvalue(); r=self.store.commit('1','a',b['sha256'],'delta',io.BytesIO(raw),len(raw),'delta.minisbak','delta')
        self.assertEqual(self.rows(r),[{'id':'y'}]); self.assertTrue(self.store.manifest('1',r['sha256'])['entries'])

    def test_parallel_devices_serialized(self):
        from concurrent.futures import ThreadPoolExecutor
        b=self.commit([{'id':'x','a':0,'b':0},{'id':'delete'}])
        self.store.ack('1','a',b['sha256']);self.store.ack('1','b',b['sha256'])
        with ThreadPoolExecutor(2) as pool:
            a=pool.submit(self.commit,[{'id':'x','a':1,'b':0}],b['sha256'],'a','parallel')
            c=pool.submit(self.commit,[{'id':'x','a':0,'b':2},{'id':'delete'}],b['sha256'],'b','parallel')
            self.assertEqual({a.result()['version'],c.result()['version']},{2,3})
        self.assertEqual(self.rows(self.store.head('1')),[{'id':'x','a':1,'b':2}])
    def test_transaction_failure_preserves_head_and_allows_retry(self):
        r=self.commit([{'id':'x'}])
        self.store.ack('1','a',r['sha256'])
        with self.store._db() as c:
            c.execute("CREATE TRIGGER crash BEFORE INSERT ON operations BEGIN SELECT RAISE(ABORT,'crash'); END")
        with self.assertRaises(Exception):self.commit([{'id':'y'}],r['sha256'],op='fail')
        self.assertEqual(self.store.head('1'),r)
        with self.store._db() as c:c.execute('DROP TRIGGER crash')
        self.assertEqual(self.commit([{'id':'y'}],r['sha256'],op='fail')['version'],2)

    def test_local_orphan_is_not_a_valid_baseline(self):
        r=self.commit([{'id':'x'}])
        raw=self.package([{'id':'orphan'}])
        orphan=self.store.snapshots.put('1',io.BytesIO(raw),len(raw),'orphan.minisbak','a')
        with self.assertRaises(CloudConflict):self.commit([],orphan['sha256'],op='invalid-base')
        with self.assertRaises(FileNotFoundError):self.store.path('1',orphan['sha256'])
        self.assertEqual(self.store.head('1'),r)
    def test_identical_body_different_base_rejected(self):
        r=self.commit([{'id':'x'}])
        with self.assertRaises(CloudConflict):self.commit([{'id':'x'}],r['sha256'])

    def test_unapplied_other_device_base_cannot_delete(self):
        r=self.commit([{'id':'a'},{'id':'b'}])
        with self.assertRaisesRegex(CloudConflict,'base_not_applied_by_device'):
            self.commit([],r['sha256'],device='b')
        self.assertEqual(self.store.head('1'),r)

    def test_first_package_rejects_bad_json_and_encryption(self):
        for raw,extra in ((b'{bad json}\n',{}),(b'',{'encryption':{'type':'unknown'}})):
            entries={'data/sessions.jsonl':raw}
            entries['manifest.json']=json.dumps(dict(categories={'chats':True},integrity={'data/sessions.jsonl':hashlib.sha256(raw).hexdigest()},**extra)).encode()
            b=io.BytesIO()
            with zipfile.ZipFile(b,'w') as z:
                for key,value in entries.items():z.writestr(key,value)
            data=b.getvalue()
            with self.assertRaises(InvalidBackup):
                self.store.commit('1','a',None,'first',io.BytesIO(data),len(data),'test.minisbak')
            self.assertIsNone(self.store.head('1'))

    def test_ack_history_and_old_retry_preserve_new_head(self):
        first=self.commit([{'id':'x'}]);self.store.ack('1','a',first['sha256'])
        second=self.commit([{'id':'x'},{'id':'y'}],first['sha256'],op='second')
        self.store.ack('1','a',second['sha256'])
        third=self.commit([{'id':'x'},{'id':'z'}],first['sha256'],op='third')
        self.assertEqual({r['id'] for r in self.rows(third)},{'x','y','z'})
        self.assertEqual(self.commit([{'id':'x'}]),first)
        self.assertEqual(self.store.head('1'),third)
    def test_own_commit_is_not_an_implicit_ack(self):
        first=self.commit([{'id':'x'}])
        with self.assertRaisesRegex(CloudConflict,'base_not_applied_by_device'):
            self.commit([],first['sha256'],op='unapplied')

    def test_chat_ack_cannot_authorize_full_commit(self):
        first=self.commit([{'id':'x'}])
        ack=self.store.ack('1','b',first['sha256'],scope='chats')
        self.assertEqual(ack['applied_scope'],'chats')
        raw=self.package([{'id':'x'},{'id':'new'}])
        with self.assertRaisesRegex(CloudConflict,'base_scope_not_applied_by_device'):
            self.store.commit('1','b',first['sha256'],'scoped',io.BytesIO(raw),len(raw),'test.minisbak',scope='all')
        receipt=self.store.commit('1','b',first['sha256'],'scoped',io.BytesIO(raw),len(raw),'test.minisbak',scope='chats')
        self.assertEqual(receipt['scope'],'chats')
        with self.assertRaisesRegex(CloudConflict,'operation_payload_mismatch'):
            self.store.commit('1','b',first['sha256'],'scoped',io.BytesIO(raw),len(raw),'test.minisbak',scope='all')
    def test_chat_version_cannot_be_acknowledged_as_all(self):
        raw=self.package([{'id':'x'}])
        receipt=self.store.commit('1','a',None,'chat-first',io.BytesIO(raw),len(raw),'test.minisbak',scope='chats')
        with self.assertRaisesRegex(CloudConflict,'version_scope_mismatch'):
            self.store.ack('1','a',receipt['sha256'])
        self.assertEqual(self.store.ack('1','a',receipt['sha256'],scope='chats')['applied_scope'],'chats')

    def test_chat_baseline_advances_without_deleting_unapplied_categories(self):
        entries={}
        with zipfile.ZipFile(io.BytesIO(self.package([{'id':'x'}]))) as z:
            for n in z.namelist():
                if n!='manifest.json':entries[n]=z.read(n)
        entries['files.index.jsonl']=b'{"category":"settings","path":"profile.json"}\n{"category":"chats","path":"removed-attachment"}\n'
        entries['settings.json']=b'{"keep":"cloud configuration"}'
        entries['manifest.json']=json.dumps({'format':1,'categories':{'chats':True},'integrity':{k:hashlib.sha256(v).hexdigest() for k,v in entries.items()}}).encode()
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            for n,v in entries.items():z.writestr(n,v)
        raw=data.getvalue();receipt=self.store.commit('1','a',None,'all-first',io.BytesIO(raw),len(raw),'test.minisbak')
        for step in range(2):
            self.store.ack('1','b',receipt['sha256'],scope='chats')
            local=self.package([{'id':'x'},{'id':'new'+str(step)}])
            receipt=self.store.commit('1','b',receipt['sha256'],'chat-'+str(step),io.BytesIO(local),len(local),'test.minisbak',scope='chats')
            with zipfile.ZipFile(self.store.path('1',receipt['sha256'])) as z:
                self.assertEqual(z.read('settings.json'),b'{"keep":"cloud configuration"}')
                rows=[json.loads(row) for row in z.read('files.index.jsonl').splitlines()]
                self.assertEqual(rows,[{'category':'settings','path':'profile.json'}])
