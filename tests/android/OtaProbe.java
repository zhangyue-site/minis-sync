package test;
import android.app.Instrumentation;
import android.content.Context;
import android.os.Bundle;
/** Synthetic marker only, formal package exclusively on our ranchu QA emulator. */
public final class OtaProbe extends Instrumentation {
 private Bundle args;
 public void onCreate(Bundle b){args=b;start();}
 public void onStart(){Bundle out=new Bundle();try{
  Context c=getTargetContext();if(!android.os.Build.HARDWARE.equals("ranchu")||!c.getPackageName().equals("site.zhangyue.minis"))throw new SecurityException("owned emulator formal-package QA only");
  int version=c.getPackageManager().getPackageInfo(c.getPackageName(),0).versionCode;
  if(args.getString("mode").equals("seed")){
   if(version!=119)throw new AssertionError("119 baseline required");
   if(!c.getSharedPreferences("minis-ota-fixture",0).edit().putString("marker","preserve-formal-120").commit())throw new AssertionError("marker write failed");
  }else{
   if(version!=Integer.parseInt(args.getString("expected","121")))throw new AssertionError("OTA target not installed: "+version);
   if(!c.getSharedPreferences("minis-ota-fixture",0).getString("marker","").equals("preserve-formal-120"))throw new AssertionError("OTA marker lost");
  }
  out.putString("stream","PASS formal QA version="+version+", synthetic marker preserved\n");finish(-1,out);
 }catch(Throwable e){out.putString("stream","FAIL "+e.toString()+"\n");finish(0,out);}}
}
