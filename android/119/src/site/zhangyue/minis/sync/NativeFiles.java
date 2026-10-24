package site.zhangyue.minis.sync;

/** Kernel-assisted atomic publication. No fallback may replace an existing path. */
public final class NativeFiles {
    static { System.loadLibrary("minis_attachment_publish"); }
    private NativeFiles() {}
    /** Returns 0 on success, otherwise the kernel errno; source stays intact on failure. */
    public static native int publish(String source,String target);
}
