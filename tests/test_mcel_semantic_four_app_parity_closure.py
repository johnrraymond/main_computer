"""Verified bounded parity gaps; never a claim of production promotion."""
from __future__ import annotations

from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from test_mcel_p3_operational import browser, isolated_task_http, _task_http_transport_bridge
from test_mcel_semantic_task_manager_convergence import _page


def test_task_manager_auto_refresh_is_present_for_paired_monitor(browser, isolated_task_http):
    page = _page(browser, isolated_task_http)
    try:
        assert page.locator('[data-mcel-auto-refresh] [data-mcel-refresh-toggle]').count() == 1
    finally:
        page.close()


def test_paired_task_monitor_polling_follows_visible_view_without_effects(browser, isolated_task_http):
    """Verify actual running callback and request shape, not just a control's DOM."""
    lab = isolated_task_http
    page = browser.new_page(viewport={'width': 1100, 'height': 850})
    try:
        page.expose_binding('__taskContractHttp',
                            lambda _source, uri, opts: _task_http_transport_bridge(lab, uri, opts))
        page.evaluate('''() => {
          window.__pollCallbacks=[];
          window.setInterval=(cb,delay)=>{window.__pollCallbacks.push({cb,delay});return 17;};
          window.clearInterval=()=>{};
          window.fetch=async(uri,opts={})=>{
            const r=await window.__taskContractHttp(String(uri),opts);
            return {ok:r.ok,status:r.status,json:async()=>r.payload};
          };
        }''')
        page.set_content(render_semantic_page('task-manager'))
        assert page.locator('#mcel-workbench-status').inner_text() == 'Semantic candidate ready'
        assert page.evaluate('window.__pollCallbacks.length') == 1
        assert page.evaluate('window.__pollCallbacks[0].delay') == 5000

        # A selected monitoring perspective owns polling, not schedules or AI.
        for mode in ('server-processes','all-processes','connections','cpu-gpu'):
            if mode != 'server-processes':
                page.locator('[data-mcel-mode-tabs] button[data-value="'+mode+'"]').click()
            previous = len([1 for path, _ in lab.requests if path.endswith('/task/overview')])
            page.evaluate('async()=>await window.__pollCallbacks[0].cb()')
            overview = [body for path, body in lab.requests if path.endswith('/task/overview')]
            assert len(overview) == previous+1, mode
            body = overview[-1]
            assert body.get('include_all', False) is (mode in ('all-processes', 'connections'))
            assert body.get('limit') == 100 and body.get('include_connections') is True
        assert lab.effects == []
        page.locator('[data-mcel-refresh-toggle]').click()
        before = len(lab.requests)
        page.evaluate('async()=>await window.__pollCallbacks[0].cb()')
        assert len(lab.requests) == before, 'pausing auto-refresh must stop requests'
    finally:
        page.close()


def test_four_app_audit_uses_actual_production_baselines_and_no_false_promotion():
    from pathlib import Path
    from tools.mcel_semantic_reference_parity_frob import run

    repo = Path(__file__).resolve().parents[1]
    result = run(repo)
    assert result['schema'] == 'mcel.semantic-four-app-parity-evidence.v3'
    assert result['source_audit_pass'] is True
    assert result['full_parity_pass'] is False
    assert result['promotion'].startswith('BLOCKED')
    assert set(result['applications']) == {'file-manager', 'task-manager', 'code-editor', 'calculator'}
    for name, app in result['applications'].items():
        assert app['parity'] == 'INCOMPLETE_LIVE_EVIDENCE', name
        assert app['certification'] == 'NOT_AUTHORIZED', name
        assert not app['missing_source_contracts'], name
        assert all(app['production_sources'][path] == (repo/path).is_file()
                   for path in app['production_sources'])
    assert result['applications']['file-manager']['candidate_effect_risks'] == ['external-read']
    assert result['applications']['task-manager']['candidate_effect_risks'] == ['consequential','external-read']
    assert result['applications']['code-editor']['candidate_effect_risks'] == ['consequential','external-read','source-write']
    assert result['applications']['calculator']['candidate_effect_risks'] == ['external-read']


def test_production_code_editor_extra_workflows_remain_explicit_parity_questions():
    from tools.mcel_semantic_reference_parity_frob import run

    report = run()['applications']['code-editor']
    extra = report['additional_production_workflows_not_yet_reconciled']
    assert {'MCEL Studio validate/serialize','MCEL Studio runtime workspace lifecycle',
            'SCM evidence and repair tools','Documentation viewport tools'} <= set(extra)
    assert all(value['visible_in_production_html'] for value in extra.values())
    assert extra['MCEL Studio validate/serialize']['qualification'] == 'BOUNDED_DOCUMENT_SCM_KERNEL_NOT_FULL_WORKBENCH'
    assert extra['SCM evidence and repair tools']['qualification'] == 'BOUNDED_KERNEL_EVIDENCE_REPLAY_NOT_FULL_STUDIO_PROOF_DOCK'
    assert extra['Documentation viewport tools']['qualification'] == 'BOUNDED_OPAQUE_ORIGIN_PIXEL_SCRIPT_NOT_FULL_VIEWPORT_PARITY'
    assert extra['MCEL Studio runtime workspace lifecycle']['qualification'] == 'BOUNDED_DOCUMENT_ROUTE_AND_DRAFT_NO_HOST_COMMIT'
    # None of these bounded source bridges certifies the incumbent's full
    # runtime, replay, viewport scripting, or production-feature parity.
    assert report['parity'] == 'INCOMPLETE_LIVE_EVIDENCE'
    assert report['certification'] == 'NOT_AUTHORIZED' 
