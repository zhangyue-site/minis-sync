import hashlib,io,json,os,sys,tempfile,unittest,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'server/v3'))
from minis_download_delta import create,apply,digest,inspect,prepare
from minis_store import InvalidBackup
from minis_cloud import CloudStore
from minis_v3_rpc import execute

class DownloadDeltaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.padding=os.urandom(3*1024*1024)
    def package(self,path,rows,reverse=False):
        raw=b''.join((json.dumps({'v':1,'t':'session','d':{'id':x}})+'\n').encode() for x in rows)
        entries={'data/sessions.jsonl':raw,'data/unchanged.bin':self.padding}
        manifest=json.dumps({'categories':{'chats':True},'integrity':{k:hashlib.sha256(v).hexdigest() for k,v in entries.items()}}).encode()
        entries['manifest.json']=manifest
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
            for k in reversed(list(entries)) if reverse else entries:z.writestr(k,entries[k])
        return digest(path)
    def fixture(self):
        base=self.root/'base';target=self.root/'target'
        b=self.package(base,['a']);t=self.package(target,['a','b'],True)
        delta=self.root/'delta';meta=create(base,target,b,t,delta)
        return base,target,b,t,delta,meta
    def test_exact_reconstruction_with_reordered_unchanged_attachment(self):
        base,target,b,t,delta,m=self.fixture();out=self.root/'out'
        apply(base,delta,out,b,t,target.stat().st_size)
        self.assertEqual(out.read_bytes(),target.read_bytes())
        self.assertLess(m['bytes'],target.stat().st_size//20)
        self.assertGreater(m['reused_bytes'],len(self.padding)-65536)
    def test_bad_base_target_and_truncated_payload_never_publish(self):
        base,target,b,t,delta,m=self.fixture();out=self.root/'out'
        with self.assertRaises(InvalidBackup):apply(base,delta,out,'0'*64,t,target.stat().st_size)
        with self.assertRaises(InvalidBackup):apply(base,delta,out,b,'0'*64,target.stat().st_size)
        delta.write_bytes(delta.read_bytes()[:-1])
        with self.assertRaises(InvalidBackup):apply(base,delta,out,b,t,target.stat().st_size)
        self.assertFalse(out.exists());self.assertEqual(digest(base),b)
    def test_existing_destination_is_never_replaced(self):
        base,target,b,t,delta,m=self.fixture();out=self.root/'out';out.write_bytes(b'existing')
        with self.assertRaises(FileExistsError):apply(base,delta,out,b,t,target.stat().st_size)
        self.assertEqual(out.read_bytes(),b'existing')
    def test_ownership_checked_even_with_cached_delta(self):
        base,target,b,t,delta,m=self.fixture();store=CloudStore(self.root/'store')
        for p,sha,v in [(base,b,1),(target,t,2)]:
            with p.open('rb') as f:store.snapshots.put('1',f,p.stat().st_size,'a.minisbak','a')
            with store._db() as c:c.execute('INSERT INTO versions VALUES(?,?,?,?)',('1',v,sha,json.dumps({'sha256':sha,'scope':'all','version':v})))
        path,meta=prepare(store,'1',b,t)
        self.assertLess(meta['bytes'],target.stat().st_size//20)
        with self.assertRaises(FileNotFoundError):prepare(store,'2',b,t)
        with self.assertRaises(FileNotFoundError):execute({'action':'delta_meta','uid':'2','base':b,'sha256':t},io.BytesIO(),store)
        self.assertEqual(execute({'action':'delta_meta','uid':'1','base':b,'sha256':t},io.BytesIO(),store)['sha256'],meta['sha256'])
    def test_cached_metadata_and_same_version_are_valid(self):
        base,target,b,t,delta,m=self.fixture()
        self.assertEqual(inspect(delta),m)
        identical=self.root/'identical';meta=create(base,base,b,b,identical)
        self.assertLess(meta['bytes'],1024)
        out=self.root/'out';apply(base,identical,out,b,b,base.stat().st_size)
        self.assertEqual(out.read_bytes(),base.read_bytes())
    def test_tampered_instruction_fails_without_touching_base(self):
        import struct
        from minis_download_delta import MAGIC
        base,target,b,t,delta,m=self.fixture();raw=delta.read_bytes();n=struct.unpack('>I',raw[8:12])[0];header=json.loads(raw[12:12+n])
        header['steps'][0]=['b',base.stat().st_size+1,1]
        h=json.dumps(header).encode();delta.write_bytes(MAGIC+struct.pack('>I',len(h))+h+raw[12+n:])
        with self.assertRaises(InvalidBackup):apply(base,delta,self.root/'out',b,t,target.stat().st_size)
        self.assertEqual(digest(base),b);self.assertFalse((self.root/'out').exists())

if __name__=='__main__':unittest.main()
