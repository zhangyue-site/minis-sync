package site.zhangyue.minis.sync;

import android.content.Context;
import java.io.File;
import java.lang.reflect.*;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Keeps the upstream backup ABI in one tested adapter. No access to other apps. */
public final class NativeAccess {
    private final Context context;
    private final Object manager;
    private final ClassLoader loader;
    private File rawSafety;
    public NativeAccess(Context context, Object manager) {
        this.context=context; this.manager=manager; this.loader=context.getClassLoader();
    }
    public File export() throws Exception {
        rawSafety=(File) suspend(manager.getClass().getMethod("a", manager.getClass(), type("A4.c")), null, manager);
        return ChatAttachments.forSync(context,rawSafety);
    }
    public File rawSafety() { return rawSafety; }
    public void restore(File snapshot, File safety) throws Exception {
        suspend(manager.getClass().getMethod("m",File.class,File.class,String.class,type("A4.c")),manager,snapshot,safety,null);
    }
    public String state(File snapshot) throws Exception {
        return (String) type("e2.a").getMethod("g0",File.class).invoke(null,snapshot);
    }
    public void progress(String message) throws Exception { manager.getClass().getMethod("j",String.class).invoke(manager,message); }
    public void result(String message, boolean success) throws Exception { manager.getClass().getMethod("e",String.class,boolean.class).invoke(manager,message,success); }
    private Class<?> type(String name) throws Exception { return Class.forName(name,true,loader); }
    private Object suspend(Method method,Object receiver,Object... arguments) throws Exception {
        CountDownLatch completed=new CountDownLatch(1);
        Object[] value=new Object[1]; Throwable[] failure=new Throwable[1];
        Class<?> continuation=type("y4.c");
        Object proxy=Proxy.newProxyInstance(loader,new Class<?>[]{continuation},(p,m,args)->{
            if(m.getName().equals("p"))return type("y4.i").getField("d").get(null);
            if(m.getName().equals("v")) {
                try { type("e2.a").getMethod("w0",Object.class).invoke(null,args[0]);value[0]=args[0]; }
                catch(InvocationTargetException e) { failure[0]=e.getCause(); }
                catch(Throwable e) { failure[0]=e; }
                finally { completed.countDown(); }
                return null;
            }
            if(m.getName().equals("toString"))return "Minis isolated backup continuation";
            return null;
        });
        Object completion=new NativeContinuation((y4.c)proxy);
        Object[] args=new Object[arguments.length+1];
        System.arraycopy(arguments,0,args,0,arguments.length);args[args.length-1]=completion;
        Object returned;
        try { returned=method.invoke(receiver,args); }
        catch(InvocationTargetException e) { throw unwrap(e.getCause()); }
        if(returned!=type("z4.a").getField("d").get(null))return returned;
        // Do not time out a native import and start a second writer underneath it.
        completed.await();
        if(failure[0]!=null)throw unwrap(failure[0]);
        return value[0];
    }
    private static Exception unwrap(Throwable error) {
        if(error instanceof Exception)return (Exception)error;
        return new RuntimeException(error);
    }
}
