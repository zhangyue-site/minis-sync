package site.zhangyue.minis.sync;

/** Persist only a verified local application; a failed ACK can safely be retried. */
public final class SyncCheckpoint {
    public interface Action { void run() throws Exception; }
    public static void confirm(Action verify,Action save,Action acknowledge) throws Exception {
        verify.run();save.run();acknowledge.run();
    }
    private SyncCheckpoint(){}
}
