"""Authenticated owner-only endpoints loaded by notes_site.Handler on 124."""
import json
from pathlib import Path
import subprocess
import urllib.parse

SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', 'root@106.52.123.149']
CONFIG = Path('/opt/notes-site/minis-owner-features.json')


def owner_allowed(user):
    try:
        config = json.loads(CONFIG.read_text())
        return int(user['user_id']) in config.get('owner_user_ids', [])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def post(handler, path, user):
    if path != '/api/minis/handoff' and not path.startswith('/api/minis/providers/'):
        return False
    if not owner_allowed(user):
        handler._json(403, {'error': 'owner_account_required'})
        return True
    if path == '/api/minis/handoff':
        try:
            body = handler._body(max_size=65536)
            result = subprocess.run(SSH + ['python3 /opt/minis-fix-20261018/server/handoff_rpc.py'],
                                    input=json.dumps(body), text=True, capture_output=True, timeout=55)
            if result.returncode:
                raise RuntimeError('handoff_transport_failed')
            data = json.loads(result.stdout)
            handler._json(200 if data.get('ok') else 409, data)
        except Exception as exc:
            handler._json(502, {'error': type(exc).__name__, 'message': '结果未知，请先读取当前交接后再决定是否重试'})
        return True
    parts = path.split('/')
    if len(parts) != 8 or parts[4] not in ('zcode', 'opencode', 'opencode-free') or parts[5:] != ['v1', 'chat', 'completions']:
        handler._json(404, {'error': 'unsupported_model_route'})
        return True
    sent_headers = False
    try:
        body = handler._body(max_size=2 * 1024 * 1024)
        process = subprocess.Popen(SSH + ['python3 /opt/minis-fix-20261018/server/provider_rpc.py'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        process.stdin.write(json.dumps({'channel': parts[4], 'body': body}).encode())
        process.stdin.close()
        # First line carries status; following bytes are upstream JSON or SSE.
        meta = json.loads(process.stdout.readline(8192))
        handler.send_response(meta['status'])
        handler.send_header('Content-Type', meta.get('content_type', 'application/json'))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Accel-Buffering', 'no')
        handler.send_header('Connection', 'close')
        handler.end_headers()
        sent_headers = True
        handler.close_connection = True
        try:
            while True:
                chunk = process.stdout.read1(16384)
                if not chunk:
                    break
                handler.wfile.write(chunk)
                handler.wfile.flush()
        finally:
            process.stdout.close()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
    except Exception:
        if not sent_headers:
            handler._json(503, {'error': 'model_transport_unavailable', 'paid_fallback': False})
        handler.close_connection = True
    return True
