import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('prepare_clients',Path(__file__).resolve().parents[1]/'scripts/prepare_clients.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class PayloadTests(unittest.TestCase):
    def fixture(self, root):
        for name in ['runtime-root/usr/bin/node','runtime-root/usr/bin/python3','runtime-root/lib/ld-musl-aarch64.so.1','opencode','zcode.cjs','zcode-builtin.json']:
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('public-runtime-fixture')
    def test_payload_excludes_user_credentials_and_manifest_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.fixture(root)
            secret=root/'runtime-root/root/.zcode/v2/credentials.json';secret.parent.mkdir(parents=True);secret.write_text('private-marker')
            other=root/'credentials.json';other.write_text('private-marker')
            output=root/'out';module.build(root,output)
            manifest=json.loads((output/'manifest.json').read_text())
            self.assertEqual(manifest['sha256'],hashlib.sha256((output/'runtime.tar.gz').read_bytes()).hexdigest())
            with tarfile.open(output/'runtime.tar.gz') as t:
                self.assertFalse(any('credentials' in x or x.startswith('root/') for x in t.getnames()))
                self.assertEqual(t.extractfile('zcode.cjs').read(),b'public-runtime-fixture')
    def test_incomplete_official_artifacts_fail_before_packaging(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with self.assertRaises(ValueError):module.build(root,root/'out')
            self.assertFalse((root/'out/runtime.tar.gz').exists())

if __name__=='__main__':unittest.main()
