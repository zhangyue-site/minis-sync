#!/usr/bin/env python3
"""Managed, signed ARM64 runtime updates. Never executes downloaded installers."""
import base64, contextlib, fcntl, resource, hashlib, io, json, os, shutil, subprocess, sys, tarfile, tempfile, time, urllib.parse, urllib.request
from pathlib import Path, PurePosixPath
INDEX='https://d.zhangyue.site/apps/minis-sync/runtime/stable.json'
PREFIX=Path('/opt/minis-clients')
MAX_PACKAGE=256*1024*1024
MAX_UNPACKED=1024*1024*1024
class UpdateError(Exception): pass

def atomic(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.write-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
        d=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(d)
        finally:os.close(d)
    finally:
        if os.path.exists(name):os.unlink(name)

def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def verify(raw,key,now=None):
    if len(raw)>65536:raise UpdateError('manifest too large')
    try:
        envelope=json.loads(raw);payload=base64.b64decode(envelope['payload'],validate=True);sig=base64.b64decode(envelope['signature'],validate=True)
        n=int(key['n'],16);e=int(key['e']);k=(n.bit_length()+7)//8
        if k<256 or len(sig)!=k or int.from_bytes(sig,'big')>=n:raise ValueError('signature size')
        block=pow(int.from_bytes(sig,'big'),e,n).to_bytes(k,'big')
        tail=bytes.fromhex('3031300d060960864801650304020105000420')+hashlib.sha256(payload).digest()
        if block!=b'\0\1'+b'\xff'*(k-len(tail)-3)+b'\0'+tail:raise ValueError('signature')
        m=json.loads(payload);clock=time.time() if now is None else now
        if m['format']!=1 or m['architecture']!='aarch64' or m['adapter']!=1 or m['data_schema']!=1:raise ValueError('incompatible runtime')
        if not isinstance(m['sequence'],int) or isinstance(m['sequence'],bool) or m['sequence']<1:raise ValueError('sequence')
        if not isinstance(m['min_host'],int) or m['min_host']<1:raise ValueError('host')
        if not clock-300<=m['expires']<=clock+91*86400 or m['issued']>clock+300 or m['issued']>m['expires']:raise ValueError('expired manifest')
        if not isinstance(m['bytes'],int) or not 1<=m['bytes']<=MAX_PACKAGE:raise ValueError('package size')
        if not isinstance(m['unpacked_bytes'],int) or not 1<=m['unpacked_bytes']<=MAX_UNPACKED:raise ValueError('unpacked size')
        if len(m['sha256'])!=64 or any(c not in '0123456789abcdef' for c in m['sha256']):raise ValueError('digest')
        source(m['url'])
        return m
    except (KeyError,TypeError,ValueError,OverflowError,json.JSONDecodeError) as e:raise UpdateError('untrusted or incompatible manifest') from e

def source(url):
    u=urllib.parse.urlsplit(url)
    if u.scheme!='https' or u.hostname!='d.zhangyue.site' or u.port not in (None,443) or u.username or u.password or u.query or u.fragment or not u.path.startswith('/apps/minis-sync/runtime/') or '%' in u.path or '..' in u.path.split('/'):raise UpdateError('untrusted download origin')
    return url
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):raise UpdateError('redirect refused')
def fetch(url,limit):
    source(url)
    with urllib.request.build_opener(NoRedirect()).open(url,timeout=30) as response:
        raw=response.read(limit+1)
        if len(raw)>limit:raise UpdateError('download limit')
        return raw

def member_path(name):
    p=PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or '\\' in name or not p.parts:raise UpdateError('unsafe archive path')
    return p

def unpack(archive,target,limit=MAX_UNPACKED):
    # Validate the whole archive before creating files. Link traversal is forbidden.
    with tarfile.open(archive,'r:gz') as t:
        members=t.getmembers();seen=set();links=set();total=0
        if len(members)>50000:raise UpdateError('too many members')
        for m in members:
            p=member_path(m.name)
            if str(p) in seen:raise UpdateError('duplicate member')
            seen.add(str(p));total+=m.size
            if total>limit or total>MAX_UNPACKED or m.size<0:raise UpdateError('unpacked limit')
            if not(m.isfile() or m.isdir() or m.issym()):raise UpdateError('unsupported archive member')
            if m.issym():
                if PurePosixPath(m.linkname).is_absolute():raise UpdateError('absolute link')
                parts=list(p.parent.parts)
                for part in PurePosixPath(m.linkname).parts:
                    if part=='..':
                        if not parts:raise UpdateError('escaping link')
                        parts.pop()
                    elif part!='.':parts.append(part)
                links.add(str(p))
        for m in members:
            if any(str(parent) in links for parent in member_path(m.name).parents):raise UpdateError('link ancestor')
        for m in members:
            out=target.joinpath(*member_path(m.name).parts);out.parent.mkdir(parents=True,exist_ok=True)
            if m.isdir():out.mkdir(exist_ok=True)
            elif m.issym():out.symlink_to(m.linkname)
            else:
                with t.extractfile(m) as src,out.open('xb') as dst:shutil.copyfileobj(src,dst)
                out.chmod(m.mode&0o777)
        for required in ['opencode','zcode.cjs','zcode-builtin.json','usr/bin/node','usr/bin/python3','lib/ld-musl-aarch64.so.1']:
            p=target/required
            if not p.is_file() or not p.resolve().is_relative_to(target.resolve()):raise UpdateError('missing runtime member '+required)

def health(runtime):
    r=str(runtime);loader=r+'/lib/ld-musl-aarch64.so.1'
    env=dict(os.environ,LD_LIBRARY_PATH=r+'/lib:'+r+'/usr/lib',ICU_DATA=r+'/usr/share/icu/76.1',NODE_ICU_DATA=r+'/usr/share/icu/76.1',ZCODE_BUILTIN_PROVIDER_CONFIG_FILE=r+'/zcode-builtin.json')
    # --version only: no prompts, model requests or user data migrations.
    for command in [[r+'/opencode','--version'],[loader,'--library-path',env['LD_LIBRARY_PATH'],r+'/usr/bin/node',r+'/zcode.cjs','--version']]:
        with tempfile.TemporaryFile() as output:
            def bounded_output():resource.setrlimit(resource.RLIMIT_FSIZE,(65536,65536))
            p=subprocess.run(command,env=env,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,timeout=45,preexec_fn=bounded_output)
            output.seek(0);text=output.read(65537)
        if p.returncode or not text.strip() or len(text)>65536:raise UpdateError('runtime health check failed')

def read_state(prefix):
    p=prefix/'state.json'
    if not p.exists():return {'current':'runtime-v1','previous':None,'highest':0}
    s=json.loads(p.read_text())
    for field in ['current','previous']:
        v=s.get(field)
        if v is not None and (v!='runtime-v1' and not(v.startswith('versions/') and len(v)==73 and all(c in '0123456789abcdef' for c in v[9:]))):raise UpdateError('invalid runtime pointer')
    return s

def backup_user(prefix,home,sha):
    dest=prefix/'data-recovery'/sha
    if dest.exists():return
    dest.parent.mkdir(exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix='.recovery-',dir=prefix))
    try:
        for rel in ['.config/opencode','.local/share/opencode','.local/state/opencode','.zcode']:
            src=home/rel
            if src.is_symlink():raise UpdateError('user data link needs manual backup')
            if src.is_dir():shutil.copytree(src,temp/rel,symlinks=True)
        os.rename(temp,dest)
    finally:
        if temp.exists():shutil.rmtree(temp)

def install(prefix,m,package,home,check=health):
    state=read_state(prefix)
    if int((prefix/'host-version').read_text().strip())<m['min_host']:raise UpdateError('update Minis first')
    if m['sequence']<state['highest']:raise UpdateError('release rollback refused')
    sha=m['sha256'];relative='versions/'+sha
    if package.stat().st_size!=m['bytes'] or digest(package)!=sha:raise UpdateError('payload mismatch')
    if m['sequence']==state['highest'] and state.get('release_sha') not in (None,sha):raise UpdateError('release sequence reused')
    if state['current']==relative:return 'already current'
    target=prefix/relative;target.parent.mkdir(exist_ok=True)
    if not target.exists():
        stage=Path(tempfile.mkdtemp(prefix='.stage-',dir=prefix))
        try:
            unpack(package,stage,m['unpacked_bytes']);check(stage)
            atomic(stage/'.payload-sha256',(sha+'\n').encode());os.rename(stage,target)
        finally:
            if stage.exists():shutil.rmtree(stage)
    else:
        if (target/'.payload-sha256').read_text().strip()!=sha:raise UpdateError('runtime receipt mismatch')
        check(target)
    backup_user(prefix,home,sha)
    next_state={'current':relative,'previous':state['current'],'highest':max(state['highest'],m['sequence']),'release_sha':sha,'versions':m.get('versions',{})}
    atomic(prefix/'state.json',encoded(next_state));return 'updated; existing processes retain their runtime'

def rollback(prefix,check=health):
    s=read_state(prefix);previous=s.get('previous')
    if not previous or previous==s['current']:raise UpdateError('no previous runtime')
    check(prefix/previous)
    s['current'],s['previous']=previous,s['current']
    atomic(prefix/'state.json',encoded(s));return 'runtime rolled back; user data preserved; recovery copies available'

def main():
    PREFIX.mkdir(exist_ok=True)
    if len(sys.argv)>2 and sys.argv[1]=='run':
        # Keep a shared lock for the entire child lifetime. Updates/rollback take
        # the exclusive lock, so user data snapshots never race managed tasks.
        with (PREFIX/'update.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
            except BlockingIOError:raise UpdateError('runtime update in progress; retry after it finishes')
            env=dict(os.environ)
            if env.get('MINIS_TOOL_NAME')=='python3':env['PYTHONHOME']=env['MINIS_TOOL_HOME']
            else:env.pop('PYTHONHOME',None)
            result=subprocess.run(sys.argv[2:],env=env,pass_fds=(lock.fileno(),))
            sys.exit(result.returncode if result.returncode>=0 else 128-result.returncode)
        return
    with (PREFIX/'update.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise UpdateError('another runtime update is active')
        command=sys.argv[1] if len(sys.argv)>1 else 'status'
        if command=='status':print(json.dumps(read_state(PREFIX),ensure_ascii=False));return
        if command=='rollback':print(rollback(PREFIX));return
        if command not in ('check-update','update'):raise UpdateError('expected status, check-update, update or rollback')
        key=json.loads((PREFIX/'update-key.json').read_text());m=verify(fetch(INDEX,65536),key)
        if command=='check-update':print(json.dumps({'available':read_state(PREFIX)['current']!='versions/'+m['sha256'],'versions':m.get('versions',{}),'sequence':m['sequence']}));return
        if shutil.disk_usage(PREFIX).free<m['bytes']+m['unpacked_bytes']+64*1024*1024:raise UpdateError('insufficient free space; original runtime preserved')
        fd,name=tempfile.mkstemp(prefix='.package-',dir=PREFIX);os.close(fd);package=Path(name)
        try:
            # Stream bounded bytes; never hold a full runtime in memory.
            source(m['url']);h=hashlib.sha256();count=0
            with urllib.request.build_opener(NoRedirect()).open(m['url'],timeout=30) as response,package.open('wb') as out:
                while True:
                    b=response.read(1024*1024)
                    if not b:break
                    count+=len(b)
                    if count>m['bytes']:raise UpdateError('payload oversized')
                    h.update(b);out.write(b)
                out.flush();os.fsync(out.fileno())
            if count!=m['bytes'] or h.hexdigest()!=m['sha256']:raise UpdateError('payload mismatch')
            print(install(PREFIX,m,package,Path.home()))
        finally:package.unlink(missing_ok=True)
if __name__=='__main__':
    try:main()
    except Exception as e:print('Runtime update stopped: '+str(e),file=sys.stderr);sys.exit(1)
