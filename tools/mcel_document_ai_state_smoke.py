"""Live, test-only acceptance of AI-directed Document Editor state changes.

Uses the authenticated canonical editor and its configured real AI provider.
Never opens or saves a real document or the user's scratchpad. No mocked AI,
HTTP response injection, or direct mutation of the result under test.
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from tools.mcel_functional_auth import ORIGIN, new_context
from tools.mcel_phase2b_browser import launch_semantic_browser

NODE = 'surface-node:document.draft'
AI_PATH = '/api/applications/docs/ai'
# Explicit observable contract for the future metadata coupling. An absent
# state is a failure, never inferred from a heading, filename or AI commentary.
METADATA = {
    'name': 'state:document-name', 'date': 'state:document-date',
    'type': 'state:document-type', 'author': 'state:document-author',
    'description': 'state:document-description',
}
LAYOUT = {
    'layout.mode': 'preset', 'layout.preset': 'a4',
    'layout.margins.top': 72, 'layout.margins.right': 80,
    'layout.margins.bottom': 88, 'layout.margins.left': 64,
    'view.mode': 'endless', 'view.zoom': 1.25, 'view.showPageBreaks': False,
}

SNAPSHOT = '''({node, metadata}) => {
 const app=window.__mcelWorkbench.app, surface=app.getDocumentSurface(node);
 const s=surface.getState(), ir=JSON.parse(document.getElementById('mcel-workbench-ir').textContent);
 const ids=new Set(ir.states.map(x=>x.id));
 const values={...(window.__mcelWorkbench.getDocumentMetadata?.()||{})};
 for(const [key,id] of Object.entries(metadata)) {
   if(ids.has(id)) values[key]=app.readState(id) ?? null;
 }
 // A future typed document metadata record is equally acceptable.
 if(ids.has('state:document-metadata')) {
   const record=app.readState('state:document-metadata');
   for(const key of Object.keys(metadata)) if(record && Object.hasOwn(record,key)) values[key]=record[key];
 }
 const t=document.createElement('template');t.innerHTML=s.html;
 const texts=sel=>Array.from(t.content.querySelectorAll(sel),x=>x.textContent.trim());
 return {html:s.html,text:t.content.textContent,revision:s.revision,instanceId:s.instanceId,
   layout:s.layout,metadata:values,headings:texts('h1'),paragraphs:texts('p'),
   bold:texts('strong,b'),italic:texts('em,i'),listItems:texts('ul li'),
   proposal:app.readState('state:proposal'),draft:app.readState('state:draft')};
}'''


def at(obj, path):
    for key in path.split('.'):
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return obj


def evaluate_full(before, after, target, layout=LAYOUT):
    """Only applied authoritative state is evidence; exact fixture values."""
    checks = {}
    def check(name, actual, expected, old, *, present=True):
        equal = type(actual) is type(expected) and actual == expected
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            equal = type(actual) in (int, float) and actual == expected
        checks[name] = {'status': 'PASS' if present and equal and old != expected else 'FAIL',
                        'expected': expected, 'before': old, 'after': actual,
                        'reason': '' if present else 'AUTHORITATIVE_STATE_NOT_EXPOSED'}
    for key, expected in target['metadata'].items():
        check('metadata.'+key, after['metadata'].get(key), expected,
              before['metadata'].get(key), present=key in after['metadata'])
    for key, expected in layout.items():
        old, actual = at(before['layout'], key), at(after['layout'], key)
        if old == expected and key != 'layout.custom.widthPx' and key != 'layout.custom.heightPx':
            checks['layout.'+key] = {'status': 'PASS' if actual == expected else 'FAIL',
                                     'before': old, 'after': actual, 'expected': expected,
                                     'reason': 'invariant; not a mutation claim'}
        else:
            check('layout.'+key, actual, expected, old)
    for key in ('headings', 'paragraphs', 'bold', 'italic', 'listItems'):
        expected = target[key]
        check('content.'+key, after[key], expected, before[key])
    check('content.original_removed', target['original'] not in after['text'], True,
          target['original'] not in before['text'])
    checks['content.state_binding'] = {'status': 'PASS' if after['html'] == after['draft'] else 'FAIL'}
    return checks


CUSTOM_LAYOUT = {**LAYOUT, 'layout.mode': 'custom', 'layout.preset': None,
                 'layout.custom.widthPx': 900, 'layout.custom.heightPx': 1200}

def snapshot(frame):
    return frame.evaluate(SNAPSHOT, {'node': NODE, 'metadata': METADATA})


def action(frame, name):
    return frame.evaluate('(id)=>window.__mcelWorkbench.app.execute(id)', 'intent:'+name)


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def open_app(page):
    response = page.goto(ORIGIN+'/applications/document', wait_until='domcontentloaded', timeout=120000)
    require(response is not None and response.ok, 'Canonical editor navigation failed')
    deadline = time.monotonic()+60
    while time.monotonic() < deadline:
        for frame in page.frames:
            parsed = urlsplit(frame.url)
            if parsed.path == '/applications/document' and parsed.netloc == urlsplit(ORIGIN).netloc:
                if frame.locator('#mcel-workbench-ir').count():
                    frame.wait_for_function("window.__mcelWorkbench?.appId==='document'", timeout=30000)
                    return frame
        page.wait_for_timeout(200)
    raise RuntimeError('Native canonical Document Editor not found')


def seed(frame, text, *, full=False):
    # Fixture setup only: an unbound in-memory document, not a stored scratchpad.
    frame.evaluate('''({node,text})=>{
      const s=__mcelWorkbench.app.getDocumentSurface(node), p=document.createElement('p');
      p.textContent=text;s.loadDocument({expectedRevision:s.getState().revision,
        html:p.outerHTML,layout:McelDocumentEngine.defaultDocumentLayoutState()});
    }''', {'node': NODE, 'text': text})
    frame.locator('.mcel-document-surface-element .mc-page-content p').first.wait_for()
    frame.evaluate('''()=>{
      const p=document.querySelector('.mcel-document-surface-element .mc-page-content p');
      p.closest('[contenteditable=true]').focus();const r=document.createRange();
      r.selectNodeContents(p);getSelection().removeAllRanges();getSelection().addRange(r);
    }''')
    if full:
        frame.get_by_role('button', name='Lock Entire Document', exact=True).click()
        frame.wait_for_function("__mcelWorkbench.app.readState('state:proposal').startsWith('Locked: entire document')")
        return
    result = action(frame, 'lock-selection')
    require(result['disposition']=='completed', 'Lock failed: '+str(result))


def request_ai(page, frame, instruction, timeout, retries=1):
    form = frame.locator('[data-mcel-node="surface-node:document.ai-instruction"] form')
    form.locator('input,textarea').first.fill(instruction)
    def match(response):
        if response.url != ORIGIN+AI_PATH or response.request.method != 'POST':
            return False
        try:
            return response.request.post_data_json.get('instruction') == instruction
        except Exception:
            return False
    start = frame.evaluate('()=>__mcelWorkbench.app.receipts().at(-1)?.sequence || 0')
    with page.expect_response(match, timeout=timeout*1000) as pending:
        form.locator('button[type=submit]').click()
    response = pending.value
    try:
        data = response.json()
    except Exception:
        data = {}
    frame.wait_for_function('''start=>__mcelWorkbench.app.receipts().some(r=>
        r.sequence>start && r.intentId==='intent:request-ai-proposal' &&
        ['completed','failed','refused-before-attempt'].includes(r.disposition))''', arg=start, timeout=15000)
    receipt = frame.evaluate('()=>__mcelWorkbench.app.receipts().filter(r=>r.intentId==="intent:request-ai-proposal").at(-1)')
    if response.status in (502, 503, 504) and retries:
        page.wait_for_timeout(2000)
        return request_ai(page, frame, instruction, timeout, retries-1)
    require(response.ok and data.get('ok') is True, 'AI HTTP failure: '+str(response.status))
    require(bool(data.get('provider')) and bool(data.get('model')), 'AI provider/model receipt missing')
    return {'http': response.status, 'provider': data['provider'], 'model': data['model'],
            'request': response.request.post_data_json, 'suggestion': data.get('suggestion'), 'receipt': receipt}


def lane(ctx, *, full, token, timeout, custom=False):
    page = ctx.new_page()
    # Only the real AI endpoint may receive a POST from this isolated app page.
    # No saves, exports, scratchpad reads or unrelated mutations.
    blocked = []
    def guard(route):
        req = route.request
        if ((req.method in ('GET', 'HEAD') and req.url.startswith(ORIGIN+'/'))
                or (req.method == 'POST' and req.url == ORIGIN+AI_PATH)):
            route.continue_()
        else:
            blocked.append({'method': req.method, 'path': urlsplit(req.url).path})
            route.abort()
    page.route('**/*', guard)
    page.on('dialog', lambda d: d.accept() if d.type == 'confirm' and 'apply' in d.message.lower() else d.dismiss())
    result = {'status': 'FAIL', 'checks': {}, 'blocked_requests': blocked}
    original = 'Original fixture '+token
    target = {'original': original,
              'metadata': {'name': 'AI smoke '+token, 'date': '1929-09-04', 'type': 'memorandum',
                           'author': 'Test Author '+token, 'description': 'Disposable AI smoke '+token},
              'headings': ['Report '+token], 'paragraphs': ['Approved '+token+' reviewed '+token],
              'bold': ['Approved '+token], 'italic': ['reviewed '+token],
              'listItems': ['First '+token, 'Second '+token]}
    replacement = 'Revised fixture '+token
    layout = CUSTOM_LAYOUT if custom else LAYOUT
    page_spec = 'Set custom page dimensions 900 pixels wide by 1200 pixels high (custom page mode); ' if custom else 'Set page preset A4; '
    if full:
        instruction = ('Update the actual editable document state, not merely its text or a description of changes. '
                       'Set metadata name, document date, type, author and description exactly to: '+json.dumps(target['metadata'])+
                       '. Replace the entire content with one H1 heading '+target['headings'][0]+
                       ', one paragraph containing bold '+target['bold'][0]+' then one space then italic '+target['italic'][0]+
                       ', and one unordered list with exactly '+json.dumps(target['listItems'])+
                       '. '+page_spec+'margins top 72, right 80, bottom 88, left 64 pixels; '
                       'endless view, zoom 125 percent, page-break guides off. Apply these as real document properties. '
                       'Do not include metadata or layout instructions as document prose.')
    else:
        instruction = 'Replace the selected text with exactly this text, no quotes or explanation: '+replacement
    try:
        frame = open_app(page)
        seed(frame, original, full=full)
        before = snapshot(frame)
        result['before'] = before
        result['ai'] = request_ai(page, frame, instruction, timeout)
        proposed = snapshot(frame)
        result['proposal_before_apply'] = proposed
        same = all(before[k] == proposed[k] for k in ('html','layout','metadata','revision'))
        result['checks']['proposal_nonmutating'] = {'status': 'PASS' if same else 'FAIL'}
        start = frame.evaluate('()=>__mcelWorkbench.app.receipts().at(-1)?.sequence || 0')
        frame.get_by_role('button', name='Apply AI Proposal', exact=True).click()
        frame.wait_for_function('''start=>__mcelWorkbench.app.receipts().some(r=>r.sequence>start && r.intentId==='intent:apply-ai-proposal' && ['completed','failed','refused-before-attempt'].includes(r.disposition))''',arg=start,timeout=15000)
        result['apply_receipt'] = frame.evaluate('()=>__mcelWorkbench.app.receipts().filter(r=>r.intentId==="intent:apply-ai-proposal").at(-1)')
        after = snapshot(frame)
        result['after'] = after
        result['checks']['ai_request_completed'] = {'status': 'PASS' if result['ai']['receipt']['disposition']=='completed' else 'FAIL'}
        result['checks']['apply_completed'] = {'status': 'PASS' if result['apply_receipt']['disposition']=='completed' else 'FAIL'}
        if full:
            req = result['ai']['request']
            result['checks']['full_state_exposed'] = {'status': 'PASS' if req.get('preferred_operation')=='transform_document' and req['document'].get('metadata')==before['metadata'] and req['document'].get('layout')==before['layout'] else 'FAIL'}
            result['checks']['metadata_ui_binding'] = {'status': 'PASS' if all(frame.locator('[data-document-metadata] [name='+k+']').input_value()==v for k,v in after['metadata'].items()) else 'FAIL'}
            result['checks'].update(evaluate_full(before, after, target, layout))
        else:
            result['checks']['selection_replaced'] = {'status': 'PASS' if after['text']==replacement and after['revision']>before['revision'] else 'FAIL'}
            result['checks']['unrelated_state_unchanged'] = {'status': 'PASS' if before['layout']==after['layout'] and before['metadata']==after['metadata'] else 'FAIL'}
        if all(v['status']=='PASS' for v in result['checks'].values()):
            result['status']='PASS'
    except Exception as exc:
        result['error'] = type(exc).__name__+': '+str(exc)
        result['status']='BLOCKED'
        if full:
            # Preserve the inventory even when a prerequisite fails.
            for key in [*('metadata.'+k for k in METADATA), *('layout.'+k for k in layout),
                        *('content.'+k for k in ('headings','paragraphs','bold','italic','listItems','original_removed','state_binding'))]:
                result['checks'].setdefault(key, {'status':'NOT_RUN', 'reason':'prerequisite failed'})
    finally:
        page.close()
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ai-timeout', type=int, default=180, help='Seconds per real AI response')
    args=parser.parse_args(argv)
    if args.ai_timeout < 1:
        parser.error('--ai-timeout must be positive')
    token=uuid.uuid4().hex[:10]
    report={'status':'BLOCKED','fixture':token,'origin':ORIGIN,'lanes':{},'production_modified':False,
            'persistence_tested':False,'real_ai_required':True}
    path=Path('runtime/reports/mcel_document_ai_state_smoke')/(token+'.json')
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser, source=launch_semantic_browser(p)
            report['browser']=source
            try:
                ctx=new_context(browser, ORIGIN, viewport={'width':1440,'height':900})
                try:
                    for name,full,custom in (('selection_control',False,False),('full_document_state',True,False),('custom_page_state',True,True)):
                        print('DOCUMENT_AI_SMOKE',name,'BEGIN',flush=True)
                        result=lane(ctx,full=full,token=token,timeout=args.ai_timeout,custom=custom)
                        report['lanes'][name]=result
                        for key,check in result['checks'].items():
                            print('DOCUMENT_AI_SMOKE',name,key,check['status'],flush=True)
                        print('DOCUMENT_AI_SMOKE',name,result['status'],result.get('error',''),flush=True)
                finally:
                    ctx.close()
                report['disposable_session_revoked']=True
            finally:
                browser.close()
        statuses=[r['status'] for r in report['lanes'].values()]
        report['status']='PASS' if statuses==['PASS','PASS','PASS'] else 'FAIL' if 'FAIL' in statuses else 'BLOCKED'
    except Exception as exc:
        report['error']=type(exc).__name__+': '+str(exc)
        report['status']='BLOCKED'
    finally:
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        print('DOCUMENT_AI_SMOKE',report['status'],'report='+str(path),flush=True)
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
