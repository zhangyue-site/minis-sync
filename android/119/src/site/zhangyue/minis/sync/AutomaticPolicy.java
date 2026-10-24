package site.zhangyue.minis.sync;
/** Shared by foreground triggers and background scheduling. Manual actions bypass it. */
public final class AutomaticPolicy {
    private AutomaticPolicy() {}
    public static boolean allowed(boolean manual,boolean enabled,boolean charging,boolean wifi) {
        return manual || waiting(enabled,charging,wifi)==null;
    }
    public static String waiting(boolean enabled,boolean charging,boolean wifi) {
        if(!enabled)return "自动同步已关闭";
        if(!charging&&!wifi)return "等待充电并连接 Wi-Fi 后自动同步";
        if(!charging)return "等待充电后自动同步";
        if(!wifi)return "等待连接 Wi-Fi 后自动同步";
        return null;
    }
}
