package site.zhangyue.minis.sync;

import android.content.Context;
import android.content.SharedPreferences;
import java.io.*;
import java.util.*;

/** Only reproducible phone-side sync caches are eligible; backups are never eligible. */
public final class CacheRetention {
    private static final long MIB=1024L*1024L, AGE=3L*24*60*60*1000, BUDGET=64*MIB;
    public interface Clock { long now(); }
    public interface SpaceProbe { long usable(File root); long total(File root); }
    public static final class Result {
        public final int deletedFiles;
        public final long deletedBytes;
        public final boolean lowSpace;
        Result(int count,long bytes,boolean low){deletedFiles=count;deletedBytes=bytes;lowSpace=low;}
    }
    private CacheRetention() {}
    public static Result clean(Context context,File... inUse) throws IOException {
        String owner=context.getSharedPreferences("letian-sync",0).getString("owner","");
        File root=context.getFilesDir();Set<File> protectedFiles=new HashSet<>();
        if(inUse!=null)for(File file:inUse)if(file!=null)protectedFiles.add(file);
        if(owner.matches("[A-Za-z0-9_-]{1,100}")) {
            SharedPreferences state=context.getSharedPreferences("minis-cloud-"+owner,0);
            File directory=new File(root,"letian/cloud-"+owner);
            for(String key:new String[]{"base","pending-base"}) {
                String hash=state.getString(key,"");if(hash.matches("[a-f0-9]{64}"))protectedFiles.add(new File(directory,hash+".minisbak"));
            }
            protectedFiles.add(new File(directory,"pending.minisbak"));protectedFiles.add(new File(directory,"pending.body"));
        }
        SharedPreferences recovery=context.getSharedPreferences("minis-cloud-recovery",0);
        for(String key:new String[]{"cloud-file","local-file"}){String path=recovery.getString(key,"");if(!path.isEmpty())protectedFiles.add(new File(path));}
        return clean(root,owner,protectedFiles,new Clock(){public long now(){return System.currentTimeMillis();}},new SpaceProbe(){public long usable(File f){return f.getUsableSpace();}public long total(File f){return f.getTotalSpace();}});
    }
    /** Injectable implementation for actual filesystem/JVM verification. No recursive deletion. */
    public static Result clean(File root,String owner,Set<File> protectedFiles,Clock clock,SpaceProbe space) throws IOException {
        File canonicalRoot=root.getCanonicalFile();
        long threshold=Math.max(256*MIB,Math.max(0,space.total(canonicalRoot))/20);
        if(space.usable(canonicalRoot)>=threshold)return new Result(0,0,false);
        // Invalid identity means no deletion, including shared projection caches.
        if(owner==null||!owner.matches("[A-Za-z0-9_-]{1,100}"))return new Result(0,0,true);
        Set<String> keep=new HashSet<>();if(protectedFiles!=null)for(File f:protectedFiles)if(f!=null)keep.add(f.getCanonicalPath());
        List<File> candidates=new ArrayList<>();
        File projections=new File(canonicalRoot,"letian/attachment-projections"), cloud=new File(canonicalRoot,"letian/cloud-"+owner);
        collect(canonicalRoot,projections,"projection-[A-Za-z0-9_-]+\\.zip",candidates);
        collect(canonicalRoot,cloud,"[a-f0-9]{64}\\.minisbak",candidates);
        Collections.sort(candidates,new Comparator<File>(){public int compare(File a,File b){int c=Long.compare(a.lastModified(),b.lastModified());return c!=0?c:a.getPath().compareTo(b.getPath());}});
        int count=0;long bytes=0, now=clock.now();
        long cutoff=now<Long.MIN_VALUE+AGE?Long.MIN_VALUE:now-AGE;
        for(File f:candidates) {
            if(count>=2||space.usable(canonicalRoot)>=threshold)break;
            // Recheck both identity and timestamp immediately before unlinking our cache.
            if(!f.getAbsoluteFile().equals(f.getCanonicalFile())||!f.isFile()||keep.contains(f.getCanonicalPath())||f.lastModified()<=0||f.lastModified()>=cutoff)continue;
            long size=f.length();if(count>0&&(size>BUDGET-bytes))continue;
            if(f.delete()){count++;bytes+=size;if(bytes>=BUDGET)break;}
        }
        return new Result(count,bytes,space.usable(canonicalRoot)<threshold);
    }
    private static void collect(File root,File dir,String pattern,List<File> output) throws IOException {
        // Reject symlinked roots, directories and files, even if they point inside filesDir.
        if(!dir.getAbsoluteFile().equals(dir.getCanonicalFile())||!dir.getPath().startsWith(root.getPath()+File.separator)||!dir.isDirectory())return;
        File[] entries=dir.listFiles();if(entries==null)return;
        for(File f:entries)if(f.getName().matches(pattern)&&f.getAbsoluteFile().equals(f.getCanonicalFile())&&f.isFile())output.add(f);
    }
}
