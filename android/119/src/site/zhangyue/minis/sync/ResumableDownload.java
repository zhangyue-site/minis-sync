package site.zhangyue.minis.sync;
import java.io.*;
import java.net.*;
import java.security.MessageDigest;
/** Digest-addressed checkpoints are private caches, never published before full verification. */
public final class ResumableDownload {
    public interface Progress {void bytes(long count)throws IOException;}
    public interface Control {void check()throws IOException;void opened(HttpURLConnection c);void closed(HttpURLConnection c);}
    public static File partial(File destination,String sha){return new File(destination.getParentFile(),sha+".download.partial");}
    public static void fetch(String address,String token,String sha,long bytes,File destination,Progress progress,Control control)throws Exception {
        if(!sha.matches("[a-f0-9]{64}")||bytes<=0||bytes>2L*1024*1024*1024)throw new IOException("云端备份信息无效");
        File part=partial(destination,sha);
        if(!part.getCanonicalFile().equals(new File(part.getParentFile().getCanonicalFile(),part.getName())))throw new IOException("下载暂存路径无效");
        if(part.length()>bytes&&!part.delete())throw new IOException("无法重置下载缓存");
        long start=part.length();
        if(start<bytes) {
            if(control!=null)control.check();
            HttpURLConnection c=(HttpURLConnection)new URL(address).openConnection();
            c.setConnectTimeout(15000);c.setReadTimeout(600000);c.setInstanceFollowRedirects(false);
            c.setRequestProperty("Authorization","Bearer "+token);c.setRequestProperty("Accept-Encoding","identity");
            if(start>0)c.setRequestProperty("Range","bytes="+start+"-");
            if(control!=null)control.opened(c);
            try {
                int status=c.getResponseCode();
                if(status==200)start=0;
                else if(status==206) {
                    String expected="bytes "+start+"-"+(bytes-1)+"/"+bytes;
                    if(!expected.equals(c.getHeaderField("Content-Range")))throw new IOException("下载续传范围不符");
                } else throw new IOException("下载请求未完成（HTTP "+status+"）");
                long total=start;
                try(InputStream in=c.getInputStream();FileOutputStream out=new FileOutputStream(part,start>0)) {
                    if(progress!=null)progress.bytes(total);
                    byte[] buffer=new byte[65536];int n;
                    while((n=in.read(buffer))!=-1) {
                        if(control!=null)control.check();
                        total+=n;if(total>bytes){out.close();part.delete();throw new IOException("下载大小超限");}
                        out.write(buffer,0,n);if(progress!=null)progress.bytes(total);
                    }
                    out.getFD().sync();
                }
                if(total!=bytes)throw new IOException("下载尚未完成，已保留断点");
            }finally{if(control!=null)control.closed(c);c.disconnect();}
        }
        if(control!=null)control.check();
        MessageDigest digest=MessageDigest.getInstance("SHA-256");
        try(InputStream in=new FileInputStream(part)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1){if(control!=null)control.check();digest.update(b,0,n);}}
        StringBuilder hash=new StringBuilder();for(byte b:digest.digest())hash.append(String.format("%02x",b&255));
        if(part.length()!=bytes||!sha.equals(hash.toString())){part.delete();throw new IOException("云端备份校验失败，原数据已保留");}
        if(!part.renameTo(destination))throw new IOException("无法发布已验证下载");
    }
}
