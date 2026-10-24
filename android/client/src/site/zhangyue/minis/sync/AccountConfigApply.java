package site.zhangyue.minis.sync;

import android.content.Context;
import java.io.*;
import java.lang.reflect.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.*;
import org.json.*;

/** Additive account restore. No bulk restore, no existing skill/key overwrite. */
public final class AccountConfigApply {
    private AccountConfigApply() {}
    private static byte[] read(ZipFile zip,String name,int limit)throws Exception {
        ZipEntry entry=zip.getEntry(name);if(entry==null)return null;
        if(entry.getSize()>limit)throw new IOException("配置成员过大");
        try(InputStream in=zip.getInputStream(entry);ByteArrayOutputStream out=new ByteArrayOutputStream()) {
            byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1){if(out.size()+n>limit)throw new IOException("配置成员过大");out.write(b,0,n);}return out.toByteArray();
        }
    }
    private static File safe(File root,String path)throws Exception {
        if(path.isEmpty()||path.startsWith("/")||path.contains("\\"))throw new IOException("配置路径无效");
        for(String part:path.split("/",-1))if(part.isEmpty()||part.equals(".")||part.equals(".."))throw new IOException("配置路径无效");
        File current=root.getCanonicalFile();for(String part:path.split("/")){current=new File(current,part);if(!current.getAbsoluteFile().equals(current.getCanonicalFile()))throw new IOException("配置符号链接已拒绝");}
        if(!current.getPath().startsWith(root.getCanonicalPath()+File.separator))throw new IOException("配置路径越界");return current;
    }
    private static File privateDir(File base,String name)throws Exception {
        File f=safe(base,name);if(!f.isDirectory()&&!f.mkdirs())throw new IOException("无法创建私有配置目录");return f;
    }
    private static void write(File f,byte[] raw)throws Exception {
        if(!f.getParentFile().isDirectory()&&!f.getParentFile().mkdirs())throw new IOException("无法创建配置目录");
        try(FileOutputStream out=new FileOutputStream(f)){out.write(raw);out.getFD().sync();}
    }
    private static boolean portable(String path){return !Arrays.asList(path.split("/")).contains(".bridge-token")&&!path.endsWith("/auth.json")&&!path.endsWith("/credentials.json");}
    private static JSONObject providers(ZipFile zip)throws Exception {
        byte[] raw=read(zip,"data/provider_config.json",8*1024*1024);
        if(raw==null)return null;JSONObject config=new JSONObject(new String(raw,"UTF-8"));
        JSONArray instances=config.optJSONArray("instances");if(instances==null)throw new IOException("供应商列表无效");
        Set<String> ids=new HashSet<>();
        for(int i=0;i<instances.length();i++){
            JSONObject row=instances.getJSONObject(i);String id=row.getString("id");
            if(id.isEmpty()||!ids.add(id))throw new IOException("供应商ID重复");
            String address=row.optString("customBaseURL","").toLowerCase(Locale.ROOT);
            if(address.startsWith("http://127.")||address.startsWith("http://localhost")||address.startsWith("http://[::1]"))row.put("isEnabled",false);
        }
        return config;
    }
    private static JSONObject secrets(ZipFile zip,JSONObject config)throws Exception {
        byte[] raw=read(zip,"secrets.json",8*1024*1024);JSONObject result=new JSONObject();JSONArray out=new JSONArray();
        if(raw!=null){JSONArray rows=new JSONObject(new String(raw,"UTF-8")).optJSONArray("providers");Set<String> ids=new HashSet<>();JSONArray instances=config.getJSONArray("instances");
            for(int i=0;i<instances.length();i++){JSONObject row=instances.getJSONObject(i);String address=row.optString("customBaseURL","").toLowerCase(Locale.ROOT);if(!address.startsWith("http://127.")&&!address.startsWith("http://localhost")&&!address.startsWith("http://[::1]"))ids.add(row.getString("id"));}
            if(rows!=null)for(int i=0;i<rows.length();i++){JSONObject row=rows.getJSONObject(i);if(!ids.contains(row.getString("instanceId")))continue;JSONObject selected=new JSONObject().put("instanceId",row.getString("instanceId"));
                // Generic private API keys are portable; OAuth/device sessions require fresh authorization.
                for(String k:new String[]{"label","providerType","apiKey"})if(row.has(k)&&!row.isNull(k))selected.put(k,row.get(k));out.put(selected);
            }
        }return result.put("v",1).put("providers",out);
    }
    public static int apply(Context context,File snapshot,String owner)throws Exception {
        AccountBinding.check(context,owner);
        File base=context.getFilesDir().getCanonicalFile();File stage=privateDir(base,"letian/account-stage-"+UUID.randomUUID());
        File conflicts=privateDir(base,"letian/account-conflicts/"+owner);int count=0;
        try(ZipFile zip=new ZipFile(snapshot)) {
            JSONObject config=providers(zip);
            if(config!=null){write(safe(stage,"data/provider_config.json"),config.toString().getBytes("UTF-8"));write(safe(stage,"secrets.json"),secrets(zip,config).toString().getBytes("UTF-8"));}
            // Stage and verify EVERY skill blob before any native settings are written.
            byte[] raw=read(zip,"files.index.jsonl",16*1024*1024);List<File[]> files=new ArrayList<>();Set<String> seen=new HashSet<>();
            if(raw!=null)for(String line:new String(raw,"UTF-8").split("\n")) {
                if(line.isEmpty())continue;JSONObject row=new JSONObject(line);if(!"skills".equals(row.optString("category"))||row.optBoolean("isDirectory",false)||row.optBoolean("skipped",false))continue;
                String path=row.getString("path");if(!path.startsWith("skills/")||!portable(path))continue;
                if(!seen.add(path))throw new IOException("技能路径重复");String sha=row.getString("sha256");if(!sha.matches("[a-f0-9]{64}"))throw new IOException("技能摘要无效");
                File target=safe(base,"minis-global/"+path),source=safe(stage,path);source.getParentFile().mkdirs();
                ZipEntry entry=zip.getEntry("blobs/"+sha.substring(0,2)+"/"+sha);if(entry==null)throw new IOException("技能内容缺失");
                long expected=AttachmentMetadata.size(zip,sha,row.has("size")&&!row.isNull("size")?Long.valueOf(row.getLong("size")):null);
                MessageDigest hash=MessageDigest.getInstance("SHA-256");long nbytes=0;
                try(InputStream in=zip.getInputStream(entry);FileOutputStream out=new FileOutputStream(source)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1){nbytes+=n;if(nbytes>expected||nbytes>64L*1024*1024)throw new IOException("技能文件超限");hash.update(b,0,n);out.write(b,0,n);}out.getFD().sync();}
                StringBuilder actual=new StringBuilder();for(byte b:hash.digest())actual.append(String.format("%02x",b&255));if(nbytes!=expected||!sha.equals(actual.toString()))throw new IOException("技能内容校验失败");
                files.add(new File[]{source,target});
            }
            AccountBinding.check(context,owner);
            if(config!=null){
                Object app=context.getApplicationContext();Object db=app.getClass().getField("f").get(app);Class<?> type=Class.forName("e3.r0",true,context.getClassLoader());
                Object importer=type.getConstructor(Context.class,Class.forName("com.openminis.app.data.db.AppDatabase",true,context.getClassLoader())).newInstance(context,db);
                Object report=type.getMethod("j",File.class).invoke(importer,stage);
                if(report.getClass().getField("n").get(report)!=null||report.getClass().getField("e").getInt(report)!=0)throw new IOException("部分供应商配置无法读取，原配置保留");
            }
            for(File[] pair:files){AccountBinding.check(context,owner);File source=pair[0],target=pair[1];target.getParentFile().mkdirs();
                if(target.exists()&&CloudTransport.sha(source).equals(CloudTransport.sha(target)))continue;
                int code=NativeFiles.publish(source.getCanonicalPath(),target.getCanonicalPath());
                if(code!=0){if(!target.exists())throw new IOException("技能写入失败，原数据保留");File backup=safe(conflicts,CloudTransport.sha(source)+".skill");if(!backup.exists()&&NativeFiles.publish(source.getCanonicalPath(),backup.getCanonicalPath())!=0)throw new IOException("无法保留技能冲突副本");count++;}
            }
            Object app=context.getApplicationContext();Field f=app.getClass().getDeclaredField("j");f.setAccessible(true);Object skills=f.get(app);
            // Reload via the native repository, not an unrelated filesystem-only index.
            skills.getClass().getMethod("y",String.class,boolean.class).invoke(skills,"account-sync",true);
            return count;
        }catch(InvocationTargetException e){Throwable cause=e.getCause();if(cause instanceof Exception)throw (Exception)cause;throw e;}
        finally{removeStage(stage);}
    }
    private static void removeStage(File f){if(f.isDirectory()){File[] a=f.listFiles();if(a!=null)for(File x:a)removeStage(x);}f.delete();}
}
