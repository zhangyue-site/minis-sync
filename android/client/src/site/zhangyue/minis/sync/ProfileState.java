package site.zhangyue.minis.sync;
import java.io.*;
import java.util.*;
import java.util.zip.*;
import org.json.*;

/** A profile ACK proves portable rows/files were applied; never authorizes deletion. */
public final class ProfileState {
    private ProfileState(){}
    private static byte[] read(ZipFile z,String name)throws Exception {
        ZipEntry e=z.getEntry(name);if(e==null)return null;if(e.getSize()>16*1024*1024)throw new IOException("配置元数据超限");
        try(InputStream in=z.getInputStream(e);ByteArrayOutputStream out=new ByteArrayOutputStream()){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1){if(out.size()+n>16*1024*1024)throw new IOException("配置元数据超限");out.write(b,0,n);}return out.toByteArray();}
    }
    private static JSONObject object(ZipFile z,String name)throws Exception{byte[] b=read(z,name);return b==null?new JSONObject():new JSONObject(new String(b,"UTF-8"));}
    private static String key(JSONObject row,String type)throws Exception {
        if(type.equals("modelEntries"))return row.getString("providerInstanceId")+"/"+row.getJSONObject("model").getString("id");
        return row.getString(type.equals("providers")?"instanceId":"id");
    }
    private static Map<String,JSONObject> rows(JSONObject o,String type)throws Exception {
        Map<String,JSONObject> result=new HashMap<>();JSONArray list=o.optJSONArray(type);if(list!=null)for(int i=0;i<list.length();i++){JSONObject row=list.getJSONObject(i);if(type.equals("instances")&&AccountPackage.localProvider(row))continue;result.put(key(row,type),row);}return result;
    }
    private static Map<String,String> skills(ZipFile z)throws Exception {
        Map<String,String> result=new HashMap<>();byte[] b=read(z,"files.index.jsonl");if(b==null)return result;
        for(String line:new String(b,"UTF-8").split("\n")){if(line.isEmpty())continue;JSONObject r=new JSONObject(line);String p=r.optString("path");
            if("skills".equals(r.optString("category"))&&!r.optBoolean("isDirectory",false)&&!r.optBoolean("skipped",false)&&!p.endsWith("/.bridge-token")&&!p.endsWith("/auth.json")&&!p.endsWith("/credentials.json"))result.put(p,r.getString("sha256"));}
        return result;
    }
    public static boolean matches(File local,File remote)throws Exception {
        try(ZipFile l=new ZipFile(local);ZipFile c=new ZipFile(remote)) {
            JSONObject actual=object(l,"data/provider_config.json"),desired=object(c,"data/provider_config.json");
            Set<String> portableIds=rows(desired,"instances").keySet();
            for(String type:new String[]{"instances","modelEntries","modelGroups","subAgents"}){
                Map<String,JSONObject> left=rows(actual,type),right=rows(desired,type);
                for(Map.Entry<String,JSONObject> item:right.entrySet()){
                    if(type.equals("modelEntries")&&!portableIds.contains(item.getValue().getString("providerInstanceId")))continue;
                    if(!left.containsKey(item.getKey())||!NativeChatApply.canonical(left.get(item.getKey())).equals(NativeChatApply.canonical(item.getValue())))return false;
                }
            }
            Map<String,JSONObject> keys=rows(object(l,"secrets.json"),"providers");
            for(Map.Entry<String,JSONObject> item:rows(object(c,"secrets.json"),"providers").entrySet()){
                JSONObject row=item.getValue();if(!portableIds.contains(item.getKey())||!row.has("apiKey")||row.isNull("apiKey"))continue;
                if(!keys.containsKey(item.getKey())||!Objects.equals(keys.get(item.getKey()).opt("apiKey"),row.opt("apiKey")))return false;
            }
            Map<String,String> files=skills(l);for(Map.Entry<String,String> e:skills(c).entrySet())if(!e.getValue().equals(files.get(e.getKey())))return false;
            return true;
        }
    }
}
