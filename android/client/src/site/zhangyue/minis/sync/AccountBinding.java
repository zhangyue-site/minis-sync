package site.zhangyue.minis.sync;
import android.content.Context;
import android.content.SharedPreferences;
import java.io.*;

/** The upstream live database is global: refuse a different account before export. */
public final class AccountBinding {
    private AccountBinding() {}
    public static synchronized void remote(Context context,String owner,String uid)throws IOException {
        check(context,owner);
        if(!uid.matches("[0-9]{1,100}"))throw new IOException("云端尚不支持账号身份验证，请先升级同步服务");
        SharedPreferences p=context.getSharedPreferences("minis-account-binding",0);String previous=p.getString("server-uid","");
        if(!previous.isEmpty()&&!previous.equals(uid))throw new IOException("登录凭据属于其他账号，已停止同步并保留原数据");
        if(previous.isEmpty()&&!p.edit().putString("server-uid",uid).commit())throw new IOException("无法保存云端账号绑定");
    }
    public static synchronized void check(Context context,String owner)throws IOException {
        if(owner.isEmpty()||owner.length()>100||owner.contains("/")||owner.contains("\\")||owner.equals(".")||owner.equals(".."))throw new IOException("同步账号标识无效");
        SharedPreferences p=context.getSharedPreferences("minis-account-binding",0);
        String active=p.getString("owner","");
        if(!active.isEmpty()&&!active.equals(owner))throw new IOException("本机数据属于另一个账号，已停止同步以保护隐私；原数据保留");
        if(active.isEmpty()) {
            File parent=new File(context.getFilesDir(),"letian");File[] children=parent.listFiles();
            if(children!=null)for(File f:children)if(f.isDirectory()&&f.getName().startsWith("cloud-")&&!f.getName().equals("cloud-"+owner))throw new IOException("发现其他账号的本机同步数据，已停止以保护原内容");
            if(!p.edit().putString("owner",owner).commit())throw new IOException("无法保存本机账号绑定");
        }
        if(!context.getSharedPreferences("letian-sync",0).getString("owner","").equals(owner))throw new IOException("同步账号已切换，原数据保留");
    }
}
