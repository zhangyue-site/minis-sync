package site.zhangyue.minis.sync;
import java.io.File;
/** Recover only a received operation whose chats are already applied and whose submitted config is unchanged. */
public final class AppliedRecovery {
    private AppliedRecovery(){}
    public static boolean matches(boolean received,File local,File remote,File submitted)throws Exception {
        return received&&submitted.isFile()
            &&CloudContent.nonChatState(local).equals(CloudContent.nonChatState(submitted))
            &&CloudContent.chatState(local).equals(CloudContent.chatState(remote));
    }
}
