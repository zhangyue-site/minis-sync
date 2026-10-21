#!/usr/bin/env python3
"""Hosted macOS unsigned build. No Apple credentials are accepted here."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile
import zipfile

BUNDLE='site.zhangyue.minis.sync'

def run(*arguments, cwd=None, env=None):
    subprocess.run([str(a) for a in arguments],cwd=cwd,env=env,check=True)

def configure(root):
    config=root/'src/ios/Configs/ProviderCustomization.xcconfig'
    if not config.exists():shutil.copy2(config.with_suffix('.xcconfig.example'),config)
    # This app is a separate installation. Use its own display name while
    # preserving the real Minis UI and sandbox implementation.
    p=root/'src/ios/Info.plist'
    data=plistlib.loads(p.read_bytes());data['CFBundleDisplayName']='Minis 同步版'
    p.write_bytes(plistlib.dumps(data,sort_keys=False))

def dependencies(root):
    # Homebrew ships LLD separately from LLVM. Prove the exact Linux ARM64
    # cross-link needed by iSH before spending time compiling FFmpeg.
    llvm=Path(subprocess.check_output(['brew','--prefix','llvm'],text=True).strip())/'bin/clang'
    with tempfile.TemporaryDirectory(prefix='minis-vdso-toolchain-') as temporary:
        probe=Path(temporary)/'probe.so'
        subprocess.run([str(llvm),'-target','aarch64-linux-gnu','-fuse-ld=lld','-nostdlib','-shared','-ffreestanding','-x','c','-','-o',str(probe)],input='int minis_link_probe(void) { return 0; }\n',text=True,check=True)
        header=probe.read_bytes()[:20]
        if header[:4]!=b'\x7fELF' or int.from_bytes(header[18:20],'little')!=183:raise ValueError('vdso_linker_not_aarch64_elf')
    for script in ('build_lame.sh','build_ffmpeg.sh','build_ish.sh','prepare_alpine_rootfs.sh'):
        run('bash',root/'deps'/script,cwd=root)
    # The deliverable is an iPhone device build; avoid spending disk and
    # compilation time on a second, unusable simulator iSH/rclone slice.
    build=root/'deps/build/rclone/device';include=build/'include'
    include.mkdir(parents=True,exist_ok=True)
    sdk=subprocess.check_output(['xcrun','--sdk','iphoneos','--show-sdk-path'],text=True).strip()
    clang=subprocess.check_output(['xcrun','--sdk','iphoneos','-f','clang'],text=True).strip()
    env=dict(os.environ,CGO_ENABLED='1',GOOS='ios',GOARCH='arm64',CC=f'{clang} -isysroot {sdk} -miphoneos-version-min=16.0 -arch arm64')
    run('go','build','-buildmode=c-archive','-trimpath','-ldflags=-s -w','-o',build/'librclone.a','./librclone',cwd=root/'deps/rclone-mobile',env=env)
    shutil.move(build/'librclone.h',include/'librclone.h')
    target=root/'deps/frameworks/Rclone.xcframework'
    if target.exists():shutil.rmtree(target)
    run('xcodebuild','-create-xcframework','-library',build/'librclone.a','-headers',include,'-output',target)
    run('go','clean','-cache',cwd=root/'deps/rclone-mobile')

def build(root,version,number,output):
    if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?',version) or not re.fullmatch(r'[1-9]\d{0,8}',number):raise ValueError('invalid_version_or_number')
    output.mkdir(parents=True,exist_ok=True)
    expected=['deps/libs/libish.a','deps/libs/libish_emu.a','deps/libs/libfakefs.a','deps/frameworks/FFmpeg.framework','deps/frameworks/Rclone.xcframework','deps/resources/alpine-rootfs.zip']
    for name in expected:
        if not (root/name).exists():raise FileNotFoundError('native_dependency_missing:'+name)
    derived=output/'DerivedData'
    args=['xcodebuild','-project',str(root/'src/ios/Minis.xcodeproj'),'-scheme','Minis','-configuration','Release','-destination','generic/platform=iOS','-derivedDataPath',str(derived),'-resultBundlePath',str(output/'build.xcresult'),'CODE_SIGNING_ALLOWED=NO','CODE_SIGNING_REQUIRED=NO','CODE_SIGN_IDENTITY=','MARKETING_VERSION='+version,'CURRENT_PROJECT_VERSION='+number,'build']
    with (output/'xcodebuild.log').open('w') as log:
        result=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT)
    if result.returncode:
        print('\n'.join((output/'xcodebuild.log').read_text(errors='replace').splitlines()[-100:]))
        raise RuntimeError('xcodebuild_failed')
    apps=list((derived/'Build/Products/Release-iphoneos').glob('*.app'))
    if len(apps)!=1:raise ValueError('expected_exactly_one_app')
    app=apps[0];info=plistlib.loads((app/'Info.plist').read_bytes())
    if info['CFBundleIdentifier']!=BUNDLE or info['CFBundleShortVersionString']!=version or str(info['CFBundleVersion'])!=number:raise ValueError('built_app_identity_mismatch')
    payload=output/'package/Payload';payload.mkdir(parents=True,exist_ok=True)
    run('ditto',app,payload/app.name)
    unsigned=output/'minis-ios-unsigned.ipa'
    run('ditto','-c','-k','--keepParent',payload,unsigned)
    with zipfile.ZipFile(unsigned) as archive:
        if not any(n.startswith('Payload/') and n.endswith('.app/Info.plist') for n in archive.namelist()):raise ValueError('invalid_ipa_layout')
    metadata={'source_revision':os.environ.get('GITHUB_SHA'),'upstream':subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),'bundle_id':BUNDLE,'version':version,'build_number':number,'signed':False,'ipa_sha256':hashlib.sha256(unsigned.read_bytes()).hexdigest(),'bytes':unsigned.stat().st_size,'runner':os.environ.get('ImageOS'),'extensions':[p.name for p in (app/'PlugIns').glob('*.appex')]}
    (output/'build-manifest.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps(metadata))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('stage',choices=['configure','dependencies','build']);parser.add_argument('--upstream',type=Path,required=True)
    parser.add_argument('--version',default='1.0.0');parser.add_argument('--number',default='1');parser.add_argument('--output',type=Path,default=Path('artifacts'))
    a=parser.parse_args();root=a.upstream.resolve()
    if a.stage=='configure':configure(root)
    elif a.stage=='dependencies':dependencies(root)
    else:build(root,a.version,a.number,a.output.resolve())
