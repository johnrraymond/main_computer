"""Dual-path DSL operation contract and icon integration; no application edits."""
from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from main_computer.mcel_application_ir import validate_application_ir
from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'mcel_apps/task-manager/application.js'
ICON_CORE = ROOT / 'main_computer/web/applications/scripts/mcel-semantic-icon-core.js'
RESTART_REQUEST = 'operation:"restart",resultTo:operationResult'


def compiled_app(tmp_path: Path, replacement: str | None = None):
    source = APP.read_text(encoding='utf-8')
    assert source.count(RESTART_REQUEST) == 1
    if replacement is not None:
        source = source.replace(RESTART_REQUEST, replacement, 1)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / 'application.js'
    path.write_text(source, encoding='utf-8')
    return compile_dsl_application(path)


def request_intent(report):
    return next(item for item in report.normalized_ir['intents'] if item['id'] == 'intent:restart-server')


def test_legacy_strings_retained_bit_for_bit_in_compiled_operation_contract(tmp_path):
    before = compile_dsl_application(APP)
    copied = compiled_app(tmp_path)
    assert before.valid and copied.valid, (before.diagnostics, copied.diagnostics)
    assert before.semantic_fingerprint == copied.semantic_fingerprint
    assert 'typedOperation' not in request_intent(before)
    assert request_intent(before)['operation'] == 'restart'
    assert request_intent(before)['runtimeMethod'] == 'restartServer'


def test_typed_operation_retains_original_runtime_binding_and_enters_semantic_ir(tmp_path):
    baseline = compile_dsl_application(APP)
    typed = compiled_app(tmp_path, 'operation:{binding:"restart",kind:"stop",target:"server"},resultTo:operationResult')
    assert baseline.valid and typed.valid, typed.diagnostics
    old, new = request_intent(baseline), request_intent(typed)
    assert new['typedOperation'] == {'schema':'mcel.typed-operation.v1','kind':'stop','target':'server'}
    assert new['operation'] == old['operation'] == 'restart'
    assert new['runtimeMethod'] == old['runtimeMethod'] == 'restartServer'
    assert new['risk'] == old['risk']
    assert new['effectRefs'] == old['effectRefs']
    assert new['requestArguments'] == old['requestArguments']
    assert typed.semantic_fingerprint != baseline.semantic_fingerprint
    copy_ir = copy.deepcopy(typed.normalized_ir)
    assert validate_application_ir(copy_ir).valid


@pytest.mark.parametrize(('replacement','code'), [
    ('operation:{binding:"restart",kind:"invented",target:"server"},resultTo:operationResult', 'MCEL_DSL_TYPED_OPERATION_KIND_INVALID'),
    ('operation:{binding:"restart",kind:"stop",target:"imaginary"},resultTo:operationResult', 'MCEL_DSL_TYPED_OPERATION_TARGET_INVALID'),
    ('operation:{kind:"stop",target:"server"},resultTo:operationResult', 'MCEL_DSL_TYPED_OPERATION_FIELDS_INVALID'),
    ('operation:{binding:"restart",kind:"stop",target:"server",icon:"action.stop"},resultTo:operationResult', 'MCEL_DSL_TYPED_OPERATION_FIELDS_INVALID'),
    ('operation:{binding:"nonexistent",kind:"stop",target:"server"},resultTo:operationResult', 'MCEL_DSL_CAPABILITY_OPERATION_UNKNOWN'),
    ('operation:{binding:"restart",kind:"stop"},resultTo:operationResult', 'MCEL_DSL_TYPED_OPERATION_FIELDS_INVALID'),
    ('operation:{binding:"restart",kind:42,target:"server"},resultTo:operationResult', 'MCEL_DSL_TYPED_OPERATION_KIND_INVALID'),
])
def test_dsl_refuses_invalid_typed_operations(tmp_path, replacement, code):
    result = compiled_app(tmp_path, replacement)
    assert not result.valid
    assert code in {d['code'] for d in result.diagnostics}, result.diagnostics


def test_ir_rejects_forged_typed_semantics(tmp_path):
    report = compiled_app(tmp_path, 'operation:{binding:"restart",kind:"stop",target:"server"},resultTo:operationResult')
    assert report.valid, report.diagnostics
    for corrupt in (
        lambda i: i['typedOperation'].__setitem__('kind','made-up'),
        lambda i: i['typedOperation'].__setitem__('target','imaginary'),
        lambda i: i['typedOperation'].__setitem__('icon','action.restart'),
        lambda i: i['typedOperation'].__setitem__('schema','mcel.typed-operation.v0'),
        lambda i: i.__setitem__('operationKind','interaction'),
        lambda i: i.pop('operation'),
    ):
        ir = copy.deepcopy(report.normalized_ir)
        corrupt(next(i for i in ir['intents'] if i['id']=='intent:restart-server'))
        verdict = validate_application_ir(ir)
        assert not verdict.valid, corrupt


def test_icon_core_prefers_typed_identity_without_reading_binding_or_source_name(tmp_path):
    baseline = compile_dsl_application(APP)
    typed = compiled_app(tmp_path, 'operation:{binding:"restart",kind:"stop",target:"server"},resultTo:operationResult')
    assert baseline.valid and typed.valid
    old, new = request_intent(baseline), request_intent(typed)
    variants = {
        'legacy': old,
        'typed': new,
        'typed-renamed': {**new, 'sourceName':'unrelated-opaque-name'},
        'typed-unknown-glyph': {**new, 'typedOperation':{**new['typedOperation'],'kind':'kill'}},
    }
    script = """
      const core=require(process.argv[1]);
      const intents=JSON.parse(process.argv[2]);
      const icons={has:(id)=>['action.restart','action.stop'].includes(id)};
      process.stdout.write(JSON.stringify(Object.fromEntries(
        Object.entries(intents).map(([name,intent])=>[name,core.resolveIntent(intent,icons)]))));
    """
    completed = subprocess.run(['node','-e',script,str(ICON_CORE),json.dumps(variants)],
                               check=True,text=True,capture_output=True)
    result = json.loads(completed.stdout)
    assert result['legacy']['iconId'] == 'action.restart'
    assert result['typed']['iconId'] == 'action.stop'
    assert result['typed']['source'] == 'typed-operation'
    assert result['typed-renamed']['iconId'] == 'action.stop'
    assert result['typed-unknown-glyph']['status'] == 'unresolved'


def test_browser_integration_uses_typed_semantics_in_actual_p3_rendered_application(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from main_computer.mcel_applications_registry import attach_inert_presentation
    from main_computer.mcel_semantic_generated_host import render_p3_generated_page
    from main_computer.mcel_knowledge.semantic_composition import synthesize_composition, P3_POLICY

    result = compiled_app(tmp_path, 'operation:{binding:"restart",kind:"stop",target:"server"},resultTo:operationResult')
    assert result.valid, result.diagnostics
    ir = result.normalized_ir
    html = render_p3_generated_page(ROOT)
    encoded = json.dumps({'ir':ir,'composition':synthesize_composition(ir,policy=P3_POLICY)},
                         separators=(',',':'),ensure_ascii=False).replace('<','\\u003c')
    html, matches = re.subn(r'(<script type="application/json" id="mcel-generated-data">)(.*?)(</script>)',
                            lambda m:m.group(1)+encoded+m.group(3),html,count=1,flags=re.S)
    assert matches == 1
    html = attach_inert_presentation(html,ROOT,'task-manager','mcel-workbench',promoted=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        page = browser.new_page()
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        try:
            page.route('**/api/**',lambda route:route.fulfill(status=503,content_type='application/json',body='{"ok":false}'))
            page.set_content(html,wait_until='domcontentloaded',timeout=30000)
            observed=page.evaluate('''() => {
              const root=document.getElementById('mcel-workbench');
              const item=root.querySelector('[data-mcel-node$=".restart-server"]');
              return {icon:item?.querySelector('[data-mcel-icon-core="slot"]')?.getAttribute('data-mcel-icon-identity'),
                slotCount:item?.querySelectorAll('[data-mcel-icon-core="slot"]').length,
                steps:window.__mcelPresentationReport?.steps,
                mounted:!!window.__mcelP3,
                source:JSON.parse(document.getElementById('mcel-generated-data').textContent).ir.intents
                  .find(i=>i.id==='intent:restart-server')};
            }''')
            assert errors == [], errors
            assert observed['mounted']
            assert observed['steps'] == {'theme':'applied','form':'applied','chrome':'applied'}
            assert observed['icon'] == 'action.stop', observed
            assert observed['slotCount'] == 1
            assert observed['source']['operation'] == 'restart'
            assert observed['source']['runtimeMethod'] == 'restartServer'
        finally:
            browser.close()


def test_typed_apply_is_valid_and_maps_without_parsing_runtime_binding(tmp_path):
    """The new Apply glyph is usable through the typed DSL migration path."""
    typed=compiled_app(tmp_path,'operation:{binding:"restart",kind:"apply",target:"server"},resultTo:operationResult')
    assert typed.valid,typed.diagnostics
    intent=request_intent(typed)
    assert intent['typedOperation']['kind']=='apply'
    assert intent['operation']=='restart' and intent['runtimeMethod']=='restartServer'
    script='''const core=require(process.argv[1]);const intent=JSON.parse(process.argv[2]);
const resolved=core.resolveIntent(intent,{has:id=>id==="action.apply"||id==="action.restart"});
process.stdout.write(JSON.stringify(resolved));'''
    run=subprocess.run(['node','-e',script,str(ICON_CORE),json.dumps(intent)],
                       check=True,capture_output=True,text=True)
    resolved=json.loads(run.stdout)
    assert resolved['status']=='resolved' and resolved['iconId']=='action.apply'
    assert resolved['source']=='typed-operation'
