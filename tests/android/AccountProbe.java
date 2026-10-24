package test;
import android.app.Instrumentation;
import android.content.*;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.os.Bundle;
import java.io.*;
import java.lang.reflect.*;
import java.util.zip.*;
import org.json.*;

/** Runs only against the isolated QA APK. All inputs and account keys are synthetic. */
public final class AccountProbe extends Instrumentation {
    private Bundle args;
    public void onCreate(Bundle b){args=b;start();}
    private Object manager(Context c)throws Exception{
        Class<?> type=Class.forName("x3.l",true,c.getClassLoader());Object companion=type.getField("i").get(null);
        return companion.getClass().getMethod("b",Context.class).invoke(companion,c);
    }
    private Object providers(Context c)throws Exception{Object a=c.getApplicationContext();Field f=a.getClass().getDeclaredField("h");f.setAccessible(true);return f.get(a);}
    private SQLiteDatabase db(Context c)throws Exception{
        Object a=c.getApplicationContext();Object room=a.getClass().getField("f").get(a);Object helper=room.getClass().getMethod("f").invoke(room);Object wrapper=helper.getClass().getMethod("H").invoke(helper);return (SQLiteDatabase)wrapper.getClass().getField("d").get(wrapper);
    }
    private void check(boolean b,String message){if(!b)throw new AssertionError(message);}
    private File file(Context c,String name){return new File(c.getFilesDir(),name);}
    private void skill(Context c)throws Exception{
        File target=file(c,"minis-global/skills/account-private/SKILL.md");target.getParentFile().mkdirs();
        try(FileOutputStream out=new FileOutputStream(target)){out.write("---\nname: account-private\ndescription: synthetic account skill\n---\nPrivate fixture body\n".getBytes("UTF-8"));}
        Object app=c.getApplicationContext();Field f=app.getClass().getDeclaredField("j");f.setAccessible(true);Object skills=f.get(app);skills.getClass().getMethod("y",String.class,boolean.class).invoke(skills,"test",true);
    }
    public void onStart(){Bundle result=new Bundle();try{
        Context c=getTargetContext();check(c.getPackageName().equals("test.zhangyue.minis"),"QA package required");
        c.getSharedPreferences("smbsync",0).edit().putBoolean("enabled",false).commit();
        Intent initial=c.getPackageManager().getLaunchIntentForPackage(c.getPackageName());initial.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);startActivitySync(initial);
        long ready=System.currentTimeMillis()+30000;while(providers(c)==null&&System.currentTimeMillis()<ready)Thread.sleep(100);
        check(providers(c)!=null,"native repositories not initialized");
        ClassLoader l=c.getClassLoader();String mode=args.getString("mode","");Object mgr=manager(c);
        if(mode.equals("seed")){
            Object p=providers(c);JSONObject row=new JSONObject().put("providerType","openAI").put("credentialType","apiKey").put("label","Private account provider").put("apiKey",android.util.Base64.encodeToString("synthetic-alpha".getBytes("UTF-8"),android.util.Base64.NO_WRAP)).put("customBaseURL","https://fixture.invalid/v1").put("appendV1Suffix",false).put("isEnabled",true)
                .put("models",new JSONArray().put(new JSONObject().put("modelId","qa-model").put("displayName","QA Model").put("isCustom",true).put("supportsTools",true).put("maxOutputTokens",8192)));
            String added=(String)p.getClass().getMethod("u",String.class).invoke(p,row.toString());check(added!=null,"provider seed failed");Object cfg=p.getClass().getMethod("m0").invoke(p);String id=null;for(Object item:(java.util.List<?>)cfg.getClass().getField("a").get(cfg)){if(added.equals(item.getClass().getField("b").get(item)))id=(String)item.getClass().getField("a").get(item);}check(id!=null,"provider ID missing");check("synthetic-alpha".equals(p.getClass().getMethod("A",String.class).invoke(p,id)),"source key fixture invalid");
            Field secretsField=p.getClass().getDeclaredField("e");secretsField.setAccessible(true);Object lazy=secretsField.get(p);android.content.SharedPreferences credentials=(android.content.SharedPreferences)lazy.getClass().getMethod("getValue").invoke(lazy);check(credentials.edit().putString("apikey_"+id,"synthetic-alpha").commit(),"fixture key not persisted");
            c.getSharedPreferences("qa-evidence",0).edit().putString("provider",id).putString("upgrade-marker","preserve-120").commit();skill(c);
            SQLiteDatabase d=db(c);android.content.ContentValues v=new android.content.ContentValues();
            v.put("id","qa-account-session");v.put("title","Shared account conversation");v.put("model_id","qa-model");v.put("created_at",System.currentTimeMillis()/1000);v.put("updated_at",System.currentTimeMillis()/1000);v.put("category","chat");v.put("memory_enabled",1);v.put("edit_count",0);
            d.insertOrThrow("sessions",null,v);
            android.content.ContentValues message=new android.content.ContentValues();message.put("id","qa-account-message");message.put("session_id","qa-account-session");message.put("role","user");message.put("parts_json","[{\"type\":\"text\",\"text\":\"Continue on another phone\"}]");message.put("created_at",System.currentTimeMillis()/1000);message.put("sort_order",0);message.put("stream_interrupt_count",0);d.insertOrThrow("messages",null,message);
            result.putString("stream","PASS seeded synthetic session, private skill and provider key\n");
        }else if(mode.equals("login")||mode.equals("sync")||mode.equals("autoLogin")){
            c.getSharedPreferences("letian-sync",0).edit().putString("qa-server",args.getString("server")).putString("qa-token",args.getString("token")).putString("owner",args.getString("owner")).putBoolean("enabled",false).commit();
            if(mode.equals("autoLogin")) {
                Intent launch=c.getPackageManager().getLaunchIntentForPackage(c.getPackageName());launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);c.startActivity(launch);
                long until=System.currentTimeMillis()+120000;
                while(System.currentTimeMillis()<until&&!args.getString("owner").equals(c.getSharedPreferences("minis-account-binding",0).getString("bootstrapped-owner","")))Thread.sleep(500);
            }else Class.forName("site.zhangyue.minis.sync.CloudSync",true,l).getMethod("run",Object.class,boolean.class).invoke(null,mgr,true);
            android.content.SharedPreferences state=c.getSharedPreferences("minis-cloud-"+args.getString("owner"),0);
            long completed=System.currentTimeMillis()+120000;while(state.getString("base","").isEmpty()&&System.currentTimeMillis()<completed)Thread.sleep(250);
            check(!state.getString("base","").isEmpty(),"sync did not confirm a base");
            check(args.getString("owner").equals(c.getSharedPreferences("minis-account-binding",0).getString("bootstrapped-owner","")),"bootstrap not confirmed");
            result.putString("stream","PASS sync base confirmed, account bootstrap completed\n");
        }else if(mode.equals("append")){
            android.content.ContentValues message=new android.content.ContentValues();message.put("id","qa-account-message-2");message.put("session_id","qa-account-session");message.put("role","user");message.put("parts_json","[{\"type\":\"text\",\"text\":\"Reply from second device\"}]");message.put("created_at",System.currentTimeMillis()/1000);message.put("sort_order",1);message.put("stream_interrupt_count",0);db(c).insertOrThrow("messages",null,message);result.putString("stream","PASS second-device message saved\n");
        }else if(mode.equals("verify")){
            SQLiteDatabase d=db(c);try(Cursor q=d.rawQuery("SELECT title FROM sessions WHERE id='qa-account-session'",null)){check(q.moveToFirst(),"session missing");check(q.getString(0).equals("Shared account conversation"),"session changed");}
            try(Cursor q=d.rawQuery("SELECT COUNT(*) FROM messages WHERE session_id='qa-account-session'",null)){q.moveToFirst();check(q.getInt(0)>=Integer.parseInt(args.getString("messages","1")),"message missing");}
            check(file(c,"minis-global/skills/account-private/SKILL.md").isFile(),"private skill missing");
            Object p=providers(c);Object config=p.getClass().getMethod("m0").invoke(p);Object list=config.getClass().getField("a").get(config);boolean key=false;
            for(Object row:(java.util.List<?>)list){String id=(String)row.getClass().getField("a").get(row);String label=(String)row.getClass().getField("b").get(row);if(label.equals("Private account provider")){String actual=(String)p.getClass().getMethod("A",String.class).invoke(p,id);key="synthetic-alpha".equals(actual);}}
            check(key,"private API key did not restore");result.putString("stream","PASS real Room session/messages, native provider/key and private skill restored\n");
        }else if(mode.equals("switchGuard")){
            c.getSharedPreferences("letian-sync",0).edit().putString("owner","other-account").commit();
            try{Class.forName("site.zhangyue.minis.sync.AccountBinding",true,l).getMethod("check",Context.class,String.class).invoke(null,c,"other-account");throw new AssertionError("cross-account allowed");}
            catch(InvocationTargetException expected){check(expected.getCause() instanceof IOException,"wrong binding failure");}
            check(file(c,"minis-global/skills/account-private/SKILL.md").isFile(),"original skill removed");result.putString("stream","PASS account switch blocked without removing original data\n");
        }else if(mode.equals("delta")){
            File base=new File(c.getExternalFilesDir(null),"fixture-base"),delta=new File(c.getExternalFilesDir(null),"fixture-delta"),out=file(c,"fixture-result");
            Class.forName("site.zhangyue.minis.sync.DownloadDelta",true,l).getMethod("apply",File.class,File.class,String.class,String.class,long.class,File.class,Class.forName("site.zhangyue.minis.sync.ResumableDownload$Control",true,l)).invoke(null,base,delta,args.getString("baseSha"),args.getString("targetSha"),Long.parseLong(args.getString("bytes")),out,null);
            result.putString("stream","PASS Java exact-delta reconstruction on Android\n");
        }else if(mode.equals("upgradeMarker")){
            check("preserve-120".equals(c.getSharedPreferences("qa-evidence",0).getString("upgrade-marker","")),"upgrade data lost");result.putString("stream","PASS upgrade marker preserved\n");
        }else throw new AssertionError("unknown probe mode");
        finish(-1,result);
    }catch(Throwable e){StringWriter w=new StringWriter();e.printStackTrace(new PrintWriter(w));result.putString("stream",w.toString());finish(0,result);}}
}
