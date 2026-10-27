"""Regression gates for the diagnostic MCEL promise-gap authoring benchmark.

These tests deliberately do not pretend to certify a completed application.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import mcel_promise_gap_frob as frob


def test_task_packet_is_pinned_and_contains_two_different_domains() -> None:
    packet = frob._task_manifest()
    assert len(packet['tasks']) == 2
    assert {t['id'] for t in packet['tasks']} == {'inventory-c2', 'work-orders-independent'}
    assert all(len(t['semanticRequirements']) >= 5 for t in packet['tasks'])
    assert frob._sha(frob.TASKS) == frob.TASK_PACKET_SHA256


def test_task_packet_refuses_substitution_and_unpinned_change(tmp_path: Path) -> None:
    altered = tmp_path / 'tasks.v1.json'
    altered.write_bytes(frob.TASKS.read_bytes())
    with pytest.raises(ValueError, match='changed or substituted'):
        frob._task_manifest(altered)
    # A changed packet at the same path must also fail the content pin.
    with pytest.MonkeyPatch.context() as monkey:
        monkey.setattr(frob, 'TASKS', altered)
        monkey.setattr(frob, 'FIXTURES', altered.parent)
        altered.write_text(altered.read_text(encoding='utf-8').replace('Inventory', 'Altered'), encoding='utf-8')
        with pytest.raises(ValueError, match='changed or substituted'):
            frob._task_manifest()


def test_public_compiler_probes_are_diagnostic_not_promotions() -> None:
    probes = [frob._compiler_probe(*item) for item in frob.PROBES]
    by_id = {p['id']: p for p in probes}
    assert by_id['host-bound-baseline']['status'] == 'PASS'
    assert by_id['invalid-orphan-action']['status'] == 'PASS'
    assert 'MCEL_DSL_ACTION_INTENT_REQUIRED' in by_id['invalid-orphan-action']['diagnosticCodes']
    assert by_id['invalid-list-key']['status'] == 'PASS'
    assert by_id['invalid-list-key']['expectedValid'] is False
    assert 'MCEL_DSL_LIST_KEY_INVALID' in by_id['invalid-list-key']['diagnosticCodes']
    for name in ('keyed-collection', 'record-schema', 'validated-form', 'keyed-surface'):
        assert by_id[name]['status'] in {'AVAILABLE_UNPROVEN', 'GAP_OBSERVED', 'BLOCKED'}
        assert by_id[name]['scope'].startswith('public mcel.dsl.v1')
        assert by_id[name]['sourceSha256']


def test_compiler_diagnostic_unknown_failure_is_not_called_missing_primitive() -> None:
    unknown = SimpleNamespace(
        valid=False, status='invalid-dsl', diagnostics=({'code': 'UNRELATED_ERROR', 'problem': 'no'},),
    )
    observation = frob._compiler_probe('keyed-collection', 'public_keyed_collection.js', True,
                                        'public-declarative-list-state', compiler=lambda *a, **kw: unknown)
    assert observation['status'] == 'BLOCKED'
    assert observation['diagnosticCodes'] == ['UNRELATED_ERROR']


def test_compiler_available_capability_is_not_application_proof() -> None:
    reference = frob._compiler_probe('keyed-collection', 'public_keyed_collection.js', True,
                                     'public-declarative-list-state')
    assert reference['status'] == 'AVAILABLE_UNPROVEN'
    from main_computer.mcel_dsl_compiler import compile_dsl_application
    real = compile_dsl_application(frob.FIXTURES / 'public_keyed_collection.js', write_candidate=False)
    positive = SimpleNamespace(valid=True, status='pass', diagnostics=(), normalized_ir=real.normalized_ir)
    observation = frob._compiler_probe('keyed-collection', 'public_keyed_collection.js', True,
                                        'public-declarative-list-state', compiler=lambda *a, **kw: positive)
    assert observation['status'] == 'AVAILABLE_UNPROVEN'


def test_cli_input_bindings_and_local_url_safety() -> None:
    ids = {'inventory-c2', 'work-orders-independent'}
    assert frob._option_map(['inventory-c2=/tmp/a.js'], ids) == {'inventory-c2': '/tmp/a.js'}
    for bad in ('inventory-c2=', 'missing=x', 'inventory-c2', 'inventory-c2=x',):
        if bad == 'inventory-c2=x':
            with pytest.raises(ValueError, match='repeated'):
                frob._option_map(['inventory-c2=a', bad], ids)
        else:
            with pytest.raises(ValueError):
                frob._option_map([bad], ids)
    assert frob._safe_local_url('http://127.0.0.1:8765/test')
    assert frob._safe_local_url('http://localhost:8765/test')
    for url in ('https://example.com', 'file:///etc/passwd', 'http://127.0.0.1.evil.com/',
                'http://user@127.0.0.1:8000/', 'javascript:alert(1)'):
        assert not frob._safe_local_url(url)


def test_no_browser_mode_outputs_incomplete_promise_and_no_false_proof(tmp_path: Path) -> None:
    result = frob.execute(output=tmp_path, browser=False)
    assert result['harnessHealth'] == 'PASS'
    assert result['status'] == 'INCOMPLETE'
    assert result['promiseFulfilled'] is False
    assert all(v == 'UNVERIFIED' for v in result['claims'].values())
    assert len(result['cases']) == 2
    for case in result['cases']:
        assert case['scaffold']['status'] == 'PASS'
        assert case['scaffold']['structuralOnly'] is True
        assert case['scaffold']['taskAppRendered'] is False
        assert case['candidate']['status'] == ('COMPILABLE_UNPROVEN' if case['id'] == 'inventory-c2' else 'UNVERIFIED')
        assert case['liveBrowser']['status'] == 'UNVERIFIED'
        assert case['independentEffects'] == 'UNVERIFIED'
        assert set(case['requiredBehavior'].values()) == {'UNVERIFIED'}
    assert result['releaseAuthority']['mutatesLiveApplication'] is False
    assert result['releaseAuthority']['changesWebsiteBuilder'] is False
    assert (tmp_path / 'report.json').exists()
    assert (tmp_path / 'barriers.md').exists()
    assert json.loads((tmp_path / 'report.json').read_text(encoding='utf-8'))['promiseFulfilled'] is False
    assert 'composition — UNVERIFIED' in (tmp_path / 'barriers.md').read_text(encoding='utf-8')


def test_candidate_compilation_does_not_count_as_task_behavior() -> None:
    task = frob._task_manifest()['tasks'][0]
    evidence = frob._candidate(frob.FIXTURES / 'public_host_smoke.js', task)
    assert evidence['status'] == 'FAIL'  # Valid DSL but wrong application identity.
    assert evidence['compilerValid'] is True
    assert evidence['appIdentityMatchesPacket'] is False
    assert evidence['taskRequirementsProven'] == []
    assert evidence['visibleApplicationProven'] is False


def test_barriers_explicitly_retain_unverified_checks() -> None:
    probes = [{'id': p[0], 'status': ('PASS' if not p[2] else 'AVAILABLE_UNPROVEN')} for p in frob.PROBES]
    cases = [{'id': 'inventory-c2'}, {'id': 'work-orders-independent'}]
    barriers = frob._barriers(probes, cases)
    assert barriers[0]['status'] == 'UNVERIFIED'
    assert {b['id'] for b in barriers} == {
        'composition', 'functional', 'effects', 'layout', 'aesthetics', 'flexibility', 'economy'
    }
    assert all(b['status'] == 'UNVERIFIED' for b in barriers)
    assert barriers[-1]['dependencies'] == ['functional', 'aesthetics', 'flexibility', 'effects']


def test_cli_defaults_to_diagnostic_success_and_strict_mode_refuses_greenwashing(tmp_path: Path) -> None:
    result = frob.main(['--output', str(tmp_path / 'ordinary'), '--no-browser'])
    strict = frob.main(['--output', str(tmp_path / 'strict'), '--no-browser', '--require-promise'])
    assert result == 0
    assert strict == 1
    saved = json.loads((tmp_path / 'strict' / 'report.json').read_text(encoding='utf-8'))
    assert saved['promiseFulfilled'] is False


def test_keyed_collection_declares_a_string_identity_and_negative_control_refuses_invalid_key() -> None:
    from main_computer.mcel_dsl_compiler import compile_dsl_application
    positive = compile_dsl_application(frob.FIXTURES / 'public_keyed_collection.js', write_candidate=False)
    assert positive.valid is True, positive.diagnostics
    assert positive.normalized_ir is not None
    items = next(item for item in positive.normalized_ir['states'] if item['id'] == 'state:items')
    schema = items['schema']
    assert schema['kind'] == 'list'
    assert schema['key'] == 'sku'
    assert schema['items']['kind'] == 'record'
    assert schema['items']['fields']['sku']['kind'] == 'string'
    negative = compile_dsl_application(frob.FIXTURES / 'public_invalid_list_key.js', write_candidate=False)
    assert negative.valid is False
    assert 'MCEL_DSL_LIST_KEY_INVALID' in {d.get('code') for d in negative.diagnostics}


def test_identity_refusal_failure_blocks_composition_promotion() -> None:
    probes = [{'id': p[0], 'status': ('FAIL' if p[0] == 'invalid-list-key' else 'AVAILABLE_UNPROVEN')}
              for p in frob.PROBES]
    barriers = frob._barriers(probes, [{'id': 'inventory-c2', 'compositionSlice': {'status': 'PASS'}}])
    composition = next(b for b in barriers if b['id'] == 'composition')
    assert composition['status'] == 'BLOCKED'
    assert 'invalid-list-key:identity-refusal-not-proven' in composition['evidence']


def test_compiler_success_without_exported_semantics_cannot_prove_public_primitive() -> None:
    # A constructed-but-unreturned handle must never be mistaken for usable IR.
    empty = SimpleNamespace(valid=True, status='pass', diagnostics=(), normalized_ir={
        'states': [], 'surfaces': [], 'intents': [],
    })
    for probe_id, fixture, label in (
        ('keyed-collection', 'public_keyed_collection.js', 'public-declarative-list-state'),
        ('record-schema', 'public_record.js', 'public-declarative-record-state'),
        ('validated-form', 'public_form.js', 'public-declarative-form'),
        ('keyed-surface', 'public_keyed_surface.js', 'public-declarative-collection-surface'),
    ):
        observation = frob._compiler_probe(probe_id, fixture, True, label, compiler=lambda *a, **kw: empty)
        assert observation['compilerStatus'] == 'pass'
        assert observation['status'] == 'BLOCKED'
        assert observation['semanticEvidencePresent'] is False


def test_exported_semantics_are_present_for_all_positive_public_contract_probes() -> None:
    for probe_id, fixture, expect_valid, label in frob.PROBES:
        observation = frob._compiler_probe(probe_id, fixture, expect_valid, label)
        if expect_valid and probe_id not in ('host-bound-baseline',):
            assert observation['status'] == 'AVAILABLE_UNPROVEN', observation
            assert observation['semanticEvidencePresent'] is True

