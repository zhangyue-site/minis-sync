import hashlib,io,json,sys,tempfile,unittest,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'server/v3'))
from minis_cloud import CloudStore,CloudConflict

class ProfileAckTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=CloudStore(self.tmp.name)
    def package(self,label='old',key='old',include=True):
        entries={'data/sessions.jsonl':b'{"v":1,"t":"session","d":{"id":"chat"}}\n',
          'data/provider_config.json':json.dumps({'instances':[{'id':'p','label':label}] if include else []}).encode(),
          'secrets.json':json.dumps({'providers':[{'instanceId':'p','apiKey':key}] if include else []}).encode()}
        entries['manifest.json']=json.dumps({'categories':{'chats':True,'providers':{}},'integrity':{k:hashlib.sha256(v).hexdigest() for k,v in entries.items()}}).encode()
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            for n,v in entries.items():z.writestr(zipfile.ZipInfo(n,(2026,1,1,0,0,0)),v)
        return out.getvalue()
    def commit(self,raw,base=None,op='one',device='a'):
        return self.store.commit('1',device,base,op,io.BytesIO(raw),len(raw),'a.minisbak',scope='chats')
    def profile(self,receipt):
        with zipfile.ZipFile(self.store.path('1',receipt['sha256'])) as z:return json.loads(z.read('data/provider_config.json')),json.loads(z.read('secrets.json'))
    def test_profile_proof_allows_later_key_update_without_deletion(self):
        first=self.commit(self.package());self.store.ack('1','a',first['sha256'],scope='chats');self.store.ack('1','a',first['sha256'],scope='profile')
        second=self.commit(self.package('edited','new'),first['sha256'],'two')
        p,s=self.profile(second);self.assertEqual(p['instances'][0]['label'],'edited');self.assertEqual(s['providers'][0]['apiKey'],'new')
        self.store.ack('1','a',second['sha256'],scope='chats');self.store.ack('1','a',second['sha256'],scope='profile')
        third=self.commit(self.package(include=False),second['sha256'],'three');p,s=self.profile(third)
        self.assertEqual(p['instances'][0]['id'],'p');self.assertEqual(s['providers'][0]['apiKey'],'new')
    def test_unapplied_profile_cannot_overwrite_cloud_key(self):
        first=self.commit(self.package());self.store.ack('1','b',first['sha256'],scope='chats')
        second=self.commit(self.package('local','local-key'),first['sha256'],'two',device='b')
        p,s=self.profile(second);self.assertEqual(p['instances'][0]['label'],'old');self.assertEqual(s['providers'][0]['apiKey'],'old');self.assertGreater(second['conflicts'],0)
    def test_profile_ack_never_grants_chat_delete_authority_or_changes_chat_ack(self):
        first=self.commit(self.package());self.store.ack('1','a',first['sha256'],scope='profile')
        with self.assertRaisesRegex(CloudConflict,'base_not_applied_by_device'):self.commit(self.package(),first['sha256'],'bad')
        with self.store._db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM devices').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM device_applied_baselines').fetchone()[0],0)
        with self.assertRaises(CloudConflict):self.store.ack('2','a',first['sha256'],scope='profile')
    def test_conflicting_key_edits_keep_both_recoverable(self):
        first=self.commit(self.package())
        for device in ['a','b']:
            self.store.ack('1',device,first['sha256'],scope='chats');self.store.ack('1',device,first['sha256'],scope='profile')
        second=self.commit(self.package('one','one-key'),first['sha256'],'two')
        third=self.commit(self.package('two','two-key'),first['sha256'],'three',device='b')
        self.assertEqual(self.profile(third)[1]['providers'][0]['apiKey'],'one-key')
        with zipfile.ZipFile(self.store.path('1',third['sha256'])) as z:self.assertIn('two-key',z.read('sync/conflicts.json').decode())

if __name__=='__main__':unittest.main()
