import importlib.util
from pathlib import Path
import unittest
import urllib.error
import urllib.request

spec = importlib.util.spec_from_file_location('verify_native_clients', Path(__file__).resolve().parents[1] / 'scripts/verify_native_clients.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TokenBridgeTests(unittest.TestCase):
    def test_wrong_path_cannot_consume_and_correct_path_is_one_shot(self):
        with module.token_server(b'isolated-test-token') as (port, path):
            base = 'http://127.0.0.1:' + str(port)
            with self.assertRaises(urllib.error.HTTPError) as wrong:
                urllib.request.urlopen(base + '/wrong')
            self.assertEqual(404, wrong.exception.code)
            with urllib.request.urlopen(base + path) as response:
                self.assertEqual(b'isolated-test-token', response.read())
                self.assertEqual('no-store', response.headers['Cache-Control'])
            with self.assertRaises(urllib.error.HTTPError) as repeat:
                urllib.request.urlopen(base + path)
            self.assertEqual(404, repeat.exception.code)
        with self.assertRaises(urllib.error.URLError):
            urllib.request.urlopen(base + path, timeout=1)

    def test_invalid_payload_is_rejected(self):
        for token in (b'', b'x' * 513):
            with self.assertRaises(ValueError):
                with module.token_server(token):
                    self.fail('invalid token accepted')


if __name__ == '__main__':
    unittest.main()
