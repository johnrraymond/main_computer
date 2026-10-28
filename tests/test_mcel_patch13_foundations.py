"""Patch 13 stages 1+2: semantic boundary, evidence, and correctness tests."""
from __future__ import annotations
import copy
import json
import unittest

from main_computer.mcel_knowledge import SemanticCatalogError, interpret, load_catalog
from main_computer.mcel_knowledge.foundations import normalize_timestamp
from main_computer.mcel_knowledge.ir_bridge import (compile_knowledge_ir, resolve_knowledge,
                                                     collection_projection)

PROCESS = {'pid':17,'created_at':'2026-10-27T17:28:30-07:00','name':'python',
           'cmdline':'python worker.py','status':'running', 'status_availability':'present',
           'memory_rss':0,'memory_availability':'present',
           'is_current_process':False,'is_main_computer':False}
PROCESS_BIND = {'task-manager.processes':{'properties':{
    'created_at':{'path':'created_at','format':'iso8601'}}}}


class Stage13FoundationTests(unittest.TestCase):
    def setUp(self):
        self.cat = load_catalog()

    def compile(self, observation, bindings=None):
        return compile_knowledge_ir('foundation-test', observation_ids=[observation],
                                    bindings=bindings)

    def test_catalog_still_has_one_shared_ir(self):
        b = self.compile('task-manager.processes')
        self.assertEqual(b['applicationIr']['schema'], 'mcel.application-ir.v1')
        self.assertEqual(len(b['applicationIr']['derivations']),0)
        self.assertFalse(b['applicationIr']['effects'])

    def test_app_role_is_derived_with_evidence_and_not_universal_process(self):
        self.assertNotIn('role', self.cat['concepts']['mc.process'])
        b = self.compile('task-manager.processes')
        view = resolve_knowledge({'processes':[PROCESS]},b)
        role = view['sources']['task-manager.processes']['records'][0]['properties']['role']
        self.assertEqual(role['raw'],'other-process')
        self.assertEqual(role['derivationEvidence']['rule'],'default')
        self.assertIn('heuristic',role['evidence'])
        self.assertIn('semanticDerivation', json.dumps(b['applicationIr']['models']))

    def test_app_role_priority_and_unknown_are_not_fabricated(self):
        current = dict(PROCESS, is_current_process=True,is_main_computer=True)
        main = dict(PROCESS, pid=18,is_main_computer=True)
        unknown = dict(PROCESS,pid=19)
        del unknown['is_current_process']
        v=resolve_knowledge({'processes':[current,main,unknown]},self.compile('task-manager.processes'))
        roles=[x['properties']['role'] for x in v['sources']['task-manager.processes']['records']]
        self.assertEqual(roles[0]['raw'],'current-process')
        self.assertEqual(roles[1]['raw'],'main-computer')
        self.assertEqual(roles[2]['state'],'missing')

    def test_identity_timestamp_is_normalized_only_with_explicit_format(self):
        b=self.compile('task-manager.processes',PROCESS_BIND)
        v=resolve_knowledge({'processes':[PROCESS]},b)
        rec=v['sources']['task-manager.processes']['records'][0]
        self.assertEqual(rec['properties']['created_at']['raw'],PROCESS['created_at'])
        self.assertEqual(rec['identity']['parts'],[17,'2026-10-28T00:28:30.000000+00:00'])
        plain=resolve_knowledge({'processes':[PROCESS]},self.compile('task-manager.processes'))
        self.assertEqual(plain['sources']['task-manager.processes']['records'][0]['identity']['parts'][1],PROCESS['created_at'])

    def test_cim_and_iso_identical_lifetime_if_binding_claims_formats(self):
        iso=self.compile('task-manager.processes',PROCESS_BIND)
        cim=self.compile('task-manager.processes',{'task-manager.processes':{'properties':{
            'created_at':{'path':'created_at','format':'cim-dmtf'}}}})
        a=resolve_knowledge({'processes':[PROCESS]},iso)['sources']['task-manager.processes']['records'][0]
        other = dict(PROCESS, created_at='20261027172830.000000-420')
        b=resolve_knowledge({'processes':[other]},cim)['sources']['task-manager.processes']['records'][0]
        self.assertEqual(a['identity']['parts'],b['identity']['parts'])
        self.assertNotEqual(a['properties']['created_at']['raw'],b['properties']['created_at']['raw'])
        self.assertNotEqual(iso['sourceBindingFingerprint'],cim['sourceBindingFingerprint'])
        self.assertEqual(iso['semanticFingerprint'],cim['semanticFingerprint'])

    def test_dotnet_and_explicit_epoch_units(self):
        stamp=normalize_timestamp('2026-10-28T00:28:30Z','iso8601')
        self.assertEqual(normalize_timestamp('20261027172830.000000-420','cim-dmtf'),stamp)
        self.assertEqual(normalize_timestamp(1793147310,'unix-seconds'),stamp)
        self.assertEqual(normalize_timestamp(1793147310000,'unix-milliseconds'),stamp)
        self.assertEqual(normalize_timestamp('/Date(1793147310000)/','dotnet-json'),stamp)

    def test_one_explicit_producer_binding_accepts_iso_cim_and_dotnet(self):
        binding={'task-manager.processes':{'properties':{
            'created_at':{'path':'created_at','formats':['iso8601','cim-dmtf','dotnet-json']}}}}
        b=self.compile('task-manager.processes',binding)
        src={'processes':[dict(PROCESS,pid=1),
             dict(PROCESS,pid=2,created_at='20261027172830.000000-420'),
             dict(PROCESS,pid=3,created_at='/Date(1793147310000)/')]}
        v=resolve_knowledge(src,b)['sources']['task-manager.processes']['records']
        self.assertEqual([x['properties']['created_at']['matchedFormat'] for x in v],
                         ['iso8601','cim-dmtf','dotnet-json'])
        self.assertEqual(len({x['identity']['parts'][1] for x in v}),1)
        self.assertEqual([x['properties']['created_at']['raw'] for x in v],
                         [x['created_at'] for x in src['processes']])

    def test_invalid_timestamp_is_record_scoped_and_preserves_source(self):
        b=self.compile('task-manager.processes',PROCESS_BIND)
        good=copy.deepcopy(PROCESS);bad=dict(PROCESS, pid=18, created_at='??')
        original={'processes':[bad,good]};preserved=copy.deepcopy(original)
        v=resolve_knowledge(original,b)
        rows=v['sources']['task-manager.processes']['records']
        self.assertEqual(rows[0]['properties']['created_at']['raw'],'??')
        self.assertEqual(rows[0]['properties']['created_at']['state'],'invalid-value')
        self.assertEqual(rows[0]['identity']['status'],'incomplete')
        self.assertEqual(rows[1]['identity']['status'],'complete')
        projected=collection_projection(v,'task-manager.processes')
        self.assertEqual(len(projected['rows']),1)
        self.assertEqual(len(projected['quarantinedRows']),1)
        self.assertIn('identity-incomplete',projected['quarantinedRows'][0]['reason'])
        self.assertEqual(projected['observationEnvelope']['completeness'],'unknown')
        self.assertEqual(original,preserved)
        self.assertIn('invalid-timestamp-format',[x['code'] for x in v['diagnostics']])

    def test_invalid_type_record_does_not_poison_valid(self):
        b=self.compile('task-manager.processes',PROCESS_BIND)
        v=resolve_knowledge({'processes':['not-a-mapping',PROCESS]},b)
        self.assertEqual(len(v['sources']['task-manager.processes']['records']),1)
        self.assertEqual(v['sources']['task-manager.processes']['rejectedRecords'][0]['raw'],'not-a-mapping')
        projected=collection_projection(v,'task-manager.processes')
        self.assertEqual(len(projected['rows']),1)
        self.assertEqual(projected['quarantinedRows'][0]['sourceRaw'],'not-a-mapping')

    def test_ambiguous_identity_remains_collection_level_refusal(self):
        b=self.compile('task-manager.processes')
        conflict=dict(PROCESS)
        unique=dict(PROCESS,pid=27)
        view=resolve_knowledge({'processes':[PROCESS,conflict,unique]},b)
        statuses=[r['identity']['status'] for r in view['sources']['task-manager.processes']['records']]
        self.assertEqual(statuses,['ambiguous','ambiguous','complete'])
        with self.assertRaisesRegex(SemanticCatalogError,'Identity unsafe'):
            collection_projection(view,'task-manager.processes')

    def test_measured_zero_and_unavailable_preserved(self):
        unavailable=dict(PROCESS,pid=22,memory_availability='unavailable')
        r=resolve_knowledge({'processes':[PROCESS,unavailable]},self.compile('task-manager.processes'))['sources']['task-manager.processes']['records']
        self.assertEqual(r[0]['properties']['resident_memory']['state'],'present')
        self.assertEqual(r[0]['properties']['resident_memory']['raw'],0)
        self.assertEqual(r[1]['properties']['resident_memory']['state'],'unavailable')
        self.assertEqual(r[1]['properties']['resident_memory']['raw'],0)

    def test_observation_envelope_preserves_known_and_unknown(self):
        binding={'task-manager.processes':{'properties':{'created_at':{'path':'created_at','format':'iso8601'}},
            'envelope':{'producer':'TaskManagerService.snapshot',
              'observedAt':{'path':'observation_time','format':'iso8601'},
              'revision':{'path':'publication_rev','domain':'publication'},
              'scope':{'kind':'bounded-top-n','parameters':{'limit':'request.limit','query':'request.query'}},
              'completeness':'bounded'}}}
        b=self.compile('task-manager.processes',binding)
        snapshot={'processes':[PROCESS],'observation_time':'2026-10-27T17:30:00-07:00',
                  'publication_rev':7,'request':{'limit':24}}
        v=resolve_knowledge(snapshot,b)
        e=v['sources']['task-manager.processes']['envelope']
        self.assertEqual(e['revision']['domain'],'publication')
        self.assertEqual(e['revision']['value'],7)
        self.assertEqual(e['scope']['kind'],'bounded-top-n')
        self.assertEqual(e['scope']['parameters']['limit']['raw'],24)
        self.assertEqual(e['scope']['parameters']['query']['state'],'missing')
        self.assertEqual(e['completeness'],'bounded')
        self.assertEqual(e['observedAt']['normalized'],'2026-10-28T00:30:00.000000+00:00')
        self.assertEqual(v['sources']['task-manager.processes']['records'][0]['identity']['status'],'complete')
        unknown=resolve_knowledge({'processes':[PROCESS]},self.compile('task-manager.processes'))
        self.assertIsNone(unknown['sources']['task-manager.processes']['envelope']['revision'])
        self.assertEqual(unknown['sources']['task-manager.processes']['envelope']['scope']['kind'],'unknown')
        self.assertEqual(unknown['sources']['task-manager.processes']['envelope']['completeness'],'unknown')

    def test_no_invented_revision_or_full_enumeration(self):
        for invalid in [
           {'revision':{'path':'rev','domain':'imaginary'}},
           {'revision':7},
           {'completeness':'producer-reported-complete'},
           {'scope':{'kind':'complete-system'}},
           {'observedAt':{'path':'when'}},
        ]:
            with self.subTest(invalid=invalid),self.assertRaises(SemanticCatalogError):
                self.compile('task-manager.processes',{'task-manager.processes':{'envelope':invalid}})

    def test_reject_presentation_settings_in_semantic_bindings(self):
        for attempt in [{'layout':{'columns':3}},{'fields':{'name':'x'}},{'labels':{'pid':'PID'}}]:
            with self.subTest(attempt=attempt), self.assertRaises(SemanticCatalogError):
                self.compile('task-manager.processes',{'task-manager.processes':attempt})
        with self.assertRaises(SemanticCatalogError):
            self.compile('task-manager.processes',{'task-manager.processes':{'properties':{
                'pid':{'path':'pid','format':'unix-seconds'}}}})

    def test_source_binding_format_is_not_semantic_fingerprint(self):
        orig=self.compile('file-explorer.entries')
        formatted=self.compile('file-explorer.entries',{'file-explorer.entries':{'properties':{
            'modified_at':{'path':'mtime','format':'unix-seconds'}}}})
        self.assertEqual(orig['semanticFingerprint'],formatted['semanticFingerprint'])
        self.assertNotEqual(orig['sourceBindingFingerprint'],formatted['sourceBindingFingerprint'])

    def test_logical_worker_distinct_from_lease_and_not_authoritative(self):
        w={'cluster_id':'c','worker_id':'w','connection_id':'s','lease_epoch':1,'status':'active'}
        binding=self.compile('worker.logical-workers')
        v=resolve_knowledge({'worker_market':{'market_workers':[w]}},binding)
        rec=v['sources']['worker.logical-workers']['records'][0]
        self.assertEqual(rec['identity']['parts'],['c','w'])
        lease=self.compile('worker.market-workers')
        l=resolve_knowledge({'worker_market':{'market_workers':[w]}},lease)['sources']['worker.market-workers']['records'][0]
        self.assertEqual(l['identity']['parts'],['c','w','s',1])
        self.assertNotIn('reconciliation',collection_projection(v,'worker.logical-workers')['spec'])
        self.assertFalse(any(x['authorized'] for x in v['operations'].values()))

    def test_two_leases_same_logical_worker_remain_ambiguous(self):
        w={'cluster_id':'c','worker_id':'w','connection_id':'s1','lease_epoch':1}
        w2=dict(w,connection_id='s2',lease_epoch=2)
        v=resolve_knowledge({'worker_market':{'market_workers':[w,w2]}},self.compile('worker.logical-workers'))
        self.assertEqual([r['identity']['status'] for r in v['sources']['worker.logical-workers']['records']],['ambiguous','ambiguous'])

    def test_worker_relation_candidates_not_authority(self):
        w={'cluster_id':'c','worker_id':'w','connection_id':'s','lease_epoch':1}
        b=compile_knowledge_ir('foundation-test',observation_ids=['worker.logical-workers','worker.market-workers'])
        v=resolve_knowledge({'worker_market':{'market_workers':[w]}},b)
        rel=v['sources']['worker.market-workers']['records'][0]['relationships'][0]
        self.assertEqual(rel['status'],'candidate-only')

    def test_unknown_formats_fail_closed(self):
        for fmt in ('auto','epoch','unix','cim',''):
            with self.assertRaises(ValueError):normalize_timestamp('2026-10-27T17:28:30-07:00',fmt)
        for invalid in ('20261027172830.000000***','2026-10-27T17:28:30', '/Date(invalid)/'):
            fmt='cim-dmtf' if invalid.startswith('202610') else 'dotnet-json' if invalid.startswith('/Date') else 'iso8601'
            with self.assertRaises(ValueError):normalize_timestamp(invalid,fmt)

    def test_no_side_effects_or_layout_in_bridge(self):
        b=self.compile('task-manager.processes',PROCESS_BIND)
        v=resolve_knowledge({'processes':[PROCESS]},b)
        self.assertFalse(v['operations']['task-manager.kill']['authorized'])
        self.assertFalse(v['operations']['task-manager.kill']['executable'])
        self.assertFalse(b['applicationIr']['layouts'])
        self.assertFalse(b['applicationIr']['effects'])


if __name__=='__main__':unittest.main()
