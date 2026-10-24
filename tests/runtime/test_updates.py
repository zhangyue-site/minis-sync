import base64, hashlib, importlib.util, io, json, subprocess, tarfile, tempfile, time, unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('runtime_update',Path(__file__).resolve().parents[2]/'assets/clients/update.py');u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)
class RuntimeTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.keys=tempfile.TemporaryDirectory();cls.key=Path(cls.keys.name)/'key.pem'
  subprocess.run(['openssl','genrsa','-out',str(cls.key),'2048'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  n=subprocess.check_output(['openssl','rsa','-in',str(cls.key),'-noout','-modulus'],stderr=subprocess.DEVNULL).decode().strip().split('=')[1];cls.public={'n':n,'e':65537}
 @classmethod
 def tearDownClass(cls):cls.keys.cleanup()
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.prefix=self.root/'prefix';self.prefix.mkdir();(self.prefix/'host-version').write_text('120');self.home=self.root/'home';(self.home/'.zcode').mkdir(parents=True);(self.home/'.zcode/user.json').write_text('private fixture');(self.prefix/'runtime-v1').mkdir()
 def manifest(self,**changes):
  m={'format':1,'architecture':'aarch64','adapter':1,'data_schema':1,'sequence':1,'min_host':120,'issued':int(time.time()),'expires':int(time.time())+86400,'bytes':100,'unpacked_bytes':4096,'sha256':'a'*64,'url':'https://d.zhangyue.site/apps/minis-sync/runtime/one.tar.gz','versions':{'opencode':'fixture','zcode':'fixture'}};m.update(changes);return m
 def sign(self,m):
  raw=u.encoded(m);sig=subprocess.check_output(['openssl','dgst','-sha256','-sign',str(self.key)],input=raw)
  return u.encoded({'payload':base64.b64encode(raw).decode(),'signature':base64.b64encode(sig).decode()})
 def package(self,extra=None):
  p=self.root/('package-'+str(len(list(self.root.glob('package-*'))))+'.tar.gz')
  with tarfile.open(p,'w:gz') as t:
   for name in ['opencode','zcode.cjs','zcode-builtin.json','usr/bin/node','usr/bin/python3','lib/ld-musl-aarch64.so.1']:
    b=b'synthetic runtime';i=tarfile.TarInfo(name);i.size=len(b);i.mode=0o755;t.addfile(i,io.BytesIO(b))
   if extra:
    i=tarfile.TarInfo(extra[0]);i.type=tarfile.SYMTYPE;i.linkname=extra[1];t.addfile(i)
  return p
 def install(self,p,**kwargs):
  m=self.manifest(sha256=u.digest(p),bytes=p.stat().st_size,**kwargs);u.install(self.prefix,m,p,self.home,check=lambda _:None);return m
 def test_signed_manifest(self):self.assertEqual(u.verify(self.sign(self.manifest()),self.public)['sequence'],1)
 def test_tampered_signature(self):
  e=json.loads(self.sign(self.manifest()));e['payload']=base64.b64encode(b'{}').decode()
  with self.assertRaises(u.UpdateError):u.verify(u.encoded(e),self.public)
 def test_expired_incompatible_and_origin_rejected(self):
  for changes in [{'expires':0},{'architecture':'x86_64'},{'data_schema':2},{'url':'https://evil.invalid/a'},{'url':'https://d.zhangyue.site/apps/minis-sync/runtime/../a'},{'sequence':True}]:
   with self.subTest(changes=changes),self.assertRaises(u.UpdateError):u.verify(self.sign(self.manifest(**changes)),self.public)
 def test_bad_size_and_sha_preserve_original(self):
  p=self.package()
  with self.assertRaises(u.UpdateError):u.install(self.prefix,self.manifest(),p,self.home,check=lambda _:None)
  self.assertFalse((self.prefix/'state.json').exists());self.assertTrue((self.prefix/'runtime-v1').is_dir())
 def test_failed_health_never_switches(self):
  p=self.package();m=self.manifest(sha256=u.digest(p),bytes=p.stat().st_size)
  def fail(_):raise u.UpdateError('bad runtime')
  with self.assertRaises(u.UpdateError):u.install(self.prefix,m,p,self.home,check=fail)
  self.assertEqual(u.read_state(self.prefix)['current'],'runtime-v1');self.assertFalse(list(self.prefix.glob('.stage-*')))
 def test_upgrade_and_rollback_keep_user_data(self):
  p=self.package();m=self.install(p);self.assertEqual(u.read_state(self.prefix)['current'],'versions/'+m['sha256']);self.assertEqual((self.prefix/'data-recovery'/m['sha256']/'.zcode/user.json').read_text(),'private fixture')
  (self.home/'.zcode/user.json').write_text('new user change');u.rollback(self.prefix,check=lambda _:None)
  self.assertEqual(u.read_state(self.prefix)['current'],'runtime-v1');self.assertEqual((self.home/'.zcode/user.json').read_text(),'new user change');self.assertTrue((self.prefix/'versions'/m['sha256']).is_dir())
 def test_old_signed_release_refused(self):
  p=self.package();self.install(p,sequence=3)
  with self.assertRaises(u.UpdateError):self.install(p,sequence=2)
 def test_same_sequence_different_bytes_refused(self):
  p=self.package();self.install(p);q=self.package(('safe-link','opencode'))
  with self.assertRaises(u.UpdateError):self.install(q)
 def test_host_upgrade_required(self):
  p=self.package()
  with self.assertRaises(u.UpdateError):self.install(p,min_host=999)
 def test_traversal_and_escaping_links(self):
  for path,target in [('../escape','opencode'),('safe','../../escape'),('absolute','/etc/passwd')]:
   p=self.package((path,target));dest=self.root/('out'+str(len(list(self.root.glob('out*')))));dest.mkdir()
   with self.assertRaises(u.UpdateError):u.unpack(p,dest)
   self.assertFalse(list(dest.iterdir()))
 def test_valid_relative_link(self):
  p=self.package(('safe','opencode'));dest=self.root/'out';dest.mkdir();u.unpack(p,dest);self.assertTrue((dest/'safe').is_file())
 def test_invalid_pointer_rejected(self):
  u.atomic(self.prefix/'state.json',u.encoded({'current':'../../home','previous':None,'highest':0}))
  with self.assertRaises(u.UpdateError):u.read_state(self.prefix)
 def test_private_symlink_backup_failure_preserves_pointer(self):
  p=self.package();(self.home/'.zcode').rename(self.home/'old');(self.home/'.zcode').symlink_to(self.home/'old')
  with self.assertRaises(u.UpdateError):self.install(p)
  self.assertFalse((self.prefix/'state.json').exists())
 def test_declared_unpacked_limit_enforced(self):
  p=self.package();dest=self.root/'out';dest.mkdir()
  with self.assertRaises(u.UpdateError):u.unpack(p,dest,1)
  self.assertFalse(list(dest.iterdir()))
 def test_managed_task_holds_update_lock(self):
  import fcntl,sys
  # Use a harmless child command and observe the actual advisory lock.
  child="from pathlib import Path;import time;Path("+repr(str(self.root/'ready'))+").touch();time.sleep(1)"
  code="import importlib.util,sys;from pathlib import Path;s=importlib.util.spec_from_file_location('u',sys.argv[1]);u=importlib.util.module_from_spec(s);s.loader.exec_module(u);u.PREFIX=Path(sys.argv[2]);sys.argv=['u','run',sys.executable,'-c',sys.argv[3]];u.main()"
  p=subprocess.Popen([sys.executable,'-B','-c',code,str(Path(u.__file__)),str(self.prefix),child])
  try:
   until=time.time()+5
   while not (self.root/'ready').exists() and time.time()<until:time.sleep(.02)
   self.assertTrue((self.root/'ready').exists())
   with (self.prefix/'update.lock').open('a') as lock:
    with self.assertRaises(BlockingIOError):fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  finally:p.wait(timeout=5)
 def test_health_exit_and_output_limit(self):
  r=self.root/'health';(r/'lib').mkdir(parents=True)
  for name in ['opencode','lib/ld-musl-aarch64.so.1']:
   p=r/name;p.write_text("#!/bin/sh\nprintf 'fixture-version\\n'\n");p.chmod(0o755)
  u.health(r)
  (r/'opencode').write_text("#!/bin/sh\nexit 3\n")
  with self.assertRaises(u.UpdateError):u.health(r)
  (r/'opencode').write_text("#!/bin/sh\nexec /bin/dd if=/dev/zero bs=1024 count=100 2>/dev/null\n")
  with self.assertRaises(u.UpdateError):u.health(r)
if __name__=='__main__':unittest.main()
