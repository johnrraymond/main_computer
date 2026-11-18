"""SLH declares its dynamic semantic elements and preserves them on observation.

No resident SLH or Coolify connections are used; all browser reads are mocked.
"""
from __future__ import annotations

import shutil

from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import compile_slh_application
from tests.test_mcel_seh_two_action_lifecycle_ui import _setup


def test_slh_owns_update_declarations_for_every_tab_and_identity_row():
    ir, _ = compile_slh_application()
    nodes = {n['id']: n for n in ir['surfaces'][0]['nodes']}
    notebook = nodes['surface-node:slh.organization-identity-notebook']
    assert notebook['updates'] == {'rows': True, 'status': True, 'retention': True}
    for target in ('local', 'network'):
        node = nodes[f'surface-node:slh.{target}-seh-lifecycle']
        assert len(node['tabs']) == 5
        for tab in node['tabs']:
            updates = tab['updates']
            assert updates['fields'] == tab['fields']
            assert set(updates['actions']) == set(tab['actions'])
            assert updates['status'] is True
            assert updates['forms'] is bool(tab.get('forms'))


def test_every_lifecycle_tab_keeps_semantic_dom_during_repeated_observations():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page, notebook = _setup(browser, 'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            for name in ('configuration','deployment','connection','status','diagnostics'):
                notebook.locator(f'[data-mcel-operation-tab="{name}"]').click()
                # Reading a connection tab can produce an authentication refusal;
                # it is a value update, not permission to rebuild the tab.
                page.wait_for_timeout(150)
                page.evaluate('''name => {
                  const n=document.querySelector('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"]');
                  const field=n.querySelector('[data-mcel-operation-field]');
                  const tab=n.querySelector(`[data-mcel-operation-tab="${name}"]`);
                  const form=n.querySelector('[data-mcel-operation-form]');
                  const button=n.querySelector('[data-mcel-operation-action]');
                  const observer=new MutationObserver(records=>{
                    window.__slhUpdateCheck.childMutations += records.filter(r=>r.type==='childList').length;
                  });
                  window.__slhUpdateCheck={n,field,tab,form,button,childMutations:0,observer};
                  observer.observe(n,{subtree:true,childList:true,attributes:true,characterData:true});
                }''', name)
                for _ in range(2):
                    receipt = page.evaluate('''async () => await window.__mcelSlh.app.execute('intent:read-seh-deployment')''')
                    assert receipt['disposition'] == 'completed', (name, receipt)
                check = page.evaluate('''() => {
                  const t=window.__slhUpdateCheck,n=t.n;
                  t.observer.disconnect();
                  return {field:t.field===n.querySelector('[data-mcel-operation-field]'),
                    tab:t.tab===n.querySelector(`[data-mcel-operation-tab="${t.tab.dataset.mcelOperationTab}"]`),
                    form:t.form===n.querySelector('[data-mcel-operation-form]'),
                    button:t.button===n.querySelector('[data-mcel-operation-action]'),
                    childMutations:t.childMutations};
                }''')
                assert check == dict(field=True,tab=True,form=True,button=True,childMutations=0), (name,check)
            assert not errors, errors
        finally:
            browser.close()
