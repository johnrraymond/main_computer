"""Flat-versus-raised materials are derived from existing shared presentation.

No application-specific runtime attribute, node ID or application implementation
may be needed to keep intentionally flat navigation controls flat.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / 'main_computer/web/applications/scripts'


def test_generic_flat_collection_and_navigation_vs_raised_command():
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        try:
            page = browser.new_page(viewport={'width':1440,'height':900})
            page.set_content(render_expanded_page(ROOT, promoted=True))
            for script in ('mcel-semantic-presentation-bridge.js',
                           'mcel-semantic-chrome-presentation-law.js'):
                page.add_script_tag(path=str(JS / script))
            result = page.evaluate(r'''async () => {
                const root=document.getElementById('mcel-expanded-root');
                const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
                const host=root.querySelector('[data-mcel-region][data-mcel-purpose="input"]');
                host.insertAdjacentHTML('beforeend', `
                  <div data-mcel-node="test:flat-list" data-node-kind="collection">
                    <button id="flat-item" style="border:0;background:transparent;box-shadow:none">Row</button>
                    <button id="boxed-item" style="border:1px solid gold;background:#202b27">Action</button>
                  </div>
                  <div data-mcel-node="test:command" data-node-kind="control">
                    <button id="command" type="button">Execute</button>
                  </div>`);
                const flat=document.getElementById('flat-item');
                const boxed=document.getElementById('boxed-item');
                const command=document.getElementById('command');
                const opts={root,ir,bridge:McelSemanticPresentationBridge,
                  styleLaw:McelLabStyleLaw,theme:'theme-machine',profile:'chrome-tng-side-rails'};
                const before={flat:flat.textContent,boxed:boxed.textContent,command:command.textContent,
                  children:[...flat.childNodes]};
                const applied=McelSemanticChromePresentationLaw.apply(opts);
                const marker=el=>el.getAttribute('data-mcel-chrome-material');
                const initial={flat:marker(flat),boxed:marker(boxed),command:marker(command),
                  text:flat.textContent,children:before.children.every((node,i)=>flat.childNodes[i]===node),
                  runtimeHints:root.querySelectorAll('[data-mcel-control-surface]').length};
                flat.setAttribute('aria-selected','true');
                await new Promise(done=>setTimeout(done,80));
                const selected=marker(flat);
                const extra=document.createElement('button');extra.textContent='New entry';
                extra.style.cssText='background:transparent;border:0;box-shadow:none';
                flat.parentElement.appendChild(extra);
                await new Promise(done=>setTimeout(done,80));
                const dynamic=marker(extra);
                const off=McelSemanticChromePresentationLaw.apply({...opts,profile:'chrome-strict-hierarchy'});
                return {applied:applied.status,initial,selected,dynamic,off:off.status,
                  offMarkers:[marker(flat),marker(boxed),marker(command),marker(extra)]};
              }''')
            assert result['applied']=='applied',result
            assert result['initial']=={'flat':None,'boxed':'raised-control','command':'raised-control',
                                      'text':'Row','children':True,'runtimeHints':0},result
            assert result['selected'] is None and result['dynamic'] is None,result
            assert result['off']=='unchanged' and result['offMarkers']==[None]*4,result
        finally:
            browser.close()
