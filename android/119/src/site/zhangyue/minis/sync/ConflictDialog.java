package site.zhangyue.minis.sync;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.widget.Toast;
import java.io.File;
import java.io.FileInputStream;
import java.io.OutputStream;
import java.util.WeakHashMap;

/** Explicit user-directed export only; never restores or modifies application data. */
public final class ConflictDialog {
    private static final int REQUEST = 0x6d31;
    private static final String PREFS = "minis-cloud-recovery";
    private static final String PENDING = "pending-export-file";
    private static final WeakHashMap<Activity, File> pending = new WeakHashMap<Activity, File>();
    private ConflictDialog() {}

    private static SharedPreferences prefs(Activity activity) {
        return activity.getSharedPreferences(PREFS, 0);
    }

    private static File safeFile(Activity activity, String value) {
        if (value == null || value.length() == 0) return null;
        try {
            File files = activity.getFilesDir().getCanonicalFile();
            File root = new File(files, "letian").getCanonicalFile();
            if (!root.getPath().startsWith(files.getPath() + File.separator)) return null;
            File file = new File(value).getCanonicalFile();
            if (!file.getPath().startsWith(root.getPath() + File.separator) || !file.isFile()) return null;
            return file;
        } catch (Exception ignored) { return null; }
    }

    public static void show(final Activity activity) {
        // onCreate precedes attachment of the Compose window and bootstrap state.
        activity.getWindow().getDecorView().postDelayed(() -> {
            if(!activity.isFinishing()&&!activity.isDestroyed())showReady(activity);
        },500);
    }
    private static void showReady(final Activity activity) {
        if (!prefs(activity).getString("owner", "").equals(activity.getSharedPreferences("letian-sync",0).getString("owner",""))) return;
        final File cloud = safeFile(activity, prefs(activity).getString("cloud-file", null));
        final File local = safeFile(activity, prefs(activity).getString("local-file", null));
        if (cloud == null && local == null) return;
        AlertDialog.Builder dialog = new AlertDialog.Builder(activity)
            .setTitle("同步冲突副本已安全保留")
            .setMessage("会话已同步；附件冲突保留双方，删除的原件保留供恢复。配置继续保留本机版本。可导出云端合并副本或同步前本机安全备份供检查；导出不会自动恢复或覆盖现有数据。")
            .setNegativeButton("稍后", null);
        if (cloud != null) dialog.setPositiveButton("导出云端合并副本", (d, which) -> choose(activity, cloud, "minis-cloud-merged.minisbak"));
        if (local != null) dialog.setNeutralButton("导出同步前本机安全备份", (d, which) -> choose(activity, local, "minis-local-backup.minisbak"));
        dialog.show();
    }

    private static void choose(Activity activity, File file, String name) {
        File checked = safeFile(activity, file.getPath());
        if (checked == null) { toast(activity, "备份文件不可用"); return; }
        synchronized (pending) { pending.put(activity, checked); }
        if (!prefs(activity).edit().putString(PENDING, checked.getPath()).putString("pending-export-owner",activity.getSharedPreferences("letian-sync",0).getString("owner","")).commit()) {
            synchronized (pending) { pending.remove(activity); }
            toast(activity, "无法准备导出，请重试"); return;
        }
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("application/octet-stream");
        intent.putExtra(Intent.EXTRA_TITLE, name);
        try { activity.startActivityForResult(intent, REQUEST); }
        catch (Exception ignored) { clear(activity); toast(activity, "无法打开文件保存器"); }
    }

    private static void clear(Activity activity) {
        synchronized (pending) { pending.remove(activity); }
        prefs(activity).edit().remove(PENDING).remove("pending-export-owner").commit();
    }

    public static boolean onResult(final Activity activity, int request, int result, Intent data) {
        if (request != REQUEST) return false;
        boolean sameOwner=prefs(activity).getString("pending-export-owner","").equals(activity.getSharedPreferences("letian-sync",0).getString("owner",""));
        File selected;
        synchronized (pending) { selected = pending.remove(activity); }
        if (selected == null) selected = safeFile(activity, prefs(activity).getString(PENDING, null));
        final File source = selected == null ? null : safeFile(activity, selected.getPath());
        clear(activity);
        if (result != Activity.RESULT_OK || data == null || data.getData() == null) {
            toast(activity, "已取消导出，未写入文件"); return true;
        }
        final Uri destination = data.getData();
        if (!sameOwner || source == null || !"content".equals(destination.getScheme())) {
            toast(activity, "备份文件或保存位置不可用"); return true;
        }
        new Thread(() -> {
            try {
                File checked = safeFile(activity, source.getPath());
                if (checked == null) throw new java.io.IOException("Unavailable backup");
                try (FileInputStream input = new FileInputStream(checked);
                     OutputStream output = activity.getContentResolver().openOutputStream(destination, "w")) {
                    if (output == null) throw new java.io.IOException("Unavailable destination");
                    byte[] buffer = new byte[32768];
                    int count;
                    while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                    output.flush();
                }
                activity.runOnUiThread(() -> toast(activity, "副本已导出"));
            } catch (Exception ignored) {
                activity.runOnUiThread(() -> toast(activity, "导出失败，请重新选择保存位置"));
            }
        }, "minis-recovery-export").start();
        return true;
    }

    private static void toast(Activity activity, String message) {
        Toast.makeText(activity.getApplicationContext(), message, Toast.LENGTH_LONG).show();
    }
}
