#!/usr/bin/env python3
"""Repack the verified credential-free runtime; sign a separate managed index."""
import argparse,base64,hashlib,io,json,os,posixpath,subprocess,tarfile,time
from pathlib import Path

def prepare(source,out,key,sequence,version_opencode,version_zcode):
 out.mkdir(parents=True,exist_ok=True);package=out/'runtime.tar.gz';total=0
 with tarfile.open(source,'r:gz') as src,tarfile.open(package,'w:gz',dereference=True) as dst:
  for m in src.getmembers():
   if m.name.startswith('/') or '..' in Path(m.name).parts:raise ValueError('unsafe input')
   if m.issym() and m.linkname.startswith('/'):
    m.linkname=posixpath.relpath(m.linkname.lstrip('/'),posixpath.dirname(m.name) or '.')
   if m.islnk():
    f=src.extractfile(m);data=f.read();m.type=tarfile.REGTYPE;m.linkname='';m.size=len(data);dst.addfile(m,io.BytesIO(data));total+=len(data)
   else:
    dst.addfile(m,src.extractfile(m) if m.isfile() else None);total+=m.size
 sha=hashlib.sha256(package.read_bytes()).hexdigest();immutable=out/(sha+'.tar.gz');package.rename(immutable)
 now=int(time.time());payload={'format':1,'architecture':'aarch64','adapter':1,'data_schema':1,'sequence':sequence,'min_host':121,'issued':now,'expires':now+90*86400,'bytes':immutable.stat().st_size,'unpacked_bytes':total,'sha256':sha,'url':'https://d.zhangyue.site/apps/minis-sync/runtime/'+sha+'.tar.gz','versions':{'opencode':version_opencode,'zcode':version_zcode}}
 raw=json.dumps(payload,sort_keys=True,separators=(',',':')).encode();signature=subprocess.check_output(['openssl','dgst','-sha256','-sign',str(key)],input=raw)
 (out/'stable.json').write_text(json.dumps({'payload':base64.b64encode(raw).decode(),'signature':base64.b64encode(signature).decode()},indent=2)+'\n');print(json.dumps(payload))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--key',type=Path,required=True);p.add_argument('--sequence',type=int,required=True);p.add_argument('--opencode',required=True);p.add_argument('--zcode',required=True);a=p.parse_args();prepare(a.source,a.out,a.key,a.sequence,a.opencode,a.zcode)
