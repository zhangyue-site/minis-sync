package site.zhangyue.minis.sync;

import java.util.Locale;

/** Monotonic per-stage counters. A completed transfer never implies sync success. */
public final class ProgressMeter {
    private long generation,started,total,done;
    private String phase;
    public synchronized long begin(String name,long size,long now) {
        generation++;phase=name;total=size;done=0;started=now;return generation;
    }
    public synchronized boolean bytes(long id,long count,long now) {
        if(phase==null||id!=generation)return false;
        done=Math.max(done,Math.max(0,count));return true;
    }
    public synchronized void stop(){phase=null;generation++;}
    public synchronized String text(long now) {
        if(phase==null)return null;
        long elapsed=Math.max(0,now-started);
        String duration=String.format(Locale.ROOT,"%02d:%02d",elapsed/60000,(elapsed/1000)%60);
        if(total<=0)return phase+"\n已用时 "+duration;
        long count=Math.min(done,total);
        int percent=count>=total?100:(int)Math.min(99,100.0*count/total);
        String speed=elapsed<1000||count==0?"正在测量速度":size(count*1000.0/elapsed)+"/s";
        return phase+" · "+percent+"%\n"+size(count)+" / "+size(total)+" · "+speed+"\n已用时 "+duration;
    }
    private static String size(double n) {
        String[] units={"B","KiB","MiB","GiB","TiB"};int i=0;
        while(n>=1024&&i<units.length-1){n/=1024;i++;}
        return String.format(Locale.ROOT,i==0?"%.0f %s":"%.1f %s",n,units[i]);
    }
}
