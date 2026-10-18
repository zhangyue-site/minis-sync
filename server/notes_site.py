#!/usr/bin/env python3
"""notes.zhangyue.site — 笔记库站：账户注册/登录 + 登录流水 + 密钥门 + 库下载。
SQLite: /opt/notes-site/notes.db  (users / login_log / downloads / app_releases)
Run: systemd notes-site.service (127.0.0.1:8320, nginx 反代 + TLS)。"""
import hashlib, hmac, json, os, secrets, sqlite3, subprocess, time, urllib.parse, urllib.request
from datetime import datetime
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from functools import lru_cache
from minis_store import SnapshotStore, InvalidBackup
from hub_bridge import HubSession, collect_qrs

@lru_cache(maxsize=1)
def minis_snapshots():
    return SnapshotStore(Path(os.environ.get('NOTES_MINIS_STORE', '/opt/notes-site/minis-store')))

# --- 增量同步：manifest / delta 合并（2026-10-17）---
import zipfile as _zipfile
import io as _io
import re as _re

MINIS_DELTA_MAX = 256 * 1024 * 1024

def snapshot_manifest(uid, sha):
    if not _re.fullmatch(r'[a-f0-9]{64}', sha):
        raise InvalidBackup('invalid_digest')
    p = minis_snapshots().path(uid, sha)
    out = []
    with _zipfile.ZipFile(p) as z:
        for i in z.infolist():
            if i.is_dir() or i.filename == '__delta__.json':
                continue
            out.append([i.filename, i.file_size, i.CRC])
    return out

def merge_delta(uid, base_sha, stream, length, name, device):
    from minis_delta import merge_delta_backup
    return merge_delta_backup(minis_snapshots(), uid, base_sha, stream, length, name, device)

# --- COS 直链下载/异步归档（2026-10-17）---
import threading as _threading
import sys as _sys
COS_BUCKET = os.environ.get('COS_BUCKET', 'zy-archive-1302243082')
COS_REGION = os.environ.get('COS_REGION', 'ap-shanghai')
COS_REDIRECT = False
_cos_client = None

def cos_client():
    global _cos_client
    if _cos_client is None:
        for p in ('/usr/local/lib/python3.10/dist-packages', '/usr/local/lib/python3.11/dist-packages'):
            if p not in _sys.path:
                _sys.path.append(p)
        sid = ski = None
        for line in open('/root/.tencent-creds'):
            m = line.strip()
            if m.startswith('SECRET_ID=') and len(m) > 12: sid = m.split('=', 1)[1]
            if m.startswith('SECRET_KEY=') and len(m) > 12: ski = m.split('=', 1)[1]
        from qcloud_cos import CosConfig, CosS3Client
        _cos_client = CosS3Client(CosConfig(Region=COS_REGION, SecretId=sid, SecretKey=ski, Token=None, Scheme='https'))
    return _cos_client

def cos_presigned_get(key, filename, expires=7200):
    return cos_client().get_presigned_url(Method='GET', Bucket=COS_BUCKET, Key=key, Expired=expires,
        Params={'response-content-disposition': 'attachment; filename="' + filename + '"'})

def _cos_async_upload(uid, meta):
    try:
        from minis_archive import ArchiveManager, policy
        ArchiveManager(minis_snapshots(), policy()).enqueue(uid, meta)
    except Exception:
        import logging
        logging.exception('Minis durable archive enqueue failed uid=%s', uid)

DB = Path('/opt/notes-site/notes.db')
VAULT_ZIP = Path('/www/wwwroot/download-hub/evernote-vault-20261014.zip')
VAULT_SHA = Path('/opt/notes-site/vault.sha256')
APP_KEY_FILE = Path('/opt/notes-site/appkey.txt')  # 预制 App 内置密钥（注册通道凭证）
TOKEN_TTL = 30 * 86400
HUB_CREDS = Path('/opt/notes-site/hub.creds')      # "user:pass" for erweima hub Basic (600)
HUB = 'https://erweima.zhangyue.site/'
SYNC_DIR = Path('/www/wwwroot/minis-sessions/sessions-upload')
CAMERA_DIR = Path('/opt/notes-site/camera')
VAULT_SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=12', 'root@106.52.123.149']
MIHOME_TOK_FILE = Path('/opt/notes-site/mihome.token')   # mihome 网关 X-Auth-Token
APPKEY_NONCE = {}   # nonce -> {'exp': ts, 'user': name}  （appkey 查看二次验证）

def _mihome_token():
    try:
        return MIHOME_TOK_FILE.read_text().strip()
    except OSError:
        t = subprocess.run(['grep', '-oP', 'TOKEN = "\\K[^\"]+', '/opt/mihome/api.py'],
                           capture_output=True, text=True, timeout=10).stdout.strip()
        if t:
            MIHOME_TOK_FILE.write_text(t)
            os.chmod(MIHOME_TOK_FILE, 0o600)
        return t

def _mihome(path):
    tok = _mihome_token()
    if not tok:
        return {'error': 'mihome_token_missing'}
    req = urllib.request.Request('http://127.0.0.1:8305' + path,
                                 headers={'X-Auth-Token': tok})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {'error': type(e).__name__}

def _ssh106(cmd, timeout=25, max_output=4000):
    try:
        p = subprocess.run(VAULT_SSH + [cmd], capture_output=True, text=True, timeout=timeout)
        return {'rc': p.returncode, 'out': p.stdout[-max_output:], 'err': p.stderr[-500:]}
    except subprocess.TimeoutExpired:
        return {'rc': 124, 'out': '', 'err': 'timeout'}

CONSOLE_STATUS_SCRIPT = (
    'echo UPT=$(uptime -p 2>/dev/null); '
    'echo SINCE=$(uptime -s 2>/dev/null); '
    'echo LOAD=$(cat /proc/loadavg 2>/dev/null); '
    "echo MEM=$(free -m | awk '/Mem:/{print $3\"/\"$2}' 2>/dev/null); "
    "echo SWAP=$(free -m | awk '/Swap:/{print $3\"/\"$2}' 2>/dev/null); "
    "echo DISK=$(df -h / | awk 'NR==2{print $3\"/\"$2\" \"$5}' 2>/dev/null); "
    'echo LOWPOWER=$(systemctl is-active power-low.slice.service 2>/dev/null); '
    'for s in nginx docker mysqld cron frps xiaozhi-relay xgimi-bridge; do '
    'echo SVC:$s=$(systemctl is-active $s 2>/dev/null); done; '
    'echo USVC:openclaw-gateway=$(XDG_RUNTIME_DIR=/run/user/0 systemctl --user is-active openclaw-gateway 2>/dev/null); '
    'echo DOCKER=$(docker ps --format "{{.Names}}:{{.Status}}" 2>/dev/null | tr "\n" ";")'
)
CONSOLE_ALLOW = (
    'status', 'uptime', 'hostname', 'date', 'whoami', 'nproc', 'df ', 'free ',
    'systemctl ', 'docker ', 'journalctl ', 'tail ', 'crontab -l', 'ls ',
    'cat /var/log/', 'cat /opt/xiaomi-mi/', 'ip -brief', 'ss -tlnp',
    'power-profile status',
)
SESSION_MIRROR = Path('/www/wwwroot/minis-sessions')

APPKEY_PAGES = ('查看激活密钥需要二次验证：生物识别 / 图案路径 / 账号密码。')
CAMERA_TTL = 600  # 任务 10 分钟过期

# ---- AI 额度聚合（给 App 的额度页）----
QUOTA_KEYS_FILE = Path('/opt/notes-site/quota_keys.json')   # {"opencode":"Bearer sk-..","deepseek":"Bearer sk-..","zhipu":"id.secret"} 600
QUOTA_CACHE = Path('/opt/notes-site/quota_cache.json')      # 聚合缓存 600
QUOTA_TTL = 300              # 缓存秒数
QUOTA_FETCH_TIMEOUT = 15     # 单源超时秒

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  key_hash TEXT NOT NULL,          -- PBKDF2(password)
  salt TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',   -- user | admin | app
  created_at TEXT NOT NULL,
  last_login_at TEXT,
  last_login_ip TEXT,
  note TEXT);
CREATE TABLE IF NOT EXISTS tokens(
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  ip TEXT, ua TEXT,
  revoked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS login_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, username TEXT, ip TEXT, ua TEXT,
  ok INTEGER NOT NULL, reason TEXT);
CREATE TABLE IF NOT EXISTS downloads(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, user_id INTEGER, username TEXT, ip TEXT,
  what TEXT, bytes INTEGER);
CREATE TABLE IF NOT EXISTS app_releases(
  version_code INTEGER PRIMARY KEY, version_name TEXT, apk TEXT, sha256 TEXT,
  notes TEXT, published_at TEXT);
CREATE TABLE IF NOT EXISTS camera_tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  spec TEXT NOT NULL,            -- 拍摄要求（给用户看的说明）
  origin TEXT NOT NULL,          -- 谁要的：dianping-risk / manual ...
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|done|expired
  created_at INTEGER NOT NULL,
  result_file TEXT, result_sha TEXT);
"""


def db():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def now():
    return datetime.now().isoformat(timespec='seconds')


def pbkdf2(pw, salt):
    return hashlib.pbkdf2_hmac('sha256', pw.encode(), bytes.fromhex(salt), 120_000).hex()


def new_salt():
    return secrets.token_hex(16)


def log_login(c, username, ip, ua, ok, reason=''):
    c.execute('INSERT INTO login_log(ts,username,ip,ua,ok,reason) VALUES(?,?,?,?,?,?)',
              (now(), username, ip, (ua or '')[:200], 1 if ok else 0, reason))
    c.commit()


def issue_token(c, uid, ip, ua):
    tok = secrets.token_urlsafe(32)
    c.execute('INSERT INTO tokens(token,user_id,created_at,expires_at,ip,ua) VALUES(?,?,?,?,?,?)',
              (tok, uid, int(time.time()), int(time.time()) + TOKEN_TTL, ip, (ua or '')[:200]))
    c.commit()
    return tok

# ---------------------------------------------------------------- quota ----
def _bj(ts_ms):
    """毫秒时间戳或 ISO 字符串 → 北京时间 'MM-DD HH:MM'；异常原样返回。"""
    try:
        if isinstance(ts_ms, str):
            from datetime import datetime
            dt = datetime.fromisoformat(ts_ms.replace('Z', '+00:00'))
            epoch = dt.timestamp() + 8 * 3600
        else:
            epoch = int(ts_ms) / 1000 + 8 * 3600
        t = time.gmtime(epoch)
        return '%02d-%02d %02d:%02d' % (t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min)
    except Exception:
        return str(ts_ms)

def _fetch(url, headers):
    import urllib.request
    if 'User-Agent' not in headers:
        headers['User-Agent'] = 'Mozilla/5.0 (X11; Linux x86_64) QuotaClient/1.0'  # 裸 UA 会被 opencode 拒 401
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=QUOTA_FETCH_TIMEOUT) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))

def quota_collect():
    """并发拉三个源，聚合为 App 友好结构。任何单源失败不影响其它源。"""
    import concurrent.futures as cf
    try:
        keys = json.loads(QUOTA_KEYS_FILE.read_text())
    except Exception:
        return {'error': 'keys_missing'}

    def opencode():
        d = _fetch('https://opencode.ai/zen/go/v1/usage',
                   {'Authorization': keys['opencode']})
        u = d.get('usage', {})
        def w(x):
            if not x: return None
            return {'percent': x.get('percent'), 'reset': _bj(x.get('resetsAt'))}
        return {'rolling': w(u.get('rolling')), 'weekly': w(u.get('weekly')),
                'monthly': w(u.get('monthly'))}

    def deepseek():
        d = _fetch('https://api.deepseek.com/user/balance',
                   {'Authorization': keys['deepseek']})
        b = (d.get('balance_infos') or [{}])[0]
        return {'amount': b.get('total_balance'), 'currency': b.get('currency', 'CNY'),
                'is_available': d.get('is_available')}

    def zhipu():
        d = _fetch('https://open.bigmodel.cn/api/monitor/usage/quota/limit',
                   {'Authorization': keys['zhipu']})
        out = {}
        for lim in (d.get('data', {}).get('limits') or []):
            reset = _bj(lim.get('nextResetTime'))
            if lim.get('type') == 'TOKENS_LIMIT' and lim.get('unit') == 3:
                out['five_hour'] = {'percent': lim.get('percentage'), 'reset': reset}
            elif lim.get('type') == 'TOKENS_LIMIT' and lim.get('unit') == 6:
                out['monthly'] = {'percent': lim.get('percentage'), 'reset': reset}
            elif lim.get('type') == 'TIME_LIMIT':
                out['mcp'] = {'used': lim.get('currentValue'),
                              'total': lim.get('usage'), 'reset': reset}
        out['level'] = d.get('data', {}).get('level')
        return out

    jobs = {'opencode': opencode, 'deepseek': deepseek, 'zhipu': zhipu}
    data, errors = {}, {}
    with cf.ThreadPoolExecutor(3) as ex:
        futs = {ex.submit(fn): name for name, fn in jobs.items()}
        for fu in cf.as_completed(futs):
            name = futs[fu]
            try:
                data[name] = fu.result()
            except Exception as e:
                errors[name] = e.__class__.__name__

    def pct(x):
        return x.get('percent') if isinstance(x, dict) else None
    alerts = []
    oc = data.get('opencode') or {}
    if max([p for p in (pct(oc.get('weekly')), pct(oc.get('monthly'))) if p is not None] or [0]) > 85:
        alerts.append('OpenCode 周或月用量超 85%')
    ds = data.get('deepseek') or {}
    try:
        if float(ds.get('amount') or 999) < 10:
            alerts.append('DeepSeek 余额低于 ¥10')
    except Exception:
        pass
    zp = data.get('zhipu') or {}
    if max([p for p in (pct(zp.get('five_hour')), pct(zp.get('monthly'))) if p is not None] or [0]) > 85:
        alerts.append('智谱任一窗口超 85%')

    return {'accounts': data, 'errors': errors, 'alerts': alerts,
            'collected_at': now()}


class Handler(BaseHTTPRequestHandler):
    server_version = 'NotesSite/1.0'

    def _json(self, code, obj, extra=None):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _ip(self):
        return self.headers.get('X-Real-IP') or self.client_address[0]

    def _body(self, max_size=65536):
        n = int(self.headers.get('Content-Length') or 0)
        if n > max_size:
            raise ValueError('body_too_large')
        return json.loads(self.rfile.read(n) or b'{}')

    def _auth(self, c):
        h = self.headers.get('Authorization', '')
        tok = h[7:].strip() if h.startswith('Bearer ') else ''
        if not tok:
            return None, 'no_token'
        row = c.execute('SELECT t.*,u.username,u.role FROM tokens t JOIN users u ON u.id=t.user_id'
                        ' WHERE t.token=? AND t.revoked=0 AND t.expires_at>?', (tok, int(time.time()))).fetchone()
        return (row if row else None), (None if row else 'invalid_or_expired')

    def do_GET(self):
        c = db()
        p = self.path.split('?')[0]
        if p == '/api/health':
            n = c.execute('SELECT COUNT(*) n FROM users').fetchone()['n']
            ll = c.execute('SELECT COUNT(*) n FROM login_log').fetchone()['n']
            return self._json(200, {'ok': True, 'users': n, 'login_events': ll,
                                    'zip_exists': VAULT_ZIP.exists(),
                                    'zip_bytes': VAULT_ZIP.stat().st_size if VAULT_ZIP.exists() else 0})
        if p == '/api/vault/info':
            import datetime as _dt
            upd = None
            if VAULT_ZIP.exists():
                upd = _dt.datetime.fromtimestamp(VAULT_ZIP.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
            try:
                _c = db()
                _logins = [dict(r) for r in _c.execute(
                    'SELECT ts,username,ip,ok,reason FROM login_log ORDER BY id DESC LIMIT 10')]
                _users = _c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
                _c.close()
            except Exception:
                _logins, _users = [], 0
            return self._json(200, {
                'file': VAULT_ZIP.name,
                'bytes': VAULT_ZIP.stat().st_size if VAULT_ZIP.exists() else 0,
                'sha256': VAULT_SHA.read_text().split()[0] if VAULT_SHA.exists() else None,
                'updated': upd,
                'users': _users,
                'logins': _logins})
        user, err = self._auth(c)
        if p == '/api/minis/snapshots' or p.startswith('/api/minis/snapshots/'):
            if not user:
                return self._json(401, {'error': err})
            if p == '/api/minis/snapshots':
                items = minis_snapshots().list(user['user_id'])
                items = [item for item in items if (minis_snapshots().root / str(user['user_id']) / (item['sha256'] + '.minisbak')).is_file()]
                return self._json(200, {'ok': True, 'items': items, 'total': len(items)})
            if p.endswith('/manifest'):
                msha = p.removeprefix('/api/minis/snapshots/').removesuffix('/manifest')
                try:
                    entries = snapshot_manifest(user['user_id'], msha)
                except (InvalidBackup, FileNotFoundError):
                    return self._json(404, {'error': 'snapshot_not_found'})
                return self._json(200, {'ok': True, 'base': msha, 'entries': entries})
            sha = p.removeprefix('/api/minis/snapshots/')
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                file = minis_snapshots().path(user['user_id'], sha)
            except (InvalidBackup, FileNotFoundError):
                return self._json(404, {'error': 'snapshot_not_found'})
            try:
                c.execute('INSERT INTO downloads(ts,user_id,username,ip,what,bytes) VALUES(?,?,?,?,?,?)',
                    (now(), user['user_id'], user['username'], self._ip(), 'minis_snapshot', file.stat().st_size))
                c.commit()
            except Exception:
                pass
            from minis_archive import ArchiveManager, policy
            if not ArchiveManager(minis_snapshots(), policy()).reserve_download(file.stat().st_size):
                return self._json(429, {'error': 'download_budget_exhausted', 'message': '近期下载额度已用完，请从NAS完整备份恢复'})
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(file.stat().st_size))
            self.send_header('Content-Disposition', 'attachment; filename="' + sha + '.minisbak"')
            self.send_header('X-Content-SHA256', sha)
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            with file.open('rb') as src:
                while chunk := src.read(65536):
                    self.wfile.write(chunk)
            return
        if p == '/api/vault/download':
            if not user:
                return self._json(401, {'error': err})
            if not VAULT_ZIP.exists():
                return self._json(404, {'error': 'vault_missing'})
            c.execute('INSERT INTO downloads(ts,user_id,username,ip,what,bytes) VALUES(?,?,?,?,?,?)',
                      (now(), user['user_id'], user['username'], self._ip(), 'vault_zip', VAULT_ZIP.stat().st_size))
            c.commit()
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Length', str(VAULT_ZIP.stat().st_size))
            self.send_header('Content-Disposition', f'attachment; filename="{VAULT_ZIP.name}"')
            self.end_headers()
            with open(VAULT_ZIP, 'rb') as f:
                while chunk := f.read(1 << 20):
                    self.wfile.write(chunk)
            return
        if p == '/api/me':
            if not user:
                return self._json(401, {'error': err})
            logs = c.execute('SELECT ts,ip,ok,reason FROM login_log WHERE username=? '
                             'ORDER BY id DESC LIMIT 10', (user['username'],)).fetchall()
            dls = c.execute('SELECT ts,what,bytes FROM downloads WHERE username=? '
                            'ORDER BY id DESC LIMIT 10', (user['username'],)).fetchall()
            return self._json(200, {'username': user['username'], 'user_id': user['user_id'], 'role': user['role'],
                                    'created_at': c.execute('SELECT created_at FROM users WHERE id=?',
                                                            (user['user_id'],)).fetchone()['created_at'],
                                    'recent_logins': [dict(r) for r in logs],
                                    'recent_downloads': [dict(r) for r in dls]})
        if p == '/api/quota/summary':
            if not user:
                return self._json(401, {'error': err})
            try:
                age = int(time.time()) - QUOTA_CACHE.stat().st_mtime
            except OSError:
                age = 10 ** 9
            if age <= QUOTA_TTL:
                try:
                    return self._json(200, {**json.loads(QUOTA_CACHE.read_text()), 'cached': True})
                except Exception:
                    pass
            data = quota_collect()
            if 'error' in data and 'accounts' not in data:
                return self._json(500, data)
            try:
                QUOTA_CACHE.write_text(json.dumps(data, ensure_ascii=False))
                os.chmod(QUOTA_CACHE, 0o600)
            except Exception:
                pass
            return self._json(200, {**data, 'cached': False})
        # ---- Hub 聚合：大模型额度 / 封禁 / 米家 / 小米健康 / 龙虾 / 密钥 ----
        if p == '/api/hub/dream':
            if not user:
                return self._json(401, {'error': err})
            try:
                day = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('day', [None])[0]
                dbp = __import__('sqlite3').connect('file:/opt/dreaming/dreams.db?mode=ro', uri=True, timeout=10)
                dbp.row_factory = __import__('sqlite3').Row
                if day:
                    rows = dbp.execute('SELECT name,status,progress,updated_at FROM projects WHERE updated_at LIKE ? ORDER BY updated_at DESC', (day+'%',)).fetchall()
                else:
                    rows = dbp.execute('SELECT name,status,progress,updated_at FROM projects ORDER BY updated_at DESC LIMIT 30').fetchall()
                rep = '/opt/dreaming/reports/%s.md' % (day or datetime.now().strftime('%Y-%m-%d'))
                body = None
                if Path(rep).exists():
                    body = Path(rep).read_text()  # 全文不截断
                return self._json(200, {'projects': [dict(r) for r in rows], 'report_md': body})
            except Exception as e:
                return self._json(502, {'error': type(e).__name__})
        if p == '/api/hub/hdreport':
            if not user:
                return self._json(401, {'error': err})
            import glob
            pdfs = sorted(glob.glob('/root/hdreport/pdfs/*.pdf'),
                          key=lambda f: -__import__('os').path.getmtime(f))[:30]
            items = [{'name': __import__('os').path.basename(f),
                      'mtime': int(__import__('os').path.getmtime(f)),
                      'size': __import__('os').path.getsize(f)} for f in pdfs]
            session_file = Path('/root/hdreport/session.json')
            session_age_h = round((time.time() - session_file.stat().st_mtime) / 3600, 1) if session_file.exists() else None
            return self._json(200, {'pdfs': items, 'session_age_hours': session_age_h,
                                    'note': '会话过期时需在 Mac 微信打开海淀小程序刷新'})
        if p == '/api/hub/hdreport/pdf':
            if not user:
                return self._json(401, {'error': err})
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            name = q.get('name', [''])[0]
            if not name or name != os.path.basename(name) or not name.lower().endswith('.pdf'):
                return self._json(400, {'error': 'invalid_pdf_name'})
            file = Path('/root/hdreport/pdfs') / name
            if not file.is_file():
                return self._json(404, {'error': 'pdf_missing'})
            self.send_response(200)
            self.send_header('Content-Type', 'application/pdf')
            self.send_header('Content-Length', str(file.stat().st_size))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            with file.open('rb') as src:
                while chunk := src.read(65536):
                    self.wfile.write(chunk)
            return
        if p == '/api/hub/werss':
            if not user:
                return self._json(401, {'error': err})
            try:
                from werss_client import read_werss
                feeds = read_werss('mps?limit=100')
                arts = read_werss('articles?limit=50')
                return self._json(200, {'feeds': feeds.get('list') or [],
                    'articles': arts.get('list') or [], 'total': arts.get('total', 0),
                    'note': '暂无订阅时，请到公众号网页授权并添加订阅源。'})
            except Exception as e:
                return self._json(502, {'error': 'werss_unavailable', 'detail': type(e).__name__})
        # ---- 设备控制台：状态 / 日志 / 会话镜像 ----
        if p == '/api/hub/console/status':
            if not user:
                return self._json(401, {'error': err})
            r = _ssh106(CONSOLE_STATUS_SCRIPT, timeout=30, max_output=4000)
            if r.get('rc') != 0:
                return self._json(502, {'error': 'ssh_failed', 'detail': (r.get('err') or '')[:400]})
            d = {'services': {}, 'user_services': {}, 'containers': []}
            for ln in (r.get('out') or '').splitlines():
                if '=' not in ln:
                    continue
                k, _, v = ln.partition('=')
                if k.startswith('SVC:'):
                    d['services'][k[4:]] = v.strip()
                elif k.startswith('USVC:'):
                    d['user_services'][k[5:]] = v.strip()
                else:
                    d[k] = v.strip()
            if d.get('DOCKER'):
                d['containers'] = [x for x in str(d['DOCKER']).split(';') if x]
            d.pop('DOCKER', None)
            return self._json(200, d)
        if p == '/api/hub/console/logs':
            if not user:
                return self._json(401, {'error': err})
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            unit = q.get('unit', [''])[0]
            try:
                lines_n = min(int(q.get('lines', ['150'])[0]), 400)
            except ValueError:
                lines_n = 150
            import re as _re
            m = _re.fullmatch(r'(?:user:)?([A-Za-z0-9_.@-]+)', unit)
            if not m:
                return self._json(400, {'error': 'invalid_unit'})
            if unit.startswith('user:'):
                cmd = ('XDG_RUNTIME_DIR=/run/user/0 journalctl --user -u %s -n %d --no-pager '
                       '2>&1 | tail -n %d' % (m.group(1), lines_n, lines_n))
            else:
                cmd = 'journalctl -u %s -n %d --no-pager 2>&1 | tail -n %d' % (m.group(1), lines_n, lines_n)
            r = _ssh106(cmd, timeout=30, max_output=30000)
            return self._json(200, {'unit': unit, 'log': r.get('out', '')})
        if p == '/api/hub/sessions':
            if not user:
                return self._json(401, {'error': err})
            try:
                data = json.loads((SESSION_MIRROR / 'data' / 'index.json').read_text())
            except Exception as e:
                return self._json(502, {'error': 'mirror_unreadable', 'detail': type(e).__name__})
            ss = data.get('sessions', [])
            return self._json(200, {'count': len(ss), 'generated_at': data.get('generated_at'),
                                    'sessions': ss})
        if p == '/api/hub/sessions/detail':
            if not user:
                return self._json(401, {'error': err})
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            sid = q.get('id', [''])[0]
            import re as _re2
            if not _re2.fullmatch(r'[0-9a-fA-F-]{36}', sid):
                return self._json(400, {'error': 'invalid_id'})
            f = SESSION_MIRROR / 'sessions' / (sid + '.json')
            if not f.is_file():
                return self._json(404, {'error': 'session_missing'})
            try:
                d = json.loads(f.read_text())
            except Exception:
                return self._json(502, {'error': 'session_unreadable'})
            msgs = []
            for mm in (d.get('messages') or [])[:400]:
                calls = [{'name': (c.get('name') or ''),
                          'input': (c.get('input') or '')[:300]}
                         for c in (mm.get('calls') or [])[:12]]
                msgs.append({'role': mm.get('role'), 'at': mm.get('at'),
                             'text': (mm.get('text') or '')[:20000], 'calls': calls})
            return self._json(200, {'title': d.get('title'), 'started_at': d.get('started_at'),
                                    'count': d.get('count'), 'messages': msgs})
        if p == '/api/hub/quota':
            if not user:
                return self._json(401, {'error': err})
            try:
                age = int(time.time()) - QUOTA_CACHE.stat().st_mtime
            except OSError:
                age = 10 ** 9
            if age <= QUOTA_TTL:
                try:
                    return self._json(200, {**json.loads(QUOTA_CACHE.read_text()), 'cached': True})
                except Exception:
                    pass
            data = quota_collect()
            try:
                QUOTA_CACHE.write_text(json.dumps(data, ensure_ascii=False))
                os.chmod(QUOTA_CACHE, 0o600)
            except Exception:
                pass
            return self._json(200, {**data, 'cached': False})
        if p == '/api/hub/vb':
            if not user:
                return self._json(401, {'error': err})
            r = _ssh106('/opt/xiaomi-mi/vb_cli.py status 2>&1 | tail -6')
            lines = [l for l in r.get('out', '').splitlines() if l.strip()]
            # 解析 kick 状态：任意一行含"封禁中"视为有封禁（vb_cli 输出形态宽松，原文透传）
            banned = any(('封禁' in l or 'kick' in l.lower()) and '未' not in l for l in lines)
            return self._json(200, {'raw': lines, 'banned_guess': banned})
        if p == '/api/hub/mihome/list':
            if not user:
                return self._json(401, {'error': err})
            return self._json(200, _mihome('/list'))
        if p == '/api/hub/mihome/ui':
            if not user:return self._json(401, {'error':err})
            import device_art
            result=_mihome('/devices')
            if 'error' in result:return self._json(502, {'error':'devices_unavailable'})
            devices=device_art.enrich(result.get('devices',[]))
            for device in devices:
                device['room']=device.get('room') or '未分房间'
            return self._json(200, {'devices':devices, 'homes':result.get('homes',[])})
        if p == '/api/hub/mihome/icons/status':
            if not user:return self._json(401,{'error':err})
            import device_art
            q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            models=q.get('models',[''])[0].split(',')[:8]
            return self._json(200,{'jobs':[dict(device_art.describe(m),model=m) for m in models if len(m)<101]})
        if p.startswith('/api/hub/mihome/icons/') and p.endswith('.png'):
            if not user:return self._json(401, {'error':err})
            import device_art
            file=device_art.image_path(p.rsplit('/',1)[-1][:-4])
            if not file:return self._json(404, {'error':'image_not_ready'})
            body=file.read_bytes();self.send_response(200)
            self.send_header('Content-Type','image/png');self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','private, max-age=86400')
            self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body);return
        if p == '/api/hub/mihome/devices':
            if not user:
                return self._json(401, {'error': err})
            return self._json(200, _mihome('/devices'))
        if p == '/api/hub/mihome/scenes':
            if not user:
                return self._json(401, {'error': err})
            return self._json(200, _mihome('/scenes'))
        if p == '/api/hub/mihome/status':
            if not user:
                return self._json(401, {'error': err})
            _q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            _n = _q.get('name', [''])[0]
            return self._json(200, _mihome('/status?name=' + urllib.parse.quote(_n) + '&did=' + urllib.parse.quote(_q.get('did',[''])[0])))
        if p == '/api/hub/mihealth':
            if not user:
                return self._json(401, {'error': err})
            _q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            kind = _q.get('kind', ['summary'])[0]
            kind = kind if kind in ('summary', 'sleep') else 'summary'
            try:
                _days = int(_q.get('days', ['1'])[0])
            except ValueError:
                _days = 1
            _days = max(1, min(14, _days))
            r = _ssh106('/opt/mihealth_query.sh %s %d 2>/dev/null' % (kind, _days), timeout=40, max_output=262144)
            try:
                return self._json(200, json.loads(r.get('out', '') or '{}'))
            except Exception:
                return self._json(502, {'error': 'mihealth_bad_json', 'raw': r.get('out', '')[:200]})
        if p == '/api/hub/lobster':
            if not user:
                return self._json(401, {'error': err})
            r = _ssh106('XDG_RUNTIME_DIR=/run/user/0 systemctl --user is-active openclaw-gateway; '
                        'XDG_RUNTIME_DIR=/run/user/0 openclaw gateway status 2>/dev/null | head -4')
            active = r.get('out', '').strip().splitlines()
            return self._json(200, {'active': bool(active and active[0].strip() == 'active'),
                                    'detail': active})
        # appkey 预检：存在性 + 是否已通过二次验证（nonce）
        if p == '/api/hub/appkey':
            if not user:
                return self._json(401, {'error': err})
            nonce = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('nonce', [''])[0]
            ent = APPKEY_NONCE.get(nonce)
            if not ent or ent['exp'] < time.time() or ent['user'] != user['username']:
                return self._json(403, {'error': 'second_factor_required'})
            APPKEY_NONCE.pop(nonce, None)
            try:
                key = APP_KEY_FILE.read_text().strip()
            except OSError:
                return self._json(404, {'error': 'no_appkey'})
            return self._json(200, {'appkey': key})
        # Original hub has QR images and text on separate authenticated endpoints.
        if p == '/api/qr/all':
            if not user:
                return self._json(401, {'error': err})
            try:
                session = HubSession(HUB, HUB_CREDS.read_text().strip())
                return self._json(200, collect_qrs(session))
            except Exception as e:
                return self._json(502, {'error': 'hub_unavailable', 'detail': type(e).__name__})
        # ---- 合并版 App：会话同步通道（服务器持 Basic 直写 sessions-upload） ----
        if p == '/api/sync/list':
            if not user:
                return self._json(401, {'error': err})
            items = []
            if SYNC_DIR.exists():
                for f in sorted(SYNC_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[:50]:
                    if f.is_file():
                        items.append({'name': f.name, 'bytes': f.stat().st_size,
                                      'mtime': int(f.stat().st_mtime)})
            return self._json(200, {'items': items})
        if p == '/api/camera/next':
            if not user:
                return self._json(401, {'error': err})
            row = c.execute("SELECT id,spec,origin,created_at FROM camera_tasks"
                            " WHERE status='pending' AND created_at>? ORDER BY id LIMIT 1",
                            (int(time.time()) - CAMERA_TTL,)).fetchone()
            if not row:
                return self._json(200, {'task': None})
            return self._json(200, {'task': dict(row)})
        if p == '/api/camera/result':
            if not user:
                return self._json(401, {'error': err})
            return self._json(405, {'error': 'use_post'})
        return self._json(404, {'error': 'not_found'})

    def do_POST(self):
        c = db()
        p = self.path.split('?')[0]
        ip, ua = self._ip(), self.headers.get('User-Agent', '')
        if p in ('/api/register', '/api/login'):
            try:
                b = self._body()
            except Exception:
                return self._json(400, {'error': 'bad_body'})
            username = str(b.get('username', '')).strip()[:32]
            password = str(b.get('password', ''))
            appkey = str(b.get('appkey', ''))
            if not username or len(password) < 6:
                return self._json(400, {'error': 'username_or_password_weak'})
            gate = APP_KEY_FILE.read_text().strip() if APP_KEY_FILE.exists() else ''
            # appkey gates REGISTRATION only; login is plain credentials.
            if p == '/api/register':
                if not gate or not hmac.compare_digest(appkey, gate):
                    log_login(c, username, ip, ua, 0, 'bad_appkey')
                    return self._json(403, {'error': 'invalid_appkey'})
                dup = c.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
                if dup:
                    log_login(c, username, ip, ua, 0, 'register_duplicate')
                    return self._json(409, {'error': 'username_taken'})
                salt = new_salt()
                c.execute('INSERT INTO users(username,key_hash,salt,role,created_at,note)'
                          ' VALUES(?,?,?,?,?,?)',
                          (username, pbkdf2(password, salt), salt, 'user', now(), 'self-registered'))
                c.commit()
                uid = c.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()['id']
                log_login(c, username, ip, ua, 1, 'registered')
                return self._json(200, {'ok': True, 'token': issue_token(c, uid, ip, ua),
                                        'username': username})
            # login
            exists = c.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
            if not exists:
                log_login(c, username, ip, ua, 0, 'no_such_user')
                return self._json(401, {'error': 'bad_credentials'})
            u = c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            if not hmac.compare_digest(pbkdf2(password, u['salt']), u['key_hash']):
                log_login(c, username, ip, ua, 0, 'wrong_password')
                return self._json(401, {'error': 'bad_credentials'})
            c.execute('UPDATE users SET last_login_at=?, last_login_ip=? WHERE id=?',
                      (now(), ip, u['id']))
            c.commit()
            log_login(c, username, ip, ua, 1, 'login')
            return self._json(200, {'ok': True, 'token': issue_token(c, u['id'], ip, ua),
                                    'username': username, 'role': u['role']})
        # ---- 合并版扩展端点（token 门） ----
        user, autherr = self._auth(c)
        if not user:
            return self._json(401, {'error': autherr})
        if p == '/api/minis/handoff' or p.startswith('/api/minis/providers/'):
            import owner_features
            if owner_features.post(self, p, user):
                return
        if p == '/api/minis/snapshots':
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                length = int(self.headers.get('Content-Length') or 0)
                self.connection.settimeout(120)
                meta = minis_snapshots().put(user['user_id'], self.rfile, length,
                    q.get('name', [''])[0], q.get('device', [''])[0])
                try:
                    _threading.Thread(target=_cos_async_upload, args=(user['user_id'], meta), daemon=True).start()
                except Exception:
                    pass
                return self._json(200, {'ok': True, **meta})
            except InvalidBackup as e:
                return self._json(400, {'error': str(e)})
            except (ValueError, TimeoutError):
                return self._json(400, {'error': 'invalid_or_incomplete_upload'})
        if p == '/api/minis/delta':
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                length = int(self.headers.get('Content-Length') or 0)
                self.connection.settimeout(600)
                meta = merge_delta(user['user_id'], q.get('base', [''])[0], self.rfile, length,
                    q.get('name', [''])[0], q.get('device', [''])[0])
                try:
                    _threading.Thread(target=_cos_async_upload, args=(user['user_id'], meta), daemon=True).start()
                except Exception:
                    pass
                return self._json(200, {'ok': True, **meta})
            except InvalidBackup as e:
                return self._json(400, {'error': str(e)})
            except FileNotFoundError as e:
                return self._json(404, {'error': str(e)})
            except (ValueError, TimeoutError):
                return self._json(400, {'error': 'invalid_or_incomplete_upload'})
        if p == '/api/sync/upload':
            try:
                ln = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                return self._json(400, {'error': 'bad_length'})
            if ln <= 0 or ln > 512 * 1024 * 1024:
                return self._json(400, {'error': 'size_out_of_range'})
            fname = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('name', [''])[0]
            fname = os.path.basename(fname).replace('..', '')
            if not fname.endswith(('.minisbak', '.zip')):
                return self._json(400, {'error': 'bad_extension'})
            SYNC_DIR.mkdir(parents=True, exist_ok=True)
            dest = SYNC_DIR / fname
            tmp = SYNC_DIR / ('.tmp-' + fname)
            got = 0
            with open(tmp, 'wb') as f:
                while got < ln:
                    chunk = self.rfile.read(min(1 << 20, ln - got))
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
            if got != ln:
                tmp.unlink(missing_ok=True)
                return self._json(400, {'error': 'short_body'})
            os.replace(tmp, dest)
            os.chmod(dest, 0o644)
            c.execute('INSERT INTO downloads(ts,user_id,username,ip,what,bytes) VALUES(?,?,?,?,?,?)',
                      (now(), user['user_id'], user['username'], ip, 'sync_upload:' + fname, ln))
            c.commit()
            return self._json(200, {'ok': True, 'name': fname, 'bytes': got})
        if p == '/api/camera/result':
            try:
                b = self._body(max_size=22 * 1024 * 1024)
            except Exception:
                return self._json(400, {'error': 'bad_body'})
            tid = int(b.get('id') or 0)
            data_url = str(b.get('image', ''))
            row = c.execute("SELECT * FROM camera_tasks WHERE id=? AND status='pending'",
                            (tid,)).fetchone()
            if not row:
                return self._json(404, {'error': 'no_task'})
            import base64
            try:
                payload = base64.b64decode(data_url.split(',', 1)[1], validate=False)
            except Exception:
                return self._json(400, {'error': 'bad_image'})
            if len(payload) > 15 * 1024 * 1024:
                return self._json(413, {'error': 'too_large'})
            CAMERA_DIR.mkdir(parents=True, exist_ok=True)
            fn = CAMERA_DIR / f'task{tid}.jpg'
            fn.write_bytes(payload)
            sha = hashlib.sha256(payload).hexdigest()
            c.execute("UPDATE camera_tasks SET status='done',result_file=?,result_sha=? WHERE id=?",
                      (str(fn), sha, tid))
            c.commit()
            return self._json(200, {'ok': True, 'task': tid, 'sha256': sha,
                                    'file': str(fn)})
        # appkey 二次验证：密码（生物/图案在端上完成后带端侧凭据标记亦可，密码为最终兜底）
        if p == '/api/hub/appkey/challenge':
            if not user:
                return self._json(401, {'error': err})
            try:
                b = self._body()
            except Exception:
                return self._json(400, {'error': 'bad_body'})
            mode = str(b.get('mode', ''))
            if mode == 'password':
                u = c.execute('SELECT * FROM users WHERE username=?',
                              (user['username'],)).fetchone()
                if not u or not hmac.compare_digest(pbkdf2(str(b.get('password', '')), u['salt']),
                                                    u['key_hash']):
                    log_login(c, user['username'], ip, ua, 0, 'appkey_challenge_failed')
                    return self._json(401, {'error': 'bad_credentials'})
            elif mode == 'device':   # 端上生物/图案已通过（本地锁同强度）
                pass
            else:
                return self._json(400, {'error': 'bad_mode'})
            nonce = secrets.token_urlsafe(16)
            APPKEY_NONCE[nonce] = {'exp': time.time() + 120, 'user': user['username']}
            return self._json(200, {'nonce': nonce, 'ttl': 120})
        # 米家两步控制：confirm -> cmd
        if p in ('/api/hub/mihome/icons/ensure','/api/hub/mihome/ui/preferences'):
            import device_art
            try:b=self._body()
            except Exception:return self._json(400,{'error':'bad_body'})
            inventory=_mihome('/devices').get('devices',[])
            if p.endswith('/ensure'):
                models=b.get('models',[])
                if not isinstance(models,list) or any(not isinstance(m,str) for m in models):return self._json(400,{'error':'invalid_models'})
                jobs=device_art.ensure(models,{d.get('model'):d.get('product_type','') for d in inventory})
                return self._json(202,{'jobs':jobs})
            did=str(b.get('did',''))
            if did not in {str(d.get('did')) for d in inventory}:return self._json(404,{'error':'device_not_found'})
            pref=device_art.save_preference(user['user_id'],did,b)
            return self._json(200,{'ok':True,'preference':pref})
        if p == '/api/hub/mihome/confirm':
            if not user:
                return self._json(401, {'error': err})
            try:
                b = self._body()
            except Exception:
                return self._json(400, {'error': 'bad_body'})
            _q = {'kind': b.get('kind', 'set'), 'name': b.get('name', ''),
                  'prop': b.get('prop', 'on'), 'value': json.dumps(b.get('value', '')),
                  'action': b.get('action', ''), 'scene_id': b.get('scene_id', ''), 'did':b.get('did','')}
            r = _mihome('/confirm?' + urllib.parse.urlencode(_q))
            return self._json(200 if 'token' in r else 502, r)
        if p == '/api/hub/mihome/cmd':
            if not user:
                return self._json(401, {'error': err})
            try:
                b = self._body()
            except Exception:
                return self._json(400, {'error': 'bad_body'})
            r = _mihome('/cmd?' + urllib.parse.urlencode({'token': b.get('token', '')}))
            ok = isinstance(r, dict) and (r.get('ok') is True or r.get('code') == 0)
            if ok:r['ok']=True
            return self._json(200 if ok else 502, r)
        if p == '/api/hub/console/exec':
            if not user:
                return self._json(401, {'error': err})
            try:
                b = self._body()
            except Exception:
                return self._json(400, {'error': 'bad_body'})
            cmd = str(b.get('cmd', '')).strip()
            if not cmd or len(cmd) > 500:
                return self._json(400, {'error': 'invalid_cmd'})
            if any(ch in cmd for ch in (';', '|', '&', '`', '$', '>', '<', '\n', '\r')):
                return self._json(400, {'error': 'metachar_not_allowed'})
            if cmd == 'status':
                cmd = CONSOLE_STATUS_SCRIPT
            elif not cmd.startswith(CONSOLE_ALLOW):
                return self._json(403, {'error': 'cmd_not_allowed'})
            r = _ssh106(cmd, timeout=60, max_output=16000)
            return self._json(200, {'rc': r.get('rc'), 'out': r.get('out', ''), 'err': r.get('err', '')})
        if p == '/api/hub/console/power':
            if not user:
                return self._json(401, {'error': err})
            try:
                b = self._body()
            except Exception:
                return self._json(400, {'error': 'bad_body'})
            mode = str(b.get('mode', ''))
            if mode not in ('on', 'off'):
                return self._json(400, {'error': 'invalid_mode'})
            r = _ssh106('/usr/local/bin/power-profile %s low' % mode, timeout=150, max_output=4000)
            ok = r.get('rc') == 0
            return self._json(200 if ok else 502,
                              {'ok': ok, 'detail': ((r.get('out') or '') + (r.get('err') or ''))[-1500:]})
        if p == '/api/hub/lobster/restart':
            if not user:
                return self._json(401, {'error': err})
            r = _ssh106('XDG_RUNTIME_DIR=/run/user/0 systemctl --user restart openclaw-gateway && '
                        'sleep 3 && XDG_RUNTIME_DIR=/run/user/0 systemctl --user is-active openclaw-gateway',
                        timeout=45)
            ok = 'active' in r.get('out', '')
            return self._json(200 if ok else 502, {'ok': ok, 'detail': r.get('out', '')})
        return self._json(404, {'error': 'not_found'})


if __name__ == '__main__':
    c = db()
    c.executescript(SCHEMA)
    c.commit()
    if not APP_KEY_FILE.exists():
        APP_KEY_FILE.write_text(secrets.token_urlsafe(24))
        os.chmod(APP_KEY_FILE, 0o600)
    addr = ('127.0.0.1', int(os.environ.get('NOTES_PORT', '8320')))
    print('listening', addr, flush=True)
    ThreadingHTTPServer(addr, Handler).serve_forever()
