"""Source-bound deterministic template binding in MCEL's main semantic route."""
from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import patch

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_knowledge.semantic_template_pipeline import (
    plan_semantic_workspace, verify_semantic_workspace_plan,
)
from main_computer.mcel_knowledge.workspace_plan import plan_workspace
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT = Path(__file__).resolve().parents[1]


def code_ir():
    return compile_semantic_app('code-editor', ROOT)


def test_normal_semantic_pipeline_selects_and_binds_all_arguments():
    ir = code_ir()
    base = plan_workspace(ir)
    with patch('main_computer.mcel_knowledge.spectral_code_override.spectral_loading',
               side_effect=AssertionError('spectral runtime gate forbidden')):
        plan = plan_semantic_workspace(ir, ROOT)
    verify_semantic_workspace_plan(ir, plan, ROOT)
    assert base['sourceIrHash'] == plan['sourceIrHash']
    assert plan['fingerprint'] != base['fingerprint']
    template = plan['semanticTemplate']
    assert template['profile'] == 'code'
    assert template['authority'] == 'canonical-ir-and-deterministic-graph-match'
    assert template['argumentHash'].startswith('sha256:')
    args = template['arguments']
    assert args['grouping'] == 'inferred'
    assert {k: len(v) for k, v in args['groups'].items()} == {
        'execution': 2, 'preview': 2, 'session': 7,
    }
    assert args['docks'] == plan['documentWorkbench']
    assert len(ir['intents']) == 39 and len(ir['capabilities']) == 13 and len(ir['effects']) == 36


def test_other_applications_keep_identical_plans_without_any_template():
    for name in ('file-manager', 'task-manager'):
        ir = compile_semantic_app(name, ROOT)
        plan = plan_semantic_workspace(ir, ROOT)
        assert plan == plan_workspace(ir)
        verify_semantic_workspace_plan(ir, plan, ROOT)
        assert 'semanticTemplate' not in plan


def test_template_verifier_refuses_tampering_and_stale_ir():
    ir = code_ir()
    plan = plan_semantic_workspace(ir, ROOT)
    for mutation in ('group', 'source', 'fingerprint', 'arguments'):
        tampered = copy.deepcopy(plan)
        if mutation == 'group':
            tampered['semanticTemplate']['arguments']['groups']['session'].append(
                tampered['semanticTemplate']['arguments']['groups']['execution'][0])
        elif mutation == 'source':
            tampered['semanticTemplate']['sourceIrHash'] = 'sha256:' + '0'*64
        elif mutation == 'fingerprint':
            tampered['fingerprint'] = 'sha256:' + 'f'*64
        else:
            tampered['semanticTemplate']['argumentHash'] = 'sha256:' + 'e'*64
        with pytest.raises(ValueError, match='disagrees'):
            verify_semantic_workspace_plan(ir, tampered, ROOT)
    mutated = copy.deepcopy(ir)
    mutated['intents'][0]['risk'] = 'source-write'
    with pytest.raises(ValueError):
        plan_semantic_workspace(mutated, ROOT)


def test_no_override_policy_is_needed_and_no_file_access(tmp_path):
    ir=code_ir()
    # The repo path is intentionally empty: no override can be consulted.
    assert plan_semantic_workspace(ir,tmp_path)['semanticTemplate']['profile']=='code'


def test_application_policy_cannot_influence_template(tmp_path):
    ir=code_ir()
    directory=tmp_path/'mcel_apps'/'code-editor'
    directory.mkdir(parents=True)
    (directory/'code-override.json').write_text('{"profile":"other"}')
    assert plan_semantic_workspace(ir,tmp_path)==plan_semantic_workspace(ir,ROOT)


def test_live_render_uses_template_in_normal_route_not_post_mount_script():
    html = render_semantic_page('code-editor', ROOT, promoted=True)
    assert 'mcel.semantic-template-binding.v1' in html
    assert 'mcel-semantic-operational-runtime.js' not in html or 'McelSemanticOperationalRuntime' in html
    assert 'McelSemanticCodeOverride=Object.freeze' not in html
    with sync_playwright() as playwright:
        browser, _ = launch_semantic_browser(playwright)
        try:
            for width in (390, 760, 1100, 1366):
                page = browser.new_page(viewport={'width': width, 'height': 860})
                errors = []
                page.on('pageerror', lambda err: errors.append(str(err)))
                page.set_content(html)
                assert page.locator('#mcel-workbench-status').inner_text() == 'Semantic application ready'
                assert page.locator('[data-mcel-semantic-template-group="execution"] [data-intent="intent:run-aider"]').count() == 1
                assert page.locator('[data-mcel-semantic-template-group="preview"] [data-intent="intent:preview-aider-plan"]').count() == 1
                assert page.locator('[data-mcel-semantic-template-group="session"] [data-intent="intent:reset-aider-context"]').count() == 1
                assert page.locator('[data-mcel-semantic-template-group]').count() == 3
                assert page.locator('[data-mcel-region$=".aider"] [data-mcel-node]').count() == 11
                assert page.locator('[data-mcel-document-workbench]').count() == 1
                if width == 1366:
                    measured = page.evaluate('''() => {
                      const host=document.querySelector('[data-mcel-document-companion]');
                      const panel=host.querySelector('[data-mcel-document-tabpanel="companion"]:not([hidden])');
                      const groups=panel.querySelectorAll('[data-mcel-semantic-template-group]');
                      return {hostWidth:host.getBoundingClientRect().width,
                        groupCount:groups.length,
                        groupScrollOwners:[...groups].map(el=>getComputedStyle(el).overflowY),
                        panelScroll:getComputedStyle(panel).overflowY};
                    }''')
                    assert measured['hostWidth'] > 50, measured
                    assert measured['groupCount'] == 3, measured
                    assert measured['panelScroll'] in ('auto', 'scroll'), measured
                    assert all(x not in ('auto', 'scroll') for x in measured['groupScrollOwners']), measured
                    chrome = page.evaluate('''() => ({
                      aide:[...document.querySelectorAll('[data-mcel-region$=".aider"] button')]
                        .map(x=>x.getAttribute('data-mcel-chrome-material')),
                      editor:[...document.querySelectorAll('[data-mcel-document-editor] button')]
                        .filter(x=>['Save source','Discard draft','Close file'].includes(x.textContent.trim()))
                        .map(x=>({name:x.textContent.trim(),material:x.getAttribute('data-mcel-chrome-material')}))
                    })''')
                    assert chrome['aide'] and all(x in ('raised-control','emissive-control') for x in chrome['aide']), chrome
                    assert {item['name'] for item in chrome['editor']} == {'Save source','Discard draft','Close file'}, chrome
                    assert all(item['material'] == 'raised-control' for item in chrome['editor']), chrome
                assert not errors, errors
                page.close()
        finally:
            browser.close()


def test_read_only_main_path_smoke():
    from tools.mcel_semantic_layout_pipeline_smoke import run
    result = run(ROOT)
    assert result['status'] == 'PASS_DETERMINISTIC_MAIN_PATH'
    assert result['arguments'] == 11
    assert result['otherApplicationPlansUnchanged'] == ['file-manager','task-manager']
    assert result['authorizesEffects'] is False
