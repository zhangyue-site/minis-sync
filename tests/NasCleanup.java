package test;

import jcifs.CIFSContext;
import jcifs.context.SingletonContext;
import jcifs.smb.NtlmPasswordAuthenticator;
import jcifs.smb.SmbFile;

public final class NasCleanup {
    public static void main(String[] arguments) throws Exception {
        CIFSContext context = SingletonContext.getInstance().withCredentials(new NtlmPasswordAuthenticator(NtlmPasswordAuthenticator.AuthenticationType.GUEST));
        String root = "smb://127.0.0.1:1445/公共空间/Minis同步/_验收_20261018/";
        for (String name : new String[] {"base.minisbak", "changed.minisbak"}) {
            try (SmbFile file = new SmbFile(root + name, context)) {
                if (file.exists()) file.delete();
            }
        }
        try (SmbFile directory = new SmbFile(root, context)) {
            if (directory.exists() && directory.list().length == 0) directory.delete();
        }
        context.close();
        System.out.println("NAS acceptance fixtures cleaned");
    }
}
