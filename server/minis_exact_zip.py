import base64
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import zipfile


def describe(paths):
    chunks = {}
    snapshots = []
    for path in paths:
        recipe = []
        digest = hashlib.sha256()
        with path.open('rb') as source, zipfile.ZipFile(path) as package:
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
                block_hash = hashlib.sha256()
                remaining = entry.compress_size
                while remaining:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        raise RuntimeError('short_source')
                    digest.update(block)
                    block_hash.update(block)
                    remaining -= len(block)
                identifier = block_hash.hexdigest()
                chunks[identifier] = {'path': str(path), 'offset': offset, 'size': entry.compress_size}
                recipe.append({'chunk': identifier})
                position = offset + entry.compress_size
            source.seek(position)
            tail = source.read()
            digest.update(tail)
            recipe.append({'literal': base64.b64encode(tail).decode()})
        if digest.hexdigest() != path.stem:
            raise RuntimeError('source_digest_mismatch ' + path.name)
        snapshots.append({'sha256': path.stem, 'bytes': path.stat().st_size, 'recipe': recipe})
    return {'snapshots': snapshots, 'chunks': chunks}


def restore(root):
    local = describe(list(root.glob('*/*.minisbak')))
    process = subprocess.run(['ssh', 'root@106.52.123.149', 'python3', '/opt/minis-fix-20261018/minis_exact_zip.py', 'describe'], capture_output=True, check=True)
    remote = json.loads(process.stdout)
    print('indexed local and mirror', len(local['chunks']), len(remote['chunks']), flush=True)
    import sqlite3
    connection = sqlite3.connect(root / 'snapshots.db')
    rows = {sha: (str(uid), size) for uid, sha, size in connection.execute('SELECT user_id,sha256,bytes FROM snapshots')}
    missing = [snapshot for snapshot in remote['snapshots'] if snapshot['sha256'] in rows and not (root / rows[snapshot['sha256']][0] / (snapshot['sha256'] + '.minisbak')).exists()]
    wanted = set(segment['chunk'] for snapshot in missing for segment in snapshot['recipe'] if 'chunk' in segment)
    transfer = {identifier: remote['chunks'][identifier] for identifier in wanted if identifier not in local['chunks']}
    cache = root.parent / 'minis-archive/repair-chunks'
    cache.mkdir(parents=True, exist_ok=True)
    print('missing snapshots', len(missing), 'transfer bytes', sum(chunk['size'] for chunk in transfer.values()), flush=True)
    with subprocess.Popen(['ssh', 'root@106.52.123.149', 'python3', '/opt/minis-fix-20261018/minis_exact_zip.py', 'stream'], stdin=subprocess.PIPE, stdout=subprocess.PIPE) as process:
        process.stdin.write(json.dumps(transfer).encode())
        process.stdin.close()
        for identifier, chunk in transfer.items():
            digest = hashlib.sha256()
            remaining = chunk['size']
            path = cache / identifier
            with path.open('wb') as output:
                while remaining:
                    block = process.stdout.read(min(1024 * 1024, remaining))
                    if not block:
                        raise RuntimeError('short_transfer')
                    output.write(block)
                    digest.update(block)
                    remaining -= len(block)
            if digest.hexdigest() != identifier:
                raise RuntimeError('transfer_digest_mismatch')
            local['chunks'][identifier] = {'path': str(path), 'offset': 0, 'size': chunk['size']}
        if process.wait() != 0:
            raise RuntimeError('mirror_stream_failed')
    for snapshot in missing:
        uid, size = rows[snapshot['sha256']]
        destination = root / uid / (snapshot['sha256'] + '.minisbak')
        temporary = destination.with_suffix('.restoring')
        digest = hashlib.sha256()
        with temporary.open('wb') as output:
            for segment in snapshot['recipe']:
                if 'literal' in segment:
                    block = base64.b64decode(segment['literal'])
                    output.write(block)
                    digest.update(block)
                    continue
                chunk = local['chunks'][segment['chunk']]
                with open(chunk['path'], 'rb') as source:
                    source.seek(chunk['offset'])
                    remaining = chunk['size']
                    while remaining:
                        block = source.read(min(1024 * 1024, remaining))
                        if not block:
                            raise RuntimeError('short_local_chunk')
                        output.write(block)
                        digest.update(block)
                        remaining -= len(block)
        if digest.hexdigest() != snapshot['sha256'] or temporary.stat().st_size != size:
            raise RuntimeError('reconstructed_digest_mismatch')
        temporary.chmod(0o600)
        temporary.replace(destination)
        print('restored byte-identical', uid, snapshot['sha256'], size, flush=True)


if __name__ == '__main__':
    if sys.argv[1] == 'describe':
        print(json.dumps(describe(list(Path('/root/lobster-vault/minisbak-124').glob('*.minisbak')))))
    elif sys.argv[1] == 'stream':
        for chunk in json.load(sys.stdin).values():
            path = Path(chunk['path'])
            if path.parent != Path('/root/lobster-vault/minisbak-124'):
                raise ValueError('invalid_source')
            with path.open('rb') as source:
                source.seek(chunk['offset'])
                remaining = chunk['size']
                while remaining:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        raise RuntimeError('short_mirror_source')
                    sys.stdout.buffer.write(block)
                    remaining -= len(block)
    elif sys.argv[1] == 'restore':
        restore(Path('/opt/notes-site/minis-store'))
