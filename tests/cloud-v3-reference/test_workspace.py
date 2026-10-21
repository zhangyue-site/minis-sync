import hashlib, io, json, sys, tempfile, unittest, zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'integrations'/'cloud-v3'))
from minis_cloud import CloudStore, CloudConflict

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=CloudStore(self.temp.name)
    def package(self, files, config=True):
        entries={'data/sessions.jsonl':b'','data/messages.jsonl':b'','data/folders.jsonl':b'','data/compact_markers.jsonl':b''}
        rows=[];blobs=[]
        for path, data in files.items():
            sha=hashlib.sha256(data).hexdigest();name='blobs/'+sha[:2]+'/'+sha
            entries[name]=data;rows.append({'path':path,'category':'shared_files','sha256':sha,'size':len(data)})
            blobs.append({'path':name,'sha256':sha,'size':len(data)})
        entries['files.index.jsonl']=b''.join((json.dumps(r)+'\n').encode() for r in rows)
        entries['blobs.index.jsonl']=b''.join((json.dumps(r)+'\n').encode() for r in {b['sha256']:b for b in blobs}.values())
        categories={'chats':{'entries':0},'shared_files':{'entries':len(rows)}}
        if config:entries['data/providers.json']=b'{"local":"keep"}';categories['providers']={'entries':1}
        entries['manifest.json']=json.dumps({'format':'minisbak/1','categories':categories,'integrity':{n:hashlib.sha256(v).hexdigest() for n,v in entries.items()}}).encode()
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
            for n,v in entries.items():z.writestr(n,v)
        return stream.getvalue()
    def commit(self,files,base=None,device='a',op='one',scope='workspace',config=True):
        raw=self.package(files,config)
        return self.store.commit('1',device,base,op,io.BytesIO(raw),len(raw),'workspace.minisbak',scope=scope)
    def files(self,result):
        with zipfile.ZipFile(self.store.path('1',result['sha256'])) as z:
            return {r['path']:r for r in map(json.loads,z.read('files.index.jsonl').splitlines())}
    def test_shared_file_deletion_and_stale_offline_edit(self):
        first=self.commit({'shared/a.py':b'a','shared/delete.txt':b'd'})
        for d in ('a','b'):self.store.ack('1',d,first['sha256'],scope='workspace')
        self.commit({'shared/a.py':b'a2'},first['sha256'],op='delete')
        merged=self.commit({'shared/a.py':b'a','shared/delete.txt':b'd','shared/b.py':b'b'},first['sha256'],device='b')
        self.assertEqual(set(self.files(merged)),{'shared/a.py','shared/b.py'})
    def test_workspace_does_not_delete_unapplied_configuration(self):
        first=self.commit({'shared/a.py':b'a'})
        self.store.ack('1','a',first['sha256'],scope='workspace')
        merged=self.commit({},first['sha256'],op='next',config=False)
        with zipfile.ZipFile(self.store.path('1',merged['sha256'])) as z:self.assertEqual(json.loads(z.read('data/providers.json')),{'local':'keep'})
    def test_chat_only_device_cannot_infer_shared_deletion(self):
        first=self.commit({'shared/a.py':b'a'})
        self.store.ack('1','b',first['sha256'],scope='chats')
        with self.assertRaises(CloudConflict):self.commit({},first['sha256'],device='b')
        merged=self.commit({},first['sha256'],device='b',scope='chats')
        self.assertIn('shared/a.py',self.files(merged))
    def test_concurrent_project_file_versions_are_recoverable(self):
        first=self.commit({'shared/a.py':b'base'})
        for d in ('a','b'):self.store.ack('1',d,first['sha256'],scope='workspace')
        self.commit({'shared/a.py':b'android'},first['sha256'],op='android')
        merged=self.commit({'shared/a.py':b'iphone'},first['sha256'],device='b')
        self.assertGreater(merged['conflicts'],0)
        with zipfile.ZipFile(self.store.path('1',merged['sha256'])) as z:
            payloads=[z.read(n) for n in z.namelist() if n.startswith('blobs/')]
            self.assertIn(b'android',payloads);self.assertIn(b'iphone',payloads)
            self.assertIn('sync/conflicts.json',z.namelist())
    def test_partial_workspace_is_rejected_without_advancing_head(self):
        import io
        first=self.commit({'shared/a.py':b'a'})
        self.store.ack('1','a',first['sha256'],scope='workspace')
        raw=self.package({})
        source=io.BytesIO(raw);target=io.BytesIO()
        with zipfile.ZipFile(source) as z,zipfile.ZipFile(target,'w') as out:
            for name in z.namelist():
                data=z.read(name)
                if name=='manifest.json':
                    manifest=json.loads(data);del manifest['categories']['shared_files'];data=json.dumps(manifest).encode()
                out.writestr(name,data)
        from minis_store import InvalidBackup
        payload=target.getvalue()
        with self.assertRaises(InvalidBackup):self.store.commit('1','a',first['sha256'],'partial',io.BytesIO(payload),len(payload),'partial.minisbak',scope='workspace')
        self.assertEqual(self.store.head('1'),first)
