package site.zhangyue.minis.sync;
import android.app.job.*;
import android.util.Log;
import java.util.concurrent.ConcurrentHashMap;
/** System wake lock lasts while the job runs; durable download checkpoints survive its time limit. */
public final class BackgroundSyncJob extends JobService {
    private static final class Run {volatile boolean stopped;Thread worker;}
    private final ConcurrentHashMap<Integer,Run> runs=new ConcurrentHashMap<>();
    public boolean onStartJob(JobParameters parameters) {
        Run run=new Run();runs.put(parameters.getJobId(),run);
        run.worker=new Thread(()->{
            boolean retry=false;
            try {
                String waiting=AutomaticConditions.waiting(this,getSharedPreferences("letian-sync",0).getBoolean("enabled",false));
                if(waiting!=null){Log.i("MinisSync","background waiting: "+waiting);return;}
                Object application=getApplicationContext();java.lang.reflect.Field ready=application.getClass().getField("d");
                long end=android.os.SystemClock.elapsedRealtime()+180000;
                while(!ready.getBoolean(application)&&!run.stopped&&android.os.SystemClock.elapsedRealtime()<end)Thread.sleep(500);
                if(run.stopped)return;if(!ready.getBoolean(application))throw new java.io.IOException("应用数据库尚未准备好");
                Log.i("MinisSync","background job started id="+parameters.getJobId());
                SyncExecutionService.JOB.set(true);
                CloudSync.run(BackgroundSync.manager(this),false);
            }catch(Exception e){retry=true;Log.w("MinisSync","background job incomplete",e);}
            finally{SyncExecutionService.JOB.remove();SyncRunGate.clearStop(Thread.currentThread());runs.remove(parameters.getJobId(),run);if(!run.stopped)jobFinished(parameters,retry);}
        },"Minis-background-sync");run.worker.start();return true;
    }
    public boolean onStopJob(JobParameters parameters) {
        Run run=runs.get(parameters.getJobId());if(run!=null){run.stopped=true;SyncRunGate.stop(run.worker);}
        Log.i("MinisSync","background job stopped; checkpoints retained");
        return getSharedPreferences("letian-sync",0).getBoolean("enabled",false);
    }
}
