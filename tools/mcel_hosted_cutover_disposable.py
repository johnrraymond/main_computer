"""Optional, tightly scoped real Windows Code Editor write witness.

No existing file may be selected or changed. The acceptance runner must first
verify that the live backend workspace is the exact checkout being tested. Each
browser uses a different newly created file inside a temporary test directory.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from tools.mcel_code_editor_functional_parity_frob import reveal as reveal_editor_control

ORIGINAL='export const mcelHostedAcceptance = 1;\n'
REPLACEMENT='export const mcelHostedAcceptance = 2;\n'


def write_decision(base, url, method, body_text, exact_path):
    from urllib.parse import urlsplit
    target=urlsplit(url);origin=urlsplit(base)
    if (not exact_path or method!='POST' or target.scheme!='http' or
        target.netloc!=origin.netloc or target.query or target.fragment or
        target.path!='/api/applications/editor/project/file/save'):
        return 'deny-disposable-write-scope'
    try:body=json.loads(body_text or '')
    except (ValueError,TypeError):return 'deny-disposable-write-json'
    keys={'repo_dir','project_root','path','replacement_text','expected_before_sha256',
          'explicit_save','stale_source_checked','write_policy','validation_profile'}
    if not isinstance(body,dict) or set(body)!=keys:
        return 'deny-disposable-write-contract'
    if (body['repo_dir']!='.' or body['project_root']!='.' or body['path']!=exact_path or
        body['replacement_text']!=REPLACEMENT or
        body['expected_before_sha256']!=hashlib.sha256(ORIGINAL.encode()).hexdigest() or
        body['explicit_save'] is not True or body['stale_source_checked'] is not True or
        body['write_policy']!='author-owned-source' or body['validation_profile']!='none'):
        return 'deny-disposable-write-payload'
    return 'allow-disposable-source-write'


def verify_workspace(browser,base,repo,storage_state=None):
    ctx=browser.new_context(storage_state=storage_state,service_workers='block')
    try:
        response=ctx.request.post(base+'/api/applications/editor/files',
            data=json.dumps({'repo_dir':'.','path':'','limit':500}),
            headers={'Content-Type':'application/json'},timeout=20000,max_redirects=0)
        if response.status!=200:raise ValueError('EDITOR_WORKSPACE_READ_PRECHECK_FAILED')
        data=response.json()
        workspace=data.get('repo_dir') if isinstance(data,dict) else None
        if not isinstance(workspace,str) or not workspace:
            raise ValueError('EDITOR_WORKSPACE_IDENTITY_UNAVAILABLE')
        if os.path.normcase(os.path.realpath(workspace))!=os.path.normcase(os.path.realpath(str(repo))):
            raise ValueError('EDITOR_WORKSPACE_NOT_SAME_AS_TEST_CHECKOUT')
    finally:ctx.close()


def create_fixture(repo:Path,directory:Path):
    base=repo.resolve();temp=directory.resolve()
    if (not temp.is_relative_to(base/'runtime/reports') or
        not temp.name.startswith('mcel-hosted-signal-') or directory.is_symlink()):
        raise ValueError('UNSAFE_DISPOSABLE_WRITE_ROOT')
    paths={};local={}
    for role,name in [('incumbent','reference.js'),('signals','candidate.js')]:
        path=temp/name
        if path.exists() or path.is_symlink():raise ValueError('DISPOSABLE_TEST_FILE_ALREADY_EXISTS')
        path.write_text(ORIGINAL,encoding='utf8')
        paths[role]=path.relative_to(base).as_posix()
        local[role]=path
    return {'paths':paths,'local':local,'relativeDirectory':temp.relative_to(base).as_posix()}


def _progress(step, role):
    # Never emit source contents or browser authentication material.
    print('MCEL_HOSTED_DISPOSABLE_PROGRESS', 'step='+step, 'role='+role, flush=True)


def _control_geometry(page, control, step=""):
    """Content-free browser diagnostics for a failed test click."""
    try:
        handle=control.element_handle(timeout=1000) if control.count() else None
        return page.evaluate('''el => {
            const root=document.querySelector('[data-mcel-document-workbench]');
            const bounds=el?.getBoundingClientRect();
            const panel=el?.closest('[data-mcel-document-tabpanel]');
            const computed=el?getComputedStyle(el):null;
            return {present:!!el,connected:!!el?.isConnected,
                visible:!!bounds?.width&&!!bounds?.height,
                display:computed?.display||null,visibility:computed?.visibility||null,
                enabled:el?!el.disabled:null,
                tabpanel:panel?.getAttribute('data-mcel-document-tabpanel')||null,
                panelHidden:panel?.hidden??null,
                explorerOpen:root?.getAttribute('data-mcel-explorer-open')||null,
                layoutRung:root?.getAttribute('data-mcel-layout-rung')||null,
                action:el?.getAttribute('data-mcel-generated-action')||null,
                isSourceChoice:el?.hasAttribute('data-select-key')||false};
        }''',handle)
    except Exception:
        # A Playwright ElementHandle is unavailable for a freshly replaced node.
        # Collect geometry through a fresh document query instead of throwing
        # away the useful evidence when the handle itself is the problem.
        selector=('[data-mcel-document-save]' if step.startswith('save-') else
                  '[data-mcel-document-toggle="explorer"]' if step=='reveal-explorer' else
                  '[data-mcel-folder-tree] button[data-mcel-tree-toggle]')
        try:
            return page.evaluate('''selector=>{
              const nodes=[...document.querySelectorAll(selector)];
              const el=nodes.length===1?nodes[0]:null;
              const rect=el?.getBoundingClientRect();
              const style=el?getComputedStyle(el):null;
              const center=rect?document.elementFromPoint(
                Math.max(0,Math.min(innerWidth-1,rect.left+rect.width/2)),
                Math.max(0,Math.min(innerHeight-1,rect.top+rect.height/2))):null;
              return {present:nodes.length>0,matchCount:nodes.length,
                connected:!!el?.isConnected,visible:!!rect?.width&&!!rect?.height,
                enabled:el?!el.disabled:null,display:style?.display||null,
                visibility:style?.visibility||null,
                hitTarget:!!el&&(center===el||el.contains(center)),
                tabpanelHidden:el?.closest('[data-mcel-document-tabpanel]')?.hidden??null,
                explorerOpen:document.querySelector('[data-mcel-document-workbench]')
                  ?.getAttribute('data-mcel-explorer-open')||null,
                diagnostic:'FRESH_DOM_QUERY_AFTER_HANDLE_UNAVAILABLE'};
            }''',selector)
        except Exception:
            try:present=bool(control.count())
            except Exception:present=False
            return {'present':present,'diagnostic':'DOM_AND_HANDLE_UNAVAILABLE'}


def _click_observed(page, control, step, role):
    """Require an actual browser click; never force or call the backend directly."""
    _progress(step,role)
    try:
        control.wait_for(state='visible',timeout=8000)
        control.scroll_into_view_if_needed(timeout=8000)
        control.click(timeout=12000)
    except Exception as exc:
        detail=_control_geometry(page,control,step)
        raise RuntimeError('DISPOSABLE_UI_CLICK_FAILED:'+step+':'+role+':'+
                           type(exc).__name__+':'+json.dumps(detail,sort_keys=True)) from exc


def _reveal_explorer_source(page,target,role):
    """Expose the real Explorer control and any collapsed parent branches."""
    root=page.locator('[data-mcel-document-workbench]').first
    if root.count()!=1:
        raise RuntimeError('DISPOSABLE_DOCUMENT_WORKBENCH_MISSING:'+role)
    if root.get_attribute('data-mcel-explorer-open')!='true':
        toggle=page.locator('[data-mcel-document-toggle="explorer"]').first
        if toggle.count()!=1:
            raise RuntimeError('DISPOSABLE_EXPLORER_TOGGLE_MISSING:'+role)
        _click_observed(page,toggle,'reveal-explorer',role)
        page.wait_for_function('''()=>document.querySelector(
            '[data-mcel-document-workbench]')?.getAttribute('data-mcel-explorer-open')==='true' ''',
            timeout=8000)
    # A cached, collapsed branch does not even render its descendant row.
    # Expand only observed ancestors through their own UI controls.
    parts=target.split('/')
    for i in range(1,len(parts)):
        prefix='/'.join(parts[:i])
        toggle=page.locator('[data-mcel-folder-tree] button[data-mcel-tree-toggle='+
                            json.dumps(prefix)+']').first
        if toggle.count() and toggle.get_attribute('aria-expanded')=='false':
            _click_observed(page,toggle,'expand-source-ancestor-'+str(i),role)
    selector='[data-record-key='+json.dumps(target)+'] button[data-select-key='+json.dumps(target)+']'
    controls=page.locator(selector)
    if controls.count()!=1:
        raise RuntimeError('DISPOSABLE_SOURCE_CONTROL_NOT_UNIQUE_OR_NOT_OBSERVED:'+
                           role+':count='+str(controls.count()))
    control=controls.first
    reveal_editor_control(page,control)
    return control


def _activate_observed_file_by_keyboard(page, target, role, before):
    """Activate the exact observed file button with native browser keyboard input.

    Deep tree nodes can be available to the DOM but difficult for the browser's
    pointer hit-testing in the Explorer rail. Verify the file-to-control binding
    and focus the native button; never invoke app.execute or dispatchEvent.
    """
    _progress('select-disposable-source-keyboard', role)
    evidence = page.evaluate('''target => {
      const controls=[...document.querySelectorAll('[data-mcel-folder-tree] button[data-select-key]')]
        .filter(node=>node.getAttribute('data-select-key')===target);
      const el=controls.length===1?controls[0]:null;
      const row=el?.closest('[data-record-key]');
      const root=el?.closest('[data-mcel-document-workbench]');
      const panel=el?.closest('[data-mcel-document-tabpanel]');
      const style=el?getComputedStyle(el):null;
      const bounds=el?.getBoundingClientRect();
      const visible=!!el && el.isConnected && !!bounds?.width && !!bounds?.height &&
        style?.display!=='none' && style?.visibility==='visible' &&
        !el.closest('[hidden],[inert],[aria-hidden="true"]');
      const valid=controls.length===1 && visible && el.tagName==='BUTTON' &&
        el.type==='button' && !el.disabled &&
        row?.getAttribute('data-record-key')===target &&
        root?.getAttribute('data-mcel-explorer-open')==='true' &&
        (!panel || !panel.hidden);
      if(valid)el.focus({preventScroll:true});
      return {count:controls.length,valid:!!valid,focused:document.activeElement===el,
        visible:!!visible,enabled:el?!el.disabled:null,
        selectedRowExact:row?.getAttribute('data-record-key')===target,
        explorerOpen:root?.getAttribute('data-mcel-explorer-open')||null,
        panelHidden:panel?.hidden??null};
    }''',target)
    if not evidence.get('valid') or not evidence.get('focused'):
        raise RuntimeError('DISPOSABLE_SOURCE_KEYBOARD_NOT_ACTIONABLE:'+role+':'+
                           json.dumps(evidence,sort_keys=True))
    # Playwright supplies the native keyboard event to the focused UI button.
    # The browser itself performs the button's click activation.
    page.keyboard.press('Enter')
    # Do not poll with the default requestAnimationFrame clock: Chromium may
    # throttle or suspend it in the incumbent context while the signal context
    # is active. The operation receipt is ordinary application state, not a
    # paint-completion witness. A wall-clock interval must observe it.
    observation_timeout=False
    try:
        page.wait_for_function('''n=>window.__mcelWorkbench.app.receipts()
            .filter(r=>r.intentId==='intent:open-source').length>n''',
            arg=before,timeout=25000,polling=100)
    except PlaywrightTimeoutError as exc:
        # Browser polling itself can time out even though the application has
        # already committed a receipt. A fresh synchronous read settles the
        # question. Never infer successful opening from the count alone.
        observation_timeout=True
        poll_error=type(exc).__name__
    observed=page.evaluate('''n=>{
        const receipts=window.__mcelWorkbench.app.receipts()
          .filter(r=>r.intentId==='intent:open-source');
        const fresh=receipts.slice(n);
        return {before:n,after:receipts.length,
          newDispositions:fresh.map(r=>r.disposition),
          newSequences:fresh.map(r=>r.sequence)};
    }''',before)
    details={**evidence,**observed,'pollTimedOut':observation_timeout}
    if observation_timeout:details['pollErrorClass']=poll_error
    if observed['after']<=before:
        raise RuntimeError('DISPOSABLE_SOURCE_KEYBOARD_NO_OPEN_RECEIPT:'+role+':'+
                           json.dumps(details,sort_keys=True))
    if observed['after']!=before+1:
        raise RuntimeError('DISPOSABLE_SOURCE_OPEN_RECEIPT_NOT_EXACTLY_ONCE:'+role+':'+
                           json.dumps(details,sort_keys=True))
    if observed['newDispositions']!=['completed']:
        raise RuntimeError('DISPOSABLE_SOURCE_OPEN_RECEIPT_NOT_COMPLETED:'+role+':'+
                           json.dumps(details,sort_keys=True))
    # A verified final snapshot is stronger evidence than a missed callback.
    # The next stage also requires native Monaco to mount this exact file.
    if observation_timeout:
        _progress('source-open-receipt-verified-after-poll-timeout',role)
    else:
        _progress('source-open-receipt-verified',role)


def exercise_write(pages,fixture):
    # Each role must obtain server-backed listing authority before it can open
    # the exact, unique fixture path. No arbitrary file path is accepted.
    for role,page in pages.items():
        prefix=''
        for part in fixture['relativeDirectory'].split('/'):
            prefix=part if not prefix else prefix+'/'+part
            _progress('observe-directory-'+str(len(prefix.split('/'))),role)
            outcome=page.evaluate('''async path=>window.__mcelWorkbench.app.execute(
                'intent:browse-directory',{path})''',prefix)
            if outcome.get('disposition')!='completed':
                raise RuntimeError('DISPOSABLE_DIRECTORY_NOT_AUTHORIZED:'+role+':'+prefix)
        target=fixture['paths'][role]
        control=_reveal_explorer_source(page,target,role)
        before=page.evaluate('''()=>window.__mcelWorkbench.app.receipts()
            .filter(r=>r.intentId==='intent:open-source').length''')
        _activate_observed_file_by_keyboard(page,target,role,before)
        result=page.evaluate('''()=>window.__mcelWorkbench.app.receipts()
            .filter(r=>r.intentId==='intent:open-source').at(-1)?.disposition''')
        if result!='completed':raise RuntimeError('DISPOSABLE_SOURCE_OPEN_FAILED:'+role)
    if any(path.read_text(encoding='utf8')!=ORIGINAL for path in fixture['local'].values()):
        raise RuntimeError('DISPOSABLE_INITIAL_FILE_CHANGED')
    # The actual source-save contract has *no operator input arguments*.
    # Its path and text come from the selected document and Monaco draft.
    for role,page in pages.items():
        _progress('await-disposable-monaco',role)
        page.wait_for_function('''path => window.MainComputerMonacoAdapter?.inspect?.().mounted===true &&
            window.MainComputerMonacoAdapter.inspect().path===path &&
            document.querySelector('[data-mcel-document-surface]')?.dataset.monacoOutcome==='pass' &&
            document.querySelector('[data-mcel-document-dirty]')?.textContent?.startsWith('Opened: '+path)''',
            arg=fixture['paths'][role],timeout=30000)
        save=page.locator('[data-mcel-document-save]').first
        reveal_editor_control(page,save)
        save.wait_for(state='visible',timeout=15000)
        _progress('edit-disposable-monaco',role)
        page.evaluate('''text=>window.__CE_MONACO_MODEL__.setValue(text)''',REPLACEMENT)
        page.wait_for_function("()=>document.querySelector('[data-mcel-document-dirty]')?.dataset.mcelDocumentDirty==='true'",timeout=10000)
        if page.locator('[data-mcel-document-save]').is_disabled():
            raise RuntimeError('DISPOSABLE_EDITOR_DRAFT_NOT_WRITABLE:'+role)
    for page in pages.values():
        page.on('dialog',lambda dialog:dialog.accept() if dialog.type=='confirm' else dialog.dismiss())
    incumbent_save=pages['incumbent'].locator('[data-mcel-document-save]').first
    reveal_editor_control(pages['incumbent'],incumbent_save)
    _click_observed(pages['incumbent'],incumbent_save,'save-incumbent-disposable-source','incumbent')
    pages['incumbent'].wait_for_function('''()=>window.__mcelWorkbench.app.receipts()
        .some(x=>x.intentId==='intent:save-source'&&x.disposition==='completed')''',timeout=25000)
    page=pages['signals']
    page.evaluate('window.__hostHoldNext="/api/applications/editor/project/file/save"')
    before=page.evaluate('''()=>window.__mcelWorkbench.app.receipts()
         .filter(r=>r.intentId==='intent:save-source').length''')
    signal_save=page.locator('[data-mcel-document-save]').first
    reveal_editor_control(page,signal_save)
    _click_observed(page,signal_save,'save-signal-disposable-source','signals')
    page.wait_for_function('()=>window.__hostHeld.length===1',timeout=25000)
    if fixture['local']['signals'].read_text(encoding='utf8')!=REPLACEMENT:
        raise RuntimeError('HOST_WRITE_NOT_APPLIED_BEFORE_HELD_RECEIPT')
    rolled=page.evaluate('window.__mcelWorkbench.app.setSignalSchedulingMode("incumbent")')
    if rolled.get('status')!='ROLLED_BACK':raise RuntimeError('DISPOSABLE_PENDING_WRITE_ROLLBACK_REFUSED')
    if page.evaluate('window.__hostRelease()')!=1:raise RuntimeError('DISPOSABLE_WRITE_RELEASE_COUNT')
    page.wait_for_function('''n=>window.__mcelWorkbench.app.receipts()
        .filter(r=>r.intentId==='intent:save-source').length===n+1''',arg=before,timeout=25000)
    if page.evaluate('''()=>window.__mcelWorkbench.app.receipts()
        .filter(r=>r.intentId==='intent:save-source').at(-1)?.disposition''')!='completed':
        raise RuntimeError('DISPOSABLE_WRITE_RECEIPT_FAILED')
    after=page.evaluate('''()=>window.__mcelWorkbench.app.receipts()
         .filter(r=>r.intentId==='intent:save-source').length''')
    if after!=before+1:raise RuntimeError('DISPOSABLE_WRITE_RECEIPT_NOT_EXACTLY_ONCE')
    if any(path.read_text(encoding='utf8')!=REPLACEMENT for path in fixture['local'].values()):
        raise RuntimeError('DISPOSABLE_SAVE_CONTENT_MISMATCH')
    reenabled=page.evaluate('window.__mcelWorkbench.app.setSignalSchedulingMode("certified")')
    if reenabled.get('status')!='ENABLED':raise RuntimeError('DISPOSABLE_WRITE_REENABLE_REFUSED')
    return 'PASS_DISPOSABLE_SOURCE_WRITE_ROLLBACK'
