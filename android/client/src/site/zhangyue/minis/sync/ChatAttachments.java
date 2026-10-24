package site.zhangyue.minis.sync;

import android.content.Context;
import android.system.ErrnoException;
import android.system.Os;
import android.system.OsConstants;
import java.io.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.*;
import org.json.*;

/** Existing native attachments are never moved or replaced; sync uses a durable logical projection. */
public final class ChatAttachments {
    public static String state(File snapshot) throws Exception {
        MessageDigest hash=MessageDigest.getInstance("SHA-256");
        try(ZipFile zip=new ZipFile(snapshot)) {
            for(Map.Entry<String,JSONObject> row:index(zip).entrySet())hash.update((row.getKey()+"\n"+NativeChatApply.canonical(row.getValue())+"\n").getBytes("UTF-8"));
        }
        StringBuilder value=new StringBuilder();for(byte b:hash.digest())value.append(String.format("%02x",b&255));return value.toString();
    }
    private static Map<String,JSONObject> index(ZipFile zip) throws Exception {
        Map<String,JSONObject> result=new TreeMap<>();Enumeration<? extends ZipEntry> es=zip.entries();
        while(es.hasMoreElements()){ZipEntry e=es.nextElement();if(e.getName().equals("files.index.jsonl")||e.getName().endsWith("/files.index.jsonl"))try(BufferedReader in=new BufferedReader(new InputStreamReader(zip.getInputStream(e),"UTF-8"))){String line;while((line=in.readLine())!=null){if(line.isEmpty())continue;JSONObject row=new JSONObject(line);if(!row.optString("category").equals("chats"))continue;String p=row.getString("path");if(!p.startsWith("chats/")||p.contains("\\")||p.contains("/../")||p.endsWith("/.."))throw new IOException("附件路径无效");if(!row.optBoolean("isDirectory",false)&&!row.optBoolean("skipped",false)){row.put("size",AttachmentMetadata.size(zip,row.getString("sha256"),row.has("size")&&!row.isNull("size")?Long.valueOf(row.getLong("size")):null));if(result.put(p,row)!=null)throw new IOException("附件路径重复");}}}}
        return result;
    }
    private static File target(Context context,String logical) throws Exception {
        File base=context.getFilesDir().getCanonicalFile();
        File root=new File(base,"minis-sessions").getCanonicalFile();
        if(!root.getPath().startsWith(base.getPath()+File.separator))throw new IOException("附件根目录越界");
        File file=new File(root,logical.substring(6)).getCanonicalFile();
        if(!file.getPath().startsWith(root.getPath()+File.separator))throw new IOException("附件路径越界");return file;
    }
    private static File controlled(Context c,String relative) throws Exception {
        File base=c.getFilesDir().getCanonicalFile(), f=new File(base,relative).getCanonicalFile();
        if(!f.getPath().startsWith(base.getPath()+File.separator))throw new IOException("附件目录越界");
        if(!f.isDirectory()&&!f.mkdirs())throw new IOException("无法创建附件目录");return f;
    }
    private static android.content.SharedPreferences prefs(Context c) throws Exception {
        String owner=c.getSharedPreferences("letian-sync",0).getString("owner","");
        if(owner.isEmpty())throw new IOException("同步账户缺失");
        return c.getSharedPreferences("minis-attachment-projection-"+owner,0);
    }
    private static void action(Context c,String path,String local,JSONObject cloud,File blob) throws Exception {
        JSONObject a=new JSONObject().put("logicalPath",path).put("localSha",local).put("cloudRow",cloud==null?JSONObject.NULL:cloud);
        if(blob!=null)a.put("blobFile",blob.getCanonicalPath());
        if(!prefs(c).edit().putString(path,a.toString()).commit())throw new IOException("无法持久保存附件同步状态");
    }
    private static String blobPath(String sha) throws Exception {
        if(!sha.matches("[a-f0-9]{64}"))throw new IOException("附件摘要无效");return "blobs/"+sha.substring(0,2)+"/"+sha;
    }
    private static void verify(File f,JSONObject row) throws Exception {
        if(!f.isFile()||f.length()!=row.getLong("size")||!CloudTransport.sha(f).equals(row.getString("sha256")))throw new IOException("附件校验失败");
    }
    private static synchronized File candidate(Context c,ZipFile zip,JSONObject row) throws Exception {
        String sha=row.getString("sha256"), path=blobPath(sha);
        File dir=controlled(c,"letian/conflicts/attachment-candidates"), result=new File(dir,sha+".blob");
        if(!result.getCanonicalFile().equals(result))throw new IOException("附件候选路径无效");
        if(result.exists()){verify(result,row);return result;}
        ZipEntry source=zip.getEntry(path);if(source==null)throw new IOException("云端附件缺失");
        File temp=File.createTempFile("attachment-",".partial",dir);
        try {
            try(InputStream in=zip.getInputStream(source);FileOutputStream out=new FileOutputStream(temp)) {
                byte[] b=new byte[65536];int n;long size=0;while((n=in.read(b))!=-1){size+=n;if(size>row.getLong("size"))throw new IOException("附件大小不符");out.write(b,0,n);}out.getFD().sync();
            }
            verify(temp,row);
            // This directory contains only our SHA-addressed verified candidates.
            // Serialized publication never touches a native attachment.
            if(result.exists()){verify(result,row);return result;}
            if(!temp.renameTo(result))throw new IOException("无法发布附件候选");
            syncDirectory(dir);
            verify(result,row);return result;
        }finally{temp.delete();}
    }
    private static void syncDirectory(File dir) throws Exception {
        java.io.FileDescriptor fd=Os.open(dir.getPath(),OsConstants.O_RDONLY,0);
        try{Os.fsync(fd);}finally{Os.close(fd);}
    }
    private static void createNative(Context c,File blob,File target) throws Exception {
        // Native code may edit an attachment in place, so never share the candidate inode.
        File temp=File.createTempFile("attachment-install-",".partial",controlled(c,"letian/attachment-projections"));
        try {
            try(InputStream in=new FileInputStream(blob);FileOutputStream out=new FileOutputStream(temp)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)out.write(b,0,n);out.getFD().sync();}
            if(!CloudTransport.sha(temp).equals(CloudTransport.sha(blob)))throw new IOException("附件安装校验失败");
            int error;
            try{error=NativeFiles.publish(temp.getPath(),target.getPath());}
            catch(LinkageError unavailable){throw new IOException("附件安全发布模块不可用，云端候选已保留",unavailable);}
            if(error!=0)throw new ErrnoException("attachment renameat2",error);
            syncDirectory(target.getParentFile());
        }finally{temp.delete();}
    }
    public static int apply(Context context,File desiredFile,File expectedFile) throws Exception {
        int conflicts=0;
        try(ZipFile desired=new ZipFile(desiredFile);ZipFile expected=new ZipFile(expectedFile)) {
            Map<String,JSONObject> next=index(desired),previous=index(expected);
            for(Map.Entry<String,JSONObject> entry:next.entrySet()) {
                JSONObject row=entry.getValue();String hash=row.getString("sha256");blobPath(hash);
                File target=target(context,entry.getKey());
                if(target.isFile()&&CloudTransport.sha(target).equals(hash))continue;
                File blob=candidate(context,desired,row);
                if(!target.exists()) {
                    if(previous.containsKey(entry.getKey()))throw new NativeChatApply.LocalChanged();
                    if(!target.getParentFile().isDirectory()&&!target.getParentFile().mkdirs())throw new IOException("无法创建附件目录");
                    // RENAME_NOREPLACE atomically publishes complete bytes without replacing a concurrent writer.
                    try{createNative(context,blob,target);continue;}
                    catch(ErrnoException e){if(e.errno!=OsConstants.EEXIST)throw e;}
                }
                if(!target.isFile())throw new IOException("附件目标不是普通文件");
                String actual=CloudTransport.sha(target);if(actual.equals(hash))continue;
                JSONObject before=previous.get(entry.getKey());
                boolean unchanged=before!=null&&actual.equals(before.optString("sha256"));
                String stored=prefs(context).getString(entry.getKey(),null);
                if(!unchanged&&before!=null&&stored!=null){JSONObject a=new JSONObject(stored);JSONObject cloud=a.optJSONObject("cloudRow");unchanged=actual.equals(a.optString("localSha"))&&cloud!=null&&cloud.optString("sha256").equals(before.optString("sha256"));}
                if(!unchanged&&before==null&&stored!=null){JSONObject a=new JSONObject(stored);unchanged=a.isNull("cloudRow")&&actual.equals(a.optString("localSha"));}
                if(!unchanged)throw new NativeChatApply.LocalChanged();
                action(context,entry.getKey(),actual,row,blob);conflicts++;
            }
            for(String path:previous.keySet())if(!next.containsKey(path)) {
                File target=target(context,path);if(!target.isFile())continue;
                String actual=CloudTransport.sha(target);
                boolean unchanged=actual.equals(previous.get(path).optString("sha256"));
                String stored=prefs(context).getString(path,null);
                if(!unchanged&&stored!=null){JSONObject a=new JSONObject(stored);JSONObject cloud=a.optJSONObject("cloudRow");unchanged=actual.equals(a.optString("localSha"))&&cloud!=null&&cloud.optString("sha256").equals(previous.get(path).optString("sha256"));}
                if(unchanged)action(context,path,actual,null,null);
            }
        }
        return conflicts;
    }
    private static byte[] read(ZipFile zip,String name) throws Exception {
        ZipEntry e=zip.getEntry(name);if(e==null)throw new IOException("备份缺少 "+name);
        try(InputStream in=zip.getInputStream(e);ByteArrayOutputStream out=new ByteArrayOutputStream()) {
            byte[] b=new byte[8192];int n;while((n=in.read(b))!=-1){if(out.size()+n>64*1024*1024)throw new IOException("附件索引过大");out.write(b,0,n);}return out.toByteArray();
        }
    }
    private static String hex(byte[] bytes){StringBuilder s=new StringBuilder();for(byte b:bytes)s.append(String.format("%02x",b&255));return s.toString();}
    private static void write(ZipOutputStream out,String name,InputStream in,JSONObject integrity) throws Exception {
        MessageDigest hash=MessageDigest.getInstance("SHA-256");ZipEntry e=new ZipEntry(name);e.setTime(0);out.putNextEntry(e);
        byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1){out.write(b,0,n);hash.update(b,0,n);}out.closeEntry();integrity.put(name,hex(hash.digest()));
    }
    /** rawNativeBackup remains a complete, untouched safety export. */
    public static File forSync(Context context,File rawNativeBackup) throws Exception {
        Map<String,?> actions=prefs(context).getAll();if(actions.isEmpty())return rawNativeBackup;
        File dir=controlled(context,"letian/attachment-projections"), output=File.createTempFile("projection-",".zip",dir);boolean done=false;
        try(ZipFile zip=new ZipFile(rawNativeBackup)) {
            JSONObject manifest=new JSONObject(new String(read(zip,"manifest.json"),"UTF-8"));
            Map<String,JSONObject> rows=index(zip);Map<String,File> additions=new TreeMap<>();Map<String,JSONObject> extraRows=new TreeMap<>();
            StringBuilder files=new StringBuilder();
            for(String line:new String(read(zip,"files.index.jsonl"),"UTF-8").split("\n")) {
                if(line.isEmpty())continue;JSONObject row=new JSONObject(line);String path=row.optString("path");Object stored=actions.get(path);
                if(rows.containsKey(path)&&stored instanceof String) {
                    JSONObject a=new JSONObject((String)stored);
                    if(path.equals(a.getString("logicalPath"))&&row.optString("sha256").equals(a.getString("localSha"))) {
                        if(a.isNull("cloudRow"))continue;
                        row=a.getJSONObject("cloudRow");if(!path.equals(row.getString("path"))||!"chats".equals(row.getString("category")))throw new IOException("附件投影路径无效");
                        String name=blobPath(row.getString("sha256"));File base=controlled(context,"letian/conflicts/attachment-candidates");
                        File blob=new File(a.getString("blobFile")).getCanonicalFile();
                        if(!blob.equals(new File(base,row.getString("sha256")+".blob")))throw new IOException("附件候选路径越界");verify(blob,row);
                        additions.put(name,blob);extraRows.put(row.getString("sha256"),new JSONObject().put("sha256",row.getString("sha256")).put("size",row.getLong("size")).put("path",row.getString("path")));
                    }
                }
                files.append(row.toString()).append('\n');
            }
            StringBuilder blobs=new StringBuilder();Set<String> known=new HashSet<>();
            for(String line:new String(read(zip,"blobs.index.jsonl"),"UTF-8").split("\n")){if(line.isEmpty())continue;JSONObject row=new JSONObject(line);String sha=row.getString("sha256");known.add(sha);JSONObject replacement=extraRows.get(sha);if(replacement!=null&&row.getLong("size")!=replacement.getLong("size"))throw new IOException("附件blob索引冲突");blobs.append(line).append('\n');}
            for(Map.Entry<String,JSONObject> row:extraRows.entrySet())if(!known.contains(row.getKey()))blobs.append(row.getValue().toString()).append('\n');
            JSONObject integrity=new JSONObject();Set<String> names=new HashSet<>();
            try(ZipOutputStream out=new ZipOutputStream(new FileOutputStream(output))) {
                Enumeration<? extends ZipEntry> es=zip.entries();while(es.hasMoreElements()) {
                    ZipEntry e=es.nextElement();String name=e.getName();if(!names.add(name)||name.startsWith("/")||Arrays.asList(name.split("/",-1)).contains("..")||name.contains("\\"))throw new IOException("备份路径无效");
                    if(name.endsWith("/manifest.json")||name.endsWith("/files.index.jsonl"))throw new IOException("不支持带前缀的备份");
                    if(e.isDirectory()||name.equals("manifest.json")||name.equals("files.index.jsonl")||name.equals("blobs.index.jsonl"))continue;
                    File verified=additions.get(name);
                    // Reuse a raw blob only after verifying its bytes against the candidate.
                    if(verified!=null){MessageDigest h=MessageDigest.getInstance("SHA-256");long count=0;try(InputStream in=zip.getInputStream(e)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1){h.update(b,0,n);count+=n;}}if(count!=verified.length()||!hex(h.digest()).equals(verified.getName().substring(0,64)))throw new IOException("原备份blob校验失败");}
                    try(InputStream in=zip.getInputStream(e)){write(out,name,in,integrity);}
                }
                for(Map.Entry<String,File> a:additions.entrySet())if(!names.contains(a.getKey()))try(InputStream in=new FileInputStream(a.getValue())){write(out,a.getKey(),in,integrity);}
                write(out,"files.index.jsonl",new ByteArrayInputStream(files.toString().getBytes("UTF-8")),integrity);
                write(out,"blobs.index.jsonl",new ByteArrayInputStream(blobs.toString().getBytes("UTF-8")),integrity);
                JSONObject categories=manifest.optJSONObject("categories");
                if(categories!=null){JSONObject chats=categories.optJSONObject("chats");if(chats!=null&&chats.has("files")){int count=0;for(String line:files.toString().split("\n")){if(line.isEmpty())continue;JSONObject row=new JSONObject(line);if("chats".equals(row.optString("category"))&&!row.optBoolean("isDirectory",false)&&!row.optBoolean("skipped",false))count++;}chats.put("files",count);}}
                manifest.put("integrity",integrity);out.putNextEntry(new ZipEntry("manifest.json"));out.write(manifest.toString().getBytes("UTF-8"));out.closeEntry();
            }
            done=true;return output;
        }finally{if(!done)output.delete();}
    }
}
