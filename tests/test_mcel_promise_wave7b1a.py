"""07B.1a graph provenance and anti-layout-leakage negative controls."""
import copy
import json
from pathlib import Path
import re
import unittest

from main_computer.mcel_application_ir import validate_application_ir
from main_computer.mcel_knowledge import load_catalog
from main_computer.mcel_knowledge.ir_bridge import compile_knowledge_ir
from main_computer.mcel_knowledge.semantic_graph import (
    SemanticAuditError, extract_validated_graph, audit_application, audit_catalog_join,
)

ROOT = Path(__file__).resolve().parents[1]


def fixture(path):
    return json.loads((ROOT / path).read_text(encoding='utf8'))


def generated_ir():
    """Read generated artifact as evidence, NOT recover missing authoring source."""
    p = ROOT / 'main_computer/web/applications/scripts/mcel-file-explorer-candidate-ir.js'
    text = p.read_text(encoding='utf8')
    match = re.search(r'\bir\s*:\s*(\{)', text)
    if not match:
        raise AssertionError('Generated artifact has no canonical IR')
    return json.JSONDecoder().raw_decode(text[match.start(1):])[0]


class GuardedDict(dict):
    BLOCKED = {'surfaces','layouts','provenance','scenarios','proof','normalization','fingerprints',
               'source', 'semanticSurface', 'application', 'route', 'runtimeFacade', 'presentationAuthority'}
    def __getitem__(self, key):
        if key in self.BLOCKED:
            raise AssertionError('PRESENTATION READ: '+key)
        return super().__getitem__(key)
    def get(self, key, default=None):
        if key in self.BLOCKED:
            raise AssertionError('PRESENTATION READ: '+key)
        return super().get(key, default)


class Wave7B1ATests(unittest.TestCase):
    def test_file_explorer_reports_insufficient_without_faking_workflow(self):
        report = audit_application(generated_ir(), catalog=load_catalog())
        self.assertTrue(report['valid'])
        self.assertEqual(report['status'], 'SEMANTICS_INSUFFICIENT')
        self.assertEqual(report['catalogJoin']['status'], 'BLOCKED_UNJOINED_SOURCES')
        self.assertEqual(report['counts']['declaredDataDependencies'], 0)
        self.assertEqual(report['counts']['verifiedTaskCoordinations'], 0)
        self.assertFalse(report['synthesisReady'])
        self.assertFalse(any(e['impliesUiCoordination'] for e in report['edges']))
        self.assertIn('TASK_COORDINATION_NOT_ESTABLISHED', {x['code'] for x in report['unresolved']})

    def test_extractor_cannot_read_layout_or_surface(self):
        raw = generated_ir()
        valid = validate_application_ir(raw)
        self.assertTrue(valid.valid)
        spy = GuardedDict(valid.normalized)
        graph = extract_validated_graph(spy)
        self.assertEqual(graph['status'], 'SEMANTICS_INSUFFICIENT')

    def test_shell_deletion_and_layout_poisoning_are_byte_invariant(self):
        normalized = validate_application_ir(generated_ir()).normalized
        baseline = extract_validated_graph(normalized)
        mutated = copy.deepcopy(normalized)
        mutated['surfaces'] = [{'id':'surface:poison','semanticSurface':{'applicationShell':{
            'layout':'poison','regions':[{'zone':'sidebar','kind':'collection','selectControl':'fabricated'}]}}}]
        mutated['layouts'] = [{'id':'layout:poison','orderedChildren':['preview','roots','entries']}]
        self.assertEqual(extract_validated_graph(mutated), baseline)
        mutated.pop('surfaces')
        mutated.pop('layouts')
        self.assertEqual(extract_validated_graph(mutated), baseline)

    def test_task_manager_tab_dataflow_not_an_inspector(self):
        raw = fixture('tools/fixtures/mcel_acid_tab_ir.json')
        report = audit_application(raw)
        self.assertTrue(report['valid'])
        self.assertEqual(report['status'], 'DEPENDENCIES_EXTRACTED')
        self.assertGreater(report['counts']['declaredDataDependencies'], 0)
        self.assertEqual(report['counts']['verifiedTaskCoordinations'], 0)
        self.assertTrue(any(x['kind']=='view-writes-state' for x in report['edges']))
        self.assertFalse(any('inspector' in x['kind'] for x in report['edges']))

    def test_reference_counter_dataflow_is_not_a_ui_coordination(self):
        graph = audit_application(fixture('tests/fixtures/mcel_application_ir/reference-counter.ir.json'))
        self.assertTrue(graph['valid'])
        self.assertGreater(graph['counts']['mechanicalDataflows'], 0)
        self.assertEqual(graph['counts']['verifiedTaskCoordinations'], 0)
        for edge in graph['edges']:
            self.assertIn(edge['certainty'], ('mechanically-derived','explicit-verified'))
            self.assertTrue(edge['evidence'])
            self.assertFalse(edge['impliesUiCoordination'])

    def test_legal_semantic_ablation_removes_only_read_edges(self):
        raw = fixture('tools/fixtures/mcel_acid_tab_ir.json')
        baseline = audit_application(raw)
        modified = copy.deepcopy(raw)
        modified['intents'][0]['reads'] = []
        after = audit_application(modified)
        self.assertTrue(after['valid'], after.get('diagnostics'))
        self.assertNotEqual(after['projectionFingerprint'], baseline['projectionFingerprint'])
        self.assertFalse(any(e['kind']=='reads-state' for e in after['edges']))
        self.assertTrue(any(e['kind']=='view-writes-state' for e in after['edges']))
        self.assertEqual(after['counts']['verifiedTaskCoordinations'], 0)

    def test_artifact_metadata_and_cosmetic_changes_ignored(self):
        raw = generated_ir()
        baseline = audit_application(raw)
        changed = copy.deepcopy(raw)
        changed['application']['title'] = 'LIES ABOUT THE INTERFACE'
        for i, item in enumerate(changed.get('surfaces', [])):
            item['semanticSurface'] = {'applicationShell':{'layout':'sidebar-workspace',
                'regions':[], 'title':'inspector and navigation'}}
        self.assertEqual(extract_validated_graph(validate_application_ir(raw).normalized)['projectionFingerprint'],
                         extract_validated_graph(GuardedDict(validate_application_ir(raw).normalized))['projectionFingerprint'])
        # Mutated presentation is excluded by the pure extractor; it is not
        # necessarily a valid full IR (the shell validator is a different layer).
        self.assertEqual(extract_validated_graph(changed)['projectionFingerprint'],baseline['projectionFingerprint'])

    def test_input_dictionary_order_and_repetition_deterministic(self):
        raw = fixture('tests/fixtures/mcel_application_ir/reference-counter.ir.json')
        a = audit_application(raw)
        changed = copy.deepcopy(raw)
        for key in ('states','intents','effects','surfaces','layouts'):
            changed[key].reverse()
        self.assertEqual(a['projectionFingerprint'], audit_application(changed)['projectionFingerprint'])
        self.assertEqual(a, audit_application(raw))

    def test_catalog_requires_explicit_model_crosswalk(self):
        catalog=load_catalog()
        compiled = compile_knowledge_ir('test-app', observation_ids=['file-explorer.entries'])
        graph = audit_application(compiled['applicationIr'], catalog=catalog)
        self.assertEqual(graph['catalogJoin']['status'], 'JOINED_MODELS_ONLY')
        self.assertEqual(graph['catalogJoin']['joins'][0]['observationId'], 'file-explorer.entries')
        self.assertEqual(graph['catalogJoin']['operationsImported'],0)
        self.assertEqual(graph['counts']['verifiedTaskCoordinations'],0)
        self.assertEqual(graph['status'],'SEMANTICS_INSUFFICIENT')

    def test_no_fabricated_authority_identity_or_catalog_operations(self):
        r = audit_application(generated_ir(), catalog=load_catalog())
        self.assertEqual(r['catalogJoin']['operationsImported'],0)
        self.assertFalse(any(n['kind']=='control' for n in r['nodes']))
        self.assertFalse(any(n['kind']=='selection' for n in r['nodes']))
        self.assertFalse(any(n['kind']=='task' for n in r['nodes']))
        for n in r['nodes']:
            self.assertNotIn('authorized',n['facts'])
            self.assertNotIn('rowKey',n['facts'])

    def test_invalid_ir_fails_closed(self):
        raw = fixture('tools/fixtures/mcel_acid_tab_ir.json')
        raw['intents'][0]['reads'] = [{'ref':'state:does-not-exist'}]
        invalid = audit_application(raw)
        self.assertFalse(invalid['valid'])
        self.assertEqual(invalid['status'],'FAIL_CONTRACT')
        self.assertTrue(invalid['diagnostics'])

    def test_unbound_model_join_refuses_to_guess(self):
        catalog=load_catalog()
        compiled=compile_knowledge_ir('test-app',observation_ids=['file-explorer.entries'])
        source=copy.deepcopy(compiled['applicationIr'])
        source['models'][0]['observationRef']='missing.observation'
        # This is a deliberate *post-validation* crosswalk contract tamper.
        graph=extract_validated_graph(source)
        with self.assertRaises(SemanticAuditError):
            audit_catalog_join(graph,catalog)


if __name__=='__main__':
    unittest.main()
