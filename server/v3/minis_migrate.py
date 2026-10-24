"""Prepare an isolated v3 staging store from latest legacy packages, never activate it.

Read-only legacy SQLite access requires an offline/checkpointed database: a live
WAL or rollback journal blocks preparation rather than risking a stale selection.
"""
import argparse
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from minis_cloud import CloudStore
from minis_merge import merge_backups
from minis_store import InvalidBackup,MAX_UPLOAD

MARKER='.migration-state.json'
REPORT='migration-report.json'

def _write(path,value):
    raw=json.dumps(value,sort_keys=True,indent=2).encode()+b'\n'
    fd,tmp=tempfile.mkstemp(prefix='.report-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:Path(tmp).unlink(missing_ok=True)

def _digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while block:=f.read(1024*1024):h.update(block)
    return h.hexdigest()

def _latest(root):
    database=root/'snapshots.db'
    if not database.is_file():raise InvalidBackup('legacy_database_missing')
    for suffix in ('-wal','-journal'):
        sidecar=Path(str(database)+suffix)
        if sidecar.exists() and sidecar.stat().st_size:raise InvalidBackup('legacy_database_requires_offline_checkpoint')
    # immutable avoids SQLite creating/updating source journal or shared-memory files.
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro&immutable=1',uri=True)) as c:
        c.row_factory=sqlite3.Row
        rows=c.execute('SELECT user_id,sha256,bytes,created_at FROM snapshots ORDER BY user_id,created_at,sha256').fetchall()
    selected={}
    for row in rows:
        if not re.fullmatch('[0-9]+',row['user_id']) or not re.fullmatch('[a-f0-9]{64}',row['sha256']):raise InvalidBackup('invalid_legacy_index')
        selected[row['user_id']]=dict(row)
    if not selected:raise InvalidBackup('legacy_database_empty')
    return list(selected.values())

def migrate(legacy_root,staging_root):
    legacy=Path(legacy_root).resolve();stage=Path(staging_root).resolve()
    if legacy==stage or legacy in stage.parents or stage in legacy.parents:raise ValueError('legacy_and_staging_must_be_separate')
    if stage.exists() and (not stage.is_dir() or not (stage/MARKER).is_file()):raise ValueError('staging_must_be_new_or_migration_owned')
    stage.mkdir(parents=True,exist_ok=True,mode=0o700)
    marker=stage/MARKER
    try:
        fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:json.dump({'format':1},f)
    except FileExistsError:pass
    with (stage/'.migration.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        report={'format':1,'status':'blocked','activation':'manual_review_required','accounts':[]}
        state=json.loads(marker.read_text())
        if state.get('format')!=1:raise ValueError('unrecognized_migration_state')
        try:latest=_latest(legacy)
        except (InvalidBackup,sqlite3.Error) as e:
            report['error']=str(e) if isinstance(e,InvalidBackup) else 'invalid_legacy_database'
            _write(stage/REPORT,report);return report
        selection=hashlib.sha256(json.dumps(latest,sort_keys=True).encode()).hexdigest()
        if state.get('selection') and state['selection']!=selection:
            report['error']='legacy_latest_selection_changed_new_staging_required';_write(stage/REPORT,report);return report
        state['selection']=selection;_write(marker,state)
        # Validate every latest package before creating any staging head. Never fall back.
        with tempfile.TemporaryDirectory(prefix='minis-migration-') as tmp:
            for row in latest:
                uid=row['user_id'];sha=row['sha256'];size=row['bytes']
                item={'uid':uid,'source_sha256':sha,'source_bytes':size}
                report['accounts'].append(item)
                source=legacy/uid/(sha+'.minisbak')
                try:
                    if not source.is_file():raise InvalidBackup('latest_package_missing_or_cold')
                    if source.is_symlink() or legacy not in source.resolve().parents:raise InvalidBackup('unsafe_legacy_package')
                    if type(size) is not int or size<=0 or size>MAX_UPLOAD or source.stat().st_size!=size:raise InvalidBackup('latest_package_size_mismatch')
                    if _digest(source)!=sha:raise InvalidBackup('latest_package_sha_mismatch')
                    merge_backups(None,source,source,Path(tmp)/(uid+'.minisbak'))
                    item['status']='validated'
                except (InvalidBackup,OSError) as e:
                    item['status']='blocked'
                    code=str(e)
                    item['error']=code if code in ('latest_package_missing_or_cold','unsafe_legacy_package','latest_package_size_mismatch','latest_package_sha_mismatch') else 'latest_package_invalid_or_unsupported'
        if any(item['status']=='blocked' for item in report['accounts']):
            _write(stage/REPORT,report);return report
        # A second read detects legacy writes during validation; staged output is still inactive.
        try:unchanged=_latest(legacy)==latest
        except (InvalidBackup,sqlite3.Error):unchanged=False
        if not unchanged:
            report['error']='legacy_changed_during_preparation';_write(stage/REPORT,report);return report
        cloud=CloudStore(stage)
        for row,item in zip(latest,report['accounts']):
            source=legacy/row['user_id']/(row['sha256']+'.minisbak')
            try:
                with source.open('rb') as body:
                    receipt=cloud.commit(row['user_id'],'legacy-migration',None,'migration-'+row['sha256'],body,row['bytes'],'migration.minisbak')
                if receipt['source_sha']!=row['sha256']:raise InvalidBackup('legacy_changed_during_preparation')
                # Reject any independent staged writes; a replay must remain at imported version 1.
                if cloud.head(row['user_id'])!=receipt or receipt['version']!=1:raise InvalidBackup('staging_modified_after_preparation')
                item.update(status='prepared',sha256=receipt['sha256'],bytes=receipt['bytes'])
            except (InvalidBackup,OSError,sqlite3.Error):
                item['status']='blocked';item['error']='staging_preparation_failed'
        try:unchanged=_latest(legacy)==latest
        except (InvalidBackup,sqlite3.Error):unchanged=False
        if not unchanged:report['error']='legacy_changed_during_preparation'
        if unchanged and not any(item['status']=='blocked' for item in report['accounts']):report['status']='ready'
        _write(stage/REPORT,report);return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-root',required=True);parser.add_argument('--staging-root',required=True)
    args=parser.parse_args()
    try:report=migrate(args.legacy_root,args.staging_root)
    except (ValueError,OSError,json.JSONDecodeError) as e:parser.error(str(e))
    print(json.dumps(report,sort_keys=True,indent=2))
    return 0 if report['status']=='ready' else 2

if __name__=='__main__':raise SystemExit(main())
