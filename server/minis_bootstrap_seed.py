import fcntl
import json
import math
from pathlib import Path
import sys
import tarfile
import time

from minis_archive import ArchiveManager, policy
from minis_nas_exchange import Slice
from minis_store import SnapshotStore


def create(requested):
    archive = ArchiveManager(SnapshotStore(Path('/opt/notes-site/minis-store')), policy())
    with (archive.root / 'worker.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        index = json.loads((archive.root / 'inventory.json').read_text())['chunks']
        if len(requested) != len(set(requested)) or any(identifier not in index for identifier in requested):
            raise ValueError('invalid_seed_request')
        temporary = archive.root / 'bootstrap.pending.tar'
        try:
            with tarfile.open(temporary, 'w') as output:
                for identifier in requested:
                    chunk = index[identifier]
                    member = tarfile.TarInfo(identifier)
                    member.size = chunk['size']
                    with open(chunk['path'], 'rb') as source:
                        source.seek(chunk['offset'])
                        output.addfile(member, Slice(source, chunk['size']))
            size = temporary.stat().st_size
            if sum(archive.objects().values()) + size > archive.settings['max_cos_bytes']:
                raise RuntimeError('bootstrap_exceeds_cos_capacity')
            digest = archive.digest_file(temporary)
            key = 'minis-store/_bootstrap/' + digest + '.tar'
            archive.reserve_requests(5 * (math.ceil(size / (8 * 1024 * 1024)) + 10))
            archive.cos().upload_file(Bucket=archive.settings['bucket'], Key=key, LocalFilePath=str(temporary), PartSize=8, MAXThread=2, Metadata={'sha256': digest})
            archive.reserve_requests(1)
            if int(archive.cos().head_object(Bucket=archive.settings['bucket'], Key=key)['Content-Length']) != size:
                raise RuntimeError('bootstrap_size_mismatch')
            from qcloud_cos import CosConfig, CosS3Client
            credentials = json.loads(Path('/root/.tccli/default.credential').read_text())
            public = CosS3Client(CosConfig(Region=archive.settings['region'], SecretId=credentials['secretId'], SecretKey=credentials['secretKey']))
            result = {'key': key, 'bytes': size, 'sha256': digest,
                      'url': public.get_presigned_url(Method='GET', Bucket=archive.settings['bucket'], Key=key, Expired=7200)}
            (archive.root / 'bootstrap-created.json').write_text(json.dumps({'key': key, 'bytes': size, 'sha256': digest, 'created_at': int(time.time())}))
            return result
        finally:
            temporary.unlink(missing_ok=True)


def cleanup():
    archive = ArchiveManager(SnapshotStore(Path('/opt/notes-site/minis-store')), policy())
    record = json.loads((archive.root / 'bootstrap-created.json').read_text())
    with archive.store._db() as connection:
        snapshots = connection.execute('SELECT user_id,sha256,bytes FROM snapshots').fetchall()
    with archive.db() as connection:
        for snapshot in snapshots:
            receipt = connection.execute('SELECT bytes,verified_at FROM nas_receipts WHERE uid=? AND sha=?', (snapshot['user_id'], snapshot['sha256'])).fetchone()
            if not receipt or receipt['bytes'] != snapshot['bytes'] or receipt['verified_at'] < time.time() - 86400:
                raise RuntimeError('complete_nas_proof_required')
    if record['key'] != 'minis-store/_bootstrap/' + record['sha256'] + '.tar':
        raise ValueError('invalid_owned_seed')
    archive.reserve_requests(1)
    archive.cos().delete_object(Bucket=archive.settings['bucket'], Key=record['key'])
    record['deleted_after_nas_proof_at'] = int(time.time())
    (archive.root / 'bootstrap-created.json').write_text(json.dumps(record))
    return {'deleted_owned_bootstrap': record['key']}


if __name__ == '__main__':
    print(json.dumps(create(json.load(sys.stdin)) if sys.argv[1] == 'create' else cleanup()))
