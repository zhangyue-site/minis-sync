package site.zhangyue.minis.sync;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.os.Build;
import java.io.File;

public final class OtaVerifier {
    public static void verify(Context context, File file, Integer expectedVersion) throws Exception {
        PackageManager manager = context.getPackageManager();
        int flags = Build.VERSION.SDK_INT >= 28 ? PackageManager.GET_SIGNING_CERTIFICATES : PackageManager.GET_SIGNATURES;
        PackageInfo candidate = manager.getPackageArchiveInfo(file.getAbsolutePath(), flags);
        PackageInfo installed = manager.getPackageInfo(context.getPackageName(), flags);
        if (candidate == null || !context.getPackageName().equals(candidate.packageName)
                || candidate.versionCode <= installed.versionCode
                || (expectedVersion != null && candidate.versionCode != expectedVersion.intValue())) {
            throw new SecurityException("OTA package or version mismatch");
        }
        Signature[] trusted;
        Signature[] history;
        if (Build.VERSION.SDK_INT >= 28) {
            if (candidate.signingInfo == null || installed.signingInfo == null
                    || candidate.signingInfo.hasMultipleSigners() || installed.signingInfo.hasMultipleSigners()) {
                throw new SecurityException("OTA ambiguous signer");
            }
            trusted = installed.signingInfo.getApkContentsSigners();
            history = candidate.signingInfo.getSigningCertificateHistory();
        } else {
            trusted = installed.signatures;
            history = candidate.signatures;
        }
        if (trusted == null || trusted.length != 1 || history == null) {
            throw new SecurityException("OTA missing signature");
        }
        for (Signature certificate : history) {
            if (trusted[0].equals(certificate)) return;
        }
        throw new SecurityException("OTA untrusted signing lineage");
    }
}
