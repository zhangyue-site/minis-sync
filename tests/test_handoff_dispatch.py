"""Exercise the real POST method without importing unrelated production host modules."""
import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class HandoffDispatchTests(unittest.TestCase):
    def setUp(self):
        source = ast.parse((Path(__file__).resolve().parents[1] / 'server/notes_site.py').read_text())
        handler = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'Handler')
        method = next(n for n in handler.body if isinstance(n, ast.FunctionDef) and n.name == 'do_POST')
        namespace = {'db': lambda: object()}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<real Handler.do_POST>', 'exec'), namespace)
        self.post = namespace['do_POST']
        self.owner = SimpleNamespace(post=Mock(side_effect=self.reply))

    @staticmethod
    def reply(handler, path, user):
        handler._json(200, {'verified': True})
        return True

    def request(self, path, authenticated=True):
        response = Mock()
        h = SimpleNamespace(path=path, headers={}, _ip=lambda: 'test', _json=response,
                            _auth=lambda c: ({'user_id': 1}, None) if authenticated else (None, 'invalid_token'))
        with patch.dict(sys.modules, {'owner_features': self.owner}):
            self.post(h)
        return h, response

    def test_authenticated_handoff_reaches_owner_dispatch(self):
        h, response = self.request('/api/minis/handoff')
        self.owner.post.assert_called_once_with(h, '/api/minis/handoff', {'user_id': 1})
        response.assert_called_once_with(200, {'verified': True})

    def test_provider_route_keeps_owner_gate(self):
        self.request('/api/minis/providers/zcode/v1/chat/completions')
        self.owner.post.assert_called_once()

    def test_unauthenticated_request_stops_before_owner_dispatch(self):
        _, response = self.request('/api/minis/handoff', authenticated=False)
        self.owner.post.assert_not_called()
        response.assert_called_once_with(401, {'error': 'invalid_token'})


if __name__ == '__main__':
    unittest.main()
