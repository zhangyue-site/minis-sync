"""Append one permission-protected JobService without reassigning existing resource IDs."""
import struct
from pathlib import Path
U=lambda b,p:struct.unpack_from('<I',b,p)[0]
def patch(data):
 b=bytearray(data);off=8;size=U(b,off+4);count=U(b,off+8);styles=U(b,off+12);flags=U(b,off+16);start=U(b,off+20);style_start=U(b,off+24)
 assert struct.unpack_from('<H',b,off)[0]==1 and not flags&0x100,'expected UTF-16 manifest pool'
 names=[]
 for i in range(count):
  p=off+start+U(b,off+28+i*4);length=struct.unpack_from('<H',b,p)[0];assert length<0x8000
  names.append(bytes(b[p+2:p+2+length*2]).decode('utf-16-le'))
 additions=['site.zhangyue.minis.sync.BackgroundSyncJob','android.permission.BIND_JOB_SERVICE','service','site.zhangyue.minis.sync.SyncExecutionService']
 assert additions[0] not in names,'background service already patched'
 encoded=bytearray();offsets=[]
 old_data_end=style_start or size
 for value in additions:
  offsets.append(old_data_end-start+len(encoded));encoded+=struct.pack('<H',len(value))+value.encode('utf-16-le')+b'\0\0'
 encoded+=b'\0'*(-len(encoded)%4)
 header=bytearray(b[off:off+28]);struct.pack_into('<I',header,4,size+4*len(additions)+len(encoded));struct.pack_into('<I',header,8,count+len(additions));struct.pack_into('<I',header,20,start+4*len(additions))
 if style_start:struct.pack_into('<I',header,24,style_start+4*len(additions)+len(encoded))
 pool=header+b[off+28:off+28+count*4]+b''.join(struct.pack('<I',v) for v in offsets)+b[off+28+count*4:off+old_data_end]+encoded+b[off+old_data_end:off+size]
 b[off:off+size]=pool;names+=additions
 pos=off+len(pool);resources={}
 insert=None
 while pos<len(b):
  typ,_,length=struct.unpack_from('<HHI',b,pos)
  assert length>=8
  if typ==0x180:
   resources={U(b,pos+8+i*4):i for i in range((length-8)//4) if U(b,pos+8+i*4)}
  if typ==0x103 and names[U(b,pos+20)]=='application':insert=pos
  pos+=length
 assert insert is not None
 uri=names.index('http://schemas.android.com/apk/res/android');none=0xffffffff
 def attr(resource,value,boolean=False):
  index=resources[resource]
  return struct.pack('<IIIHBBI',uri,index,none if boolean else names.index(value),8,0,0x12 if boolean else 3,1 if boolean else names.index(value))
 attrs=attr(0x1010003,additions[0])+attr(0x1010006,additions[1])+attr(0x1010010,None,True)
 service=names.index('service')
 start_node=struct.pack('<HHIIIIIHHHHHH',0x102,16,36+len(attrs),1,none,none,service,20,20,3,0,0,0)+attrs
 end_node=struct.pack('<HHIIIII',0x103,16,24,1,none,none,service)
 # Execution service is private; its dataSync type uses the existing framework attribute resource ID.
 fgs_resource=0x1010599
 if fgs_resource not in resources:
  raise AssertionError('base manifest lacks foregroundServiceType resource')
 execution_attrs=attr(0x1010003,additions[3])+struct.pack('<IIIHBBI',uri,resources[0x1010010],none,8,0,0x12,0)+struct.pack('<IIIHBBI',uri,resources[fgs_resource],none,8,0,0x11,1)
 execution=struct.pack('<HHIIIIIHHHHHH',0x102,16,36+len(execution_attrs),1,none,none,service,20,20,3,0,0,0)+execution_attrs+end_node
 b[insert:insert]=start_node+end_node+execution;struct.pack_into('<I',b,4,len(b));return bytes(b)
if __name__=='__main__':
 import sys
 p=Path(sys.argv[1]);p.write_bytes(patch(p.read_bytes()))
