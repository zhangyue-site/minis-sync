package site.zhangyue.minis.handoff;

import android.content.Context;
import android.util.Log;
import java.io.*;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.zip.GZIPInputStream;
import org.json.JSONObject;

/** Offline installation into an owned prefix; user home and credentials are never bundled. */
public final class ClientBootstrap {
    private static boolean started;
    public static synchronized void ensure(Context context) {
        if (started) return;
        started = true;
        final Context app = context.getApplicationContext();
        Thread thread = new Thread(() -> {
            try { install(app); }
            catch (Exception e) { Log.e("MinisClients", "install failed: " + e.getClass().getSimpleName()); synchronized(ClientBootstrap.class){started=false;} }
        }, "minis-client-install");
        thread.setDaemon(true);thread.start();
    }
    private static byte[] bytes(InputStream source) throws Exception {
        try(InputStream in=source;ByteArrayOutputStream out=new ByteArrayOutputStream()){
            byte[] b=new byte[8192];int n;while((n=in.read(b))!=-1)out.write(b,0,n);return out.toByteArray();
        }
    }
    private static void write(File file, String text) throws Exception {
        file.getParentFile().mkdirs();File temp=new File(file.getPath()+".minis-tmp");
        try(FileOutputStream out=new FileOutputStream(temp)){out.write(text.getBytes(StandardCharsets.UTF_8));out.getFD().sync();}
        if(!temp.renameTo(file))throw new IOException("atomic_replace_failed");
    }
    private static boolean userCommand(File file) throws Exception {
        if(java.nio.file.Files.isSymbolicLink(file.toPath()))return true;
        if(!file.exists())return false;
        try(InputStream in=new FileInputStream(file)){
            byte[] header=new byte[256];int n=in.read(header);
            return n<0 || !new String(header,0,n,StandardCharsets.UTF_8).contains("minis-managed-clients-v1");
        }
    }
    private static void skillLink(File root,String directory,String name) throws Exception {
        File link=new File(root,directory+"/"+name);link.getParentFile().mkdirs();
        if(!java.nio.file.Files.exists(link.toPath(),java.nio.file.LinkOption.NOFOLLOW_LINKS))
            java.nio.file.Files.createSymbolicLink(link.toPath(),java.nio.file.Paths.get("/var/minis/skills/"+name));
    }
    public static synchronized void install(Context context) throws Exception {
        ClassLoader loader=context.getClassLoader();
        Class<?> rootClass=Class.forName("M3.q0",true,loader);
        Object manager=rootClass.getConstructor(Context.class).newInstance(context);
        if(!(Boolean)rootClass.getMethod("e").invoke(manager)) {
            Log.i("MinisClients","waiting for native rootfs initialization");
            synchronized(ClientBootstrap.class){started=false;}
            return;
        }
        File root=new File(context.getFilesDir(),"alpine-rootfs");
        JSONObject manifest=new JSONObject(new String(bytes(context.getAssets().open("clients/manifest.json")),StandardCharsets.UTF_8));
        String expected=manifest.getString("sha256");
        if(!expected.matches("[a-f0-9]{64}") || !manifest.getString("runtime").equals("runtime-v1"))throw new IOException("invalid_client_manifest");
        File prefix=new File(root,"opt/minis-clients");prefix.mkdirs();
        File runtime=new File(prefix,"runtime-v1");File receipt=new File(runtime,".payload-sha256");
        boolean present=receipt.isFile() && new String(bytes(new FileInputStream(receipt)),StandardCharsets.UTF_8).trim().equals(expected);
        if(!present && receipt.isFile()) {
            // A newer APK must not unpack over a core used by an existing task.
            Log.i("MinisClients","existing bundled runtime preserved; use verified runtime update");
            present=true;
        }
        if(!present){
            if(runtime.exists())throw new IOException("incomplete_runtime_preserved");
            File packed=new File(prefix,"payload.pending");MessageDigest digest=MessageDigest.getInstance("SHA-256");long length=0;
            try(InputStream in=context.getAssets().open("clients/runtime.tar.gz");FileOutputStream out=new FileOutputStream(packed)){
                byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1){digest.update(b,0,n);out.write(b,0,n);length+=n;}out.getFD().sync();
            }
            StringBuilder actual=new StringBuilder();for(byte b:digest.digest())actual.append(String.format("%02x",b&255));
            if(!expected.equals(actual.toString()) || length!=manifest.getLong("bytes")){packed.delete();throw new IOException("client_payload_digest_mismatch");}
            // Prefix contains only managed, credential-free runtime files. A failed install retries safely.
            File stage=new File(prefix,"builtin-stage-"+java.util.UUID.randomUUID());stage.mkdirs();
            try {
                try(FilterInputStream in=new GZIPInputStream(new FileInputStream(packed))){rootClass.getMethod("c",FilterInputStream.class,File.class).invoke(null,in,stage);}
                write(new File(stage,".payload-sha256"),expected+"\n");
                if(!stage.renameTo(runtime))throw new IOException("runtime_publish_failed");
            } finally { packed.delete(); }

        }
        write(new File(prefix,"update.py"),new String(bytes(context.getAssets().open("clients/update.py")),StandardCharsets.UTF_8));
        write(new File(prefix,"update-key.json"),new String(bytes(context.getAssets().open("clients/update-key.json")),StandardCharsets.UTF_8));
        write(new File(prefix,"host-version"),Integer.toString(context.getPackageManager().getPackageInfo(context.getPackageName(),0).versionCode));
        write(new File(prefix,"launch"),new String(bytes(context.getAssets().open("clients/launch")),StandardCharsets.UTF_8));
        new File(prefix,"launch").setExecutable(true,false);
        for(String name:new String[]{"opencode","zcode","node","python3","git"}){
            File wrapper=new File(root,"usr/local/bin/"+name);
            if(userCommand(wrapper)){
                Log.i("MinisClients","existing command preserved: "+name);continue;
            }
            write(wrapper,"#!/bin/sh\n# minis-managed-clients-v1\nexec /bin/sh /opt/minis-clients/launch "+name+" \"$@\"\n");wrapper.setExecutable(true,false);
        }
        File config=new File(root,"root/.config/opencode/opencode.json");
        if(!config.exists())write(config,"{\"model\":\"opencode/deepseek-v4.1-flash-free\"}\n");
        String start="<!-- minis-clients-handoff-v1 -->",end="<!-- /minis-clients-handoff-v1 -->";
        for(String name:new String[]{"root/AGENTS.md","root/.config/opencode/AGENTS.md","root/.zcode/AGENTS.md"}){
            String skillPath=name.contains(".config/opencode/")?"/root/.config/opencode/skills/dida-dev-handoff/SKILL.md":name.contains(".zcode/")?"/root/.zcode/skills/dida-dev-handoff/SKILL.md":"/root/.agents/skills/dida-dev-handoff/SKILL.md";
            String instruction=start+"\nDevelopment tasks: read "+skillPath+". Save the project handoff before changes and after tests; use that skill's checkpoint.py to update the same Dida task. Preserve other sessions' work. If unavailable, retain a pending local handoff.\n"+end;
            File file=new File(root,name);String old=file.isFile()?new String(bytes(new FileInputStream(file)),StandardCharsets.UTF_8):"";
            int begin=old.indexOf(start),finish=old.indexOf(end);
            if(begin>=0 && finish>=begin)write(file,old.substring(0,begin)+instruction+old.substring(finish+end.length()));
            else if(begin<0 && finish<0)write(file,old+"\n"+instruction+"\n");
            // Malformed or user-owned blocks are preserved instead of guessed replacements.
        }
        for(String directory:new String[]{"root/.config/opencode/skills","root/.zcode/skills","root/.agents/skills"})
            for(String skillName:new String[]{"dida-dev-handoff","coding-clients"})skillLink(root,directory,skillName);
        File utility=new File(root,"usr/local/bin/minis-clients");
        if(!utility.exists()) {write(utility,"#!/bin/sh\n# minis-managed-clients-v1\nexec /bin/sh /opt/minis-clients/launch \"$@\"\n");utility.setExecutable(true,false);}
        File skill=new File(context.getFilesDir(),"minis-global/skills/coding-clients/SKILL.md");
        if(!skill.exists()) {
            Object app=context.getApplicationContext();java.lang.reflect.Field field=app.getClass().getDeclaredField("j");field.setAccessible(true);
            Object repository=field.get(app);
            repository.getClass().getMethod("m",ByteArrayInputStream.class).invoke(repository,new ByteArrayInputStream(bytes(context.getAssets().open("clients/coding-clients.zip"))));
            if(!skill.isFile())throw new IOException("client_skill_registration_failed");
        }
        Log.i("MinisClients","offline official client payload installed; user login remains private");
    }
}
