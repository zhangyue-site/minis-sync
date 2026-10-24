"""Compile/sign the QA instrumentation on the prepared Android build host."""
from pathlib import Path
import os,subprocess,zipfile
probe=os.environ.get('MINIS_PROBE_CLASS','AccountProbe')
if probe not in ('AccountProbe','ClientRuntimeInstrumentation','OtaProbe'):raise ValueError('unknown probe')
root=Path(os.environ.get('MINIS_BUILD_ROOT','/opt/minis-account-20261024'));out=root/({'AccountProbe':'account-probe','ClientRuntimeInstrumentation':'runtime-probe','OtaProbe':'ota-probe'}[probe]);out.mkdir(exist_ok=True)
tools=Path('/opt/minis-background-20261023/android-sdk/build-tools/35.0.0');android=Path('/opt/minis-background-20261023/android-sdk/platforms/android-34/android.jar')
def run(*args):subprocess.run([str(x) for x in args],check=True)
for d in ['classes','dex']:(out/d).mkdir(exist_ok=True)
run('javac','-encoding','UTF-8','-source','8','-target','8','-cp',android,'-d',out/'classes',root/('tests/ClientRuntimeInstrumentation.java' if probe=='ClientRuntimeInstrumentation' else 'tests/android/'+probe+'.java'))
run('jar','cf',out/'probe.jar','-C',out/'classes','.')
run('java','-Xmx512m','-cp',tools/'lib/d8.jar','com.android.tools.r8.D8','--min-api','26','--lib',android,'--output',out/'dex',out/'probe.jar')
(out/'AndroidManifest.xml').write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="'+({'AccountProbe':'test.zhangyue.minis.accountprobe','ClientRuntimeInstrumentation':'test.zhangyue.minis.clients','OtaProbe':'test.zhangyue.minis.otaprobe'}[probe])+'"><uses-sdk android:minSdkVersion="26" android:targetSdkVersion="28"/><uses-permission android:name="android.permission.INTERNET"/><application android:label="Synthetic account sync acceptance"/><instrumentation android:name="test.'+probe+'" android:targetPackage="'+('site.zhangyue.minis' if probe=='OtaProbe' else 'test.zhangyue.minis')+'"/></manifest>')
run(tools/'aapt','package','-f','-M',out/'AndroidManifest.xml','-I',android,'-F',out/'unsigned.apk')
with zipfile.ZipFile(out/'unsigned.apk','a') as z:z.write(out/'dex/classes.dex','classes.dex')
run(tools/'zipalign','-f','4',out/'unsigned.apk',out/'aligned.apk')
for row in Path('/opt/keystore-vault/README.md').read_text().splitlines():
 if row.startswith('| notesvault.keystore '):os.environ['MINIS_SIGNING_PASSWORD']=row.split('|')[3].strip().strip('`')
run(tools/'apksigner','sign','--ks','/opt/keystore-vault/notesvault.keystore','--ks-key-alias','notesvault','--ks-pass','env:MINIS_SIGNING_PASSWORD','--out',out/'probe.apk',out/'aligned.apk')
