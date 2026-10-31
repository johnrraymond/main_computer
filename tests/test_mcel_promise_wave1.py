"""Source-controlled Task Manager notebook candidate (not historical Phase 3 recovery)."""
from pathlib import Path
import json

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_notebook_projection import compile_notebook_preview
from tools.mcel_task_notebook_candidate_bundle import SOURCE, TARGET, build

ROOT=Path(__file__).resolve().parents[1]

def test_public_source_compiles_with_bounded_external_read_authority():
    report=compile_dsl_application(SOURCE)
    assert report.valid,report.diagnostics
    ir=report.normalized_ir
    assert ir['application']['appId']=='task-manager-notebook'
    assert len(ir['effects']) == len(ir['capabilities']) == 1
    assert ir['effects'][0]['risk'] == ir['capabilities'][0]['risk'] == 'external-read'
    assert len(ir['intents'])==2
    local=next(i for i in ir['intents'] if i['lane']=='local-view-state')
    read=next(i for i in ir['intents'] if i['lane']=='external-read')
    assert not local['writes'] and not local['effectRefs']
    assert not read['writes'] and len(read['effectRefs'])==1
    assert [t['id'] for t in ir['surfaces'][0]['semanticSurface']['tabGroup']['tabs']]==[
      'server-processes','all-processes','connections','hardware']

def test_bundled_browser_ir_is_exact_compiler_output_and_labeled():
    expected,meta=build()
    assert TARGET.read_text(encoding='utf-8')==expected
    assert meta['status']=='new-candidate-not-historical-recovery'
    assert 'sourceSha256' in expected

def test_offline_preview_generated_from_authored_semantics():
    fixture=SOURCE.parent/'read-only-sample.json'
    assert 'DETERMINISTIC SYNTHETIC' in fixture.read_text(encoding='utf-8')
    output=compile_notebook_preview(SOURCE,fixture_path=fixture)
    assert 'task-manager-notebook' in output
    assert 'mcel-tab-workspace' in output
    assert 'McelNotebookPreview' in output

def test_host_route_is_explicit_candidate_and_restores_legacy():
    bridge=(ROOT/'main_computer/web/applications/scripts/mcel-task-manager-notebook-bridge.js').read_text()
    assert 'new-candidate-not-historical-recovery' in bridge
    assert 'root.setAttribute("data-mcel-notebook-mode", requestedMode)' in bridge
    assert 'legacy.setAttribute("inert", "")' in bridge
    assert 'state.legacy.removeAttribute("inert")' in bridge
    assert 'state.workspace.destroy()' in bridge
    assert 'mcel_task_notebook' in (ROOT/'main_computer/web/applications/scripts/app-routing.js').read_text()
