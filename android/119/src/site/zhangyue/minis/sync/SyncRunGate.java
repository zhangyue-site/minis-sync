package site.zhangyue.minis.sync;
import android.content.Context;
import java.io.IOException;
import java.net.HttpURLConnection;
import java.util.concurrent.*;
/** Stop network work at safe checkpoints; never interrupt native import/export coroutines. */
final class SyncRunGate implements AutoCloseable,ResumableDownload.Control {
    private static final ThreadLocal<SyncRunGate> CURRENT=new ThreadLocal<>();
    private static final ConcurrentHashMap<Thread,SyncRunGate> RUNS=new ConcurrentHashMap<>();
    private static final java.util.Set<Thread> STOPPED=ConcurrentHashMap.newKeySet();
    private final Context context;private final boolean manual;private final Thread thread;
    private final ScheduledExecutorService timer=Executors.newSingleThreadScheduledExecutor();
    private volatile String stopped;private volatile boolean applying;private volatile HttpURLConnection connection;
    SyncRunGate(Context context,boolean manual) {
        this.context=context;this.manual=manual;thread=Thread.currentThread();CURRENT.set(this);RUNS.put(thread,this);if(STOPPED.contains(thread))stop("后台任务已暂停，保留断点等待下次同步");
        timer.scheduleAtFixedRate(()->{if(!manual){String reason=AutomaticConditions.waiting(context,context.getSharedPreferences("letian-sync",0).getBoolean("enabled",false));if(reason!=null)stop(reason);}},2,2,TimeUnit.SECONDS);
    }
    static SyncRunGate current(){return CURRENT.get();}
    static void clearStop(Thread thread){STOPPED.remove(thread);}
    static void stop(Thread thread){STOPPED.add(thread);SyncRunGate gate=RUNS.get(thread);if(gate!=null)gate.stop("后台任务已暂停，保留断点等待下次同步");}
    private synchronized void stop(String reason){stopped=reason;if(applying)return;HttpURLConnection c=connection;if(c!=null)c.disconnect();}
    public void check()throws Paused {if(stopped!=null&&!applying)throw new Paused(stopped);}
    synchronized void applying()throws IOException{check();applying=true;}
    synchronized void finishApplying()throws IOException{applying=false;check();}
    public void opened(HttpURLConnection c){connection=c;if(stopped!=null&&!applying)c.disconnect();}
    public void closed(HttpURLConnection c){if(connection==c)connection=null;}
    public void close(){timer.shutdownNow();RUNS.remove(thread);CURRENT.remove();}
    static final class Paused extends IOException {Paused(String message){super(message);}}
}
