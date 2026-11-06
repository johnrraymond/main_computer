"""Read-only File Explorer ↔ semantic File Manager differential acceptance.

Exercises the incumbent's checked-in DOM and JavaScript and the compiled paired
candidate against one deterministic *real temporary filesystem* fixture. The
browser transport is simulated; Windows-host authority, cross-app document
handoff, user design approval and canonical promotion are explicitly unverified.

This is an independent incumbent-function oracle; candidate declarations do
not determine which incumbent capabilities count as required.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT = Path(__file__).resolve().parents[1]
REPORT = Path('runtime/reports/mcel-file-manager-functional-parity/report.json')
SCRIPTS = Path('main_computer/web/applications/scripts')
INCUMBENT_MARKUP = Path('main_computer/web/applications/apps/file-explorer.html')
INCUMBENT_SCRIPT = SCRIPTS / 'file-explorer.js'
CANDIDATE_SOURCE = Path('tools/fixtures/mcel_semantic_other_apps/file-manager/application.js')
CANDIDATE_RUNTIME = SCRIPTS / 'mcel-semantic-operational-runtime.js'
PORT = SCRIPTS / 'mcel-semantic-read-ports.js'

@dataclass(frozen=True)
class Feature:
    id: str
    label: str
    legacy_dom: tuple[str,...]
    legacy_code: tuple[str,...]
    semantic_intents: tuple[str,...]
    next_evidence: str = 'Windows-host comparison against authorized, observed filesystem roots'

FEATURES = (
    Feature('roots','Root discovery and stable root identity',('file-explorer-roots',),('loadSystemFileExplorerRoots',),('intent:load-roots','intent:choose-root')),
    Feature('listing','Directory enumeration, identity, and count',('file-explorer-list',),('listSystemFileExplorerDirectory',),('intent:browse',)),
    Feature('nested','Enter a nested directory',('file-explorer-list',),('systemFileExplorerOpenEntry',),('intent:enter-directory',)),
    Feature('directory-metadata','Inspect directory metadata without entering or reading the directory',
            ('file-explorer-preview',),('previewSystemFileExplorerEntry',),('intent:choose-entry',)),
    Feature('parent','Navigate to the parent directory',('file-explorer-up',),('systemFileExplorerSelectLocation',),('intent:enter-directory',)),
    Feature('retained-tree','Return to an observed directory without losing sibling branches',('file-explorer-list',),('systemFileExplorerEntryToTreeNode',),('intent:enter-directory',)),
    Feature('search','Search within current directory',('file-explorer-search',),('async function searchSystemFileExplorer()',),('intent:search',)),
    Feature('preview','Read selected file with exact path and content',('file-explorer-preview',),('readSystemFileExplorerEntry',),('intent:preview-entry',)),
    Feature('metadata-only','Respect non-readable file previews',('file-explorer-preview',),('Preview unavailable:',),('intent:preview-entry',)),
    Feature('handoff','Suggested application handoff for an observed file',('file-explorer-preview',),('setActiveApp(app)',),('intent:open-with',),
            'Verify selected file identity is actually delivered into owning app; route-only navigation is insufficient'),
    Feature('root-isolation','Switch roots without displaying old-root entries',('file-explorer-roots',),('systemFileExplorerSelectLocation',),('intent:choose-root',)),
    Feature('authorization','No out-of-root preview or unobserved file handoff',('file-explorer-list',),('systemFileExplorerApi',),('intent:preview-entry','intent:open-with'),
            'Windows-host denied-operation and stale-response tests'),
)

REPAIRS = {
 'roots':'Preserve all root IDs and labels; selection must scope subsequent reads to the chosen root.',
 'listing':'Use the same bounded listing endpoint and preserve full relative-path identity and row counts.',
 'nested':'Enter directory from an observed tree item without manually guessing paths.',
 'directory-metadata':'Offer selection and metadata inspection of an observed folder without treating it as readable file content.',
 'parent':'Navigate to an observed parent, including the root, without discarding sibling evidence.',
 'retained-tree':'Maintain observed branches when moving between directories; invalidate only a removed branch.',
 'search':'Send the active root and current directory to the existing scoped search endpoint; render actual results.',
 'preview':'Read a selected observed path, render its content and metadata without treating a directory as a file.',
 'metadata-only':'Preserve the file status and refusal reason when the endpoint denies a content preview.',
 'handoff':'Only offer supported owning apps for observed entries; preserve exact file identity in any future handoff contract.',
 'root-isolation':'Invalidate stale selections, listing and preview when changing roots.',
 'authorization':'Enforce observed-root/observed-entry capability evidence and reject path traversal and forged writes.',
}

def read(repo:Path,rel:Path)->str:
    return (repo/rel).read_text(encoding='utf-8')

def source_inventory(repo:Path, *, ir:dict|None=None, markup:str|None=None,
                     incumbent_js:str|None=None, candidate_js:str|None=None)->dict:
    ir = ir if ir is not None else compile_semantic_app('file-manager',repo)
    markup = markup if markup is not None else read(repo,INCUMBENT_MARKUP)
    incumbent_js = incumbent_js if incumbent_js is not None else read(repo,INCUMBENT_SCRIPT)
    candidate_js = candidate_js if candidate_js is not None else read(repo,CANDIDATE_SOURCE)
    intents={item['id'] for item in ir.get('intents',[])}
    rows={}
    for f in FEATURES:
        missing_dom=[n for n in f.legacy_dom if 'id="'+n+'"' not in markup]
        missing_js=[n for n in f.legacy_code if n not in incumbent_js]
        missing_intents=[n for n in f.semantic_intents if n not in intents]
        rows[f.id]={
            'label':f.label,'status':'UNVERIFIED' if missing_dom or missing_js else 'MISSING' if missing_intents else 'SOURCE_COVERED',
            'incumbent':{'missingDomIds':missing_dom,'missingCodeSignatures':missing_js},
            'candidate':{'missingIntentIds':missing_intents},
            'repairGuidance':REPAIRS[f.id],'requiredNextEvidence':f.next_evidence,'browserScenarios':[],
        }
    return {'features':rows,'declaredIntents':sorted(intents),'sourceEvidence':{
        'incumbentMarkupSha256':hashlib.sha256(markup.encode()).hexdigest(),
        'incumbentScriptSha256':hashlib.sha256(incumbent_js.encode()).hexdigest(),
        'candidateScriptSha256':hashlib.sha256(candidate_js.encode()).hexdigest(),
        'candidateIrFingerprint':hashlib.sha256(json.dumps(ir,sort_keys=True).encode()).hexdigest(),
    }}

def incumbent_html(repo:Path)->str:
    markup=read(repo,INCUMBENT_MARKUP)
    bindings=read(repo,SCRIPTS/'dom-bindings/file-explorer.js')
    state=read(repo,SCRIPTS/'dom-bindings/file-explorer-state.js')
    script=read(repo,INCUMBENT_SCRIPT)
    # The production file tree uses Wunderbaum from a CDN. For this *offline*
    # acceptance only, force the incumbent's own fallback list branch. All
    # navigation, preview, search and handoff handlers remain its real code.
    needle='function systemFileExplorerLoadWunderbaum() {'
    if script.count(needle)!=1:raise RuntimeError('Incumbent offline tree setup changed')
    script=script.replace(needle,needle+' return Promise.reject(new Error("isolated offline fallback"));',1)
    parts=(bindings,state,script)
    if any('</script' in p.lower() for p in parts):raise RuntimeError('Inline script terminator in incumbent asset')
    header='''<script>var routeableApps=new Set(["code-editor","document","spreadsheet","game-editor"]);
    function setActiveApp(app){window.__fmNavigated=app;}
    function escapeHtml(v){return String(v).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}</script>'''
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"></head><body>'+markup+header+
            ''.join('<script>'+p+'</script>' for p in parts)+'<script>initSystemFileExplorerApp();</script></body></html>')

def fixture_payloads(path:Path)->dict:
    """Produce endpoint replies from real temporary files, not imagined entry IDs."""
    for rel,contents in {
      'README.md':'# Project overview\n',
      'docs/guide.txt':'Guide for the project.\n',
      'docs/nested/todo.md':'Nested todo list.\n',
      'src/main.py':'print("sample")\n',
      'assets/preview.bin':b'\x00\xff\x01',
    }.items():
        target=path/rel;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(contents if isinstance(contents,bytes) else contents.encode())
        os.utime(target,(1700000000,1700000000))
    def rows(parent:Path):
        return [{
            'relative_path':entry.relative_to(path).as_posix(),
            'name':entry.name,'kind':'directory' if entry.is_dir() else 'file',
            'bytes':entry.stat().st_size if entry.is_file() else 0,
            'mtime':int(entry.stat().st_mtime),'path_display':entry.relative_to(path).as_posix(),
            'category':'document' if entry.suffix in ('.md','.txt') else 'code' if entry.suffix=='.py' else 'other',
            'suggested_app':'document' if entry.suffix in ('.md','.txt') else 'code-editor' if entry.suffix=='.py' else '',
            'main_computer_purview':True,'mounted_windows_drive':False,
        } for entry in sorted(parent.iterdir(),key=lambda x:x.name) if entry.is_file() or entry.is_dir()]
    listing={}
    for folder in sorted((p for p in path.rglob('*') if p.is_dir()),key=str):
        listing[folder.relative_to(path).as_posix()]=rows(folder)
    listing['']=rows(path)
    return {'roots':[{'id':'workspace','label':'Workspace','path_display':str(path)},
                     {'id':'scratch','label':'Scratch','path_display':'isolated-scratch'}],
            'listing':listing,'content':{
             p.relative_to(path).as_posix():p.read_text(encoding='utf-8')
             for p in path.rglob('*') if p.is_file() and p.suffix!='.bin'}}

def inject_mock(page,fixture:dict)->None:
    page.goto('about:blank')
    page.evaluate('''data=>{
      window.__fmCalls=[];window.__fmFixture=data;
      window.fetch=async (url,opts={})=>{
        const path=String(url), a=JSON.parse(opts.body||'{}');
        if(!path.startsWith('/api/applications/file-explorer/'))
          return {ok:false,status:404,json:async()=>({ok:false,error:'isolated-unknown-route'})};
        window.__fmCalls.push({path,body:a});
        let result={ok:false,error:'unrecognized-endpoint'};
        const rel=String(a.relative_path||'');
        const root=a.root_id||'workspace';
        const isAuthorizedPath=(s)=>s==='' || (typeof s==='string' && !s.startsWith('/') && !s.endsWith('/') &&
          !s.includes('\\\\') && !s.split('/').some(x=>x===''||x==='..'||x==='.') && !s.includes('\\0'));
        if(path.endsWith('/roots'))result={ok:true,roots:data.roots};
        else if(path.endsWith('/list') && ['workspace','scratch'].includes(root) && isAuthorizedPath(rel)){
          const entries=root==='scratch'? [{relative_path:'scratch.txt',name:'scratch.txt',kind:'file',bytes:7,
            category:'document',suggested_app:'document'}]:data.listing[rel];
          if(entries)result={ok:true,root_id:root,relative_path:rel,count:entries.length,entries};
        }else if(path.endsWith('/search') && ['workspace','scratch'].includes(root) && isAuthorizedPath(rel)){
          const pool=(root==='scratch'?[{relative_path:'scratch.txt',name:'scratch.txt',kind:'file',bytes:7,
            category:'document',suggested_app:'document'}]:Object.entries(data.listing)
            .filter(([folder])=>folder===rel || (rel==='' || folder.startsWith(rel+'/')))
            .flatMap(([,entries])=>entries));
          const results=pool.filter(r=>r.name.toLowerCase().includes(String(a.query||'').toLowerCase()));
          result={ok:true,root_id:root,relative_path:rel,results,count:results.length};
        }else if(path.endsWith('/read') && isAuthorizedPath(rel) && ['workspace','scratch'].includes(root)){
          const meta=root==='scratch'?{relative_path:'scratch.txt',name:'scratch.txt',kind:'file'}:
            Object.values(data.listing).flat().find(v=>v.relative_path===rel && v.kind==='file');
          if(meta && (root==='scratch'?rel==='scratch.txt':true)){
            const readable=root==='scratch'||Object.prototype.hasOwnProperty.call(data.content,rel);
            result={ok:true,readable,content:root==='scratch'?'scratch':(data.content[rel]||''),
              reason:readable?'':'binary file preview is disabled',entry:meta};
          }
        }
        return {ok:result.ok===true,status:result.ok===true?200:400,json:async()=>result};
      };
    }''',fixture)

def _calls(page,endpoint:str)->list:
    return page.evaluate('''name=>window.__fmCalls.filter(v=>v.path.endsWith('/'+name)).map(v=>v.body)''',endpoint)

def _case(rows:list,feature:str,case:str,incumbent:Any,candidate:Any,ok:bool,note:str=''):
    rows.append({'id':case,'feature':feature,'status':'PASS' if ok else 'MISMATCH',
                 'incumbent':incumbent,'candidate':candidate,'note':note or None,'mockedHttp':True})

def browser_comparison(repo:Path)->dict:
    from playwright.sync_api import sync_playwright
    cases=[]; errors={'incumbent':[],'candidate':[]}
    with tempfile.TemporaryDirectory(prefix='mcel-fm-parity-') as temp:
      fixture=fixture_payloads(Path(temp))
      with sync_playwright() as p:
       browser,backend=launch_semantic_browser(p)
       left=browser.new_page(viewport={'width':1100,'height':900})
       right=browser.new_page(viewport={'width':1100,'height':900})
       left.on('pageerror',lambda e:errors['incumbent'].append(str(e)))
       right.on('pageerror',lambda e:errors['candidate'].append(str(e)))
       try:
        inject_mock(left,fixture);inject_mock(right,fixture)
        left.set_content(incumbent_html(repo))
        semantic_html=render_semantic_page('file-manager',repo)
        original='const port=McelSemanticReadPorts.create({appId:ir.application.appId});'
        if semantic_html.count(original)!=1:raise RuntimeError('Semantic port factory drift')
        semantic_html=semantic_html.replace(original,
          'const port=(window.__fmMountedPort=McelSemanticReadPorts.create({appId:ir.application.appId,navigate:(route,meta)=>{window.__fmNavigated={route,meta};return true;}}));')
        right.set_content(semantic_html)
        if right.locator('#mcel-workbench-status').inner_text()!='Semantic candidate ready':
            raise RuntimeError('Semantic candidate mount refused: '+right.locator('#mcel-workbench-status').inner_text())
        left.wait_for_function('window.__fmCalls.filter(v=>v.path.endsWith("/list")).length>=1')
        # Startup is a user-visible behavior: the incumbent discovers and
        # opens its default location automatically. The candidate must do the
        # same, with no Load Roots / Select / Browse clicks from this test.
        right.locator('[data-record-key="README.md"]').wait_for(timeout=10000)
        boot={
            'roots':len(_calls(right,'roots')),
            'lists':len(_calls(right,'list')),
            'selectedRoot':right.evaluate('window.__mcelWorkbench.app.readState("state:selected-root")'),
            'visibleRows':right.locator('[data-record-key="README.md"]').count(),
            'refusals':right.evaluate('window.__mcelWorkbench.app.receipts().filter(x=>x.disposition.startsWith("refused"))'),
        }
        _case(cases,'roots','startup-default-location-and-listing',
              {'roots':1,'lists':1,'selectedRoot':'workspace','visibleRows':1,'refusals':[]},boot,
              boot=={'roots':1,'lists':1,'selectedRoot':'workspace','visibleRows':1,'refusals':[]},
              'The incumbent and candidate must show an authorized directory on first load, without a manual root-selection sequence.')
        root_labels=left.locator('#file-explorer-roots button').all_inner_texts()
        candidate_roots=right.locator('[data-record-key="workspace"],[data-record-key="scratch"]').count()
        _case(cases,'roots','roots-observed',len(root_labels),candidate_roots,
              len(root_labels)==candidate_roots==2)
        incumbent_items=sorted(x['relative_path'] for x in fixture['listing'][''])
        candidate_items=sorted(right.locator('[data-record-key]').evaluate_all('els=>els.filter(e=>e.hasAttribute("data-mcel-tree-depth")).map(e=>e.dataset.recordKey)'))
        _case(cases,'listing','root-list-exact-identities',incumbent_items,candidate_items,incumbent_items==candidate_items)
        _case(cases,'listing','root-list-api-path',_calls(left,'list')[-1],_calls(right,'list')[-1],
              _calls(left,'list')[-1]==_calls(right,'list')[-1])
        # Unlike opening a folder, inspecting folder metadata must not
        # navigate or attempt the /read endpoint (directories are unreadable).
        left.locator('[data-file-explorer-entry-path="docs"]').click()
        right.locator('[data-mcel-inspect-folder="docs"]').click()
        right.locator('[data-mcel-selected-details="docs"]').wait_for(timeout=5000)
        left_details=left.locator('#file-explorer-preview').inner_text()
        semantic_details=right.locator('[data-mcel-selected-details="docs"]').inner_text()
        _case(cases,'directory-metadata','inspect-folder-without-navigation',
              {'name':'docs','kind':'directory'},
              {'name':'docs' if 'docs' in semantic_details else None,
               'kind':'directory' if 'directory' in semantic_details else None},
              'Name: docs' in left_details and 'Kind: directory' in left_details and
              'docs' in semantic_details and 'directory' in semantic_details)
        # The same observed directory opens from an incumbent double-click and
        # a semantic tree 'Open folder' action. Neither receives a guessed path.
        left.locator('[data-file-explorer-entry-path="docs"]').dblclick()
        right.locator('[data-record-key="docs"] button[data-select-key="docs"]').click()
        left.wait_for_function('window.__fmCalls.some(v=>v.path.endsWith("/list") && v.body.relative_path==="docs")')
        right.locator('[data-record-key="docs/guide.txt"]').wait_for()
        _case(cases,'nested','enter-observed-directory',_calls(left,'list')[-1],_calls(right,'list')[-1],
              _calls(left,'list')[-1]==_calls(right,'list')[-1])
        # The parent control must restore root contents; a visual button is
        # not enough. Its scope must also match the real parent path.
        left.locator('#file-explorer-up').click();right.locator('[data-mcel-parent-directory]').click()
        right.locator('[data-record-key="README.md"]').wait_for()
        _case(cases,'parent','parent-restores-root',_calls(left,'list')[-1],_calls(right,'list')[-1],
              _calls(left,'list')[-1]==_calls(right,'list')[-1])
        # A previously observed sibling remains navigable after visiting the
        # other sibling. Verify real request paths, not only stored tree labels.
        right.locator('[data-record-key="src"] button[data-select-key="src"]').click()
        right.locator('[data-record-key="src/main.py"]').wait_for()
        right.locator('[data-mcel-parent-directory]').click()
        right.locator('[data-record-key="docs"] button[data-select-key="docs"]').wait_for()
        right.locator('[data-record-key="docs"] button[data-select-key="docs"]').click()
        right.locator('[data-record-key="docs/guide.txt"]').wait_for()
        retained=[x.get('relative_path') for x in _calls(right,'list')[-3:]]
        _case(cases,'retained-tree','sibling-navigation-retains-branches',
              ['src','','docs'],retained,retained==['src','','docs'])
        right.locator('[data-mcel-parent-directory]').click()
        right.locator('[data-record-key="README.md"]').wait_for()
        # Search within a nested directory. The real backend search walks the
        # selected subtree; the candidate must pass that subtree root.
        left.locator('[data-file-explorer-entry-path="docs"]').dblclick()
        right.locator('[data-record-key="docs"] button[data-select-key="docs"]').click()
        right.locator('[data-record-key="docs/guide.txt"]').wait_for()
        left.locator('#file-explorer-search').fill('guide')
        left.locator('#file-explorer-search-run').click()
        right.locator('form[data-intent="intent:search"] input[name="query"]').fill('guide')
        right.locator('form[data-intent="intent:search"] button[type="submit"]').click()
        left.wait_for_function('window.__fmCalls.some(v=>v.path.endsWith("/search") && v.body.relative_path==="docs")')
        right.wait_for_function('window.__fmCalls.some(v=>v.path.endsWith("/search") && v.body.relative_path==="docs")')
        _case(cases,'search','nested-search-preserves-directory',_calls(left,'search')[-1],_calls(right,'search')[-1],
              all(_calls(right,'search')[-1].get(k)==_calls(left,'search')[-1].get(k) for k in ('root_id','relative_path','query')))
        left.locator('#file-explorer-up').click()
        right.locator('[data-mcel-parent-directory]').click()
        right.locator('[data-record-key="README.md"]').wait_for()
        # Search the *current* directory through the same scoped endpoint.
        left.locator('#file-explorer-search').fill('README')
        left.locator('#file-explorer-search-run').click()
        right.locator('form[data-intent="intent:search"] input[name="query"]').fill('README')
        right.locator('form[data-intent="intent:search"] button[type="submit"]').click()
        left.wait_for_function('window.__fmCalls.some(v=>v.path.endsWith("/search"))')
        right.wait_for_function('window.__fmCalls.some(v=>v.path.endsWith("/search"))')
        _case(cases,'search','search-current-root',_calls(left,'search')[-1],_calls(right,'search')[-1],
              all(_calls(right,'search')[-1].get(k)==_calls(left,'search')[-1].get(k) for k in ('root_id','relative_path','query')),
              'Search result limit may differ; root, directory and query must match.')
        # The selected file must be the same exact relative path, not just a
        # matching display name. Both previews use the incumbent read endpoint.
        left.locator('[data-file-explorer-entry-path="README.md"]').click()
        left.locator('[data-file-explorer-entry-path="README.md"]').dblclick()
        right.locator('[data-record-key="README.md"] button').click()
        right.get_by_role('button',name='Preview Entry').click()
        right.locator('[data-state="state:preview"]').get_by_text('Project overview',exact=False).wait_for()
        left.wait_for_function('window.__fmCalls.some(v=>v.path.endsWith("/read"))')
        _case(cases,'preview','preview-exact-file',_calls(left,'read')[-1],_calls(right,'read')[-1],
              _calls(left,'read')[-1]==_calls(right,'read')[-1])
        # The incumbent offers a route switch. Neither candidate nor legacy
        # actually transfers a selected file into the other application's
        # document loader. Report route parity without claiming file-handoff.
        left.locator('[data-file-explorer-entry-path="README.md"]').click()
        left.locator('#file-explorer-preview [data-open-with="document"]').click()
        right.get_by_role('button',name='Open With').click()
        right.wait_for_function('window.__fmNavigated?.route==="/applications/document"',timeout=5000)
        candidate_nav=right.evaluate('window.__mcelWorkbench.app.readState("state:opened-with")')
        nav_meta=right.evaluate('window.__fmNavigated?.meta')
        _case(cases,'handoff','suggested-application-route-only',left.evaluate('window.__fmNavigated'),
              {'route':candidate_nav,'requestMeta':nav_meta},
              left.evaluate('window.__fmNavigated')=='document' and '/applications/document' in candidate_nav and
              nav_meta.get('rootId')=='workspace' and nav_meta.get('relativePath')=='README.md',
              'Candidate sends observed file identity to its navigation callback; hosted target consumption remains unverified.')
        # Non-readable preview must be metadata-only, not a stale content
        # replay. The incumbent also reports the backend refusal reason.
        left.locator('#file-explorer-search').fill('')
        left.locator('#file-explorer-search-run').click()
        left.locator('[data-file-explorer-entry-path="assets"]').wait_for()
        left.locator('[data-file-explorer-entry-path="assets"]').dblclick()
        left.locator('[data-file-explorer-entry-path="assets/preview.bin"]').wait_for()
        left.locator('[data-file-explorer-entry-path="assets/preview.bin"]').dblclick()
        left.wait_for_function('document.querySelector("#file-explorer-preview")?.textContent.includes("binary file preview is disabled")')
        right.locator('form[data-intent="intent:search"] input[name="query"]').fill('')
        right.get_by_role('button',name='Browse',exact=True).click()
        right.locator('[data-record-key="assets"] button[data-select-key="assets"]').wait_for()
        right.locator('[data-record-key="assets"] button[data-select-key="assets"]').click()
        right.locator('[data-record-key="assets/preview.bin"] button').wait_for()
        right.locator('[data-record-key="assets/preview.bin"] button').click()
        right.get_by_role('button',name='Preview Entry').click()
        right.wait_for_function('window.__mcelWorkbench.app.readState("state:preview")?.reason==="binary file preview is disabled"',timeout=5000)
        binary=right.evaluate('window.__mcelWorkbench.app.readState("state:preview")')
        _case(cases,'metadata-only','binary-preview-honors-refusal',
              {'readable':False,'reason':'binary file preview is disabled'},
              {'readable':binary['readable'],'reason':binary['reason']},
              binary['readable'] is False and binary['reason']=='binary file preview is disabled')
        # Root switching must never leave a workspace file selectable in the
        # scratch root; root IDs are evidence, not filesystem paths.
        right.locator('[data-record-key="scratch"] button').click()
        # Choosing another observed location must fetch its directory without
        # the second Browse click required by the old candidate.
        right.locator('[data-record-key="scratch.txt"]').wait_for()
        leaked=right.locator('[data-record-key="README.md"]').count()
        stale_preview=right.evaluate('window.__mcelWorkbench.app.readState("state:preview")')
        _case(cases,'root-isolation','root-switch-clears-old-entries-and-preview',
              {'staleRows':0,'stalePreview':False},
              {'staleRows':leaked,'stalePreview':stale_preview is not None},
              leaked==0 and stale_preview is None)
        _case(cases,'root-isolation','root-selection-opens-directory',
              {'selectedRoot':'scratch','directory':'scratch.txt','listRoot':'scratch'},
              {'selectedRoot':right.evaluate('window.__mcelWorkbench.app.readState("state:selected-root")'),
               'directory':'scratch.txt' if right.locator('[data-record-key="scratch.txt"]').count() else '',
               'listRoot':_calls(right,'list')[-1].get('root_id')},
              right.evaluate('window.__mcelWorkbench.app.readState("state:selected-root")')=='scratch' and
              right.locator('[data-record-key="scratch.txt"]').count()==1 and
              _calls(right,'list')[-1].get('root_id')=='scratch')
        # Authoritative request guard: neither a not-observed path nor a
        # traversal string can be previewed through the admitted port.
        right.locator('[data-record-key="scratch.txt"] button').click()
        forged=right.evaluate('''async()=>{
          const port=window.__fmMountedPort;
          const req=(p)=>({appId:'semantic-file-manager',intentId:'intent:preview-entry',
            effectId:'effect:preview-entry.request',capabilityId:'capability:filesystem-read',
            operation:'preview',runtimeMethod:'readPreview',risk:'external-read',
            arguments:{rootId:'scratch',relativePath:p}});
          return [await port.authorize(req('../secret.txt')),await port.authorize(req('not-listed.txt')),
                  await port.authorize(req('scratch.txt'))];
        }''')
        _case(cases,'authorization','unobserved-paths-denied',[False,False,True],forged,forged==[False,False,True])
        # A direct browser observation of a binary/non-readable file is not
        # available in this path sequence. Do not synthesize a PASS from source.
       except Exception as exc:
        return {'status':'INCOMPLETE','backend':backend,'cases':cases,'pageErrors':errors,
                'reason':type(exc).__name__+': '+str(exc)}
       finally:
        left.close();right.close();browser.close()
    return {'status':'PASS' if cases and all(c['status']=='PASS' for c in cases) and not any(errors.values()) else 'INCOMPLETE',
            'backend':backend,'cases':cases,'pageErrors':errors}

def assess(repo:Path=ROOT,*,browser:bool=True)->dict:
    report={'schema':'mcel.file-manager-functional-parity-frob.v1',
            'scope':'isolated-read-only-no-production-promotion',
            'canonicalRoute':'/applications/file-explorer',
            'candidateRoute':'/applications/mcel-semantic-file-manager',
            'promotionAuthorized':False,
            'evidenceLimits':['Browser endpoints use deterministic replies backed by temporary real files, not the Windows service',
                              'Incumbent browser runs its real handlers with its supported offline fallback file list',
                              'Both previews run without authenticated production host',
                              'Suggested-app route navigation is not a verified file identity handoff',
                              'Live Windows filesystem identities, user design review and production promotion remain unverified']}
    try:
        report.update(source_inventory(repo))
        report['browser']={'status':'UNVERIFIED','cases':[],'reason':'--no-browser selected'}
        if browser:report['browser']=browser_comparison(repo)
        mapped={}
        for case in report['browser']['cases']:mapped.setdefault(case['feature'],[]).append(case)
        for name,row in report['features'].items():
            witnesses=mapped.get(name,[])
            row['browserScenarios']=[c['id'] for c in witnesses]
            if row['status']=='SOURCE_COVERED':
                if any(c['status']=='MISMATCH' for c in witnesses):row['status']='BROKEN'
                elif witnesses and all(c['status']=='PASS' for c in witnesses):row['status']='PASS_ISOLATED'
                else:row['status']='UNVERIFIED'
        statuses=[r['status'] for r in report['features'].values()]
        counts={s:statuses.count(s) for s in sorted(set(statuses))}
        report['summary']={'featureCount':len(statuses),'byStatus':counts,
             'browserCases':len(report['browser']['cases']),
             'browserMismatches':sum(c['status']=='MISMATCH' for c in report['browser']['cases']),
             'missingFeatures':[k for k,v in report['features'].items() if v['status']=='MISSING'],
             'brokenFeatures':[k for k,v in report['features'].items() if v['status']=='BROKEN'],
             'unverifiedFeatures':[k for k,v in report['features'].items() if v['status']=='UNVERIFIED']}
        report['status']='PASS_ISOLATED' if statuses and all(s=='PASS_ISOLATED' for s in statuses) and report['browser']['status']=='PASS' else 'INCOMPLETE'
    except Exception as exc:
        report['status']='INCOMPLETE';report['error']=type(exc).__name__+': '+str(exc)
    report['liveWindowsParity']='UNVERIFIED';report['designApproval']='UNVERIFIED'
    return report

def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=ROOT)
    parser.add_argument('--report',type=Path,default=REPORT)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args(argv)
    repo=args.repo.resolve()
    result=assess(repo,browser=not args.no_browser)
    dest=args.report if args.report.is_absolute() else repo/args.report
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(result,indent=2,sort_keys=True,ensure_ascii=False)+'\n',encoding='utf-8')
    print('MCEL_FILE_MANAGER_FUNCTIONAL_PARITY '+result['status'])
    for name,row in result.get('features',{}).items():print('  '+name+': '+row['status'])
    print('  browser: '+str(result.get('summary',{}).get('browserCases',0))+' cases; mismatches: '+str(result.get('summary',{}).get('browserMismatches',0)))
    if result.get('error'):print('  error: '+result['error'])
    if result.get('browser',{}).get('reason'):print('  browser: '+result['browser']['reason'])
    print('  report: '+str(dest))
    return 0 if result['status']=='PASS_ISOLATED' else 1

if __name__=='__main__':raise SystemExit(main())
