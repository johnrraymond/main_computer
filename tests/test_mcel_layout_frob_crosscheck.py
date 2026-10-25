"""Crosscheck contract integrity and deliberately incomplete audit truth."""
from __future__ import annotations
import copy
import importlib.util
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'tools' / 'mcel_layout_frob_crosscheck.py'
spec = importlib.util.spec_from_file_location('mcel_layout_frob_crosscheck', MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
CONTRACT_PATH = ROOT / 'mcel_apps' / 'task-manager' / 'frob_crosscheck.yaml'
DESIGN_PATH = ROOT / 'mcel_apps' / 'task-manager' / 'design.yaml'


class FrobCrosscheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = module.load_yaml(CONTRACT_PATH)
        cls.design = module.load_yaml(DESIGN_PATH)

    def test_contract_expands_all_declared_members(self):
        instances = module.validate(self.contract, self.design)
        self.assertGreaterEqual(len(instances), 250)
        all_ids = [i['id'] for i in instances]
        self.assertEqual(len(all_ids), len(set(all_ids)))
        for entity in self.design['semantics']['entities']:
            self.assertIn('S02-entity-shape::'+entity, all_ids)
        for row in self.design['rows']['types']:
            self.assertIn('S03-row-shape::'+row, all_ids)
        for workflow in self.design['workflows']:
            self.assertIn('S05-workflow-shape::'+workflow['id'], all_ids)

    def test_only_static_cases_pass_and_unbuilt_frob_is_incomplete(self):
        report = module.audit(self.design,self.contract,mode='audit')
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertGreater(report['counts'].get('PASS',0),0)
        self.assertGreater(report['counts'].get('NOT_IMPLEMENTED',0),0)
        self.assertFalse(any(x['status']=='PASS' for x in report['results'] if x['probe'] not in module.STATIC_PROBES))

    def test_missing_semantic_identity_fails_not_skips(self):
        design = copy.deepcopy(self.design)
        del design['semantics']['entities']['process']['identity']
        report = module.audit(design,self.contract,mode='audit')
        sample = next(x for x in report['results'] if x['id']=='S02-entity-shape::process')
        self.assertEqual(sample['status'],'FAIL')
        self.assertIn('identity',sample['evidence']['missing_fields'])

    def test_unknown_row_entity_fails(self):
        design=copy.deepcopy(self.design)
        design['rows']['types']['port-row']['entity']='nonexistent_entity'
        report=module.audit(design,self.contract,mode='audit')
        sample=next(x for x in report['results'] if x['id']=='S04-row-reference::port-row')
        self.assertEqual(sample['status'],'FAIL')

    def test_invalid_relationship_endpoint_fails(self):
        design=copy.deepcopy(self.design)
        design['semantics']['relationships'][0][2]='ghost_resource'
        report=module.audit(design,self.contract,mode='audit')
        self.assertEqual(next(x for x in report['results'] if x['id'].startswith('S11-relationship-refs::process--owns'))['status'],'FAIL')

    def test_adding_entity_expands_obligations(self):
        design=copy.deepcopy(self.design)
        design['semantics']['entities']['new_resource']={'status':'proposed','identity':['key']}
        original=module.validate(self.contract,self.design)
        expanded=module.validate(self.contract,design)
        self.assertGreater(len(expanded),len(original))
        self.assertTrue(any(x['id']=='S02-entity-shape::new_resource' for x in expanded))

    def test_undeclared_runner_refuses(self):
        contract=copy.deepcopy(self.contract)
        contract['probes']['browser.interaction']['implemented']=True
        with self.assertRaisesRegex(ValueError,'unrecognized implemented runner'):
            module.validate(contract,self.design)

    def test_missing_per_member_coverage_refuses(self):
        contract=copy.deepcopy(self.contract)
        contract['tests']=[t for t in contract['tests'] if t['target']!='data_sources']
        with self.assertRaisesRegex(ValueError,'coverage hole'):
            module.validate(contract,self.design)

    def test_bad_test_target_refuses(self):
        contract=copy.deepcopy(self.contract)
        contract['tests'][0]['target']='semantics.aliens'
        with self.assertRaisesRegex(ValueError,'reference not found'):
            module.validate(contract,self.design)

    def test_historical_mobile_three_columns_is_not_pass(self):
        prior={'schema':'task-manager.semantic-layout-probe.v1',
               'canonicalBrowser':{'390':{'status':'pass','columns':3,'viewportOverflow':False}},
               'inferredWorkspace':{'390':{'attentionFirst':False},'1440':{'attentionFirst':False}}}
        diag=module.compare_prior_probe(prior)
        self.assertEqual(diag['mobile_390_one_column']['status'],'FAIL')
        self.assertEqual(diag['automatic_priority_first']['status'],'FAIL')

    def test_same_run_console_and_report_disagreement_fails(self):
        prior={'canonicalBrowser':{'390':{'columns':1}}}
        console=module.parse_prior_console("canonical_browser: {'390': {'columns': 3}}\n")
        self.assertEqual(module.compare_console_report(console,prior)['status'],'FAIL')
        self.assertIn('390.columns',module.compare_console_report(console,prior)['mismatches'])

    def test_same_run_console_and_report_agreement_passes(self):
        prior={'canonicalBrowser':{'390':{'columns':1}}}
        console=module.parse_prior_console("canonical_browser: {'390': {'columns': 1}}\n")
        self.assertEqual(module.compare_console_report(console,prior)['status'],'PASS')

    def test_consequential_effects_must_not_be_live(self):
        self.assertEqual(self.contract['authority']['safety'].startswith('read-only/offline'),True)
        self.assertTrue(self.contract['authority']['no_website_builder_changes'])
        for test in self.contract['tests']:
            if test['group']=='effects':
                self.assertIn(test['probe'],('runtime.effect_boundary',))
                self.assertIn(self.contract['probes'][test['probe']]['runner'],
                              ('tools/mcel_layout_frob_runtime.py',))


if __name__=='__main__':
    unittest.main()
