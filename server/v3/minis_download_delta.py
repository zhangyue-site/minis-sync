"""Byte-exact download deltas. Immutable native ZIPs remain the source of truth.

Blocks are aligned inside ZIP members, so moving an unchanged attachment does
not retransmit it. Every reconstruction verifies the full target SHA before use.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import time
import zipfile
from minis_store import InvalidBackup, MAX_UPLOAD

MAGIC = b'MSD1\r\n\x1a\n'
BLOCK = 65536
MAX_HEADER = 8 * 1024 * 1024
MAX_STEPS = 250000


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def segments(path):
    """Partition exact ZIP bytes, including headers, descriptors and central dir."""
    with Path(path).open('rb') as f, zipfile.ZipFile(path) as z:
        position=0
        for entry in sorted(z.infolist(),key=lambda e:e.header_offset):
            f.seek(entry.header_offset);header=f.read(30)
            if len(header)!=30 or header[:4]!=b'PK\x03\x04':raise InvalidBackup('invalid_zip_header')
            a,b=struct.unpack_from('<HH',header,26);start=entry.header_offset+30+a+b
            if start<position or start+entry.compress_size>Path(path).stat().st_size:raise InvalidBackup('invalid_zip_extent')
            for begin,end in [(position,start),(start,start+entry.compress_size)]:
                for offset in range(begin,end,BLOCK):yield offset,min(BLOCK,end-offset)
            position=start+entry.compress_size
        for offset in range(position,Path(path).stat().st_size,BLOCK):yield offset,min(BLOCK,Path(path).stat().st_size-offset)


def create(base,target,base_sha,target_sha,destination):
    base,target,destination=Path(base),Path(target),Path(destination)
    if destination.exists():raise FileExistsError('existing_delta_preserved')
    size=target.stat().st_size
    if not 0<size<=MAX_UPLOAD:raise InvalidBackup('invalid_target_size')
    if shutil.disk_usage(destination.parent).free<2*size+2*1024**3:raise RuntimeError('delta_capacity_originals_preserved')
    index={};h=hashlib.sha256()
    with base.open('rb') as f:
        for offset,length in segments(base):
            f.seek(offset);b=f.read(length)
            if len(b)!=length:raise InvalidBackup('short_delta_base')
            h.update(b);index.setdefault((hashlib.sha256(b).digest(),length),offset)
    if h.hexdigest()!=base_sha:raise InvalidBackup('delta_base_digest_mismatch')
    steps=[];new_bytes=0;reused=0;h=hashlib.sha256()
    fd,temp=tempfile.mkstemp(prefix='.download-delta-',dir=destination.parent)
    try:
        with tempfile.TemporaryFile(dir=destination.parent) as payload,target.open('rb') as f:
            for offset,length in segments(target):
                f.seek(offset);b=f.read(length)
                if len(b)!=length:raise InvalidBackup('short_delta_target')
                h.update(b);found=index.get((hashlib.sha256(b).digest(),length))
                if found is None:
                    step=['d',new_bytes,length];payload.write(b);new_bytes+=length
                else:step=['b',found,length];reused+=length
                if steps and steps[-1][0]==step[0] and steps[-1][1]+steps[-1][2]==step[1]:steps[-1][2]+=length
                else:steps.append(step)
                if len(steps)>MAX_STEPS:raise InvalidBackup('delta_plan_too_large')
            if h.hexdigest()!=target_sha:raise InvalidBackup('delta_target_digest_mismatch')
            header=json.dumps({'v':1,'base':base_sha,'target':target_sha,'bytes':size,'payload_bytes':new_bytes,'steps':steps},separators=(',',':')).encode()
            if len(header)>MAX_HEADER:raise InvalidBackup('delta_plan_too_large')
            with os.fdopen(fd,'wb') as out:
                fd=None;out.write(MAGIC);out.write(struct.pack('>I',len(header)));out.write(header)
                payload.seek(0);shutil.copyfileobj(payload,out,1024*1024);out.flush();os.fsync(out.fileno())
        os.link(temp,destination)
        return {'sha256':digest(destination),'bytes':destination.stat().st_size,'base':base_sha,
                'target_sha256':target_sha,'target_bytes':size,'reused_bytes':reused,'protocol':1}
    finally:
        if fd is not None:os.close(fd)
        Path(temp).unlink(missing_ok=True)


def prepare(store,uid,base_sha,target_sha,build=True):
    """Ownership is checked for BOTH versions, including a cached result."""
    base=store.path(uid,base_sha);target=store.path(uid,target_sha)
    folder=store.root/'download-deltas'/str(uid);folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=folder/(base_sha+'-'+target_sha+'.mdelta')
    with store._lock(str(uid)):
        if not path.exists():
            if not build:raise FileNotFoundError('download_delta_not_prepared')
            meta=create(base,target,base_sha,target_sha,path)
        else:
            meta=inspect(path)
            if meta['base']!=base_sha or meta['target_sha256']!=target_sha:raise InvalidBackup('invalid_cached_delta')
        # Only derived cache files are eligible; in-flight files are retained a day.
        for old in folder.glob('*.mdelta'):
            if old!=path and time.time()-old.stat().st_mtime>86400:old.unlink()
    return path,meta


def inspect(path):
    with Path(path).open('rb') as f:
        if f.read(8)!=MAGIC:raise InvalidBackup('invalid_delta_magic')
        length=struct.unpack('>I',f.read(4))[0]
        if not 0<length<=MAX_HEADER:raise InvalidBackup('invalid_delta_header')
        h=json.loads(f.read(length))
    return {'sha256':digest(path),'bytes':Path(path).stat().st_size,'base':h['base'],
            'target_sha256':h['target'],'target_bytes':h['bytes'],
            'reused_bytes':sum(s[2] for s in h['steps'] if s[0]=='b'),'protocol':1}


def apply(base,delta,destination,base_sha,target_sha,target_size):
    """Reference decoder, also used to verify cross-language interoperability."""
    base,delta,destination=Path(base),Path(delta),Path(destination)
    if destination.exists():raise FileExistsError('existing_target_preserved')
    if digest(base)!=base_sha:raise InvalidBackup('delta_base_digest_mismatch')
    fd,temp=tempfile.mkstemp(prefix='.delta-apply-',dir=destination.parent)
    try:
        with delta.open('rb') as f,base.open('rb') as source,os.fdopen(fd,'wb') as out:
            fd=None
            if f.read(8)!=MAGIC:raise InvalidBackup('invalid_delta_magic')
            raw=f.read(4)
            if len(raw)!=4:raise InvalidBackup('short_delta_header')
            length=struct.unpack('>I',raw)[0]
            if not 0<length<=MAX_HEADER:raise InvalidBackup('invalid_delta_header')
            h=json.loads(f.read(length));payload=f.tell()
            if h.get('v')!=1 or h.get('base')!=base_sha or h.get('target')!=target_sha or h.get('bytes')!=target_size:raise InvalidBackup('delta_identity_mismatch')
            if type(target_size) is not int or not 0<target_size<=MAX_UPLOAD:raise InvalidBackup('invalid_target_size')
            if not isinstance(h.get('steps'),list) or len(h['steps'])>MAX_STEPS:raise InvalidBackup('invalid_delta_steps')
            if type(h.get('payload_bytes')) is not int or h['payload_bytes']<0 or payload+h['payload_bytes']!=delta.stat().st_size:raise InvalidBackup('delta_payload_size_mismatch')
            total=0;data_position=0;sha=hashlib.sha256()
            for step in h['steps']:
                if not isinstance(step,list) or len(step)!=3 or step[0] not in ('b','d') or any(type(x) is not int for x in step[1:]):raise InvalidBackup('invalid_delta_step')
                kind,offset,size=step
                if offset<0 or size<=0 or total+size>target_size:raise InvalidBackup('invalid_delta_extent')
                if kind=='b':
                    if offset+size>base.stat().st_size:raise InvalidBackup('invalid_base_extent')
                    stream=source;stream.seek(offset)
                else:
                    if offset!=data_position or offset+size>h['payload_bytes']:raise InvalidBackup('invalid_payload_extent')
                    data_position+=size;stream=f;stream.seek(payload+offset)
                left=size
                while left:
                    b=stream.read(min(left,65536))
                    if not b:raise InvalidBackup('short_delta_body')
                    out.write(b);sha.update(b);left-=len(b)
                total+=size
            if total!=target_size or data_position!=h['payload_bytes'] or sha.hexdigest()!=target_sha:raise InvalidBackup('reconstructed_digest_mismatch')
            out.flush();os.fsync(out.fileno())
        os.link(temp,destination)
    except (KeyError,TypeError,ValueError,struct.error) as e:raise InvalidBackup('invalid_delta') from e
    finally:
        if fd is not None:os.close(fd)
        Path(temp).unlink(missing_ok=True)
