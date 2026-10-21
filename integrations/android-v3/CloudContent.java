package site.zhangyue.minis.sync;

import java.io.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.*;
import org.json.*;

/** A configuration fingerprint independent of applied chat rows and ZIP metadata. */
public final class CloudContent {
    public static String chatState(File file) throws Exception { return NativeChatApply.state(file)+":"+ChatAttachments.state(file); }
    public static String nonChatState(File file) throws Exception {
        MessageDigest hash=MessageDigest.getInstance("SHA-256");
        try(ZipFile zip=new ZipFile(file)) {
            List<String> names=new ArrayList<>();Enumeration<? extends ZipEntry> es=zip.entries();
            while(es.hasMoreElements()){ZipEntry e=es.nextElement();if(!e.isDirectory())names.add(e.getName());}Collections.sort(names);
            for(String name:names) {
                if(name.equals("manifest.json")||name.equals("blobs.index.jsonl")||name.startsWith("blobs/")||name.startsWith("sync/")||name.matches("data/(sessions|messages|folders|compact_markers)(?:-[^/]+)?\\.jsonl"))continue;
                ZipEntry e=zip.getEntry(name);hash.update(name.getBytes("UTF-8"));hash.update((byte)0);
                if(name.equals("files.index.jsonl")) {
                    TreeMap<String,String> rows=new TreeMap<>();
                    try(BufferedReader in=new BufferedReader(new InputStreamReader(zip.getInputStream(e),"UTF-8"))) {
                        String line;while((line=in.readLine())!=null){if(line.isEmpty())continue;JSONObject row=new JSONObject(line);if(!row.optString("category").equals("chats")&&!row.optString("category").equals("shared_files"))rows.put(row.optString("category")+"/"+row.getString("path"),NativeChatApply.canonical(row));}
                    }
                    for(String row:rows.values())hash.update((row+"\n").getBytes("UTF-8"));
                } else try(InputStream in=zip.getInputStream(e)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)hash.update(b,0,n);}
                hash.update((byte)0);
            }
        }
        StringBuilder out=new StringBuilder();for(byte b:hash.digest())out.append(String.format("%02x",b&255));return out.toString();
    }
}
