"""Real Swift URLSession client against the isolated Python v3 service."""
import hashlib,json,os,subprocess,sys,tempfile,threading,unittest,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'integrations/cloud-v3'))
sys.path.insert(0,str(ROOT/'tests/cloud-v3-reference'))
from minis_cloud import CloudStore
from minis_cloud_http import create_server
import test_workspace

class SwiftHTTPTests(unittest.TestCase):
    def test_two_devices_shared_file_deletion_account_boundary_and_retries(self):
        probe=ROOT/'ios/MinisSyncCore/.build/debug/MinisSyncProbe'
        self.assertTrue(probe.is_file(),'build Swift probe first')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store=None
            endpoint=os.environ.get('MINIS_TEST_CLOUD_URL')
            if not endpoint:store=CloudStore(root/'cloud')
            if store is not None:
                server=create_server(store,{'fixture-a':'1','fixture-b':'2'})
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
                endpoint='http://127.0.0.1:'+str(server.server_port)
            token=root/'token';token.write_text('fixture-a');token.chmod(0o600)
            url=endpoint
            factory=test_workspace.WorkspaceTests()
            def package(name,files):
                path=root/name;path.write_bytes(factory.package(files,config=False));return path
            def run(command,device,path,base=None,op=None,credential=token,ok=True,mode="full"):
                args=[str(probe),command,url,str(credential),device,str(path)]
                if command=='commit':args += [base or '-',op,mode]
                result=subprocess.run(args,text=True,capture_output=True,timeout=30)
                if not ok:self.assertNotEqual(result.returncode,0);return
                self.assertEqual(result.returncode,0,result.stderr)
                return json.loads(result.stdout)
            first=package('android.minisbak',{'shared/main.py':b'old','shared/deleted.txt':b'delete'})
            a=run('commit','android',first,op='android-first')
            run('fetch','android',root/'android-down.minisbak')
            run('fetch','iphone',root/'iphone-down.minisbak')
            local=package('iphone.minisbak',{'shared/main.py':b'old','shared/new.py':b'iphone'})
            merged=run('commit','iphone',local,base=a['sha256'],op='iphone-second')
            repeat=run('commit','iphone',local,base=a['sha256'],op='iphone-second')
            self.assertEqual(merged,repeat)
            run('fetch','iphone',root/'iphone-base.minisbak')
            newest=package('newest.minisbak',{'shared/main.py':b'changed','shared/new.py':b'iphone'})
            delta=root/'delta.zip'
            with zipfile.ZipFile(newest) as current,zipfile.ZipFile(root/'iphone-base.minisbak') as before,zipfile.ZipFile(delta,'w',zipfile.ZIP_DEFLATED) as output:
                changed={n:current.read(n) for n in current.namelist() if n not in before.namelist() or current.read(n)!=before.read(n)}
                deleted=sorted(set(before.namelist())-set(current.namelist()))
                output.writestr('__delta__.json',json.dumps({'v':2,'base':merged['sha256'],'deleted':deleted}))
                for name,data in changed.items():output.writestr(name,data)
            merged=run('commit','iphone',delta,base=merged['sha256'],op='iphone-delta',mode='delta')
            run('fetch','android',root/'final.minisbak')
            with zipfile.ZipFile(root/'final.minisbak') as z:
                paths={r['path'] for r in map(json.loads,z.read('files.index.jsonl').splitlines())}
            self.assertEqual(paths,{'shared/main.py','shared/new.py'})
            other=root/'other-token';other.write_text('fixture-b');other.chmod(0o600)
            run('fetch','intruder',root/'other.minisbak',credential=other,ok=False)
            self.assertEqual(merged['version'],3)
            if store is not None:
                self.assertEqual(store.head('1')['version'],3);self.assertIsNone(store.head('2'))
                with store._db() as c:
                    receipt=c.execute('SELECT applied_scope FROM devices WHERE uid=? AND device=?',('1','android')).fetchone()
                self.assertEqual(receipt[0],'workspace')
if __name__=='__main__':unittest.main(verbosity=2)
