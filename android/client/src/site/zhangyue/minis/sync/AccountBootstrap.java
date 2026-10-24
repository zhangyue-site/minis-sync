package site.zhangyue.minis.sync;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import java.util.concurrent.atomic.AtomicBoolean;

/** Login/first foreground bootstrap is independent of background charge/Wi-Fi jobs. */
public final class AccountBootstrap {
    private static final AtomicBoolean ACTIVE=new AtomicBoolean();
    private static final Handler MAIN=new Handler(Looper.getMainLooper());
    private AccountBootstrap(){}
    public static void ensure(Context context) {
        Context app=context.getApplicationContext();SharedPreferences login=app.getSharedPreferences("letian-sync",0);
        String owner=login.getString("owner","");
        if(owner.isEmpty()||owner.equals(app.getSharedPreferences("minis-account-binding",0).getString("bootstrapped-owner",""))||!ACTIVE.compareAndSet(false,true))return;
        MAIN.postDelayed(()->new Thread(()->{
            try {AccountBinding.check(app,owner);Object manager=BackgroundSync.manager(app);CloudSync.run(manager,true);}
            catch(Exception e){Log.w("MinisSync","account bootstrap deferred: "+e.getClass().getSimpleName());}
            finally{ACTIVE.set(false);}
        },"Minis-account-bootstrap").start(),1500);
    }
}
