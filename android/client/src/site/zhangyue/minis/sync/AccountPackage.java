package site.zhangyue.minis.sync;
import android.content.Context;
import java.io.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.*;
import org.json.*;

/** Account-portable view. The untouched native export remains the safety copy. */
public final class AccountPackage {
    private AccountPackage(){}
    private static byte[] read(ZipFile z,String path,int limit)throws Exception {
        ZipEntry e=z.getEntry(path);if(e==null)return null;
        try(InputStream in=z.getInputStream(e);ByteArrayOutputStream out=new ByteArrayOutputStream()){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1){if(out.size()+n>limit)throw new IOException("账号配置元数据超限");out.write(b,0,n);}return out.toByteArray();}
    }
    public static boolean localProvider(JSONObject p)throws Exception {
        String s=p.optString("customBaseURL","").toLowerCase(Locale.ROOT);
        return s.startsWith("http://127.")||s.startsWith("http://localhost")||s.startsWith("http://[::1]");
    }
    public static File project(Context context,File source)throws Exception {
        File folder=new File(context.getCacheDir(),"minis-account-exports");folder.mkdirs();File target=File.createTempFile("portable-",".minisbak",folder);boolean done=false;
        try(ZipFile z=new ZipFile(source)) {
            byte[] manifestRaw=read(z,"manifest.json",8*1024*1024);if(manifestRaw==null)throw new IOException("仅支持已核实的无前缀原生导出");JSONObject manifest=new JSONObject(new String(manifestRaw,"UTF-8"));
            Map<String,byte[]> changes=new HashMap<>();Set<String> used=new HashSet<>(),excluded=new HashSet<>();
            byte[] providers=read(z,"data/provider_config.json",8*1024*1024);
            if(providers!=null){JSONObject p=new JSONObject(new String(providers,"UTF-8"));JSONArray instances=p.optJSONArray("instances"),kept=new JSONArray();
                if(instances!=null)for(int i=0;i<instances.length();i++){JSONObject row=instances.getJSONObject(i);if(localProvider(row))excluded.add(row.getString("id"));else kept.put(row);}p.put("instances",kept);
                JSONArray models=p.optJSONArray("modelEntries"),m=new JSONArray();Set<String> removedModels=new HashSet<>();
                if(models!=null)for(int i=0;i<models.length();i++){JSONObject row=models.getJSONObject(i);if(excluded.contains(row.optString("providerInstanceId")))removedModels.add(row.optString("uuid"));else m.put(row);}p.put("modelEntries",m);
                JSONArray groups=p.optJSONArray("modelGroups");if(groups!=null)for(int i=0;i<groups.length();i++){JSONObject row=groups.getJSONObject(i);JSONArray ids=row.optJSONArray("modelEntryIds");if(ids!=null){JSONArray keep=new JSONArray();for(int j=0;j<ids.length();j++)if(!removedModels.contains(ids.getString(j)))keep.put(ids.getString(j));row.put("modelEntryIds",keep);}}
                changes.put("data/provider_config.json",p.toString().getBytes("UTF-8"));
            }
            // The sync export omits secrets. Read portable API keys from the native
            // repository, leaving the raw safety export and device OAuth untouched.
            JSONObject privateKeys=new JSONObject().put("v",1);JSONArray keyRows=new JSONArray();
            if(providers!=null){
                Object app=context.getApplicationContext();java.lang.reflect.Field field=app.getClass().getDeclaredField("h");field.setAccessible(true);Object repository=field.get(app);
                if(repository==null)throw new IOException("供应商仓库尚未初始化");
                JSONArray instances=new JSONObject(new String(changes.get("data/provider_config.json"),"UTF-8")).getJSONArray("instances");
                for(int i=0;i<instances.length();i++){JSONObject row=instances.getJSONObject(i);String id=row.getString("id");
                    String key=(String)repository.getClass().getMethod("A",String.class).invoke(repository,id);
                    JSONObject entry=new JSONObject().put("instanceId",id).put("label",row.optString("label")).put("providerType",row.optString("providerType"));
                    if(key!=null&&!key.isEmpty())entry.put("apiKey",android.util.Base64.encodeToString(key.getBytes("UTF-8"),android.util.Base64.NO_WRAP));
                    keyRows.put(entry);
                }
            }
            privateKeys.put("providers",keyRows);changes.put("secrets.json",privateKeys.toString().getBytes("UTF-8"));
            byte[] index=read(z,"files.index.jsonl",16*1024*1024);
            if(index!=null){StringBuilder out=new StringBuilder();for(String line:new String(index,"UTF-8").split("\n")){if(line.isEmpty())continue;JSONObject row=new JSONObject(line);String path=row.optString("path");if(path.endsWith("/.bridge-token")||path.endsWith("/auth.json")||path.endsWith("/credentials.json"))continue;String sha=row.optString("sha256");if(!sha.isEmpty())used.add(sha);out.append(row.toString()).append('\n');}changes.put("files.index.jsonl",out.toString().getBytes("UTF-8"));}
            byte[] blobs=read(z,"blobs.index.jsonl",16*1024*1024);
            if(index!=null&&blobs!=null){StringBuilder out=new StringBuilder();for(String line:new String(blobs,"UTF-8").split("\n")){if(line.isEmpty())continue;JSONObject row=new JSONObject(line);if(used.contains(row.getString("sha256")))out.append(line).append('\n');}changes.put("blobs.index.jsonl",out.toString().getBytes("UTF-8"));}
            JSONObject integrity=new JSONObject();
            try(ZipOutputStream out=new ZipOutputStream(new FileOutputStream(target))) {
                Enumeration<? extends ZipEntry> es=z.entries();while(es.hasMoreElements()){ZipEntry e=es.nextElement();String name=e.getName();if(e.isDirectory()||name.equals("manifest.json"))continue;
                    if(index!=null&&name.startsWith("blobs/")&&!used.contains(name.substring(name.lastIndexOf('/')+1)))continue;
                    MessageDigest hash=MessageDigest.getInstance("SHA-256");out.putNextEntry(new ZipEntry(name));byte[] replacement=changes.get(name);
                    if(replacement!=null){out.write(replacement);hash.update(replacement);}else try(InputStream in=z.getInputStream(e)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1){out.write(b,0,n);hash.update(b,0,n);}}
                    out.closeEntry();StringBuilder hex=new StringBuilder();for(byte b:hash.digest())hex.append(String.format("%02x",b&255));integrity.put(name,hex.toString());
                }
                for(Map.Entry<String,byte[]> added:changes.entrySet())if(z.getEntry(added.getKey())==null){
                    out.putNextEntry(new ZipEntry(added.getKey()));out.write(added.getValue());out.closeEntry();
                    MessageDigest hash=MessageDigest.getInstance("SHA-256");hash.update(added.getValue());StringBuilder hex=new StringBuilder();for(byte b:hash.digest())hex.append(String.format("%02x",b&255));integrity.put(added.getKey(),hex.toString());
                }
                manifest.put("integrity",integrity);out.putNextEntry(new ZipEntry("manifest.json"));out.write(manifest.toString().getBytes("UTF-8"));out.closeEntry();
            }
            done=true;return target;
        }finally{if(!done)target.delete();}
    }
}
