#!/usr/bin/env python3
"""Apply this repository's iOS overlay to an isolated, pinned upstream checkout."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import re
import subprocess

PIN = 'b4c0661d5631ebab4d1a2e6f3fd4c805d4030a6c'
ROOT = Path(__file__).resolve().parents[1]
MARKER = '// Minis workspace sync overlay v1'

def prepare(upstream, bundle_id="site.zhangyue.minis.workspaceqa"):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+){2,}",bundle_id):raise ValueError("invalid_bundle_id")
    upstream = upstream.resolve()
    commit = subprocess.check_output(['git','-C',str(upstream),'rev-parse','HEAD'],text=True).strip()
    if commit != PIN: raise ValueError('upstream_commit_mismatch')
    ios = upstream/'src/ios'
    previous_manifest=ios/'MinisWorkspace/overlay-manifest.json'
    if previous_manifest.exists() and json.loads(previous_manifest.read_text()).get('bundle_id')!=bundle_id:
        raise ValueError('namespace_change_requires_fresh_checkout')
    state_path=upstream/'.minis-workspace-overlay-state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    patched=[]
    def patch(path, transform):
        target = ios/path
        current = target.read_text()
        original = subprocess.check_output(['git','-C',str(upstream),'show',f'HEAD:src/ios/{path}'],text=True)
        key=str(path)
        if current != original and hashlib.sha256(current.encode()).hexdigest()!=state.get(key):raise ValueError('unrelated_upstream_changes:'+str(path))
        changed = transform(original)
        if changed == original:raise ValueError('patch_anchor_not_found:'+str(path))
        target.write_text(changed+'\n'+MARKER+'\n')
        patched.append(key)
    overlay = ios/'MinisWorkspace'
    overlay.mkdir(exist_ok=True)
    sources = sorted((ROOT/'ios/MinisSyncCore/Sources/MinisSyncCore').glob('*.swift'))+sorted((ROOT/'ios/Overlay').glob('*.swift'))
    for source in sources:shutil.copy2(source,overlay/source.name)
    def workspace_export(s):
        s=s.replace('        var allowResume = false','        var allowResume = false\n        var workspaceTransport = false',1)
        s=s.replace('             allowResume: Bool = false) {','             allowResume: Bool = false, workspaceTransport: Bool = false) {',1)
        s=s.replace('            self.allowResume = allowResume','            self.allowResume = allowResume\n            self.workspaceTransport = workspaceTransport',1)
        start=s.index('        BackupRescueIndex.build(')
        end=s.index('.writeIgnoringFailure(to: staging)',start)+len('.writeIgnoringFailure(to: staging)')
        s=s[:start]+'        if !options.workspaceTransport {\n'+s[start:end]+'\n        }'+s[end:]
        start=s.index('        try? BackupJSONFile.write(\n            manifest, to: staging.appendingPathComponent(BackupRescueIndex.manifestCopyFilename))')
        end=start+len('        try? BackupJSONFile.write(\n            manifest, to: staging.appendingPathComponent(BackupRescueIndex.manifestCopyFilename))')
        s=s[:start]+'        if !options.workspaceTransport {\n'+s[start:end]+'\n        }'+s[end:]
        return s
    patch(Path('Agent/Backup/BackupExporter.swift'),workspace_export)
    patch(Path('Agent/Chat/ChatStore.swift'),lambda s:s+'\n'+(ROOT/'ios/Overlay/ChatStoreWorkspaceExtension.swift.inc').read_text())
    patch(Path('Views/Settings/BackupAndRestoreView.swift'),lambda s:s.replace('        VStack(spacing: 0) {','''        VStack(spacing: 0) {
            NavigationLink(destination: MinisWorkspaceSettings()) {
                Label("安卓与 iPhone 同步", systemImage: "arrow.triangle.2.circlepath")
                    .padding(.vertical, 8)
            }
''',1))
    patch(Path('MinisApp.swift'),lambda s:s.replace('    init() {','    init() {\n        Task { @MainActor in MinisWorkspaceController.shared.startAutomatic() }',1))
    def stream(s):
        a=s.index('        if entry.isCompressed {',s.index('private static func writeEntry'))
        b=s.index('\n        }',a)+10
        return s[:a]+'''        if entry.isCompressed {
            try RawDeflate.extract(input: handle, compressedBytes: entry.compressedSize,
                                   expectedBytes: entry.uncompressedSize, output: sink)
            return
        }'''+s[b:]
    patch(Path('Agent/Backup/BackupZipExtractor.swift'),stream)
    def project(s):
        builds=[];refs=[];group=[];entries=[]
        for file in sources:
            rid=hashlib.sha256(('workspace-ref:'+file.name).encode()).hexdigest()[:24].upper()
            bid=hashlib.sha256(('workspace-build:'+file.name).encode()).hexdigest()[:24].upper()
            builds.append(f'\t\t{bid} /* {file.name} in Sources */ = {{isa = PBXBuildFile; fileRef = {rid}; }};')
            refs.append(f'\t\t{rid} /* {file.name} */ = {{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = MinisWorkspace/{file.name}; sourceTree = "<group>"; }};')
            group.append(f'\t\t\t\t{rid} /* {file.name} */,')
            entries.append(f'\t\t\t\t{bid} /* {file.name} in Sources */,')
        s=s.replace('/* Begin PBXBuildFile section */','/* Begin PBXBuildFile section */\n'+'\n'.join(builds),1)
        s=s.replace('/* Begin PBXFileReference section */','/* Begin PBXFileReference section */\n'+'\n'.join(refs),1)
        s=s.replace('\t\t\t\tE51000011 /* MinisApp.swift */,','\t\t\t\tE51000011 /* MinisApp.swift */,\n'+'\n'.join(group),1)
        anchor='\t\t\t\tBKP000105 /* BackupExporter.swift in Sources */,'
        s=s.replace(anchor,anchor+'\n'+'\n'.join(entries),1)
        # Isolate every app/extension identifier from the user's official Minis.
        s=s.replace('com.openminis.app',bundle_id).replace('com.openminis.Minis',bundle_id)
        return s
    patch(Path('Minis.xcodeproj/project.pbxproj'),project)
    patch(Path('ShareExtension/ShareViewController.swift'),lambda s:s.replace('minis://share',bundle_id+'://share'))
    patch(Path('Shared/DeepLinkRouter.swift'),lambda s:s.replace('guard url.scheme == "minis", let host = url.host else {','guard (url.scheme == "minis" || url.scheme == "'+bundle_id+'"), let host = url.host else {',1))
    # The QA app must never join the official app's containers or CloudKit.
    for target in ios.rglob('*'):
        if target.suffix not in ('.swift','.entitlements') and target.name!='Info.plist':continue
        current=target.read_text()
        changed=current.replace('group.com.openminis.app','group.'+bundle_id).replace('iCloud.com.openminis.app','iCloud.'+bundle_id).replace('com.openminis.app.files',bundle_id+'.files')
        if target==ios/'Info.plist' and '<string>'+bundle_id+'</string>' not in changed:
            changed=changed.replace('<string>minis</string>','<string>minis</string>\n                <string>'+bundle_id+'</string>',1)
        if changed!=current:target.write_text(changed)
    state_path.write_text(json.dumps({key:hashlib.sha256((ios/key).read_bytes()).hexdigest() for key in patched},indent=2)+'\n')
    metadata={'patcher_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'extension_sha256':hashlib.sha256((ROOT/'ios/Overlay/ChatStoreWorkspaceExtension.swift.inc').read_bytes()).hexdigest(),'bundle_id':bundle_id,'upstream':PIN,'sources':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},'production_modified':False}
    (overlay/'overlay-manifest.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps({'ok':True,'project':str(ios/'Minis.xcodeproj'),'upstream':PIN,'files':len(sources)}))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--upstream',type=Path,required=True);parser.add_argument('--bundle-id',default='site.zhangyue.minis.workspaceqa')
    args=parser.parse_args();prepare(args.upstream,args.bundle_id)
