"""Opt-in semantic collection row selection, with genuine field projection.

No real backend is contacted. Only task manager's explicitly authored process
collections opt in; ordinary File Manager/Code Editor collection UI stays as is.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser


LONG_COMMAND = 'C:\\Python312\\python.exe -u -m main_computer.slh.organization_service --state-dir C:\\main_computer\\runtime\\state ' + '--worker=all ' * 35
ROWS = [
    {'pid':4321,'name':'python.exe','created_at':'2026-11-16T12:00:00Z',
     'status':'unknown','memory_human':'n/a','cmdline':LONG_COMMAND,
     'command_preview':'truncated preview...'},
    {'pid':4322,'name':'worker.exe','created_at':'2026-11-16T12:01:00Z',
     'status':'running','memory_human':'82 MB','cmdline':'worker.exe --mode=normal'},
]


def _mount(browser, width=1272):
    page=browser.new_page(viewport={'width':width,'height':588})
    page.evaluate('''() => {
      window.__requests=[];
      window.fetch=async(url,opts) => {
        window.__requests.push({url,body:JSON.parse(opts.body||'{}')});
        return {ok:true,status:200,json:async()=>({processes:window.__rowTestProcesses})};
      };
    }''')
    page.evaluate('(rows) => window.__rowTestProcesses=rows',ROWS)
    page.set_content(render_semantic_page('task-manager',promoted=True))
    page.locator('[data-collection][data-mcel-row-layout] [data-mcel-row-selectable]').first.wait_for()
    return page


def test_opt_in_declared_by_source_not_app_specific_renderer():
    ir=compile_semantic_app('task-manager')
    nodes=ir['surfaces'][0]['nodes']
    by_name={n['sourceName']:n for n in nodes if n.get('sourceName')}
    layout={'identity':['name','pid'],'description':'commandLine',
            'metadata':['status','memory'],'selectOnRow':True}
    for name in ('process-list','all-process-list'):
        assert by_name[name]['rowLayout']==layout
        assert by_name[name]['fields']==['name','pid','commandLine','status','memory']
        assert by_name[name]['selectionIntent']
    assert 'rowLayout' not in by_name['schedule-list']
    assert 'rowLayout' not in by_name['connection-list']
    assert 'rowLayout' not in next(n for n in compile_semantic_app('file-manager')['surfaces'][0]['nodes']
                                   if n['nodeKind']=='collection')


@pytest.mark.parametrize('width', [390,680,1272])
def test_whole_row_selects_and_full_command_has_priority(width):
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=_mount(browser,width)
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            list_=page.locator('[data-collection][data-mcel-row-layout]:visible')
            rows=list_.locator('[data-mcel-row-selectable]')
            assert rows.count()==2
            assert rows.first.get_attribute('role')=='option'
            assert list_.get_attribute('role')=='listbox'
            assert rows.locator('button').count()==0
            assert rows.first.locator('[data-record-field="commandLine"]').inner_text()==LONG_COMMAND
            assert 'truncated preview' not in rows.first.inner_text()
            assert rows.first.locator('[data-record-field="status"]').inner_text()=='unknown'
            assert rows.first.locator('[data-record-field="memory"]').inner_text()=='n/a'
            sizes=page.evaluate('''() => {
              const row=document.querySelector('[data-mcel-row-selectable]');
              const identity=row.querySelector('[data-mcel-row-identity]').getBoundingClientRect();
              const command=row.querySelector('[data-mcel-row-description]').getBoundingClientRect();
              const meta=row.querySelector('[data-mcel-row-metadata]').getBoundingClientRect();
              const workspace=document.querySelector('[data-mcel-zone="workspace"]');
              return {commandWidth:command.width,metaRight:meta.right,identityRight:identity.right,
                commandRight:command.right,metaTop:meta.top,identityTop:identity.top,
                overflow:document.documentElement.scrollWidth-innerWidth,
                workspaceOverflow:workspace.scrollHeight-workspace.clientHeight};
            }''')
            assert sizes['commandWidth'] > 80
            assert sizes['overflow'] <= 2
            if width >= 680:
                assert sizes['workspaceOverflow'] <= 2
            if width >= 680:
                assert sizes['metaRight'] >= sizes['identityRight']
            else:
                assert sizes['metaTop'] >= sizes['identityTop']
            rows.first.click()
            assert page.evaluate('window.__mcelWorkbench.app.readState("state:selected")')=='4321:2026-11-16T12:00:00Z'
            assert rows.first.get_attribute('aria-selected')=='true'
            assert LONG_COMMAND in page.locator('[data-mcel-selected-details]').inner_text()
            assert not any(str(item['url']).endswith('/task/action') for item in page.evaluate('window.__requests'))
            rows.nth(1).focus()
            page.keyboard.press('Space')
            assert page.evaluate('window.__mcelWorkbench.app.readState("state:selected")')=='4322:2026-11-16T12:01:00Z'
            assert rows.nth(1).get_attribute('aria-selected')=='true'
            assert rows.first.get_attribute('aria-selected')=='false'
            assert not errors,errors
            page.close()
        finally:
            browser.close()


def test_refresh_reconciles_rows_without_losing_selection_or_focus():
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=_mount(browser)
            row=page.locator('[data-mcel-row-selectable]').first
            row.click()
            row.focus()
            before=page.evaluate('''() => {
              const r=document.querySelector('[data-mcel-row-selectable]');
              window.__selectedBeforeRefresh=r;return r;
            }''')
            page.evaluate('''async () => await window.__mcelWorkbench.app.execute('intent:refresh',
              {query:'',limit:'100',includeConnections:'true'})''')
            assert page.evaluate('document.querySelector("[data-mcel-row-selectable]")===window.__selectedBeforeRefresh')
            assert page.evaluate('document.activeElement===window.__selectedBeforeRefresh')
            assert row.get_attribute('aria-selected')=='true'
            # Switching to all processes preserves the same entity identity.
            page.locator('[data-mcel-purpose="navigation"] [data-value="all-processes"]').click()
            page.evaluate('''async () => await window.__mcelWorkbench.app.execute('intent:refresh-all',
              {query:'',limit:'100',includeConnections:'true'})''')
            alternate=page.locator('[data-mcel-region$=".all"] [data-mcel-row-selectable]').first
            alternate.wait_for()
            assert alternate.locator('[data-record-field="commandLine"]').inner_text()==LONG_COMMAND
            alternate.click()
            assert page.evaluate('window.__mcelWorkbench.app.readState("state:selected")')=='4321:2026-11-16T12:00:00Z'
            page.close()
        finally:
            browser.close()
