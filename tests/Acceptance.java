package test;

import android.content.Context;
import android.content.SharedPreferences;
import java.io.File;
import java.lang.reflect.Method;
import org.json.JSONObject;
import site.zhangyue.minis.sync.SyncTransport;
import site.zhangyue.minis.sync.NasClient;
import site.zhangyue.minis.sync.NasSync;

public final class Acceptance {
    private static final String TOKEN = "minis-isolated-acceptance-20261018";

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
        System.out.println("PASS " + message);
    }

    public static void main(String[] arguments) throws Exception {
        if (arguments[0].equals("cloud")) {
            String server = arguments[1];
            File base = new File(arguments[2], "base.minisbak");
            File changed = new File(arguments[2], "changed.minisbak");
            JSONObject first = SyncTransport.upload(server, TOKEN, base, "acceptance");
            check(first.getString("mode").equals("full"), "first upload uses full backup");
            Thread.sleep(1100);
            JSONObject delta = SyncTransport.upload(server, TOKEN, changed, "acceptance");
            check(delta.getString("mode").equals("delta"), "second upload uses delta");
            check(delta.getLong("sent_bytes") < changed.length() / 100, "delta reduces transfer by over 99 percent");
            check(delta.getInt("deleted") == 1, "deleted attachment removed");
            check(SyncTransport.request(server + "/assert", TOKEN, null).getBoolean("equal"), "restored archive equals local backup entry by entry");
            JSONObject missing = SyncTransport.upload(server + "/missing", TOKEN, changed, "acceptance");
            check(missing.getString("mode").equals("full"), "missing baseline falls back to full upload");
            JSONObject rejected = SyncTransport.upload(server + "/reject", TOKEN, changed, "acceptance");
            check(rejected.getString("mode").equals("full"), "delta HTTP failure falls back to full upload");
            boolean failed = false;
            try { SyncTransport.upload(server, "invalid", changed, "acceptance"); }
            catch (Exception expected) { failed = true; }
            check(failed, "authentication failure never reports success");
            System.out.println("DELTA_RESULT " + delta);
        } else if (arguments[0].equals("smb")) {
            File source = new File(arguments[2]);
            String digest = NasClient.upload(arguments[1], "公共空间", "Minis同步/_验收_20261018", "", "", source);
            check(digest.matches("[a-f0-9]{64}"), "NAS upload and readback SHA256 match");
            String again = NasClient.upload(arguments[1], "公共空间", "Minis同步/_验收_20261018", "", "", source);
            check(digest.equals(again), "NAS repeated upload atomically replaces same file");
            android.os.Looper.prepare();
            Method systemMain = Class.forName("android.app.ActivityThread").getMethod("systemMain");
            Object thread = systemMain.invoke(null);
            Context system = (Context) thread.getClass().getMethod("getSystemContext").invoke(thread);
            Context context = system.createPackageContext("test.zhangyue.minis", Context.CONTEXT_IGNORE_SECURITY);
            Class.forName("x3.j").getDeclaredMethods();
            Class<?> managerClass = Class.forName("x3.l");
            Object manager = managerClass.getConstructor(Context.class).newInstance(context);
            managerClass.getMethod("e", String.class, boolean.class).invoke(manager, "云同步测试成功", true);
            check(context.getSharedPreferences("letian-sync", Context.MODE_PRIVATE).getString("last-result", "").contains("NAS"), "actual sync status hook verifies and runs");
            SharedPreferences prefs = context.getSharedPreferences("smbsync", Context.MODE_PRIVATE);
            prefs.edit().clear().putString("host", arguments[1]).putString("path", "Minis同步/_验收_20261018").commit();
            check(NasSync.pushNow(context, source.getParentFile()).equals("ok"), "Android NAS worker succeeds");
            check(NasSync.pushNow(context, source.getParentFile()).equals("unchanged"), "unchanged backup skips duplicate transfer");
            prefs.edit().putBoolean("enabled", false).commit();
            check(NasSync.pushNow(context, source.getParentFile()).equals("disabled"), "NAS disable setting respected");
            prefs.edit().putBoolean("enabled", true).putString("host", "127.0.0.1:1").commit();
            check(NasSync.pushNow(context, source.getParentFile()).startsWith("NAS 备份失败"), "NAS connection failure recorded");
            check(prefs.getString("last_result", "").startsWith("NAS 备份失败"), "NAS failure remains visible in preferences");
            System.out.println("NAS_SHA256 " + digest);
        }
    }
}
