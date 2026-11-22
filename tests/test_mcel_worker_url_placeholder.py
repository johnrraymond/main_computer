"""Worker SLH endpoint URL hint: semantic, non-persistent, and non-authoritative."""
from pathlib import Path
import shutil

import pytest

from main_computer.mcel_worker_application import compile_worker_application, render_worker_page
from tests.test_mcel_worker_hub_navigation import MOCK_LOCAL_BACKEND
from tests.test_mcel_worker_identity_navigation import IDENTITIES_MOCK

ROOT=Path(__file__).resolve().parents[1]
HINT='https://localhost:8898'


def test_endpoint_url_hint_declared_in_worker_semantics():
    ir,_=compile_worker_application(ROOT)
    form=next(n for n in ir['surfaces'][0]['nodes']
              if n['id']=='surface-node:worker.configure-slh-target')
    assert form['placeholders']=={'hubUrl':HINT}
    assert form['defaults']=={'hubUrl':''}
    assert form['fields']['hubUrl']['kind']=='string'


@pytest.mark.parametrize('configured', [False,True])
def test_endpoint_url_placeholder_never_becomes_saved_value(configured):
    pw=pytest.importorskip('playwright.sync_api')
    mock=MOCK_LOCAL_BACKEND
    if not configured:
        mock=mock.replace("hubUrl:'https://configured.example:8899'", "hubUrl:''")
    with pw.sync_playwright() as driver:
        chromium=shutil.which('chromium') or shutil.which('chromium-browser')
        try:
            browser=driver.chromium.launch(headless=True, **({'executable_path':chromium} if chromium else {}))
        except Exception as e:
            pytest.skip(f'Chromium unavailable: {e}')
        try:
            page=browser.new_page(viewport={'width':740,'height':440})
            errors=[]
            page.on('pageerror',lambda e: errors.append(str(e)))
            page.evaluate(mock)
            page.evaluate(IDENTITIES_MOCK)
            page.set_content(render_worker_page(ROOT),wait_until='domcontentloaded')
            page.wait_for_function("() => window.__mcelWorker?.app.readState('state:selected-network')==='testnet'")
            page.locator('[data-mcel-radio-choices] input[value="slh"]').check()
            field=page.locator('input[name="hubUrl"]')
            field.wait_for(state='visible')
            page.wait_for_function('''() => window.__mcelWorker.app.readState('state:slh-target')?.authenticationState==='NOT_VERIFIED' ''')
            assert field.get_attribute('placeholder')==HINT
            assert field.input_value()==('https://configured.example:8899' if configured else '')
            if not configured:
                assert not page.evaluate('''() => window.workerRequests.some(r => r.method==='POST' &&
                  (r.body?.managedSLHUrl || r.body?.changed_fields?.includes('managedSLHUrl')))''')
                # Placeholder remains a hint when a user clears a value.
                field.fill('https://example.test:8898')
                assert field.get_attribute('placeholder')==HINT
                field.fill('')
                assert field.input_value()==''
            assert errors==[],errors
        finally:
            browser.close()
