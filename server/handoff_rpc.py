"""Private stdin RPC on 106; no token or arbitrary command accepted in input."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys

PROJECT = '6acc2909e4b090d7735c638c'
STATE = Path('/root/.local/state/dev-handoff/minis-rpc')


def handle(data, client):
    key = data.get('key', '')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', key):
        raise ValueError('invalid_task_key')
    operation = data.get('operation')
    if operation not in ('read', 'checkpoint'):
        raise ValueError('unsupported_operation')
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / (key + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        binding = STATE / (key + '.json')
        tid = data.get('task_id') or (json.loads(binding.read_text()).get('id') if binding.exists() else None)
        if tid and not re.fullmatch(r'[a-f0-9]{24}', tid):
            raise ValueError('invalid_task_id')
        task = client.get_task(PROJECT, tid) if tid else None
        if task is None:
            marker = '[minis-checkpoint:' + key + ']'
            matches = [t for t in client.get_project_data(PROJECT).get('tasks', []) if marker in t.get('content', '')]
            if len(matches) > 1:
                raise ValueError('ambiguous_task')
            task = matches[0] if matches else None
        old = task.get('content', '') if task else ''
        digest = hashlib.sha256(old.encode()).hexdigest()
        if operation == 'read':
            return {'ok': True, 'task_id': task['id'] if task else None, 'base_hash': digest, 'content': old}
        if data.get('base_hash') != digest:
            return {'ok': False, 'error': 'conflict_read_latest', 'task_id': task['id'] if task else None}
        content = data.get('content')
        if not isinstance(content, str) or not content.strip() or len(content.encode()) > 48000:
            raise ValueError('invalid_checkpoint')
        start, end = '[minis-checkpoint:' + key + ']', '[/minis-checkpoint:' + key + ']'
        if start in content or end in content:
            raise ValueError('reserved_marker')
        block = start + '\n' + content + '\n' + end
        if start in old:
            if old.count(start) != 1 or old.count(end) != 1:
                raise ValueError('invalid_existing_marker')
            a, z = old.index(start), old.index(end) + len(end)
            if z < a:
                raise ValueError('invalid_existing_marker')
            new = old[:a] + block + old[z:]
        else:
            new = block + ('\n\n——原有记录（保留）——\n' + old if old else '')
        if task:
            # Recheck immediately before writing; this API has no atomic compare-and-swap.
            if client.get_task(PROJECT, task['id']).get('content', '') != old:
                return {'ok': False, 'error': 'conflict_read_latest'}
            client.update_task(task['id'], PROJECT, {'content': new})
            tid = task['id']
        else:
            title = data.get('title')
            if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
                raise ValueError('title_required')
            attempt = STATE / (key + '.creation.json')
            if attempt.exists():
                raise ValueError('creation_outcome_requires_reconciliation')
            attempt.write_text(json.dumps({'status': 'sending'}))
            attempt.chmod(0o600)
            tid = client.create_task({'projectId': PROJECT, 'title': title, 'content': new})['id']
            attempt.write_text(json.dumps({'status': 'sent', 'id': tid}))
        verified = client.get_task(PROJECT, tid)
        if verified.get('content') != new:
            raise ValueError('readback_mismatch')
        temp = binding.with_suffix('.tmp')
        temp.write_text(json.dumps({'id': tid}))
        temp.chmod(0o600)
        temp.replace(binding)
        return {'ok': True, 'task_id': tid, 'verified': True, 'content_hash': hashlib.sha256(new.encode()).hexdigest()}


def main():
    os.umask(0o077)
    sys.path.insert(0, '/root/.openclaw/workspace/skills/dida365-skill')
    from auth import load_env_file
    from client import Dida365Client
    from cache import invalidate_project_cache
    load_env_file()
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError('request_too_large')
        result = handle(json.loads(raw), Dida365Client())
        invalidate_project_cache(PROJECT)
    except Exception as exc:
        # Do not emit upstream exception text: it may contain private payloads.
        result = {'ok': False, 'error': str(exc) if isinstance(exc, ValueError) else type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
