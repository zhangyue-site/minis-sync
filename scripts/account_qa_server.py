"""Loopback-only synthetic account acceptance service. Never uses production data."""
from pathlib import Path
import argparse,os,sqlite3,sys,time
p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--port',type=int,default=18991);a=p.parse_args()
root=Path(a.root).resolve();root.mkdir(parents=True,exist_ok=True,mode=0o700)
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server/v3'))
os.environ['MINIS_V3_ROOT']=str(root/'store');os.environ['MINIS_FRONT_STATE']=str(root/'front-state')
from minis_cloud import CloudStore
import front
store=CloudStore(root/'store');db=root/'auth.db'
with sqlite3.connect(db) as c:
 c.execute('CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id TEXT,revoked INTEGER,expires_at INTEGER)')
 for token,uid in [('qa-alpha','1'),('qa-beta','2')]:c.execute('INSERT OR REPLACE INTO tokens VALUES(?,?,0,?)',(token,uid,int(time.time())+86400))
 c.execute('CREATE TABLE IF NOT EXISTS minis_auth_replica(singleton INTEGER PRIMARY KEY,synced_at INTEGER)')
 c.execute('INSERT OR REPLACE INTO minis_auth_replica VALUES(1,?)',(int(time.time()),))
db.chmod(0o600)
front.DB=str(db);front.AUTH_MAX_AGE=86400;front.STORE=str(store.snapshots.root)
# Direct loopback clients have no nginx to consume X-Accel-Redirect.
front.try_accel=lambda *args: False
front.PY=sys.executable;front.RPC=str(Path(front.__file__).parent/'minis_v3_rpc.py')
print('QA only loopback port '+str(a.port),flush=True)
front.ThreadingHTTPServer(('127.0.0.1',a.port),front.H).serve_forever()
