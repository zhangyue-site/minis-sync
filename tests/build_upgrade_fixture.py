import os
from pathlib import Path
import subprocess
import zipfile

root = Path('/opt/minis-fix-20261018')
base = Path('/opt/minis-sync-build/minis-sync-1.14.5-family.apk')
unsigned = root / 'out/upgrade-baseline-unsigned.apk'
aligned = root / 'out/upgrade-baseline-aligned.apk'
result = root / 'out/upgrade-baseline.apk'
with zipfile.ZipFile(base) as original, zipfile.ZipFile(unsigned, 'w', zipfile.ZIP_DEFLATED) as output:
    for entry in original.infolist():
        if entry.filename.startswith('META-INF/'):
            continue
        content = original.read(entry.filename)
        if entry.filename == 'AndroidManifest.xml':
            content = content.replace('site.zhangyue.minis'.encode('utf-16-le'), 'test.zhangyue.minis'.encode('utf-16-le'))
            content = content.replace(b'site.zhangyue.minis', b'test.zhangyue.minis')
        if entry.filename == 'resources.arsc' or entry.filename.endswith('.so'):
            entry.compress_type = zipfile.ZIP_STORED
        output.writestr(entry, content)
tools = Path('/opt/android-sdk/build-tools/35.0.0')
subprocess.run([str(tools / 'zipalign'), '-p', '-f', '4', str(unsigned), str(aligned)], check=True)
subprocess.run([str(tools / 'apksigner'), 'sign', '--ks', '/opt/keystore-vault/notesvault.keystore', '--ks-key-alias', 'notesvault', '--ks-pass', 'env:MINIS_SIGNING_PASSWORD', '--out', str(result), str(aligned)], check=True)
