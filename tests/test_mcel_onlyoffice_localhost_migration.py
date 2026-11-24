"""Normal default-browser startup, exact source migration and native localhost policy."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from main_computer import onlyoffice_deployment as d
from main_computer import auth_bootstrap_service_deployment as adapter
from main_computer.auth_hostnames import HOSTS, validate_origin
from tools import mcel_onlyoffice_deployment as tool


class BrowserContract(unittest.TestCase):
    def test_default_browser_not_separate_profile(self):
        root=tool.ROOT
        policy={'primary':'dev','roots':{'dev':str(root)}}
        with patch.object(d,'verify_source'), patch.object(adapter.bootstrap,'recover_deleted_proof_locally'), patch.object(adapter.bootstrap,'wait_for_probe',return_value=(True,'OK')), patch.object(os,'startfile',create=True) as open_url, patch.object(adapter.subprocess,'Popen') as launch:
            adapter.open_browser(root,root,policy)
            open_url.assert_called_once_with('https://dev.main-computer.localhost:8875/auth/login?next=%2Fgraphical')
            launch.assert_not_called()

    def test_secondary_browser_uses_same_default_profile_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'debug'; primary=Path(tmp)/'dev'
            policy={'primary':'dev','roots':{'dev':str(primary),'debug':str(root)}}
            with patch.object(d,'verify_source'), patch.object(adapter.bootstrap,'recover_deleted_proof_locally'), patch.object(adapter.bootstrap,'wait_for_probe',return_value=(True,'OK')), patch.object(os,'startfile',create=True) as open_url:
                adapter.open_browser(root,root,policy)
                open_url.assert_called_once_with('https://dev.main-computer.localhost:8875/auth/deployment/handoff/debug')

    def test_exact_https_names_and_separate_docs_origin(self):
        self.assertEqual(set(HOSTS.values()),{f'{name}.main-computer.localhost' for name in ('dev','unleashed','debug','safe','onlyoffice')})
        self.assertTrue(validate_origin('https://dev.main-computer.localhost:8875'))
        self.assertFalse(validate_origin('https://unknown.main-computer.localhost:8875'))
        self.assertFalse(validate_origin('https://dev.main-computer.local:8875'))
        self.assertFalse(validate_origin('http://dev.main-computer.localhost:8875'))
        self.assertEqual(d.DOCS_ORIGIN,'https://onlyoffice.main-computer.localhost:18086')


class Migration(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.state=Path(temp.name); self.root=tool.ROOT
        self.manifest=json.loads((self.root/'tools/onlyoffice-deployment-localhost.json').read_text())
        self.current=d.file_hashes(self.root,tool.ALL_FILES)
        self.assertEqual({k:self.current[k] for k in self.manifest['corrected']},self.manifest['corrected'])
        self.policy={'schema':1,'mode':'hardened','primary':'dev','roots':{'dev':str(self.root)},'fingerprint':{**self.current,**self.manifest['previous']},'image_tag':'latest','image_id':'sha256:old'}
        self.opts=SimpleNamespace(authorize_cutover=True)
        self.write()

    def write(self): (self.state/d.POLICY_NAME).write_text(json.dumps(self.policy))

    def migrate(self, evidence=None, run=None):
        with patch.object(tool,'no_supervisors'),patch.object(tool,'source_evidence',side_effect=evidence) as checked,patch.object(tool,'image_id',return_value='sha256:old'),patch.object(tool.subprocess,'run',side_effect=run,return_value=SimpleNamespace(returncode=0)) as process:
            tool.migrate_localhost(self.state,self.root,self.opts)
            return checked,process

    def test_migration_preserves_authority_and_requires_new_evidence(self):
        sentinel=self.state/'sessions.json';sentinel.write_bytes(b'unchanged')
        checked,process=self.migrate(evidence=[RuntimeError('STALE'),None,RuntimeError('STALE'),None,None])
        self.assertEqual(checked.call_count,5)
        self.assertEqual([call.args[0][2:4] for call in process.call_args_list],
                         [['main_computer.auth_bootstrap_service','--root'],
                          ['tools.mcel_onlyoffice_cutover','gateway'],['tools.mcel_onlyoffice_cutover','live']])
        self.assertEqual(d.read_policy(self.state),{**self.policy,'fingerprint':self.current})
        self.assertEqual(sentinel.read_bytes(),b'unchanged')

    def test_recertification_failure_does_not_update_policy(self):
        with self.assertRaisesRegex(RuntimeError,'LOCALHOST_GATEWAY_CERTIFICATION_FAILED'):
            self.migrate(evidence=RuntimeError('STALE'),run=[SimpleNamespace(returncode=0),SimpleNamespace(returncode=1)])
        self.assertEqual(d.read_policy(self.state),self.policy)

    def test_certificate_failure_stops_before_evidence_or_docker(self):
        with patch.object(tool,'no_supervisors'),patch.object(tool,'source_evidence') as evidence,patch.object(tool,'image_id') as image,patch.object(tool.subprocess,'run',return_value=SimpleNamespace(returncode=1)) as process:
            with self.assertRaisesRegex(RuntimeError,'LOCALHOST_CERTIFICATE_PREFLIGHT_FAILED'):
                tool.migrate_localhost(self.state,self.root,self.opts)
            evidence.assert_not_called(); image.assert_not_called()
            process.assert_called_once()
            self.assertEqual(process.call_args.args[0][2],'main_computer.auth_bootstrap_service')
            self.assertEqual(process.call_args.kwargs['env']['MAIN_COMPUTER_HARDENED_CANDIDATE'],'1')
        self.assertEqual(d.read_policy(self.state),self.policy)

    def test_valid_evidence_reused_only_after_certificate(self):
        checked,process=self.migrate()
        self.assertEqual(checked.call_count,3)
        process.assert_called_once()
        self.assertEqual(process.call_args.args[0][2],'main_computer.auth_bootstrap_service')
        self.assertEqual(d.read_policy(self.state)['fingerprint'],self.current)

    def test_partial_evidence_reuses_gateway_and_runs_only_live(self):
        checked,process=self.migrate(evidence=[None,RuntimeError('STALE'),None,None])
        self.assertEqual(checked.call_count,4)
        self.assertEqual([c.args[0][2:4] for c in process.call_args_list],
                         [['main_computer.auth_bootstrap_service','--root'],['tools.mcel_onlyoffice_cutover','live']])

    def test_intermediate_selected_localhost_policy_can_migrate(self):
        self.policy['fingerprint']={**self.current,**self.manifest['intermediate']}; self.write()
        self.migrate()
        self.assertEqual(d.read_policy(self.state)['fingerprint'],self.current)

    def test_evidence_reuse_requires_latest_success_and_exact_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); reports=root/'runtime/reports/mcel-onlyoffice-cutover'
            def report(name,action,verdict,fingerprint):
                path=reports/name;path.mkdir(parents=True)
                (path/'summary.json').write_text(json.dumps({'action':action,'verdict':verdict,'fingerprint':fingerprint}))
            expected={'source':'current'}
            report('01','gateway','PASS_ISOLATED_GATEWAY',expected)
            report('02','live','PASS_ISOLATED_LIVE',{'source':'retired'})
            with patch.object(d,'file_hashes',return_value=expected):
                tool.source_evidence(root,actions=('gateway',))
                with self.assertRaisesRegex(RuntimeError,'SOURCE_MATCHED_LIVE'):tool.source_evidence(root)
                report('03','live','PASS_ISOLATED_LIVE',expected)
                tool.source_evidence(root)
                report('04','gateway','BLOCKED',expected)
                with self.assertRaisesRegex(RuntimeError,'SOURCE_MATCHED_GATEWAY'):tool.source_evidence(root)

    def test_unrelated_mutation_is_not_rebound(self):
        k=next(k for k in self.current if k not in self.manifest['corrected'])
        self.policy['fingerprint'][k]='0'*64;self.write()
        with self.assertRaisesRegex(RuntimeError,'LOCALHOST_SOURCE_MISMATCH'): self.migrate()
        self.assertEqual(d.read_policy(self.state),self.policy)

    def test_explicit_authorization(self):
        self.opts.authorize_cutover=False
        with self.assertRaisesRegex(RuntimeError,'EXPLICIT_CUTOVER'): self.migrate()

if __name__=='__main__': unittest.main()
