"""Authenticated, real ONLYOFFICE + configured AI provider acceptance.

Creates two uniquely named disposable XLSX files and a new chat. Never modifies
existing workbooks, executes generated code, changes provider settings, or
fabricates callbacks. Artifacts remain in the dev installation for inspection.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import io
import json
import re
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tools.mcel_functional_auth import ORIGIN, new_context
from tools.mcel_phase2b_browser import launch_semantic_browser

API = '/api/applications/onlyoffice/'
ROOT = '#mcel-onlyoffice-root'
NAME = '[data-mcel-node="surface-node:onlyoffice.new-workbook"] input'
CODE = '[data-mcel-node="surface-node:onlyoffice.selected-cell-code"]'
NOTEBOOK = '#onlyoffice-advanced-chat-notebook'
FORMULA = '=SUM(A1:A10)'
DEFAULT_REPORT = Path('runtime/reports/mcel-onlyoffice-full-functional/report.json')


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def validate_host(base):
    if base not in (ORIGIN, ORIGIN + '/'):
        raise ValueError('Only the authenticated dev gateway is allowed')
    return ORIGIN


def open_app(page, base, *, shell=True):
    response = page.goto(base + '/applications/onlyoffice' + ('' if shell else '?mcel-shell-child=1'),
                         wait_until='domcontentloaded', timeout=120000)
    require(response is not None and response.ok, 'ONLYOFFICE navigation failed')
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        for frame in page.frames:
            if frame.url.startswith(base + '/applications/onlyoffice') and frame.locator(ROOT).count():
                frame.wait_for_function("document.body.dataset.mcelOnlyofficeReady === 'true'", timeout=30000)
                frame.wait_for_function("__mcelWorkbench.app.readState('state:server-status') !== 'Checking Document Server'", timeout=30000)
                return frame
        page.wait_for_timeout(200)
    raise AssertionError('Canonical MCEL ONLYOFFICE frame not found')


def wait_active(app, path):
    app.wait_for_function("path => __mcelWorkbench.app.readState('state:active-workbook') === path",
                          arg=path, timeout=150000)
    app.locator('#onlyoffice-editor-host > iframe').wait_for(state='visible')


def config_action(page, app, path, action, *, hardened=True):
    def match(response):
        if urlsplit(response.url).path != API + 'config' or response.request.method != 'POST':
            return False
        try:
            return response.request.post_data_json.get('path') == path
        except Exception:
            return False
    with page.expect_response(match, timeout=45000) as event:
        action()
    response = event.value
    data = response.json()
    require(response.ok and data.get('ok') is True, 'Workbook configuration refused')
    require(data['config']['document']['title'] == path, 'Wrong document title')
    if hardened:
        origin = urlsplit(data['public_url'])
        require(data.get('hardened_editor_origin') is True and origin.scheme == 'https'
                and origin.hostname == 'onlyoffice.main-computer.localhost', 'Non-isolated editor configuration')
        download_path(data, path)
    wait_active(app, path)
    return data


def download_path(config, path):
    # Never follow the Docker/internal callback hostname or an arbitrary URL.
    # Read the same purpose-bound grant through the authenticated app origin.
    url = urlsplit(config['config']['document']['url'])
    query = parse_qs(url.query)
    require(url.path == API + 'file' and query.get('path') == [path]
            and len(query.get('cap', [])) == 1 and bool(query['cap'][0]), 'Invalid workbook download grant')
    return url.path + '?' + url.query


def create(page, app, name, *, hardened=True):
    app.locator(NAME).fill(name)
    return config_action(page, app, name, lambda: app.get_by_role('button', name='New XLSX', exact=True).click(), hardened=hardened)


def import_book(page, app, path, *, hardened=True):
    def action():
        with page.expect_file_chooser() as chooser:
            app.get_by_role('button', name='Import', exact=True).click()
        chooser.value.set_files(str(path))
    return config_action(page, app, path.name, action, hardened=hardened)


def select_book(page, app, name, *, hardened=True):
    return config_action(page, app, name, lambda: app.get_by_role('button', name=name, exact=True).click(), hardened=hardened)


def assert_surface(app):
    require(app.evaluate('typeof window.DocsAPI') == 'undefined', 'DocsAPI leaked into MCEL origin')
    for label in ('Refresh Server Status', 'Save Now', 'Reload Editor', 'Open Workbook', 'Upload Workbook'):
        require(app.get_by_role('button', name=label, exact=True).count() == 0, 'Obsolete control: ' + label)
    require(app.get_by_role('button', name='Import', exact=True).count() == 1, 'Import unavailable')
    require(app.evaluate('''() => {const b=[...document.querySelectorAll('button')];
        const i=b.find(e=>e.textContent.trim()==='Import'), a=b.find(e=>e.textContent.trim()==='Advanced');
        return i && a && i.getBoundingClientRect().top < a.getBoundingClientRect().top;}'''), 'Import must precede Advanced')


def assert_geometry(app):
    require(app.evaluate('''() => {const h=document.querySelector('#onlyoffice-editor-host').getBoundingClientRect();
        const r=document.querySelector('#mcel-onlyoffice-root').getBoundingClientRect();
        return Math.abs(h.top-r.top)<=1 && Math.abs(h.bottom-r.bottom)<=1 && h.width>0;}'''), 'Editor lost viewport height')


def open_advanced(app):
    app.get_by_role('button', name='Advanced', exact=True).click()
    app.locator(NOTEBOOK).wait_for(state='visible', timeout=20000)
    assert_geometry(app)


def ask_ai(app, prompt, *, output_pattern, timeout=180000, progress=None):
    """Send via the actual embedded AI cell and require a NEW output, not prompt text."""
    def step(name):
        if progress is not None:
            progress(name)
    step('locate-ai-cell')
    notebook = app.locator(NOTEBOOK)
    # New browser context has no prior local thread.
    cell = notebook.locator('[data-mcel-chat-cell-type="ai"]').last
    textarea = cell.locator('textarea').first
    textarea.wait_for(state='visible', timeout=20000)
    before = set(notebook.locator('[data-mcel-chat-cell-type="output"]').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.cellId)'))
    step('fill-ai-prompt')
    textarea.fill(prompt)
    step('click-ai-run')
    cell.get_by_role('button', name='Run', exact=True).click()
    step('await-ai-output')
    return app.wait_for_function('''({selector,before,pattern})=>{
        for (const n of document.querySelectorAll(selector+' [data-mcel-chat-cell-type="output"]')) {
            if(before.includes(n.dataset.cellId))continue;
            const text=[...n.querySelectorAll('.chat-output-part')].map(p=>p.textContent).join('\\n');
            if(new RegExp(pattern,'i').test(text))return text;
        } return false;
    }''', arg={'selector': NOTEBOOK, 'before': list(before), 'pattern': output_pattern}, timeout=timeout).json_value()


def extract_formula(text):
    # Never evaluate AI-generated JavaScript or paste arbitrary model text into
    # a spreadsheet. This gate admits precisely the requested local formula.
    matches = re.findall(r'=SUM\(A1:A10\)', text, flags=re.I)
    require(bool(matches), 'AI did not return the required cell formula')
    return matches[0].upper()


NS = {'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def write_fixture(path, numbers):
    # Reuse the existing real-editor lab's valid minimal XLSX package. Only
    # replace its test sheet, so this suite adds no third-party dependency.
    from tools.mcel_onlyoffice_real_editor_lab import create_workbook
    require(len(numbers)==10 and all(type(n) is int for n in numbers), 'Expected ten integers')
    create_workbook(path, 'fixture')
    with zipfile.ZipFile(path) as book:
        parts={name:book.read(name) for name in book.namelist()}
    rows=''.join(f'<row r="{i}"><c r="A{i}"><v>{n}</v></c></row>' for i,n in enumerate(numbers,1))
    parts['xl/worksheets/sheet1.xml']=(f'<worksheet xmlns="{NS["x"]}"><dimension ref="A1:A10"/><sheetViews><sheetView workbookViewId="0"/></sheetViews><sheetFormatPr defaultRowHeight="15"/><sheetData>{rows}</sheetData></worksheet>').encode()
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as book:
        for name,data in parts.items():book.writestr(name,data)


def xlsx_values(raw, *, data_only=False):
    with zipfile.ZipFile(io.BytesIO(raw)) as book:
        workbook=ET.fromstring(book.read('xl/workbook.xml'))
        require([e.get('name') for e in workbook.findall('x:sheets/x:sheet',NS)]==['Sheet1'], 'Expected one Sheet1')
        sheet=ET.fromstring(book.read('xl/worksheets/sheet1.xml'))
        strings=[]
        if 'xl/sharedStrings.xml' in book.namelist():
            strings=[''.join(t.text or '' for t in si.findall('.//x:t',NS)) for si in ET.fromstring(book.read('xl/sharedStrings.xml')).findall('x:si',NS)]
        values={}
        for cell in sheet.findall('.//x:sheetData/x:row/x:c',NS):
            formula=cell.find('x:f',NS)
            value=cell.findtext('x:v',default='',namespaces=NS)
            kind=cell.get('t','n')
            if formula is not None and not data_only: value='='+(formula.text or '')
            elif kind=='inlineStr':value=''.join(t.text or '' for t in cell.findall('.//x:t',NS))
            elif kind=='s' and value:value=strings[int(value)]
            elif kind=='n' and value:
                try:value=float(value)
                except ValueError:pass
            if value!='':values[cell.get('r')]=value
        return values


def read_cells(ctx, base, config, path, *, data_only=False):
    response = ctx.request.get(base + download_path(config, path), timeout=15000)
    require(response.status == 200, 'Authorized workbook download failed')
    values=xlsx_values(response.body(),data_only=data_only)
    allowed={f'A{i}' for i in range(1,12)}|{'B1'}
    require(set(values)<=allowed,'Unexpected populated cells in workbook')
    return [values.get(f'A{i}') for i in range(1,12)]+[values.get('B1')]


def editor_frame(page, app, config):
    frame = app.locator('#onlyoffice-editor-host > iframe').element_handle().content_frame()
    require(frame is not None, 'Isolated iframe missing')
    expected = urlsplit(config['public_url'])
    actual = urlsplit(frame.url)
    require((actual.scheme,actual.netloc,actual.path)==(expected.scheme,expected.netloc,'/mc-editor-host.html'), 'Unexpected live editor origin')
    # wait_active already required the source- and origin-bound document-ready
    # bridge receipt. Do not depend on globals from the standalone Docker lab.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        for child in frame.child_frames:
            if 'spreadsheeteditor' in child.url:
                for selector in ('#editor_sdk', '.sdk-canvas', 'canvas'):
                    canvas = child.locator(selector).first
                    if canvas.count() and canvas.is_visible():
                        box = canvas.bounding_box()
                        if box and box['width']>60 and box['height']>60:
                            return canvas, frame
        page.wait_for_timeout(200)
    raise AssertionError('Real spreadsheet canvas unavailable')


def enter_formula(page, canvas, formula):
    require(formula == FORMULA, 'Unapproved formula')
    box = canvas.bounding_box()
    canvas.click(position={'x': min(140, box['width']//3), 'y': min(120, box['height']//3)})
    page.keyboard.press('Control+Home')
    for _ in range(10):
        page.keyboard.press('ArrowDown')
    page.keyboard.type(formula, delay=35)
    page.keyboard.press('Enter')


def failure_details(exc):
    """Report allowlisted diagnostic categories, never raw browser errors or URLs."""
    details = {'failure_class': type(exc).__name__}
    # All local assertion messages are authored by this test, not server output.
    if isinstance(exc, AssertionError):
        details['failure_reason'] = str(exc)
    else:
        message = str(exc).lower()
        for marker, reason in (
            ('target page, context or browser has been closed', 'browser-target-closed'),
            ('execution context was destroyed', 'execution-context-destroyed'),
            ('frame was detached', 'frame-detached'),
            ('strict mode violation', 'ambiguous-locator'),
            ('timeout', 'timeout'),
            ('disposable-session-revocation-unverified', 'disposable-session-revocation-unverified'),
            ('failed to fetch', 'browser-fetch-failed'),
        ):
            if marker in message:
                details['failure_reason'] = reason
                break
        else:
            details['failure_reason'] = 'unclassified-exception'
    return details


@contextmanager
def reported_cleanup(result, name, close):
    """Do not let a cleanup exception replace the original test exception."""
    primary = None
    try:
        yield
    except BaseException as exc:
        primary = exc
        raise
    finally:
        cleanup = result.setdefault('cleanup', {})
        try:
            close()
        except Exception as exc:
            cleanup[name] = {'status': 'FAIL', **failure_details(exc)}
            print('ONLYOFFICE_CLEANUP', name, 'FAIL', cleanup[name]['failure_reason'], flush=True)
            if primary is None:
                result['failed_stage'] = name
                raise
        else:
            cleanup[name] = {'status': 'PASS'}
            print('ONLYOFFICE_CLEANUP', name, 'PASS', flush=True)


def run(base=ORIGIN, report=DEFAULT_REPORT, *, authorize_disposable_writes=False):
    base = validate_host(base)
    if not authorize_disposable_writes:
        raise ValueError('Explicit --authorize-disposable-writes required (creates two workbooks and runs AI)')
    from playwright.sync_api import sync_playwright
    report = Path(report)
    suffix = uuid.uuid4().hex[:12]
    first, second = f'MCEL-OO-blank-{suffix}.xlsx', f'MCEL-OO-ai-{suffix}.xlsx'
    numbers = [3, 5, 8, 13, 21, 34, 55, 89, 144, 233]
    result = {'schema':'mcel.onlyoffice.full-functional.v1', 'status':'FAIL', 'scope':'live-dev-real-editor-real-configured-ai',
              'workbooks':[first, second], 'checks':[], 'failed_stage':'setup', 'ai':{}}
    def stage(name, fn):
        result['failed_stage'] = name
        print('ONLYOFFICE_FUNCTIONAL', name, 'BEGIN', flush=True)
        value = fn()
        result['checks'].append(name)
        result['failed_stage'] = None
        print('ONLYOFFICE_FUNCTIONAL', name, 'PASS', flush=True)
        return value
    try:
        with tempfile.TemporaryDirectory(prefix='mcel-oo-functional-') as tmp, sync_playwright() as pw:
            path = Path(tmp)/second
            write_fixture(path,numbers)
            browser, _ = launch_semantic_browser(pw)
            with reported_cleanup(result, 'browser-close', browser.close):
                result['failed_stage'] = 'authenticate-disposable-session'
                ctx = new_context(browser,base,viewport={'width':1440,'height':900})
                with reported_cleanup(result, 'session-revocation', ctx.close):
                    result['failed_stage'] = 'application-page-setup'
                    page = ctx.new_page()
                    # A busy local AI must not fall back to a paid remote worker.
                    # Do not alter settings or approve remote execution.
                    page.route('**/api/applications/chat-console/ai/remote-overflow/**',
                               lambda route: route.abort() if any(x in route.request.url for x in ('hub-submit','requester-authorize')) else route.continue_())
                    app = stage('canonical-mcel-route',lambda:open_app(page,base))
                    stage('current-controls',lambda:assert_surface(app))
                    stage('full-height',lambda:assert_geometry(app))
                    a = stage('create-blank-auto-open',lambda:create(page,app,first))
                    stage('blank-xlsx-bytes',lambda:require(read_cells(ctx,base,a,first)==[None]*12,'New workbook not blank'))
                    stage('real-editor-ready',lambda:editor_frame(page,app,a))
                    b = stage('import-and-auto-open',lambda:import_book(page,app,path))
                    stage('separate-workbook-keys',lambda:require(a['config']['document']['key']!=b['config']['document']['key'],'Workbook keys collided'))
                    stage('imported-xlsx-bytes',lambda:require(read_cells(ctx,base,b,second)==numbers+[None,None],'Imported workbook contents changed'))
                    # Select both entries through visible library controls.
                    a = stage('click-to-reopen-blank',lambda:select_book(page,app,first))
                    b = stage('click-to-reopen-import',lambda:select_book(page,app,second))
                    canvas, docs = stage('real-imported-editor-ready',lambda:editor_frame(page,app,b))
                    stage('advanced-chat-mount',lambda:open_advanced(app))
                    stage('advanced-preserves-editor',lambda:require(docs==app.locator('#onlyoffice-editor-host > iframe').element_handle().content_frame(),'Editor remounted'))
                    prompt = 'In a spreadsheet A1 through A10 contain numbers. Give ONLY the Excel formula for A11 that sums that range, not a numeric answer. Do not use tools or run code.'
                    def real_ai():
                        diag = result['ai']
                        diag.update(request_observed=False, response_observed=False, output_observed=False)
                        def progress(name):
                            diag['step'] = name
                            print('ONLYOFFICE_AI', name, 'BEGIN', flush=True)
                        def matches(req):
                            return (urlsplit(req.url).path == '/api/applications/chat-console/cell/evaluate'
                                    and req.method == 'POST')
                        def on_request(req):
                            if matches(req):
                                diag['request_observed'] = True
                                try:
                                    cell = req.post_data_json.get('cell', {})
                                    diag['prompt_matches'] = cell.get('source') == prompt and cell.get('type') == 'ai'
                                except Exception:
                                    diag['prompt_matches'] = False
                                print('ONLYOFFICE_AI request-observed', flush=True)
                        def on_response(response):
                            if matches(response.request):
                                diag['response_observed'] = True
                                diag['response_http'] = response.status
                                print('ONLYOFFICE_AI response-http', response.status, flush=True)
                        page.on('request', on_request)
                        page.on('response', on_response)
                        try:
                            # Any new nonempty output is sufficient for this wait; the
                            # next stage separately validates its exact formula.
                            text = ask_ai(app, prompt, output_pattern=r'\S', progress=progress)
                            diag['output_observed'] = True
                            require(diag['request_observed'] and diag.get('prompt_matches'),
                                    'Expected real local AI request missing')
                            require(diag['response_observed'] and 200 <= diag['response_http'] < 300,
                                    'Expected successful local AI HTTP response missing')
                            progress('output-received')
                            return text
                        finally:
                            page.remove_listener('request', on_request)
                            page.remove_listener('response', on_response)
                    text = stage('real-ai-notebook-response',real_ai)
                    formula = stage('validate-ai-formula',lambda:extract_formula(text))
                    stage('apply-ai-formula-in-real-editor',lambda:enter_formula(page,canvas,formula))
                    # The UI Save Now control was removed. Use the existing
                    # semantic intent to request a journal-authorized save;
                    # command acceptance alone is NOT durable save evidence.
                    def save():
                        # Allow the editor's collaborative change to reach the
                        # server before requesting the journal-authorized save.
                        page.wait_for_timeout(3000)
                        receipt = app.evaluate("async()=>await __mcelWorkbench.app.execute('intent:save-now')")
                        require(receipt.get('disposition')=='completed','Journal force-save refused')
                    stage('request-journal-save',save)
                    def persisted():
                        deadline=time.monotonic()+120
                        while time.monotonic()<deadline:
                            cells=read_cells(ctx,base,b,second)
                            if cells[:11]==numbers+[formula]:
                                cached=read_cells(ctx,base,b,second,data_only=True)
                                if cached[10]==sum(numbers):return
                            page.wait_for_timeout(1000)
                        raise AssertionError('Formula or calculated value missing from persisted XLSX')
                    stage('persisted-formula-and-result',persisted)
                    a = stage('switch-back-to-independent-blank',lambda:select_book(page,app,first))
                    stage('blank-unchanged',lambda:require(read_cells(ctx,base,a,first)==[None]*12,'Other workbook was modified'))
                    b = stage('reopen-saved-ai-workbook',lambda:select_book(page,app,second))
                    stage('reopen-persistence',lambda:require(read_cells(ctx,base,b,second)[:11]==numbers+[formula],'Formula lost on reopen'))
        result['status']='PASS_LIVE_FULL_FUNCTIONAL'
        result['failed_stage']=None
    except Exception as exc:
        if result['failed_stage'] is None:
            result['failed_stage'] = 'test-infrastructure'
        result.update(failure_details(exc))
        print('ONLYOFFICE_FUNCTIONAL', result['failed_stage'], 'FAIL', result['failure_reason'], flush=True)
    finally:
        report.parent.mkdir(parents=True,exist_ok=True)
        report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host-url',default=ORIGIN)
    parser.add_argument('--authorize-disposable-writes',action='store_true')
    parser.add_argument('--report',type=Path,default=DEFAULT_REPORT)
    args=parser.parse_args(argv)
    try:result=run(args.host_url,args.report,authorize_disposable_writes=args.authorize_disposable_writes)
    except ValueError as exc:parser.error(str(exc))
    print('ONLYOFFICE_FUNCTIONAL_OVERALL',result['status'],'failed_stage=',result['failed_stage'],'report=',args.report,flush=True)
    return 0 if result['status']=='PASS_LIVE_FULL_FUNCTIONAL' else 1


if __name__=='__main__':raise SystemExit(main())
