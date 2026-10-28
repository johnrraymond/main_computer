"""Stage 4: proof must be scoped to the actual process generation and issuer."""
from __future__ import annotations

from copy import deepcopy
import unittest

from main_computer.mcel_knowledge.live_adoption import build_live_shadow
from main_computer.mcel_knowledge.continuity import with_process_generation_evidence
from tests.test_mcel_patch13_live_shadow import BASE, SCOPE

ISSUER = 'a'*32


def make(rows, *, issuer=ISSUER, source='win32-cim-creation-date'):
    snapshot={'processes':rows}
    raw=deepcopy(snapshot)
    shadow=build_live_shadow(snapshot,profile='task-manager',request_scope=SCOPE)
    result=with_process_generation_evidence(shadow,server_instance=issuer,source_kind=source)
    assert snapshot==raw
    return result


def collection(shadow):
    return shadow['observations']['task-manager.processes']['collection']


class Patch13ContinuityTests(unittest.TestCase):
    def test_producer_evidence_optin_never_promotes_runtime(self):
        s=make([BASE]);c=collection(s)
        self.assertEqual(s['processGenerationEvidence']['status'],'eligible-read-only')
        self.assertEqual(c['spec']['reconciliation'],{'generationFrom':'mcelGeneration'})
        self.assertIn('mcelGeneration',c['spec']['sourceFields'])
        self.assertTrue(c['rows'][0]['mcelGeneration'].startswith('pgen1:'))
        self.assertFalse(s['continuityCertified'])
        self.assertNotIn('operations',c)
        self.assertTrue(c['noOperations'])
        self.assertEqual(s['authority'],'read-only; not promoted; no operational effects')

    def test_same_pid_cim_vs_iso_representation(self):
        iso=make([BASE])
        cim=make([dict(BASE,created_at='20261027172830.000000-420')])
        self.assertEqual(collection(iso)['rows'][0]['mcelGeneration'],collection(cim)['rows'][0]['mcelGeneration'])
        self.assertNotEqual(collection(iso)['rows'][0]['created_at'],collection(cim)['rows'][0]['created_at'])

    def test_same_generation_changed_measurement_preserves_token(self):
        a=make([BASE]); b=make([dict(BASE,memory_rss=50,name='python2')])
        self.assertEqual(collection(a)['rows'][0]['mcelGeneration'],collection(b)['rows'][0]['mcelGeneration'])

    def test_pid_reuse_different_generation_rotates(self):
        a=make([BASE]); b=make([dict(BASE,created_at='2026-10-27T17:29:00-07:00')])
        self.assertNotEqual(collection(a)['rows'][0]['mcelGeneration'],collection(b)['rows'][0]['mcelGeneration'])

    def test_server_restart_rotates_even_same_process_generation(self):
        a=make([BASE]); b=make([BASE],issuer='b'*32)
        self.assertNotEqual(collection(a)['rows'][0]['mcelGeneration'],collection(b)['rows'][0]['mcelGeneration'])
        self.assertNotEqual(a['processGenerationEvidence']['producerInstanceScope'],
                            b['processGenerationEvidence']['producerInstanceScope'])

    def test_missing_and_invalid_lifetimes_are_quarantined_not_trusted(self):
        s=make([BASE,dict(BASE,pid=99,created_at=''),dict(BASE,pid=100,created_at='invalid')])
        c=collection(s)
        self.assertEqual(len(c['rows']),1)
        self.assertEqual(len(c['quarantinedRows']),2)
        self.assertEqual(s['processGenerationEvidence']['qualifiedRows'],1)

    def test_invalid_pid_not_attested(self):
        s=make([dict(BASE,pid=0),BASE]);c=collection(s)
        self.assertEqual(len(c['rows']),1)
        self.assertEqual(len(c['quarantinedRows']),1)
        self.assertEqual(s['processGenerationEvidence']['qualifiedRows'],1)

    def test_source_scope_invalid_fails_closed(self):
        a=make([BASE],source='unspecified')
        self.assertEqual(a['processGenerationEvidence']['status'],'withheld')
        self.assertNotIn('reconciliation',collection(a)['spec'])
        b=make([BASE],issuer='unknown')
        self.assertNotIn('reconciliation',collection(b)['spec'])

    def test_multiple_rows_stable_reorder_and_partial(self):
        p2=dict(BASE,pid=814,created_at='2026-10-27T17:29:00-07:00')
        a=make([BASE,p2]);b=make([p2,dict(BASE,memory_rss=20)])
        self.assertEqual({r['mcelGeneration'] for r in collection(a)['rows']},
                         {r['mcelGeneration'] for r in collection(b)['rows']})
        partial=make([p2]);self.assertEqual(len(collection(partial)['rows']),1)
        self.assertEqual(partial['observations']['task-manager.processes']['collection']['observationEnvelope']['completeness'],'bounded')

    def test_rejected_duplicate_identity_retains_no_reconciliation_ability(self):
        s=make([BASE,deepcopy(BASE)])
        self.assertFalse(s['ok'])
        self.assertEqual(s['processGenerationEvidence']['status'],'withheld')
        self.assertEqual(s['observations']['task-manager.processes']['status'],'refused')

    def test_stage3_without_issuer_stays_nonreconciling(self):
        original=build_live_shadow({'processes':[BASE]},profile='task-manager',request_scope=SCOPE)
        self.assertNotIn('reconciliation',collection(original)['spec'])
        self.assertFalse(original['continuityCertified'])

    def test_source_is_not_modified(self):
        before={'processes':[BASE]};raw=deepcopy(before)
        stage3=build_live_shadow(before,profile='task-manager',request_scope=SCOPE)
        ref=deepcopy(stage3)
        with_process_generation_evidence(stage3,server_instance=ISSUER,source_kind='psutil-create-time')
        self.assertEqual(stage3,ref)
        self.assertEqual(before,raw)


if __name__=='__main__':unittest.main()
