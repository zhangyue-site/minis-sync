import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'server' / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rpc, owner, providers = load('handoff_rpc'), load('owner_features'), load('provider_rpc')


class Fake:
    def __init__(self):
        self.task = {'id': '123456789012345678901234', 'content': 'Keep original user notes.', 'title': 'existing', 'priority': 5}
        self.created = 0

    def get_task(self, pid, tid):
        return dict(self.task)

    def get_project_data(self, pid):
        return {'tasks': [dict(self.task)]}

    def update_task(self, tid, pid, data):
        self.task.update(data)

    def create_task(self, data):
        self.created += 1
        self.task = dict(data, id='abcdefabcdefabcdefabcdef')
        return dict(self.task)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        rpc.STATE = Path(self.tmp.name)
        owner.CONFIG = Path(self.tmp.name) / 'owner.json'
        self.client = Fake()

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, **kw):
        return dict(key='fixture', task_id=self.client.task['id'], **kw)

    def test_update_preserves_user_content_and_fields_and_reuses_block(self):
        for content in ['First checkpoint', 'Updated checkpoint']:
            old = rpc.handle(self.request(operation='read'), self.client)
            result = rpc.handle(self.request(operation='checkpoint', base_hash=old['base_hash'], content=content), self.client)
            self.assertTrue(result['verified'])
        self.assertIn('Keep original user notes.', self.client.task['content'])
        self.assertEqual(1, self.client.task['content'].count('[minis-checkpoint:fixture]'))
        self.assertNotIn('First checkpoint', self.client.task['content'])
        self.assertEqual(5, self.client.task['priority'])
        self.assertEqual('existing', self.client.task['title'])

    def test_stale_hash_rejects_overwrite(self):
        before = rpc.handle(self.request(operation='read'), self.client)
        self.client.task['content'] += ' fresh edit'
        out = rpc.handle(self.request(operation='checkpoint', base_hash=before['base_hash'], content='replace'), self.client)
        self.assertFalse(out['ok'])
        self.assertIn('fresh edit', self.client.task['content'])

    def test_marker_injection_and_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            rpc.handle({'key': '../outside', 'operation': 'read'}, self.client)
        digest = hashlib.sha256(self.client.task['content'].encode()).hexdigest()
        with self.assertRaises(ValueError):
            rpc.handle(self.request(operation='checkpoint', base_hash=digest, content='[minis-checkpoint:fixture]'), self.client)

    def test_creation_is_idempotent(self):
        req = {'operation': 'read', 'key': 'fresh'}
        old = rpc.handle(req, self.client)
        update = dict(req, operation='checkpoint', base_hash=old['base_hash'], title='New project', content='one')
        rpc.handle(update, self.client)
        new = rpc.handle(req, self.client)
        rpc.handle(dict(update, base_hash=new['base_hash'], content='two'), self.client)
        self.assertEqual(1, self.client.created)

    def test_owner_config_fails_closed(self):
        self.assertFalse(owner.owner_allowed({'user_id': 5}))
        owner.CONFIG.write_text(json.dumps({'owner_user_ids': [5]}))
        self.assertTrue(owner.owner_allowed({'user_id': 5}))
        self.assertFalse(owner.owner_allowed({'user_id': 4}))

    def test_free_route_never_uses_paid_model(self):
        with self.assertRaisesRegex(ValueError, 'free_model_required_no_paid_fallback'):
            providers.run({'channel': 'opencode-free', 'body': {'model': 'glm-5.3'}})

    def test_output_limit_is_bounded(self):
        with self.assertRaisesRegex(ValueError, 'output_limit_exceeded'):
            providers.run({'channel': 'opencode', 'body': {'model': 'deepseek-v4-flash', 'max_tokens': 1000000}})


if __name__ == '__main__':
    unittest.main()
