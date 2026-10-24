package site.zhangyue.minis.sync;

import java.io.*;
import java.security.MessageDigest;
import org.json.*;

/** Byte-exact inverse of the server's MSD1 format; never mutates the base. */
public final class DownloadDelta {
    private DownloadDelta() {}
    private static final byte[] MAGIC={'M','S','D','1',13,10,26,10};
    private static long integer(JSONObject o,String key)throws Exception {
        Object value=o.get(key);if(!(value instanceof Number))throw new IOException("增量整数无效");
        long n=((Number)value).longValue();if(((Number)value).doubleValue()!=n)throw new IOException("增量整数无效");return n;
    }
    public static void apply(File base,File delta,String baseSha,String targetSha,long bytes,File destination,ResumableDownload.Control control)throws Exception {
        if(!baseSha.matches("[a-f0-9]{64}")||!targetSha.matches("[a-f0-9]{64}")||bytes<=0||bytes>2L*1024*1024*1024)throw new IOException("增量目标无效");
        if(!CloudTransport.sha(base).equals(baseSha))throw new IOException("本机增量基线校验失败，原数据保留");
        if(destination.exists())throw new IOException("目标已存在，保留原文件");
        File temp=File.createTempFile(".delta-applying-",".tmp",destination.getParentFile());
        try(RandomAccessFile src=new RandomAccessFile(base,"r");RandomAccessFile wire=new RandomAccessFile(delta,"r");FileOutputStream out=new FileOutputStream(temp)) {
            byte[] magic=new byte[8];wire.readFully(magic);if(!java.util.Arrays.equals(magic,MAGIC))throw new IOException("增量格式无效");
            int length=wire.readInt();if(length<=0||length>8*1024*1024)throw new IOException("增量头超限");
            byte[] raw=new byte[length];wire.readFully(raw);JSONObject h=new JSONObject(new String(raw,"UTF-8"));long payload=wire.getFilePointer();
            if(integer(h,"v")!=1||!baseSha.equals(h.getString("base"))||!targetSha.equals(h.getString("target"))||integer(h,"bytes")!=bytes)throw new IOException("增量版本不匹配");
            long payloadBytes=integer(h,"payload_bytes");if(payloadBytes<0||payloadBytes!=wire.length()-payload)throw new IOException("增量正文大小无效");
            JSONArray steps=h.getJSONArray("steps");if(steps.length()>250000)throw new IOException("增量步骤超限");
            long total=0,dataPosition=0;MessageDigest hash=MessageDigest.getInstance("SHA-256");byte[] buffer=new byte[65536];
            for(int i=0;i<steps.length();i++) {
                if(control!=null)control.check();JSONArray s=steps.getJSONArray(i);
                if(s.length()!=3||!(s.get(1) instanceof Number)||!(s.get(2) instanceof Number))throw new IOException("增量步骤无效");
                String kind=s.getString(0);long offset=s.getLong(1),count=s.getLong(2);
                if(offset<0||count<=0||count>bytes-total)throw new IOException("增量范围无效");
                RandomAccessFile input;
                if(kind.equals("b")){if(offset>src.length()||count>src.length()-offset)throw new IOException("增量基线越界");input=src;input.seek(offset);}
                else if(kind.equals("d")){if(offset!=dataPosition||count>payloadBytes-offset)throw new IOException("增量正文越界");dataPosition+=count;input=wire;input.seek(payload+offset);}
                else throw new IOException("未知增量操作");
                long left=count;while(left>0){if(control!=null)control.check();int n=input.read(buffer,0,(int)Math.min(left,buffer.length));if(n<0)throw new EOFException("增量尚未完整下载");out.write(buffer,0,n);hash.update(buffer,0,n);left-=n;}total+=count;
            }
            StringBuilder actual=new StringBuilder();for(byte b:hash.digest())actual.append(String.format("%02x",b&255));
            if(total!=bytes||dataPosition!=payloadBytes||!targetSha.equals(actual.toString()))throw new IOException("增量重建校验失败，原数据保留");
            out.getFD().sync();
            if(NativeFiles.publish(temp.getCanonicalPath(),destination.getCanonicalPath())!=0)throw new IOException("无法发布已校验的云端版本");
        }finally{temp.delete();}
    }
}
