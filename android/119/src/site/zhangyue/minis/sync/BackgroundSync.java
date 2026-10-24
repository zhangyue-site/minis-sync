package site.zhangyue.minis.sync;
import android.app.job.*;
import android.content.*;
import android.net.*;
import android.os.*;
import android.util.Log;
/** OS-owned persisted jobs survive process death and reboot. No idle polling service. */
public final class BackgroundSync {
    public static final int PERIODIC=147131,IMMEDIATE=147132;
    private static boolean wifiEligible;
    private static Context app;private static android.content.SharedPreferences.OnSharedPreferenceChangeListener listener;
    private static final Handler MAIN=new Handler(Looper.getMainLooper());
    public static synchronized void initialize(Context context) {
        if(app!=null)return;app=context.getApplicationContext();
        listener=(p,key)->{if("enabled".equals(key)||"owner".equals(key))refresh(true);};
        app.getSharedPreferences("letian-sync",0).registerOnSharedPreferenceChangeListener(listener);
        MAIN.post(()->{
            try {
                app.registerReceiver(new BroadcastReceiver(){public void onReceive(Context c,Intent i){refresh(true);}},new IntentFilter(Intent.ACTION_POWER_CONNECTED));
                ConnectivityManager cm=(ConnectivityManager)app.getSystemService(Context.CONNECTIVITY_SERVICE);
                cm.registerDefaultNetworkCallback(new ConnectivityManager.NetworkCallback(){
                    public void onCapabilitiesChanged(Network n,NetworkCapabilities caps){networkChanged(caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)&&!caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)&&caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED));}
                    public void onLost(Network n){networkChanged(false);}
                });
            }catch(Exception e){Log.w("MinisSync","condition listener unavailable; persisted job remains",e);}
            refresh(true);
        });
    }
    static synchronized void networkChanged(boolean eligible) {
        boolean connected=eligible&&!wifiEligible;wifiEligible=eligible;
        if(connected)refresh(true);
    }
    static synchronized void refresh(boolean immediate) {
        if(app==null)return;
        JobScheduler scheduler=(JobScheduler)app.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        android.content.SharedPreferences prefs=app.getSharedPreferences("letian-sync",0);
        if(!prefs.getBoolean("enabled",false)||prefs.getString("owner","").isEmpty()) {scheduler.cancel(PERIODIC);scheduler.cancel(IMMEDIATE);return;}
        if(scheduler.getPendingJob(PERIODIC)==null)schedule(scheduler,PERIODIC,true);
        if(immediate&&scheduler.getPendingJob(IMMEDIATE)==null)schedule(scheduler,IMMEDIATE,false);
    }
    private static void schedule(JobScheduler scheduler,int id,boolean periodic) {
        JobInfo.Builder b=new JobInfo.Builder(id,new ComponentName(app,BackgroundSyncJob.class)).setRequiresCharging(true).setPersisted(true);
        if(Build.VERSION.SDK_INT>=28)b.setRequiredNetwork(new NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET).build());
        else b.setRequiredNetworkType(JobInfo.NETWORK_TYPE_UNMETERED);
        if(periodic)b.setPeriodic(15*60*1000L,5*60*1000L);else b.setMinimumLatency(1000);
        if(scheduler.schedule(b.build())!=JobScheduler.RESULT_SUCCESS)Log.w("MinisSync","background job scheduling declined");
    }
    static Object manager(Context c)throws Exception {
        Class<?> type=Class.forName("x3.l",true,c.getClassLoader());Object companion=type.getField("i").get(null);
        return companion.getClass().getMethod("b",Context.class).invoke(companion,c);
    }
}
