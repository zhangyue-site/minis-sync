"""Authenticated v3 adapter and isolated loopback acceptance server.

Embed: handle(handler, handler.path, authenticated_user, cloud_store) -> handled.
CLI: python3 server/minis_cloud_http.py --root /tmp/minis-v3 --port 18763
     --token-file /tmp/minis-v3-tokens.json
Token file must be mode 0600 and contain {"test-only-token": "123"}.
"""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit
from minis_cloud import CloudStore, CloudConflict
from minis_store import InvalidBackup, MAX_UPLOAD

class RecoveryUnavailable(Exception):
    def __init__(self,retry_after=5):
        self.retry_after=max(1,int(retry_after)); super().__init__('recovery_in_progress')

def _json(handler,status,payload):
    raw=json.dumps(payload,separators=(',',':')).encode()
    handler.send_response(status);handler.send_header('Content-Type','application/json; charset=utf-8')
    handler.send_header('Content-Length',str(len(raw)));handler.send_header('Cache-Control','no-store')
    if status==503:handler.send_header('Retry-After',str(payload.get('retry_after',5)))
    handler.send_header('Connection','close');handler.end_headers();handler.wfile.write(raw);handler.close_connection=True

def handle(handler,path,user,cloud_store):
    """Return False for unrelated routes; uid comes exclusively from authenticated user."""
    parsed=urlsplit(path); route=parsed.path
    snapshot=re.fullmatch(r'/api/minis/v3/snapshots/([a-f0-9]{64})(/manifest)?',route)
    if route not in ('/api/minis/v3/head','/api/minis/v3/commit','/api/minis/v3/ack') and snapshot is None:return False
    try:
        if not isinstance(user,dict) or not user.get('user_id'):
            _json(handler,401,{'ok':False,'error':'authentication_required'});return True
        uid=user['user_id']
        method=handler.command
        expected='POST' if route.endswith(('/commit','/ack')) else 'GET'
        if method!=expected:raise InvalidBackup('method_not_allowed')
        params=parse_qs(parsed.query,keep_blank_values=True)
        def arg(key,default=None):
            values=params.get(key)
            if values is None:return default
            if len(values)!=1:raise InvalidBackup('duplicate_parameter')
            return values[0]
        if getattr(cloud_store,'recovering',False):raise RecoveryUnavailable(getattr(cloud_store,'retry_after',5))
        if route.endswith('/head'):
            _json(handler,200,{'ok':True,'head':cloud_store.head(uid),'protocol':3})
        elif snapshot:
            sha=snapshot[1]
            if snapshot[2]:_json(handler,200,dict(ok=True,**cloud_store.manifest(uid,sha)))
            else:
                source=cloud_store.path(uid,sha)
                with source.open('rb') as f:
                    handler.send_response(200);handler.send_header('Content-Type','application/octet-stream')
                    handler.send_header('Content-Length',str(source.stat().st_size));handler.send_header('X-Content-SHA256',sha)
                    handler.send_header('ETag','"'+sha+'"');handler.send_header('Cache-Control','no-store')
                    handler.send_header('Connection','close');handler.end_headers();handler.close_connection=True
                    shutil.copyfileobj(f,handler.wfile,1024*1024)
        else:
            if handler.headers.get('Transfer-Encoding'):raise InvalidBackup('transfer_encoding_not_supported')
            lengths=handler.headers.get_all('Content-Length',[])
            if len(lengths)!=1 or not re.fullmatch(r'[0-9]+',lengths[0]):raise InvalidBackup('content_length_required')
            length=int(lengths[0])
            if length>MAX_UPLOAD:raise InvalidBackup('size_out_of_range')
            if route.endswith('/ack'):
                if length!=0:raise InvalidBackup('ack_body_must_be_empty')
                receipt=cloud_store.ack(uid,arg('device'),arg('sha'),scope=arg('scope','all'))
            else:
                if length<=0:raise InvalidBackup('size_out_of_range')
                receipt=cloud_store.commit(uid,arg('device'),arg('base'),arg('op'),handler.rfile,length,arg('name','backup.minisbak'),arg('mode','full'),scope=arg('scope','all'))
            _json(handler,200,dict(ok=True,**receipt))
    except RecoveryUnavailable as e:_json(handler,503,{'ok':False,'error':'recovery_in_progress','retry_after':e.retry_after})
    except CloudConflict as e:_json(handler,409,{'ok':False,'error':str(e)})
    except FileNotFoundError:_json(handler,409,{'ok':False,'error':'cloud_version_not_found'})
    except (socket.timeout,TimeoutError):_json(handler,400,{'ok':False,'error':'upload_timeout'})
    except InvalidBackup as e:
        status=503 if str(e)=='storage_capacity_exhausted_originals_preserved' else 400
        _json(handler,status,dict(ok=False,error=str(e),**({'retry_after':5} if status==503 else {})))
    except (sqlite3.OperationalError,OSError) as e:
        if isinstance(e,(BrokenPipeError,ConnectionResetError,ConnectionAbortedError)):
            handler.close_connection=True;return True
        _json(handler,503,{'ok':False,'error':'storage_temporarily_unavailable','retry_after':5})
    return True

def handler_factory(cloud_store,tokens):
    """Tokens are dedicated loopback acceptance credentials, never production cookies."""
    mapping={str(token):str(uid) for token,uid in tokens.items()}
    if not mapping or any(not t or not re.fullmatch(r'[0-9]+',u) for t,u in mapping.items()):raise ValueError('invalid_test_token_mapping')
    class Handler(BaseHTTPRequestHandler):
        timeout=15
        # One bounded metadata merge at a time across every account on this server.
        request_slots=threading.BoundedSemaphore(1)
        def log_message(self,*args):pass
        def _dispatch(self):
            auth=self.headers.get('Authorization','');provided=auth[7:] if auth.startswith('Bearer ') else ''
            uid=next((u for t,u in mapping.items() if hmac.compare_digest(t.encode(),provided.encode())),None)
            acquired=False
            try:
                acquired=self.request_slots.acquire(blocking=False)
                if not acquired:
                    _json(self,503,{'ok':False,'error':'busy','retry_after':1})
                    return
                if not handle(self,self.path,{'user_id':uid} if uid else None,cloud_store):_json(self,404,{'ok':False,'error':'not_found'})
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):self.close_connection=True
            finally:
                if acquired:self.request_slots.release()
        do_GET=_dispatch
        do_POST=_dispatch
        do_PUT=_dispatch
        do_DELETE=_dispatch
    return Handler

def create_server(cloud_store,tokens,host='127.0.0.1',port=0):
    try:loopback=ipaddress.ip_address(host).is_loopback
    except ValueError:loopback=False
    if not loopback:raise ValueError('loopback_bind_required')
    class Server(ThreadingHTTPServer):
        daemon_threads=True
        address_family=socket.AF_INET6 if ':' in host else socket.AF_INET
    return Server((host,port),handler_factory(cloud_store,tokens))

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True)
    parser.add_argument('--port',type=int,default=18763);parser.add_argument('--token-file',required=True)
    args=parser.parse_args();path=Path(args.token_file)
    if path.stat().st_mode & 0o777 != 0o600:parser.error('token-file must have mode 0600')
    tokens=json.loads(path.read_text())
    if not isinstance(tokens,dict):parser.error('token-file must be a JSON token-to-uid object')
    server=create_server(CloudStore(args.root),tokens,port=args.port)
    print('Isolated Minis v3 listening on http://127.0.0.1:'+str(server.server_port),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
