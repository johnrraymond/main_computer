"""SEH Deployment always uses semantic value updates after initial rendering.

Browser HTTP calls are mocked; no live SLH or Coolify mutation is performed.
"""
import shutil

from playwright.sync_api import sync_playwright
from tests.test_mcel_seh_two_action_lifecycle_ui import _setup


def test_seh_deployment_semantic_updates_are_default_and_preserve_dom():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None,
                                     args=['--no-sandbox'])
        try:
            page, _ = _setup(browser, 'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED')
            errors = []
            page.on('pageerror', lambda err: errors.append(str(err)))
            assert page.evaluate('typeof window.McelSemanticValueUpdaters') == 'object'
            assert page.evaluate('window.MCEL_SEH_DEPLOYMENT_UPDATE_MODE') is None
            page.evaluate('''() => {
              const n=document.querySelector('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"]');
              const tab=n.querySelector('[data-mcel-operation-tab="deployment"]');
              const field=n.querySelector('[data-mcel-operation-field="deploymentStatus"]');
              const button=n.querySelector('[data-mcel-operation-action="Deploy / Redeploy"]');
              const wear=document.createElement('span');
              wear.setAttribute('data-mcel-chrome-decorative','true');
              button.appendChild(wear);
              window.__sehUpdateTest={n,tab,field,textNode:field.firstChild,button,wear,mutations:[]};
              const observer = new MutationObserver(records => {
                window.__sehUpdateTest.mutations.push(...records.map(r => ({
                  kind:r.type,target:r.target.nodeName,attribute:r.attributeName||null
                })));
              });
              observer.observe(n,{subtree:true,childList:true,characterData:true,attributes:true});
              window.__sehUpdateTest.observer=observer;
            }''')
            for _ in range(2):
                result=page.evaluate("async()=>await window.__mcelSlh.app.execute('intent:read-seh-deployment')")
                assert result['disposition']=='completed'
            stable=page.evaluate('''() => {
              const t=window.__sehUpdateTest, n=t.n;
              return {mutations:t.mutations.filter(m=>m.kind!=='attributes').length,
                tab:n.querySelector('[data-mcel-operation-tab="deployment"]')===t.tab,
                field:n.querySelector('[data-mcel-operation-field="deploymentStatus"]')===t.field,
                button:n.querySelector('[data-mcel-operation-action="Deploy / Redeploy"]')===t.button,
                wear:t.button.contains(t.wear),
                status:t.field.textContent};
            }''')
            assert stable == {'mutations':0,'tab':True,'field':True,'button':True,'wear':True,
                              'status':'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED'},stable
            for phase in ('SEH_UNDEPLOYED', 'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED'):
                page.evaluate('(phase)=>{window.sehUiPhase=phase}',phase)
                result=page.evaluate("async()=>await window.__mcelSlh.app.execute('intent:read-seh-deployment')")
                assert result['disposition']=='completed'
                changed=page.evaluate('''() => {
                  const t=window.__sehUpdateTest, n=t.n;
                  return {status:t.field.textContent,
                    tab:n.querySelector('[data-mcel-operation-tab="deployment"]')===t.tab,
                    field:n.querySelector('[data-mcel-operation-field="deploymentStatus"]')===t.field,
                    button:n.querySelector('[data-mcel-operation-action="Deploy / Redeploy"]')===t.button,
                    wear:t.button.contains(t.wear),
                    textNode:t.field.firstChild===t.textNode,
                    mutationKinds:t.mutations.filter(m=>m.kind!=='attributes').map(x=>x.kind)};
                }''')
                assert changed['status']==phase,changed
                assert all(changed[k] for k in ('tab','field','button','wear','textNode')),changed
                assert set(changed['mutationKinds']) <= {'characterData'},changed
            # A deliberate tab change may rebuild structure. The subsequently
            # reentered Deployment tab must resume managed updates immediately.
            page.locator('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"] [data-mcel-operation-tab="connection"]').click()
            page.locator('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"] [data-mcel-operation-tab="deployment"]').click()
            page.evaluate('window.sehUiPhase="SEH_UNDEPLOYED"')
            result=page.evaluate("async()=>await window.__mcelSlh.app.execute('intent:read-seh-deployment')")
            assert result['disposition']=='completed'
            assert page.locator('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"] [data-mcel-operation-field="deploymentStatus"]').inner_text()=='SEH_UNDEPLOYED'
            assert not errors,errors
        finally:
            browser.close()
