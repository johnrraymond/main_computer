"""Task-layout acceptance from semantic roles, not SLH-specific CSS selectors.

The SLH source is one production acceptance case. Negative DOM mutations must
break the geometry oracle. All mutations are browser-local and are not persisted.
"""
from __future__ import annotations

import shutil
from copy import deepcopy

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import compile_slh_application, render_slh_page
from main_computer.mcel_knowledge.semantic_composition import synthesize_composition


MOCK = r'''() => {
  const storage={};
  Object.defineProperty(window,'sessionStorage',{configurable:true,value:{
    getItem:k=>storage[k]??null,setItem:(k,v)=>storage[k]=String(v),removeItem:k=>delete storage[k]
  }});
  window.lifecycle='not_configured';window.slhCalls=[];
  window.fetch=async(url,options={})=>{
    const method=options.method||'GET',body=options.body?JSON.parse(options.body):null;
    window.slhCalls.push({url,method,body});
    if(url==='/api/applications/slh/identities')return {ok:true,json:async()=>({
      schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'device-a',
      identities:[
        {deviceId:'device-a',label:'Default identity',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'},
        {deviceId:'device-b',label:'Backup identity',fingerprint:'b'.repeat(64),defaultStatus:'ADDITIONAL'}
      ],explanation:'available'})};
    if(url==='/api/applications/slh/observe?view=overview')return {ok:true,json:async()=>({
      ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',
      overview:{organizationState:window.lifecycle,organizationEndpoint:'0.0.0.0:8899'}})};
    if(url==='/api/applications/slh/setup/action'){
      if(body.action==='configure-and-enable')window.lifecycle='running';
      if(body.action==='disable')window.lifecycle='disabled';
      return {ok:true,json:async()=>({
        outcome:window.lifecycle==='running'?'RUNNING':'DISABLED',message:'Changed',deviceId:body.arguments.adminDeviceId
      })};
    }
    throw Error('UNEXPECTED_API:'+url);
  };
}'''


def _facts(page):
    return page.evaluate('''() => {
      const region=document.querySelector('[data-mcel-purpose="task"]');
      const form=region?.querySelector('form');
      const address=form?.querySelector('label[data-field="bindAddress"]');
      const port=form?.querySelector('label[data-field="port"]');
      const echo=region?.querySelector('[data-mcel-reference="selected"]');
      const action=form?.querySelector('button[type="submit"]');
      const reset=form?.querySelector('button[type="reset"]');
      const footer=form?.querySelector('[data-mcel-task-actions]');
      const status=form?.querySelector('[data-mcel-form-result]');
      const commands=document.querySelector('[data-mcel-purpose="commands"]');
      const disable=commands?.querySelector('[data-intent="intent:disable-organization"]');
      const rect=el=>el?.getBoundingClientRect();
      return {
        task:!!region, form:!!form, echo:!!echo, commands:!!commands,
        sameTask:!!(echo && form && form.contains(echo)),
        identityText:echo?.innerText||'',
        addressWidth:rect(address)?.width||0, portWidth:rect(port)?.width||0,
        addressY:rect(address)?.y||0, portY:rect(port)?.y||0,
        identityY:rect(echo)?.y||0, actionY:rect(action)?.y||0,
        actionWidth:rect(action)?.width||0, formWidth:rect(form)?.width||0,
        resetY:rect(reset)?.y||0, referenceFont:echo?getComputedStyle(echo).fontFamily:'',
        addressBottom:rect(address)?.bottom||0,portBottom:rect(port)?.bottom||0,
        identityBottom:rect(echo)?.bottom||0,formRight:rect(form)?.right||0,
        footerY:rect(footer)?.y||0,footerRight:rect(footer)?.right||0,
        actionRight:rect(action)?.right||0,resetRight:rect(reset)?.right||0,
        statusY:rect(status)?.y||0,statusInFooter:!!status && !!footer?.contains(status),
        statusVisible:!!status && !!status.textContent?.trim(),
        footerContainsButtons:!!footer && footer.contains(action) && footer.contains(reset),
        taskFieldPolicy:form?.dataset.mcelTaskFields,
        disableVisible:!!disable && !!disable.getClientRects().length && !disable.closest('[hidden]'),
        disableDisabled:disable?.disabled ?? null,
        lifecycleVisible:!!commands && !!commands.getClientRects().length && !commands.closest('[hidden]'),
        commandSeparated:!!(commands && disable && !region.contains(disable)),
        viewportOverflow:document.documentElement.scrollWidth>document.documentElement.clientWidth+1
      };
    }''')


def _check(f,width,identity="Default identity"):
    assert f['task'] and f['form'] and f['echo'] and f['commands'],f
    assert f['sameTask'] and f['commandSeparated'],f
    assert identity in f['identityText'],f
    assert 'a'*64 not in f['identityText'],f  # full identity metadata stays in navigation
    assert 80 <= f['portWidth'] <= 165, f
    assert f['addressWidth'] > f['portWidth']*1.6 or width <= 480, f
    assert f['actionWidth'] < min(250, f['formWidth']*.8),f
    assert f['taskFieldPolicy']=='text-number',f
    assert f['footerContainsButtons'] and not f['statusInFooter'],f
    assert f['identityY'] >= max(f['addressBottom'],f['portBottom'])-1,f
    assert f['footerY'] > f['identityBottom']-1,f
    assert abs(f['actionY']-f['resetY']) <= 4,f
    assert f['resetRight'] <= f['actionRight']-1,f
    assert abs(f['actionRight']-f['formRight']) <= 3,f
    if f['statusVisible']: assert f['statusY'] >= f['footerY']+30,f
    assert abs(f['addressY']-f['portY'])<=4,f
    assert not f['viewportOverflow'],f


def test_task_relation_is_derived_from_source_and_refuses_detached_echo():
    ir,plan=compile_slh_application()
    task=next(r for r in plan['regions'] if r['recipe']=='task')
    commands=next(r for r in plan['regions'] if r['recipe']=='commands')
    assert task['purpose']=='task' and task['priority']=='primary'
    assert [m['ref'].rsplit('.',1)[-1] for m in task['members']]==[
      'configure-org-slh','first-administrator-identity']
    assert [m['ref'].rsplit('.',1)[-1] for m in commands['members']]==['disable-org-slh']
    assert not any(r['recipe']=='selection-echo' for r in plan['regions'])
    broken=deepcopy(ir)
    region=next(r for r in broken['surfaces'][0]['interfaceIntent']['regions'] if r['role']=='task')
    region['members']=[region['members'][0]]
    with pytest.raises(ValueError):synthesize_composition(broken,policy='mcel.semantic-structure.p3')


@pytest.mark.parametrize('width',[390,1040,1440])
def test_semantic_task_layout_and_real_lifecycle(width):
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':width,'height':800})
            page.on('dialog',lambda dialog:dialog.accept())
            page.evaluate(MOCK)
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='device-a'")
            facts=_facts(page);_check(facts,width)
            assert facts['lifecycleVisible'] and facts['disableVisible'] and facts['disableDisabled']  # stable section
            assert all(c['method']=='GET' for c in page.evaluate('window.slhCalls'))
            page.locator('[data-select-key="device-b"]').click()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:selected-local-identity')==='device-b'")
            assert 'Backup identity' in _facts(page)['identityText']
            form=page.locator('form[data-intent="intent:configure-and-enable-organization"]')
            form.get_by_role('button',name='Configure ORG-SLH').click()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='running'")
            after_configure=_facts(page)
            _check(after_configure,width,"Backup identity")
            assert after_configure['lifecycleVisible'] and after_configure['disableVisible'] and not after_configure['disableDisabled']
            assert after_configure['statusVisible']
            req=page.evaluate("window.slhCalls.find(c=>c.body?.action==='configure-and-enable')")
            assert req['body']['arguments']['adminDeviceId']=='device-b'
            page.locator('button[data-intent="intent:disable-organization"]').click()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='disabled'")
            assert _facts(page)['lifecycleVisible'] and _facts(page)['disableVisible'] and _facts(page)['disableDisabled']
            assert form.locator('[name="port"]').input_value()=='8899'
            # Negative controls: restoring generic flex must not pass as a task
            # just because the controls are present and functionally bound.
            page.evaluate('''() => {document.querySelector('[data-mcel-purpose="task"] form').style.display='flex';}''')
            with pytest.raises(AssertionError):_check(_facts(page),width,"Backup identity")
            page.evaluate('''() => {document.querySelector('[data-mcel-purpose="task"] form').style.display='';}''')
            # Operational feedback is separate from the task action footer.
            page.evaluate('''() => {const form=document.querySelector('[data-mcel-purpose="task"] form');
              form.querySelector('[data-mcel-task-actions]').append(form.querySelector('[data-mcel-form-result]'));}''')
            with pytest.raises(AssertionError):_check(_facts(page),width,"Backup identity")
            page.evaluate('''() => {const form=document.querySelector('[data-mcel-purpose="task"] form');
              form.append(form.querySelector('[data-mcel-form-result]'));}''')
            # Negative controls: reintroduce the oversized port and button layouts.
            page.evaluate('''() => {let p=document.querySelector('[data-field="port"]');p.style.width='470px';}''')
            with pytest.raises(AssertionError):_check(_facts(page),width)
            page.evaluate('''() => {let p=document.querySelector('[data-field="port"]');p.style.width='';
              let e=document.querySelector('[data-mcel-reference="selected"]');e.remove();}''')
            with pytest.raises(AssertionError):_check(_facts(page),width)
        finally:
            browser.close()


def test_service_lifecycle_disable_remains_available_when_enabled_service_has_failed():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':1040,'height':800})
            page.evaluate(MOCK)
            page.evaluate("window.lifecycle='failed'")
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:organization-lifecycle')?.state==='failed'")
            assert _facts(page)['disableVisible'] and not _facts(page)['disableDisabled']
            assert all(c['method']=='GET' for c in page.evaluate('window.slhCalls'))
        finally:
            browser.close()
