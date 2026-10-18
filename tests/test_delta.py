import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from minis_store import SnapshotStore, InvalidBackup, validate_package
from minis_delta import merge_delta_backup


def backup(entries):
    entries = dict(entries)
    entries['manifest.json'] = json.dumps({'categories': {'chats': True}, 'integrity': {
        name: hashlib.sha256(value).hexdigest() for name, value in entries.items()
    }}).encode()
    return archive(entries)


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as package:
        for name, value in entries.items():
            package.writestr(name, value)
    return output.getvalue()


class DeltaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = SnapshotStore(Path(self.temporary.name))
        self.original = backup({'data/chats.jsonl': b'old\n', 'blobs/obsolete': b'delete me', 'blobs/keep': b'unchanged'})
        self.base = self.store.put('1', io.BytesIO(self.original), len(self.original), 'base.minisbak', 'test')['sha256']

    def delta(self, desired, deleted=(), **control):
        with zipfile.ZipFile(io.BytesIO(desired)) as package:
            entries = {name: package.read(name) for name in package.namelist() if name != 'blobs/keep'}
        entries['__delta__.json'] = json.dumps(dict(base=self.base, v=2, deleted=list(deleted), **control)).encode()
        return archive(entries)

    def merge(self, value, uid='1', base=None):
        return merge_delta_backup(self.store, uid, base or self.base, io.BytesIO(value), len(value), 'result.minisbak', 'test')

    def test_add_change_delete_and_restore(self):
        desired = backup({'data/chats.jsonl': b'changed\n', 'blobs/keep': b'unchanged', 'blobs/new': b'new'})
        result = self.merge(self.delta(desired, ['blobs/obsolete']))
        restored = self.store.path('1', result['sha256'])
        validate_package(restored)
        with zipfile.ZipFile(restored) as actual, zipfile.ZipFile(io.BytesIO(desired)) as expected:
            self.assertEqual(set(actual.namelist()), set(expected.namelist()))
            for name in expected.namelist():
                self.assertEqual(actual.read(name), expected.read(name))
        self.assertEqual(result['deleted'], 1)
        self.assertEqual(result['reused'], 1)
        self.assertEqual(result['protocol'], 2)

    def test_invalid_integrity_never_enters_store(self):
        desired = backup({'data/chats.jsonl': b'changed', 'blobs/keep': b'unchanged'})
        delta = self.delta(desired, ['blobs/obsolete'])
        with zipfile.ZipFile(io.BytesIO(delta)) as package:
            entries = {name: package.read(name) for name in package.namelist()}
        entries['data/chats.jsonl'] = b'corrupted'
        with self.assertRaises(InvalidBackup):
            self.merge(archive(entries))
        self.assertEqual(len(self.store.list('1')), 1)

    def test_account_isolation(self):
        with self.assertRaises(FileNotFoundError):
            self.merge(archive({'__delta__.json': b'{}'}), uid='2')

    def test_missing_baseline(self):
        with self.assertRaises(FileNotFoundError):
            self.merge(archive({'__delta__.json': b'{}'}), base='0' * 64)

    def test_base_mismatch(self):
        with self.assertRaises(InvalidBackup):
            self.merge(archive({'__delta__.json': json.dumps({'base': '0' * 64, 'v': 2}).encode()}))

    def test_short_upload(self):
        with self.assertRaises(InvalidBackup):
            merge_delta_backup(self.store, '1', self.base, io.BytesIO(b'zip'), 10, 'x.minisbak', 'test')

    def test_unsafe_path(self):
        with self.assertRaises(InvalidBackup):
            self.merge(archive({'__delta__.json': json.dumps({'base': self.base, 'v': 2, 'deleted': ['../escape']}).encode()}))

    def test_conflicting_deleted_entries(self):
        desired = backup({'data/chats.jsonl': b'changed', 'blobs/keep': b'unchanged'})
        with self.assertRaises(InvalidBackup):
            self.merge(self.delta(desired, ['data/chats.jsonl']))

    def test_duplicate_entries(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as package:
            package.writestr('__delta__.json', '{}')
            package.writestr('__delta__.json', '{}')
        with self.assertRaises(InvalidBackup):
            self.merge(output.getvalue())

    def test_legacy_protocol(self):
        desired = backup({'data/chats.jsonl': b'changed', 'blobs/keep': b'unchanged', 'blobs/obsolete': b'delete me'})
        with zipfile.ZipFile(io.BytesIO(self.delta(desired))) as package:
            entries = {name: package.read(name) for name in package.namelist()}
        entries['__delta__.json'] = json.dumps({'base': self.base, 'v': 1}).encode()
        self.assertEqual(self.merge(archive(entries))['protocol'], 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
