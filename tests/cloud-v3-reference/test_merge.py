import hashlib, json, sys, tempfile, unittest, zipfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'integrations' / 'cloud-v3'))
from minis_store import InvalidBackup, validate_package
from minis_merge import merge_backups, UnsupportedMerge

class MergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root = Path(self.tmp.name); self.n = 0
    def package(self, rows=(), extra=None):
        self.n += 1; p = self.root / str(self.n)
        entries = {'data/sessions.jsonl': b''.join((json.dumps({'v':1,'t':'session','d':r})+'\n').encode() for r in rows)}
        entries.update(extra or {})
        entries['manifest.json'] = json.dumps({'format':1,'categories':{'chats':True},'integrity':{n:hashlib.sha256(v).hexdigest() for n,v in entries.items()}}).encode()
        with zipfile.ZipFile(p,'w') as z:
            for n,v in entries.items(): z.writestr(n,v)
        return p
    def merge(self,b,l,c,**kw):
        p = self.root/'result'; result=merge_backups(b,l,c,p,**kw); validate_package(p)
        with zipfile.ZipFile(p) as z:
            rows=[json.loads(x)['d'] for x in z.read('data/sessions.jsonl').splitlines()]
            conflicts=json.loads(z.read('sync/conflicts.json')) if 'sync/conflicts.json' in z.namelist() else []
        return result,rows,conflicts
    def test_independent_additions_and_fields(self):
        b=self.package([{'id':'a','x':0,'y':0}]); l=self.package([{'id':'a','x':1,'y':0},{'id':'l'}]); c=self.package([{'id':'a','x':0,'y':2},{'id':'c'}])
        r,rows,_=self.merge(b,l,c); self.assertEqual({x['id'] for x in rows},{'a','l','c'}); self.assertIn({'id':'a','x':1,'y':2},rows); self.assertEqual(r['conflicts'],0)
    def test_concurrent_edit_recoverable(self):
        r,rows,conflicts=self.merge(self.package([{'id':'a','x':0}]),self.package([{'id':'a','x':1}]),self.package([{'id':'a','x':2}]))
        self.assertEqual(rows,[{'id':'a','x':2}]); self.assertEqual(r['conflicts'],1); self.assertEqual(conflicts[0]['local']['d']['x'],1)
    def test_delete_and_stale_offline_does_not_revive(self):
        b=self.package([{'id':'a'}]); r,rows,_=self.merge(b,b,self.package()); self.assertEqual(rows,[]); self.assertEqual(r['deleted'],1)
    def test_delete_modify(self):
        _,rows,c=self.merge(self.package([{'id':'a','x':0}]),self.package(),self.package([{'id':'a','x':2}]))
        self.assertEqual(rows,[{'id':'a','x':2}]); self.assertTrue(c[0]['local_deleted'])
    def test_first_sync_union(self):
        _,rows,_=self.merge(None,self.package([{'id':'a'}]),self.package([{'id':'b'}])); self.assertEqual(len(rows),2)
    def test_blobs_preserved(self):
        blob=b'hello'; sha=hashlib.sha256(blob).hexdigest(); name='blobs/'+sha[:2]+'/'+sha
        l=self.package(extra={name:blob}); c=self.package(); self.merge(None,l,c)
        with zipfile.ZipFile(self.root/'result') as z:self.assertEqual(z.read(name),blob)
    def test_unknown_dual_edit_fails(self):
        with self.assertRaises(UnsupportedMerge):self.merge(self.package(extra={'x':b'a'}),self.package(extra={'x':b'b'}),self.package(extra={'x':b'c'}))
    def test_duplicate_id_fails(self):
        p=self.package([{'id':'a'},{'id':'a'}])
        with self.assertRaises(InvalidBackup):self.merge(None,p,self.package())
    def test_index_requires_explicit_key(self):
        p=self.package(extra={'files.index.jsonl':b'{"path":"a"}\n'})
        with self.assertRaises(UnsupportedMerge):self.merge(None,p,self.package())
    def test_shards_are_one_collection(self):
        row=lambda v:(json.dumps({'v':1,'t':'session','d':v})+'\n').encode()
        b=self.package(extra={'data/sessions-001.jsonl':row({'id':'a','x':0})})
        l=self.package([{'id':'a','x':1}]); c=self.package(extra={'data/sessions-002.jsonl':row({'id':'a','x':0}),'data/sessions-003.jsonl':row({'id':'b'})})
        r,rows,_=self.merge(b,l,c); self.assertEqual(r['conflicts'],0); self.assertEqual(rows,[{'id':'a','x':1},{'id':'b'}])
    def test_index_configured_and_blob_integrity(self):
        blob=b'file'; sha=hashlib.sha256(blob).hexdigest(); name='blobs/'+sha[:2]+'/'+sha
        p=self.package(extra={'files.index.jsonl':(json.dumps({'path':'a','sha256':sha})+'\n').encode(),name:blob})
        self.merge(None,p,self.package(),index_key='path')
    def test_index_category_path_identity(self):
        p=self.package(extra={'files.index.jsonl':b'{"category":"a","path":"same"}\n{"category":"b","path":"same"}\n'})
        self.merge(None,p,self.package())
        with zipfile.ZipFile(self.root/'result') as z:self.assertEqual(len(z.read('files.index.jsonl').splitlines()),2)
    def test_bad_blob_reference_rejected(self):
        p=self.package(extra={'files.index.jsonl':(json.dumps({'path':'a','sha256':'0'*64})+'\n').encode()})
        with self.assertRaises(InvalidBackup):self.merge(None,p,self.package(),index_key='path')
    def test_duplicate_json_key_rejected(self):
        p=self.package(extra={'data/sessions.jsonl':b'{"v":1,"t":"session","d":{"id":"a","id":"b"}}\n'})
        with self.assertRaises(InvalidBackup):self.merge(None,p,self.package())
    def test_original_and_existing_destination_untouched(self):
        b=self.package([{'id':'a'}]); before=b.read_bytes()
        with self.assertRaises(InvalidBackup):merge_backups(None,b,b,b)
        self.assertEqual(b.read_bytes(),before)
        dest=self.root/'existing';dest.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):merge_backups(None,b,b,dest)
        self.assertEqual(dest.read_bytes(),b'keep')
    def rewrite_manifest(self,p,**changes):
        with zipfile.ZipFile(p) as z: entries={n:z.read(n) for n in z.namelist()}
        m=json.loads(entries['manifest.json']);m.update(changes);entries['manifest.json']=json.dumps(m).encode()
        with zipfile.ZipFile(p,'w') as z:
            for n,v in entries.items():z.writestr(n,v)
        return p
    def test_native_metadata_changes_and_categories_union(self):
        l=self.rewrite_manifest(self.package([{'id':'a'}]),created_at='old',snapshot_at='old',device_name='one',app={'version':'1'},categories={'sessions':1})
        c=self.rewrite_manifest(self.package([{'id':'b'}]),created_at='new',snapshot_at='new',device_name='two',app={'version':'2'},categories={'sessions':1,'messages':0})
        self.merge(None,l,c)
        with zipfile.ZipFile(self.root/'result') as z:
            m=json.loads(z.read('manifest.json'));self.assertEqual(m['categories'],{'sessions':2,'messages':0})
    def test_history_conflicts_survive_native_export_omission(self):
        history=[{'path':'old','local':{'secret':'recover'}}]
        b=self.package(extra={'sync/conflicts.json':json.dumps(history).encode()})
        _,_,conflicts=self.merge(b,self.package(),b);self.assertEqual(conflicts,history)
    def test_first_config_and_memory_conflicts_preserved(self):
        _,_,conflicts=self.merge(None,self.package(extra={'data/provider_config.json':b'{"a":1,"l":true}','data/memory/a.md':b'local'}),self.package(extra={'data/provider_config.json':b'{"a":2,"c":true}','data/memory/a.md':b'cloud'}))
        self.assertEqual(len(conflicts),2)
        with zipfile.ZipFile(self.root/'result') as z:
            self.assertEqual(json.loads(z.read('data/provider_config.json')),{'a':2,'l':True,'c':True})
            self.assertEqual(z.read('data/memory/a.md'),b'cloud')
    def test_blob_index_union(self):
        def entries(blob):
            sha=hashlib.sha256(blob).hexdigest();name='blobs/'+sha[:2]+'/'+sha
            return {name:blob,'blobs.index.jsonl':(json.dumps({'sha256':sha,'size':len(blob),'path':name})+'\n').encode()}
        self.merge(None,self.package(extra=entries(b'a')),self.package(extra=entries(b'b')))
        with zipfile.ZipFile(self.root/'result') as z:self.assertEqual(len(z.read('blobs.index.jsonl').splitlines()),2)
    def test_native_session_and_concurrent_parent_delete(self):
        session={'v':1,'t':'SessionV2','d':{'session':{'id':'s','folderId':None},'memoryEnabled':True}}
        message={'v':1,'t':'MessageV2','d':{'id':'m','sessionId':'s','parts':[]}}
        raw=lambda rows:b''.join((json.dumps(row)+'\n').encode() for row in rows)
        b=self.package(extra={'data/sessions.jsonl':raw([session])})
        l=self.package();c=self.package(extra={'data/sessions.jsonl':raw([session]),'data/messages.jsonl':raw([message])})
        r,rows,conflicts=self.merge(b,l,c)
        self.assertEqual(rows,[session['d']]);self.assertEqual(r['deleted'],0);self.assertEqual(conflicts[0]['reason'],'referenced_parent_preserved')
    def test_folder_references_preserved(self):
        folder={'v':1,'t':'FolderV1','d':{'id':'f'}}
        session={'v':1,'t':'SessionV2','d':{'session':{'id':'s','folderId':'f'}}}
        raw=lambda x:(json.dumps(x)+'\n').encode()
        b=self.package(extra={'data/folders.jsonl':raw(folder)})
        l=self.package();c=self.package(extra={'data/folders.jsonl':raw(folder),'data/sessions.jsonl':raw(session)})
        _,_,conflicts=self.merge(b,l,c)
        self.assertEqual(conflicts[0]['path'],'data/folders.jsonl')
    def test_root_secrets_first_sync_preserved(self):
        self.merge(None,self.package(extra={'secrets.json':b'{"local":"a"}'}),self.package(extra={'secrets.json':b'{"cloud":"b"}'}))
        with zipfile.ZipFile(self.root/'result') as z:self.assertEqual(json.loads(z.read('secrets.json')),{'local':'a','cloud':'b'})
    def test_blobs_never_eager_read(self):
        from unittest.mock import patch
        blob=b'x'*1024;sha=hashlib.sha256(blob).hexdigest();p=self.package(extra={'blobs/'+sha[:2]+'/'+sha:blob})
        original=zipfile.ZipFile.read
        def guarded(z,name,*args,**kwargs):
            filename=name.filename if isinstance(name,zipfile.ZipInfo) else name
            if filename.startswith('blobs/'):raise AssertionError('eager blob read')
            return original(z,name,*args,**kwargs)
        with patch.object(zipfile.ZipFile,'read',guarded):self.merge(None,p,p)
    def test_metadata_limit_fails_without_output(self):
        from unittest.mock import patch
        p=self.package()
        with patch('minis_merge.MAX_METADATA',1):
            with self.assertRaises(UnsupportedMerge):self.merge(None,p,p)
        self.assertFalse((self.root/'result').exists())
    def test_chat_scope_does_not_delete_unapplied_config_or_index(self):
        extra={'data/provider_config.json':b'{"cloud":true}','files.index.jsonl':b'{"category":"skills","path":"a"}\n'}
        b=self.package([{'id':'s'}],extra);l=self.package();c=self.package([{'id':'s'}],extra)
        _,rows,_=self.merge(b,l,c,scope='chats');self.assertEqual(rows,[])
        with zipfile.ZipFile(self.root/'result') as z:
            self.assertEqual(json.loads(z.read('data/provider_config.json')),{'cloud':True})
            self.assertEqual(len(z.read('files.index.jsonl').splitlines()),1)
    def test_chat_scope_config_conflict_recovers_both(self):
        b=self.package(extra={'data/provider_config.json':b'{"value":0}'})
        _,_,conflicts=self.merge(b,self.package(extra={'data/provider_config.json':b'{"value":1}'}),self.package(extra={'data/provider_config.json':b'{"value":2}'}),scope='chats')
        self.assertEqual(conflicts[0]['local'],{'value':1});self.assertEqual(conflicts[0]['cloud'],{'value':2})
    def test_compact_markers_normalize_and_preserve_parent(self):
        raw=lambda row:(json.dumps(row)+'\n').encode()
        s={'v':1,'t':'SessionV2','d':{'session':{'id':'s'}}}; marker={'v':1,'t':'CompactMarkerV1','d':{'id':'marker','sessionId':'s'}}
        b=self.package(extra={'data/sessions.jsonl':raw(s)})
        c=self.package(extra={'data/sessions.jsonl':raw(s),'data/compact_markers-001.jsonl':raw(marker)})
        _,rows,conflicts=self.merge(b,self.package(),c,scope='chats');self.assertEqual(rows,[s['d']]);self.assertEqual(conflicts[0]['reason'],'referenced_parent_preserved')
        with zipfile.ZipFile(self.root/'result') as z:self.assertIn('data/compact_markers.jsonl',z.namelist())
    def test_large_sharded_jsonl_disk_merge_has_bounded_rss(self):
        import subprocess
        paths=[];record_count=2048
        for side in range(3):
            package=self.root/('large-'+str(side)+'.minisbak');integrity={};total=0
            with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED) as z:
                for shard in range(2):
                    name='data/sessions-'+str(shard)+'.jsonl';digest=hashlib.sha256()
                    with z.open(name,'w') as dest:
                        for n in range(shard*1024,(shard+1)*1024):
                            record={'v':1,'t':'session','d':{'id':str(n),'payload':'x'*(36*1024),'x':int(side==1 and n==0),'y':int(side==2 and n==0)}}
                            raw=(json.dumps(record,separators=(',',':'))+'\n').encode();dest.write(raw);digest.update(raw);total+=len(raw)
                    integrity[name]=digest.hexdigest()
                z.writestr('manifest.json',json.dumps({'categories':{'sessions':record_count},'integrity':integrity}))
            self.assertGreater(total,64*1024*1024);paths.append(str(package))
        server=str(Path(__file__).resolve().parents[2]/'integrations'/'cloud-v3')
        code="""import json,resource,sys,zipfile
sys.path.insert(0,sys.argv[1])
from minis_merge import merge_backups
result=merge_backups(sys.argv[2],sys.argv[3],sys.argv[4],sys.argv[5])
with zipfile.ZipFile(sys.argv[5]) as z:
 with z.open('data/sessions.jsonl') as f:
  first=json.loads(next(f));count=1+sum(1 for _ in f)
assert first['d']['x']==1 and first['d']['y']==1
rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
if sys.platform!='darwin':rss*=1024
print(json.dumps({'rss_bytes':rss,'rows':count,'conflicts':result['conflicts']}))
"""
        result=subprocess.run([sys.executable,'-c',code,server,*paths,str(self.root/'large-output')],capture_output=True,text=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stderr)
        metrics=json.loads(result.stdout);self.assertEqual(metrics['rows'],record_count);self.assertEqual(metrics['conflicts'],0)
        self.assertLess(metrics['rss_bytes'],256*1024*1024,metrics)
        print('Disk-backed merge RSS bytes:',metrics['rss_bytes'])
    def test_invalid_zip(self):
        p=self.root/'bad';p.write_bytes(b'bad')
        with self.assertRaises(InvalidBackup):self.merge(None,p,self.package())
if __name__=='__main__':unittest.main()
