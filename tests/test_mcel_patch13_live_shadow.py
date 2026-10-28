"""Patch 13 stage 3: real-source semantic shadow and hard non-promotion gates."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from main_computer.mcel_knowledge.live_adoption import LIVE_SCHEMA, build_live_shadow
from main_computer.mcel_knowledge import SemanticCatalogError
from main_computer.task_manager import TaskManagerService

ROOT = Path(__file__).resolve().parents[1]
BASE = {'pid':812,'name':'python','status':'running','cmdline':'python worker.py',
        'created_at':'2026-10-27T17:28:30-07:00','memory_rss':0,
        'memory_availability':'present','status_availability':'present',
        'is_current_process':False,'is_main_computer':False}
SCOPE = {'query':'', 'limit':24, 'include_all':False}


def capture(rows):
    return build_live_shadow({'processes':rows},profile='task-manager',request_scope=SCOPE)


class LiveStage3Tests(unittest.TestCase):
    def test_only_existing_ir_and_never_executable(self):
        result=capture([BASE])
        self.assertEqual(result['schema'],LIVE_SCHEMA)
        self.assertEqual(result['irSchema'],'mcel.application-ir.v1')
        self.assertFalse(result['continuityCertified'])
        self.assertIsNone(result['revision'])
        self.assertIn('no operational effects',result['authority'])
        collection=result['observations']['task-manager.processes']['collection']
        self.assertTrue(collection['noOperations'])
        self.assertNotIn('reconciliation',collection['spec'])

    def test_original_source_is_unmodified_and_parity_accounts(self):
        snap={'processes':[BASE,dict(BASE,pid=813,memory_rss=None,memory_availability='unavailable')]}
        prior=deepcopy(snap)
        result=build_live_shadow(snap,profile='task-manager',request_scope=SCOPE)
        self.assertEqual(snap,prior)
        check=result['observations']['task-manager.processes']['parity']
        self.assertEqual((check['sourceRecords'],check['accepted'],check['quarantined']),(2,2,0))
        self.assertTrue(check['canonicalRecordsUnchanged'])
        rows=result['observations']['task-manager.processes']['collection']['rows']
        self.assertEqual(rows[0]['resident_memory'],0)
        self.assertEqual(rows[0]['resident_memoryAvailability'],'present')
        self.assertEqual(rows[1]['resident_memoryAvailability'],'unavailable')
        self.assertNotIn('resident_memory',rows[1])

    def test_declared_psutil_and_cim_process_lifetimes_match(self):
        p=dict(BASE,pid=44)
        c=dict(p,created_at='20261027172830.000000-420')
        a=capture([p])['observations']['task-manager.processes']['collection']['rows'][0]
        b=capture([c])['observations']['task-manager.processes']['collection']['rows'][0]
        self.assertEqual(a['mcelIdentity'],b['mcelIdentity'])
        self.assertNotEqual(a['created_at'],b['created_at'])
        self.assertEqual(a['mcelCanonicalRecord']['properties']['created_at']['normalized'],
                         b['mcelCanonicalRecord']['properties']['created_at']['normalized'])

    def test_different_generation_is_not_reconciled(self):
        old=capture([BASE])['observations']['task-manager.processes']['collection']
        new=capture([dict(BASE,created_at='2026-10-27T17:29:00-07:00')])['observations']['task-manager.processes']['collection']
        self.assertNotEqual(old['rows'][0]['mcelIdentity'],new['rows'][0]['mcelIdentity'])
        self.assertNotIn('reconciliation',old['spec'])
        self.assertNotIn('reconciliation',new['spec'])

    def test_one_invalid_timestamp_quarantined_other_record_retained(self):
        bad=dict(BASE,pid=99,created_at='broken')
        result=capture([bad,BASE])
        projected=result['observations']['task-manager.processes']['collection']
        self.assertTrue(result['ok'])
        self.assertEqual(len(projected['rows']),1)
        self.assertEqual(len(projected['quarantinedRows']),1)
        self.assertEqual(projected['quarantinedRows'][0]['reason'],'identity-incomplete')
        self.assertIn('invalid-timestamp-format', [d['code'] for d in result['diagnostics']])

    def test_all_invalid_refused_but_no_empty_success(self):
        result=capture([dict(BASE,created_at='?')])
        self.assertFalse(result['ok'])
        self.assertEqual(result['observations']['task-manager.processes']['status'],'refused')

    def test_duplicate_identity_still_collection_refusal(self):
        result=capture([BASE,deepcopy(BASE)])
        self.assertFalse(result['ok'])
        self.assertEqual(result['observations']['task-manager.processes']['status'],'refused')
        self.assertIn('ambiguous',result['observations']['task-manager.processes']['reason'])

    def test_envelope_scope_no_false_full_enumeration_or_revision(self):
        result=capture([BASE])
        env=result['observations']['task-manager.processes']['collection']['observationEnvelope']
        self.assertEqual(env['scope']['kind'],'bounded-top-n')
        self.assertEqual(env['scope']['parameters']['limit']['raw'],24)
        self.assertEqual(env['completeness'],'bounded')
        self.assertIsNone(env['revision'])
        self.assertIsNone(env['observedAt'])

    def test_fresh_real_task_manager_producer_not_fixture(self):
        snapshot=TaskManagerService(ROOT).snapshot(limit=5,include_connections=False)
        out=build_live_shadow(snapshot,profile='task-manager',request_scope={'query':'','limit':5,'include_all':False})
        self.assertTrue(out['ok'],out.get('diagnostics'))
        parity=out['observations']['task-manager.processes']['parity']
        self.assertTrue(parity['ok'])
        self.assertEqual(parity['sourceRecords'],len(snapshot['processes']))
        self.assertFalse(out['producerSnapshotSampledAgain'])

    def test_real_file_explorer_response_shape_same_shared_resolver(self):
        snapshot=json.loads((ROOT/'tools/fixtures/mcel_computer_knowledge/expanded_computer_snapshot.v1.json').read_text())
        out=build_live_shadow(snapshot,profile='file-explorer')
        self.assertTrue(out['ok'])
        entry=out['observations']['file-explorer.entries']
        self.assertEqual(entry['status'],'shadow-only')
        self.assertEqual(entry['collection']['observationEnvelope']['scope']['kind'],'filtered')
        self.assertTrue(entry['parity']['ok'])
        self.assertTrue(all(r['mcelCanonicalRecord']['concept']=='mc.file.entry' for r in entry['collection']['rows']))

    def test_worker_lease_vs_logical_worker_no_synthetic_identity(self):
        snapshot=json.loads((ROOT/'tools/fixtures/mcel_computer_knowledge/expanded_computer_snapshot.v1.json').read_text())
        out=build_live_shadow(snapshot,profile='worker')
        self.assertTrue(out['ok'])
        self.assertEqual(out['observations']['worker.logical-workers']['status'],'shadow-only')
        self.assertEqual(out['observations']['worker.market-workers']['status'],'shadow-only')
        self.assertEqual(out['observations']['worker.runtime']['status'],'canonical-only')
        self.assertFalse(out['continuityCertified'])

    def test_missing_source_not_fabricated_empty(self):
        out=build_live_shadow({},profile='file-explorer')
        self.assertEqual(out['observations']['file-explorer.entries']['status'],'not-observed')
        self.assertNotIn('collection',out['observations']['file-explorer.entries'])

    def test_nonoptin_and_invalid_profile_fail_closed(self):
        with self.assertRaises(SemanticCatalogError):build_live_shadow({},profile='unknown')
        with self.assertRaises(SemanticCatalogError):build_live_shadow({},profile='worker',request_scope={'limit':3})

    def test_browser_shadow_is_read_only_and_does_not_claim_continuity(self):
        # A static code assertion here is supplemented by the executable
        # Chromium consumer Frob. It is not a substitute for a hosted browser.
        js=(ROOT/'main_computer/web/applications/scripts/mcel-task-manager-semantic-shadow.js').read_text()
        self.assertIn('mcel_semantic_shadow',js)
        self.assertIn("S.mount(",js)
        self.assertIn('gated-unverified',js)
        self.assertNotIn('fetch(',js)
        self.assertNotIn('setInterval(',js)
        self.assertNotIn('perform_action(',js)


if __name__=='__main__':unittest.main()
