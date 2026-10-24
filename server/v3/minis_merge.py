"""Conservative, disk-backed merge of immutable native Minis ZIP packages."""
import base64
from contextlib import ExitStack, closing
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tempfile
import zipfile
from minis_store import InvalidBackup, MAX_UPLOAD, MAX_EXPANDED, validate_package

class UnsupportedMerge(InvalidBackup):
    pass

_MISSING = object()
MAX_METADATA = 512 * 1024 * 1024
MAX_JSON_FILE = 64 * 1024 * 1024
MAX_LINE = 8 * 1024 * 1024
_CHAT = ('sessions','messages','folders','compact_markers')

@dataclass(frozen=True)
class _Blob:
    source: str = field(compare=False)
    member: str = field(compare=False)
    size: int
    digest: str

@dataclass(frozen=True)
class _Disk:
    source: str = field(compare=False)
    size: int
    digest: str

@dataclass(frozen=True)
class _Rows:
    side: int
    path: str

def _size(value):
    return value.size if isinstance(value,(_Blob,_Disk)) else len(value)

def _digest(value):
    return value.digest if isinstance(value,(_Blob,_Disk)) else hashlib.sha256(value).hexdigest()

def _raw(value):
    if value is _MISSING:return value
    if isinstance(value,_Disk):
        if value.size>MAX_JSON_FILE:raise UnsupportedMerge('non_jsonl_file_limit_originals_preserved')
        return Path(value.source).read_bytes()
    if isinstance(value,_Blob):
        if value.size > MAX_JSON_FILE:raise UnsupportedMerge('non_jsonl_file_limit_originals_preserved')
        with zipfile.ZipFile(value.source) as z:return z.read(value.member)
    return value

def _disk_file(work,chunks,limit=None):
    if limit is None:limit=MAX_METADATA
    fd,name=tempfile.mkstemp(prefix='output-',dir=work)
    h=hashlib.sha256();size=0
    with os.fdopen(fd,'wb') as f:
        for chunk in chunks:
            if isinstance(chunk,str):chunk=chunk.encode()
            size+=len(chunk)
            if size>limit:raise UnsupportedMerge('metadata_limit_originals_preserved')
            h.update(chunk);f.write(chunk)
    return _Disk(name,size,h.hexdigest())

def _canonical(path):
    match=re.fullmatch(r'data/(folders|sessions|messages|compact_markers)(?:-[^/]+)?\.jsonl',path)
    return 'data/'+match.group(1)+'.jsonl' if match else path

def _is_records(path):
    return path.startswith('data/') and path.endswith('.jsonl') or path in ('files.index.jsonl','blobs.index.jsonl')

def _dump(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),sort_keys=True)

def _json(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result: raise InvalidBackup('duplicate_json_key')
            result[k] = v
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (ValueError, UnicodeError, RecursionError) as e:
        raise InvalidBackup('invalid_json') from e

def _records(raw, path, index_key):
    records = {}
    for line in raw.splitlines():
        if not line: continue
        if len(line) > 8 * 1024 * 1024: raise InvalidBackup('record_too_large')
        row = _json(line)
        if not isinstance(row, dict): raise InvalidBackup('invalid_record')
        if path == 'blobs.index.jsonl':
            key = row.get('sha256')
            if not isinstance(key,str) or not re.fullmatch(r'[0-9a-f]{64}',key): raise InvalidBackup('bad_blob_hash')
            if not isinstance(row.get('size'),int) or isinstance(row.get('size'),bool) or row['size'] < 0: raise InvalidBackup('bad_blob_size')
            blob_path=row.get('path')
            if not isinstance(blob_path,str) or not blob_path or PurePosixPath(blob_path).is_absolute() or '..' in PurePosixPath(blob_path).parts or '\\' in blob_path:raise InvalidBackup('bad_blob_path')
        elif path == 'files.index.jsonl':
            if not index_key: raise UnsupportedMerge('files_index_key_required')
            fields = (index_key,) if isinstance(index_key,str) else index_key
            if not isinstance(fields,(tuple,list)) or not fields: raise UnsupportedMerge('invalid_index_key_config')
            values = []
            for field in fields:
                if not isinstance(field,str) or not field: raise UnsupportedMerge('invalid_index_key_config')
                value = row
                for part in field.split('.'):
                    value = value.get(part,_MISSING) if isinstance(value,dict) else _MISSING
                if not isinstance(value,str) or not value: raise UnsupportedMerge('stable_record_id_required')
                values.append(value)
            key = tuple(values)
        else:
            d = row.get('d')
            if row.get('v') != 1 or not isinstance(row.get('t'), str) or not isinstance(d, dict): raise UnsupportedMerge('unsupported_record_schema')
            identity = d.get('session') if path == 'data/sessions.jsonl' and row['t'] == 'SessionV2' else d
            if not isinstance(identity,dict):raise UnsupportedMerge('stable_record_id_required')
            key = (row['t'], identity.get('id'))
            if not isinstance(key[1], str) or not key[1]: raise UnsupportedMerge('stable_record_id_required')
        if not isinstance(key, (str, tuple)) or not key: raise UnsupportedMerge('stable_record_id_required')
        if key in records: raise InvalidBackup('duplicate_record_id')
        records[key] = row
    return records

def _three(b, l, c):
    if b is _MISSING and (l is _MISSING or c is _MISSING):
        return (c if l is _MISSING else l), False
    if l == c: return l, False
    if b is not _MISSING and l == b: return c, False
    if b is not _MISSING and c == b: return l, False
    if isinstance(l,dict) and isinstance(c,dict) and (isinstance(b,dict) or b is _MISSING):
        if b is _MISSING: b = {}
        merged = {}; conflict = False
        for k in sorted(set(b)|set(l)|set(c)):
            v, bad = _three(b.get(k,_MISSING), l.get(k,_MISSING), c.get(k,_MISSING))
            conflict |= bad
            if v is not _MISSING: merged[k] = v
        return merged, conflict
    return (l if c is _MISSING else c), True

def _read(path, db, side, index_key):
    if Path(path).stat().st_size > MAX_UPLOAD:raise InvalidBackup('backup_too_large')
    metadata=validate_package(path)
    if metadata['encrypted']:raise UnsupportedMerge('encrypted_backup')
    with zipfile.ZipFile(path) as z:
        manifest_name=next(n for n in z.namelist() if n=='manifest.json' or n.endswith('/manifest.json'))
        prefix=manifest_name[:-len('manifest.json')]
        manifest=_json(z.read(manifest_name));entries={};original_names=set();metadata_size=0
        for info in z.infolist():
            name=info.filename
            if info.is_dir():continue
            p=PurePosixPath(name)
            if str(p)!=name or not name or any(ord(c)<32 for c in name) or info.external_attr>>16 & 0o170000==0o120000:raise InvalidBackup('unsafe_archive_path')
            if not name.startswith(prefix):raise InvalidBackup('mixed_archive_roots')
            relative=name[len(prefix):]
            if relative=='manifest.json':continue
            original_names.add(relative)
            canonical=_canonical(relative)
            if not relative.startswith('blobs/'):
                metadata_size+=info.file_size
                if metadata_size>MAX_METADATA:raise UnsupportedMerge('metadata_limit_originals_preserved')
            if _is_records(canonical):
                entries[canonical]=_Rows(side,canonical)
                with z.open(info) as src:
                    while line:=src.readline(MAX_LINE+1):
                        if len(line)>MAX_LINE:raise InvalidBackup('record_too_large')
                        if not line.strip():continue
                        records=_records(line,canonical,index_key)
                        for key,row in records.items():
                            stable_id=key[-1] if isinstance(key,tuple) else key
                            try:db.execute('INSERT INTO records VALUES(?,?,?,?,?)',(side,canonical,_dump(key),stable_id,_dump(row)))
                            except sqlite3.IntegrityError as e:raise InvalidBackup('duplicate_record_id') from e
            else:
                if not relative.startswith('blobs/') and info.file_size>MAX_JSON_FILE:raise UnsupportedMerge('non_jsonl_file_limit_originals_preserved')
                digest=manifest['integrity'].get(relative)
                if not isinstance(digest,str):raise InvalidBackup('incomplete_integrity')
                value=_Blob(str(path),name,info.file_size,digest.lower())
                if relative.endswith('.json'):_json(_raw(value))
                elif relative.endswith('.jsonl'):
                    with z.open(info) as src:
                        while line:=src.readline(MAX_LINE+1):
                            if len(line)>MAX_LINE:raise InvalidBackup('record_too_large')
                            if line.strip():_json(line)
                entries[canonical]=value
        if set(manifest['integrity'])!=original_names:raise InvalidBackup('incomplete_integrity')
        db.commit()
        return prefix,manifest,entries


def iter_record_keys(snapshot, *, validated=False):
    """Yield (canonical_path, internal JSON key) for chat records only.

    Integrity is checked before iteration; duplicate IDs across shards are
    detected with a temporary disk index. Callers must roll back on failure.
    validated=True is only for immutable snapshots already checked by the store.
    """
    if not validated:
        metadata=validate_package(snapshot)
        if metadata['encrypted']:raise UnsupportedMerge('encrypted_backup')
    with tempfile.TemporaryDirectory(prefix='minis-keys-') as work, closing(sqlite3.connect(str(Path(work)/'keys.sqlite'))) as db, zipfile.ZipFile(snapshot) as z:
        db.execute('PRAGMA cache_size=-2048');db.execute('PRAGMA temp_store=FILE')
        db.execute('CREATE TABLE keys(path TEXT,k TEXT,PRIMARY KEY(path,k)) WITHOUT ROWID')
        manifest=next(n for n in z.namelist() if n=='manifest.json' or n.endswith('/manifest.json'))
        prefix=manifest[:-len('manifest.json')]
        for info in z.infolist():
            if info.is_dir() or not info.filename.startswith(prefix):continue
            path=_canonical(info.filename[len(prefix):])
            if path not in tuple('data/'+name+'.jsonl' for name in _CHAT)+('files.index.jsonl',):continue
            with z.open(info) as src:
                while line:=src.readline(MAX_LINE+1):
                    if len(line)>MAX_LINE:raise InvalidBackup('record_too_large')
                    if not line.strip():continue
                    for key,row in _records(line,path,('category','path')).items():
                        if path=='files.index.jsonl' and row.get('category')!='chats':continue
                        serialized=_dump(key)
                        try:db.execute('INSERT INTO keys VALUES(?,?)',(path,serialized))
                        except sqlite3.IntegrityError as e:raise InvalidBackup('duplicate_record_id') from e
                        yield path,serialized


def _legacy_three(b,l,c,deleted=False):
    # A legacy upload is not an acknowledgement and can never infer deletion.
    if l is _MISSING or l==b:return c,False
    if c is _MISSING and deleted:return c,True
    if l==c:return c,False
    if c==b:return l,False
    return c,True


def merge_backups(base, local, cloud, destination, *, index_key=('category','path'), scope='all', legacy=False, legacy_deleted=None):
    """Merge immutable ZIPs into a new validated ZIP, with bounded record memory.

    JSONL records and reference checks use temporary SQLite; at most one record
    per side is parsed at a time. Blobs stream between ZIPs. Metadata is capped
    at 512 MiB per input/output, non-JSONL files at 64 MiB, JSONL lines at 8 MiB.
    Conflicts retain complete versions in sync/conflicts.json. scope='chats'
    limits deletion inference to chat records and category=chats file records.
    legacy=True treats base only as the last submission, never as an ACK;
    legacy_deleted(path, internal_json_key) blocks tombstoned record revival
    for legacy uploads and v3 first syncs without an acknowledged base.
    """
    if scope not in ('all','chats'):raise UnsupportedMerge('unsupported_merge_scope')
    destination=Path(destination)
    if any(p is not None and Path(p).resolve()==destination.resolve() for p in (base,local,cloud)):raise InvalidBackup('cannot_overwrite_original')
    if destination.exists():raise FileExistsError(destination)
    with tempfile.TemporaryDirectory(prefix='.merge-work-',dir=destination.parent) as work:
        with closing(sqlite3.connect(str(Path(work)/'records.sqlite'))) as db:
            db.execute('PRAGMA cache_size=-4096')
            db.execute('PRAGMA temp_store=FILE')
            db.execute('CREATE TABLE records(side INTEGER,path TEXT,k TEXT,id TEXT,value TEXT,PRIMARY KEY(side,path,k)) WITHOUT ROWID')
            db.execute('CREATE INDEX record_ids ON records(side,path,id)')
            db.execute('CREATE INDEX record_keys ON records(path,k,side)')
            db.execute('CREATE TABLE pending(path TEXT,k TEXT,PRIMARY KEY(path,k)) WITHOUT ROWID')
            db.execute('CREATE TABLE edges(parent_path TEXT,parent_id TEXT,child_path TEXT,child_k TEXT,PRIMARY KEY(parent_path,parent_id,child_path,child_k)) WITHOUT ROWID')
            db.execute('CREATE TABLE conflicts(value TEXT PRIMARY KEY) WITHOUT ROWID')
            db.execute('CREATE TABLE checked(path TEXT,k TEXT,PRIMARY KEY(path,k)) WITHOUT ROWID')
            db.execute('CREATE TABLE isolated(path TEXT,k TEXT,PRIMARY KEY(path,k)) WITHOUT ROWID')
            return _merge(base,local,cloud,destination,index_key,scope,db,Path(work),legacy,legacy_deleted)


def _merge(base,local,cloud,destination,index_key,scope,db,work,legacy,legacy_deleted):
    lp,lm,le=_read(local,db,1,index_key);cp,cm,ce=_read(cloud,db,2,index_key)
    if base is None:bp,bm,be=cp,cm,{}
    else:bp,bm,be=_read(base,db,0,index_key)
    if lp!=cp or bp!=cp:raise UnsupportedMerge('archive_root_mismatch')
    variable=('integrity','backup_id','created_at','createdAt','timestamp','snapshot_at','created_at_ms','app','device_name','categories')
    schema=lambda m:{k:v for k,v in m.items() if k not in variable}
    if schema(lm)!=schema(cm) or base is not None and schema(bm)!=schema(cm):raise UnsupportedMerge('manifest_schema_mismatch')
    result={};conflict_count=0;deleted=0
    def remember(item,new=True):
        nonlocal conflict_count
        db.execute('INSERT OR IGNORE INTO conflicts VALUES(?)',(_dump(item),))
        if new:conflict_count+=1
    def recovery(path,b,l,c,encoding=False,**extra):
        def saved(v):return None if v is _MISSING else base64.b64encode(_raw(v)).decode() if encoding else v
        item={'path':path,'base':saved(b),'local':saved(l),'cloud':saved(c),'local_deleted':l is _MISSING,'cloud_deleted':c is _MISSING,**extra}
        if encoding:item['encoding']='base64'
        remember(item)
    for entries in (be,le,ce):
        raw=entries.pop('sync/conflicts.json',None)
        if raw is not None:
            records=_json(_raw(raw))
            if not isinstance(records,list):raise InvalidBackup('bad_conflict_archive')
            for item in records:remember(item,False)
            del records
    def versions(path,key):
        rows={side:_json(value) for side,value in db.execute('SELECT side,value FROM records WHERE path=? AND k=? AND side<3',(path,key))}
        return [rows.get(side,_MISSING) for side in (0,1,2)]
    def put(path,key,row):
        stable_id=_json(key);stable_id=stable_id[-1] if isinstance(stable_id,list) else stable_id
        db.execute('INSERT OR REPLACE INTO records VALUES(3,?,?,?,?)',(path,key,stable_id,_dump(row)))
    for path in sorted(set(be)|set(le)|set(ce)):
        b,l,c=[e.get(path,_MISSING) for e in (be,le,ce)]
        chat_path=path in tuple('data/'+n+'.jsonl' for n in _CHAT)
        conservative=scope=='chats' and not chat_path
        if _is_records(path):
            for (key,) in db.execute('SELECT k FROM records WHERE path=? AND side<3 GROUP BY k ORDER BY k',(path,)):
                bv,lv,cv=versions(path,key);effective_base=bv
                if conservative and not legacy and (path!='files.index.jsonl' or next((r.get('category') for r in (cv,lv,bv) if r is not _MISSING),None)!='chats'):
                    effective_base=_MISSING
                    if lv is _MISSING and cv is _MISSING:cv=bv
                if path=='blobs.index.jsonl':
                    present=[v for v in (bv,lv,cv) if v is not _MISSING]
                    if any(v['size']!=present[0]['size'] for v in present):raise InvalidBackup('blob_index_collision')
                    value,bad=present[-1],False
                    if any(v!=value for v in present):recovery(path,bv,lv,cv)
                elif base is None and not legacy and legacy_deleted is not None and lv is not _MISSING and legacy_deleted(path,key):
                    recovery(path,bv,lv,cv,key=_json(key),reason='tombstone_revival_blocked')
                    db.execute('INSERT OR IGNORE INTO isolated VALUES(?,?)',(path,key))
                    value,bad=cv,False
                elif legacy:
                    tombstoned=bool(legacy_deleted(path,key)) if legacy_deleted is not None and cv is _MISSING else False
                    value,bad=_legacy_three(bv,lv,cv,tombstoned)
                else:value,bad=_three(effective_base,lv,cv)
                if bad:
                    recovery(path,bv,lv,cv,key=_json(key))
                    value=cv if legacy else lv if cv is _MISSING else cv
                if value is not _MISSING:put(path,key,value)
                elif bv is not _MISSING:deleted+=1
            result[path]=_Rows(3,path)
        elif path.startswith('blobs/'):
            values=[v for v in (b,l,c) if v is not _MISSING]
            if any(v!=values[0] for v in values):raise UnsupportedMerge('blob_collision')
            result[path]=values[0]
        else:
            if conservative and not legacy:
                if l is _MISSING and c is _MISSING:c=b
                b=_MISSING
            if path.startswith('data/') and path.endswith('.json') or path=='secrets.json':
                bv,lv,cv=[_json(_raw(v)) if v is not _MISSING else v for v in (b,l,c)]
                value,bad=(_legacy_three if legacy else _three)(bv,lv,cv)
                if bad:recovery(path,bv,lv,cv)
                if value is not _MISSING:result[path]=_disk_file(work,json.JSONEncoder(ensure_ascii=False).iterencode(value),MAX_JSON_FILE)
                del bv,lv,cv,value
            elif path.startswith('data/memory/') and path.endswith('.md'):
                for v in (b,l,c):
                    if v is not _MISSING:
                        try:_raw(v).decode('utf-8')
                        except UnicodeError as e:raise UnsupportedMerge('non_utf8_memory') from e
                value,bad=(_legacy_three if legacy else _three)(b,l,c)
                if bad:recovery(path,b,l,c,True)
                if value is not _MISSING:result[path]=value
            else:
                value,bad=(_legacy_three if legacy else _three)(b,l,c)
                if bad:
                    if scope=='chats' or legacy:recovery(path,b,l,c,True)
                    else:raise UnsupportedMerge('unknown_member_conflict:'+path)
                if value is not _MISSING:result[path]=value
    # Check references using indexed IDs, including parents restored mid-traversal.
    def restore_parent(name,record_id):
        nonlocal deleted
        path='data/'+name+'.jsonl'
        if db.execute('SELECT 1 FROM records WHERE side=3 AND path=? AND id=?',(path,record_id)).fetchone():return True
        rows=list(db.execute('SELECT side,k,value FROM records WHERE side<3 AND path=? AND id=? ORDER BY side',(path,record_id)))
        if not rows:
            if legacy:return False
            raise InvalidBackup('dangling_record_reference')
        by_side={}
        for side,key,value in rows:
            if side in by_side:raise InvalidBackup('ambiguous_parent_id')
            by_side[side]=(key,_json(value))
        if any(db.execute('SELECT 1 FROM isolated WHERE path=? AND k=?',(path,key)).fetchone() for _,key,_ in rows):return False
        if legacy and 2 not in by_side:return False
        key,chosen=by_side[max(by_side)]
        recovery(path,*[by_side.get(side,(None,_MISSING))[1] for side in (0,1,2)],reason='referenced_parent_preserved')
        put(path,key,chosen);db.execute('INSERT OR IGNORE INTO pending VALUES(?,?)',(path,key));result[path]=_Rows(3,path);deleted=max(0,deleted-1)
        return True
    for name in _CHAT:
        db.execute('INSERT INTO pending SELECT path,k FROM records WHERE side=3 AND path=?',('data/'+name+'.jsonl',))
    while entry:=db.execute('SELECT path,k FROM pending LIMIT 1').fetchone():
        path,key=entry;db.execute('DELETE FROM pending WHERE path=? AND k=?',(path,key))
        if db.execute('SELECT 1 FROM checked WHERE path=? AND k=?',(path,key)).fetchone():continue
        row=db.execute('SELECT value FROM records WHERE side=3 AND path=? AND k=?',(path,key)).fetchone()
        if row is None:continue
        record=_json(row[0]);d=record['d'];name=path[5:-6]
        db.execute('INSERT INTO checked VALUES(?,?)',(path,key))
        if name in ('messages','compact_markers'):refs=[('sessionId','sessions')]
        elif name=='sessions':d=d.get('session',d);refs=[('folderId','folders'),('parentSessionId','sessions')]
        else:refs=[('parentId','folders'),('parentFolderId','folders')]
        for field,parent in refs:
            ref=d.get(field)
            if ref is not None:
                if not isinstance(ref,str) or not ref:raise InvalidBackup('bad_parent_reference')
                db.execute('INSERT OR IGNORE INTO edges VALUES(?,?,?,?)',('data/'+parent+'.jsonl',ref,path,key))
                if restore_parent(parent,ref) is False:
                    bv,lv,cv=versions(path,key)
                    if cv is not _MISSING:raise InvalidBackup('dangling_cloud_record_reference')
                    recovery(path,bv,lv,cv,key=_json(key),reason='legacy_parent_deleted' if legacy else 'tombstoned_parent_deleted')
                    db.execute('INSERT OR IGNORE INTO isolated VALUES(?,?)',(path,key))
                    db.execute('DELETE FROM records WHERE side=3 AND path=? AND k=?',(path,key))
                    stable_id=_json(key)[-1]
                    for child_path,child_key in db.execute('SELECT child_path,child_k FROM edges WHERE parent_path=? AND parent_id=?',(path,stable_id)):
                        db.execute('DELETE FROM checked WHERE path=? AND k=?',(child_path,child_key))
                        db.execute('INSERT OR IGNORE INTO pending VALUES(?,?)',(child_path,child_key))
                    break
    count=lambda path:db.execute('SELECT COUNT(*) FROM records WHERE side=3 AND path=?',(path,)).fetchone()[0]
    file_stats={}
    for (raw,) in db.execute("SELECT value FROM records WHERE side=3 AND path='files.index.jsonl'"):
        row=_json(raw);category=row.get('category');stats=file_stats.setdefault(category,{'entries':0,'files':0,'bytes':0})
        stats['entries']+=1
        if not row.get('isDirectory',False) and not row.get('skipped',False):stats['files']+=1
        if isinstance(row.get('size'),int):stats['bytes']+=row['size']
    for (raw,) in db.execute("SELECT value FROM records WHERE side=3 AND path='blobs.index.jsonl'"):
        row=_json(raw);sha=row['sha256'];blob=result.get('blobs/'+sha[:2]+'/'+sha)
        if not isinstance(blob,_Blob) or blob.size!=row['size'] or blob.digest!=sha:raise InvalidBackup('blob_index_reference_mismatch')
    manifest=dict(cm);categories={}
    for m in (bm,lm,cm):
        for key,value in m['categories'].items():
            if isinstance(value,dict):
                if not isinstance(categories.get(key),dict):categories[key]={}
                categories[key].update(value)
            else:categories[key]=value
    for key,value in list(categories.items()):
        path='data/'+key+'.jsonl'
        if isinstance(value,int) and not isinstance(value,bool) and path in result:categories[key]=count(path)
        elif isinstance(value,dict):
            if key=='chats':value['messages']=count('data/messages.jsonl')
            if path in result and 'entries' in value:value['entries']=count(path)
            stats=file_stats.get(key,{'entries':0,'files':0,'bytes':0})
            if 'files' in value:value['files']=stats['files']
            if key=='memory':
                members=[v for p,v in result.items() if p.startswith('data/memory/')]
                value['entries']=len(members);value['bytes']=sum(map(_size,members))
            elif stats['entries']:
                if 'bytes' in value:value['bytes']=stats['bytes']
                if 'entries' in value:value['entries']=stats['entries']
            elif key=='providers' and 'data/provider_config.json' in result:
                config=_json(_raw(result['data/provider_config.json']));value['bytes']=_size(result['data/provider_config.json'])
                if isinstance(config,list):value['entries']=len(config)
                elif isinstance(config,dict) and isinstance(config.get('providers'),list):value['entries']=len(config['providers'])
    # Write JSONL and recovery logs incrementally; only row-sized strings in RAM.
    for path,value in list(result.items()):
        if isinstance(value,_Rows):
            result[path]=_disk_file(work,(raw+'\n' for (raw,) in db.execute('SELECT value FROM records WHERE side=3 AND path=? ORDER BY k',(path,))))
    if db.execute('SELECT 1 FROM conflicts LIMIT 1').fetchone():
        def recovery_chunks():
            yield '[';first=True
            for (raw,) in db.execute('SELECT value FROM conflicts ORDER BY value'):
                if not first:yield ','
                first=False;yield raw
            yield ']'
        result['sync/conflicts.json']=_disk_file(work,recovery_chunks())
    manifest['categories']=categories
    manifest['integrity']={path:_digest(value) for path,value in result.items()}
    result['manifest.json']=json.dumps(manifest,ensure_ascii=False).encode()
    if sum(_size(v) for p,v in result.items() if not p.startswith('blobs/'))>MAX_METADATA:raise UnsupportedMerge('metadata_limit_originals_preserved')
    if sum(map(_size,result.values()))>MAX_EXPANDED:raise InvalidBackup('backup_too_large')
    tmp=work/'merged.zip'
    with ExitStack() as source_stack, zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED) as z:
        sources={}
        for path,value in sorted(result.items()):
            if isinstance(value,_Blob):
                source=sources.get(value.source)
                if source is None:
                    source=source_stack.enter_context(zipfile.ZipFile(value.source))
                    sources[value.source]=source
                with source.open(value.member) as src,z.open(cp+path,'w',force_zip64=True) as dst:shutil.copyfileobj(src,dst,1024*1024)
            elif isinstance(value,_Disk):
                with open(value.source,'rb') as src,z.open(cp+path,'w',force_zip64=True) as dst:shutil.copyfileobj(src,dst,1024*1024)
            else:z.writestr(cp+path,value)
    validate_package(tmp)
    with tmp.open('rb') as f:os.fsync(f.fileno())
    os.link(tmp,destination)
    return {'conflicts':conflict_count,'deleted':deleted,'path':str(destination)}
