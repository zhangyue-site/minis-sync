"""Bounded model relay; model credentials stay on 106, never in the APK."""
import ast
import json
from pathlib import Path
import sys
import urllib.request
import urllib.error

ROUTES = {
    'zcode': ('http://127.0.0.1:9388/v1/chat/completions', '/opt/zcode-gw/zcode_gateway.py', {'glm-5.3', 'glm-5.3-flash'}),
    'opencode': ('http://127.0.0.1:9387/v1/chat/completions', '/opt/life-integration/deals/oc_gateway.py', {'deepseek-v4-flash', 'deepseek-v4-pro', 'glm-5.3', 'glm-5.3-flash'}),
}
FREE = {'glm-5.3-flash-free', 'deepseek-v4.1-flash-free', 'space-bunny-free', 'longcat-2.5-preview-free'}


def gateway_key(path):
    for node in ast.parse(Path(path).read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ('AUTH_KEY', 'GW_KEY') for t in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                return value
    raise RuntimeError('gateway_key_unavailable')


def run(data):
    channel, body = data.get('channel'), data.get('body')
    if not isinstance(body, dict):
        raise ValueError('invalid_request')
    requested = body.get('max_tokens', 8192)
    if not isinstance(requested, int) or not 1 <= requested <= 8192:
        raise ValueError('output_limit_exceeded')
    if channel == 'opencode-free':
        if body.get('model') not in FREE:
            raise ValueError('free_model_required_no_paid_fallback')
        config = json.loads(Path('/root/.openclaw/openclaw.json').read_text())
        provider = config.get('models', {}).get('providers', {}).get('opencode-zen')
        if not provider or not provider.get('apiKey'):
            raise ValueError('official_free_account_not_configured')
        url, key = 'https://opencode.ai/inference/openai/v1/chat/completions', provider['apiKey']
    else:
        if channel not in ROUTES:
            raise ValueError('unsupported_channel')
        url, filename, allowed = ROUTES[channel]
        if body.get('model') not in allowed:
            raise ValueError('model_not_enabled')
        key = gateway_key(filename)
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                 headers={'Authorization': 'Bearer ' + key, 'X-GW-Key': key, 'Content-Type': 'application/json'})
    try:
        response = urllib.request.urlopen(req, timeout=150)
    except urllib.error.HTTPError as exc:
        # Error body may be useful but never forward tokens or internal headers.
        raw = exc.read(8192)
        print(json.dumps({'status': exc.code, 'content_type': 'application/json'}), flush=True)
        sys.stdout.buffer.write(raw)
        return
    with response:
        print(json.dumps({'status': response.status, 'content_type': response.headers.get('Content-Type', 'application/json')}), flush=True)
        while True:
            chunk = response.read1(16384)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()


if __name__ == '__main__':
    try:
        raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('request_too_large')
        run(json.loads(raw))
    except Exception as exc:
        print(json.dumps({'status': 503, 'content_type': 'application/json'}), flush=True)
        print(json.dumps({'error': str(exc) if isinstance(exc, ValueError) else type(exc).__name__, 'paid_fallback': False}))
