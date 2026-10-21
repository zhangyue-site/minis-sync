"""Compile the pinned app's actual ZIP reader/writer with the sync core on macOS."""
import argparse,hashlib,json,subprocess,tempfile,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def verify(upstream):
    with tempfile.TemporaryDirectory(prefix='minis-archive-qa-') as directory:
        work=Path(directory)
        source=work/'Probe.swift'
        source.write_text('''import Foundation
struct AppLogger { init(category: String) {} ; func warning(_ message: String) {} }
@main struct Probe {
 static func main() throws {
  let args=CommandLine.arguments
  let source=URL(fileURLWithPath:args[1]), archive=URL(fileURLWithPath:args[2]), unpack=URL(fileURLWithPath:args[3])
  let writer=try BackupZipWriter(url:archive)
  for path in ["manifest.json","files.index.jsonl","blobs.index.jsonl","blobs/payload"] { _ = try writer.addFile(at:source.appendingPathComponent(path),name:path) }
  try writer.close()
  try BackupZipExtractor.extract(archive,to:unpack)
  try BackupZipExtractor.extract(URL(fileURLWithPath:args[4]),to:URL(fileURLWithPath:args[5]))
  print("actual native ZIP64 writer/reader and compressed blob: PASS")
 }
}''')
        binary=work/'probe'
        subprocess.run(['swiftc','-parse-as-library',*map(str,sorted((ROOT/'ios/MinisSyncCore/Sources/MinisSyncCore').glob('*.swift'))),str(upstream/'src/ios/Agent/Backup/BackupZipWriter.swift'),str(upstream/'src/ios/Agent/Backup/BackupZipExtractor.swift'),str(source),'-o',str(binary)],check=True)
        original=work/'original';original.mkdir()
        values={'manifest.json':b'{"test":true}','files.index.jsonl':b'{}\n','blobs.index.jsonl':b'{}\n','blobs/payload':b'x'*2_000_000}
        for name,data in values.items():
            p=original/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        compressed=work/'compressed.zip'
        with zipfile.ZipFile(compressed,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as archive:
            for name,data in values.items():archive.writestr(name,data)
        subprocess.run([str(binary),str(original),str(work/'stored.zip'),str(work/'unpacked'),str(compressed),str(work/'inflated')],check=True)
        for folder in ('unpacked','inflated'):
            for name,data in values.items():assert (work/folder/name).read_bytes()==data
        with zipfile.ZipFile(work/'stored.zip') as archive:
            assert all(archive.read(name)==data for name,data in values.items())
        print('Python ZIP interoperability, 2MB blob exact bytes: PASS')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--upstream',type=Path,required=True);verify(p.parse_args().upstream)
