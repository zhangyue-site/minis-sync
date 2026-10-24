"""Chat-only legacy import view of a verified native backup; master stays intact."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
import zipfile
from minis_store import InvalidBackup, MAX_UPLOAD, MAX_EXPANDED, validate_package
from minis_merge import _json, _records, _canonical, _dump, MAX_LINE

_CHAT = re.compile(r'data/(sessions|messages|folders|compact_markers)(?:-[^/]+)?\.jsonl\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _safe(name):
    p = PurePosixPath(name)
    return (bool(name) and not p.is_absolute() and '..' not in p.parts
            and '\\' not in name and str(p) == name
            and not any(ord(c) < 32 for c in name))


def _lines(z, info):
    with z.open(info) as source:
        while raw := source.readline(MAX_LINE + 1):
            if len(raw) > MAX_LINE:
                raise InvalidBackup('record_too_large')
            if not raw.strip():
                continue
            row = _json(raw)
            if not isinstance(row, dict):
                raise InvalidBackup('invalid_record')
            yield raw, row


def project_chats(source, destination):
    """Return {sha256, bytes, path, metadata}; publish only a validated new ZIP.

    The caller owns the immutable source/master and registers this shared view
    once per master head. No device identifiers are added to the projection.
    """
    source, destination = Path(source), Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    if source.resolve() == destination.resolve():
        raise InvalidBackup('source_destination_overlap')
    if source.stat().st_size > MAX_UPLOAD:
        raise InvalidBackup('backup_too_large')
    with tempfile.TemporaryDirectory(prefix='.minis-chat-', dir=destination.parent) as directory:
        work = Path(directory)
        with closing(sqlite3.connect(work / 'index.sqlite')) as db, zipfile.ZipFile(source) as original:
            db.execute('PRAGMA cache_size=-2048')
            db.execute('PRAGMA temp_store=FILE')
            db.execute('CREATE TABLE records(path TEXT,k TEXT,PRIMARY KEY(path,k)) WITHOUT ROWID')
            db.execute('CREATE TABLE refs(sha TEXT PRIMARY KEY, indexed INTEGER DEFAULT 0) WITHOUT ROWID')
            db.execute('CREATE TABLE files(k TEXT PRIMARY KEY) WITHOUT ROWID')
            db.execute('CREATE TABLE blobs(sha TEXT PRIMARY KEY) WITHOUT ROWID')
            infos = original.infolist()
            if len(infos) > 100000 or sum(i.file_size for i in infos) > MAX_EXPANDED:
                raise InvalidBackup('backup_too_large')
            names = [i.filename for i in infos]
            if len(names) != len(set(names)):
                raise InvalidBackup('duplicate_archive_member')
            manifests = [n for n in names if n == 'manifest.json' or n.endswith('/manifest.json')]
            if len(manifests) != 1:
                raise InvalidBackup('manifest_required')
            member = manifests[0]
            if original.getinfo(member).file_size > MAX_LINE:
                raise InvalidBackup('manifest_too_large')
            manifest = _json(original.read(member))
            if not isinstance(manifest, dict) or not isinstance(manifest.get('categories'), dict):
                raise InvalidBackup('bad_manifest')
            if manifest.get('encryption'):
                raise InvalidBackup('encrypted_projection_unsupported')
            if 'chats' not in manifest['categories']:
                raise InvalidBackup('chats_category_required')
            prefix = member[:-len('manifest.json')]
            members = {}
            for info in infos:
                name = info.filename
                if not _safe(name.rstrip('/') if info.is_dir() else name) or info.external_attr >> 16 & 0o170000 == 0o120000:
                    raise InvalidBackup('unsafe_archive_path')
                if not name.startswith(prefix):
                    raise InvalidBackup('mixed_archive_roots')
                relative = name[len(prefix):]
                if info.is_dir() or relative == 'manifest.json':
                    continue
                members[relative] = info
            integrity = manifest.get('integrity')
            if not isinstance(integrity, dict) or set(integrity) != set(members):
                raise InvalidBackup('incomplete_integrity')
            # Even excluded content is CRC/hash checked, with bounded buffers.
            for name, info in members.items():
                expected = integrity[name]
                if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', expected):
                    raise InvalidBackup('bad_integrity_entry')
                digest = hashlib.sha256()
                with original.open(info) as stream:
                    while chunk := stream.read(1024 * 1024): digest.update(chunk)
                if digest.hexdigest() != expected.lower():
                    raise InvalidBackup('integrity_mismatch')
            output = work / 'projection.zip'
            output_integrity = {}
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as target:
                def emit(name, chunks):
                    digest = hashlib.sha256()
                    with target.open(prefix + name, 'w', force_zip64=True) as stream:
                        for chunk in chunks:
                            digest.update(chunk)
                            stream.write(chunk)
                    output_integrity[name] = digest.hexdigest()

                for name, info in sorted(members.items()):
                    if not _CHAT.fullmatch(name): continue
                    canonical = _canonical(name)
                    def chat_chunks(info=info, canonical=canonical):
                        for raw, row in _lines(original, info):
                            for key in _records(raw, canonical, None):
                                try: db.execute('INSERT INTO records VALUES(?,?)', (canonical, _dump(key)))
                                except sqlite3.IntegrityError as error: raise InvalidBackup('duplicate_record_id') from error
                            yield raw if raw.endswith(b'\n') else raw + b'\n'
                    emit(name, chat_chunks())

                if 'files.index.jsonl' in members:
                    def file_chunks():
                        for raw, row in _lines(original, members['files.index.jsonl']):
                            if row.get('category') != 'chats': continue
                            path = row.get('path')
                            if not isinstance(path, str) or not _safe(path):
                                raise InvalidBackup('bad_file_path')
                            try: db.execute('INSERT INTO files VALUES(?)', (path,))
                            except sqlite3.IntegrityError as error: raise InvalidBackup('duplicate_record_id') from error
                            sha = row.get('sha256')
                            if sha is not None:
                                if not isinstance(sha, str) or not _SHA.fullmatch(sha): raise InvalidBackup('bad_blob_hash')
                                db.execute('INSERT OR IGNORE INTO refs(sha) VALUES(?)', (sha,))
                            yield raw if raw.endswith(b'\n') else raw + b'\n'
                    emit('files.index.jsonl', file_chunks())

                if 'blobs.index.jsonl' in members:
                    def blob_index_chunks():
                        for raw, row in _lines(original, members['blobs.index.jsonl']):
                            _records(raw, 'blobs.index.jsonl', None)
                            sha = row['sha256']
                            try: db.execute('INSERT INTO blobs VALUES(?)', (sha,))
                            except sqlite3.IntegrityError as error: raise InvalidBackup('duplicate_record_id') from error
                            if not db.execute('SELECT 1 FROM refs WHERE sha=?', (sha,)).fetchone(): continue
                            expected_path = 'blobs/' + sha[:2] + '/' + sha
                            info = members.get(expected_path)
                            if row['path'] != expected_path or info is None or row['size'] != info.file_size or integrity[expected_path].lower() != sha:
                                raise InvalidBackup('blob_index_reference_mismatch')
                            db.execute('UPDATE refs SET indexed=1 WHERE sha=?', (sha,))
                            yield raw if raw.endswith(b'\n') else raw + b'\n'
                    emit('blobs.index.jsonl', blob_index_chunks())
                if db.execute('SELECT 1 FROM refs WHERE indexed=0 LIMIT 1').fetchone():
                    raise InvalidBackup('missing_blob_index_reference')
                for (sha,) in db.execute('SELECT sha FROM refs ORDER BY sha'):
                    name = 'blobs/' + sha[:2] + '/' + sha
                    with original.open(members[name]) as stream:
                        emit(name, iter(lambda: stream.read(1024 * 1024), b''))
                # Only native format/version descriptors are allowed through;
                # arbitrary master metadata can itself contain private config.
                projected = {key: manifest[key] for key in ('format', 'version', 'format_version', 'backup_id', 'created_at', 'createdAt', 'timestamp', 'snapshot_at', 'created_at_ms', 'app', 'device_name') if key in manifest}
                projected['categories'] = {'chats': manifest['categories']['chats']}
                projected['integrity'] = output_integrity
                target.writestr(prefix + 'manifest.json', json.dumps(projected, ensure_ascii=False, separators=(',', ':')).encode())
            metadata = validate_package(output)
            digest = hashlib.sha256()
            with output.open('rb') as stream:
                while chunk := stream.read(1024 * 1024): digest.update(chunk)
                os.fsync(stream.fileno())
            size = output.stat().st_size
            os.link(output, destination)
            return {'sha256': digest.hexdigest(), 'bytes': size, 'path': str(destination), 'metadata': metadata}
