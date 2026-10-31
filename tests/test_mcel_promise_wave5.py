"""Patch 05 independent app source-bound, shared-shell, and rollback contracts."""
from __future__ import annotations
import copy
from pathlib import Path
import pytest
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_semantic_application_shell import validate_semantic_application_shell
from tools.mcel_file_explorer_candidate_bundle import SOURCE,TARGET,build

ROOT=Path(__file__).resolve().parents[1]

def _ir():
    result=compile_dsl_application(SOURCE,write_candidate=False)
    assert result.valid, result.diagnostics
    return result.normalized_ir

def test_new_file_explorer_candidate_is_source_bound():
    expected,meta=build()
    assert TARGET.read_text(encoding='utf-8')==expected
    assert meta['source']=='mcel_experiments/file-explorer-candidate/application.js'
    assert meta['status']=='opt-in-read-only-candidate'

def test_second_app_uses_public_ir_and_shared_shell_without_primary_notebook():
    ir=_ir()
    assert ir['application']['appId']=='file-explorer-candidate'
    spec=validate_semantic_application_shell(ir)
    assert not any(r['kind']=='slot' for r in spec['regions'])
    assert any(r['kind']=='collection' and r['zone']=='main' and r.get('selectControl') for r in spec['regions'])
    assert len(ir['effects'])==len(ir['capabilities'])==6
    assert all(effect['risk']=='external-read' for effect in ir['effects'])

def test_unknown_selectable_collection_capability_rejected():
    ir=copy.deepcopy(_ir())
    spec=next(s['semanticSurface']['applicationShell'] for s in ir['surfaces'])
    target=next(r for r in spec['regions'] if r.get('selectControl'))
    target['selectControl']='undeclared-action'
    with pytest.raises(ValueError,match='MCEL_SHELL_INVALID_COLLECTION'):
        validate_semantic_application_shell(ir)

def test_file_explorer_cannot_implicitly_admit_delete():
    ir=_ir()
    spec=validate_semantic_application_shell(ir)
    delete=next(a for r in spec['regions'] for a in r.get('actions',[]) if a['id']=='delete')
    assert delete['authority']=='not-admitted' and delete['intent'] is None
    assert not any('delete' in c['sourceName'].lower() for c in ir['capabilities'])

def test_candidate_is_explicitly_opt_in_and_original_is_preserved():
    app=(ROOT/'main_computer/web/applications/scripts/file-explorer.js').read_text(encoding='utf-8')
    bridge=(ROOT/'main_computer/web/applications/scripts/mcel-file-explorer-candidate-bridge.js').read_text(encoding='utf-8')
    html=(ROOT/'main_computer/web/applications.html').read_text(encoding='utf-8')
    assert "mode === 'candidate'" in app
    assert 'McelFileExplorerCandidate.destroy()' in app
    assert "legacy.setAttribute('hidden','')" in bridge
    assert "s.legacy.removeAttribute('hidden')" in bridge
    assert html.index('applications/scripts/file-explorer-semantic-adapter.js') < html.index('applications/scripts/mcel-file-explorer-candidate-bridge.js')
