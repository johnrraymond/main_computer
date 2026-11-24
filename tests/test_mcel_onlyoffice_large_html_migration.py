import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from main_computer import onlyoffice_deployment as d
from tools import mcel_onlyoffice_deployment as tool

class Migration(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.state=Path(temp.name);self.root=tool.ROOT
        self.manifest=json.loads((self.root/'tools/onlyoffice-deployment-large-html.json').read_text())
        self.current=d.file_hashes(self.root,tool.ALL_FILES)
        self.assertEqual({k:self.current[k] for k in self.manifest['corrected']},self.manifest['corrected'])
        self.policy={'schema':1,'mode':'hardened','primary':'dev','roots':{'dev':str(self.root)},'fingerprint':{**self.current,**self.manifest['previous']},'image_tag':'latest','image_id':'sha256:old'}
        self.opts=SimpleNamespace(authorize_cutover=True)
        self.write()
    def write(self): (self.state/d.POLICY_NAME).write_text(json.dumps(self.policy))
    def migrate(self,evidence=None,run=None):
        with patch.object(tool,'no_supervisors'),patch.object(tool,'source_evidence',side_effect=evidence) as checked,patch.object(tool,'image_id',return_value='sha256:old'),patch.object(tool.subprocess,'run',side_effect=run,return_value=SimpleNamespace(returncode=0)) as process:
            tool.migrate_large_html(self.state,self.root,self.opts)
            return checked,process
    def test_recertification_order_and_commit(self):
        checked,process=self.migrate(evidence=[RuntimeError('STALE'),None,RuntimeError('STALE'),None,None])
        self.assertEqual([c.args[0][2:4] for c in process.call_args_list], [['main_computer.auth_bootstrap_service','--root'],['unittest','discover'],['tools.mcel_onlyoffice_cutover','gateway'],['tools.mcel_onlyoffice_cutover','live']])
        self.assertEqual(d.read_policy(self.state)['fingerprint'],self.current)
    def test_certificate_failure_precedes_regression_or_certification(self):
        with patch.object(tool,'no_supervisors'),patch.object(tool,'source_evidence') as evidence,patch.object(tool,'image_id') as image,patch.object(tool.subprocess,'run',return_value=SimpleNamespace(returncode=1)) as process:
            with self.assertRaisesRegex(RuntimeError,'LARGE_HTML_CERTIFICATE_PREFLIGHT_FAILED'):tool.migrate_large_html(self.state,self.root,self.opts)
            evidence.assert_not_called();image.assert_not_called();process.assert_called_once()
        self.assertEqual(d.read_policy(self.state),self.policy)
    def test_regression_failure_does_not_commit(self):
        with self.assertRaisesRegex(RuntimeError,'LARGE_HTML_GATEWAY_REGRESSION_FAILED'):
            self.migrate(run=[SimpleNamespace(returncode=0),SimpleNamespace(returncode=1)])
        self.assertEqual(d.read_policy(self.state),self.policy)
    def test_source_matched_evidence_reused(self):
        checked,process=self.migrate()
        self.assertEqual(process.call_count,2)
        self.assertEqual(d.read_policy(self.state)['fingerprint'],self.current)
    def test_unrelated_change_denied(self):
        k=next(k for k in self.current if k not in self.manifest['corrected'])
        self.policy['fingerprint'][k]='0'*64;self.write()
        with self.assertRaisesRegex(RuntimeError,'LARGE_HTML_SOURCE_MISMATCH'):self.migrate()
        self.assertEqual(d.read_policy(self.state),self.policy)
if __name__=='__main__':unittest.main()
