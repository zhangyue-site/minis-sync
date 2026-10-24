package site.zhangyue.minis.sync;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import java.io.*;
import java.lang.reflect.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.zip.*;
import org.json.*;

/** Cloud merges immutable device deltas; clients apply only the resulting version. */
public final class CloudSync {
    private static final AtomicBoolean RUNNING=new AtomicBoolean();
    private static long lastAttempt=-60000;
    private static final Handler RETRY=new Handler(Looper.getMainLooper());
    private static Runnable retry;

    /** Returns false only when the server explicitly has no v3 capability. */
    public static boolean run(Object manager,boolean forced) {
        Context context;
        try { context=(Context)manager.getClass().getField("a").get(manager); }
        catch(Exception e){throw new IllegalStateException(e);}
        BackgroundSync.initialize(context);
        SharedPreferences legacy=context.getSharedPreferences("letian-sync",0);
        if(!forced) {
            String waiting=AutomaticConditions.waiting(context,legacy.getBoolean("enabled",false));
            if(waiting!=null) {
                if(!RUNNING.get())try{manager.getClass().getMethod("j",String.class).invoke(manager,waiting);}catch(Exception ignored){}
                return true;
            }
        }
        if(!forced&&SystemClock.elapsedRealtime()-lastAttempt<60000)return true;
        if(!RUNNING.compareAndSet(false,true))return true;
        SyncRunGate gate=new SyncRunGate(context,forced);
        NativeAccess nativeApi=new NativeAccess(context,manager);
        SyncProgress progress=new SyncProgress(manager);
        boolean supported=true;
        try {
            SyncExecutionService.acquire(context);
            lastAttempt=SystemClock.elapsedRealtime();
            String owner=legacy.getString("owner","");
            if(owner.isEmpty())throw new IOException("请先登录同步账户");
            String server="https://notes.zhangyue.site";
            String token=site.zhangyue.minis.handoff.HandoffBootstrap.syncToken(context);
            // Loopback settings are honoured only in the isolated acceptance package.
            if(context.getPackageName().equals("test.zhangyue.minis")) {
                server=legacy.getString("qa-server",server);token=legacy.getString("qa-token",token);
            }
            CloudTransport transport=new CloudTransport(server,token);
            progress.phase("正在连接同步服务…",-1);
            JSONObject listing;
            try { listing=transport.head(); }
            catch(SyncTransport.HttpFailure e) { if(e.status==404){supported=false;return false;}throw e; }
            if(listing.getInt("protocol")!=3)throw new IOException("云端同步协议不兼容");
            setBusy(manager,true);
            SharedPreferences state=context.getSharedPreferences("minis-cloud-"+owner,0);
            String device=legacy.getString("device","");
            if(!device.matches("[A-Za-z0-9_-]{1,100}")){device=UUID.randomUUID().toString();if(!legacy.edit().putString("device",device).commit())throw new IOException("无法保存设备标识");}
            File directory=new File(context.getFilesDir(),"letian/cloud-"+owner);
            if(!directory.isDirectory()&&!directory.mkdirs())throw new IOException("无法创建同步目录");
            CacheRetention.Result cleaned=CacheRetention.clean(context);
            if(cleaned.deletedFiles>0)Log.i("MinisSync","reclaimed old sync caches: files="+cleaned.deletedFiles+" bytes="+cleaned.deletedBytes);
            String base=state.getString("base","");
            if(!base.isEmpty())transport.ack(device,base); // Retries a crash after local apply but before ACK.
            progress.phase("正在创建并校验同步前安全备份…",-1);
            gate.check();
            File safety=nativeApi.export();File rawSafety=nativeApi.rawSafety();String fingerprint=nativeApi.state(safety);
            JSONObject head=listing.isNull("head")?null:listing.getJSONObject("head");
            String configState=CloudContent.nonChatState(safety);
            boolean changed=!configState.equals(state.getString("submitted-config-state",""))||!CloudContent.chatState(safety).equals(state.getString("applied-chats",""));
            gate.check();
            String pending=state.getString("pending-op","");
            boolean alreadyApplied=false;String recoveredSha="";
            if(changed||!pending.isEmpty()) {
                File body=new File(directory,"pending.body");
                File local=new File(directory,"pending.minisbak");
                String mode=state.getString("pending-mode","full");
                String operation=pending;
                String uploadBase=state.getString("pending-base",base);
                if(pending.isEmpty()) {
                    progress.phase("正在准备同步数据与增量…",-1);
                    copy(safety,local);
                    File baseline=base.isEmpty()?null:new File(directory,base+".minisbak");
                    if(baseline!=null&&(!baseline.isFile()||!CloudTransport.sha(baseline).equals(base)))throw new IOException("本机同步基线缺失，已停止以保护删除记录");
                    File delta=CloudTransport.delta(baseline,base,local,body);
                    mode=delta==null?"full":"delta";if(delta==null)copy(local,body);
                    operation=UUID.randomUUID().toString();uploadBase=base;
                    if(!state.edit().putString("pending-op",operation).putString("pending-base",uploadBase).putString("pending-mode",mode).putString("pending-state",fingerprint).putString("pending-hash",CloudTransport.sha(body)).commit())throw new IOException("无法保存待同步任务");
                }
                if(!body.isFile()||!CloudTransport.sha(body).equals(state.getString("pending-hash","")))throw new IOException("待同步任务文件缺失或损坏，安全备份已保留");
                boolean received=head!=null&&device.equals(head.optString("device"))&&operation.equals(head.optString("op_id"))
                    &&uploadBase.equals(head.isNull("base_sha")?"":head.optString("base_sha"))&&mode.equals(head.optString("mode"))
                    &&"chats".equals(head.optString("scope"))&&state.getString("pending-hash","").equals(head.optString("source_sha"));
                if(!received)transport.commit(device,uploadBase,operation,body,mode,progress.transfer(mode.equals("delta")?"正在上传增量":"正在上传初始备份",body.length(),"上传已发送，等待云端校验与合并…"));
                else Log.i("MinisSync","pending upload already confirmed by exact cloud receipt; skipping body");
                if(!fingerprint.equals(state.getString("pending-state",""))) {
                    File receivedSnapshot=head==null?null:new File(directory,head.getString("sha256")+".minisbak");
                    if(received&&receivedSnapshot.isFile()&&CloudTransport.sha(receivedSnapshot).equals(head.getString("sha256"))) {
                        progress.phase("正在校验上次已接收的云端版本…",-1);
                        nativeApi.state(receivedSnapshot);
                        progress.phase("正在核对本机已应用的会话与附件…",-1);
                        alreadyApplied=AppliedRecovery.matches(true,safety,receivedSnapshot,local);
                        if(alreadyApplied)recoveredSha=head.getString("sha256");
                    }
                    if(alreadyApplied)Log.i("MinisSync","recovering verified applied cloud version; retaining received operation without another upload");
                    else {
                    if(!state.edit().remove("pending-op").remove("pending-base").remove("pending-mode").remove("pending-state").remove("pending-hash").commit())throw new IOException("无法保存同步重试状态");
                    throw new NativeChatApply.LocalChanged();
                    }
                }
                // Re-read head: an idempotent retry can return an older operation result.
                JSONObject current=transport.head();head=current.isNull("head")?null:current.getJSONObject("head");
            }
            if(head==null)throw new IOException("云端尚无可应用版本");
            String sha=head.getString("sha256");
            alreadyApplied=alreadyApplied&&sha.equals(recoveredSha);
            if(sha.equals(base)&&!changed&&pending.isEmpty()) {
                progress.close();
                nativeApi.result("已与云端版本 "+head.getLong("version")+" 一致；其他设备接收后分别确认",true);
                state.edit().putBoolean("retry-pending",false).commit();return true;
            }
            File remote=new File(directory,sha+".minisbak");
            if(!remote.isFile()||!CloudTransport.sha(remote).equals(sha)) {
                transport.download(sha,head.getLong("bytes"),remote,progress.transfer("正在下载云端版本 "+head.getLong("version"),head.getLong("bytes"),"正在校验并保存下载结果…"));
            }
            progress.phase("正在校验云端备份…",-1);
            if(!alreadyApplied)nativeApi.state(remote); // Recovered snapshots already passed this validation.
            final String expectedChats=CloudContent.chatState(remote);
            progress.phase("正在复核本机数据，保护同步期间的修改…",-1);
            File now=nativeApi.export();
            String nowState=nativeApi.state(now);
            if(!fingerprint.equals(nowState))throw new NativeChatApply.LocalChanged();
            gate.applying();
            progress.phase("正在应用会话与附件；原内容已安全备份…",-1);
            // Configurations have independent writers (including background
            // agents). Never invoke the bulk restore over their live files.
            // scope=chats also prevents unapplied categories becoming deletions
            // on the server when this device next submits a native export.
            int attachmentConflicts=0;
            if(!alreadyApplied) {
                attachmentConflicts=ChatAttachments.apply(context,remote,now);
                NativeChatApply.apply(context,remote,now);
            }
            persistConflicts(context,remote,sha);
            File recovery=new File(context.getFilesDir(),"letian/conflicts");
            if(!recovery.isDirectory()&&!recovery.mkdirs())throw new IOException("无法保存云端恢复副本");
            File retained=new File(recovery,"cloud-"+sha+".minisbak");if(!retained.exists())copy(remote,retained);
            progress.phase("正在验证本机应用结果…",-1);
            File applied=alreadyApplied?now:nativeApi.export();String appliedState=alreadyApplied?nowState:nativeApi.state(applied);
            final String acknowledgedDevice=device;
            SyncCheckpoint.confirm(()->{
                if(!CloudContent.chatState(applied).equals(expectedChats)||!CloudContent.nonChatState(applied).equals(configState))throw new NativeChatApply.LocalChanged();
            },()->{
                if(!state.edit().putString("base",sha).putString("applied-state",appliedState).putString("submitted-config-state",configState).putString("applied-chats",expectedChats).remove("pending-op").remove("pending-base").remove("pending-mode").remove("pending-state").remove("pending-hash").putBoolean("retry-pending",false).putInt("retry-attempt",0).commit())throw new IOException("无法确认本机同步状态，安全备份已保留");
            },()->{
                gate.finishApplying();
                progress.phase("正在向云端确认本机同步完成…",-1);
                transport.ack(acknowledgedDevice,sha);
            });
            Set<String> processed=new HashSet<>(legacy.getStringSet("processed",Collections.emptySet()));processed.add(sha);
            legacy.edit().putStringSet("processed",processed).putString("last-upload-sha256",sha).commit();
            long conflicts=head.optLong("conflicts",0)+attachmentConflicts;
            if(!context.getSharedPreferences("minis-cloud-recovery",0).edit().putString("owner",owner).putString("cloud-file",retained.getPath()).putString("local-file",rawSafety.getPath()).commit())throw new IOException("无法保存恢复入口，安全备份已保留");
            progress.close();
            nativeApi.result("会话已应用云端版本 "+head.getLong("version")+(attachmentConflicts>0?"；有 "+attachmentConflicts+" 个附件保留双方，待处理":"；附件已接收，删除的原件保留供恢复")+(conflicts>0?"；冲突副本已保留":"")+"；配置等其他分类保留本机，副本可在同步设置导出处理",true);
            gate.check();
            if(forced)NasSync.push(context,new File(context.getFilesDir(),"letian/safety"));
            if(retry!=null){RETRY.removeCallbacks(retry);retry=null;}
            return true;
        } catch(Exception e) {
            progress.close();
            try{gate.check();}catch(SyncRunGate.Paused paused){
                try{nativeApi.progress(paused.getMessage());}catch(Exception ignored){}
                Log.i("MinisSync",paused.getMessage());return supported;
            }
            Log.w("MinisSync","cloud synchronization incomplete",e);
            try {nativeApi.result("同步未完成："+e.getMessage()+"。原内容和安全备份保留。",false);}catch(Exception ignored){}
            boolean again=e instanceof NativeChatApply.LocalChanged||e instanceof java.net.SocketException||e instanceof java.net.SocketTimeoutException||e instanceof java.net.UnknownHostException;
            int delay=10;
            if(e instanceof SyncTransport.HttpFailure) {SyncTransport.HttpFailure h=(SyncTransport.HttpFailure)e;again=h.status==429||h.status==503||h.status==502||h.status==504;delay=h.retryAfter;}
            if(again) {
                SharedPreferences state=context.getSharedPreferences("minis-cloud-"+legacy.getString("owner",""),0);
                int attempt=Math.min(6,state.getInt("retry-attempt",0)+1);
                state.edit().putBoolean("retry-pending",true).putInt("retry-attempt",attempt).commit();
                final Object target=manager;
                if(retry!=null)RETRY.removeCallbacks(retry);
                // Automatic retries must retain automatic constraints and respect the switch.
                retry=()->new Thread(()->run(target,forced),"Minis-cloud-retry").start();
                RETRY.postDelayed(retry,Math.max(forced?0:61,Math.max(delay,Math.min(300,1<<attempt)))*1000L);
            }
            return supported;
        } finally {SyncExecutionService.release(context);gate.close();progress.close();try{setBusy(manager,false);}catch(Exception ignored){}RUNNING.set(false);}
    }
    private static void setBusy(Object manager,boolean value) throws Exception {
        Object flow=manager.getClass().getField("g").get(manager);flow.getClass().getMethod("j",Object.class).invoke(flow,value);
    }
    private static void copy(File source,File target) throws Exception {
        File partial=new File(target+".partial");
        try(InputStream in=new FileInputStream(source);FileOutputStream out=new FileOutputStream(partial)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)out.write(b,0,n);out.getFD().sync();}
        if(!partial.renameTo(target))throw new IOException("无法保存同步文件");
    }
    private static boolean nonChats(File source,File target) throws Exception {
        try(ZipFile zip=new ZipFile(source)) {
            String manifestName=null;Enumeration<? extends ZipEntry> es=zip.entries();while(es.hasMoreElements()){String n=es.nextElement().getName();if(n.equals("manifest.json")||n.endsWith("/manifest.json"))manifestName=n;}
            if(manifestName==null)throw new IOException("备份缺少清单");
            String prefix=manifestName.substring(0,manifestName.length()-13);
            JSONObject manifest=new JSONObject(read(zip,zip.getEntry(manifestName),8*1024*1024));
            JSONObject categories=manifest.getJSONObject("categories");categories.remove("chats");if(categories.length()==0)return false;
            JSONObject integrity=new JSONObject();
            try(ZipOutputStream out=new ZipOutputStream(new FileOutputStream(target))) {
                es=zip.entries();byte[] buffer=new byte[65536];
                while(es.hasMoreElements()) {
                    ZipEntry e=es.nextElement();String path=e.getName().substring(prefix.length());
                    if(e.isDirectory()||path.equals("manifest.json")||path.startsWith("sync/")||path.matches("data/(sessions|messages|folders|compact_markers)(?:-[^/]+)?\\.jsonl"))continue;
                    byte[] transformed=null;
                    if(path.equals("files.index.jsonl")) {
                        StringBuilder filtered=new StringBuilder();for(String line:read(zip,e,64*1024*1024).split("\n")){if(line.isEmpty())continue;JSONObject row=new JSONObject(line);if(row.optString("category").equals("chats")||row.optString("path").endsWith("/.bridge-token"))continue;filtered.append(line).append('\n');}transformed=filtered.toString().getBytes("UTF-8");
                    }
                    java.security.MessageDigest hash=java.security.MessageDigest.getInstance("SHA-256");out.putNextEntry(new ZipEntry(e.getName()));
                    if(transformed!=null){out.write(transformed);hash.update(transformed);}else try(InputStream in=zip.getInputStream(e)){int n;while((n=in.read(buffer))!=-1){out.write(buffer,0,n);hash.update(buffer,0,n);}}
                    out.closeEntry();StringBuilder hex=new StringBuilder();for(byte b:hash.digest())hex.append(String.format("%02x",b&255));integrity.put(path,hex.toString());
                }
                manifest.put("integrity",integrity);out.putNextEntry(new ZipEntry(manifestName));out.write(manifest.toString().getBytes("UTF-8"));out.closeEntry();
            }
            return true;
        }
    }
    private static String read(ZipFile zip,ZipEntry entry,int limit) throws Exception {
        if(entry.getSize()>limit)throw new IOException("同步元数据过大");try(InputStream in=zip.getInputStream(entry);ByteArrayOutputStream out=new ByteArrayOutputStream()){byte[] b=new byte[8192];int n;while((n=in.read(b))!=-1){if(out.size()+n>limit)throw new IOException("同步元数据过大");out.write(b,0,n);}return out.toString("UTF-8");}
    }
    private static void persistConflicts(Context context,File remote,String sha) throws Exception {
        try(ZipFile zip=new ZipFile(remote)) {
            Enumeration<? extends ZipEntry> entries=zip.entries();while(entries.hasMoreElements()){ZipEntry e=entries.nextElement();if(e.getName().equals("sync/conflicts.json")||e.getName().endsWith("/sync/conflicts.json")) {
                File directory=new File(context.getFilesDir(),"letian/conflicts");if(!directory.isDirectory()&&!directory.mkdirs())throw new IOException("无法保存冲突恢复记录");
                File record=new File(directory,"cloud-"+sha+".json");try(FileOutputStream out=new FileOutputStream(record)){out.write(read(zip,e,64*1024*1024).getBytes("UTF-8"));out.getFD().sync();}
            }}
        }
    }
}
