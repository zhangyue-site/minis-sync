package site.zhangyue.minis.sync;

import java.io.*;
import java.net.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.*;
import org.json.*;

/** Version 3: changes are relative to this device's applied cloud version. */
public final class CloudTransport {
    private final String server,token;
    public CloudTransport(String server,String token) { this.server=server;this.token=token; }
    public JSONObject head() throws Exception { return SyncTransport.request(server+"/api/minis/v3/head",token,null); }
    public JSONObject ack(String device,String sha) throws Exception {
        return postEmpty("/api/minis/v3/ack?device="+enc(device)+"&sha="+sha+"&scope=workspace");
    }
    public JSONObject commit(String device,String base,String operation,File body,String mode) throws Exception {
        return SyncTransport.request(server+"/api/minis/v3/commit?device="+enc(device)+"&base="+enc(base)+"&op="+enc(operation)+"&mode="+mode+"&scope=workspace&name=sync.minisbak",token,body);
    }
    private JSONObject postEmpty(String path) throws Exception {
        File empty=File.createTempFile("minis-ack-",".tmp");
        try { return SyncTransport.request(server+path,token,empty); }finally{empty.delete();}
    }
    private static String enc(String s) throws Exception { return URLEncoder.encode(s,"UTF-8"); }
    public void download(String sha,long bytes,File destination) throws Exception {
        if(!sha.matches("[a-f0-9]{64}")||bytes<=0||bytes>2L*1024*1024*1024)throw new IOException("云端备份信息无效");
        File partial=new File(destination+".partial");
        HttpURLConnection c=(HttpURLConnection)new URL(server+"/api/minis/v3/snapshots/"+sha).openConnection();
        c.setConnectTimeout(15000);c.setReadTimeout(600000);c.setInstanceFollowRedirects(false);c.setRequestProperty("Authorization","Bearer "+token);
        try {
            int status=c.getResponseCode();
            if(status!=200)throw new SyncTransport.HttpFailure(status,"snapshot_not_ready",5);
            MessageDigest hash=MessageDigest.getInstance("SHA-256");long total=0;
            try(InputStream in=c.getInputStream();FileOutputStream out=new FileOutputStream(partial)) {
                byte[] buffer=new byte[65536];int n;while((n=in.read(buffer))!=-1){total+=n;if(total>bytes)throw new IOException("云端下载大小超限");out.write(buffer,0,n);hash.update(buffer,0,n);}out.getFD().sync();
            }
            if(total!=bytes||!sha.equals(hex(hash.digest())))throw new IOException("云端备份校验失败，原数据已保留");
            if(!partial.renameTo(destination))throw new IOException("无法保存云端备份");
        } finally { c.disconnect();partial.delete(); }
    }
    public static String sha(File file) throws Exception {
        MessageDigest hash=MessageDigest.getInstance("SHA-256");try(InputStream in=new FileInputStream(file)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)hash.update(b,0,n);}return hex(hash.digest());
    }
    private static String hex(byte[] bytes) {StringBuilder s=new StringBuilder();for(byte b:bytes)s.append(String.format("%02x",b&255));return s.toString();}
    /** Returns null only when a complete upload is smaller or there is no baseline. */
    public static File delta(File baseline,String base,File local,File destination) throws Exception {
        if(baseline==null||!baseline.isFile())return null;
        try(ZipFile old=new ZipFile(baseline);ZipFile current=new ZipFile(local)) {
            Map<String,ZipEntry> before=new TreeMap<>();Enumeration<? extends ZipEntry> entries=old.entries();while(entries.hasMoreElements()){ZipEntry e=entries.nextElement();if(!e.isDirectory())before.put(e.getName(),e);}
            List<ZipEntry> changed=new ArrayList<>();entries=current.entries();
            while(entries.hasMoreElements()){ZipEntry e=entries.nextElement();if(e.isDirectory())continue;ZipEntry previous=before.remove(e.getName());if(previous==null||previous.getCrc()!=e.getCrc()||previous.getSize()!=e.getSize())changed.add(e);}
            JSONObject control=new JSONObject().put("v",2).put("base",base).put("deleted",new JSONArray(before.keySet()));
            try(ZipOutputStream out=new ZipOutputStream(new FileOutputStream(destination))) {
                ZipEntry entry=new ZipEntry("__delta__.json");entry.setTime(0);out.putNextEntry(entry);out.write(control.toString().getBytes("UTF-8"));out.closeEntry();
                byte[] buffer=new byte[65536];for(ZipEntry e:changed){entry=new ZipEntry(e.getName());entry.setTime(0);out.putNextEntry(entry);try(InputStream in=current.getInputStream(e)){int n;while((n=in.read(buffer))!=-1)out.write(buffer,0,n);}out.closeEntry();}
            }
            if(destination.length()>=local.length()||destination.length()>256L*1024*1024){destination.delete();return null;}
            return destination;
        }
    }
}
