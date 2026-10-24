"""Read only historical chat metadata, seed tombstones in an isolated staging store.

No network access or package recovery. Default is a dry report; --apply requires
all indexed historical snapshots to be covered. Never prints record IDs/content.
"""
import argparse,base64,gzip,hashlib,io,json,os,re,sqlite3,struct,tempfile,zipfile,zlib
from contextlib import closing,contextmanager,ExitStack
from pathlib import Path
from minis_cloud import CloudStore
from minis_merge import _canonical,_records,_dump,MAX_LINE,iter_record_keys
from minis_store import InvalidBackup

CHAT=tuple('data/'+x+'.jsonl' for x in ('sessions','messages','folders','compact_markers'))+('files.index.jsonl',)

def _wanted(name):return _canonical(name) in CHAT or name=='manifest.json'
def _rows(root):
    p=Path(root).resolve()/'snapshots.db'
    for ext in ('-wal','-journal'):
        q=Path(str(p)+ext)
        if q.exists() and q.stat().st_size:raise InvalidBackup('legacy_requires_offline_checkpoint')
    with closing(sqlite3.connect(p.as_uri()+'?mode=ro&immutable=1',uri=True)) as c:
        return [tuple(r) for r in c.execute('SELECT user_id,sha256,bytes,created_at FROM snapshots ORDER BY user_id,created_at,sha256')]
def _hash(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        while b:=f.read(1024*1024):h.update(b)
    return h.hexdigest()

def _keys(path,source,expected):
    digest=hashlib.sha256()
    while raw:=source.readline(MAX_LINE+1):
        if len(raw)>MAX_LINE:raise InvalidBackup('record_too_large')
        digest.update(raw)
        if not raw.strip():continue
        for key,row in _records(raw,path,('category','path')).items():
            if path=='files.index.jsonl' and row.get('category')!='chats':continue
            yield path,_dump(key)
    if digest.hexdigest()!=expected:raise InvalidBackup('metadata_integrity_mismatch')

def zip_keys(path):
    with zipfile.ZipFile(path) as z:
        manifest_name=next(n for n in z.namelist() if n=='manifest.json' or n.endswith('/manifest.json'))
        if z.getinfo(manifest_name).file_size>8*1024**2:raise InvalidBackup('manifest_too_large')
        m=json.loads(z.read(manifest_name));prefix=manifest_name[:-13]
        if m.get('encryption'):raise InvalidBackup('encrypted_backup')
        for i in z.infolist():
            relative=i.filename[len(prefix):];canonical=_canonical(relative)
            if canonical not in CHAT:continue
            with z.open(i) as source:yield from _keys(canonical,source,m['integrity'][relative])

@contextmanager
def _inflate(chunk,method):
    with tempfile.TemporaryFile() as f,chunk.open('rb') as source:
        decompressor=zlib.decompressobj(-15) if method==8 else None
        if method not in (0,8):raise InvalidBackup('unsupported_compression')
        total=0
        while b:=source.read(65536):
            if decompressor:
                while b:
                    out=decompressor.decompress(b,1024*1024);b=decompressor.unconsumed_tail;f.write(out);total+=len(out)
                    if total>512*1024**2:raise InvalidBackup('metadata_too_large')
            else:f.write(b);total+=len(b)
        if decompressor and not decompressor.eof:raise InvalidBackup('truncated_deflate')
        f.seek(0);yield f

def recipe_keys(uid,sha,size,cas):
    manifest_path=cas/(uid+'-'+sha+'.json.gz')
    with gzip.open(manifest_path,'rb') as f:
        raw=f.read(64*1024**2+1)
    if len(raw)>64*1024**2:raise InvalidBackup('recipe_too_large')
    m=json.loads(raw)
    if m['uid']!=uid or m['sha256']!=sha or m['bytes']!=size:raise InvalidBackup('recipe_identity_mismatch')
    members={};recipe=m['recipe']
    for i,seg in enumerate(recipe[:-1]):
        if 'literal' not in seg:continue
        header=base64.b64decode(seg['literal'],validate=True);at=header.rfind(b'PK\x03\x04')
        if at<0:continue
        h=header[at:];nl,xl=struct.unpack_from('<HH',h,26);name=h[30:30+nl].decode('utf-8')
        # Native packages may have an arbitrary common top directory.
        members[name]=(recipe[i+1],struct.unpack_from('<H',h,8)[0])
    manifests=[n for n in members if n=='manifest.json' or n.endswith('/manifest.json')]
    if len(manifests)!=1:raise InvalidBackup('manifest_required')
    manifest_name=manifests[0];prefix=manifest_name[:-13]
    def chunk_for(name):
        seg,method=members[name];digest=seg['chunk']
        if not re.fullmatch('[a-f0-9]{64}',digest):raise InvalidBackup('bad_chunk_hash')
        p=cas/'chunks'/digest
        if not p.is_file():raise FileNotFoundError('metadata_chunk_missing')
        if p.stat().st_size!=seg['size'] or _hash(p)!=digest:raise InvalidBackup('metadata_chunk_corrupt')
        return p,method
    with _inflate(*chunk_for(manifest_name)) as f:
        raw=f.read(8*1024**2+1)
        if len(raw)>8*1024**2:raise InvalidBackup('manifest_too_large')
        native=json.loads(raw)
    if native.get('encryption'):raise InvalidBackup('encrypted_backup')
    for name in members:
        relative=name[len(prefix):];canonical=_canonical(relative)
        if not name.startswith(prefix) or canonical not in CHAT:continue
        with _inflate(*chunk_for(name)) as f:yield from _keys(canonical,f,native['integrity'][relative])

def inventory(legacy_root,staging_root,*,cas_root=None,source_roots=(),apply=False):
    legacy=Path(legacy_root).resolve();stage=Path(staging_root).resolve()
    if legacy==stage or legacy in stage.parents or stage in legacy.parents:raise ValueError('staging_must_be_separate')
    if not (stage/'.migration-state.json').exists() and not (stage/'heads.sqlite3').is_file():raise ValueError('existing_staging_required')
    if apply and not (stage/'.migration-state.json').is_file():raise ValueError('apply_requires_migration_staging_marker')
    rows=_rows(legacy);generation=hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()
    report={'generation':generation,'status':'blocked','applied':False,'snapshots_total':len(rows),'snapshots_scanned':0,'accounts':[]}
    if not rows:report['error']='empty_legacy_index';return report
    with tempfile.TemporaryDirectory(prefix='minis-history-') as tmp,closing(sqlite3.connect(str(Path(tmp)/'inventory.sqlite3'))) as index:
        index.execute('PRAGMA temp_store=FILE');index.execute('PRAGMA cache_size=-2048')
        index.execute('CREATE TABLE seen(uid TEXT,path TEXT,k TEXT,PRIMARY KEY(uid,path,k)) WITHOUT ROWID')
        blocked=False
        for uid,sha,size,created in rows:
            if not re.fullmatch('[0-9]+',uid) or not re.fullmatch('[a-f0-9]{64}',sha):raise InvalidBackup('invalid_legacy_index')
            item={'uid':uid,'sha256':sha,'bytes':size}
            report['accounts'].append(item)
            candidates=[legacy/uid/(sha+'.minisbak')]
            for root in source_roots:
                p=Path(root);candidates.extend((p/uid/(sha+'.minisbak'),p/(sha+'.minisbak'),p/('user'+uid)/(sha+'.minisbak')))
            full=next((p for p in candidates if p.is_file()),None)
            try:
                if full:
                    if full.stat().st_size!=size:raise InvalidBackup('historical_size_mismatch')
                    keys=zip_keys(full)
                elif cas_root and (Path(cas_root)/(uid+'-'+sha+'.json.gz')).is_file():keys=recipe_keys(uid,sha,size,Path(cas_root))
                else:raise FileNotFoundError('historical_metadata_unavailable')
                index.executemany('INSERT OR IGNORE INTO seen VALUES(?,?,?)',((uid,p,k) for p,k in keys))
                item['status']='scanned';report['snapshots_scanned']+=1
            except (OSError,ValueError,KeyError,zipfile.BadZipFile,struct.error,zlib.error):
                item['status']='blocked';item['error']='historical_metadata_unavailable_or_invalid';blocked=True
        if _rows(legacy)!=rows:blocked=True;report['error']='legacy_index_changed'
        if blocked:return report
        report['seen_records']=index.execute('SELECT COUNT(*) FROM seen').fetchone()[0]
        # Read head identities without initializing or changing staging in dry mode.
        with closing(sqlite3.connect((stage/'heads.sqlite3').as_uri()+'?mode=ro',uri=True)) as c:
            heads=list(c.execute('SELECT uid,sha FROM versions JOIN heads USING(uid,version) ORDER BY uid'))
        if not set(u for u,s,n,t in rows).issubset({u for u,s in heads}):report['error']='staging_head_missing';return report
        index.execute('CREATE TABLE active(uid TEXT,path TEXT,k TEXT,PRIMARY KEY(uid,path,k)) WITHOUT ROWID')
        for uid,sha in heads:
            path=stage/'snapshots'/uid/(sha+'.minisbak')
            index.executemany('INSERT OR IGNORE INTO active VALUES(?,?,?)',((uid,p,k) for p,k in iter_record_keys(path,validated=True)))
        report['tombstoned_records']=index.execute('SELECT COUNT(*) FROM seen s WHERE NOT EXISTS(SELECT 1 FROM active a WHERE a.uid=s.uid AND a.path=s.path AND a.k=s.k)').fetchone()[0]
        if apply:
            # Only explicit staging roots may be mutated; source is never opened writable.
            cloud=CloudStore(stage)
            with ExitStack() as stack:
                for uid in sorted({u for u,s in heads}):stack.enter_context(cloud._lock(uid))
                with cloud._db() as c:
                    current=list(c.execute('SELECT uid,sha FROM versions JOIN heads USING(uid,version) ORDER BY uid'))
                    if [tuple(r) for r in current]!=heads:raise InvalidBackup('staging_head_changed')
                    c.execute('CREATE TABLE IF NOT EXISTS history_generations(generation TEXT PRIMARY KEY,heads TEXT NOT NULL)')
                    c.executemany('INSERT OR IGNORE INTO known_records VALUES(?,?,?,1)',index.execute('SELECT uid,path,k FROM seen'))
                    for uid,sha in heads:cloud._inventory(c,uid,sha)
                    c.execute('INSERT OR REPLACE INTO history_generations VALUES(?,?)',(generation,json.dumps(heads)))
            report['applied']=True
        report['status']='ready';return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--legacy-root',required=True);p.add_argument('--staging-root',required=True);p.add_argument('--cas-root');p.add_argument('--source-root',action='append',default=[]);p.add_argument('--apply',action='store_true')
    a=p.parse_args();result=inventory(a.legacy_root,a.staging_root,cas_root=a.cas_root,source_roots=a.source_root,apply=a.apply);print(json.dumps(result,indent=2,sort_keys=True));return 0 if result['status']=='ready' else 2

if __name__=='__main__':raise SystemExit(main())
