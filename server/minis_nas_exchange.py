import base64
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tarfile
import time
import zipfile

from minis_archive import ArchiveManager, cos_internal_client, policy
from minis_store import SnapshotStore


def manager():
    return ArchiveManager(SnapshotStore(Path('/opt/notes-site/minis-store')), policy())


def inventory(archive):
    objects = archive.objects()
    chunks = {}
    snapshots = []
    with archive.store._db() as connection:
        rows = connection.execute('SELECT user_id,sha256,bytes,created_at FROM snapshots ORDER BY created_at,sha256').fetchall()
    for row in rows:
        uid, sha = row['user_id'], row['sha256']
        try:
            path = archive.store.path(uid, sha)
        except FileNotFoundError:
            with archive.db() as connection:
                job = connection.execute('SELECT status FROM jobs WHERE uid=? AND sha=?', (uid, sha)).fetchone()
            if not job or job['status'] != 'nas_only':
                raise
            snapshots.append({'uid': uid, 'sha256': sha, 'bytes': row['bytes'], 'recipe': [], 'nas_only': True})
            continue
        recipe = []
        digest = hashlib.sha256()
        with zipfile.ZipFile(path) as package, path.open('rb') as source:
            position = 0
            for entry in sorted(package.infolist(), key=lambda value: value.header_offset):
                source.seek(entry.header_offset)
                header = source.read(30)
                name_length, extra_length = struct.unpack_from('<HH', header, 26)
                offset = entry.header_offset + 30 + name_length + extra_length
                source.seek(position)
                metadata = source.read(offset - position)
                digest.update(metadata)
                recipe.append({'literal': base64.b64encode(metadata).decode()})
                part_digest = hashlib.sha256()
                remaining = entry.compress_size
                while remaining:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        raise RuntimeError('incomplete_snapshot')
                    remaining -= len(block)
                    digest.update(block)
                    part_digest.update(block)
                chunk_id = part_digest.hexdigest()
                recipe.append({'chunk': chunk_id, 'size': entry.compress_size})
                candidate = {'path': str(path), 'offset': offset, 'size': entry.compress_size}
                key = archive.key(uid, sha)
                if key in objects:
                    candidate['cos_key'] = key
                if chunk_id not in chunks or ('cos_key' in candidate and 'cos_key' not in chunks[chunk_id]):
                    chunks[chunk_id] = candidate
                position = offset + entry.compress_size
            source.seek(position)
            tail = source.read()
            digest.update(tail)
            recipe.append({'literal': base64.b64encode(tail).decode()})
        if digest.hexdigest() != sha:
            raise RuntimeError('snapshot_digest_mismatch ' + sha)
        snapshots.append({'uid': uid, 'sha256': sha, 'bytes': row['bytes'], 'recipe': recipe})
    result = {'snapshots': snapshots, 'chunks': chunks, 'generated_at': int(time.time())}
    temporary = archive.root / 'inventory.pending.json'
    temporary.write_text(json.dumps(result))
    temporary.replace(archive.root / 'inventory.json')
    return seed_urls(archive, result)


def seed_urls(archive, result):
    objects = archive.objects()
    chunks = result['chunks']
    for chunk in chunks.values():
        source = Path(chunk['path'])
        key = archive.key(source.parent.name, source.stem)
        if key in objects:
            chunk['cos_key'] = key
    from qcloud_cos import CosConfig, CosS3Client
    credentials = json.loads(Path('/root/.tccli/default.credential').read_text())
    client = CosS3Client(CosConfig(Region=archive.settings['region'], SecretId=credentials['secretId'], SecretKey=credentials['secretKey']))
    urls = {}
    for chunk in chunks.values():
        key = chunk.get('cos_key')
        if key and key not in urls:
            urls[key] = client.get_presigned_url(Method='GET', Bucket=archive.settings['bucket'], Key=key, Expired=7200)
    result['seed_urls'] = urls
    return result


class Slice:
    def __init__(self, source, remaining):
        self.source = source
        self.remaining = remaining

    def read(self, size=-1):
        size = self.remaining if size < 0 else min(size, self.remaining)
        block = self.source.read(size)
        self.remaining -= len(block)
        return block


def bundle(archive, requested):
    index = json.loads((archive.root / 'inventory.json').read_text())['chunks']
    if not isinstance(requested, list) or len(requested) > 100000:
        raise ValueError('invalid_chunk_request')
    if not archive.reserve_download(sum(index[identifier]['size'] for identifier in requested)):
        raise RuntimeError('server_backup_download_budget_exhausted')
    with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as output:
        for identifier in requested:
            chunk = index[identifier]
            info = tarfile.TarInfo(identifier)
            info.size = chunk['size']
            with Path(chunk['path']).open('rb') as source:
                source.seek(chunk['offset'])
                output.addfile(info, Slice(source, chunk['size']))


if __name__ == '__main__':
    archive = manager()
    command = sys.argv[1]
    if command == 'inventory':
        print(json.dumps(inventory(archive)))
    elif command == 'cached-inventory':
        index = json.loads((archive.root / 'inventory.json').read_text())
        if time.time() - index['generated_at'] > 900:
            raise RuntimeError('inventory_cache_expired')
        print(json.dumps(seed_urls(archive, index)))
    elif command == 'bundle':
        bundle(archive, json.load(sys.stdin))
    elif command == 'ack':
        receipts = json.load(sys.stdin)
        for receipt in receipts:
            archive.receipt(receipt['uid'], receipt['sha256'], receipt['bytes'], receipt['path'], receipt['verified_at'])
        print(json.dumps({'accepted': len(receipts)}))
