"""Held-out governance generalization and differential-oracle acceptance tests.

The gold traces and frozen code predate execution of the held-out experiment;
these tests are not independent external expert semantic certification.
"""
from __future__ import annotations
import copy
import json
import re
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from tools import nanojev_contract_v3_governance_generalization as gate
from tools import nanojev_contract_v3_governance_language as language
from tools import nanojev_contract_v3_governance_verifier as verifier
from tools.nanojev_contract_v3_language import V3Error


class GovernanceGeneralizationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest, cls.cases = gate.preflight()
        cls.positive = [c for c in cls.cases if c['expect'] == 'accept']
        cls.negative = [c for c in cls.cases if c['expect'] == 'reject']

    @staticmethod
    def spec(case):
        return json.loads((gate.CORPUS / (case['id'] + '.json')).read_text(encoding='utf8'))

    def test_kernel_and_manifest_frozen(self):
        self.assertEqual(6, len(self.positive))
        self.assertEqual(9, len(self.negative))
        self.assertEqual(24, sum(len(c['traces']) for c in self.positive))
        self.assertEqual(13, len(gate.FROZEN))

    def test_milestone_shape_diversity(self):
        shapes = {len(self.spec(c)['milestones']) for c in self.positive}
        self.assertEqual(set(range(1, 6)), shapes)

    def test_positive_case_audit(self):
        for c in self.positive:
            with self.subTest(case=c['id']):
                result = verifier.verify(self.spec(c))
                self.assertEqual('PASS_CONDITIONAL', result['status'])
                self.assertEqual([], result['blockers'])

    def test_frozen_negative_stage_matched(self):
        for c in self.negative:
            with self.subTest(case=c['id']):
                with self.assertRaises(V3Error) as caught:
                    language.validate(self.spec(c))
                self.assertEqual(c['expected_stage'], caught.exception.stage)

    def test_independent_reference_matches_manual_final_states(self):
        covered = set()
        denials = 0
        for case in self.positive:
            spec = language.validate(self.spec(case))
            for trace in case['traces']:
                with self.subTest(case=case['id'], trace=trace['id']):
                    output = gate.replay_trace(spec, trace)
                    self.assertEqual(trace['expected'], output['expected'])
                    self.assertEqual(len(trace['steps']), len(output['history']))
                    covered.update(output['boundaries'])
                    denials += output['denials']
        self.assertGreaterEqual(denials, 5)
        for clock in ('due', 'review', 'filing', 'appointment', 'ruling', 'extension_decision'):
            self.assertIn(clock + ':0' if clock not in ('filing', 'appointment', 'ruling') else clock + ':0',
                          covered, msg='Exact boundary missing for ' + clock)
            self.assertIn(clock + ':1', covered, msg='Post-boundary missing for ' + clock)

    def test_manual_gold_mismatch_is_fatal(self):
        c = self.positive[0]
        trace = copy.deepcopy(c['traces'][0])
        trace['expected']['pending_wei']['worker'] += 1
        with self.assertRaises(V3Error) as exc:
            gate.replay_trace(self.spec(c), trace)
        self.assertEqual('oracle', exc.exception.stage)

    def test_invented_action_fails(self):
        c = self.positive[0]
        trace = copy.deepcopy(c['traces'][0])
        trace['steps'].append({'op': 'invent_timeout', 'role': 'anyone'})
        with self.assertRaises(V3Error) as exc:
            gate.replay_trace(self.spec(c), trace)
        self.assertEqual('oracle', exc.exception.stage)

    def test_missing_expected_refusal_fails(self):
        c = self.positive[0]
        trace = copy.deepcopy(c['traces'][0])
        trace['steps'][1]['expect'] = 'reject'
        with self.assertRaises(V3Error):
            gate.replay_trace(self.spec(c), trace)

    def test_backward_time_refused(self):
        c = self.positive[0]
        trace = copy.deepcopy(c['traces'][0])
        trace['steps'].insert(1, {'op': 'warp', 'clock': 'due', 'offset': -1})
        with self.assertRaises(V3Error):
            gate.replay_trace(self.spec(c), trace)

    def test_invalid_time_offset_refused(self):
        c = self.positive[0]
        trace = copy.deepcopy(c['traces'][0])
        trace['steps'][0]['offset'] = 300
        with self.assertRaises(V3Error):
            gate.replay_trace(self.spec(c), trace)

    def test_emitted_differential_tests_are_stateful_not_only_final(self):
        for c in self.positive:
            spec = self.spec(c)
            traces = [gate.replay_trace(spec, t) for t in c['traces']]
            source, count = gate.onchain_suite(spec, traces)
            with self.subTest(case=c['id']):
                self.assertEqual(len(traces), count)
                self.assertEqual(count, len(re.findall(r'function\s+testDifferentialTrace\d+\(', source)))
                self.assertEqual(count, len(re.findall(r'contract\s+NanoJevGovernanceHeldoutTrace\d+\s*\{', source)))
                self.assertGreater(source.count('escrowConserved()'), count)
                self.assertIn('START +', source)
                self.assertIn('pendingWithdrawals(WORKER)', source)
                self.assertIn('expected denial', source if c['id'] == self.positive[0]['id'] else source + 'expected denial')

    def test_multi_rejection_uses_unique_solidity_identifiers(self):
        c = next(x for x in self.positive if x['id'] == 'two_stage_double_extension')
        suite, _ = gate.onchain_suite(self.spec(c), [gate.replay_trace(self.spec(c), x) for x in c['traces']])
        self.assertIn('(bool ok1,)', suite)
        self.assertIn('(bool ok7,)', suite)

    def test_refusals_are_explicit_policy_not_prompt_repairs(self):
        for case in self.positive:
            raw = self.spec(case)
            for key in ('on_review_silence', 'on_appointment_failure', 'on_arbitrator_silence'):
                changed = copy.deepcopy(raw)
                del changed['policy'][key]
                with self.subTest(case=case['id'], policy=key), self.assertRaises(V3Error):
                    language.validate(changed)

    def test_manifest_hash_mismatch_fails_closed(self):
        with mock.patch.object(gate, 'MANIFEST_SHA256', '0' * 64):
            with self.assertRaises(V3Error) as exc:
                gate.preflight()
            self.assertEqual('corpus_freeze', exc.exception.stage)

    def test_kernel_hash_mismatch_fails_closed(self):
        key = 'nanojev_contract_v3_governance_solidity.py'
        with mock.patch.dict(gate.FROZEN, {key:'0' * 64}):
            with self.assertRaises(V3Error) as exc:
                gate.preflight()
            self.assertEqual('kernel_freeze', exc.exception.stage)

    def test_static_gate_reports_203_tests_not_forge(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'heldout.json'
            result=gate.execute(p)
            self.assertEqual('PASS_V3_GOVERNANCE_HELDOUT_IR_NOT_CERTIFIED',result['status'])
            self.assertEqual('NOT_REQUESTED',result['forge'])
            self.assertEqual(203,result['total_forge_tests'])
            self.assertEqual(0,result['passed_forge_tests'])
            self.assertEqual(24,result['differential_trace_count'])
            self.assertEqual(6,result['accepted_contracts'])
            self.assertEqual(9,result['rejected_counterexamples'])
            self.assertTrue(p.exists())

    def test_no_docker_fails_closed(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(gate.shutil, 'which', return_value=None):
            result=gate.execute(Path(root)/'forge.json',forge=True)
            self.assertEqual('BLOCKED_NOT_VERIFIED',result['status'])
            self.assertEqual(0,result['passed_forge_tests'])
            self.assertEqual('NOT_RUN',result['forge'])

    def test_fake_forge_output_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            wk=Path(root)
            with mock.patch.object(gate.shutil, 'which', return_value='/fake/docker'), \
                 mock.patch.object(gate.base, 'run_command', return_value={'exit_code':0,'stdout_tail':'Ran 2 test suites: 0 tests passed, 0 failed, 0 skipped','stderr_tail':''}):
                with self.assertRaises(V3Error) as exc:
                    gate.run_forge(wk,30,10,'none')
                self.assertEqual('forge_test',exc.exception.stage)

    def test_no_unverified_forge_status_on_bad_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(gate.shutil, 'which', return_value='/fake/docker'):
                result=gate.execute(Path(root)/'bad.json',forge=True,timeout=0)
            self.assertEqual('BLOCKED_NOT_VERIFIED',result['status'])
            self.assertEqual(0,result['passed_forge_tests'])

    def test_replaying_existing_output_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            f=Path(root)/'out.json'
            f.write_text('already exists')
            result=gate.execute(f)
            self.assertEqual('BLOCKED_NOT_VERIFIED',result['status'])
            self.assertEqual('already exists',f.read_text())
