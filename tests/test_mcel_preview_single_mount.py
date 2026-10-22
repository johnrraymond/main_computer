"""Regression: candidate sweeps reuse an inert measurement tree, with one resize owner."""
from pathlib import Path

from test_mcel_canonical_recursive_layout import _browser
from test_mcel_package_canonical_layout_mount import _page, async_mount, payload

ENGINE = Path(__file__).resolve().parents[1] / 'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js'

TREE = {'schema': 'mcel.layout-tree.v1', 'root': {
    'id': 'workspace', 'selector': '#app', 'role': 'group', 'maxColumns': 2,
    'children': [
        {'id': 'nav', 'selector': '.nav', 'role': 'region'},
        {'id': 'controls', 'selector': '.controls', 'role': 'control-grid', 'maxColumns': 3, 'minTargetWidth': 40},
    ],
}}

MARKUP = '''<style>
 #app {display:grid;width:480px;grid-template-columns:1fr 1fr;gap:6px;}
 .controls {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;min-width:0;}
 button {min-width:0;min-height:40px;padding:7px;font:700 13px Arial;overflow:hidden;}
 </style><div id="app"><aside class="nav">Navigation</aside>
 <section class="controls"><button>Compute</button><button>Verify</button>
 <button>Run</button><button>Store</button></section>
 <iframe id="doc" sandbox="" srcdoc="<script>window.problem=1</script>"></iframe></div>'''


def test_candidate_sweep_mounts_one_inert_tree_and_verifies_winner_independently():
    for browser in _browser():
        page = browser.new_page()
        page.set_content(MARKUP)
        page.add_script_tag(path=str(ENGINE))
        result = page.evaluate('''tree => {
          const host=document.querySelector('#app');
          const original=host.outerHTML;
          const observer=new MutationObserver(()=>{});
          observer.observe(host.parentElement,{childList:true});
          const api=window.McelSemanticLayoutInference;
          const evaluation=api.evaluateCanonicalLayoutTree(host,tree);
          const first=observer.takeRecords();
          const applied=api.applyCanonicalLayoutTree(host,evaluation);
          const second=observer.takeRecords();
          const counts=records=>records.flatMap(record=>[...record.addedNodes])
            .filter(node=>node.getAttribute?.('data-mcel-layout-preview')==='true').length;
          const noFrames=records=>records.flatMap(record=>[...record.addedNodes])
            .filter(node=>node.getAttribute?.('data-mcel-layout-preview')==='true')
            .every(node=>node.querySelectorAll('iframe').length===0);
          api.clearCanonicalLayoutTree(host);
          observer.disconnect();
          return {candidateCount:evaluation.candidates.length,previewCount:counts(first),
            verificationCount:counts(second),noFrames:noFrames(first)&&noFrames(second),
            status:applied.status,selected:evaluation.selected?.choices?.map(c=>c.template),
            untouched:evaluation.applicationAuthorized===false,
            restored:host.outerHTML===original};
        }''', TREE)
        assert result['candidateCount'] > 2, result
        assert result['previewCount'] == 1 and result['verificationCount'] == 1, result
        assert result['noFrames'] and result['status'] == 'pass', result
        assert result['untouched'] and result['restored'], result


def test_standalone_resize_observer_remains_opt_in_when_external_owner():
    for browser in _browser():
        page = browser.new_page()
        page.set_content(MARKUP)
        page.add_script_tag(path=str(ENGINE))
        result=page.evaluate('''tree => {
          const host=document.querySelector('#app'),api=window.McelSemanticLayoutInference;
          const Base=window.ResizeObserver;
          let made=0;
          window.ResizeObserver=class extends Base {constructor(callback){super(callback);made++;}};
          const external=api.mountCanonicalLayoutTree(host,tree,{observeResize:false});
          api.unmountCanonicalLayoutTree(host);
          const before=made;
          const standalone=api.mountCanonicalLayoutTree(host,tree);
          api.unmountCanonicalLayoutTree(host);
          window.ResizeObserver=Base;
          return {external:external.status,observation:external.observation,
            externalObservers:before,standalone:standalone.status,standaloneObservers:made-before};
        }''',TREE)
        assert result == {'external':'pass','observation':'external','externalObservers':0,
                          'standalone':'pass','standaloneObservers':1}, result


def test_code_editor_host_bound_layout_owns_only_one_resize_observer():
    for browser in _browser():
        page=_page(browser,'code-editor')
        page.evaluate('''() => {
          const Original=window.ResizeObserver;window.__observerCount=0;
          window.ResizeObserver=class extends Original {
            constructor(cb){super(cb);window.__observerCount++;}
          };
        }''')
        mounted=page.evaluate(async_mount,payload('code-editor'))
        assert mounted['status']=='deferred',mounted
        page.evaluate('''() => {
          const root=document.querySelector('#code-editor-app');
          root.style.setProperty('display','block','important');
          root.style.width='1220px';root.style.height='800px';root.dataset.mcelLayoutLive='true';
          const rail=root.querySelector('.code-studio-activitybar');
          rail.style.setProperty('display','grid','important');
          rail.style.width='52px';rail.style.height='370px';rail.style.padding='2px';
        }''')
        page.wait_for_function("document.querySelector('#code-editor-app').dataset.mcelHostBoundLayoutStatus === 'pass'",timeout=20000)
        count=page.evaluate('''() => ({total:window.__observerCount,
          status:McelHostBoundApplicationRuntime.getMount('code-editor').layoutStatus})''')
        assert count == {'total':1,'status':'pass'},count
