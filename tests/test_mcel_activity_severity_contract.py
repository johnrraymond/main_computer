"""MCEL Activity severity is app-authored; legacy receipts remain supported.

All operations use deterministic, local browser stubs. No SLH, SEH, network,
LMS or Coolify mutation is performed by these tests.
"""
from __future__ import annotations

import shutil
from itertools import product

from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import compile_slh_application
from main_computer.mcel_semantic_generated_host import render_generated_page
from tests.test_mcel_seh_two_action_lifecycle_ui import _setup


def _browser(p):
    return p.chromium.launch(headless=True,
        executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])


def test_slh_alone_owns_activity_dispositions_and_excludes_observations():
    ir, _ = compile_slh_application()
    activity, = [n for n in ir['surfaces'][0]['nodes'] if n['nodeKind']=='operation-evidence']
    entries = activity['activityEntries']
    assert entries and {item['severity'] for item in entries} == {'info','warn','error'}
    declared = {item['intent']['ref'] for item in entries}
    assert 'intent:seh-deployment-deploy' in declared
    assert 'intent:organization-remove' in declared
    assert not any(name.startswith(('intent:read-', 'intent:refresh-')) for name in declared)
    assert all('dispositions' in e and e['message'] for e in entries)
    # A deferred acceptance is not a verified completion.
    deferred = [item for item in entries if item['intent']['ref']=='intent:seh-deployment-deploy'
                and 'accepted-deferred' in item['dispositions']]
    assert len(deferred)==1 and 'pending' in deferred[0]['message']


def test_app_authored_activity_is_newest_first_unlimited_and_filterable():
    with sync_playwright() as p:
        browser = _browser(p)
        try:
            page, _ = _setup(browser,'CONNECTED_VERIFIED')
            errors=[]
            page.on('pageerror', lambda error: errors.append(str(error)))
            # Mount the actual SLH IR through MCEL with a separate deterministic
            # capability port so the three outcome classes can be tested.
            page.evaluate('''() => {
              const {ir,composition}=JSON.parse(document.getElementById('mcel-generated-data').textContent);
              const probe=document.createElement('main');probe.id='activity-probe';document.body.append(probe);
              window.activityProbeMode='info';
              window.activityProbe=McelSemanticOperationalRuntime.mount(probe,ir,{
                composition,presentation:'expert',capabilities:{
                  authorize:async req=>window.activityProbeMode!=='warn',
                  invoke:async req=>{
                    if(window.activityProbeMode==='error')throw Error('TEST_FAILURE');
                    if(req.intentId==='intent:seh-deployment-deploy')return {
                      outcome:'accepted-deferred',message:'queued',operationId:'test',applicationUuid:'test-uuid'};
                    if(req.intentId==='intent:read-seh-deployment')return {
                      generation:33,selectedController:'coolify-c',applicationUuid:'test-uuid',
                      deploymentStatus:'QUEUED',controllers:[{identity:'coolify-c'}]};
                    throw Error('UNEXPECTED_OPERATION');
                  }
                }
              });
            }''')
            box=page.locator('#activity-probe [data-mcel-activity]')
            assert box.locator('li').count()==0
            assert box.locator('details').count()==0  # the Activity is itself a details element
            assert box.evaluate('node=>node.open') is False
            # Reads still generate execution receipts, but the app did not
            # authorize these events for its operator Activity surface.
            read=page.evaluate("async()=>await activityProbe.execute('intent:read-seh-deployment')")
            assert read['disposition']=='completed'
            assert box.locator('li').count()==0
            # More than six entries are retained. The first is the latest, not
            # the earliest, even when the history keeps growing.
            for i in range(12):
                r=page.evaluate("async()=>await activityProbe.execute('intent:seh-deployment-deploy')")
                assert r['disposition']=='accepted-deferred'
            assert box.locator('li').count()==12
            sequences=box.locator('li').evaluate_all('rows=>rows.map(r=>r.getAttribute("data-operation-intent"))')
            assert set(sequences)=={'intent:seh-deployment-deploy'}
            latest=box.locator('li').first
            assert latest.get_attribute('data-activity-severity')=='info'
            assert 'verification pending' in latest.text_content()
            assert page.evaluate("activityProbe.receipts().length")>=13
            # Cause two operator-authored severities by changing the *port*;
            # MCEL must not infer the policy from SLH or the risk class.
            page.evaluate("window.activityProbeMode='warn'")
            warn=page.evaluate("async()=>await activityProbe.execute('intent:seh-deployment-deploy')")
            assert warn['disposition']=='refused-before-attempt'
            page.evaluate("window.activityProbeMode='error'")
            failed=page.evaluate("async()=>await activityProbe.execute('intent:seh-deployment-deploy')")
            assert failed['disposition']=='failed'
            assert box.locator('li').count()==14
            assert box.locator('li').first.get_attribute('data-activity-severity')=='error'
            assert 'TEST_FAILURE' in box.locator('li').first.text_content()
            assert box.locator('li').nth(1).get_attribute('data-activity-severity')=='warn'
            # The user, and only the user, opens/closes Activity.
            assert box.evaluate('node=>node.open') is False
            box.locator('summary').click()
            assert box.evaluate('node=>node.open') is True
            toggles=box.locator('button[data-mcel-activity-level-toggle]')
            assert toggles.all_text_contents()==['Info','Warn','Error']
            assert toggles.evaluate_all('bs=>bs.map(b=>b.getAttribute("aria-pressed"))')==[
                'true','true','true']
            history=box.locator('ol[data-mcel-activity-history]')
            # The first *visible* entry is numbered 1, including after filtering.
            assert history.evaluate('el=>getComputedStyle(el).listStyleType')=='decimal'
            assert history.locator('li').first.evaluate('el=>getComputedStyle(el).display')=='list-item'
            assert history.get_attribute('start') is None
            # Toggle off just Warn: Info and Error remain. Each toggle is
            # independent; the old cumulative three-mode select is gone.
            box.locator('[data-mcel-activity-level-toggle="warn"]').click()
            assert history.locator('li').count()==13
            assert history.locator('li').first.get_attribute('data-activity-severity')=='error'
            assert toggles.evaluate_all('bs=>bs.map(b=>b.getAttribute("aria-pressed"))')==[
                'true','false','true']
            # All eight on/off combinations, including none selected.
            for info,warn,error in product([False,True],repeat=3):
                enabled={'info':info,'warn':warn,'error':error}
                for level,want in enabled.items():
                    button=box.locator(f'[data-mcel-activity-level-toggle="{level}"]')
                    if (button.get_attribute('aria-pressed')=='true')!=want:
                        button.click()
                assert history.locator('li').count()==12*info+warn+error,enabled
                assert set(history.locator('li').evaluate_all(
                    'rows=>rows.map(r=>r.dataset.activitySeverity)'))=={
                        k for k,v in enabled.items() if v and (k=='info' or k in ('warn','error'))
                    },enabled
                assert history.evaluate('el=>getComputedStyle(el).listStyleType')=='decimal'
            # Persist the choice while a new event arrives and do not reopen
            # Activity or discard entries that are currently hidden.
            for level in ('info','warn'):
                button=box.locator(f'[data-mcel-activity-level-toggle="{level}"]')
                if button.get_attribute('aria-pressed')=='true':button.click()
            error_button=box.locator('[data-mcel-activity-level-toggle="error"]')
            if error_button.get_attribute('aria-pressed')=='false':error_button.click()
            page.evaluate("window.activityProbeMode='info'")
            r=page.evaluate("async()=>await activityProbe.execute('intent:seh-deployment-deploy')")
            assert r['disposition']=='accepted-deferred'
            assert toggles.evaluate_all('bs=>bs.map(b=>b.getAttribute("aria-pressed"))')==[
                'false','false','true']
            assert history.locator('li').count()==1
            assert box.evaluate('node=>node.open') is True
            # Restoring every level reveals all fifteen, latest at position 1.
            for level in ('info','warn'):
                box.locator(f'[data-mcel-activity-level-toggle="{level}"]').click()
            assert history.locator('li').count()==15
            assert history.locator('li').first.get_attribute('data-activity-severity')=='info'
            assert history.locator('li').nth(1).get_attribute('data-activity-severity')=='error'
            assert history.evaluate('el=>getComputedStyle(el).listStyleType')=='decimal'
            # Scrolling has a bounded viewport, not a bounded history.
            assert history.evaluate('el=>getComputedStyle(el).overflowY')=='auto'
            assert not errors,errors
        finally:
            browser.close()


def test_legacy_activity_receipt_call_keeps_unlimited_info_history():
    with sync_playwright() as p:
        browser=_browser(p)
        try:
            page=browser.new_page()
            page.evaluate("""() => {window.fetch=async()=>({ok:false,status:503,json:async()=>({})});}""")
            page.set_content(render_generated_page())
            activity=page.locator('[data-mcel-activity]')
            for i in range(9):
                result=page.evaluate("async()=>await window.__mcelP1.app.execute('intent:refresh',{query:''})")
                # This app's explicit result is controlled by an unavailable endpoint.
                assert result.get('disposition') in ('refused-before-attempt','failed','completed'),result
            assert activity.locator('li').count()>=9
            assert all(x=='info' for x in activity.locator('li').evaluate_all(
                'nodes=>nodes.map(node=>node.dataset.activitySeverity)'))
        finally:
            browser.close()
