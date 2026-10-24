package test;
import android.app.Application;
import android.app.Instrumentation;
import android.content.Context;
import android.os.Bundle;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import java.io.*;
import java.security.MessageDigest;
import java.util.*;
import org.json.*;
/** Copies evidence without initializing MinisApp or invoking application sync. No restoration. */
public final class ReadOnlySnapshot extends Instrumentation {
 public Application newApplication(ClassLoader loader,String name,Context context) throws InstantiationException,IllegalAccessException,ClassNotFoundException {
  if(!context.getPackageName().equals("site.zhangyue.minis"))throw new SecurityException("formal package only");
  return super.newApplication(loader,Application.class.getName(),context);
 }
 public void callApplicationOnCreate(Application application) { /* Deliberately do not start MinisApp. */ }
 public void onCreate(Bundle b){start();}
 static String hash(File f)throws Exception {MessageDigest d=MessageDigest.getInstance("SHA-256");try(InputStream in=new FileInputStream(f)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)d.update(b,0,n);}StringBuilder s=new StringBuilder();for(byte b:d.digest())s.append(String.format("%02x",b&255));return s.toString();}
 static void copy(File from,File to)throws Exception {if(!from.getAbsolutePath().equals(from.getCanonicalPath()))throw new IOException("symlink source");if(to.exists())throw new IOException("refuse overwrite");File parent=to.getParentFile();if(!parent.isDirectory()&&!parent.mkdirs())throw new IOException("mkdir");try(InputStream in=new FileInputStream(from);FileOutputStream out=new FileOutputStream(to)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)out.write(b,0,n);out.getFD().sync();}}
 void tree(File src,File dst,JSONArray entries,String prefix)throws Exception {if(!src.exists())return;if(!src.getAbsolutePath().equals(src.getCanonicalPath()))throw new IOException("symlink");if(src.isDirectory()){File[] children=src.listFiles();if(children==null)throw new IOException("unreadable");for(File f:children)tree(f,new File(dst,f.getName()),entries,prefix+"/"+f.getName());return;}String before=hash(src);copy(src,dst);String after=hash(src);if(!before.equals(after)||!before.equals(hash(dst)))throw new IOException("source changed during copy");entries.put(new JSONObject().put("path",prefix).put("bytes",src.length()).put("sha256",before));}
 public void onStart(){Bundle result=new Bundle();try{
  Context c=getTargetContext();File out=new File(c.getExternalFilesDir(null),"readonly-snapshot-"+System.currentTimeMillis());if(out.exists()||!out.mkdirs())throw new IOException("new evidence directory required");
  JSONArray entries=new JSONArray();File data=c.getDataDir().getCanonicalFile();tree(new File(data,"databases"),new File(out,"databases"),entries,"databases");tree(new File(data,"shared_prefs"),new File(out,"shared_prefs"),entries,"shared_prefs");tree(new File(c.getFilesDir().getCanonicalFile(),"minis-global/skills"),new File(out,"skills"),entries,"skills");
  JSONObject counts=new JSONObject();File db=new File(out,"databases/minis.db");if(db.isFile())try(SQLiteDatabase d=SQLiteDatabase.openDatabase(db.getPath(),null,SQLiteDatabase.OPEN_READONLY)){
   try(Cursor q=d.rawQuery("PRAGMA quick_check",null)){if(!q.moveToFirst()||!"ok".equals(q.getString(0)))throw new IOException("copy quick_check failed");}
   for(String table:new String[]{"folders","sessions","messages","compact_markers"})try(Cursor q=d.rawQuery("SELECT COUNT(*) FROM "+table,null)){q.moveToFirst();counts.put(table,q.getLong(0));}
  }
  JSONObject report=new JSONObject().put("application_initialized",false).put("source_hashes_unchanged",true).put("snapshot",out.getAbsolutePath()).put("files",entries).put("counts",counts);
  try(FileOutputStream f=new FileOutputStream(new File(out,"report.json"))){f.write(report.toString(2).getBytes("UTF-8"));f.getFD().sync();}
  result.putString("stream","READ_ONLY_SNAPSHOT "+out.getAbsolutePath()+"\ncounts="+counts.toString()+"\nsource_hashes_unchanged=true application_initialized=false\n");finish(-1,result);
 }catch(Throwable error){result.putString("stream","snapshot failed: "+error.getClass().getName()+" "+error.getMessage()+"; retain evidence, no restoration performed\n");finish(0,result);}}
}
