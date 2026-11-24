"""CRT paint boundaries follow visible material framing, not application names.

Integration uses the SLH page because it has both a transparent content stream
and visually framed peer panels, then injects app-neutral semantic surfaces to
exercise the shared rule, dynamic updates, and cleanup.
"""
import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
from main_computer.mcel_slh_application import render_slh_page
from tests.test_mcel_slh_startup_ewg import MOCK

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'main_computer/web/applications/scripts/mcel-semantic-chrome-presentation-law.js'


def test_boundary_law_has_no_app_specific_exception():
    src=SOURCE.read_text(encoding='utf-8')
    assert 'surface-node:slh.activity' not in src
    assert 'data-node-kind="operation-evidence"' not in src
    assert 'inheritsEnclosingCRT(' in src
    assert 'new root.ownerDocument.defaultView.CSSStyleSheet()' in src
    assert 'inheritsEnclosingCRT(' in src


def test_generic_unframed_content_inherits_while_real_surfaces_stay_independent():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1440,'height':900})
            page.evaluate(MOCK)
            page.set_content(render_slh_page(repo=ROOT))
            page.wait_for_function('() => !!window.__mcelSlh?.app')
            report=page.evaluate(r'''async () => {
              const root=document.querySelector('#mcel-workbench');
              const activity=root.querySelector('[data-node-kind="operation-evidence"]');
              const frame=activity.closest('[data-mcel-region]');
              const collection=root.querySelector('[data-node-kind="collection"]');
              const css=el=>{const s=getComputedStyle(el);return {
                color:s.backgroundColor,image:s.backgroundImage,shadow:s.boxShadow,
                radius:s.borderRadius,border:s.borderWidth}};
              const marker=el=>el.getAttribute('data-mcel-chrome-surface-boundary');
              const first={activity:css(activity),marker:marker(activity),
                collection:css(collection),collectionMarker:marker(collection),
                frame:css(frame),frameMarker:marker(frame)};
              const named = suffix => {
                const el=document.createElement('div');
                el.setAttribute('data-mcel-node',`surface-node:test-${suffix}`);
                el.setAttribute('data-mcel-color-role','surface.panel');
                el.setAttribute('data-mcel-color-state',activity.getAttribute('data-mcel-color-state'));
                el.replaceChildren(document.createElement('section'),document.createElement('details'));
                return el;
              };
              const plain=named('plain');
              const round=named('round'); round.style.borderRadius='6px';
              const border=named('border');border.style.border='1px solid rgb(100,100,100)';
              const elevated=named('elevated');elevated.style.boxShadow='inset 0 0 4px red';
              const explicit=named('explicit');explicit.setAttribute('data-mcel-material-boundary','independent');
              frame.append(plain,round,border,elevated,explicit);
              await new Promise(ok=>setTimeout(ok,140));
              const initial=Object.fromEntries([
                ['plain',plain],['round',round],['border',border],
                ['elevated',elevated],['explicit',explicit]
              ].map(([k,v])=>[k,{marker:marker(v),paint:css(v)}]));
              // A runtime change to the *physical* boundary must restore its
              // independent paint even though the semantic color role is the same.
              plain.style.border='1px solid rgb(100,100,100)';
              await new Promise(ok=>setTimeout(ok,140));
              const raised={marker:marker(plain),paint:css(plain)};
              plain.style.border='none';
              await new Promise(ok=>setTimeout(ok,140));
              const flattened={marker:marker(plain),paint:css(plain)};
              plain.setAttribute('data-mcel-color-role','text.primary');
              await new Promise(ok=>setTimeout(ok,140));
              const changedRole={marker:marker(plain),paint:css(plain)};
              const otherControls=root.querySelectorAll('[data-mcel-activity-level-toggle]');
              const btn=otherControls[0];
              const buttonBefore={text:btn.textContent,children:btn.childNodes.length,paint:css(btn)};
              btn.click();
              const buttonAfter={text:btn.textContent,children:btn.childNodes.length,paint:css(btn)};
              // Non-TNG chrome must not use the physical boundary override.
              root.setAttribute('data-mcel-semantic-chrome','chrome-strict-hierarchy');
              const nonTng={marker:marker(activity),paint:css(activity)};
              root.setAttribute('data-mcel-semantic-chrome','chrome-tng-side-rails');
              const restored={marker:marker(activity),paint:css(activity)};
              [plain,round,border,elevated,explicit].forEach(el=>el.remove());
              return {first,initial,raised,flattened,changedRole,buttonBefore,buttonAfter,nonTng,restored};
            }''')
            assert report['first']['marker'] is None,report
            assert report['first']['activity']['color']=='rgba(0, 0, 0, 0)',report
            assert report['first']['activity']['image']=='none',report
            assert report['first']['activity']['shadow']=='none',report
            assert report['first']['collectionMarker'] is None,report
            assert report['first']['collection']['color']!='rgba(0, 0, 0, 0)',report
            assert report['first']['collection']['radius'] != '0px',report
            assert report['first']['frameMarker'] is None,report
            assert report['first']['frame']['color']!='rgba(0, 0, 0, 0)',report
            assert report['initial']['plain']['marker'] is None,report
            assert report['initial']['plain']['paint']['color']=='rgba(0, 0, 0, 0)',report
            for kind in ['round','border','elevated','explicit']:
                assert report['initial'][kind]['marker'] is None,(kind,report)
                assert report['initial'][kind]['paint']['color']!='rgba(0, 0, 0, 0)',(kind,report)
            assert report['raised']['marker'] is None,report
            assert report['raised']['paint']['color']!='rgba(0, 0, 0, 0)',report
            assert report['flattened']['marker'] is None,report
            assert report['flattened']['paint']['color']=='rgba(0, 0, 0, 0)',report
            assert report['changedRole']['marker'] is None,report
            assert report['changedRole']['paint']['color']!='rgba(0, 0, 0, 0)',report
            assert report['buttonBefore']['text']==report['buttonAfter']['text'],report
            assert report['buttonBefore']['children']==report['buttonAfter']['children'],report
            assert report['nonTng']['paint']['color']!='rgba(0, 0, 0, 0)',report
            assert report['restored']['paint']['color']=='rgba(0, 0, 0, 0)',report
        finally:
            browser.close()


def test_generic_boundary_stylesheet_does_not_leak_on_chrome_switch():
    from main_computer.mcel_calculator_expanded_host import render_expanded_page
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            page=browser.new_page(viewport={'width':1300,'height':850})
            page.set_content(render_expanded_page(ROOT,promoted=True))
            for name in ('mcel-semantic-presentation-bridge.js',
                         'mcel-semantic-chrome-presentation-law.js'):
                page.add_script_tag(path=str(ROOT/'main_computer/web/applications/scripts'/name))
            report=page.evaluate('''() => {
              const root=document.getElementById('mcel-expanded-root');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const bridge=McelSemanticPresentationBridge;
              const opts={root,ir,bridge,styleLaw:McelLabStyleLaw,
                theme:'theme-machine',profile:'chrome-tng-side-rails'};
              const prior=document.adoptedStyleSheets.length;
              const control=root.querySelector('button');
              const children=[...control.childNodes];
              const before=control.textContent;
              const applied=McelSemanticChromePresentationLaw.apply(opts);
              const mounted=document.adoptedStyleSheets.length;
              const off=McelSemanticChromePresentationLaw.apply({...opts,
                profile:'chrome-strict-hierarchy'});
              const cleared=document.adoptedStyleSheets.length;
              return {prior,mounted,cleared,applied:applied.status,off:off.status,
                sameControlText:control.textContent===before,
                sameControlNodes:children.length===control.childNodes.length &&
                  children.every((n,i)=>n===control.childNodes[i])};
            }''')
            assert report['applied']=='applied',report
            assert report['off']=='unchanged',report
            assert report['mounted']==report['prior']+1,report
            assert report['cleared']==report['prior'],report
            assert report['sameControlText'] and report['sameControlNodes'],report
        finally:
            browser.close()
