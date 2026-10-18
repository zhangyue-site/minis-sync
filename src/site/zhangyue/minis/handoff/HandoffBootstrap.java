package site.zhangyue.minis.handoff;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import org.json.*;

/** Installs through Minis' real repositories, then starts a private local bridge. */
public final class HandoffBootstrap {
    private static volatile ServerSocket server;
    private static final String SKILL = "dida-dev-handoff";
    private static final String ORIGIN = "https://notes.zhangyue.site";

    public static void ensure(Context context) {
        final Context app = context.getApplicationContext();
        new Handler(Looper.getMainLooper()).postDelayed(() -> {
            try {
                bridge(app);
                install(app);
                ClientBootstrap.ensure(app);
            } catch (Exception e) {
                Log.e("MinisHandoff", "bootstrap failed: " + e.getClass().getSimpleName());
            }
        }, 1200);
    }

    private static Object repository(Context context, String field) throws Exception {
        Object app = context.getApplicationContext();
        java.lang.reflect.Field f = app.getClass().getDeclaredField(field);
        f.setAccessible(true);
        Object value = f.get(app);
        if (value == null) throw new IllegalStateException("repository_not_ready");
        return value;
    }

    private static byte[] read(InputStream input, int limit) throws Exception {
        try (InputStream source = input; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buf = new byte[8192]; int n;
            while ((n = source.read(buf)) != -1) {
                if (out.size() + n > limit) throw new IOException("response_too_large");
                out.write(buf, 0, n);
            }
            return out.toByteArray();
        }
    }

    private static File directory(Context context) {
        return new File(context.getFilesDir(), "minis-global/skills/" + SKILL);
    }

    public static void install(Context context) throws Exception {
        File dir = directory(context);
        Object skills = repository(context, "j");
        // Existing personal edits win; import only when the skill is absent.
        if (!new File(dir, "SKILL.md").isFile()) {
            byte[] zip = read(context.getAssets().open("handoff/dida-dev-handoff.zip"), 256 * 1024);
            Object imported = skills.getClass().getMethod("m", ByteArrayInputStream.class)
                .invoke(skills, new ByteArrayInputStream(zip));
            if (imported == null || !new File(dir, "SKILL.md").isFile())
                throw new IOException("skill_import_failed");
        }
        SharedPreferences prefs = context.getSharedPreferences("handoff-bootstrap", Context.MODE_PRIVATE);
        if (!prefs.getBoolean("providers-v1", false)) {
            Object providers = findProvider(context);
            JSONArray templates = new JSONArray(new String(read(context.getAssets().open("handoff/providers.json"), 65536), StandardCharsets.UTF_8));
            for (int n = 0; n < templates.length(); n++) {
                JSONObject template = templates.getJSONObject(n);
                String flag = "provider-v1-" + n;
                if (prefs.getBoolean(flag, false)) continue;
                template.put("apiKey", new String(read(new FileInputStream(new File(dir, ".bridge-token")), 128), StandardCharsets.UTF_8).trim());
                Object result = providers.getClass().getMethod("u", String.class).invoke(providers, template.toString());
                if (result == null) throw new IOException("provider_import_failed");
                prefs.edit().putBoolean(flag, true).commit();
            }
            prefs.edit().putBoolean("providers-v1", true).commit();
        }
        prefs.edit().putBoolean("skill-installed", true).commit();
        Log.i("MinisHandoff", "skill registered; model templates available");
    }

    private static Object findProvider(Context context) throws Exception {
        Object app = context.getApplicationContext();
        for (java.lang.reflect.Field f : app.getClass().getDeclaredFields()) {
            if (f.getType().getName().equals("q3.p0")) {
                f.setAccessible(true); return f.get(app);
            }
        }
        throw new IllegalStateException("provider_repository_unavailable");
    }

    public static synchronized void bridge(Context context) throws Exception {
        if (server != null && !server.isClosed()) return;
        File dir = directory(context); dir.mkdirs();
        File secretFile = new File(dir, ".bridge-token");
        if (!secretFile.isFile()) {
            byte[] entropy = new byte[32]; new java.security.SecureRandom().nextBytes(entropy);
            String secret = android.util.Base64.encodeToString(entropy, android.util.Base64.NO_WRAP);
            try (FileOutputStream out = new FileOutputStream(secretFile)) { out.write(secret.getBytes(StandardCharsets.UTF_8)); }
            secretFile.setReadable(false, false); secretFile.setWritable(false, false);
            secretFile.setReadable(true, true); secretFile.setWritable(true, true);
        }
        final String secret = new String(read(new FileInputStream(secretFile), 128), StandardCharsets.UTF_8).trim();
        server = new ServerSocket(18768, 8, InetAddress.getByName("127.0.0.1"));
        Thread worker = new Thread(() -> {
            while (!server.isClosed()) {
                try {
                    final Socket socket = server.accept();
                    Thread request = new Thread(() -> serve(context, socket, secret), "minis-handoff-request");
                    request.setDaemon(true); request.start();
                } catch (IOException ignored) { break; }
            }
        }, "minis-handoff-loopback");
        worker.setDaemon(true); worker.start();
    }

    private static String line(InputStream in) throws Exception {
        ByteArrayOutputStream b = new ByteArrayOutputStream(); int c;
        while ((c = in.read()) != -1 && c != '\n') {
            if (b.size() > 8192) throw new IOException("header_too_long");
            if (c != '\r') b.write(c);
        }
        return b.toString("US-ASCII");
    }

    private static void serve(Context context, Socket socket, String secret) {
        try (Socket s = socket) {
            s.setSoTimeout(60000);
            InputStream in = s.getInputStream(); String[] first = line(in).split(" ");
            Map<String, String> headers = new HashMap<>();
            for (int n = 0; n < 40; n++) {
                String h = line(in); if (h.isEmpty()) break;
                int colon = h.indexOf(':');
                if (colon < 1) throw new IOException("invalid_header");
                headers.put(h.substring(0, colon).toLowerCase(Locale.ROOT), h.substring(colon + 1).trim());
            }
            String auth = headers.getOrDefault("authorization", "");
            if (!MessageDigest.isEqual(auth.getBytes(StandardCharsets.UTF_8), ("Bearer " + secret).getBytes(StandardCharsets.UTF_8))) {
                reply(s, 401, new JSONObject().put("error", "unauthorized")); return;
            }
            if (first.length < 2 || !first[0].equals("POST") ||
                !(first[1].equals("/handoff") || first[1].matches("/providers/(zcode|opencode|opencode-free)/v1/chat/completions"))) {
                reply(s, 404, new JSONObject().put("error", "unknown_route")); return;
            }
            int size = Integer.parseInt(headers.getOrDefault("content-length", "-1"));
            if (size < 2 || size > (first[1].equals("/handoff") ? 65536 : 2 * 1024 * 1024) || headers.containsKey("transfer-encoding")) {
                reply(s, 400, new JSONObject().put("error", "invalid_length")); return;
            }
            byte[] body = new byte[size]; int pos = 0;
            while (pos < size) { int n = in.read(body, pos, size - pos); if (n < 0) throw new EOFException(); pos += n; }
            new JSONObject(new String(body, StandardCharsets.UTF_8));
            String token = syncToken(context);
            if (token.isEmpty()) { reply(s, 401, new JSONObject().put("error", "sync_login_required")); return; }
            String target = first[1].equals("/handoff") ? "/api/minis/handoff" : "/api/minis" + first[1];
            HttpURLConnection conn = (HttpURLConnection) new URL(ORIGIN + target).openConnection();
            conn.setConnectTimeout(12000); conn.setReadTimeout(first[1].equals("/handoff") ? 55000 : 180000); conn.setInstanceFollowRedirects(false);
            conn.setRequestMethod("POST"); conn.setDoOutput(true);
            conn.setRequestProperty("Authorization", "Bearer " + token);
            conn.setRequestProperty("Content-Type", "application/json"); conn.setFixedLengthStreamingMode(body.length);
            try {
                try (OutputStream out = conn.getOutputStream()) { out.write(body); }
                int status = conn.getResponseCode();
                OutputStream out = s.getOutputStream();
                String type = conn.getContentType();
                if (type == null || type.indexOf('\r') >= 0 || type.indexOf('\n') >= 0) type = "application/json";
                out.write(("HTTP/1.1 " + status + " Response\r\nContent-Type: " + type + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
                try (InputStream response = status >= 400 ? conn.getErrorStream() : conn.getInputStream()) {
                    byte[] buf = new byte[8192]; int count;
                    while ((count = response.read(buf)) != -1) { out.write(buf, 0, count); out.flush(); }
                }
            } finally { conn.disconnect(); }
        } catch (Exception ignored) { /* Never echo payloads, tokens, or exception URLs. */ }
    }

    public static String syncToken(Context context) throws Exception {
        String encoded = context.getSharedPreferences("letian-sync", Context.MODE_PRIVATE).getString("token", "");
        if (encoded.isEmpty()) return "";
        byte[] packed = android.util.Base64.decode(encoded, android.util.Base64.NO_WRAP);
        if (packed.length < 29) throw new IOException("invalid_sync_session");
        javax.crypto.SecretKey key = (javax.crypto.SecretKey) Class.forName("x3.l").getMethod("i").invoke(null);
        javax.crypto.Cipher cipher = javax.crypto.Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(javax.crypto.Cipher.DECRYPT_MODE, key, new javax.crypto.spec.GCMParameterSpec(128, Arrays.copyOfRange(packed, 0, 12)));
        return new String(cipher.doFinal(Arrays.copyOfRange(packed, 12, packed.length)), StandardCharsets.UTF_8);
    }

    private static void reply(Socket s, int status, JSONObject body) throws Exception {
        byte[] data = body.toString().getBytes(StandardCharsets.UTF_8);
        OutputStream out = s.getOutputStream();
        out.write(("HTTP/1.1 " + status + " Response\r\nContent-Type: application/json\r\nContent-Length: " + data.length + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
        out.write(data); out.flush();
    }
}
