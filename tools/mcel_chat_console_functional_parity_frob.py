"""Bounded incumbent-versus-semantic Chat Console functional parity acceptance.

The *same* checked-in notebook/runtime code is exercised twice, with and without
MCEL cell-frame composition. Real JS workers and browser storage are used;
external network, native model/terminal services, and production data are not.
No route promotion or consequential host operations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer/web/applications/scripts"
CHAT_TEMPLATE = ROOT / "main_computer/web/applications/apps/chat-console.html"
SEMANTIC_SOURCE = SCRIPTS / "mcel-semantic-chat-console.js"
REPORT = ROOT / "runtime/reports/mcel-chat-console-functional-parity/report.json"
FEATURES = (
    "notebook-mount", "cell-types", "draft-persistence", "cell-insert",
    "cell-duplicate", "cell-reorder", "cell-delete", "javascript-worker",
    "output-lineage", "output-variants", "shared-variables", "attachments",
    "thread-create", "thread-switch", "thread-search", "thread-rename",
    "thread-clone", "thread-archive", "thread-persistence",
    "thinking-preferences", "terminal-review", "embedded-mount",
    "embedded-context", "embedded-extension", "semantic-composition",
    "network-boundary",
)
REQUIRED_SHARED_CASE_IDS = frozenset({
    "actual-isolated-js-worker", "all-incumbent-type-tabs", "archive-record-not-deleted",
    "clone-separate-id-and-content", "delete-only-target", "drafts-remain-independent",
    "duplicate-independent", "earlier-result-selectable", "embedded-edit-keeps-thread-source",
    "embedded-mount-read-only", "exact-thread-selected", "host-notebook-linked-to-thread",
    "host-plugin-option-persisted", "host-provided-toggle-present", "initial-notebook",
    "insert-new-ai", "latest-result-selectable", "new-chat-independent",
    "only-attachment-request", "output-linked-to-exact-source", "rag-at-setting-persisted",
    "remove-exact-attachment", "rename-keeps-source", "restores-selected-source",
    "search-includes-saved-code", "shared-contract-mounts", "shared-variable-from-js",
    "source-saved-on-edit", "stage-does-not-execute", "store-retains-unarchived-and-archived",
    "terminal-stage-visible", "thinking-selection-persisted", "two-variants-retained",
    "up-preserves-both-drafts", "upload-metadata-persisted",
    "notebook-no-js-errors", "threads-no-js-errors", "preferences-no-js-errors",
    "embedded-no-js-errors", "composition-no-js-errors",
})

SCRIPTS_TO_LOAD = (
    "chat-thread-store.js", "chat-thread-controller.js",
    "dom-bindings/calculator.js", "dom-bindings/chat-console.js",
    "chat-console-state.js", "chat-console-code-runtime.js",
)
POST_SCRIPTS = ("chat-console.js",)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _html() -> str:
    content = CHAT_TEMPLATE.read_text(encoding="utf-8")
    for language in ("js", "python", "basic"):
        include = f"<!-- @include applications/scripts/chat-console-{language}-worker.js -->"
        if include not in content:
            raise RuntimeError(f"Missing checked-in worker include: {language}")
        content = content.replace(include, (SCRIPTS / f"chat-console-{language}-worker.js").read_text(encoding="utf-8"))
    return '<!doctype html><html><body data-active-app="chat-console">' + content + '</body></html>'


class Witness:
    def __init__(self, page: Any, semantic: bool):
        self.page = page
        self.semantic = semantic
        self.cases: list[dict[str, Any]] = []

    def expect(self, feature: str, case_id: str, observed: Any, expected: Any) -> None:
        ok = observed == expected
        self.cases.append({"feature": feature, "id": case_id, "expected": expected,
                           "observed": observed, "status": "PASS" if ok else "FAIL"})

    def read(self, js: str) -> Any:
        return self.page.evaluate(js)


def _new_page(browser: Any, semantic: bool) -> tuple[Any, Witness]:
    page = browser.new_page(viewport={"width": 1272, "height": 900})
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.set_content(_html(), wait_until="load")
    # The container browser prohibits navigation to loopback/nominal HTTP origins.
    # Provide only isolated document storage; no real thread data is read or written.
    page.evaluate('''() => {
      const entries = new Map();
      Object.defineProperty(window, 'localStorage', {
        configurable:true, value:{
          getItem:k=>entries.has(k)?entries.get(k):null,
          setItem:(k,v)=>entries.set(k,String(v)),
          removeItem:k=>entries.delete(k), clear:()=>entries.clear(),
          key:i=>[...entries.keys()][i]||null,
          get length(){return entries.size}
        }
      });
      window.confirm=()=>true;
      window.prompt=()=>"Parity Renamed Thread";
      window.history.replaceState=()=>{};
      window.__parityNetwork=[];
      window.fetch=async (resource,options={})=>{
        const path=String(resource);
        const method=String(options.method||'GET').toUpperCase();
        window.__parityNetwork.push({path,method});
        if(path==='/api/applications/chat-console/attachments' && method==='POST') {
          const data=JSON.parse(options.body||'{}');
          return {ok:true,status:200,json:async()=>({ok:true,attachment:{
            id:'attachment-fixture-1',filename:data.filename||'proof.txt',
            mime_type:data.mime_type||'text/plain',size:3,
            data_base64:data.data_base64||''}})};
        }
        throw new Error('parity-external-request-refused:'+path);
      };
    }''')
    for name in SCRIPTS_TO_LOAD:
        page.add_script_tag(content=(SCRIPTS / name).read_text(encoding="utf-8"))
    if semantic:
        page.add_script_tag(content=SEMANTIC_SOURCE.read_text(encoding="utf-8"))
    for name in POST_SCRIPTS:
        page.add_script_tag(content=(SCRIPTS / name).read_text(encoding="utf-8"))
    page.evaluate("initChatConsoleApp()")
    page.wait_for_selector("#chat-console-notebook .chat-cell")
    witness = Witness(page, semantic)
    witness.page_errors = page_errors
    return page, witness


def _finish(witness: Witness, group: str) -> list[dict[str, Any]]:
    witness.expect("browser-safety", f"{group}-no-js-errors", list(witness.page_errors), [])
    return witness.cases


def _notebook(browser: Any, semantic: bool) -> list[dict[str, Any]]:
    page, w = _new_page(browser, semantic)
    try:
        w.expect("notebook-mount", "initial-notebook", w.read("() => [chatConsoleState.cells.length, document.querySelectorAll('#chat-console-notebook .chat-cell').length]"), [1, 1])
        w.expect("cell-types", "all-incumbent-type-tabs", w.read("() => [...document.querySelectorAll('#chat-console-notebook .chat-cell-tab')].map(n=>n.dataset.chatCellTab)"),
                 ["ai", "javascript", "python", "basic", "calculator", "terminal", "mathics", "comment"])
        page.locator('#chat-console-notebook .chat-cell-tab[data-chat-cell-tab="javascript"]').first.click()
        page.locator('#chat-console-notebook textarea').first.fill('vars.answer = 42; console.log("HELLO_PARITY");')
        w.expect("draft-persistence", "source-saved-on-edit", w.read("() => [chatConsoleState.cells[0].type,chatConsoleState.cells[0].source,JSON.parse(localStorage.getItem('main-computer-chat-console-v1')).cells[0].source]"),
                 ["javascript", 'vars.answer = 42; console.log("HELLO_PARITY");', 'vars.answer = 42; console.log("HELLO_PARITY");'])
        page.locator('#chat-console-notebook .chat-cell-controls').first.get_by_role('button', name='Run JS').click()
        page.wait_for_function("() => chatConsoleState.cells.some(c=>c.type==='output' && c.parts?.some(p=>String(p.content).includes('HELLO_PARITY')))", timeout=8000)
        w.expect("javascript-worker", "actual-isolated-js-worker", w.read("() => chatConsoleState.cells.find(c=>c.type==='output').parts.some(p=>String(p.content).includes('HELLO_PARITY'))"), True)
        w.expect("shared-variables", "shared-variable-from-js", w.read("() => chatConsoleState.variables.answer"), 42)
        w.expect("output-lineage", "output-linked-to-exact-source", w.read("() => {const s=chatConsoleState.cells[0];const out=chatConsoleState.cells.find(c=>c.type==='output');return [out.source_cell_id===s.id,chatConsoleState.cells.some(c=>c.thread_parent_output_cell_id===out.id)]}"), [True,True])
        # Fixture a second valid saved output variant, so the UI variant
        # switch is tested without assuming the incumbent supports immediate
        # repeated execution before the previous run's lifecycle settles.
        page.evaluate("""() => {
          const source=chatConsoleState.cells[0];
          const first=chatConsoleState.cells.find(c=>c.type==='output');
          const second=JSON.parse(JSON.stringify(first));
          second.id='parity-output-variant-two';second.variant_index=1;
          second.parts=[{id:'parity-part-two',kind:'stdout',title:'Second result',content:'SECOND_VARIANT',snippets:[]}];
          chatConsoleState.cells.push(second);
          source.output_variant_ids=[first.id,second.id];
          source.selected_output_variant_index=1;
          saveChatConsoleState('second fixture variant');renderChatConsoleNotebook();
        }""")
        w.expect("output-variants", "two-variants-retained", w.read("() => [chatConsoleState.cells[0].output_variant_ids.length,document.querySelector('.chat-output-counter')?.textContent]"), [2,"2/2"])
        page.locator('#chat-console-notebook button[aria-label="Previous result"]').first.click()
        w.expect("output-variants", "earlier-result-selectable", w.read("() => document.querySelector('.chat-output-counter')?.textContent"),"1/2")
        page.locator('#chat-console-notebook button[aria-label="Next result"]').first.click()
        w.expect("output-variants", "latest-result-selectable", w.read("() => document.querySelector('.chat-output-counter')?.textContent"),"2/2")
        # A separate editable continuation is retained after the source output.
        page.locator('#chat-console-notebook .chat-console-cell-input textarea').last.fill('DRAFT_ONE')
        page.locator('#chat-console-notebook .chat-console-cell-input .chat-cell-controls').last.get_by_role('button',name='Duplicate').click()
        w.expect("cell-duplicate", "duplicate-independent", w.read("() => chatConsoleState.cells.filter(c=>c.source==='DRAFT_ONE').length"),2)
        page.locator('#chat-console-notebook .chat-console-cell-input textarea').last.fill('DRAFT_TWO')
        w.expect("draft-persistence", "drafts-remain-independent", w.read("() => ['DRAFT_ONE','DRAFT_TWO'].map(v=>chatConsoleState.cells.filter(c=>c.source===v).length)"),[1,1])
        page.locator('#chat-console-notebook .chat-console-cell-input .chat-cell-controls').last.get_by_role('button',name='Up',exact=True).click()
        w.expect("cell-reorder", "up-preserves-both-drafts", w.read("() => chatConsoleState.cells.filter(c=>c.source.startsWith('DRAFT_')).map(c=>c.source)"), ["DRAFT_TWO","DRAFT_ONE"])
        page.locator('#chat-console-notebook .chat-console-cell-input .chat-cell-controls').last.get_by_role('button',name='Delete',exact=True).click()
        w.expect("cell-delete", "delete-only-target", w.read("() => [chatConsoleState.cells.filter(c=>c.source==='DRAFT_ONE').length,chatConsoleState.cells.filter(c=>c.source==='DRAFT_TWO').length]"),[0,1])
        page.locator('#chat-console-notebook button[data-chat-console-insert="ai"]').first.click()
        w.expect("cell-insert", "insert-new-ai", w.read("() => chatConsoleState.cells.filter(c=>c.type==='ai').length"),1)
        page.locator('#chat-console-notebook .chat-console-cell-input input[type="file"]').last.set_input_files({"name":"proof.txt","mimeType":"text/plain","buffer":b"ABC"})
        page.wait_for_function("() => chatConsoleState.cells.some(c=>c.attachments?.some(a=>a.filename==='proof.txt'))",timeout=5000)
        w.expect("attachments", "upload-metadata-persisted", w.read("() => chatConsoleState.cells.flatMap(c=>c.attachments||[]).map(a=>[a.filename,a.mime_type])"),[["proof.txt","text/plain"]])
        page.locator('#chat-console-notebook .chat-attachment button').first.click()
        w.expect("attachments", "remove-exact-attachment", w.read("() => chatConsoleState.cells.flatMap(c=>c.attachments||[]).length"),0)
        w.expect("network-boundary", "only-attachment-request", w.read("() => window.__parityNetwork.filter(x=>x.method!=='GET').map(x=>[x.method,x.path])"), [["POST","/api/applications/chat-console/attachments"]])
        return _finish(w, "notebook")
    finally:
        page.close()


def _threads(browser: Any, semantic: bool) -> list[dict[str, Any]]:
    page,w = _new_page(browser,semantic)
    try:
        page.locator('#chat-console-notebook textarea').first.fill('UNIQUE_PARITY_THREAD_CONTENT')
        first_id=w.read('() => chatConsoleState.id')
        page.evaluate('(id) => {window.__firstParityThread=id}', first_id)
        page.locator('#chat-thread-new').click()
        w.expect('thread-create','new-chat-independent',w.read("() => [MainComputerChatThreads.list().length,chatConsoleState.cells[0].source]"),[2,""])
        page.locator('#chat-thread-search').fill('UNIQUE_PARITY_THREAD_CONTENT')
        w.expect('thread-search','search-includes-saved-code',page.locator('#chat-thread-list .chat-thread-row').count(),1)
        page.locator('#chat-thread-list .chat-thread-row').first.click()
        w.expect('thread-switch','restores-selected-source',w.read("() => chatConsoleState.cells[0].source"),'UNIQUE_PARITY_THREAD_CONTENT')
        w.expect('thread-switch','exact-thread-selected',w.read("() => chatConsoleState.id===window.__firstParityThread"),True)
        page.locator('#chat-thread-rename').click()
        w.expect('thread-rename','rename-keeps-source',w.read("() => [MainComputerChatThreads.get(chatConsoleState.id).title,chatConsoleState.cells[0].source]"),['Parity Renamed Thread','UNIQUE_PARITY_THREAD_CONTENT'])
        page.locator('#chat-thread-clone').click()
        w.expect('thread-clone','clone-separate-id-and-content',w.read("() => [chatConsoleState.id!==MainComputerChatThreads.list().find(t=>t.id!==chatConsoleState.id && t.cells.some(c=>c.source==='UNIQUE_PARITY_THREAD_CONTENT'))?.id,chatConsoleState.cells[0].source,MainComputerChatThreads.list().length]"),[True,'UNIQUE_PARITY_THREAD_CONTENT',3])
        page.locator('#chat-thread-archive').click()
        w.expect('thread-archive','archive-record-not-deleted',w.read("() => [Object.values(MainComputerChatThreads.exportState().threads).filter(t=>t.archived).length,MainComputerChatThreads.list().length]"),[1,2])
        w.expect('thread-persistence','store-retains-unarchived-and-archived',w.read("() => [JSON.parse(localStorage.getItem(MainComputerChatThreads.storageKey)).thread_order.length,!!localStorage.getItem('main-computer-chat-console-v1')]"),[3,True])
        return _finish(w, "threads")
    finally:page.close()


def _preferences(browser: Any, semantic: bool) -> list[dict[str, Any]]:
    page,w = _new_page(browser,semantic)
    try:
        page.locator('#chat-console-notebook .chat-rag-at-toggle input[type="checkbox"]').first.check()
        w.expect('thinking-preferences','rag-at-setting-persisted',w.read("() => chatConsoleState.cells[0].rag_assisted_thinking"),True)
        page.locator('#chat-console-notebook .chat-rag-at-field select').first.select_option('medium')
        w.expect('thinking-preferences','thinking-selection-persisted',w.read("() => chatConsoleState.cells[0].rag_assisted_thinking_options.think"),'medium')
        page.locator('#chat-console-notebook .chat-cell-tab[data-chat-cell-tab="terminal"]').first.click()
        w.expect('terminal-review','terminal-stage-visible',page.get_by_role('button',name='Stage').count(),1)
        page.get_by_role('button',name='Stage').click()
        w.expect('terminal-review','stage-does-not-execute',w.read("() => [chatConsoleState.cells[0].status,window.__parityNetwork.length]"),['review-required',0])
        return _finish(w, "preferences")
    finally:page.close()


def _embedded(browser: Any, semantic: bool) -> list[dict[str, Any]]:
    page,w = _new_page(browser,semantic)
    try:
        page.evaluate("""() => { document.body.dataset.activeApp='calculator';
           const host=document.createElement('aside');host.id='parity-calculator-embed';
           host.dataset.chatConsoleEmbed='calculator';document.body.append(host);
           window.__linkedThread='';
           const controller=MainComputerChatConsole.mountEmbedded(host,{
             embedId:'calculator',activeApp:'calculator',idPrefix:'parity-calc',
             layout:'compact',showThreadRail:false,showCurrentThreadBar:true,
             title:'Calculator Chat',threadTitle:'Calculator Parity Chat',
             getLinkedThreadId:()=>window.__linkedThread,
             setLinkedThreadId:id=>{window.__linkedThread=id;},
             plugins:[{id:'calculator-context',label:'Calculator context',appliesTo:'ai',defaultEnabled:false}]
           });window.__embedMounted=!!controller;
        }""")
        w.expect('embedded-mount','shared-contract-mounts',w.read("() => [window.__embedMounted,document.querySelectorAll('#parity-calculator-embed .chat-console-notebook .chat-cell').length,!!window.__linkedThread]"),[True,1,True])
        w.expect('embedded-context','host-notebook-linked-to-thread',w.read("() => [chatConsoleState.id===window.__linkedThread,MainComputerChatThreads.get(window.__linkedThread).metadata.origin_app]"),[True,'calculator'])
        w.expect('embedded-extension','host-provided-toggle-present',w.read("() => [...document.querySelectorAll('#parity-calculator-embed [data-chat-mount-plugin-id]')].map(x=>x.dataset.chatMountPluginId)"),['calculator-context'])
        page.locator('#parity-calculator-embed [data-chat-mount-plugin-id="calculator-context"] input').check()
        w.expect('embedded-extension','host-plugin-option-persisted',w.read("() => chatConsoleState.cells[0].mount_plugin_options['calculator-context'].enabled"),True)
        page.locator('#parity-calculator-embed .chat-console-cell-input textarea').first.fill('EMBED_DRAFT')
        w.expect('embedded-context','embedded-edit-keeps-thread-source',w.read("() => [chatConsoleState.cells[0].source,MainComputerChatThreads.get(window.__linkedThread).cells[0].source]"),['EMBED_DRAFT','EMBED_DRAFT'])
        w.expect('network-boundary','embedded-mount-read-only',w.read("() => window.__parityNetwork.length"),0)
        return _finish(w, "embedded")
    finally:page.close()


def _composition(browser: Any, semantic: bool) -> list[dict[str, Any]]:
    page,w = _new_page(browser,semantic)
    try:
        if semantic:
            result = w.read("""() => {const host=document.getElementById('chat-console-app');
             const cell=host.querySelector('.chat-cell');return [
             host.dataset.mcelChatContract||'',host.dataset.mcelChatContext||'',
             cell.dataset.mcelChatRole||'',
             cell.querySelector('.chat-cell-tabs')?.dataset.mcelChatRole||'',
             cell.querySelector('.chat-cell-content')?.dataset.mcelChatRole||''];}""")
            w.expect('semantic-composition','real-host-and-cell-roles',result,
                     ['mcel.chat-console.composition.v1','standalone','cell','cell-choices','cell-body'])
            page.locator('#chat-console-notebook .chat-cell-tab[data-chat-cell-tab="comment"]').first.click()
            w.expect('semantic-composition','role-preserved-after-rerender',w.read("() => document.querySelector('#chat-console-notebook .chat-cell')?.dataset.mcelChatRole"),'cell')
        else:
            w.expect('semantic-composition','incumbent-unmodified',w.read("() => document.querySelector('#chat-console-app').hasAttribute('data-mcel-semantic-chat')"),False)
        return _finish(w, "composition")
    finally:page.close()


def run_parity(*, report_path: Path=REPORT) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return {"status":"INCOMPLETE", "reason":"playwright-unavailable", "error":str(exc)}
    source_paths=[CHAT_TEMPLATE,SEMANTIC_SOURCE,*[SCRIPTS/x for x in SCRIPTS_TO_LOAD+POST_SCRIPTS],
                  *[SCRIPTS/f'chat-console-{x}-worker.js' for x in ('js','python','basic')]]
    runs:dict[str,list[dict[str,Any]]]={}
    exceptions:dict[str,str]={}
    with sync_playwright() as p:
        try:
            from tools.mcel_chat_console_layout_oracle import _launch
            browser=_launch(p)
        except Exception as exc:
            return {"status":"INCOMPLETE", "reason":"browser-unavailable", "error":str(exc)}
        try:
            for semantic,label in ((False,'incumbent'),(True,'semantic')):
                found=[]
                for group,func in (("notebook",_notebook),("threads",_threads),("preferences",_preferences),
                                   ("embedded",_embedded),("composition",_composition)):
                    try:
                        found+=func(browser,semantic)
                    except Exception as exc:
                        exceptions[f'{label}:{group}']=f'{type(exc).__name__}: {str(exc)[:650]}'
                runs[label]=found
        finally:browser.close()
    baseline={case['id']:case for case in runs.get('incumbent',[])}
    candidate={case['id']:case for case in runs.get('semantic',[])}
    comparable_ids=set(baseline)|set(candidate)
    comparisons=[]
    for case_id in sorted(comparable_ids):
        left=baseline.get(case_id);right=candidate.get(case_id)
        if case_id in ('real-host-and-cell-roles','role-preserved-after-rerender','incumbent-unmodified'):
            continue
        matches=bool(left and right and left['status']=='PASS' and right['status']=='PASS' and left['observed']==right['observed'])
        comparisons.append({'id':case_id,'feature':(right or left or {}).get('feature',''),
                            'incumbent_status':left['status'] if left else 'MISSING',
                            'semantic_status':right['status'] if right else 'MISSING',
                            'same_observation':bool(left and right and left['observed']==right['observed']),
                            'status':'PASS' if matches else 'MISMATCH'})
    missing_ids=sorted(REQUIRED_SHARED_CASE_IDS - {case['id'] for case in comparisons})
    unexpected_ids=sorted({case['id'] for case in comparisons} - REQUIRED_SHARED_CASE_IDS)
    failures=[case for case in comparisons if case['status']!='PASS']
    semantic_checks=[case for case in candidate.values() if case['feature']=='semantic-composition']
    composition_pass=len(semantic_checks)==2 and all(case['status']=='PASS' for case in semantic_checks)
    status='PASS_ISOLATED' if not failures and not exceptions and composition_pass and not missing_ids and not unexpected_ids and len(comparisons)==len(REQUIRED_SHARED_CASE_IDS) else 'INCOMPLETE' if exceptions else 'FAIL_PARITY'
    output={
      'schema':'mcel.chat-console-functional-parity.v1', 'status':status,
      'scope':'incumbent-and-shared-semantic-browser-runtime; in-memory-thread-storage; native-isolated-js-worker; mocked-attachments; no-provider-or-host-mutation',
      'source_sha256':{str(path.relative_to(ROOT)).replace('\\','/'): _sha256(path) for path in source_paths},
      'summary':{'shared_scenarios':len(comparisons),'shared_pass':sum(c['status']=='PASS' for c in comparisons),
                 'shared_mismatches':len(failures),'missing_required_scenarios':missing_ids,'unexpected_scenarios':unexpected_ids,'semantic_composition_cases':len(semantic_checks),
                 'semantic_composition_pass':composition_pass,
                 'features_covered':sorted(set(c['feature'] for c in comparisons))},
      'comparisons':comparisons,'mismatches':failures,'exceptions':exceptions,'runs':runs,
      'evidence_limits':['No real AI provider, Mathics service, Python runtime, BASIC runtime, terminal, or production attachment upload was executed',
                         'Browser fixture uses the checked-in Chat Console code and isolated storage, not a live Windows-host route',
                         'Real thread persistence and hosted mounting have separate existing acceptance; no canonical route promotion is authorized'],
      'known_incumbent_limitations':[
          'The first JS output is correctly linked to its source and continuation, but its source output_variant_ids field may lag the saved output; this occurs with or without semantic composition',
          'Output-variant navigation is tested using a second persisted output fixture, not an immediate second code execution',
      ],
      'promotion_authorized':False
    }
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(output,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return output


def main(argv:list[str]|None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=REPORT)
    args=parser.parse_args(argv)
    result=run_parity(report_path=args.output)
    print(f"MCEL_CHAT_CONSOLE_FUNCTIONAL_PARITY {result['status']}")
    for feature in FEATURES:
        cases=[c for c in result.get('comparisons',[]) if c['feature']==feature]
        if not cases:
            if feature=='semantic-composition':
                print(f"  {feature}: {'PASS' if result.get('summary',{}).get('semantic_composition_pass') else 'INCOMPLETE'}")
            continue
        print(f"  {feature}: {sum(c['status']=='PASS' for c in cases)}/{len(cases)}")
    summary=result.get('summary',{})
    print(f"  browser parity: {summary.get('shared_pass',0)}/{summary.get('shared_scenarios',0)} cases; mismatches: {summary.get('shared_mismatches','unknown')}")
    if result.get('exceptions'):
        for name,reason in result['exceptions'].items():print(f"  {name}: {reason[:220]}")
    if result.get('mismatches'):
        for item in result['mismatches'][:8]:print(f"  mismatch: {item['id']}")
    print(f"  report: {args.output}")
    print('  live providers, filesystem/process effects, canonical promotion: UNVERIFIED / NOT MODIFIED')
    return 0 if result['status']=='PASS_ISOLATED' else 1

if __name__=='__main__':
    raise SystemExit(main())
