"""Patch 13 completion: view compatibility and honest authoring accounting."""
from __future__ import annotations

from copy import deepcopy
import unittest

from main_computer.mcel_knowledge import SemanticCatalogError
from main_computer.mcel_knowledge.presentation_policy import (
    apply_presentation_policy, load_presentation_policy,
)
from main_computer.mcel_knowledge.authoring_audit import authoring_audit
from main_computer.mcel_knowledge.live_adoption import build_live_shadow
from main_computer.mcel_knowledge.continuity import with_process_generation_evidence
from tests.test_mcel_patch13_live_shadow import BASE, SCOPE

POLICY = load_presentation_policy()


def project(rows, policy=POLICY):
    result = build_live_shadow({'processes':rows}, profile='task-manager',
                               request_scope=SCOPE, presentation_policy=policy)
    assert result['ok'], result['observations']['task-manager.processes']
    return result['observations']['task-manager.processes']['collection']


class Patch13CompletionTests(unittest.TestCase):
    def test_same_canonical_records_as_unstyled_path(self):
        snapshot=[BASE,dict(BASE,pid=814,name='other')]
        baseline=build_live_shadow({'processes':snapshot},profile='task-manager',request_scope=SCOPE)
        new=project(snapshot)
        old=baseline['observations']['task-manager.processes']['collection']
        self.assertEqual([r['mcelCanonicalRecord'] for r in new['rows']],
                         [r['mcelCanonicalRecord'] for r in old['rows']])
        self.assertEqual([r['mcelIdentity'] for r in new['rows']],
                         [r['mcelIdentity'] for r in old['rows']])
        self.assertTrue(new['presentationCompatibility']['identityUnchanged'])
        self.assertTrue(new['presentationCompatibility']['canonicalRawUnchanged'])

    def test_seven_domain_fields_use_existing_v2_contract(self):
        new=project([BASE]);spec=new['spec']
        self.assertEqual(spec['schema'],'mcel.semantic-collection-spec.v2')
        self.assertEqual(spec['identity'],'mcelIdentity')
        self.assertEqual([f['key'] for f in spec['fields'] if f['key']!='mcelIdentity'],POLICY['fields'])
        self.assertEqual([f['label'] for f in spec['fields'] if f['key']!='mcelIdentity'],
                         ['PID','Process','Role','Memory','Status','Command','Started'])
        self.assertEqual([f['tier'] for f in spec['fields'] if f['key']!='mcelIdentity'],
                         ['primary','primary','secondary','secondary','secondary','secondary','detail'])
        self.assertEqual([f['kind'] for f in spec['fields'] if f['key']!='mcelIdentity'],
                         ['identifier','text','enum','quantity','enum','long-text','timestamp'])
        self.assertEqual(next(f for f in spec['fields'] if f['key']=='command')['presentation'],
                         {'maxChars':46,'compactDisclosure':True})

    def test_roles_are_presentational_labels_not_rederived_semantics(self):
        rows=[dict(BASE,pid=812,is_current_process=True,is_main_computer=True),
              dict(BASE,pid=813,is_current_process=False,is_main_computer=True),
              dict(BASE,pid=814,is_current_process=False,is_main_computer=False)]
        projection=project(rows)
        self.assertEqual([r['mcelDisplay_role'] for r in projection['rows']],
                         ['Current','Main Computer','Other'])
        self.assertEqual([r['role'] for r in projection['rows']],
                         ['current-process','main-computer','other-process'])
        for row in projection['rows']:
            self.assertEqual(row['role'],row['mcelCanonicalRecord']['properties']['role']['raw'])
            self.assertEqual(row['mcelCanonicalRecord']['properties']['role']['fromContext'],'derived')

    def test_preserve_missing_and_zero_and_unknown_role(self):
        rows=[BASE, dict(BASE,pid=813,memory_rss=None,memory_availability='unavailable')]
        proj=project(rows)
        self.assertEqual(proj['rows'][0]['resident_memory'],0)
        self.assertEqual(proj['rows'][0]['resident_memoryAvailability'],'present')
        self.assertNotIn('resident_memory',proj['rows'][1])
        self.assertEqual(proj['rows'][1]['resident_memoryAvailability'],'unavailable')
        # A future category remains visible as its exact value rather than
        # being silently mislabelled as an existing role.
        plain=build_live_shadow({'processes':[BASE]},profile='task-manager',request_scope=SCOPE)
        item=plain['observations']['task-manager.processes']['collection']
        item['rows'][0]['role']='future-categorization'
        new=apply_presentation_policy(item,POLICY)
        self.assertEqual(new['rows'][0]['mcelDisplay_role'],'future-categorization')

    def test_source_and_existing_behavior_unchanged(self):
        original={'processes':[dict(BASE,cmdline='python ' + 'very-long ' * 20)]}
        snap=deepcopy(original)
        projected=project(original['processes'])
        self.assertEqual(original,snap)
        self.assertEqual(projected['rows'][0]['command'],original['processes'][0]['cmdline'])
        self.assertEqual(projected['rows'][0]['mcelCanonicalRecord']['properties']['command']['raw'],
                         original['processes'][0]['cmdline'])
        self.assertTrue(projected['noOperations'])
        self.assertFalse(projected.get('productionPromotion',False))

    def test_continuity_token_remains_scoped_to_real_producer(self):
        base=build_live_shadow({'processes':[BASE]},profile='task-manager',request_scope=SCOPE,
                               presentation_policy=POLICY)
        self.assertNotIn('reconciliation',base['observations']['task-manager.processes']['collection']['spec'])
        proof=with_process_generation_evidence(base,server_instance='a'*32,source_kind='psutil-create-time')
        col=proof['observations']['task-manager.processes']['collection']
        self.assertEqual(col['spec']['reconciliation'],{'generationFrom':'mcelGeneration'})
        self.assertTrue(col['rows'][0]['mcelGeneration'].startswith('pgen1:'))
        self.assertFalse(proof['continuityCertified'])

    def test_bad_policies_fail_closed_without_changing_original(self):
        baseline=build_live_shadow({'processes':[BASE]},profile='task-manager',request_scope=SCOPE)
        raw=baseline['observations']['task-manager.processes']['collection'];before=deepcopy(raw)
        for bad in [dict(POLICY,fields=['pid','pid']),
                    dict(POLICY,fields=['pid','mcelIdentity']),
                    dict(POLICY,fields=['pid','rogue']),
                    dict(POLICY,primary=[]),
                    dict(POLICY,valueLabels={'pid':{'a':'A'}}),
                    dict(POLICY,presentation={'pid':{'maxChars':46,'compactDisclosure':True}})]:
            with self.subTest(bad=bad):
                with self.assertRaises(SemanticCatalogError):
                    apply_presentation_policy(raw,bad)
        self.assertEqual(raw,before)

    def test_one_bad_record_still_quarantined(self):
        snap=[BASE,dict(BASE,pid=813,created_at='unparseable')]
        col=project(snap)
        self.assertEqual(len(col['rows']),1)
        self.assertEqual(len(col['quarantinedRows']),1)

    def test_duplicate_refusal_still_fail_closed(self):
        out=build_live_shadow({'processes':[BASE,deepcopy(BASE)]},profile='task-manager',
                              request_scope=SCOPE,presentation_policy=POLICY)
        self.assertFalse(out['ok'])
        self.assertEqual(out['observations']['task-manager.processes']['status'],'refused')

    def test_authoring_audit_is_reproducible_and_strict(self):
        audit=authoring_audit(POLICY)
        self.assertEqual(audit['legacy']['fieldDeclarations'],7)
        self.assertEqual(audit['legacy']['sourceFieldNames'],10)
        self.assertEqual(audit['overlayDeclarationInstances'],22)
        self.assertGreater(audit['legacyDeclarationInstances'],audit['overlayDeclarationInstances'])
        self.assertFalse(audit['fullyUnhinted'])
        self.assertFalse(audit['pixelEquivalent'])
        self.assertIn('NOT total system LOC',audit['scopeCaveat'])


if __name__=='__main__':unittest.main()
