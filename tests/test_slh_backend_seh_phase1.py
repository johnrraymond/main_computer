"""Backend control-plane gates; no claim of a deployed relay or worker network."""
import json
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace

from main_computer.slh import SLHControlBackend, SEHTarget
from main_computer.slh.config import SLHConfigError
from main_computer.slh.coolify import SEHDeploymentError, compose_yaml, plan, SEHDeployer
from tools.seh_harness import _FakeCoolify, _local_verify

TARGET = Path(__file__).resolve().parents[1] / 'deploy' / 'seh' / 'target.example.json'


class TestSEHBackend(unittest.TestCase):
    def setUp(self):
        import base64
        from main_computer.slh.authenticator import generate_identity
        _, public = generate_identity()
        data = json.loads(TARGET.read_text())
        data['slh_public_key_b64'] = base64.b64encode(public.encode('ascii')).decode('ascii')
        self.target = SEHTarget.from_dict(data)
        self.backend = SLHControlBackend()
        self.fake = _FakeCoolify()

    def test_registry_preserves_all_four_public_hubs(self):
        catalog = self.backend.connection_catalog(seh_url=self.target.public_url)
        profiles = {x['key']: x for x in catalog['profiles']}
        self.assertEqual(set(profiles), {'slh','seh','mainnet','testnet','test','dev'})
        self.assertTrue(profiles['mainnet']['client_wallet_required'])
        self.assertFalse(profiles['seh']['client_wallet_required'])
        self.assertTrue(profiles['slh']['slh_managed_marketplace_wallet'])
        self.assertEqual(profiles['slh']['status'], 'unimplemented')

    def test_no_secrets_or_unpinned_images_in_target(self):
        d = self.target.to_dict()
        d['coolify_token'] = 'leak'
        with self.assertRaises(SLHConfigError): SEHTarget.from_dict(d)
        d.pop('coolify_token')
        d['image_digest'] = 'latest'
        with self.assertRaises(SLHConfigError): SEHTarget.from_dict(d)
        d['image_digest'] = self.target.image_digest
        d['public_url'] = 'http://hub.secure.company.com'
        with self.assertRaises(SLHConfigError): SEHTarget.from_dict(d)
        d['public_url'] = 'https://private:secret@hub.secure.company.com'
        with self.assertRaises(SLHConfigError): SEHTarget.from_dict(d)

    def test_pinned_compose_has_no_port_mapping_or_secrets(self):
        y = compose_yaml(self.target)
        self.assertIn('@sha256:', y)
        self.assertIn('cap_drop:', y)
        self.assertNotIn('ports:', y)
        self.assertNotIn('PRIVATE_KEY', y)

    def test_dry_run_is_read_only_and_no_worker_claim(self):
        p = self.backend.seh_plan(self.target)
        self.assertEqual(p['status'], 'PLANNED_ONLY')
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.backend.managed_work_api()['implementation'], 'NOT_IMPLEMENTED')

    def test_preflight_and_guarded_mock_create_and_idempotency(self):
        self.assertEqual(self.backend.seh_preflight(self.target,self.fake)['status'], 'CREATE_ELIGIBLE')
        p = plan(self.target)
        with self.assertRaises(SEHDeploymentError):
            self.backend.seh_create(self.target,self.fake,digest='bad',target_id=self.target.target_id)
        self.assertEqual(len([c for c in self.fake.calls if c[0]=='POST']),0)
        receipt=self.backend.seh_create(self.target,self.fake,digest=p['plan_digest'],target_id=self.target.target_id)
        self.assertEqual(receipt['status'], 'CREATED_NOT_STARTED')
        self.assertFalse(receipt['ready'])
        with self.assertRaises(SEHDeploymentError):
            self.backend.seh_create(self.target,self.fake,digest=p['plan_digest'],target_id=self.target.target_id)
        self.assertEqual(len([c for c in self.fake.calls if c[0]=='POST']),1)

    def test_adoption_requires_exact_marker(self):
        t = replace(self.target, service_uuid='existing')
        self.fake.services.append({'uuid':'existing','name':'public-mainnet-hub','description':'other'})
        with self.assertRaises(SEHDeploymentError):
            self.backend.seh_preflight(t,self.fake)
        self.assertFalse(any(m=='POST' for m,_ in self.fake.calls))

    def test_mock_start_never_means_ready(self):
        t=replace(self.target,service_uuid='known')
        self.fake.services.append({'uuid':'known','name':f'mc-seh-{t.target_id}',
                                   'description':f'main-computer-seh:v1 target={t.target_id} org={t.organization_id} slh={t.slh_id}'})
        p=plan(t)
        receipt=self.backend.seh_start(t,self.fake,digest=p['plan_digest'],service_uuid='known')
        self.assertEqual(receipt['status'],'START_QUEUED')
        self.assertFalse(receipt['ready'])

    def test_local_harness_reports_unimplemented_as_unimplemented(self):
        report=_local_verify(self.target)
        self.assertEqual(report['status'],'PASS')
        self.assertEqual(report['worker_delegation'],'NOT_IMPLEMENTED')
        self.assertEqual(report['coolify_live'],'NOT_TESTED')

if __name__=='__main__':unittest.main()
