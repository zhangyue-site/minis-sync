"""Build on the existing signing host; output remains an unpublished candidate."""
from pathlib import Path
import os,runpy,shlex,sys
root=Path(os.environ.get('MINIS_BUILD_ROOT','/opt/minis-account-20261024'))
os.environ['MINIS_BUILD_ROOT']=str(root)
os.environ['JAVA_TOOL_OPTIONS']='-Xmx1g'
for row in Path('/opt/keystore-vault/README.md').read_text().splitlines():
    if row.startswith('| notesvault.keystore '):os.environ['MINIS_SIGNING_PASSWORD']=row.split('|')[3].strip().strip('`')
old=dict(part.split('=',1) for part in shlex.split(Path('/opt/minis-sync-build/keystore.info').read_text()))
os.environ['MINIS_OLD_SIGNING_PASSWORD']=old['storepass']
os.environ['MINIS_OLD_SIGNING_ALIAS']=old['alias']
os.environ['MINIS_SIGNING_LINEAGE']='/opt/keystore-vault/minis-sync-family.lineage'
os.chdir(root);sys.path.insert(0,str(root))
from scripts.prepare_verified_base import prepare
prepare()
runpy.run_path(str(root/'build.py'),run_name='__main__')
