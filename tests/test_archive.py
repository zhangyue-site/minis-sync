import base64
import hashlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_delta import backup
from minis_archive import ArchiveManager, GIB
from minis_exact_zip import describe
from minis_store import InvalidBackup, SnapshotStore


class FakeBody:
    def __init__(self, content):
        self.content = content

    def get_raw_stream(self):
        return io.BytesIO(self.content)


class FakeCos:
    def __init__(self):
        self.data = {}
        self.deleted = []
        self.fail = False
        self.corrupt = False

    def list_objects(self, **arguments):
        return {'Contents': [{'Key': key, 'Size': len(value)} for key, value in self.data.items()]}

    def upload_file(self, **arguments):
        if self.fail:
            raise RuntimeError('simulated_network_failure')
        self.data[arguments['Key']] = Path(arguments['LocalFilePath']).read_bytes()

    def head_object(self, **arguments):
        return {'Content-Length': len(self.data[arguments['Key']])}

    def get_object(self, **arguments):
        value = self.data[arguments['Key']]
        return {'Body': FakeBody(value + b'corrupt' if self.corrupt else value)}

    def delete_object(self, **arguments):
        self.deleted.append(arguments['Key'])
        del self.data[arguments['Key']]


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.store = SnapshotStore(root / 'store')
        self.client = FakeCos()
        self.manager = ArchiveManager(self.store, {'state_root': str(root / 'state'), 'recent_per_account': 1, 'lighthouse_instance_id': None}, self.client)
        self.snapshots = []
        for number in range(3):
            data = backup({'data/chats.jsonl': str(number).encode()})
            meta = self.store.put('1', io.BytesIO(data), len(data), 'test.minisbak', 'test')
            self.snapshots.append(meta)
            with self.store._db() as connection:
                connection.execute('UPDATE snapshots SET created_at=? WHERE sha256=?', (number, meta['sha256']))

    def receipt(self, meta):
        self.manager.receipt('1', meta['sha256'], meta['bytes'], '\\\\192.168.1.4\\公共空间\\Minis同步\\服务器完整快照\\user1\\' + meta['sha256'] + '.minisbak', int(time.time()))

    def test_full_readback_and_no_deletion_without_nas_proof(self):
        result = self.manager.run()
        self.assertEqual(len(result['uploaded_verified']), 3)
        self.assertEqual(self.client.deleted, [])
        self.assertEqual(len(self.client.data), 3)

    def test_only_old_verified_nas_copies_are_pruned(self):
        self.manager.run()
        for meta in self.snapshots:
            self.receipt(meta)
        unknown = 'minis-store/unknown.minisbak'
        self.client.data[unknown] = b'preserve'
        result = self.manager.run()
        self.assertEqual(len(result['deleted_after_nas_proof']), 2)
        self.assertIn(self.manager.key('1', self.snapshots[-1]['sha256']), self.client.data)
        self.assertIn(unknown, self.client.data)
        self.assertTrue(all(self.store.path('1', meta['sha256']).exists() for meta in self.snapshots))

    def test_corrupt_local_copy_blocks_pruning(self):
        self.manager.run()
        meta = self.snapshots[0]
        self.receipt(meta)
        self.store.path('1', meta['sha256']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(RuntimeError, 'local_safety_copy'):
            self.manager.run()
        self.assertEqual(self.client.deleted, [])

    def test_local_cleanup_only_after_nas_and_cos_retention(self):
        self.manager.settings['prune_local_after_nas'] = True
        self.manager.run()
        self.assertTrue(all(self.store.path('1', meta['sha256']).exists() for meta in self.snapshots))
        for meta in self.snapshots:
            self.receipt(meta)
        result = self.manager.run()
        self.assertEqual(len(result['local_archived_to_nas']), 2)
        self.assertTrue(self.store.path('1', self.snapshots[-1]['sha256']).exists())
        for meta in self.snapshots[:-1]:
            with self.assertRaises(FileNotFoundError):
                self.store.path('1', meta['sha256'])
            self.receipt(meta)
        self.assertEqual(self.manager.run()['local_archived_to_nas'], [])

    def test_nas_only_inventory_preserves_historical_records(self):
        from minis_nas_exchange import inventory
        self.manager.settings['prune_local_after_nas'] = True
        self.manager.run()
        for meta in self.snapshots:
            self.receipt(meta)
        self.manager.run()
        with patch('minis_nas_exchange.seed_urls', side_effect=lambda archive, index: index):
            index = inventory(self.manager)
        self.assertEqual(len(index['snapshots']), 3)
        self.assertEqual(sum(snapshot.get('nas_only', False) for snapshot in index['snapshots']), 2)

    def test_expired_nas_proof_does_not_allow_cleanup(self):
        self.manager.run()
        self.receipt(self.snapshots[0])
        with self.manager.db() as connection:
            connection.execute('UPDATE nas_receipts SET verified_at=1')
        self.manager.run()
        self.assertEqual(self.client.deleted, [])

    def test_capacity_wait_preserves_source(self):
        self.manager.settings['max_cos_bytes'] = 1
        self.manager.run()
        self.assertEqual(self.client.data, {})
        with self.manager.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM jobs WHERE status='waiting_capacity'").fetchone()[0], 3)

    def test_network_failure_is_persisted_for_retry(self):
        self.client.fail = True
        self.manager.run()
        with self.manager.db() as connection:
            rows = connection.execute('SELECT * FROM jobs').fetchall()
        self.assertTrue(all(row['status'] == 'retry' and row['attempts'] == 1 and row['last_error'] for row in rows))

    def test_bad_cos_readback_never_marked_verified(self):
        self.client.corrupt = True
        self.manager.run()
        with self.manager.db() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM jobs WHERE verified_at IS NOT NULL').fetchone()[0], 0)

    def test_daily_and_annual_request_limits(self):
        self.manager.settings['daily_request_limit'] = 2
        self.manager.reserve_requests(2)
        with self.assertRaisesRegex(RuntimeError, 'budget'):
            self.manager.reserve_requests(1)
        self.manager.settings['annual_budget_yuan'] = 1
        with self.assertRaisesRegex(RuntimeError, 'budget'):
            self.manager.reserve_requests(1)

    def test_rolling_download_budget(self):
        self.manager.settings['max_server_download_bytes_month'] = 10
        self.assertTrue(self.manager.reserve_download(6))
        self.assertFalse(self.manager.reserve_download(5))
        self.assertTrue(self.manager.reserve_download(4))

    def test_server_traffic_unavailable_or_insufficient_fails_closed(self):
        self.manager.settings['lighthouse_instance_id'] = 'test'
        with patch.object(self.manager, 'traffic_remaining', side_effect=RuntimeError('unavailable')):
            self.assertFalse(self.manager.reserve_download(1))
        with patch.object(self.manager, 'traffic_remaining', return_value=GIB):
            self.assertFalse(self.manager.reserve_download(1))
        with patch.object(self.manager, 'traffic_remaining', return_value=100 * GIB):
            self.assertTrue(self.manager.reserve_download(GIB))

    def test_reconstruction_preserves_exact_zip_bytes(self):
        index = describe([self.store.path('1', meta['sha256']) for meta in self.snapshots])
        for snapshot in index['snapshots']:
            output = io.BytesIO()
            for segment in snapshot['recipe']:
                if 'literal' in segment:
                    output.write(base64.b64decode(segment['literal']))
                else:
                    chunk = index['chunks'][segment['chunk']]
                    with open(chunk['path'], 'rb') as source:
                        source.seek(chunk['offset'])
                        output.write(source.read(chunk['size']))
            self.assertEqual(hashlib.sha256(output.getvalue()).hexdigest(), snapshot['sha256'])

    def test_low_disk_rejects_upload_preserving_existing(self):
        with patch('shutil.disk_usage', return_value=type('Usage', (), {'free': GIB})()):
            with self.assertRaisesRegex(InvalidBackup, 'storage_capacity'):
                self.store.put('1', io.BytesIO(b'x'), 1, 'test.minisbak', 'test')
        self.assertEqual(len(self.store.list('1')), 3)

    def test_bootstrap_capacity_guard_and_cleanup_proof_gate(self):
        import minis_bootstrap_seed as seed
        index = describe([self.store.path('1', meta['sha256']) for meta in self.snapshots])
        (self.manager.root / 'inventory.json').write_text(json.dumps(index))
        with patch.object(seed, 'ArchiveManager', return_value=self.manager), patch.object(seed, 'SnapshotStore', return_value=self.store):
            self.manager.settings['max_cos_bytes'] = 1
            with self.assertRaisesRegex(RuntimeError, 'capacity'):
                seed.create(list(index['chunks'])[:1])
            self.assertFalse((self.manager.root / 'bootstrap.pending.tar').exists())
            digest = 'a' * 64
            key = 'minis-store/_bootstrap/' + digest + '.tar'
            record = {'key': key, 'bytes': 4, 'sha256': digest}
            (self.manager.root / 'bootstrap-created.json').write_text(json.dumps(record))
            self.client.data[key] = b'seed'
            with self.assertRaisesRegex(RuntimeError, 'complete_nas_proof'):
                seed.cleanup()
            self.assertIn(key, self.client.data)
            for meta in self.snapshots:
                self.receipt(meta)
            self.manager.settings['max_cos_bytes'] = 8 * GIB
            seed.cleanup()
            self.assertEqual(self.client.deleted, [key])


if __name__ == '__main__':
    unittest.main(verbosity=2)
