package site.zhangyue.minis.sync;

import android.os.SystemClock;
import java.util.concurrent.*;

/** Updates the existing observable status; closing serializes against timer callbacks. */
public final class SyncProgress implements AutoCloseable {
    private final Object manager;
    private final ProgressMeter meter=new ProgressMeter();
    private final ScheduledExecutorService timer;
    private boolean closed;
    private long lastPublished=-1000;
    public SyncProgress(Object manager) {
        this.manager=manager;
        timer=Executors.newSingleThreadScheduledExecutor(r->{Thread t=new Thread(r,"Minis-sync-progress");t.setDaemon(true);return t;});
        timer.scheduleAtFixedRate(()->publish(false),1,1,TimeUnit.SECONDS);
    }
    public synchronized long phase(String name,long bytes) {
        if(closed)return -1;
        long id=meter.begin(name,bytes,SystemClock.elapsedRealtime());publish(true);return id;
    }
    public SyncTransport.TransferProgress transfer(String name,long bytes,String following) {
        final long id=phase(name,bytes);
        return new SyncTransport.TransferProgress() {
            public void bytes(long count) {synchronized(SyncProgress.this){if(!closed&&meter.bytes(id,count,SystemClock.elapsedRealtime()))publish(false);}}
            public void awaiting() {synchronized(SyncProgress.this){if(!closed&&meter.bytes(id,bytes,SystemClock.elapsedRealtime()))phase(following,-1);}}
        };
    }
    private synchronized void publish(boolean force) {
        if(closed)return;
        long now=SystemClock.elapsedRealtime();if(!force&&now-lastPublished<500)return;
        String value=meter.text(now);if(value==null)return;
        try{manager.getClass().getMethod("j",String.class).invoke(manager,value);lastPublished=now;}catch(Exception ignored){}
    }
    public synchronized void close(){if(closed)return;closed=true;meter.stop();timer.shutdownNow();}
}
