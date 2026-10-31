"""07B.1b: semantic-only authoring probe; strictly no interface synthesis."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from main_computer.mcel_application_ir import validate_application_ir
from main_computer.mcel_knowledge import load_catalog
from main_computer.mcel_knowledge.semantic_graph import (
    SemanticAuditError, audit_application, extract_validated_graph,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/mcel_semantic_synthesis/file_explorer_semantic_only.ir.json'
BASE = 'file-explorer-semantic-probe'


def fixture():
    return json.loads(FIXTURE.read_text(encoding='utf8'))


def ids(graph, kind):
    return {(edge['from'], edge['to']) for edge in graph['edges'] if edge['kind'] == kind}


def intent(name):
    return f'intent:{BASE}.{name}'


def model(name):
    return f'model:knowledge.file-explorer.{name}'


class Wave7B1BTests(unittest.TestCase):
    def test_fixture_is_existing_valid_ir_without_layout_or_runtime(self):
        raw = fixture()
        validation = validate_application_ir(raw)
        self.assertTrue(validation.valid, [(x.code, x.problem) for x in validation.diagnostics])
        self.assertEqual(raw['surfaces'] if 'surfaces' in raw else [], [])
        self.assertEqual(raw['layouts'] if 'layouts' in raw else [], [])
        self.assertEqual(raw.get('effects', []), [])
        self.assertFalse('semanticSurface' in json.dumps(raw))
        self.assertFalse('applicationShell' in json.dumps(raw))
        self.assertFalse(any(i.get('executionBinding') for i in raw['intents']))
        self.assertFalse(any(i.get('runtimeMethod') for i in raw['intents']))

    def test_three_models_explicitly_join_catalog_without_importing_actions(self):
        graph = audit_application(fixture(), catalog=load_catalog())
        self.assertEqual(graph['catalogJoin']['status'], 'JOINED_MODELS_ONLY')
        self.assertEqual({x['observationId'] for x in graph['catalogJoin']['joins']},
                         {'file-explorer.roots', 'file-explorer.entries', 'file-explorer.read-preview'})
        self.assertEqual(graph['catalogJoin']['operationsImported'], 0)
        self.assertFalse(any(n['kind'] == 'effect' for n in graph['nodes']))

    def test_bounded_typed_selection_edges_have_real_provenance(self):
        graph = audit_application(fixture())
        self.assertEqual(ids(graph, 'bounded-typed-view-selection'), {
            (intent('select-root'), model('roots')),
            (intent('select-entry'), model('entries')),
        })
        self.assertEqual(graph['counts']['boundedTypedViewSelections'], 2)
        edges = [e for e in graph['edges'] if e['kind']=='bounded-typed-view-selection']
        for edge in edges:
            self.assertEqual(edge['certainty'], 'mechanically-derived')
            self.assertFalse(edge['impliesUiCoordination'])
            self.assertTrue(any('/viewTransition/' in p for p in edge['evidence']))
            self.assertTrue(any('/schema/of/ref' in p for p in edge['evidence']))

    def test_dataflow_expresses_preconditions_not_preview_rendering(self):
        graph = audit_application(fixture())
        relations = ids(graph, 'shared-state-dataflow')
        self.assertIn((intent('select-root'), intent('list-directory')),relations)
        self.assertIn((intent('select-entry'), intent('read-preview')),relations)
        self.assertIn((intent('select-root'), intent('read-preview')),relations)
        self.assertEqual(graph['counts']['mechanicalDataflows'], 4)
        self.assertEqual(graph['counts']['verifiedTaskCoordinations'], 0)
        self.assertFalse(graph['synthesisReady'])
        self.assertNotIn((intent('list-directory'), model('entries')),relations)
        self.assertNotIn((intent('read-preview'), model('read-preview')), relations)
        self.assertTrue(all(not e['impliesUiCoordination'] for e in graph['edges']))

    def test_preview_read_ablation_drops_only_corresponding_dependencies(self):
        raw=fixture()
        before = ids(audit_application(raw),'shared-state-dataflow')
        for i in raw['intents']:
            if i['id'] == intent('read-preview'):
                i['reads'] = []
        after = ids(audit_application(raw),'shared-state-dataflow')
        self.assertEqual(before-after, {
            (intent('select-root'),intent('read-preview')),
            (intent('select-entry'),intent('read-preview')),
        })
        self.assertEqual(after-before,set())
        self.assertEqual(len(ids(audit_application(raw),'bounded-typed-view-selection')),2)

    def test_select_transition_ablation_drops_selection_and_its_dataflow(self):
        raw=fixture()
        for i in raw['intents']:
            if i['id'] == intent('select-entry'):
                i.pop('viewTransition')
                i.pop('viewWrites')
        graph=audit_application(raw)
        self.assertTrue(graph['valid'],graph.get('diagnostics'))
        self.assertNotIn((intent('select-entry'),model('entries')),
                         ids(graph,'bounded-typed-view-selection'))
        self.assertNotIn((intent('select-entry'),intent('read-preview')),
                         ids(graph,'shared-state-dataflow'))

    def test_mismatched_typed_input_is_not_a_selection_of_model(self):
        raw=fixture()
        for i in raw['intents']:
            if i['id']==intent('select-entry'):
                i['input'][0]['schema']['of']['ref']=model('roots')
        graph=audit_application(raw)
        self.assertTrue(graph['valid'])
        self.assertNotIn((intent('select-entry'),model('entries')),
                         ids(graph,'bounded-typed-view-selection'))
        self.assertEqual(graph['counts']['boundedTypedViewSelections'],1)

    def test_schema_of_invalid_ref_is_fail_closed(self):
        raw=fixture()
        raw['states'][0]['schema']['of']='file-explorer.roots'
        normalized=validate_application_ir(raw)
        self.assertTrue(normalized.valid)  # Existing IR treats `of` as open schema metadata.
        with self.assertRaises(SemanticAuditError):
            extract_validated_graph(normalized.normalized)

    def test_unknown_model_ref_is_rejected_by_canonical_validator(self):
        raw=fixture()
        raw['states'][0]['schema']['of']['ref']='model:does-not-exist'
        result=validate_application_ir(raw)
        self.assertFalse(result.valid)
        self.assertIn('MCEL_IR_REFERENCE_UNRESOLVED',{x.code for x in result.diagnostics})

    def test_invalid_view_state_authority_rejected(self):
        raw=fixture()
        raw['states'][0]['authority']='canonical'
        result=validate_application_ir(raw)
        self.assertFalse(result.valid)
        self.assertIn('MCEL_IR_VIEW_TRANSITION_INVALID',{x.code for x in result.diagnostics})

    def test_dynamic_selection_not_accepted_as_bounded_view_transition(self):
        raw=fixture()
        raw['states'][0]['schema'].pop('enum')
        raw['intents'][0]['input'][0]['schema'].pop('enum')
        self.assertFalse(validate_application_ir(raw).valid)

    def test_layout_poisoning_and_cosmetic_names_do_not_affect_graph(self):
        raw=fixture()
        normalized=validate_application_ir(raw).normalized
        original=extract_validated_graph(normalized)
        changed=copy.deepcopy(normalized)
        changed['layouts']=[{'id':'layout:poison', 'kind':'layout','orderedChildren':[]}]
        changed['surfaces']=[{'id':'surface:poison', 'kind':'surface','semanticSurface':{
            'applicationShell':{'regions':[{'kind':'inspector','zone':'sidebar'}]}}}]
        changed['application']['title']='False hierarchy/inspector claims'
        self.assertEqual(extract_validated_graph(changed),original)
        self.assertEqual(extract_validated_graph(changed)['projectionFingerprint'],
                         original['projectionFingerprint'])

    def test_determinism_under_declaration_permutation(self):
        raw=fixture()
        baseline=audit_application(raw,catalog=load_catalog())
        reordered=copy.deepcopy(raw)
        for key in ('models','states','intents'):
            reordered[key].reverse()
        graph=audit_application(reordered,catalog=load_catalog())
        self.assertEqual(graph['projectionFingerprint'],baseline['projectionFingerprint'])
        self.assertEqual(ids(graph,'bounded-typed-view-selection'),
                         ids(baseline,'bounded-typed-view-selection'))
        self.assertEqual(ids(graph,'shared-state-dataflow'),
                         ids(baseline,'shared-state-dataflow'))

    def test_no_unverified_semantic_claims_or_authorization(self):
        graph=audit_application(fixture(),catalog=load_catalog())
        self.assertFalse(graph['synthesisReady'])
        self.assertEqual(graph['catalogJoin']['verifiedTaskRelationsImported'],0)
        self.assertEqual(graph['counts']['verifiedTaskCoordinations'],0)
        self.assertNotIn('typed-inspector', {e['kind'] for e in graph['edges']})
        self.assertNotIn('result-binds-to-model', {e['kind'] for e in graph['edges']})
        self.assertEqual(graph['status'],'DEPENDENCIES_EXTRACTED')


if __name__=='__main__':
    unittest.main()
