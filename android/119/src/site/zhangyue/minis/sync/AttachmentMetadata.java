package site.zhangyue.minis.sync;
import java.io.IOException;
import java.util.zip.ZipFile;
import java.util.zip.ZipEntry;
/** Native logical file rows may omit size; the blob entry remains authoritative. */
public final class AttachmentMetadata {
    public static long size(ZipFile zip,String sha,Long declared) throws IOException {
        if(sha==null||!sha.matches("[a-f0-9]{64}"))throw new IOException("附件摘要无效");
        ZipEntry blob=zip.getEntry("blobs/"+sha.substring(0,2)+"/"+sha);
        if(blob==null||blob.isDirectory()||blob.getSize()<0||blob.getSize()>2L*1024*1024*1024)throw new IOException("附件内容缺失或大小无效");
        long size=blob.getSize();
        if(declared!=null&&(declared<0||declared!=size))throw new IOException("附件声明大小与内容不符");
        return size;
    }
}
