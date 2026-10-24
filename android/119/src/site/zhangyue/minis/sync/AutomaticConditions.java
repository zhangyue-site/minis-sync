package site.zhangyue.minis.sync;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.os.BatteryManager;
final class AutomaticConditions {
    static String waiting(Context context,boolean enabled) {
        Intent battery=context.registerReceiver(null,new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        int status=battery==null?-1:battery.getIntExtra(BatteryManager.EXTRA_STATUS,-1);
        boolean charging=battery!=null&&battery.getIntExtra(BatteryManager.EXTRA_PLUGGED,0)!=0
            &&(status==BatteryManager.BATTERY_STATUS_CHARGING||status==BatteryManager.BATTERY_STATUS_FULL);
        ConnectivityManager cm=(ConnectivityManager)context.getSystemService(Context.CONNECTIVITY_SERVICE);
        NetworkCapabilities network=cm==null?null:cm.getNetworkCapabilities(cm.getActiveNetwork());
        boolean wifi=network!=null&&network.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
            &&network.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
            &&!network.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR);
        return AutomaticPolicy.waiting(enabled,charging,wifi);
    }
}
