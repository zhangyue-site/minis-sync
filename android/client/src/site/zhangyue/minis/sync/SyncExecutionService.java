package site.zhangyue.minis.sync;

import android.app.*;
import android.content.*;
import android.os.*;
import java.io.IOException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Visible execution ownership for a sync begun while the app is visible. Jobs own their own lifetime. */
public final class SyncExecutionService extends Service {
    private static final Object LOCK=new Object();
    private static boolean wanted;
    private static CountDownLatch ready;
    static final ThreadLocal<Boolean> JOB=new ThreadLocal<>();
    public static void acquire(Context context)throws Exception {
        if(Boolean.TRUE.equals(JOB.get()))return;
        ActivityManager.RunningAppProcessInfo process=new ActivityManager.RunningAppProcessInfo();
        ActivityManager.getMyMemoryState(process);
        if(process.importance>ActivityManager.RunningAppProcessInfo.IMPORTANCE_FOREGROUND_SERVICE) {
            BackgroundSync.refresh(true);
            throw new IOException("已安排后台同步，等待系统调度");
        }
        CountDownLatch waiting;
        synchronized(LOCK){wanted=true;ready=waiting=new CountDownLatch(1);}
        try {
            context.startForegroundService(new Intent(context,SyncExecutionService.class));
            if(!waiting.await(10,TimeUnit.SECONDS))throw new IOException("同步运行服务未就绪");
        }catch(Exception e){release(context);throw e;}
    }
    public static void release(Context context) {
        if(Boolean.TRUE.equals(JOB.get()))return;
        synchronized(LOCK){wanted=false;}
        context.stopService(new Intent(context,SyncExecutionService.class));
    }
    public int onStartCommand(Intent intent,int flags,int startId) {
        NotificationManager notifications=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        notifications.createNotificationChannel(new NotificationChannel("minis-sync-execution","同步运行状态",NotificationManager.IMPORTANCE_LOW));
        Notification notification=new Notification.Builder(this,"minis-sync-execution")
            .setSmallIcon(android.R.drawable.stat_notify_sync).setContentTitle("Minis 正在同步")
            .setContentText("正在校验与同步数据，详细进度请查看同步设置")
            .setOngoing(true).setOnlyAlertOnce(true).setCategory(Notification.CATEGORY_PROGRESS).build();
        if(Build.VERSION.SDK_INT>=29)startForeground(147133,notification,android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        else startForeground(147133,notification);
        synchronized(LOCK){if(ready!=null)ready.countDown();if(!wanted){stopForeground(true);stopSelf(startId);}}
        return START_NOT_STICKY;
    }
    public IBinder onBind(Intent intent){return null;}
    public void onDestroy(){stopForeground(true);super.onDestroy();}
}
