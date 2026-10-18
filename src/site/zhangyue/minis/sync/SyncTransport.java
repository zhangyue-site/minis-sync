package site.zhangyue.minis.sync;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.zip.*;
import org.json.*;

public final class SyncTransport {
    private static final long MAX_DELTA = 256L * 1024 * 1024;

    public static void upload(Context context, SharedPreferences prefs, File file) throws Exception {
        String token = site.zhangyue.minis.handoff.HandoffBootstrap.syncToken(context);
        String server = "https://notes.zhangyue.site";
        JSONObject result = upload(server, token, file, prefs.getString("device", "android"));
        prefs.edit().putString("last-upload-mode", result.getString("mode"))
            .putLong("last-upload-bytes", result.getLong("sent_bytes"))
            .putString("last-upload-sha256", result.getString("sha256")).commit();
        Log.i("MinisSync", "upload " + result.getString("mode") + " bytes=" + result.getLong("sent_bytes"));
    }

    public static JSONObject upload(String server, String token, File file, String device) throws Exception {
        if (token == null || token.isEmpty()) throw new IOException("请重新登录同步账户");
        if (!file.isFile() || file.length() == 0) throw new IOException("备份文件不存在或为空");
        try {
            JSONObject result = delta(server, token, file, device);
            if (result != null) return result;
        } catch (Exception failure) {
            Log.w("MinisSync", "delta failed; falling back to full upload", failure);
        }
        JSONObject result = request(server + "/api/minis/snapshots?name=" + encode(file.getName())
            + "&device=" + encode(device), token, file);
        validate(result);
        result.put("mode", "full").put("sent_bytes", file.length());
        return result;
    }

    private static JSONObject delta(String server, String token, File file, String device) throws Exception {
        JSONObject listing = request(server + "/api/minis/snapshots", token, null);
        JSONArray items = listing.getJSONArray("items");
        if (items.length() == 0) return null;
        String base = items.getJSONObject(items.length() - 1).getString("sha256");
        if (!base.matches("[a-f0-9]{64}")) throw new IOException("无效的基线摘要");
        JSONObject manifest = request(server + "/api/minis/snapshots/" + base + "/manifest", token, null);
        JSONArray entries = manifest.getJSONArray("entries");
        Map<String, long[]> baseline = new HashMap<>();
        for (int index = 0; index < entries.length(); index++) {
            JSONArray entry = entries.getJSONArray(index);
            baseline.put(entry.getString(0), new long[] {entry.getLong(1), entry.getLong(2)});
        }
        File delta = File.createTempFile("minis-delta-", ".zip", file.getParentFile());
        try (ZipFile current = new ZipFile(file)) {
            List<ZipEntry> changed = new ArrayList<>();
            Enumeration<? extends ZipEntry> iterator = current.entries();
            while (iterator.hasMoreElements()) {
                ZipEntry entry = iterator.nextElement();
                if (entry.isDirectory()) continue;
                if (entry.getName().equals("__delta__.json")) throw new IOException("保留的备份条目名称");
                long[] previous = baseline.remove(entry.getName());
                if (previous == null || previous[0] != entry.getSize() || previous[1] != entry.getCrc()) changed.add(entry);
            }
            JSONArray deleted = new JSONArray();
            for (String name : new TreeSet<>(baseline.keySet())) deleted.put(name);
            JSONObject control = new JSONObject().put("v", 2).put("base", base).put("deleted", deleted);
            try (ZipOutputStream output = new ZipOutputStream(new BufferedOutputStream(new FileOutputStream(delta)))) {
                output.putNextEntry(new ZipEntry("__delta__.json"));
                output.write(control.toString().getBytes(StandardCharsets.UTF_8));
                output.closeEntry();
                byte[] buffer = new byte[65536];
                for (ZipEntry entry : changed) {
                    output.putNextEntry(new ZipEntry(entry.getName()));
                    try (InputStream input = current.getInputStream(entry)) {
                        int count;
                        while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                    }
                    output.closeEntry();
                    if (delta.length() > MAX_DELTA || delta.length() >= file.length()) return null;
                }
            }
            if (delta.length() > MAX_DELTA || delta.length() >= file.length()) return null;
            JSONObject result = request(server + "/api/minis/delta?base=" + base + "&name=" + encode(file.getName())
                + "&device=" + encode(device), token, delta);
            validate(result);
            if (result.optInt("protocol", 0) < 2) throw new IOException("服务端尚未支持删除条目");
            result.put("mode", "delta").put("sent_bytes", delta.length());
            return result;
        } finally {
            if (!delta.delete()) delta.deleteOnExit();
        }
    }

    private static void validate(JSONObject result) throws Exception {
        if (!result.optBoolean("ok") || !result.optString("sha256").matches("[a-f0-9]{64}"))
            throw new IOException("服务端未确认备份成功");
    }

    private static String encode(String value) throws UnsupportedEncodingException {
        return URLEncoder.encode(value, "UTF-8");
    }

    public static JSONObject request(String address, String token, File body) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(address).openConnection();
        connection.setConnectTimeout(15000);
        connection.setReadTimeout(120000);
        connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("Authorization", "Bearer " + token);
        try {
            if (body != null) {
                connection.setRequestMethod("POST");
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/octet-stream");
                connection.setFixedLengthStreamingMode(body.length());
                try (InputStream input = new BufferedInputStream(new FileInputStream(body));
                     OutputStream output = new BufferedOutputStream(connection.getOutputStream())) {
                    byte[] buffer = new byte[65536];
                    int count;
                    while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                }
            }
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) throw new IOException("同步请求失败 HTTP " + status);
            try (InputStream input = connection.getInputStream(); ByteArrayOutputStream output = new ByteArrayOutputStream()) {
                byte[] buffer = new byte[8192];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    if (output.size() + count > 16 * 1024 * 1024) throw new IOException("同步响应过大");
                    output.write(buffer, 0, count);
                }
                return new JSONObject(new String(output.toByteArray(), StandardCharsets.UTF_8));
            }
        } finally {
            connection.disconnect();
        }
    }
}
