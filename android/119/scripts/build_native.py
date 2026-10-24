"""Build the small no-replace file publication adapter with the existing Android NDK."""
from pathlib import Path
import subprocess
ROOT=Path('/opt/minis-background-20261023')
BIN=Path('/opt/android-sdk/ndk/27.1.12297006/toolchains/llvm/prebuilt/linux-x86_64/bin')
def build():
    for abi,target in [('arm64-v8a','aarch64-linux-android26'),('armeabi-v7a','armv7a-linux-androideabi26'),('x86_64','x86_64-linux-android26'),('x86','i686-linux-android26')]:
        output=ROOT/'native-libs'/abi/'libminis_attachment_publish.so';output.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run([str(BIN/(target+'-clang')),'-shared','-fPIC','-O2','-Wall','-Wextra','-Werror','-Wl,-z,relro,-z,now','-o',str(output),str(ROOT/'src/native/attachment_publish.c')],check=True)
if __name__=='__main__':build()
