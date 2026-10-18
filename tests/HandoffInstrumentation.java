package test;

import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import javax.crypto.*;
import org.json.*;

/** Uses the real target Application, repositories, AndroidKeyStore, and HTTP bridge. */
public final class HandoffInstrumentation extends Instrumentation {
    private Bundle arguments;

    public void onCreate(Bundle args) { arguments = args; start(); }

    private JSONObject request(String secret, JSONObject body) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL("http://127.0.0.1:18768/handoff").openConnection(Proxy.NO_PROXY);
        c.setConnectTimeout(5000); c.setReadTimeout(60000); c.setRequestMethod("POST"); c.setDoOutput(true);
        c.setRequestProperty("Authorization", "Bearer " + secret); c.setRequestProperty("Content-Type", "application/json");
        byte[] data = body.toString().getBytes(StandardCharsets.UTF_8);
        try {
            try (OutputStream out = c.getOutputStream()) { out.write(data); }
            int status = c.getResponseCode();
            InputStream in = status >= 400 ? c.getErrorStream() : c.getInputStream();
            ByteArrayOutputStream out = new ByteArrayOutputStream(); byte[] buf = new byte[4096]; int n;
            while ((n = in.read(buf)) != -1) out.write(buf, 0, n);
            JSONObject result = new JSONObject(out.toString("UTF-8"));
            result.put("http_status", status); return result;
        } finally { c.disconnect(); }
    }

    public void onStart() {
        Bundle result = new Bundle();
        try {
            Context target = getTargetContext();
            ClassLoader loader = target.getClassLoader();
            Class<?> nativeClass = Class.forName("site.zhangyue.minis.handoff.HandoffBootstrap", true, loader);
            Class<?> sync = Class.forName("x3.l", true, loader);
            String token = new String(Files.readAllBytes(Paths.get(arguments.getString("tokenFile"))), StandardCharsets.UTF_8).trim();
            SecretKey key = (SecretKey) sync.getMethod("i").invoke(null);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE, key);
            byte[] encrypted = cipher.doFinal(token.getBytes(StandardCharsets.UTF_8));
            byte[] packed = new byte[12 + encrypted.length];
            System.arraycopy(cipher.getIV(), 0, packed, 0, 12); System.arraycopy(encrypted, 0, packed, 12, encrypted.length);
            target.getSharedPreferences("letian-sync", 0).edit().putString("token", android.util.Base64.encodeToString(packed, 2))
                .putString("owner", "minis_244_249").commit();
            String decoded = (String) nativeClass.getMethod("syncToken", Context.class).invoke(null, target);
            if (!decoded.equals(token)) throw new AssertionError("keystore_decode");
            startActivitySync(new Intent().setClassName("test.zhangyue.minis", "com.openminis.app.MainActivity")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            File dir = new File(target.getFilesDir(), "minis-global/skills/dida-dev-handoff");
            long until = System.currentTimeMillis() + 12000;
            while ((!new File(dir, ".bridge-token").isFile() || !target.getSharedPreferences("handoff-bootstrap", 0).getBoolean("skill-installed", false)) && System.currentTimeMillis() < until)
                Thread.sleep(200);
            if (!new File(dir, "SKILL.md").isFile()) throw new AssertionError("skill_not_installed");
            String secret = new String(Files.readAllBytes(new File(dir, ".bridge-token").toPath()), StandardCharsets.UTF_8).trim();
            JSONObject read = new JSONObject().put("operation", "read").put("key", "minis-e2e-20261018").put("task_id", "6ad44c18e4b05503d4ce7eee");
            JSONObject unauthorized = null;
            until = System.currentTimeMillis() + 15000;
            while (unauthorized == null) {
                try { unauthorized = request("wrong-token", read); }
                catch (ConnectException e) { if (System.currentTimeMillis() >= until) throw e; Thread.sleep(300); }
            }
            if (unauthorized.getInt("http_status") != 401) throw new AssertionError("local_auth");
            JSONObject old = request(secret, read);
            if (!old.optBoolean("ok")) throw new AssertionError("read_failed_" + old.optString("error"));
            String summary = "现状：手机原生交接桥验收中。已通过真实Skill导入、AndroidKeyStore令牌解密及本地未授权401；当前验证手机到滴答写入。代码：Minis同步版1.14.8隔离包。下一步：读回确认写入，保留原有其他工具验收记录。";
            JSONObject write = new JSONObject(read.toString()).put("operation", "checkpoint").put("base_hash", old.getString("base_hash")).put("content", summary);
            JSONObject receipt = request(secret, write);
            if (!receipt.optBoolean("verified")) throw new AssertionError("write_failed_" + receipt.optString("error"));
            JSONObject finalRead = request(secret, read);
            if (!finalRead.getString("content").contains(summary)) throw new AssertionError("readback_failed");
            result.putString("stream", "PASS real Application skill registration; PASS real AndroidKeyStore; PASS local 401; PASS phone→124→106→Dida write and independent readback; task=" + receipt.getString("task_id") + "\n");
            finish(-1, result);
        } catch (Throwable e) {
            result.putString("stream", "FAIL " + e.getClass().getSimpleName() + ": " + e.getMessage() + "\n");
            finish(0, result);
        }
    }
}
