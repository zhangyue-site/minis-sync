import os
from pathlib import Path
import shutil
import subprocess
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get('MINIS_BUILD_ROOT', '/opt/minis-account-20261024'))
VERSION_NAME = '1.14.19'
VERSION_CODE = 120
BUILD_TIME = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
BASE = Path('/opt/msx107/base/dex1_smali')
BT = Path('/opt/minis-background-20261023/android-sdk/build-tools/35.0.0')
base = Path('/opt/msx107/out/minis-sync-1.14.6.apk')


def run(*arguments):
    subprocess.run([str(value) for value in arguments], check=True)


if os.environ.get('MINIS_REUSE_ASSEMBLY') != '1':
    import runpy
    runpy.run_path(str(ROOT / 'scripts/build_native.py'), run_name='__main__')

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
        source = source.replace(old, '    const/16 ' + register + ', ' + hex(VERSION_CODE))
        if filename == 'V.smali':
            begin = source.index('.method public static d(Landroid/content/Context;Ljava/io/File;Ljava/lang/Integer;)V')
            end = source.index('.end method', begin) + len('.end method')
            source = source[:begin] + '''.method public static d(Landroid/content/Context;Ljava/io/File;Ljava/lang/Integer;)V
        .registers 3
        invoke-static {p0, p1, p2}, Lsite/zhangyue/minis/sync/OtaVerifier;->verify(Landroid/content/Context;Ljava/io/File;Ljava/lang/Integer;)V
        return-void
    .end method''' + source[end:]
        updater.write_text(source)
    def patch_handoff(folder):
        activity = folder / 'com/openminis/app/MainActivity.smali'
        text = activity.read_text()
        start = text.index('.method', text.index('onCreate(Landroid/os/Bundle;)V') - 100)
        # Find the exact method boundary, preserving all unrelated Activity code.
        namepos = text.index('onCreate(Landroid/os/Bundle;)V')
        start = text.rfind('.method', 0, namepos)
        end = text.index('.end method', namepos)
        block = text[start:end]
        if 'HandoffBootstrap;->ensure' not in block:
            import re
            block, count = re.subn(r'(invoke-super[^\n]*->onCreate\(Landroid/os/Bundle;\)V)',
                r'\1\n\n    invoke-static/range {p0 .. p0}, Lsite/zhangyue/minis/handoff/HandoffBootstrap;->ensure(Landroid/content/Context;)V', block, count=1)
            assert count == 1, 'MainActivity hook anchor missing'
            activity.write_text(text[:start] + block + text[end:])
        text = activity.read_text()
        bootstrap = '    invoke-static/range {p0 .. p0}, Lsite/zhangyue/minis/handoff/HandoffBootstrap;->ensure(Landroid/content/Context;)V'
        assert text.count(bootstrap)==1
        activity.write_text(text.replace(bootstrap,bootstrap+'\n\n    invoke-static/range {p0 .. p0}, Lsite/zhangyue/minis/sync/BackgroundSync;->initialize(Landroid/content/Context;)V'))
        kernel = folder / 'M3/P.smali'
        text = kernel.read_text()
        begin = text.index('.method public final c(Landroid/content/Context;LA4/c;)Ljava/lang/Object;')
        end = text.index('.end method', begin)
        block = text[begin:end]
        if 'ClientBootstrap;->install' not in block:
            hook = '    invoke-virtual {p2}, LM3/q0;->f()V'
            assert block.count(hook) == 1, 'native rootfs initialization anchor missing'
            block = block.replace(hook, hook + '\n\n    invoke-static/range {p1 .. p1}, Lsite/zhangyue/minis/handoff/ClientBootstrap;->install(Landroid/content/Context;)V')
            kernel.write_text(text[:begin] + block + text[end:])
        prompt = folder / 'Y3/Q7.smali'
        text = prompt.read_text()
        anchor = 'Interactive terminal:'
        note = 'Development handoff is enabled by default for this user. For every development task, read /var/minis/skills/dida-dev-handoff/SKILL.md, save local checkpoints before large edits and after meaningful changes or tests, and use its checkpoint.py bridge to update the same Dida task before handing back. Do not print credentials. If sync fails, retain a local pending checkpoint and report failure. Do not claim completion from files alone.\\n\\n'
        assert text.count(anchor) == 1
        prompt.write_text(text.replace(anchor, note + anchor))

    def patch_version_metadata(folder):
        for relative in ('C3/G.smali', 'L/n.smali', 'Y3/E0.smali', 'e1/w.smali', 'j4/a.smali'):
            target = folder / relative
            text = target.read_text()
            assert '1.14.4' in text, 'version display anchor missing: ' + relative
            text = text.replace('1.14.4', VERSION_NAME).replace('(build 105)', '(build ' + str(VERSION_CODE) + ')').replace('(105)', '(' + str(VERSION_CODE) + ')')
            if relative == 'j4/a.smali':
                text = text.replace('"105"', '"' + str(VERSION_CODE) + '"').replace('2026-10-16 18:23:52', BUILD_TIME)
            target.write_text(text)

    from scripts.patch_sync_ui import patch_sync_ui
    patch_sync_ui(tree)
    from scripts.patch_cloud import patch_cloud
    patch_cloud(tree)
    from scripts.patch_backup_limits import patch_backup_limits
    patch_backup_limits(tree)
    from scripts.patch_conflict_ui import patch_conflict_ui
    patch_conflict_ui(tree)
    patch_handoff(tree)
    patch_version_metadata(tree)

    run('java', '-jar', '/opt/msx107/smali.jar', 'a', tree, '-o', out / 'classes2.dex')
    primary = ROOT / 'primary-smali'
    if primary.exists():
        shutil.rmtree(primary)
    with zipfile.ZipFile(base) as original:
        (out / 'primary-original.dex').write_bytes(original.read('classes.dex'))
    run('java', '-jar', '/opt/msx107/baksmali.jar', 'd', out / 'primary-original.dex', '-o', primary)
    for relative in ('x3/j.smali', 'x3/l.smali', 'x3/n.smali', 'e2/a.smali', 'n3/Q.smali', 'n3/V.smali', 'com/openminis/app/letian/SyncActivity.smali', 'c/j.smali'):
        shutil.copyfile(tree / relative, primary / relative)
    patch_handoff(primary)
    patch_version_metadata(primary)
    run('java', '-jar', '/opt/msx107/smali.jar', 'a', primary, '-o', out / 'classes.dex')
out = ROOT / 'out'
classes = ROOT / 'classes'
classes.mkdir(exist_ok=True)
jars = sorted(Path('/opt/msx107/lib_jcifs').glob('*.jar'))
abi=ROOT/'abi-compile-only'
abi.mkdir(exist_ok=True)
run('javac','-source','8','-target','8','-d',abi,*sorted((ROOT/'tests/abi').rglob('*.java')))
classpath = os.pathsep.join(str(value) for value in jars + [Path('/opt/minis-background-20261023/android-sdk/platforms/android-34/android.jar'),abi])
run('javac', '-encoding', 'UTF-8', '-source', '8', '-target', '8', '-cp', classpath, '-d', classes, *sorted((ROOT / 'src').rglob('*.java')))
run('jar', 'cf', out / 'sync.jar', '-C', classes, '.')
dex = ROOT / 'dex'
dex.mkdir(exist_ok=True)
run('java', '-Xmx1g', '-cp', BT / 'lib/d8.jar', 'com.android.tools.r8.D8', '--release', '--min-api', '26', '--lib', '/opt/minis-background-20261023/android-sdk/platforms/android-34/android.jar', '--output', dex, out / 'sync.jar', *jars)
run('python3', ROOT / 'patch_axml_v2.py', base, str(VERSION_CODE), '1.14.6', VERSION_NAME, out / 'AndroidManifest.xml')
run('python3', ROOT / 'scripts/patch_background_manifest.py', out / 'AndroidManifest.xml')
for test in (False, True):
    name = 'minis-sync-' + VERSION_NAME + ('-test' if test else '')
    manifest = (out / 'AndroidManifest.xml').read_bytes()
    if test:
        manifest = manifest.replace('site.zhangyue.minis'.encode('utf-16-le'), 'test.zhangyue.minis'.encode('utf-16-le'))
        manifest = manifest.replace(b'site.zhangyue.minis', b'test.zhangyue.minis')
        manifest = manifest.replace('test.zhangyue.minis.sync.BackgroundSyncJob'.encode('utf-16-le'), 'site.zhangyue.minis.sync.BackgroundSyncJob'.encode('utf-16-le'))
        manifest = manifest.replace('test.zhangyue.minis.sync.SyncExecutionService'.encode('utf-16-le'), 'site.zhangyue.minis.sync.SyncExecutionService'.encode('utf-16-le'))
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
        for library in sorted((ROOT / 'native-libs').rglob('*.so')):
            output.write(library, 'lib/' + str(library.relative_to(ROOT / 'native-libs')))
        for asset in sorted((ROOT / 'assets').rglob('*')):
            if asset.is_file(): output.write(asset, 'assets/' + str(asset.relative_to(ROOT / 'assets')), compress_type=zipfile.ZIP_STORED if asset.name.endswith('.tar.gz') else zipfile.ZIP_DEFLATED)
    run(BT / 'zipalign', '-p', '-f', '4', out / (name + '-unsigned.apk'), out / (name + '-aligned.apk'))
    signing = []
    lineage = os.environ.get('MINIS_SIGNING_LINEAGE')
    if lineage:
        signing = ['--ks', '/opt/minis-sync-build/keystore.jks', '--ks-key-alias', os.environ['MINIS_OLD_SIGNING_ALIAS'], '--ks-pass', 'env:MINIS_OLD_SIGNING_PASSWORD', '--next-signer']
    signing += ['--ks', '/opt/keystore-vault/notesvault.keystore', '--ks-key-alias', 'notesvault', '--ks-pass', 'env:MINIS_SIGNING_PASSWORD']
    if lineage:
        signing += ['--lineage', lineage, '--rotation-min-sdk-version', '28']
    run(BT / 'apksigner', 'sign', *signing, '--out', out / (name + '.apk'), out / (name + '-aligned.apk'))
    verification = subprocess.run([str(BT / 'apksigner'), 'verify', '--verbose', '--print-certs', str(out / (name + '.apk'))], check=True, capture_output=True, text=True)
    if 'b5aaac0eb0369165d34ee3bae1cddd637eaa2bc3566adf9e3f2dc2f5a1a473e4' not in verification.stdout:
        raise RuntimeError('APK certificate does not match the Letian family key')
    print(verification.stdout)
