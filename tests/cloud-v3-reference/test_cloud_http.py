import hashlib, http.client, io, json, sys, tempfile, threading, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from minis_cloud import CloudStore
from minis_cloud_http import create_server, handle, RecoveryUnavailable

class CloudHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=CloudStore(self.tmp.name)
        self.server=create_server(self.store,{'alpha':'1','beta':'2'},port=0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)
    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
    def request(self,method,path,body=None,token='alpha',headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        h={'Authorization':'Bearer '+token};h.update(headers or {})
        c.request(method,path,body=body,headers=h);r=c.getresponse();raw=r.read();result=(r.status,dict(r.getheaders()),raw);c.close();return result
    def package(self,value='x'):
        raw=(json.dumps({'v':1,'t':'session','d':{'id':value}})+'\n').encode()
        entries={'data/sessions.jsonl':raw}; entries['manifest.json']=json.dumps({'categories':{'chats':True},'integrity':{'data/sessions.jsonl':hashlib.sha256(raw).hexdigest()}}).encode()
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:
            for k,v in entries.items():z.writestr(k,v)
        return b.getvalue()
    def commit(self,raw,op='one',extra=''):
        status,_,body=self.request('POST','/api/minis/v3/commit?device=a&op='+op+'&name=a.minisbak'+extra,raw)
        return status,json.loads(body)
    def test_full_head_download_ack_retry_isolation(self):
        status,receipt=self.commit(self.package());self.assertEqual(status,200)
        self.assertEqual(self.commit(self.package())[1],receipt)
        self.assertEqual(self.commit(self.package('other'))[0],409)
        status,_,raw=self.request('GET','/api/minis/v3/head');self.assertEqual(json.loads(raw)['head']['sha256'],receipt['sha256'])
        path='/api/minis/v3/snapshots/'+receipt['sha256']
        status,h,raw=self.request('GET',path);self.assertEqual(status,200);self.assertEqual(hashlib.sha256(raw).hexdigest(),receipt['sha256']);self.assertEqual(int(h['Content-Length']),len(raw))
        self.assertEqual(json.loads(self.request('GET',path+'/manifest')[2])['sha256'],receipt['sha256'])
        self.assertEqual(self.request('GET',path,token='beta')[0],409)
        ack='/api/minis/v3/ack?device=a&sha='+receipt['sha256']
        self.assertEqual(self.request('POST',ack,b'')[0],200);self.assertEqual(self.request('POST',ack,b'',token='beta')[0],409)
        self.assertIsNone(json.loads(self.request('GET','/api/minis/v3/head?user_id=1',token='beta')[2])['head'])
    def test_auth_errors_and_unknown(self):
        self.assertEqual(self.request('GET','/api/minis/v3/head',token='bad')[0],401)
        self.assertEqual(self.request('GET','/not-owned')[0],404)
        self.assertFalse(handle(None,'/unknown',None,self.store))
        self.assertEqual(self.commit(b'bad')[0],400)
        self.assertEqual(self.request('POST','/api/minis/v3/ack?device=a&sha=x',b'no')[0],400)
        self.assertEqual(self.request('POST','/api/minis/v3/commit?device=a&op=two',b'x',headers={'Content-Length':str(2*1024**3+1)})[0],400)
    def test_delta(self):
        _,base=self.commit(self.package());self.request('POST','/api/minis/v3/ack?device=a&sha='+base['sha256'],b'');target=self.package('y');b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z,zipfile.ZipFile(io.BytesIO(target)) as source:
            z.writestr('__delta__.json',json.dumps({'v':2,'base':base['sha256'],'deleted':[]}))
            for n in source.namelist():z.writestr(n,source.read(n))
        self.assertEqual(self.commit(b.getvalue(),'delta','&mode=delta&base='+base['sha256'])[0],200)
    def test_recovery(self):
        with patch.object(self.store,'head',side_effect=RecoveryUnavailable(7)):
            status,h,raw=self.request('GET','/api/minis/v3/head')
        self.assertEqual(status,503);self.assertEqual(json.loads(raw)['retry_after'],7);self.assertEqual(h['Retry-After'],'7')
    def test_remote_bind_rejected(self):
        with self.assertRaises(ValueError):create_server(self.store,{'test':'1'},host='0.0.0.0',port=0)

    def test_short_upload_does_not_advance_head(self):
        import socket
        sock=socket.create_connection(('127.0.0.1',self.server.server_port),timeout=3)
        sock.sendall(b'POST /api/minis/v3/commit?device=a&op=short HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer alpha\r\nContent-Length: 100\r\n\r\nx')
        sock.shutdown(socket.SHUT_WR)
        raw=b''
        while True:
            chunk=sock.recv(4096)
            if not chunk:break
            raw+=chunk
        sock.close();self.assertIn(b'400 Bad Request',raw);self.assertIn(b'short_upload',raw)
        self.assertIsNone(self.store.head('1'))
    def test_missing_base_duplicate_parameters_and_capacity(self):
        self.assertEqual(self.commit(self.package(),extra='&base='+'f'*64)[0],409)
        self.assertEqual(self.commit(self.package(),extra='&op=duplicate')[0],400)
        from minis_store import InvalidBackup
        with patch.object(self.store,'commit',side_effect=InvalidBackup('storage_capacity_exhausted_originals_preserved')):
            status,body=self.commit(self.package())
        self.assertEqual(status,503);self.assertEqual(body['retry_after'],5)
    def test_disconnect_upload_preserves_head(self):
        import socket,time
        sock=socket.create_connection(('127.0.0.1',self.server.server_port),timeout=3)
        sock.sendall(b'POST /api/minis/v3/commit?device=a&op=disconnect HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer alpha\r\nContent-Length: 100\r\n\r\nx');sock.close()
        self.assertIsNone(self.store.head('1'))

    def test_factory_concurrency_limit_rejects_without_reading_body(self):
        entered=threading.Event();release=threading.Event()
        original=self.store.head
        def blocked(uid):
            entered.set();release.wait(2);return original(uid)
        results=[]
        with patch.object(self.store,'head',side_effect=blocked):
            worker=threading.Thread(target=lambda:results.append(self.request('GET','/api/minis/v3/head')))
            worker.start();self.assertTrue(entered.wait(2))
            try:
                status,h,raw=self.request('POST','/api/minis/v3/commit?device=a&op=busy',b'',headers={'Content-Length':'100000000'})
                self.assertEqual(status,503);self.assertEqual(json.loads(raw)['error'],'busy');self.assertEqual(json.loads(raw)['retry_after'],1)
                self.assertEqual(h['Retry-After'],'1')
            finally:release.set();worker.join(3)
        self.assertEqual(results[0][0],200)
        self.assertEqual(self.request('GET','/api/minis/v3/head')[0],200)

    def test_scoped_commit_ack_and_scope_conflict(self):
        _,first=self.commit(self.package())
        ack='/api/minis/v3/ack?device=a&sha='+first['sha256']+'&scope=chats'
        status,_,raw=self.request('POST',ack,b'');self.assertEqual(status,200);self.assertEqual(json.loads(raw)['applied_scope'],'chats')
        self.assertEqual(self.commit(self.package('new'),'chat','&base='+first['sha256'])[0],409)
        status,receipt=self.commit(self.package('new'),'chat','&base='+first['sha256']+'&scope=chats')
        self.assertEqual(status,200);self.assertEqual(receipt['scope'],'chats')
        self.assertEqual(self.commit(self.package('new'),'chat','&base='+first['sha256']+'&scope=all')[0],409)
        self.assertEqual(self.commit(self.package('new'),'invalid','&scope=bad')[0],400)
