package site.zhangyue.minis.sync;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;
import java.io.*;
import java.security.MessageDigest;
import java.util.concurrent.atomic.AtomicBoolean;

public final class NasSync {
    private static final AtomicBoolean RUNNING = new AtomicBoolean();

    public static void push(Context context, File directory) {
        if (!RUNNING.compareAndSet(false, true)) return;
        try {
            new Thread(() -> {
                try {
                    pushNow(context, directory);
                } finally {
                    RUNNING.set(false);
                }
            }, "Minis-NAS-backup").start();
        } catch (RuntimeException failure) {
            RUNNING.set(false);
            throw failure;
        }
    }

    public static String pushNow(Context context, File directory) {
        SharedPreferences prefs = context.getSharedPreferences("smbsync", Context.MODE_PRIVATE);
        if (!prefs.getBoolean("enabled", true)) return "disabled";
        try {
            File[] files = directory.listFiles();
            File newest = null;
            if (files != null) for (File file : files) {
                if (file.isFile() && file.getName().endsWith(".minisbak") && (newest == null || file.lastModified() > newest.lastModified())) newest = file;
            }
            if (newest == null) throw new IOException("没有可备份到 NAS 的安全备份");
            String host = prefs.getString("host", "192.168.1.4");
            String share = prefs.getString("share", "公共空间");
            String path = prefs.getString("path", "Minis同步");
            String user = prefs.getString("user", "");
            String target = host + "/" + share + "/" + path + "/" + user;
            String fingerprint = sha256(newest);
            if (target.equals(prefs.getString("last_target", "")) && fingerprint.equals(prefs.getString("last_sha256", ""))) return "unchanged";
            prefs.edit().putString("last_result", "正在备份到 NAS").commit();
            String verified = NasClient.upload(host, share, path, user, prefs.getString("pass", ""), newest);
            if (!fingerprint.equals(verified)) throw new IOException("本地备份在上传过程中发生变化");
            prefs.edit().putString("last_result", "NAS 备份成功，文件校验通过")
                .putString("last_sha256", verified).putString("last_target", target)
                .putLong("last_success", System.currentTimeMillis()).commit();
            publish(context, "NAS 备份成功，文件校验通过");
            Log.i("MinisSync", "NAS backup verified bytes=" + newest.length());
            return "ok";
        } catch (Exception failure) {
            String result = "NAS 备份失败：" + failure.getClass().getSimpleName() + ": " + failure.getMessage();
            prefs.edit().putString("last_result", result).commit();
            publish(context, result);
            Log.w("MinisSync", "NAS backup failed", failure);
            return result;
        }
    }

    public static String status(String cloudStatus, Context context) {
        SharedPreferences prefs = context.getSharedPreferences("smbsync", Context.MODE_PRIVATE);
        if (!prefs.getBoolean("enabled", true)) return cloudStatus;
        return cloudStatus + "\n" + prefs.getString("last_result", "NAS 备份等待执行");
    }

    private static void publish(Context context, String result) {
        SharedPreferences cloud = context.getSharedPreferences("letian-sync", Context.MODE_PRIVATE);
        String previous = cloud.getString("last-result", "");
        int separator = previous.indexOf('\n');
        if (separator >= 0) previous = previous.substring(0, separator);
        cloud.edit().putString("last-result", previous + "\n" + result).commit();
        Looper main = Looper.getMainLooper();
        if (main != null) new Handler(main).post(() -> Toast.makeText(context, result, Toast.LENGTH_LONG).show());
    }

    private static String sha256(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = new BufferedInputStream(new FileInputStream(file))) {
            byte[] buffer = new byte[65536];
            int count;
            while ((count = input.read(buffer)) != -1) digest.update(buffer, 0, count);
        }
        StringBuilder hex = new StringBuilder();
        for (byte value : digest.digest()) hex.append(String.format("%02x", value & 255));
        return hex.toString();
    }
}
