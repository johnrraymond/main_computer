"""Patch 03 source-bound and authority-negative contract regressions."""
from __future__ import annotations
import copy
import pytest
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_semantic_application_shell import validate_semantic_application_shell
from tools.mcel_task_notebook_candidate_bundle import SOURCE, build, TARGET


def _ir():
    report=compile_dsl_application(SOURCE, write_candidate=False)
    assert report.valid
    return report.normalized_ir


def test_source_generated_artifact_is_exact():
    artifact,meta=build()
    assert TARGET.read_text(encoding='utf-8')==artifact
    assert meta['status']=='new-candidate-not-historical-recovery'


def test_semantic_application_layout_compiles_and_reuses_public_ir():
    ir=_ir()
    shell=validate_semantic_application_shell(ir)
    assert shell['schema']=='mcel.semantic-application-shell.v1'
    assert len(shell['regions'])==7
    assert shell['layout']=='sidebar-workspace'


def test_undeclared_operation_rejected():
    ir=copy.deepcopy(_ir())
    action=ir['surfaces'][0]['semanticSurface']['applicationShell']['regions'][1]['actions'][0]
    action['control']='unauthorized-operation'
    with pytest.raises(ValueError,match='UNDECLARED_OPERATION'):
        validate_semantic_application_shell(ir)


def test_machine_mutation_cannot_be_renamed_to_safe_read():
    ir=copy.deepcopy(_ir())
    action=ir['surfaces'][0]['semanticSurface']['applicationShell']['regions'][2]['actions'][0]
    action['authority']='external-read'
    action['intent']='intent:task-manager-notebook.refresh-process-data'
    with pytest.raises(ValueError,match='UNDECLARED_OPERATION'):
        validate_semantic_application_shell(ir)


def test_unsafe_binding_path_refused():
    ir=copy.deepcopy(_ir())
    ir['surfaces'][0]['semanticSurface']['applicationShell']['regions'][0]['items'][0]['path']='constructor.prototype'
    with pytest.raises(ValueError,match='INVALID_BINDING'):
        validate_semantic_application_shell(ir)


def test_duplicate_slot_refused():
    ir=copy.deepcopy(_ir())
    shell=ir['surfaces'][0]['semanticSurface']['applicationShell']
    shell['regions'].append(copy.deepcopy(shell['regions'][4]))
    shell['regions'][-1]['id']='region:more'
    with pytest.raises(ValueError,match='INVALID_SLOT'):
        validate_semantic_application_shell(ir)


def test_hosted_source_process_parity_allows_verified_empty_snapshot():
    from tools.mcel_promise_wave3_frob import source_process_parity
    assert source_process_parity(0, 0, 'No data for this view')
    assert source_process_parity(1, 1, '')
    assert source_process_parity(500, 500, '')
    assert source_process_parity(501, 500, '')


def test_hosted_source_process_parity_rejects_missing_or_lost_data():
    from tools.mcel_promise_wave3_frob import source_process_parity
    assert not source_process_parity(None, 0, 'No data for this view')
    assert not source_process_parity(0, 0, '')
    assert not source_process_parity(1, 0, 'No data for this view')
    assert not source_process_parity(0, 1, '')
    assert not source_process_parity(501, 499, '')
