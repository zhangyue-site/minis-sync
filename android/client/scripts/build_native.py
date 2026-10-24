"""Compile with NDK, or verify unchanged prebuilt adapters on the build host."""
from pathlib import Path
import hashlib,json,os,subprocess
ROOT=Path(os.environ.get('MINIS_BUILD_ROOT','/opt/minis-account-20261024'))
BIN=Path(os.environ.get('MINIS_NDK_BIN','/opt/android-sdk/ndk/27.1.12297006/toolchains/llvm/prebuilt/linux-x86_64/bin'))
def build():
    if not (BIN/'aarch64-linux-android26-clang').exists():
        m=json.loads((ROOT/'native-manifest.json').read_text())
        if hashlib.sha256((ROOT/'src/native/attachment_publish.c').read_bytes()).hexdigest()!=m['source_sha256']:raise RuntimeError('native_source_changed_requires_ndk')
        for name,sha in m['libraries'].items():
            p=ROOT/'native-libs'/name
            if hashlib.sha256(p.read_bytes()).hexdigest()!=sha:raise RuntimeError('prebuilt_native_digest_mismatch')
        print('Verified unchanged prebuilt native adapters; no NDK compilation claimed')
        return
    for abi,target in [('arm64-v8a','aarch64-linux-android26'),('armeabi-v7a','armv7a-linux-androideabi26'),('x86_64','x86_64-linux-android26'),('x86','i686-linux-android26')]:
        output=ROOT/'native-libs'/abi/'libminis_attachment_publish.so';output.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run([str(BIN/(target+'-clang')),'-shared','-fPIC','-O2','-Wall','-Wextra','-Werror','-Wl,-z,relro,-z,now','-o',str(output),str(ROOT/'src/native/attachment_publish.c')],check=True)
if __name__=='__main__':build()
