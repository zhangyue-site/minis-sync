import io
import json
import shutil
import tempfile
import zipfile
from pathlib import PurePosixPath

from minis_store import InvalidBackup, MAX_EXPANDED, validate_package

MAX_DELTA = 256 * 1024 * 1024


def merge_delta_backup(store, uid, base_sha, stream, length, name, device):
    uid = store._uid(uid)
    if not isinstance(length, int) or length <= 0 or length > MAX_DELTA:
        raise InvalidBackup('size_out_of_range')
    base_path = store.path(uid, base_sha)
    if shutil.disk_usage(store.root).free < base_path.stat().st_size * 3 + length + 2 * 1024 ** 3:
        raise InvalidBackup('storage_capacity_exhausted_originals_preserved')
    with tempfile.TemporaryFile() as uploaded:
        remaining = length
        while remaining:
            chunk = stream.read(min(remaining, 1024 * 1024))
            if not chunk:
                raise InvalidBackup('invalid_or_incomplete_upload')
            uploaded.write(chunk)
            remaining -= len(chunk)
        uploaded.seek(0)
        try:
            with zipfile.ZipFile(uploaded) as delta, zipfile.ZipFile(base_path) as baseline:
                infos = delta.infolist()
                if len(infos) > 100000 or sum(entry.file_size for entry in infos) > MAX_EXPANDED:
                    raise InvalidBackup('backup_too_large')
                names = [entry.filename for entry in infos]
                if len(names) != len(set(names)):
                    raise InvalidBackup('duplicate_archive_member')
                if '__delta__.json' not in names or delta.getinfo('__delta__.json').file_size > 8 * 1024 * 1024:
                    raise InvalidBackup('not_delta')
                control = json.loads(delta.read('__delta__.json'))
                if not isinstance(control, dict) or control.get('base') != base_sha:
                    raise InvalidBackup('base_mismatch')
                if control.get('v') not in (1, 2):
                    raise InvalidBackup('unsupported_delta_version')
                deleted = control.get('deleted', [])
                if not isinstance(deleted, list) or len(deleted) > 100000 or any(not isinstance(value, str) for value in deleted):
                    raise InvalidBackup('invalid_deleted_entries')
                changed = {entry.filename: entry for entry in infos if not entry.is_dir() and entry.filename != '__delta__.json'}
                for value in names + deleted:
                    path = PurePosixPath(value)
                    if path.is_absolute() or '..' in path.parts or '\\' in value:
                        raise InvalidBackup('unsafe_archive_path')
                removed = set(deleted)
                if removed & changed.keys():
                    raise InvalidBackup('conflicting_delta_entries')
                reused = [entry for entry in baseline.infolist()
                          if not entry.is_dir() and entry.filename not in changed and entry.filename not in removed]
                if len(reused) + len(changed) > 100000 or sum(entry.file_size for entry in reused) + sum(entry.file_size for entry in changed.values()) > MAX_EXPANDED:
                    raise InvalidBackup('backup_too_large')
                with tempfile.NamedTemporaryFile(dir=store.root, suffix='.minisbak') as merged:
                    with zipfile.ZipFile(merged, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as output:
                        for archive, entries in ((baseline, reused), (delta, changed.values())):
                            for entry in entries:
                                with archive.open(entry) as source, output.open(entry.filename, 'w', force_zip64=True) as target:
                                    shutil.copyfileobj(source, target, 1024 * 1024)
                    merged.flush()
                    validate_package(merged.name)
                    merged.seek(0, io.SEEK_END)
                    size = merged.tell()
                    merged.seek(0)
                    meta = store.put(uid, merged, size, name, device)
                extra = {'source': 'delta', 'base': base_sha, 'reused': len(reused), 'added': len(changed), 'deleted': len(removed)}
                with store._db() as connection:
                    row = connection.execute('SELECT metadata FROM snapshots WHERE user_id=? AND sha256=?', (uid, meta['sha256'])).fetchone()
                    metadata = json.loads(row[0])
                    metadata.update(extra)
                    connection.execute('UPDATE snapshots SET metadata=? WHERE user_id=? AND sha256=?', (json.dumps(metadata), uid, meta['sha256']))
                return dict(meta, **extra, delta_bytes=length, protocol=2)
        except InvalidBackup:
            raise
        except (zipfile.BadZipFile, KeyError, ValueError, TypeError, RuntimeError, OSError) as error:
            raise InvalidBackup('invalid_native_backup') from error
