#!/usr/bin/env python3
"""Minis v3 直达前端 v3（106 本机）
- 鉴权：本地 notes.db 副本（cron 每 5 分钟同步）
- 上传：本机 exec v3 RPC（无 SSH 中转）
- 下载：只做鉴权+定位文件，返回 X-Accel-Redirect 让 nginx 直接发（快 10 倍、不占 Python）
- 全链路诊断日志
"""
import base64, json, os, shlex, sqlite3, subprocess, sys, time, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import re

LOCAL_SERVER = os.path.dirname(os.path.abspath(__file__))
SERVER = os.environ.get('MINIS_V3_SERVER', LOCAL_SERVER if os.path.isfile(os.path.join(LOCAL_SERVER, 'minis_v3_proxy.py')) else '/opt/minis-v3/current/server')
sys.path.insert(0, SERVER)
from minis_transfer_budget import configured_budget
import minis_v3_proxy as v3

PORT = 8325
PY = '/opt/minis-sync/venv/bin/python'
RPC = '/opt/minis-v3/current/server/minis_v3_rpc.py'
DB = '/opt/minis-v3/front/notes.db'
AUTH_MAX_AGE = 600
STORE = '/opt/minis-v3/store/snapshots'          # nginx alias 根，内部 URL /__store/


def log(msg):
    sys.stderr.write('[front] %s %s\n' % (time.strftime('%H:%M:%S'), msg))
    sys.stderr.flush()


class AuthReplicaUnavailable(RuntimeError):
    pass


def resolve_uid(token):
    if not token: return None
    from contextlib import closing
    try:
        with closing(sqlite3.connect('file:%s?mode=ro' % DB, uri=True, timeout=2)) as c:
            stamp = c.execute('SELECT synced_at FROM minis_auth_replica WHERE singleton=1').fetchone()
            if not stamp or not 0 <= time.time() - stamp[0] <= AUTH_MAX_AGE:
                raise AuthReplicaUnavailable('auth_replica_stale')
            row = c.execute('SELECT user_id FROM tokens WHERE token=? AND revoked=0 AND expires_at>?',
                            (token, int(time.time()))).fetchone()
        uid = str(row[0]) if row else ''
        return uid if re.fullmatch(r'[0-9]{1,32}', uid) else None
    except (sqlite3.Error, OSError, AuthReplicaUnavailable) as e:
        log('auth-replica-unavailable %s' % type(e).__name__)
        raise AuthReplicaUnavailable('authentication_temporarily_unavailable') from e


def local_command(request):
    payload = base64.urlsafe_b64encode(
        json.dumps(request, separators=(',', ':')).encode()).decode()
    return [PY, RPC, payload]


v3.command = local_command


def reserve_transfer(size):
    try:
        return configured_budget().reserve(size)
    except Exception as e:
        log('transfer-budget-unavailable %s' % type(e).__name__)
        return False


def try_accel(self, path, uid):
    """GET /api/minis/(v3/)?snapshots/<sha>  → 让 nginx 直接发文件。"""
    if self.command != 'GET' or path.query:
        return False
    parts = path.path.strip('/').split('/')
    # ['api','minis','snapshots',sha] 或 ['api','minis','v3','snapshots',sha]
    if len(parts) == 5 and parts[2] == 'v3' and parts[3] == 'snapshots':
        sha = parts[4]
    elif len(parts) == 4 and parts[2] == 'snapshots':
        sha = parts[3]
    else:
        return False
    if parts[0] != 'api' or parts[1] != 'minis':
        return False
    if len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
        return False
    f = os.path.join(STORE, uid, sha + '.minisbak')
    if not os.path.isfile(f):
        return False
    # nginx worker(www) 需要可读；新写入默认 600，这里即时放行
    try:
        os.chmod(os.path.dirname(f), 0o755)
        os.chmod(f, 0o644)
    except OSError:
        pass
    size = os.path.getsize(f)
    if not reserve_transfer(size):
        self._json(429, {'ok': False, 'error': 'download_budget_exhausted'})
        return True
    self.send_response(200)
    self.send_header('Content-Type', 'application/octet-stream')
    self.send_header('Content-Length', str(size))
    self.send_header('X-Content-SHA256', sha)
    self.send_header('Cache-Control', 'no-store')
    self.send_header('X-Accel-Redirect', '/__store/%s/%s.minisbak' % (uid, sha))
    self.end_headers()
    log('ACCEL %s uid=%s %.0fMB' % (sha[:12], uid, size / 1048576))
    return True


class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        if code >= 400:
            self.close_connection = True
            self.send_header('Connection', 'close')
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            self.close_connection = True

    def _serve(self):
        t0 = time.time()
        path = urlsplit(self.path)
        auth = self.headers.get('Authorization', '')
        token = auth[7:].strip() if auth.startswith('Bearer ') else ''
        try:
            uid = resolve_uid(token)
        except AuthReplicaUnavailable:
            self._json(503, {'ok': False, 'error': 'authentication_temporarily_unavailable', 'retry_after': 5})
            return
        user = {'user_id': uid} if uid else None

        if uid and try_accel(self, path, uid):
            return

        # --- diag: 记录请求体长度与响应码 ---
        _orig_sr = self.send_response
        def _sr(code, *a, **k):
            try:
                log('RESP %s %s clen=%s %s' % (code, self.command, self.headers.get('Content-Length'), path.path[:150]))
            except Exception:
                pass
            return _orig_sr(code, *a, **k)
        self.send_response = _sr
        ok = False
        try:
            ok = v3.handle(self, self.path, user, reserve_transfer)
        except Exception as e:
            log('HANDLE-EXC %s: %s' % (type(e).__name__, str(e)[:100]))
            traceback.print_exc()
        if not ok:
            log('404 %s %s' % (self.command, path.path[:50]))
            self._json(404, {'ok': False, 'error': 'not_found'})
        else:
            log('%s %s uid=%s %.0fms' % (self.command, path.path[:44], uid or '-', (time.time() - t0) * 1000))

    def do_GET(self):
        self._serve()

    def do_POST(self):
        self._serve()


if __name__ == '__main__':
    log('front v3 on %d (db=%s, store=%s)' % (PORT, os.path.exists(DB), os.path.isdir(STORE)))
    ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
