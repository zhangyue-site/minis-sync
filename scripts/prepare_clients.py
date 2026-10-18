#!/usr/bin/env python3
"""Package official ARM64 artifacts, without any user config or credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def build(source, output):
    root = source / 'runtime-root'
    required = [root/'usr/bin/node', root/'usr/bin/python3', root/'lib/ld-musl-aarch64.so.1', source/'opencode', source/'zcode.cjs', source/'zcode-builtin.json']
    for path in required:
        if not path.is_file():
            raise ValueError('missing official artifact: ' + str(path))
    output.mkdir(parents=True, exist_ok=True)
    target = output/'runtime.tar.gz'
    with tarfile.open(target, 'w:gz') as archive:
        for relative in ['lib', 'usr', 'etc/ssl']:
            parent = root/relative
            if parent.exists():
                for path in sorted(parent.rglob('*')):
                    if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
                        continue
                    # Executables and library symlinks retain package modes and targets.
                    archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
        for name in ['opencode', 'zcode.cjs', 'zcode-builtin.json']:
            archive.add(source/name, arcname=name, recursive=False)
    manifest = {'format': 1, 'runtime': 'runtime-v1', 'architecture': 'aarch64',
                'sha256': digest(target),
                'bytes': target.stat().st_size,
                'artifacts': {p.name: digest(p) for p in required},
                'sources': {'opencode': 'https://registry.npmjs.org/opencode-linux-arm64-musl/1.18.3',
                            'alpine': 'https://dl-cdn.alpinelinux.org/alpine/v3.23/main/aarch64/',
                            'zcode': 'Official ZCode Desktop resources/glm/zcode.cjs and resources/config/provider/zcode-builtin.json; never personal provider_config.json'}}
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps({'payload_bytes': manifest['bytes'], 'sha256': manifest['sha256']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.output)
