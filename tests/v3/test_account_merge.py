import io,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'server/v3'))
from minis_account_merge import merge
from minis_store import InvalidBackup
import test_merge

class AccountMergeTests(unittest.TestCase):
    def test_union_preserves_both_phones_providers_models_and_keys(self):
        missing=object()
        local={'instances':[{'id':'a','label':'Phone A'}],'modelEntries':[{'providerInstanceId':'a','model':{'id':'m'}}]}
        cloud={'instances':[{'id':'b','label':'Phone B'}],'modelEntries':[{'providerInstanceId':'b','model':{'id':'m'}}]}
        result,conflict=merge(local,cloud,missing)
        self.assertFalse(conflict);self.assertEqual({r['id'] for r in result['instances']},{'a','b'});self.assertEqual(len(result['modelEntries']),2)
        result,conflict=merge({'providers':[{'instanceId':'a','apiKey':'local'}]},{'providers':[{'instanceId':'b','apiKey':'remote'}]},missing)
        self.assertEqual(len(result['providers']),2);self.assertFalse(conflict)
    def test_conflicting_existing_key_keeps_cloud_and_reports_recovery(self):
        r,bad=merge({'providers':[{'instanceId':'a','apiKey':'new'}]},{'providers':[{'instanceId':'a','apiKey':'old'}]},object())
        self.assertTrue(bad);self.assertEqual(r['providers'][0]['apiKey'],'old')
    def test_omission_does_not_delete_private_config(self):
        r,bad=merge({'instances':[]},{'instances':[{'id':'a','label':'private'}]},object())
        self.assertEqual(r['instances'],[{'id':'a','label':'private'}]);self.assertFalse(bad)
    def test_duplicate_and_unstable_ids_are_rejected(self):
        for rows in [[{'id':'a'},{'id':'a'}],[{'label':'missing id'}]]:
            with self.assertRaises(InvalidBackup):merge({'instances':rows},{'instances':[]},object())
    def test_actual_chats_scope_merge_preserves_private_provider_union(self):
        # Exercise the existing backup merger, not just the helper.
        f=test_merge.MergeTests(methodName='test_first_sync_union');f.setUp()
        try:
            l=f.package(extra={'data/provider_config.json':json.dumps({'instances':[{'id':'a','label':'local'}]}).encode()})
            c=f.package(extra={'data/provider_config.json':json.dumps({'instances':[{'id':'b','label':'cloud'}]}).encode()})
            _,_,_=f.merge(None,l,c,scope='chats')
            import zipfile
            with zipfile.ZipFile(f.root/'result') as z:r=json.loads(z.read('data/provider_config.json'))
            self.assertEqual({row['id'] for row in r['instances']},{'a','b'})
        finally:f.doCleanups()

if __name__=='__main__':unittest.main()
