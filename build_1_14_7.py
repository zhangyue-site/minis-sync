import os
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path('/opt/minis-fix-20261018')
BASE = Path('/opt/msx107/base/dex1_smali')
BT = Path('/opt/android-sdk/build-tools/35.0.0')
base = Path('/opt/msx107/out/minis-sync-1.14.6.apk')


def run(*arguments):
    subprocess.run([str(value) for value in arguments], check=True)


out = ROOT / 'out'
out.mkdir(exist_ok=True)
tree = ROOT / 'smali'
if tree.exists():
    shutil.rmtree(tree)
shutil.copytree(BASE, tree)
for filename in ('SmbPush.smali', 'SmbPushTask.smali'):
    (tree / 'x3' / filename).unlink()
sync = tree / 'x3/j.smali'
source = sync.read_text()
start = source.index('# --- smb push patch (vc107) start ---')
end = source.index('# --- smb push patch (vc107) end ---', start) + len('# --- smb push patch (vc107) end ---')
source = source[:start] + '''    iget-object v3, v12, Lx3/l;->a:Landroid/content/Context;
    invoke-virtual {v12}, Lx3/l;->g()Ljava/io/File;
    move-result-object v5
    invoke-static {v3, v5}, Lsite/zhangyue/minis/sync/NasSync;->push(Landroid/content/Context;Ljava/io/File;)V
''' + source[end:]
start = source.index('    invoke-virtual {v0}, Ljava/io/File;->getName()Ljava/lang/String;', source.index(':goto_419'))
end = source.index('    invoke-static {v3, v8}, La/a;->d(Ljava/io/Closeable;Ljava/lang/Throwable;)V', start) + len('    invoke-static {v3, v8}, La/a;->d(Ljava/io/Closeable;Ljava/lang/Throwable;)V')
source = source[:start] + '''    iget-object v3, v12, Lx3/l;->a:Landroid/content/Context;
    iget-object v5, v12, Lx3/l;->b:Landroid/content/SharedPreferences;
    invoke-static {v3, v5, v0}, Lsite/zhangyue/minis/sync/SyncTransport;->upload(Landroid/content/Context;Landroid/content/SharedPreferences;Ljava/io/File;)V
''' + source[end:]
sync.write_text(source)
state = tree / 'x3/l.smali'
source = state.read_text()
anchor = '.method public final e(Ljava/lang/String;Z)V\n    .registers 6\n'
assert source.count(anchor) == 1
source = source.replace(anchor, anchor + '''
    iget-object v0, p0, Lx3/l;->a:Landroid/content/Context;
    invoke-static {p1, v0}, Lsite/zhangyue/minis/sync/NasSync;->status(Ljava/lang/String;Landroid/content/Context;)Ljava/lang/String;
    move-result-object p1
''')
state.write_text(source)
for filename, register in (('Q.smali', 'v11'), ('V.smali', 'v3')):
    updater = tree / 'n3' / filename
    source = updater.read_text()
    old = '    const/16 ' + register + ', 0x69'
    assert source.count(old) == 1
    updater.write_text(source.replace(old, '    const/16 ' + register + ', 0x6c'))
run('java', '-jar', '/opt/msx107/smali.jar', 'a', tree, '-o', out / 'classes2.dex')
primary = ROOT / 'primary-smali'
if primary.exists():
    shutil.rmtree(primary)
with zipfile.ZipFile(base) as original:
    (out / 'primary-original.dex').write_bytes(original.read('classes.dex'))
run('java', '-jar', '/opt/msx107/baksmali.jar', 'd', out / 'primary-original.dex', '-o', primary)
for relative in ('x3/j.smali', 'x3/l.smali', 'n3/Q.smali', 'n3/V.smali'):
    shutil.copyfile(tree / relative, primary / relative)
run('java', '-jar', '/opt/msx107/smali.jar', 'a', primary, '-o', out / 'classes.dex')
classes = ROOT / 'classes'
classes.mkdir(exist_ok=True)
jars = sorted(Path('/opt/msx107/lib_jcifs').glob('*.jar'))
classpath = os.pathsep.join(str(value) for value in jars + [Path('/opt/android-sdk/platforms/android-34/android.jar')])
run('javac', '-encoding', 'UTF-8', '-source', '8', '-target', '8', '-cp', classpath, '-d', classes, *sorted((ROOT / 'src').rglob('*.java')))
run('jar', 'cf', out / 'sync.jar', '-C', classes, '.')
dex = ROOT / 'dex'
dex.mkdir(exist_ok=True)
run('java', '-Xmx1g', '-cp', BT / 'lib/d8.jar', 'com.android.tools.r8.D8', '--release', '--min-api', '26', '--lib', '/opt/android-sdk/platforms/android-34/android.jar', '--output', dex, out / 'sync.jar', *jars)
run('python3', ROOT / 'patch_axml_v2.py', base, '108', '1.14.6', '1.14.7', out / 'AndroidManifest.xml')
for test in (False, True):
    name = 'minis-sync-1.14.7-test' if test else 'minis-sync-1.14.7'
    manifest = (out / 'AndroidManifest.xml').read_bytes()
    if test:
        manifest = manifest.replace('site.zhangyue.minis'.encode('utf-16-le'), 'test.zhangyue.minis'.encode('utf-16-le'))
        manifest = manifest.replace(b'site.zhangyue.minis', b'test.zhangyue.minis')
    with zipfile.ZipFile(base) as original, zipfile.ZipFile(out / (name + '-unsigned.apk'), 'w', zipfile.ZIP_DEFLATED) as output:
        for entry in original.infolist():
            if entry.filename.startswith('META-INF/') or entry.filename in ('AndroidManifest.xml', 'classes.dex', 'classes2.dex', 'classes3.dex'):
                continue
            content = original.read(entry.filename)
            if entry.filename == 'resources.arsc' or entry.filename.endswith('.so'):
                entry.compress_type = zipfile.ZIP_STORED
            output.writestr(entry, content)
        output.writestr('AndroidManifest.xml', manifest)
        output.write(out / 'classes.dex', 'classes.dex')
        output.write(out / 'classes2.dex', 'classes2.dex')
        output.write(dex / 'classes.dex', 'classes3.dex')
    run(BT / 'zipalign', '-p', '-f', '4', out / (name + '-unsigned.apk'), out / (name + '-aligned.apk'))
    run(BT / 'apksigner', 'sign', '--ks', '/opt/keystore-vault/notesvault.keystore', '--ks-key-alias', 'notesvault', '--ks-pass', 'env:MINIS_SIGNING_PASSWORD', '--out', out / (name + '.apk'), out / (name + '-aligned.apk'))
    verification = subprocess.run([str(BT / 'apksigner'), 'verify', '--verbose', '--print-certs', str(out / (name + '.apk'))], check=True, capture_output=True, text=True)
    if 'b5aaac0eb0369165d34ee3bae1cddd637eaa2bc3566adf9e3f2dc2f5a1a473e4' not in verification.stdout:
        raise RuntimeError('APK certificate does not match the Letian family key')
    print(verification.stdout)
