"""Rebuild both DEX hook sets from the verified 119 release, then patch versions.

The old 107 smali recipe contains obsolete anchors. Never silently patch a
mutable old directory or assume an unverified APK contains the required hooks.
"""
from pathlib import Path
import hashlib,os,re,shutil,subprocess,zipfile
ROOT=Path(os.environ.get('MINIS_BUILD_ROOT','/opt/minis-account-20261024'))
APK=Path(os.environ.get('MINIS_BASE_119_APK','/opt/minis-background-20261023/out/minis-sync-1.14.18.apk'))
VERSION_NAME='1.14.20'
VERSION_CODE=121
EXPECTED='e4b20eee38c41607b8ab561221c2cf62c6875c9f46d1bef5119e918a15076474'
def prepare():
    h=hashlib.sha256()
    with APK.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    if h.hexdigest()!=EXPECTED:raise RuntimeError('upstream_119_digest_mismatch')
    out=ROOT/'out';out.mkdir(exist_ok=True)
    import json
    receipt=out/'verified-dex.json'
    if os.environ.get('MINIS_REUSE_VERIFIED_DEX')=='1' and receipt.is_file():
        known=json.loads(receipt.read_text())
        if known.get('input_apk')!=EXPECTED:raise RuntimeError('dex_input_changed')
        if known.get('version_code')!=VERSION_CODE:os.environ.pop('MINIS_REUSE_VERIFIED_DEX',None);return prepare()
        for name,sha in known['files'].items():
            if hashlib.sha256((out/name).read_bytes()).hexdigest()!=sha:raise RuntimeError('verified_dex_changed')
        os.environ['MINIS_REUSE_ASSEMBLY']='1';return

    with zipfile.ZipFile(APK) as z:
        for dex,folder in [('classes.dex','primary-smali'),('classes2.dex','smali')]:
            raw=out/('input-'+dex);raw.write_bytes(z.read(dex));tree=ROOT/folder
            if tree.exists():shutil.rmtree(tree)
            subprocess.run(['java','-Xmx1g','-jar','/opt/msx107/baksmali.jar','d',str(raw),'-o',str(tree)],check=True)
            # Existing CloudSync, background and installer hooks must be present in each first-loaded DEX.
            if 'CloudSync;->run' not in (tree/'x3/j.smali').read_text():raise RuntimeError('missing_cloud_hook')
            for relative in ['C3/G.smali','L/n.smali','Y3/E0.smali','e1/w.smali','j4/a.smali']:
                p=tree/relative
                if not p.exists():continue
                s=p.read_text();s=s.replace('1.14.18',VERSION_NAME).replace('(build 119)','(build '+str(VERSION_CODE)+')').replace('(119)','('+str(VERSION_CODE)+')').replace('"119"','"'+str(VERSION_CODE)+'"');p.write_text(s)
            for relative,register in [('n3/Q.smali','v11'),('n3/V.smali','v3')]:
                p=tree/relative;s=p.read_text();old='const/16 '+register+', 0x77'
                if relative=='n3/V.smali' and 'OtaVerifier;->verify' in s and old not in s:continue
                if s.count(old)!=1:raise RuntimeError('ota_version_anchor_changed_'+relative)
                p.write_text(s.replace(old,'const/16 '+register+', '+hex(VERSION_CODE)))
            subprocess.run(['java','-Xmx1g','-jar','/opt/msx107/smali.jar','a',str(tree),'-o',str(out/dex)],check=True)
    receipt.write_text(json.dumps({'input_apk':EXPECTED,'version_code':VERSION_CODE,'files':{name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in ['classes.dex','classes2.dex']}}))
    os.environ['MINIS_REUSE_ASSEMBLY']='1'
if __name__=='__main__':prepare()
