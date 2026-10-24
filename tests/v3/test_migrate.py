import hashlib,io,json,sqlite3,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from collections import namedtuple
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'server'/'v3'))
from minis_store import SnapshotStore
from minis_cloud import CloudStore
from minis_migrate import migrate

class MigrationTests(unittest.TestCase):
    def setUp(self):
        capacity=patch('shutil.disk_usage',return_value=namedtuple('usage','total used free')(100*1024**3,0,100*1024**3));capacity.start();self.addCleanup(capacity.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.legacy=SnapshotStore(self.root/'legacy');self.stage=self.root/'stage'
    def package(self,ids):
        raw=b''.join((json.dumps({'v':1,'t':'session','d':{'id':x}})+'\n').encode() for x in ids)
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            z.writestr('data/sessions.jsonl',raw);z.writestr('manifest.json',json.dumps({'categories':{'chats':True},'integrity':{'data/sessions.jsonl':hashlib.sha256(raw).hexdigest()}}))
        return data.getvalue()
    def put(self,uid,ids,when):
        raw=self.package(ids);meta=self.legacy.put(uid,io.BytesIO(raw),len(raw),'private-name.minisbak','device')
        with self.legacy._db() as c:c.execute('UPDATE snapshots SET created_at=? WHERE user_id=? AND sha256=?',(when,uid,meta['sha256']))
        return meta
    def tree(self):
        return {str(p.relative_to(self.legacy.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.legacy.root.rglob('*') if p.is_file()}
    def test_latest_only_readonly_and_idempotent(self):
        self.put('1',['deleted','keep'],1);latest=self.put('1',['keep'],2);self.put('2',['second'],1)
        before=self.tree();report=migrate(self.legacy.root,self.stage)
        self.assertEqual(report['status'],'ready');self.assertEqual(before,self.tree())
        cloud=CloudStore(self.stage);head=cloud.head('1');self.assertEqual(head['version'],1)
        with zipfile.ZipFile(cloud.path('1',head['sha256'])) as z:self.assertNotIn(b'deleted',z.read('data/sessions.jsonl'))
        again=migrate(self.legacy.root,self.stage);self.assertEqual(report,again);self.assertEqual(cloud.head('1'),head)
        self.assertNotIn('private-name',json.dumps(report));self.assertEqual(report['accounts'][0]['source_sha256'],latest['sha256'])
    def test_missing_latest_blocks_without_fallback(self):
        self.put('1',['old'],1);new=self.put('1',['new'],2);self.legacy.path('1',new['sha256']).unlink()
        before=self.tree();report=migrate(self.legacy.root,self.stage)
        self.assertEqual(report['status'],'blocked');self.assertEqual(report['accounts'][0]['error'],'latest_package_missing_or_cold');self.assertFalse((self.stage/'heads.sqlite3').exists());self.assertEqual(before,self.tree())
    def test_corrupt_sha_blocks(self):
        meta=self.put('1',['x'],1);self.legacy.path('1',meta['sha256']).write_bytes(self.package(['corrupt']))
        before=self.tree();report=migrate(self.legacy.root,self.stage)
        self.assertEqual(report['status'],'blocked');self.assertEqual(before,self.tree())
    def test_refuses_existing_unrelated_and_overlapping_root(self):
        self.stage.mkdir();(self.stage/'do-not-touch').write_text('existing')
        with self.assertRaises(ValueError):migrate(self.legacy.root,self.stage)
        with self.assertRaises(ValueError):migrate(self.legacy.root,self.legacy.root/'stage')
    def test_new_latest_blocks_reusing_prepared_stage(self):
        self.put('1',['first'],1);self.assertEqual(migrate(self.legacy.root,self.stage)['status'],'ready')
        cloud=CloudStore(self.stage);old=cloud.head('1');self.put('1',['second'],2)
        report=migrate(self.legacy.root,self.stage);self.assertEqual(report['status'],'blocked');self.assertEqual(cloud.head('1'),old)

    def test_invalid_integrity_blocks_all_accounts_before_head_creation(self):
        self.put('1',['valid'],1)
        raw=self.package(['bad']);entries={}
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for name in z.namelist():entries[name]=z.read(name)
        manifest=json.loads(entries['manifest.json']);manifest['integrity']['data/sessions.jsonl']='f'*64;entries['manifest.json']=json.dumps(manifest).encode()
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            for name,value in entries.items():z.writestr(name,value)
        raw=data.getvalue();sha=hashlib.sha256(raw).hexdigest();folder=self.legacy.root/'2';folder.mkdir();(folder/(sha+'.minisbak')).write_bytes(raw)
        with self.legacy._db() as c:c.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?,?)',('2',sha,'private',len(raw),'private',1,'{}'))
        before=self.tree();report=migrate(self.legacy.root,self.stage)
        self.assertEqual(report['status'],'blocked');self.assertFalse((self.stage/'heads.sqlite3').exists());self.assertEqual(before,self.tree())
        self.assertEqual(report['accounts'][1]['error'],'latest_package_invalid_or_unsupported')
    def test_live_wal_requires_explicit_offline_checkpoint(self):
        self.put('1',['valid'],1)
        wal=Path(str(self.legacy.database)+'-wal');wal.write_bytes(b'cold-or-live-state')
        before=self.tree();report=migrate(self.legacy.root,self.stage)
        self.assertEqual(report['status'],'blocked');self.assertEqual(report['error'],'legacy_database_requires_offline_checkpoint');self.assertEqual(before,self.tree())
