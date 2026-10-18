package test;
import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import java.io.*;
import java.lang.reflect.*;
import java.util.*;
import java.util.concurrent.TimeUnit;
import javax.crypto.*;
import android.util.Base64;
/** Starts the actual Minis rootfs installer and command runner in the isolated app. */
public final class ClientRuntimeInstrumentation extends Instrumentation {
 private Bundle args;
 public void onCreate(Bundle b) { args=b;start(); }
 public void onStart() {
  Bundle out=new Bundle();
  try {
   Context c=getTargetContext();ClassLoader l=c.getClassLoader();
   startActivitySync(new Intent().setClassName("test.zhangyue.minis","com.openminis.app.MainActivity").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
   if(args.getString("syncTokenFile")!=null){
    byte[] token=java.nio.file.Files.readAllBytes(new File(args.getString("syncTokenFile")).toPath());
    SecretKey key=(SecretKey)Class.forName("x3.l",true,l).getMethod("i").invoke(null);
    Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key);
    byte[] encrypted=cipher.doFinal(new String(token,"UTF-8").trim().getBytes("UTF-8"));byte[] packed=new byte[12+encrypted.length];
    System.arraycopy(cipher.getIV(),0,packed,0,12);System.arraycopy(encrypted,0,packed,12,encrypted.length);
    c.getSharedPreferences("letian-sync",0).edit().putString("token",Base64.encodeToString(packed,2)).commit();
   }
   Class<?> root=Class.forName("M3.q0",true,l);Object manager=root.getConstructor(Context.class).newInstance(c);
   if (!(Boolean)root.getMethod("e").invoke(manager)) {
    Class<?> job=Class.forName("M3.m0",true,l),cont=Class.forName("y4.c",true,l);
    Object installer=job.getConstructor(root,cont).newInstance(manager,null);
    Object unit=Class.forName("t4.y",true,l).getField("a").get(null);
    job.getMethod("q",Object.class).invoke(installer,unit);
   }
   if (!(Boolean)root.getMethod("e").invoke(manager)) throw new IOException("rootfs_install_failed");
   try { Class.forName("site.zhangyue.minis.handoff.ClientBootstrap",true,l).getMethod("install",Context.class).invoke(null,c); }
   catch (ClassNotFoundException initialBaseline) { }
   Class<?> kernel=Class.forName("M3.P",true,l);
   String nativeDir=c.getApplicationInfo().nativeLibraryDir;
   kernel.getField("c").set(null,nativeDir);
   kernel.getField("d").set(null,nativeDir+"/libproot-loader.so");
   kernel.getField("e").set(null,nativeDir+"/libproot-loader32.so");
   Map<String,String> env=(Map<String,String>)kernel.getField("h").get(null);
   env.put("PATH","/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin");env.put("HOME","/root");
   kernel.getMethod("h",Context.class).invoke(null,c);
   Map<String,String> mounts=new HashMap<>((Map<String,String>)kernel.getField("i").get(null));
   File work=new File(c.getFilesDir(),"minis-sessions/client-runtime-acceptance/workspace");work.mkdirs();mounts.put("/var/minis/workspace",work.getAbsolutePath());
   Class<?> runner=Class.forName("M3.x",true,l);
   Object shell=runner.getConstructor(Context.class,String.class,Map.class,boolean.class).newInstance(c,"client-runtime-acceptance",mounts,false);
   String command=args.getString("command","uname -m; /bin/busybox echo MINIS_ROOTFS_OK");
   Process p=(Process)runner.getMethod("c",runner,String.class,Map.class,String.class).invoke(null,shell,command,new HashMap<String,String>(),"/tmp/client-runtime-status");
   ByteArrayOutputStream bytes=new ByteArrayOutputStream();
   Thread reader=new Thread(()->{try{byte[] b=new byte[4096];int n;while((n=p.getInputStream().read(b))!=-1){if(bytes.size()<262144)bytes.write(b,0,n);}}catch(Exception ignored){}});reader.start();
   if(!p.waitFor(900,TimeUnit.SECONDS)){p.destroy();throw new IOException("command_timeout");}
   reader.join(2000);
   out.putString("stream","exit="+p.exitValue()+"\n"+bytes.toString("UTF-8")+"\n");finish(p.exitValue()==0?-1:0,out);
  } catch(Throwable e){while(e instanceof InvocationTargetException)e=((InvocationTargetException)e).getTargetException();out.putString("stream","FAIL "+e.getClass().getSimpleName()+": "+e.getMessage()+"\n");finish(0,out);}
 }
}
