"""Independent layout oracle for the standalone and embedded Chat Console.

Migration step 1: observe geometry and fail on the known raw-embed defects.
This module does NOT migrate the Chat Console, change its CSS, execute cells,
contact providers, or rewrite notebooks. Fixture samples exercise the actual
checked-in legacy stylesheet and representative populated notebook structures.
Live observation is opt-in and reports INCOMPLETE when content is missing.

Run: python -u -m tools.mcel_chat_console_layout_oracle --mode baseline
     python -u -m tools.mcel_chat_console_layout_oracle --mode hosted --host-url http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
STYLESHEET = ROOT / 'main_computer/web/applications/styles/chat-console.css'
CALCULATOR_STYLESHEET = ROOT / 'main_computer/web/applications/styles/calculator.css'
MCEL_EXPERT_STYLESHEET = ROOT / 'main_computer/web/applications/styles/mcel-semantic-expert.css'
SEMANTIC_CSS = ROOT / 'main_computer/web/applications/styles/mcel-semantic-chat-console.css'
SEMANTIC_RUNTIME = ROOT / 'main_computer/web/applications/scripts/mcel-semantic-chat-console.js'
SEMANTIC_CONTRACT = 'mcel.chat-console.composition.v1'
DEFAULT_REPORT = ROOT / 'runtime/reports/mcel-chat-console-layout-oracle/report.json'
VIEWPORTS = (320, 390, 560, 1100, 1272)
EMBED_WIDTHS = (320, 390, 480, 500, 560, 680)

# The desired oracle, independent of the current HTML/CSS and layout planner.
# Thresholds are deliberately modest: they catch miscomposition, not aesthetics.
ORACLE = {
    'semantics': ('thread-navigation', 'active-thread', 'notebook', 'typed-cell',
                  'cell-type-choices', 'editable-input', 'execution-controls',
                  'thinking-state', 'output'),
    'contexts': ('standalone', 'embedded'),
    'min_editable_width': 160,
    'min_thinking_card_width': 130,
    'min_control_height': 23,
    'max_horizontal_leak_px': 2,
    'max_unintended_overlap_px': 2,
}

# Data-based selectors permit the future MCEL renderer to expose its own semantic
# regions. The fallback selectors observe the incumbent, not define the oracle.
SELECTORS = {
    'shell': '[data-chat-console-embedded-shell], [data-chat-semantic-shell], .chat-console-shell',
    'notebook': '[data-chat-console-embedded-notebook], [data-chat-region="notebook"], .chat-console-notebook',
    'navigation': '[data-chat-region="threads"], .chat-thread-rail',
    'workspace': '[data-chat-region="workspace"], .chat-thread-workspace',
    'cell': '[data-chat-cell], .chat-cell',
    'tabs': '[data-chat-cell-tabs], .chat-cell-tabs',
    'edit': '[data-chat-cell-input] textarea, .chat-cell textarea, textarea[data-chat-editor]',
    'controls': '[data-chat-cell-controls], .chat-cell-controls',
    'thinking': '[data-chat-thinking], .chat-thinking-frame',
    'card': '[data-chat-thinking-card], .chat-thinking-card',
    'output': '[data-chat-cell-output], .chat-console-cell-output',
}

OBSERVE_JS = r'''(selectors) => {
  const scope = document.querySelector('[data-chat-oracle-root]');
  const one=(sel,root=scope)=>root?.querySelector(sel)||null;
  const all=(sel,root=scope)=>[...(root?.querySelectorAll(sel)||[])];
  const visible=el=>!!el && !el.hidden && getComputedStyle(el).display!=='none' &&
    getComputedStyle(el).visibility!=='hidden' && el.getClientRects().length>0;
  const box=el=>{if(!el || !visible(el))return null;const r=el.getBoundingClientRect();
    return {x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,bottom:r.bottom};};
  const groups=(sel,root=scope)=>all(sel,root).filter(visible).map(box);
  const cells=all(selectors.cell).filter(visible);
  const tabs=all(selectors.tabs).filter(visible);
  const inputs=all(selectors.edit).filter(visible);
  const controls=all(selectors.controls).filter(visible);
  const frames=all(selectors.thinking).filter(visible);
  const cards=frames.flatMap(f=>all(selectors.card,f).filter(visible));
  const notebook=one(selectors.notebook),shell=one(selectors.shell);
  const rootBox=box(scope);
  return {
    root:rootBox,shell:box(shell),notebook:box(notebook),workspace:box(one(selectors.workspace)),
    nav:box(one(selectors.navigation)),cells:cells.map(box),
    tabs:tabs.map(box),tabButtons:tabs.map(tab=>all('button,[role="tab"]',tab).filter(visible).map(box)),
    editorInputs:inputs.map(box),controlRows:controls.map(box),
    controlButtons:controls.flatMap(row=>all('button',row).filter(visible).map(box)),
    thinking:frames.map(box),thinkingCards:cards.map(box),
    outputs:groups(selectors.output),
    ragControls:groups('.chat-rag-at-controls,[data-chat-rag-controls]'),
    interactive:all('button,input,textarea,select,[role="tab"]')
      .filter(el=>visible(el)&&!el.matches('input[type="hidden"]'))
      .map(el=>({name:el.getAttribute('aria-label')||el.textContent?.trim().slice(0,25)||el.tagName,
                box:box(el)})),
    scrolledX: scope.scrollWidth>scope.clientWidth+2,
    bodyScrolledX: document.documentElement.scrollWidth>innerWidth+2,
    rootWidth:scope.clientWidth,
    semanticMarker:!!scope?.hasAttribute('data-mcel-semantic-chat'),
    semanticContract:scope?.getAttribute('data-mcel-chat-contract')||'',
    semanticRegions:all('[data-mcel-chat-role]').map(el=>el.getAttribute('data-mcel-chat-role'))
  };
}'''


def _outside(inner: dict | None, outer: dict | None, tol: float = 2) -> bool:
    return not inner or not outer or inner['x'] < outer['x'] - tol or inner['right'] > outer['right'] + tol


def _intersects(a: dict, b: dict, tolerance: float = 2) -> bool:
    return min(a['right'], b['right']) - max(a['x'], b['x']) > tolerance and (
        min(a['bottom'], b['bottom']) - max(a['y'], b['y']) > tolerance)


def evaluate_layout(context: str, observed: dict, *, populated: bool = True,
                    require_semantic: bool = False) -> dict:
    """Pure expectation evaluator: never asks the compositor what it intended."""
    if context not in ORACLE['contexts']:
        raise ValueError('Unknown Chat Console context: ' + context)
    fail=[]
    root=observed.get('root')
    shell=observed.get('shell')
    notebook=observed.get('notebook')
    cells=observed.get('cells') or []
    tabs=observed.get('tabs') or []
    tab_buttons=observed.get('tabButtons') or []
    inputs=observed.get('editorInputs') or []
    controls=observed.get('controlRows') or []
    buttons=observed.get('controlButtons') or []
    output=observed.get('outputs') or []
    thinking=observed.get('thinking') or []
    cards=observed.get('thinkingCards') or []
    if require_semantic:
        if not observed.get('semanticMarker') or observed.get('semanticContract') != SEMANTIC_CONTRACT:
            fail.append('raw-chat-renderer-not-migrated')
        required_roles={'shell','thread-layout','workspace','notebook'}
        if populated:
            required_roles.update({'cell','cell-choices','cell-editor','cell-actions',
                                   'thinking','thinking-collection','thinking-card','output'})
        if not required_roles.issubset(set(observed.get('semanticRegions') or [])):
            fail.append('semantic-region-coverage-missing')
    if not root:fail.append('missing-mounted-root')
    if not shell:fail.append('missing-chat-shell')
    if not notebook:fail.append('missing-notebook-workspace')
    if root and shell and _outside(shell, root):fail.append('shell-escapes-host')
    if shell and notebook and _outside(notebook,shell):fail.append('notebook-escapes-shell')
    if context=='standalone':
        if not observed.get('nav'):fail.append('missing-thread-navigation')
        if not observed.get('workspace'):fail.append('missing-active-thread')
    if observed.get('scrolledX') or observed.get('bodyScrolledX'):
        fail.append('horizontal-page-overflow')
    if populated:
        if len(cells)<2:fail.append('missing-populated-cells')
        if not tabs or not any(group for group in tab_buttons):fail.append('missing-cell-type-tabs')
        if not inputs:fail.append('missing-editable-input')
        if not controls or not buttons:fail.append('missing-execution-controls')
        if not output:fail.append('missing-output-cell')
        if not thinking or len(cards)<2:fail.append('missing-expanded-thinking-cards')
    # Geometry stays inside the mounted app, not merely inside the browser viewport.
    for label, rects in (('cell', cells),('tab', tabs),('tab-button',[b for group in tab_buttons for b in group]),
                         ('edit',inputs),('control',buttons),('thinking-card',cards),
                         ('output',output),('rag-controls',observed.get('ragControls') or [])):
        if any(_outside(r,root) for r in rects):fail.append(label+'-escapes-host')
    # A horizontal choice strip must wrap or reflow; no horizontal clipping or
    # scrolling it out of the embedded panel to make the buttons inaccessible.
    for group in tab_buttons:
        if any(_outside(b,notebook) for b in group):
            fail.append('cell-type-choices-overflow-notebook')
    for r in inputs:
        if r['w'] < min(ORACLE['min_editable_width'], max(90,(root or {}).get('w',0)*.60)):
            fail.append('editor-unreadably-narrow')
    for r in cards:
        if r['w'] < min(ORACLE['min_thinking_card_width'], max(90,(root or {}).get('w',0)*.48)):
            fail.append('thinking-card-unreadably-narrow')
    for r in buttons:
        if r['h'] < ORACLE['min_control_height']:fail.append('control-too-small')
    # Controls may wrap onto another row, but must never overlap each other.
    for i,a in enumerate(buttons):
        if any(_intersects(a,b) for b in buttons[i+1:]):
            fail.append('action-buttons-overlap');break
    # The notebook's cells must remain ordered and not occlude one another.
    for a,b in zip(cells,cells[1:]):
        if a['bottom'] > b['y']+ORACLE['max_unintended_overlap_px']:
            fail.append('notebook-cells-overlap');break
    return {'status':'PASS' if not fail else 'FAIL', 'failures':sorted(set(fail)),
            'context':context,'populated':populated,
            'measured':{'host_width':round(root['w'],1) if root else None,
                        'cell_count':len(cells),'choice_count':sum(map(len,tab_buttons)),
                        'thinking_card_count':len(cards),'output_count':len(output),
                        'semantic_marker_present':bool(observed.get('semanticMarker')),
                        'semantic_contract':observed.get('semanticContract','')}}


def _sample_cell(kind='ai', *, with_thinking=False) -> str:
    types=['AI','JS','Python','BASIC','Calc','Term','Math','Note']
    tabs=''.join('<button class="chat-cell-tab">'+t+'</button>' for t in types)
    if with_thinking:tabs+='<button class="chat-cell-tab chat-cell-thinking-tab">Thinking</button>'
    rag='''<div class="chat-rag-at-controls"><label class="chat-rag-at-toggle"><input type="checkbox">RAG-AT</label>
        <label class="chat-mount-plugin-toggle"><input type="checkbox">Remote worker when local AI is busy for this chat</label>
        <label class="chat-rag-at-field">Thinking <select><option>low</option></select></label><span>off</span></div>'''
    monitor='''<details class="chat-thinking-frame" open><summary>Thinking node/leaf waiting</summary>
        <div class="chat-thinking-grid"><div class="chat-thinking-card">CURRENT MESSAGE <p>hi</p></div>
        <div class="chat-thinking-card">LOCAL AI CAPACITY <p>local AI available</p></div>
        <div class="chat-thinking-card">WAITING FOR ACTIVITY <p>Request is running</p></div>
        <div class="chat-thinking-card">MODELS IN USE <p>No models currently loaded</p></div></div></details>'''
    controls='<div class="chat-cell-controls">'+''.join('<button>'+t+'</button>' for t in ('Run','Add attachment','Duplicate','Up','Down','Delete'))+'</div>'
    return f'<article class="chat-cell chat-cell-{kind} chat-console-cell-input"><div class="chat-cell-tabs">{tabs}</div>' + \
        f'<textarea aria-label="Cell editor">Sample input</textarea>{rag if kind=="ai" else ""}{monitor if with_thinking else ""}{controls}</article>'


def _sample_notebook() -> str:
    return '''<div class="chat-console-notebook" id="chat-console-notebook" data-chat-console-embedded-notebook>''' + \
        '<div class="chat-console-insert-strip"><button>+ Insert</button></div>' + \
        _sample_cell(with_thinking=True) + \
        '''<article class="chat-cell chat-cell-output chat-console-cell-output chat-console-output-indent">
        <div class="chat-cell-tabs"><button class="chat-cell-tab">Out</button></div>
        <div class="chat-output-part">Sample output that should remain contained in the notebook.</div></article>''' + \
        '<div class="chat-console-insert-strip"><button>+ Insert</button></div>' + _sample_cell('javascript') + '</div>'


def _sample_html(context: str, width: int) -> str:
    rail='''<aside class="chat-thread-rail"><div class="chat-thread-rail-header"><strong>Threads</strong><button>New chat</button></div>
        <label>Search chats</label><input type="search"/><div class="chat-thread-list">Saved thread</div>
        <div class="chat-thread-actions"><button>Rename</button><button>Clone</button><button>Archive</button></div></aside>'''
    if context=='embedded':
        shell='chat-console-shell chat-console-embedded-layout-compact chat-console-embedded-no-thread-rail chat-console-embedded-no-current-thread-bar'
        host=f'data-chat-console-embed="calculator" data-mcel-embedded-chat style="width:{width}px;display:block;padding:0"'
        attrs='data-chat-console-embedded-shell data-chat-console-layout="compact"'
        rail=''
    else:
        shell='chat-console-shell'
        host=f'style="width:{width}px;display:block;padding:0"'
        attrs=''
    root_classes='chat-console-app calculator-chat-pane' if context=='embedded' else 'chat-console-app'
    return f'''<!doctype html><html><head><meta charset="utf-8"><style>
        :root{{--line:#3a4b55;--ink:#f7f3e8;--gold:#ffd366;--muted:#b6cbd6;--sky:#65acd6;--mint:#7ee2af;--coral:#d26a61}}
        *{{box-sizing:border-box}}html,body{{margin:0;padding:0;background:#101820;color:#f7f3e8;font:13px Arial,sans-serif}}
        [data-chat-oracle-root]{{margin:8px;max-width:none;min-width:0}}
        .chat-console-app{{display:block;padding:0}}
        button{{border:1px solid var(--line);border-radius:8px;background:var(--gold);color:#090806;
          font-weight:800;padding:10px 14px;cursor:pointer}}</style></head><body>
        <main data-chat-oracle-root id="chat-console-app" {host} class="{root_classes}"><div class="{shell}" {attrs}>
        <header class="chat-console-header"><strong>{'Calculator Chat' if context=='embedded' else 'Chat Console'}</strong>
        <span>Embedded notebook cells use the shared Chat Console renderer.</span></header>
        <div class="chat-console-thread-layout">{rail}<section class="chat-thread-workspace">
        <div class="chat-thread-current-bar" {'hidden' if context=='embedded' else ''}>Current thread</div>
        {_sample_notebook()}</section></div></div></main></body></html>'''


def _launch(pw):
    executable=os.environ.get('MCEL_CHROMIUM_EXECUTABLE')
    if not executable and os.name!='nt':executable='/usr/bin/chromium'
    kwargs={'headless':True}
    if executable and Path(executable).exists():kwargs['executable_path']=executable
    if os.name!='nt':kwargs['args']=['--no-sandbox']
    return pw.chromium.launch(**kwargs)


def fixture_sample(browser, context: str, width: int, *, css: str | None = None,
                   repair_css: str | None = None, screenshot: Path | None = None,
                   semantic: bool = False) -> dict:
    # Keep the browser wide for embedded samples: container width, not window
    # width, is the governing constraint for application-within-application UI.
    viewport=1272 if context=='embedded' else max(380,width+24)
    page=browser.new_page(viewport={'width':viewport,'height':900},device_scale_factor=1)
    try:
        page.route('**/*',lambda route:route.abort())
        page.set_content(_sample_html(context,width), wait_until='load')
        page.add_style_tag(content=STYLESHEET.read_text(encoding='utf-8') if css is None else css)
        if context=='embedded':
            page.add_style_tag(content=CALCULATOR_STYLESHEET.read_text(encoding='utf-8'))
            page.add_style_tag(content=MCEL_EXPERT_STYLESHEET.read_text(encoding='utf-8'))
        if repair_css:page.add_style_tag(content=repair_css)
        if semantic:
            page.add_style_tag(content=SEMANTIC_CSS.read_text(encoding='utf-8'))
            page.add_script_tag(content=SEMANTIC_RUNTIME.read_text(encoding='utf-8'))
            mounted=page.evaluate('window.MainComputerMCELChatConsole.compose(document.querySelector("[data-chat-oracle-root]"))')
            if not mounted:raise RuntimeError('Semantic Chat Console did not compose the fixture')
        page.evaluate('document.fonts.ready')
        observed=page.evaluate(OBSERVE_JS,SELECTORS)
        result=evaluate_layout(context,observed,require_semantic=semantic)
        if screenshot:
            screenshot.parent.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(screenshot),full_page=True)
            result['screenshot']=str(screenshot)
        return result
    finally:page.close()



def fixture_resize_sequence(browser, context: str, widths: tuple[int, ...],
                            *, repair_css: str | None = None,
                            semantic: bool = False) -> dict:
    """Resize one already-mounted notebook; the host must not recompile it."""
    if not widths:
        raise ValueError('Resize sequence must contain at least one width')
    viewport=1272 if context=='embedded' else max(380,widths[0]+24)
    page=browser.new_page(viewport={'width':viewport,'height':900},device_scale_factor=1)
    try:
        page.route('**/*',lambda route:route.abort())
        page.set_content(_sample_html(context,widths[0]),wait_until='load')
        page.add_style_tag(content=STYLESHEET.read_text(encoding='utf-8'))
        if context=='embedded':
            page.add_style_tag(content=CALCULATOR_STYLESHEET.read_text(encoding='utf-8'))
            page.add_style_tag(content=MCEL_EXPERT_STYLESHEET.read_text(encoding='utf-8'))
        if repair_css:page.add_style_tag(content=repair_css)
        if semantic:
            page.add_style_tag(content=SEMANTIC_CSS.read_text(encoding='utf-8'))
            page.add_script_tag(content=SEMANTIC_RUNTIME.read_text(encoding='utf-8'))
            mounted=page.evaluate('window.MainComputerMCELChatConsole.compose(document.querySelector("[data-chat-oracle-root]"))')
            if not mounted:raise RuntimeError('Semantic Chat Console did not compose the fixture')
        steps=[]
        for width in widths:
            if context=='standalone':page.set_viewport_size({'width':max(380,width+24),'height':900})
            page.evaluate('(width)=>{document.querySelector("[data-chat-oracle-root]").style.width=width+"px"}',width)
            value=evaluate_layout(context,page.evaluate(OBSERVE_JS,SELECTORS),require_semantic=semantic)
            steps.append({'width':width,'status':value['status'],'failures':value['failures']})
        fails=sorted({failure for step in steps for failure in step['failures']})
        return {'status':'PASS' if not fails else 'FAIL','context':context,
                'width':' -> '.join(map(str,widths)), 'failures':fails,'steps':steps}
    finally:page.close()


def observe_hosted(browser, url: str, context: str, *, screenshot: Path | None = None,
                   require_semantic: bool = False, structure_only: bool = False) -> dict:
    # Hosted observation only switches to the Help view; no cells, threads, or effects are created.
    from playwright.sync_api import TimeoutError as BrowserTimeout

    page=browser.new_page(viewport={'width':1272,'height':900},device_scale_factor=1)
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)[:300]))
    try:
        response=page.goto(url,wait_until='domcontentloaded',timeout=20000)
        if context=='embedded':
            # The semantic Calculator initially opens in Basic mode. The chat
            # is mounted but deliberately hidden until Help is selected.
            help_tab=page.locator('[data-mcel-mode-tabs] button[data-value="help"]')
            if help_tab.count():help_tab.first.click(timeout=5000)
        root_selector='[data-chat-console-embed="calculator"]' if context=='embedded' else '.chat-console-app'
        try:
            page.locator(root_selector).first.wait_for(state='attached',timeout=8000)
        except BrowserTimeout:
            pass
        # Wait for the mounted notebook instead of sampling at a fixed 650 ms.
        if page.locator(root_selector).count():
            try:
                page.wait_for_function('''selector => {
                  const host=document.querySelector(selector);
                  return !!(host && host.querySelector('.chat-console-notebook'));
                }''',arg=root_selector,timeout=3500)
            except BrowserTimeout:
                pass
        page.evaluate('''selector=>{
          for(const node of document.querySelectorAll('[data-chat-oracle-root]'))
            node.removeAttribute('data-chat-oracle-root');
          const root=document.querySelector(selector);
          if(root)root.setAttribute('data-chat-oracle-root','');
        }''',root_selector)
        observed=page.evaluate(OBSERVE_JS,SELECTORS)
        mounted=bool(observed.get('root') and observed.get('shell') and observed.get('notebook'))
        populated=bool(len(observed.get('cells') or []) >= 2 and
                       observed.get('thinkingCards') and observed.get('outputs'))
        structural=evaluate_layout(context,observed,populated=False,require_semantic=require_semantic)
        result=(evaluate_layout(context,observed,populated=True,require_semantic=require_semantic)
                if populated else structural.copy())
        result['structural_gate']={'status':structural['status'],'failures':structural['failures']}
        result['populated_witness']={'status':'PRESENT' if populated else 'UNAVAILABLE',
             'cells':len(observed.get('cells') or []),
             'outputs':len(observed.get('outputs') or []),
             'thinking_cards':len(observed.get('thinkingCards') or [])}
        # Structural failures are real failures. Missing populated content is
        # INCOMPLETE, not evidence of a bad layout or a passing layout.
        if not mounted:
            result['status']='INCOMPLETE'
            result['failures']=sorted(set(result['failures'])|{'chat-mount-not-observed'})
        elif structural['status']!='PASS':
            result['status']='FAIL'
        elif not populated and not structure_only:
            result['status']='INCOMPLETE'
            result['failures']=['populated-chat-witness-unavailable']
        else:
            result['status']='PASS'
            result['failures']=[]
        result['diagnostics']=page.evaluate('''() => ({
          title:document.title,
          calculator_status:(document.querySelector('#mcel-expanded-diagnostic')?.textContent||'').trim().slice(0,300),
          embedded_slot_count:document.querySelectorAll('[data-mcel-embedded-chat]').length,
          help_tab_count:document.querySelectorAll('[data-mcel-mode-tabs] button[data-value="help"]').length,
          embedded_mount_count:document.querySelectorAll('[data-chat-console-embed="calculator"]').length,
          shared_chat_api:!!window.MainComputerChatConsole?.mountEmbedded,
          semantic_composer_loaded:!!window.MainComputerMCELChatConsole?.compose,
          composer_contract:window.MainComputerMCELChatConsole?.contract?.id||null
        })''')
        result['page_errors']=errors
        result['route_http']=response.status if response else None
        result['url']=url
        if screenshot:
            screenshot.parent.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(screenshot),full_page=True)
            result['screenshot']=str(screenshot)
        return result
    finally:page.close()


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('baseline','candidate','hosted'),default='baseline')
    parser.add_argument('--host-url',default='http://127.0.0.1:8765')
    parser.add_argument('--report',type=Path,default=DEFAULT_REPORT)
    parser.add_argument('--screenshots',action='store_true')
    parser.add_argument('--structure-only',action='store_true',
                        help='Hosted only: verify real semantic mount without needing populated AI/output cells')
    parser.add_argument('--require-semantic',action='store_true',
                        help='Post-migration gate: reject a raw legacy Chat Console render')
    args=parser.parse_args(argv)
    if args.mode=='hosted':
        parsed=urlparse(args.host_url)
        if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost','::1'} or parsed.username or parsed.password:
            parser.error('--host-url must refer to a loopback HTTP host')
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print('MCEL_CHAT_LAYOUT_ORACLE INCOMPLETE: Playwright required')
        return 2
    results=[]
    with sync_playwright() as pw:
        browser=_launch(pw)
        try:
            if args.mode in ('baseline','candidate'):
                for context,widths in [('standalone',VIEWPORTS),('embedded',EMBED_WIDTHS)]:
                    for width in widths:
                        shot=args.report.parent/'screenshots'/f'{context}-{width}.png' if args.screenshots else None
                        value=fixture_sample(browser,context,width,screenshot=shot,semantic=args.mode=='candidate')
                        results.append({'context':context,'width':width,**value})
                results.append(fixture_resize_sequence(browser,'embedded',(680,500,390,680),semantic=args.mode=='candidate'))
            else:
                for context,route in [('standalone','/applications/chat-console'),
                                      ('embedded','/applications/calculator')]:
                    shot=args.report.parent/'screenshots'/f'hosted-{context}.png' if args.screenshots else None
                    value=observe_hosted(browser,args.host_url.rstrip('/')+route,context,screenshot=shot,
                                         require_semantic=args.require_semantic,structure_only=args.structure_only)
                    results.append({'context':context,'width':value['measured']['host_width'],**value})
        finally:browser.close()
    counts={key:sum(x['status']==key for x in results) for key in ('PASS','FAIL','INCOMPLETE')}
    status='FAIL' if counts['FAIL'] else 'INCOMPLETE' if counts['INCOMPLETE'] else 'PASS'
    fingerprint_paths=(STYLESHEET,CALCULATOR_STYLESHEET,MCEL_EXPERT_STYLESHEET,SEMANTIC_CSS,SEMANTIC_RUNTIME,
        ROOT/'main_computer/web/applications.html',
        ROOT/'main_computer/web/applications/apps/chat-console.html',
        ROOT/'main_computer/web/applications/scripts/chat-console.js',
        ROOT/'mcel_apps/calculator/application.js')
    source_fingerprints={str(path.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in fingerprint_paths if path.is_file()}
    report={'schema':'mcel.chat-console-layout-oracle.v1','mode':args.mode,'status':status,
            'source':('legacy Chat Console stylesheet + representative populated notebook' if args.mode=='baseline' else 'MCEL semantic Chat Console composition + populated notebook fixture' if args.mode=='candidate' else 'passive hosted DOM'),
            'oracle':ORACLE,'counts':counts,'cases':results,
            'source_fingerprints':source_fingerprints,'requires_semantic':args.require_semantic}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    for c in results:
        failures=','.join(c['failures'][:3]);print(f"CHAT_LAYOUT_ORACLE {c['context']} {c['width']}px {c['status']} {failures}")
    print(f'MCEL_CHAT_CONSOLE_LAYOUT_ORACLE {status} (pass={counts["PASS"]}, fail={counts["FAIL"]}, incomplete={counts["INCOMPLETE"]})')
    print('  report:',args.report)
    if args.mode=='baseline':print('  baseline failures are expected until the Chat Console renderer is migrated')
    return 0 if status=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
