import base64
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
import shutil
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

import smbclient

ROOT = Path.home() / 'Library/Application Support/MinisBackup'
CACHE = ROOT / 'chunks'
NAS = '\\\\192.168.1.4\\公共空间\\Minis同步'
DESTINATION = NAS + '\\服务器完整快照'
SMB_OPTIONS = {'username': 'guest', 'password': '', 'require_signing': False, 'encrypt': False}
SSH = ['ssh', '-C', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3', 'wo_fuwuqi', 'python3', '/opt/notes-site/minis_nas_exchange.py']


def log(message):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), message, flush=True)


def chunk_path(identifier):
    if len(identifier) != 64 or any(character not in '0123456789abcdef' for character in identifier):
        raise ValueError('invalid_chunk_digest')
    directory = CACHE / identifier[:2]
    directory.mkdir(parents=True, exist_ok=True)
    return directory / identifier


def store_chunk(source, identifier, length):
    path = chunk_path(identifier)
    if path.exists() and path.stat().st_size == length:
        return
    temporary = path.with_suffix('.part')
    remaining = length
    digest = hashlib.sha256()
    with temporary.open('wb') as output:
        while remaining:
            block = source.read(min(1024 * 1024, remaining))
            if not block:
                raise RuntimeError('short_chunk')
            remaining -= len(block)
            digest.update(block)
            output.write(block)
    if digest.hexdigest() != identifier:
        temporary.unlink(missing_ok=True)
        raise RuntimeError('chunk_digest_mismatch')
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def seed_from_nas(chunks):
    path = NAS + '\\Minis-Merged-Pixel3-Xiaomi17Ultra-20261017.minisbak'
    if not smbclient.path.exists(path, **SMB_OPTIONS):
        return
    log('读取现有NAS完整备份，复用相同数据块')
    with smbclient.open_file(path, 'rb', buffering=1024 * 1024, **SMB_OPTIONS) as source, zipfile.ZipFile(source) as package:
        for entry in package.infolist():
            source.seek(entry.header_offset)
            header = source.read(30)
            name_length, extra_length = struct.unpack_from('<HH', header, 26)
            source.seek(entry.header_offset + 30 + name_length + extra_length)
            remaining = entry.compress_size
            digest = hashlib.sha256()
            temporary = ROOT / 'seed.part'
            with temporary.open('wb') as output:
                while remaining:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        raise RuntimeError('short_nas_seed')
                    remaining -= len(block)
                    digest.update(block)
                    output.write(block)
            identifier = digest.hexdigest()
            if chunks is None or identifier in chunks:
                destination = chunk_path(identifier)
                if not destination.exists():
                    temporary.replace(destination)
            temporary.unlink(missing_ok=True)


def fetch_chunks(index):
    chunks = index['chunks']
    missing = [identifier for identifier, chunk in chunks.items() if not chunk_path(identifier).exists()]
    if shutil.disk_usage(ROOT).free < sum(chunks[identifier]['size'] for identifier in missing) + 1024 ** 3:
        raise RuntimeError('local_cache_disk_insufficient; originals preserved')
    cloud = [identifier for identifier in missing if 'cos_key' in chunks[identifier] and chunks[identifier]['size'] > 128 * 1024]
    ledger_path = ROOT / 'seed-budget.json'
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {'reserved_bytes': 0}
    amount = sum(chunks[identifier]['size'] for identifier in cloud)
    if ledger['reserved_bytes'] + amount > 2 * 1024 ** 3:
        cloud = []
        amount = 0
    ledger['reserved_bytes'] += amount
    ledger_path.write_text(json.dumps(ledger))
    log('缺少数据块=%d，COS一次性种子字节=%d，种子总限额2GiB' % (len(missing), amount))

    def fetch(identifier):
        chunk = chunks[identifier]
        request = urllib.request.Request(index['seed_urls'][chunk['cos_key']], headers={
            'Range': 'bytes=%d-%d' % (chunk['offset'], chunk['offset'] + chunk['size'] - 1)})
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.status != 206 or int(response.headers['Content-Length']) != chunk['size']:
                raise RuntimeError('cos_range_not_honored')
            store_chunk(response, identifier, chunk['size'])

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, cloud))
    requested = [identifier for identifier in missing if not chunk_path(identifier).exists()]
    seed_path = ROOT / 'bootstrap.json'
    if requested and '--bootstrap' in sys.argv and seed_path.exists():
        seed = json.loads(seed_path.read_text())
        if ledger['reserved_bytes'] + seed['bytes'] <= 2 * 1024 ** 3:
            ledger['reserved_bytes'] += seed['bytes']
            ledger_path.write_text(json.dumps(ledger))
            log('通过已有桶下载一次性去重种子，字节=%d' % seed['bytes'])
            with urllib.request.urlopen(seed['url'], timeout=120) as response:
                if int(response.headers['Content-Length']) != seed['bytes']:
                    raise RuntimeError('bootstrap_size_mismatch')
                with tarfile.open(fileobj=response, mode='r|') as archive:
                    for member in archive:
                        if member.name not in chunks or member.size != chunks[member.name]['size']:
                            raise RuntimeError('unexpected_bootstrap_chunk')
                        store_chunk(archive.extractfile(member), member.name, member.size)
            seed_path.unlink()
            requested = [identifier for identifier in missing if not chunk_path(identifier).exists()]
    if requested:
        log('从已有服务器传输剩余去重数据，字节=%d' % sum(chunks[identifier]['size'] for identifier in requested))
        with subprocess.Popen(SSH + ['bundle'], stdin=subprocess.PIPE, stdout=subprocess.PIPE) as process:
            process.stdin.write(json.dumps(requested).encode())
            process.stdin.close()
            received = set()
            with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
                for member in archive:
                    if member.name not in requested or member.size != chunks[member.name]['size']:
                        raise RuntimeError('unexpected_chunk')
                    store_chunk(archive.extractfile(member), member.name, member.size)
                    received.add(member.name)
            if process.wait() != 0 or received != set(requested):
                raise RuntimeError('incomplete_chunk_bundle')


def backup(snapshot):
    uid, sha, size = snapshot['uid'], snapshot['sha256'], snapshot['bytes']
    directory = DESTINATION + '\\user' + uid
    smbclient.makedirs(directory, exist_ok=True, **SMB_OPTIONS)
    path = directory + '\\' + sha + '.minisbak'
    receipt = ROOT / ('receipt-' + uid + '-' + sha + '.json')
    previous = json.loads(receipt.read_text()) if receipt.exists() else None
    if previous and smbclient.path.exists(path, **SMB_OPTIONS):
        stat = smbclient.stat(path, **SMB_OPTIONS)
        if stat.st_size == size and stat.st_mtime_ns == previous.get('nas_mtime_ns'):
            previous['verified_at'] = int(time.time())
            return previous
    if smbclient.path.exists(path, **SMB_OPTIONS):
        digest = hashlib.sha256()
        with smbclient.open_file(path, 'rb', buffering=4 * 1024 * 1024, **SMB_OPTIONS) as source:
            for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != sha:
            raise RuntimeError('existing_nas_file_digest_mismatch; original preserved')
    else:
        if snapshot.get('nas_only'):
            raise RuntimeError('NAS-only historical snapshot missing; automatic deletion blocked')
        temporary = path + '.uploading'
        digest = hashlib.sha256()
        with smbclient.open_file(temporary, 'wb', buffering=4 * 1024 * 1024, **SMB_OPTIONS) as output:
            for segment in snapshot['recipe']:
                if 'literal' in segment:
                    block = base64.b64decode(segment['literal'])
                    output.write(block)
                    digest.update(block)
                else:
                    with chunk_path(segment['chunk']).open('rb') as source:
                        for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
                            output.write(block)
                            digest.update(block)
        if digest.hexdigest() != sha or smbclient.stat(temporary, **SMB_OPTIONS).st_size != size:
            raise RuntimeError('nas_reconstruction_mismatch')
        digest = hashlib.sha256()
        with smbclient.open_file(temporary, 'rb', buffering=4 * 1024 * 1024, **SMB_OPTIONS) as source:
            for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != sha:
            raise RuntimeError('nas_full_readback_digest_mismatch')
        smbclient.rename(temporary, path, **SMB_OPTIONS)
    result = {'uid': uid, 'sha256': sha, 'bytes': size, 'path': path, 'verified_at': int(time.time()), 'nas_mtime_ns': smbclient.stat(path, **SMB_OPTIONS).st_mtime_ns}
    receipt.write_text(json.dumps(result, ensure_ascii=False))
    log('NAS完整备份已回读校验 user=%s sha=%s bytes=%d' % (uid, sha[:12], size))
    return result


def required_chunks(index):
    required = set()
    for snapshot in index['snapshots']:
        path = DESTINATION + '\\user' + snapshot['uid'] + '\\' + snapshot['sha256'] + '.minisbak'
        if not smbclient.path.exists(path, **SMB_OPTIONS):
            required.update(segment['chunk'] for segment in snapshot['recipe'] if 'chunk' in segment)
    return {identifier: chunk for identifier, chunk in index['chunks'].items() if identifier in required}


def trim_cache(snapshots):
    counts = {}
    keep = set()
    for snapshot in reversed(snapshots):
        uid = snapshot['uid']
        counts[uid] = counts.get(uid, 0) + 1
        if counts[uid] <= 3:
            keep.update(segment['chunk'] for segment in snapshot['recipe'] if 'chunk' in segment)
    for path in CACHE.glob('*/*'):
        if path.is_file() and len(path.name) == 64 and path.name not in keep:
            path.unlink()


def main():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (ROOT / 'agent.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        smbclient.ClientConfig(username='guest', password='', require_secure_negotiate=False)
        smbclient.register_session('192.168.1.4', username='guest', password='', require_signing=False, encrypt=False, connection_timeout=10)
        log('读取服务器不可变快照清单')
        command = 'cached-inventory' if '--cached-inventory' in sys.argv else 'inventory'
        process = subprocess.run(SSH + [command], capture_output=True, check=True, timeout=900)
        index = json.loads(process.stdout)
        log('快照=%d，去重压缩块字节=%d' % (len(index['snapshots']), sum(chunk['size'] for chunk in index['chunks'].values())))
        pending = dict(index, chunks=required_chunks(index))
        if any(not chunk_path(identifier).exists() for identifier in pending['chunks']):
            seed_from_nas(pending['chunks'])
            fetch_chunks(pending)
        receipts = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            for receipt in pool.map(backup, index['snapshots']):
                subprocess.run(SSH + ['ack'], input=json.dumps([receipt]).encode(), capture_output=True, check=True, timeout=60)
                receipts.append(receipt)
        receipts = [backup(snapshot) for snapshot in index['snapshots']]
        subprocess.run(SSH + ['ack'], input=json.dumps(receipts).encode(), capture_output=True, check=True, timeout=60)
        trim_cache(index['snapshots'])
        status = {'completed_at': int(time.time()), 'verified_snapshots': len(receipts), 'verified_bytes': sum(receipt['bytes'] for receipt in receipts)}
        temporary = ROOT / 'last-run.pending.json'
        temporary.write_text(json.dumps(status))
        temporary.replace(ROOT / 'last-run.json')
        log('NAS归档完成 ' + json.dumps(status))


if __name__ == '__main__':
    main()
