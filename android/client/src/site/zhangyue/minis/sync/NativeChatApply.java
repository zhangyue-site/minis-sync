package site.zhangyue.minis.sync;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import java.io.*;
import java.time.Instant;
import java.util.*;
import java.util.zip.*;
import org.json.*;

/** Transactional chat application. Never clears the database or replaces a parent row. */
public final class NativeChatApply {
    private static final String[] TABLES={"folders","sessions","messages","compact_markers"};
    private static final String[][] FIELDS={
        {"id","name","icon","color","origin","sort_index","pinned_at","description","created_at","updated_at"},
        {"id","title","model_id","created_at","updated_at","category","last_message","model_binding","source","memory_enabled","pinned_at","edit_count","thinking_override","folder_id","parent_session_id","parent_tool_use_id"},
        {"id","session_id","role","parts_json","created_at","token_usage","sort_order","reasoning_content","stream_interrupt_count","model_id","model_display_name","provider_type","provider_instance_id"},
        {"id","session_id","summary","first_kept_sort_order","compacted_count","created_at","ui_boundary_sort_order","boundary_message_id","first_kept_message_id","last_compacted_message_id","version"}
    };
    private static String camel(String key) {
        if(key.equals("parts_json"))return "parts";
        if(key.equals("description"))return "desc";
        StringBuilder out=new StringBuilder();boolean upper=false;
        for(char c:key.toCharArray()) { if(c=='_')upper=true;else {out.append(upper?Character.toUpperCase(c):c);upper=false;} }
        return out.toString();
    }
    private static boolean time(String key) { return key.equals("created_at")||key.equals("updated_at")||key.equals("pinned_at"); }
    private static Object normalize(Object v,String key) throws Exception {
        if(v==null||v==JSONObject.NULL)return null;
        if(time(key))return v instanceof Number ? ((Number)v).longValue()/1000 : Instant.parse(v.toString()).toEpochMilli()/1000;
        if(v instanceof Boolean)return (Boolean)v?1L:0L;
        if(v instanceof Number)return ((Number)v).longValue();
        if(v instanceof JSONObject||v instanceof JSONArray)return canonical(v);
        // JSON stored in text columns must compare by value, not whitespace/order.
        if(key.equals("parts_json")||key.equals("model_binding")||key.equals("token_usage")||key.equals("thinking_override")) {
            String s=v.toString();
            if(s.startsWith("{")||s.startsWith("["))return canonical(new JSONTokener(s).nextValue());
        }
        return v.toString();
    }
    public static String canonical(Object v) throws Exception {
        if(v instanceof JSONObject) {
            JSONObject o=(JSONObject)v;TreeSet<String> keys=new TreeSet<>();Iterator<String> it=o.keys();while(it.hasNext())keys.add(it.next());
            StringBuilder s=new StringBuilder("{");for(String k:keys) {if(s.length()>1)s.append(',');s.append(JSONObject.quote(k)).append(':').append(canonical(o.get(k)));}return s.append('}').toString();
        }
        if(v instanceof JSONArray) {JSONArray a=(JSONArray)v;StringBuilder s=new StringBuilder("[");for(int i=0;i<a.length();i++){if(i>0)s.append(',');s.append(canonical(a.get(i)));}return s.append(']').toString();}
        if(v instanceof Number)return new java.math.BigDecimal(v.toString()).stripTrailingZeros().toPlainString();
        return v==null||v==JSONObject.NULL?"null":v instanceof String?JSONObject.quote((String)v):v.toString();
    }
    private static final String[] TYPES={"FolderV2","SessionV2","MessageV2","CompactMarkerV2"};
    /** Own private scratch database; never opens or deletes an application database. */
    private static final class Stage implements AutoCloseable {
        final File file; SQLiteDatabase db;
        Stage(File snapshot) throws Exception {
            file=File.createTempFile("chat-stage-", ".sqlite", snapshot.getAbsoluteFile().getParentFile());
            try {
                db=SQLiteDatabase.openOrCreateDatabase(file,null);
                db.execSQL("CREATE TABLE records (table_name TEXT NOT NULL,id TEXT NOT NULL,id_sort TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(table_name,id)) WITHOUT ROWID");
                db.beginTransaction();
                try(ZipFile zip=new ZipFile(snapshot)) {
                    Enumeration<? extends ZipEntry> entries=zip.entries();
                    while(entries.hasMoreElements()) {
                        ZipEntry e=entries.nextElement();String n=e.getName();
                        for(int t=0;t<TABLES.length;t++)if(n.matches("(?:[^/]+/)?data/"+TABLES[t]+"(?:-[^/]+)?\\.jsonl")) {
                            try(InputStream in=new BufferedInputStream(zip.getInputStream(e))) {
                                String line;while((line=line(in))!=null) {
                                    if(line.isEmpty())continue;
                                    JSONObject row=new JSONObject(line);
                                    if(row.getInt("v")!=1||!TYPES[t].equals(row.getString("t")))throw new IOException("不支持的同步记录版本");
                                    JSONObject d=row.getJSONObject("d");
                                    if(t==1) {
                                        JSONObject flat=d.getJSONObject("session");
                                        Iterator<String> keys=d.keys();while(keys.hasNext()){String k=keys.next();if(!k.equals("session"))flat.put(k,d.get(k));}d=flat;
                                    }
                                    Object identity=d.get("id");if(!(identity instanceof String)||((String)identity).isEmpty())throw new IOException("同步记录ID无效");
                                    ContentValues v=new ContentValues();v.put("table_name",TABLES[t]);v.put("id",(String)identity);v.put("id_sort",sortKey((String)identity));v.put("data",d.toString());
                                    db.insertOrThrow("records",null,v);
                                }
                            }
                        }
                    }
                    db.setTransactionSuccessful();
                } finally {db.endTransaction();}
            } catch(Exception ex) {close();throw ex;}
        }
        Cursor rows(String table) {return db.rawQuery("SELECT id FROM records WHERE table_name=? ORDER BY id_sort COLLATE BINARY",new String[]{table});}
        JSONObject get(String table,String id) throws Exception {
            StringBuilder data=new StringBuilder();
            for(int offset=1;;offset+=65536)try(Cursor c=db.rawQuery("SELECT substr(data,?,65536) FROM records WHERE table_name=? AND id=?",new String[]{String.valueOf(offset),table,id})) {
                if(!c.moveToNext())return null;String part=c.getString(0);data.append(part);if(part.length()<65536)return new JSONObject(data.toString());
            }
        }

        boolean has(String table,String id) {
            try(Cursor c=db.rawQuery("SELECT 1 FROM records WHERE table_name=? AND id=?",new String[]{table,id})){return c.moveToNext();}
        }
        long count(String table) {try(Cursor c=db.rawQuery("SELECT count(*) FROM records WHERE table_name=?",new String[]{table})){c.moveToNext();return c.getLong(0);}}
        public void close() {
            try {if(db!=null)db.close();} finally {
                file.delete();new File(file.getPath()+"-journal").delete();new File(file.getPath()+"-wal").delete();new File(file.getPath()+"-shm").delete();
            }
        }
    }
    // Match Java String UTF-16 ordering even for supplementary IDs.
    private static String sortKey(String id) {
        StringBuilder key=new StringBuilder();String hex="0123456789abcdef";
        for(int i=0;i<id.length();i++){int c=id.charAt(i);for(int shift=12;shift>=0;shift-=4)key.append(hex.charAt((c>>shift)&15));}
        return key.toString();
    }
    // Bound bytes before decoding; BufferedReader.readLine allocates without a bound.
    private static String line(InputStream in) throws Exception {
        ByteArrayOutputStream out=new ByteArrayOutputStream();int ch;
        while((ch=in.read())!=-1&&ch!='\n') {if(out.size()>=8*1024*1024)throw new IOException("同步记录过大");out.write(ch);}
        if(ch==-1&&out.size()==0)return null;
        return java.nio.charset.StandardCharsets.UTF_8.newDecoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT).decode(java.nio.ByteBuffer.wrap(out.toByteArray())).toString();
    }
    private static JSONObject live(SQLiteDatabase db,int t,String id) throws Exception {
        try(Cursor c=db.query(TABLES[t],FIELDS[t],"id=?",new String[]{id},null,null,null)) {
            if(!c.moveToNext())return null;JSONObject d=new JSONObject();
            for(int i=0;i<FIELDS[t].length;i++)d.put(camel(FIELDS[t][i]),c.isNull(i)?JSONObject.NULL:c.getType(i)==Cursor.FIELD_TYPE_INTEGER?c.getLong(i):c.getString(i));
            return d;
        }
    }
    private static boolean equal(JSONObject a,JSONObject b,int table) throws Exception {
        return difference(a,b,table)==null;
    }
    private static String difference(JSONObject a,JSONObject b,int table) throws Exception {
        if(a==null||b==null)return a==b?null:"record";
        for(String key:FIELDS[table]) {
            Object av=normalize(a.opt(camel(key)),key),bv=normalize(b.opt(camel(key)),key);
            // Older exports omit optional message metadata; defaults are empty/null.
            if(av==null&&bv instanceof Number&&((Number)bv).longValue()==0)continue;
            if(bv==null&&av instanceof Number&&((Number)av).longValue()==0)continue;
            if(!Objects.equals(av,bv))return key;
        }
        return null;
    }
    public static void apply(Context context,File desiredFile,File expectedFile) throws Exception {
        try(Stage desired=new Stage(desiredFile);Stage expected=new Stage(expectedFile)) {
        // Use Room's own connection and transaction lifecycle so its temporary
        // invalidation triggers notify the live UI. Never open a competing writer.
        Object application=context.getApplicationContext();
        Object room=application.getClass().getField("f").get(application);
        Object helper=room.getClass().getMethod("f").invoke(room);
        Object wrapper=helper.getClass().getMethod("H").invoke(helper);
        SQLiteDatabase db=(SQLiteDatabase)wrapper.getClass().getField("d").get(wrapper);
        room.getClass().getMethod("b").invoke(room);
        {
            try {
                for(int t=0;t<TABLES.length;t++) {
                    try(Cursor count=db.rawQuery("SELECT count(*) FROM "+TABLES[t],null)) {
                        count.moveToNext();if(count.getLong(0)!=expected.count(TABLES[t]))throw new LocalChanged();
                    }
                    try(Cursor rows=expected.rows(TABLES[t])) {while(rows.moveToNext()){String field=difference(live(db,t,rows.getString(0)),expected.get(TABLES[t],rows.getString(0)),t);if(field!=null)throw new LocalChanged(TABLES[t]+"."+field);}}
                }
                // Validate all references before the first write.
                for(int t=1;t<TABLES.length;t++)try(Cursor rows=desired.rows(TABLES[t])) {while(rows.moveToNext()) {
                    JSONObject d=desired.get(TABLES[t],rows.getString(0));
                    if(t>=2&&!desired.has("sessions",d.getString("sessionId")))throw new IOException("云端记录缺少所属会话");
                    if(t==1)for(String field:new String[]{"folderId","parentSessionId"}) {
                        String id=d.isNull(field)?"":d.optString(field,"");
                        if(!id.isEmpty()&&!desired.has(field.equals("folderId")?"folders":"sessions",id))throw new IOException("云端会话缺少父记录");
                    }
                }}
                for(int t=0;t<TABLES.length;t++)try(Cursor rows=desired.rows(TABLES[t])) {while(rows.moveToNext()) {
                    String id=rows.getString(0);JSONObject d=desired.get(TABLES[t],rows.getString(0));
                    if(equal(d,expected.get(TABLES[t],id),t))continue;
                    ContentValues values=new ContentValues();
                    for(String field:FIELDS[t]) {
                        String key=camel(field);Object v=d.opt(key);
                        if(v==null||v==JSONObject.NULL)values.putNull(field);
                        else if(time(field))values.put(field,v instanceof Number?((Number)v).longValue():Instant.parse(v.toString()).toEpochMilli());
                        else if(v instanceof Boolean)values.put(field,(Boolean)v?1:0);
                        else if(v instanceof Number)values.put(field,((Number)v).longValue());
                        else values.put(field,v instanceof JSONObject||v instanceof JSONArray?canonical(v):v.toString());
                    }
                    if(db.update(TABLES[t],values,"id=?",new String[]{id})==0&&db.insert(TABLES[t],null,values)==-1)throw new IOException("无法写入同步记录");
                }
                }
                // Children first. Only rows represented by the safety export are deletable.
                for(int t=TABLES.length-1;t>=0;t--)try(Cursor rows=expected.rows(TABLES[t])) {while(rows.moveToNext()) {
                    String id=rows.getString(0);if(!desired.has(TABLES[t],id))db.delete(TABLES[t],"id=?",new String[]{id});
                }}
                room.getClass().getMethod("m").invoke(room);
            } finally { room.getClass().getMethod("i").invoke(room); }
        }
    }
    }
    public static String state(File snapshot) throws Exception {
        try(Stage all=new Stage(snapshot)) {
        java.security.MessageDigest hash=java.security.MessageDigest.getInstance("SHA-256");
        for(int t=0;t<TABLES.length;t++)try(Cursor rows=all.rows(TABLES[t])) {while(rows.moveToNext()) {
            JSONObject row=all.get(TABLES[t],rows.getString(0));
            hash.update((TABLES[t]+"\n"+rows.getString(0)+"\n").getBytes("UTF-8"));
            for(String field:FIELDS[t])hash.update((field+"="+String.valueOf(normalize(row.opt(camel(field)),field))+"\n").getBytes("UTF-8"));
        }}
        StringBuilder out=new StringBuilder();for(byte b:hash.digest())out.append(String.format("%02x",b&255));return out.toString();
        }
    }
    public static final class LocalChanged extends IOException {
        public LocalChanged(){this(null);}
        public LocalChanged(String field){super("同步期间本机内容发生变化，已保留本机数据并安排重试"+(field==null?"":"（"+field+"）"));}
    }
}
