"""Compile/sign the QA instrumentation on the prepared Android build host."""
from pathlib import Path
import os,subprocess,zipfile
probe=os.environ.get('MINIS_PROBE_CLASS','AccountProbe')
if probe not in ('AccountProbe','ClientRuntimeInstrumentation','OtaProbe','WebPageProbe','ReadOnlySnapshot'):raise ValueError('unknown probe')
root=Path(os.environ.get('MINIS_BUILD_ROOT','/opt/minis-account-20261024'));out=root/({'AccountProbe':'account-probe','ClientRuntimeInstrumentation':'runtime-probe','OtaProbe':'ota-probe','WebPageProbe':'web-probe','ReadOnlySnapshot':'readonly-snapshot'}[probe]);out.mkdir(exist_ok=True)
tools=Path('/opt/minis-background-20261023/android-sdk/build-tools/35.0.0');android=Path('/opt/minis-background-20261023/android-sdk/platforms/android-34/android.jar')
def run(*args):subprocess.run([str(x) for x in args],check=True)
for d in ['classes','dex']:(out/d).mkdir(exist_ok=True)
run('javac','-encoding','UTF-8','-source','8','-target','8','-cp',android,'-d',out/'classes',root/('tests/ClientRuntimeInstrumentation.java' if probe=='ClientRuntimeInstrumentation' else 'tests/android/'+probe+'.java'))
run('jar','cf',out/'probe.jar','-C',out/'classes','.')
run('java','-Xmx512m','-cp',tools/'lib/d8.jar','com.android.tools.r8.D8','--min-api','26','--lib',android,'--output',out/'dex',out/'probe.jar')
packages = {
    'AccountProbe': 'test.zhangyue.minis.accountprobe',
    'ClientRuntimeInstrumentation': 'test.zhangyue.minis.clients',
    'OtaProbe': 'test.zhangyue.minis.otaprobe',
    'WebPageProbe': 'test.zhangyue.minis.webprobe',
    'ReadOnlySnapshot': 'site.zhangyue.minis.readonlysnapshot',
}
readonly = probe == 'ReadOnlySnapshot'
target = 'site.zhangyue.minis' if probe in ('OtaProbe', 'ReadOnlySnapshot') else 'test.zhangyue.minis'
label = 'Minis只读快照（维护工具）' if readonly else 'Synthetic account sync acceptance'
permission = '' if readonly else '<uses-permission android:name="android.permission.INTERNET"/>'
(out/'AndroidManifest.xml').write_text(
    '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
    f'package="{packages[probe]}"><uses-sdk android:minSdkVersion="26" '
    f'android:targetSdkVersion="{35 if readonly else 28}"/>{permission}'
    f'<application android:label="{label}"/><instrumentation android:name="test.{probe}" '
    f'android:targetPackage="{target}"/></manifest>'
)
run(tools/'aapt','package','-f','-M',out/'AndroidManifest.xml','-I',android,'-F',out/'unsigned.apk')
with zipfile.ZipFile(out/'unsigned.apk','a') as z:z.write(out/'dex/classes.dex','classes.dex')
run(tools/'zipalign','-f','4',out/'unsigned.apk',out/'aligned.apk')
for row in Path('/opt/keystore-vault/README.md').read_text().splitlines():
 if row.startswith('| notesvault.keystore '):os.environ['MINIS_SIGNING_PASSWORD']=row.split('|')[3].strip().strip('`')
run(tools/'apksigner','sign','--ks','/opt/keystore-vault/notesvault.keystore','--ks-key-alias','notesvault','--ks-pass','env:MINIS_SIGNING_PASSWORD','--out',out/'probe.apk',out/'aligned.apk')
