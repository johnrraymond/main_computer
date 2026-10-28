"""Hosted lifecycle acceptance diagnostics: preserve gates, explain failures.

No local or production server is started. The offline browser Frob independently
exercises the actual semantic renderer and bridge.
"""
from tools.mcel_task_manager_live_lifecycle_frob import hosted_gate_checks, hosted_failure_hints


def _good():
    first={'identical':True,'sourceCount':22,'renderedCount':22,
           'delivered':True,'build':'mcel-task-notebook-lifecycle-p10.v1'}
    second={'before':2,'after':3,'refreshError':None,'parity':True,
            'stillSameGeneration':True,'continuous':True,'focused':True,
            'inspectorPreserved':True,'report':{'ok':True}}
    return first,second


def test_complete_live_acceptance_gate():
    first,second=_good()
    assert all(hosted_gate_checks(200,first,second,[]).values())


def test_stale_host_build_is_separate_from_semantic_parity():
    first,second=_good()
    first['build']='mcel-task-notebook-lifecycle-p9.v1'
    failed=[k for k,v in hosted_gate_checks(200,first,second,[]).items() if not v]
    assert failed==['running-bridge-build']
    assert 'restart' in hosted_failure_hints(failed)[0]


def test_queued_refresh_without_receipt_is_not_certified():
    first,second=_good()
    second.update({'before':2,'after':2})
    failed=[k for k,v in hosted_gate_checks(200,first,second,[]).items() if not v]
    assert failed==['second-refresh-published']


def test_changed_generation_may_replace_node_without_false_failure():
    first,second=_good()
    second.update({'stillSameGeneration':False,'continuous':False,'focused':False,
                   'inspectorPreserved':False})
    assert all(hosted_gate_checks(200,first,second,[]).values())


def test_same_generation_must_preserve_identity_focus_and_inspector():
    first,second=_good()
    second.update({'continuous':False,'focused':False,'inspectorPreserved':False})
    failed=[k for k,v in hosted_gate_checks(200,first,second,[]).items() if not v]
    assert set(failed)=={'same-generation-dom-continuity',
                        'same-generation-focus-continuity',
                        'same-generation-inspection-continuity'}


def test_refused_receipt_and_pageerror_remain_hard_failures():
    first,second=_good()
    second['report']={'ok':False,'code':'renderer-update-refused'}
    failed=[k for k,v in hosted_gate_checks(200,first,second,['Error: failure']).items() if not v]
    assert failed==['second-publish-receipt','browser-page-errors']


def test_identity_mismatch_and_unauthorized_success_are_not_accepted():
    first,second=_good()
    first.update({'identical':True,'sourceCount':22,'renderedCount':21})
    second.update({'parity':False})
    failed=[k for k,v in hosted_gate_checks(200,first,second,[]).items() if not v]
    assert failed==['cold-source-identity-parity','second-source-identity-parity']


def test_cdp_non_error_exception_retains_origin():
    from tools.mcel_task_manager_live_lifecycle_frob import compact_cdp_exception
    raw={'exceptionDetails':{'text':'Uncaught','url':'http://example.test/scripts/init.js',
      'lineNumber':19,'columnNumber':3,
      'exception':{'type':'object','className':'Event','description':'Event'},
      'stackTrace':{'callFrames':[{'url':'http://example.test/scripts/init.js',
        'functionName':'startup','lineNumber':19,'columnNumber':3}]}}}
    result=compact_cdp_exception(raw)
    assert result['className']=='Event'
    assert result['line']==19
    assert result['frames'][0]['function']=='startup'
    assert result['frames'][0]['url'].endswith('/scripts/init.js')


def test_browser_error_evidence_distinguishes_uncaught_event_and_resource_error():
    from playwright.sync_api import sync_playwright
    from tools.mcel_task_manager_live_lifecycle_frob import (
      _HOST_EVENT_PROBE, hosted_error_evidence, hosted_gate_checks)
    import shutil
    with sync_playwright() as p:
      exe=shutil.which('chromium') or shutil.which('chromium-browser')
      b=p.chromium.launch(headless=True,executable_path=exe,args=['--no-sandbox'] if exe else [])
      try:
        page=b.new_page()
        errors=[]
        page.add_init_script(script=_HOST_EVENT_PROBE)
        page.on('pageerror',lambda err:errors.append(str(err)))
        # The restricted offline browser cannot navigate to data: URLs.
        # Install the same probe explicitly for the local synthetic document.
        page.set_content('<html><body>test</body></html>')
        page.evaluate(_HOST_EVENT_PROBE)
        page.evaluate('''() => {setTimeout(() => {throw new Event('probe-event')}, 0)}''')
        page.wait_for_timeout(200)
        evidence=hosted_error_evidence(page)
        assert errors and 'Event' in errors[0]
        assert any(event['kind']=='uncaught-error' and
            event['exceptionEventType']=='probe-event' for event in evidence['captured'])
        # This is an actual uncaught exception; the acceptance gate must fail.
        assert hosted_gate_checks(200,{}, {}, errors)['browser-page-errors'] is False
      finally:b.close()
