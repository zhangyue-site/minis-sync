import base64
import hashlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import minis_nas_agent as agent


class FakeNas:
    def __init__(self, root):
        self.root = root
        self.corrupt_read = False
        self.path = SimpleNamespace(exists=lambda path, **arguments: self.local(path).exists())

    def local(self, path):
        return self.root / path.replace('\\', '/')

    def makedirs(self, path, **arguments):
        self.local(path).mkdir(parents=True, exist_ok=arguments.get('exist_ok', False))

    def open_file(self, path, mode, **arguments):
        if mode == 'rb' and self.corrupt_read:
            return io.BytesIO(b'corrupt')
        return self.local(path).open(mode)

    def stat(self, path, **arguments):
        return self.local(path).stat()

    def rename(self, source, destination, **arguments):
        self.local(source).rename(self.local(destination))


class NasAgentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.nas = FakeNas(root / 'nas')
        self.patches = [patch.object(agent, 'ROOT', root), patch.object(agent, 'CACHE', root / 'chunks'),
                        patch.object(agent, 'DESTINATION', 'NAS'), patch.object(agent, 'smbclient', self.nas)]
        for replacement in self.patches:
            replacement.start()
            self.addCleanup(replacement.stop)
        self.content = b'exact-original-backup-bytes'
        self.sha = hashlib.sha256(self.content).hexdigest()
        self.snapshot = {'uid': '1', 'sha256': self.sha, 'bytes': len(self.content),
                         'recipe': [{'literal': base64.b64encode(self.content).decode()}]}
        self.path = 'NAS\\user1\\' + self.sha + '.minisbak'

    def test_exact_write_full_readback_and_idempotent_receipt(self):
        receipt = agent.backup(self.snapshot)
        self.assertEqual(self.nas.local(self.path).read_bytes(), self.content)
        self.assertEqual(receipt['sha256'], self.sha)
        self.assertEqual(agent.backup(self.snapshot)['bytes'], len(self.content))

    def test_readback_corruption_prevents_final_rename(self):
        self.nas.corrupt_read = True
        with self.assertRaisesRegex(RuntimeError, 'readback_digest'):
            agent.backup(self.snapshot)
        self.assertFalse(self.nas.local(self.path).exists())

    def test_existing_different_file_is_never_overwritten(self):
        destination = self.nas.local(self.path)
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b'preserve-existing')
        with self.assertRaisesRegex(RuntimeError, 'original preserved'):
            agent.backup(self.snapshot)
        self.assertEqual(destination.read_bytes(), b'preserve-existing')

    def test_wrong_reconstruction_does_not_publish(self):
        self.snapshot['sha256'] = 'a' * 64
        with self.assertRaisesRegex(RuntimeError, 'reconstruction_mismatch'):
            agent.backup(self.snapshot)
        self.assertFalse(self.nas.local('NAS\\user1\\' + 'a' * 64 + '.minisbak').exists())

    def test_missing_nas_only_history_is_not_replaced_by_empty_file(self):
        self.snapshot['nas_only'] = True
        self.snapshot['recipe'] = []
        with self.assertRaisesRegex(RuntimeError, 'historical snapshot missing'):
            agent.backup(self.snapshot)
        self.assertFalse(self.nas.local(self.path).exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
