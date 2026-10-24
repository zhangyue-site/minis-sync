package test;
import android.app.*;
import android.content.*;
import android.os.*;
import android.webkit.*;
import android.graphics.Bitmap;
import java.io.*;
import java.util.concurrent.*;
/** Read-only mobile layout check of our public download page on owned QA. */
public final class WebPageProbe extends Instrumentation {
 public void onCreate(Bundle b){start();}
 public void onStart(){Bundle out=new Bundle();try{
  Context c=getTargetContext();if(!c.getPackageName().equals("test.zhangyue.minis")||!Build.HARDWARE.equals("ranchu"))throw new SecurityException("owned QA only");
  Activity activity=startActivitySync(new Intent().setClassName(c.getPackageName(),"com.openminis.app.MainActivity").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
  CountDownLatch done=new CountDownLatch(1);String[] metrics={null};
  runOnMainSync(()->{WebView v=new WebView(activity);v.getSettings().setJavaScriptEnabled(true);v.getSettings().setUseWideViewPort(true);v.getSettings().setLoadWithOverviewMode(true);activity.setContentView(v);v.setWebViewClient(new WebViewClient(){public void onPageFinished(WebView view,String url){if(!url.equals("https://d.zhangyue.site/apps/minis-sync/"))return;view.evaluateJavascript("JSON.stringify({title:document.title,width:innerWidth,scroll:document.documentElement.scrollWidth,download:document.querySelector('a.download').href})",value->{metrics[0]=value;view.postVisualStateCallback(1,new WebView.VisualStateCallback(){public void onComplete(long id){view.postOnAnimation(()->view.postOnAnimation(()->done.countDown()));}});});}});v.loadUrl("https://d.zhangyue.site/apps/minis-sync/");});
  if(!done.await(45,TimeUnit.SECONDS))throw new IOException("page timeout");
  org.json.JSONObject m=new org.json.JSONObject(new org.json.JSONTokener(metrics[0]).nextValue().toString());
  if(!m.getString("title").contains("1.14.20")||m.getInt("width")>480||m.getInt("scroll")>m.getInt("width")+1||!m.getString("download").endsWith("/1.14.20/minis-sync-1.14.20.apk"))throw new AssertionError(m.toString());
  Bitmap screenshot=getUiAutomation().takeScreenshot();File target=new File(c.getExternalFilesDir(null),"minis-release-mobile-121.png");try(FileOutputStream f=new FileOutputStream(target)){screenshot.compress(Bitmap.CompressFormat.PNG,100,f);}screenshot.recycle();
  out.putString("stream","PASS public mobile layout "+m.toString()+"\n");finish(-1,out);
 }catch(Throwable e){out.putString("stream","FAIL "+e.toString()+"\n");finish(0,out);}}
}
