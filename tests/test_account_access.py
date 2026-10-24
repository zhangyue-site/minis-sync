import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import unittest,json,tempfile
from pathlib import Path
from unittest.mock import Mock,patch
import account_access as access
import owner_features
class AccessTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();p=Path(self.tmp.name)/'owner.json';p.write_text(json.dumps({'owner_user_ids':[101]}));self.p=patch.object(owner_features,'CONFIG',p);self.p.start();self.addCleanup(self.p.stop);self.addCleanup(self.tmp.cleanup)
 def handler(self,user=None):
  h=Mock();h._auth.return_value=(user,'invalid_or_expired' if not user else None);return h
 def test_anonymous_all_shared_private_routes_denied(self):
  for path in ['/api/vault/info','/api/vault/download','/api/quota/summary','/api/hub/console/exec','/api/hub/appkey/challenge','/api/qr/all','/api/sync/upload','/api/camera/result']:
   with self.subTest(path=path):
    h=self.handler();self.assertTrue(access.enforce(h,None,path));self.assertEqual(h._json.call_args.args[0],401)
 def test_other_account_even_admin_role_cannot_read_owner_resources(self):
  for role in ('user','admin','app'):
   h=self.handler({'user_id':102,'role':role});self.assertTrue(access.enforce(h,None,'/api/hub/sessions'));self.assertEqual(h._json.call_args.args[0],403)
 def test_real_configured_owner_allowed(self):
  h=self.handler({'user_id':101});self.assertFalse(access.enforce(h,None,'/api/hub/console/exec'));h._json.assert_not_called()
 def test_per_user_minis_and_identity_routes_unaffected(self):
  for p in ['/api/minis/snapshots','/api/me','/api/login','/api/register','/api/player/progress']:
   h=self.handler();self.assertFalse(access.enforce(h,None,p));h._auth.assert_not_called()
 def test_missing_config_fails_closed(self):
  with patch.object(owner_features,'CONFIG',Path('/missing-owner-config')):self.assertFalse(access.is_owner({'user_id':101}))
if __name__=='__main__':unittest.main()
