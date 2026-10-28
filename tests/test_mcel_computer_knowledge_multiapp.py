"""Independent offline checks of source-grounded multi-app computer semantics."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from main_computer.mcel_knowledge import CATALOG, SemanticCatalogError, interpret, load_catalog

FIXTURE = Path(__file__).parents[1] / 'tools/fixtures/mcel_computer_knowledge/expanded_computer_snapshot.v1.json'


def data():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


class MultiAppKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()

    def test_builtin_vocabulary_has_no_duplicate_concept_ids(self):
        self.assertGreaterEqual(len(self.catalog['concepts']), 40)
        self.assertEqual(len(self.catalog['concepts']), len(set(self.catalog['concepts'])))
        self.assertEqual({x['concept'] for x in self.catalog['observations'].values() if x['id'].startswith('file-explorer.')}, {'mc.file.root', 'mc.file.entry', 'mc.file.preview'})
        self.assertEqual(sum(x.startswith('task-manager.') for x in self.catalog['observations']), 8)

    def test_file_entry_identity_is_root_scoped_and_does_not_modify_source(self):
        raw = data(); before = copy.deepcopy(raw)
        graph = interpret(raw)
        rows = graph['sources']['file-explorer.entries']['records']
        self.assertEqual(rows[0]['identity']['parts'], ['workspace', 'tools/runs'])
        self.assertEqual(rows[1]['identity']['parts'], ['workspace', 'tools/result.json'])
        self.assertEqual(raw, before)
        self.assertEqual(graph['sources']['file-explorer.entries']['sourcePath'], 'entries')
        self.assertEqual(rows[0]['context']['root_id'], 'workspace')
        self.assertEqual(rows[0]['properties']['relative_path']['raw'], 'tools/runs')

    def test_missing_root_context_prevents_complete_file_identity(self):
        raw = data(); del raw['root_id']
        rows = interpret(raw)['sources']['file-explorer.entries']['records']
        self.assertEqual(rows[0]['identity']['status'], 'incomplete')
        self.assertIsNone(rows[0]['identity']['parts'])

    def test_file_size_and_mtime_producer_placeholders_not_measured_zero(self):
        rows = interpret(data())['sources']['file-explorer.entries']['records']
        self.assertEqual(rows[0]['properties']['size_bytes']['state'], 'unavailable')
        self.assertEqual(rows[0]['properties']['modified_at']['state'], 'unavailable')
        self.assertEqual(rows[1]['properties']['size_bytes']['state'], 'present')
        self.assertEqual(rows[1]['properties']['size_bytes']['raw'], 0)
        self.assertEqual(rows[1]['properties']['size_bytes']['unit'], 'bytes')

    def test_search_reuses_entry_semantics_without_duplicate_observation(self):
        raw = data(); raw['results'] = raw.pop('entries')
        graph = interpret(raw)
        self.assertEqual(graph['sources']['file-explorer.entries']['sourcePath'], 'results')
        self.assertEqual(len(graph['sources']['file-explorer.entries']['records']), 2)

    def test_preview_readable_false_is_not_a_failed_or_missing_boolean(self):
        g = interpret({'entry': {'kind':'file', 'relative_path':'a.bin'}, 'readable': False,
                       'reason':'binary file preview is disabled', 'read_only':True})
        props = g['sources']['file-explorer.read-preview']['records'][0]['properties']
        self.assertEqual(props['readable']['raw'], False)
        self.assertEqual(props['readable']['state'], 'present')
        self.assertEqual(props['reason']['raw'], 'binary file preview is disabled')
        self.assertEqual(props['encoding']['state'], 'missing')

    def test_scheduler_is_stored_plan_not_execution(self):
        graph = interpret(data())
        x = graph['sources']['task-manager.schedules']['records'][0]
        self.assertEqual(x['properties']['run_at']['raw'], '2026-10-28T11:30:00+00:00')
        self.assertEqual(x['properties']['action']['type'], 'mc.schedule.action')
        self.assertEqual(x['properties']['enabled']['raw'], True)
        for key in ['scheduler.create-server-action', 'scheduler.delete-server-action']:
            self.assertFalse(graph['operations'][key]['authorized'])
            self.assertFalse(graph['operations'][key]['executable'])

    def test_worker_map_values_without_identity_from_map_key(self):
        raw = data()
        raw['worker_market']['market_workers'] = {'alias': raw['worker_market']['market_workers']['w1']}
        graph = interpret(raw)
        r = graph['sources']['worker.market-workers']['records'][0]
        self.assertEqual(r['identity']['parts'], ['test-cluster','w1','socket-B',2])
        self.assertEqual(r['properties']['price']['raw'], {'amount':'0.1','unit':'credit'})
        self.assertEqual(r['properties']['capabilities']['raw'], ['text'])
        self.assertEqual(r['properties']['active_sessions']['state'], 'present')

    def test_worker_list_projection_supported_without_special_case(self):
        raw = data()
        raw['worker_market']['workers'] = list(raw['worker_market'].pop('market_workers').values())
        raw['worker_sessions']['sessions'] = list(raw['worker_sessions'].pop('accepted_sessions').values())
        graph = interpret(raw)
        self.assertEqual(graph['sources']['worker.market-workers']['sourcePath'], 'worker_market.workers')
        self.assertEqual(graph['sources']['worker.sessions']['sourcePath'], 'worker_sessions.sessions')
        self.assertEqual(len(graph['sources']['worker.sessions']['records']), 2)

    def test_worker_lease_matching_is_candidate_only_not_assumed_ownership(self):
        graph = interpret(data())
        rows = graph['sources']['worker.sessions']['records']
        self.assertEqual(rows[0]['relationships'][0]['status'], 'candidate-only')
        self.assertEqual(rows[0]['relationships'][0]['candidateIndices'], [0])
        self.assertEqual(rows[1]['relationships'][0]['status'], 'unresolved')
        # The worker ID matches but a socket generation mismatch must reject it.
        self.assertFalse(graph['operations']['worker.runtime-sync']['authorized'])

    def test_worker_bad_type_is_invalid_not_normalized(self):
        raw = data(); raw['worker_market']['market_workers']['w1']['capabilities'] = 'text'
        raw['worker_market']['market_workers']['w1']['price'] = '0.1'
        raw['worker_market']['market_workers']['w1']['active_sessions'] = -1
        fields = interpret(raw)['sources']['worker.market-workers']['records'][0]['properties']
        self.assertEqual(fields['capabilities']['state'], 'invalid-value')
        self.assertEqual(fields['price']['state'], 'invalid-value')
        self.assertEqual(fields['active_sessions']['state'], 'invalid-value')

    def test_worker_duplicate_lease_ambiguous_and_never_affects_source(self):
        raw = data(); first = copy.deepcopy(raw['worker_market']['market_workers']['w1'])
        raw['worker_market']['market_workers']['other-key'] = first
        rows = interpret(raw)['sources']['worker.market-workers']['records']
        self.assertEqual(rows[0]['identity']['status'], 'ambiguous')
        self.assertEqual(rows[2]['identity']['status'], 'ambiguous')

    def test_worker_runtime_policy_is_observation_not_authority(self):
        raw = data(); graph = interpret(raw)
        fields = graph['sources']['worker.runtime']['records'][0]['properties']
        self.assertEqual(fields['allowed_to_accept']['raw'], True)
        self.assertEqual(fields['allowed_to_accept']['evidence'], 'runtime-reported-policy-not-semantic-authorization')
        self.assertEqual(fields['status']['raw'], 'accepting')
        for op in graph['operations'].values():
            self.assertFalse(op['authorized']); self.assertFalse(op['executable'])

    def test_invalid_relation_declaration_fails_catalog_loading(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            for f in CATALOG.glob('*.json'):
                (target / f.name).write_bytes(f.read_bytes())
            p = target / '20-worker.json'
            obj = json.loads(p.read_text()); obj['observations'][1]['relationships'][0]['matches'][0]['targetProperty'] = 'fake'
            p.write_text(json.dumps(obj))
            with self.assertRaises(SemanticCatalogError): load_catalog(target)

    def test_missing_worker_source_is_not_empty_success(self):
        g = interpret({'worker_market': {'market_workers': []}})
        self.assertEqual(g['sources']['worker.market-workers']['state'], 'present')
        self.assertEqual(g['sources']['worker.sessions']['state'], 'missing-source')

if __name__ == '__main__':
    unittest.main()
