package site.zhangyue.minis.sync;

import java.io.*;
import java.security.MessageDigest;
import java.util.Properties;
import java.util.UUID;
import jcifs.CIFSContext;
import jcifs.config.PropertyConfiguration;
import jcifs.context.BaseContext;
import jcifs.smb.NtlmPasswordAuthenticator;
import jcifs.smb.SmbFile;

public final class NasClient {
    public static String upload(String host, String share, String path, String user, String password, File source) throws Exception {
        if (!source.isFile() || source.length() == 0) throw new IOException("备份文件不存在或为空");
        if (host.startsWith("smb://")) host = host.substring(6);
        if (!host.matches("[a-zA-Z0-9._:-]+")) throw new IOException("SMB 主机地址无效");
        validatePath(share);
        validatePath(path);
        Properties properties = new Properties();
        properties.setProperty("jcifs.smb.client.minVersion", "SMB202");
        properties.setProperty("jcifs.smb.client.maxVersion", "SMB311");
        properties.setProperty("jcifs.smb.client.connTimeout", "5000");
        properties.setProperty("jcifs.smb.client.responseTimeout", "20000");
        properties.setProperty("jcifs.smb.client.sessionTimeout", "20000");
        BaseContext base = new BaseContext(new PropertyConfiguration(properties));
        CIFSContext context = base.withCredentials(user == null || user.isEmpty()
            ? new NtlmPasswordAuthenticator(NtlmPasswordAuthenticator.AuthenticationType.GUEST)
            : new NtlmPasswordAuthenticator("", user, password));
        String directory = "smb://" + host + "/" + share + "/" + (path.isEmpty() ? "" : path + "/");
        String temporaryName = "." + source.getName() + "." + UUID.randomUUID() + ".part";
        try (SmbFile folder = new SmbFile(directory, context);
             SmbFile temporary = new SmbFile(directory + temporaryName, context);
             SmbFile destination = new SmbFile(directory + source.getName(), context)) {
            if (!folder.exists()) folder.mkdirs();
            try {
                MessageDigest sent = MessageDigest.getInstance("SHA-256");
                long bytes = 0;
                try (InputStream input = new BufferedInputStream(new FileInputStream(source));
                     OutputStream output = temporary.getOutputStream()) {
                    byte[] buffer = new byte[65536];
                    int count;
                    while ((count = input.read(buffer)) != -1) {
                        output.write(buffer, 0, count);
                        sent.update(buffer, 0, count);
                        bytes += count;
                    }
                }
                if (temporary.length() != bytes || bytes != source.length()) throw new IOException("SMB 文件大小校验失败");
                MessageDigest received = MessageDigest.getInstance("SHA-256");
                try (InputStream input = temporary.getInputStream()) {
                    byte[] buffer = new byte[65536];
                    int count;
                    while ((count = input.read(buffer)) != -1) received.update(buffer, 0, count);
                }
                byte[] digest = sent.digest();
                if (!MessageDigest.isEqual(digest, received.digest())) throw new IOException("SMB 文件摘要校验失败");
                temporary.renameTo(destination, true);
                StringBuilder hex = new StringBuilder();
                for (byte value : digest) hex.append(String.format("%02x", value & 255));
                return hex.toString();
            } finally {
                if (temporary.exists()) temporary.delete();
            }
        } finally {
            context.close();
            base.close();
        }
    }

    private static void validatePath(String path) throws IOException {
        if (path == null || path.startsWith("/") || path.contains("\\") || path.contains("?") || path.contains("#"))
            throw new IOException("SMB 路径无效");
        for (String segment : path.split("/")) if (segment.equals("..") || segment.equals(".")) throw new IOException("SMB 路径无效");
    }
}
