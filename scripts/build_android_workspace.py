#!/usr/bin/env python3
"""On 106: rebuild an isolated QA APK from the already-tested 1.14.12 hooks.

No deployment, OTA modification, production database or phone installation.
The existing signing scripts keep all credentials on the build host.
"""
import argparse
from pathlib import Path
import os
import shutil
import subprocess


def build(root, baseline):
    root=root.resolve();baseline=baseline.resolve()
    if root==baseline:raise ValueError('isolated_build_root_required')
    required=['src/site/zhangyue/minis/handoff','abi-compile-only','out/minis-sync-1.14.12-test.apk']
    for name in required:
        source=baseline/name;target=root/name
        if target.exists():continue
        target.parent.mkdir(parents=True,exist_ok=True)
        if source.is_dir():shutil.copytree(source,target)
        else:shutil.copy2(source,target)
    for name in ('repack_java.py','build_native.py'):
        source=(baseline/'scripts'/name).read_text()
        old="/opt/minis-repair-20261020"
        if old not in source:raise ValueError('unexpected_upstream_build_script')
        target=root/'scripts'/name;target.parent.mkdir(exist_ok=True)
        target.write_text(source.replace(old,str(root)))
    for name in ('classes','dex','out'):(root/name).mkdir(exist_ok=True)
    environment=dict(os.environ,MINIS_QA_ONLY='1')
    subprocess.run(['python3',str(root/'scripts/repack_java.py')],env=environment,check=True)
    print('Unpublished QA APK:',root/'out/minis-sync-1.14.12-test.apk')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,default=Path('/opt/minis-repair-20261020'))
    args=parser.parse_args();build(args.root,args.baseline)
