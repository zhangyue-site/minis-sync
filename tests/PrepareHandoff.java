package test;

import android.content.Context;
import android.content.SharedPreferences;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import site.zhangyue.minis.handoff.HandoffBootstrap;

/** Runs as the isolated test package's UID; uses its real AndroidKeyStore. */
public final class PrepareHandoff {
    public static void main(String[] args) throws Exception {
        android.os.Looper.prepare();
        Object thread = Class.forName("android.app.ActivityThread").getMethod("systemMain").invoke(null);
        Context system = (Context) thread.getClass().getMethod("getSystemContext").invoke(thread);
        Context context = system.createPackageContext("test.zhangyue.minis", Context.CONTEXT_IGNORE_SECURITY);
        String token = new String(Files.readAllBytes(Paths.get(args[0])), StandardCharsets.UTF_8).trim();
        SecretKey key = (SecretKey) Class.forName("x3.l").getMethod("i").invoke(null);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key);
        byte[] ciphertext = cipher.doFinal(token.getBytes(StandardCharsets.UTF_8));
        byte[] packed = new byte[cipher.getIV().length + ciphertext.length];
        System.arraycopy(cipher.getIV(), 0, packed, 0, cipher.getIV().length);
        System.arraycopy(ciphertext, 0, packed, cipher.getIV().length, ciphertext.length);
        SharedPreferences prefs = context.getSharedPreferences("letian-sync", Context.MODE_PRIVATE);
        if (!prefs.edit().putString("token", android.util.Base64.encodeToString(packed, android.util.Base64.NO_WRAP))
            .putString("owner", "minis_244_249").commit()) throw new AssertionError("preferences not saved");
        if (!HandoffBootstrap.syncToken(context).equals(token)) throw new AssertionError("encrypted token mismatch");
        System.out.println("PASS real AndroidKeyStore token encryption/decryption");
    }
}
