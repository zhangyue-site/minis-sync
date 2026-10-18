import hashlib
import os
from pathlib import Path
import sqlite3

from minis_archive import cos_internal_client, policy
from minis_store import validate_package

root = Path('/opt/notes-site/minis-store')
connection = sqlite3.connect('file:' + str(root / 'snapshots.db') + '?mode=ro', uri=True)
settings = policy()
client = cos_internal_client(settings)
for uid, sha, size in connection.execute('SELECT user_id,sha256,bytes FROM snapshots'):
    destination = root / str(uid) / (sha + '.minisbak')
    if destination.exists():
        continue
    temporary = destination.with_suffix('.restoring')
    digest = hashlib.sha256()
    response = client.get_object(Bucket=settings['bucket'], Key='minis-store/' + str(uid) + '/' + sha + '.minisbak')
    body = response['Body'].get_raw_stream()
    try:
        with temporary.open('wb') as output:
            for block in iter(lambda: body.read(1024 * 1024), b''):
                output.write(block)
                digest.update(block)
        if digest.hexdigest() != sha or temporary.stat().st_size != size:
            raise RuntimeError('restore_digest_mismatch')
        validate_package(temporary)
        os.chmod(temporary, 0o600)
        temporary.replace(destination)
        print('restored', uid, sha, size, flush=True)
    finally:
        body.close()
        temporary.unlink(missing_ok=True)
