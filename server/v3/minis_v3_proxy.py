"""Authenticated 124 adapter for the single isolated 106 state chain."""
import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
from urllib.parse import parse_qs,urlsplit
SSH=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','root@106.52.123.149']
MAX_UPLOAD=2*1024**3

def enabled():return Path(os.environ.get('MINIS_V3_ENABLED','/opt/notes-site/minis-v3.enabled')).is_file()
def command(request):
    payload=base64.urlsafe_b64encode(json.dumps(request,separators=(',',':')).encode()).decode()
    return SSH+[shlex.join(['/opt/minis-sync/venv/bin/python','/opt/minis-v3/current/server/minis_v3_rpc.py',payload])]
def call(request,stream=None):
    with subprocess.Popen(command(request),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL) as p:
        try:
            left=request.get('length',0)
            while left:
                block=stream.read(min(65536,left))
                if not block or len(block)>left:raise ValueError('short_upload')
                p.stdin.write(block);left-=len(block)
            p.stdin.close();p.stdin=None
            output,_=p.communicate(timeout=900)
            if p.returncode or len(output)>16*1024**2:raise RuntimeError('transport_unavailable')
            result=json.loads(output)
            if result.get('status') not in (200,400,404,409,503):raise RuntimeError('invalid_rpc_response')
            return result
        except Exception:
            p.kill();p.wait();raise

def handle(handler,path,user,download_guard):
    parsed=urlsplit(path);route=parsed.path
    snap=re.fullmatch(r'/api/minis/(v3/)?snapshots/([a-f0-9]{64})(/manifest)?',route)
    routes={'/api/minis/v3/head':('GET','head'),'/api/minis/v3/commit':('POST','v3_commit'),'/api/minis/v3/ack':('POST','v3_ack'),'/api/minis/snapshots':(None,'list'),'/api/minis/delta':('POST','legacy_delta')}
    if not snap and route not in routes:return False
    started=False
    def reply(status,data):
        if status==503:data=dict(data,retry_after=data.get('retry_after',5))
        handler._json(status,data)
    try:
        try:uid=str(user['user_id']) if user is not None else ''
        except (KeyError,TypeError,IndexError):uid=''
        if not uid:reply(401,{'ok':False,'error':'authentication_required'});return True
        if not re.fullmatch(r'[0-9]{1,32}',uid):raise ValueError('bad_uid')
        params=parse_qs(parsed.query,keep_blank_values=True)
        allowed={'device','name','base','op','mode','scope','sha'}
        if set(params)-allowed or any(len(v)!=1 for v in params.values()):raise ValueError('invalid_parameters')
        args={k:v[0] for k,v in params.items()}
        if any(len(v)>512 for v in args.values()):raise ValueError('parameter_too_long')
        for key in ('base','sha'):
            if args.get(key) and not re.fullmatch(r'[a-f0-9]{64}',args[key]):raise ValueError('bad_sha')
        if snap:method,action='GET','manifest' if snap[3] else 'download'
        else:method,action=routes[route]
        if route=='/api/minis/snapshots':
            method=handler.command if handler.command in ('GET','POST') else 'GET'
            if method=='POST':action='legacy_put'
        if method!=handler.command:reply(405,{'ok':False,'error':'method_not_allowed'});return True
        request=dict(args,uid=uid,action=action)
        if 'sha' in request:request['sha256']=request.pop('sha')
        if snap:request.update(sha256=snap[2],legacy=not bool(snap[1]))
        if handler.command=='POST':
            if handler.headers.get('Transfer-Encoding'):raise ValueError('transfer_encoding_not_supported')
            lengths=handler.headers.get_all('Content-Length',[])
            if len(lengths)!=1 or not re.fullmatch(r'[0-9]{1,12}',lengths[0]):raise ValueError('content_length_required')
            length=int(lengths[0]);limit=256*1024**2 if action=='legacy_delta' else MAX_UPLOAD
            if (action=='v3_ack' and length!=0) or (action!='v3_ack' and not 0<length<=limit):raise ValueError('size_out_of_range')
            request['length']=length
            if length and not download_guard(length):reply(429,{'ok':False,'error':'server_transfer_budget_exhausted'});return True
            handler.connection.settimeout(600)
        if action!='download':
            response=call(request,handler.rfile if handler.command=='POST' else None);reply(response['status'],response['data']);return True
        response=call(dict(request,action='meta'))
        if response['status']!=200:reply(response['status'],response['data']);return True
        size=response['data']['bytes']
        if type(size) is not int or not 0<size<=MAX_UPLOAD or response['data']['sha256']!=snap[2]:raise RuntimeError('invalid_metadata')
        if not download_guard(size):reply(429,{'ok':False,'error':'download_budget_exhausted'});return True
        with subprocess.Popen(command(request),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL) as p:
            try:
                handler.send_response(200);started=True
                for k,v in {'Content-Type':'application/octet-stream','Content-Length':str(size),'X-Content-SHA256':snap[2],'Cache-Control':'no-store','Connection':'close'}.items():handler.send_header(k,v)
                handler.end_headers();handler.close_connection=True
                left=size
                while left:
                    block=p.stdout.read(min(65536,left))
                    if not block:raise RuntimeError('short_download')
                    handler.wfile.write(block);left-=len(block)
                if p.stdout.read(1) or p.wait(timeout=60):raise RuntimeError('download_failed')
            except Exception:p.kill();p.wait();raise
    except (ValueError,KeyError):
        if started:handler.close_connection=True
        else:reply(400,{'ok':False,'error':'invalid_request'})
    except Exception:
        if started:handler.close_connection=True
        else:reply(503,{'ok':False,'error':'sync_temporarily_unavailable_originals_preserved','retry_after':5})
    return True
