"""TNG material composition follows the mounted operational MCEL IR, not Calculator IDs."""
from pathlib import Path

from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'main_computer/web/applications/scripts'
MATERIAL = 'data-mcel-chrome-material'


def _page(browser):
    page = browser.new_page(viewport={'width': 1440, 'height': 850}, device_scale_factor=.75)
    page.evaluate('''() => {for (const name of ['localStorage','sessionStorage']) {
      const values=new Map();Object.defineProperty(window,name,{configurable:true,value:{
        getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,String(v)),
        removeItem:k=>values.delete(k),clear:()=>values.clear()}});}}''')
    page.set_content(render_expanded_page(ROOT, promoted=True))
    assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
    for name in ('mcel-semantic-presentation-bridge.js', 'mcel-semantic-chrome-presentation-law.js'):
        page.add_script_tag(path=str(WEB / name))
    return page


def test_mounted_calculator_every_canonical_region_and_control_material():
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = _page(browser)
        data = page.evaluate('''() => {
          const root=document.getElementById('mcel-expanded-root');
          const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
          const bridge=McelSemanticPresentationBridge;
          const tracked=[...root.querySelectorAll('button, input:not([type=hidden]), textarea, select')]
            .filter(el=>el.closest('[data-mcel-region][data-mcel-purpose]:not([data-mcel-purpose="workspace"])') &&
              !(el.localName==='input' && ['hidden','checkbox','radio','file','range','color','image'].includes(el.type)));
          const before=new Map(tracked.map(el=>[el,{
            rect:[el.offsetWidth,el.offsetHeight], bg:getComputedStyle(el).backgroundColor,
            fg:getComputedStyle(el).color,border:getComputedStyle(el).borderColor}]));
          const outcome=McelSemanticChromePresentationLaw.apply({root,ir,bridge,
            styleLaw:McelLabStyleLaw,theme:'theme-machine',profile:'chrome-tng-side-rails'});
          const entries=tracked.map(el=>({tag:el.tagName, material:el.getAttribute('data-mcel-chrome-material'),
            before:before.get(el),after:{rect:[el.offsetWidth,el.offsetHeight],
              bg:getComputedStyle(el).backgroundColor,fg:getComputedStyle(el).color,
              border:getComputedStyle(el).borderColor}}));
          const regions=[...root.querySelectorAll('[data-mcel-region]')].map(el=>({
            purpose:el.getAttribute('data-mcel-purpose'),material:el.getAttribute('data-mcel-chrome-material')}));
          return {outcome,entries,regions};
        }''')
        assert data['outcome']['status'] == 'applied'
        assert data['outcome']['rails'] == 2
        assert len(data['entries']) >= 50, data
        for entry in data['entries']:
            assert entry['material'] in ('raised-control', 'emissive-control', 'recessed-field'), entry
            assert entry['before'] == entry['after'], entry
        assert any(x['purpose'] == 'assistance' and x['material'] == 'housing' for x in data['regions'])
        assert any(x['purpose'] == 'results' and x['material'] == 'instrument-glass' for x in data['regions'])
        assert any(x['purpose'] == 'inspection' and x['material'] == 'housing' for x in data['regions'])
        # Distinct modes expose distinct controls, but the assignment rule is shared.
        for mode in ('Graphing', 'Symbolic', 'Help', 'Basic'):
            page.get_by_role('button', name=mode, exact=True).click()
            checks=page.evaluate('''() => {
              const root=document.getElementById('mcel-expanded-root');
              return [...root.querySelectorAll('button')].filter(el=>el.getClientRects().length &&
                el.closest('[data-mcel-region][data-mcel-purpose]:not([data-mcel-purpose="workspace"])'))
                .map(el=>({mat:el.getAttribute('data-mcel-chrome-material'),
                  wear:!!el.querySelector(':scope > [data-mcel-tng-wear-surface="button"]')}));
            }''')
            assert checks and all(x['mat'] in ('raised-control', 'emissive-control') and x['wear'] for x in checks), (mode, checks)
        page.locator('[data-mcel-node$="arithmetic-expression"] input').fill('2+3')
        page.locator('[data-mcel-node$="arithmetic-expression"] button[type="submit"]').click()
        page.wait_for_function("() => document.querySelector('[data-mcel-node$=calculated-value]')?.textContent?.trim()==='5'")
        browser.close()


def test_unknown_mounted_application_receives_material_without_calculator_or_rails():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=_page(browser)
        result=page.evaluate('''() => {
          const host=document.createElement('div');
          host.id='unfamiliar-mounted-app';
          host.innerHTML=`<main data-mcel-region="interface-region:unfamiliar.workspace" data-mcel-purpose="workspace">
            <section data-mcel-region="interface-region:unfamiliar.tools" data-mcel-purpose="input">
              <div data-mcel-node="surface-node:unfamiliar.action" data-node-kind="control">
                <button id="unfamiliar-run">Launch</button><input id="unfamiliar-query" value="abc">
              </div>
            </section>
            <output id="unfamiliar-result" data-mcel-region="interface-region:unfamiliar.results" data-mcel-purpose="results">Done</output>
            <aside id="unfamiliar-audit" data-mcel-region="interface-region:unfamiliar.audit" data-mcel-purpose="inspection">Log</aside>
          </main>`;
          document.body.appendChild(host);
          const ir={schema:'mcel.application-ir.v1',application:{appId:'unfamiliar-tool'},
            surfaces:[{nodes:[{id:'surface-node:unfamiliar.action',nodeKind:'control',intent:{ref:'intent:unfamiliar.launch'}}],
              interfaceIntent:{regions:[
                {id:'interface-region:unfamiliar.workspace',purpose:'workspace'},
                {id:'interface-region:unfamiliar.tools',purpose:'input'},
                {id:'interface-region:unfamiliar.results',purpose:'results'},
                {id:'interface-region:unfamiliar.audit',purpose:'inspection'}]}}],
            intents:[{id:'intent:unfamiliar.launch',operationKind:'invoke'}]};
          const bridge=McelSemanticPresentationBridge,styleLaw=McelLabStyleLaw;
          const source=bridge.observeRuntimePresentationTargets({root:host,ir});
          const els=['unfamiliar-run','unfamiliar-query','unfamiliar-result','unfamiliar-audit']
            .map(id=>document.getElementById(id));
          const before=els.map(el=>({bg:getComputedStyle(el).backgroundColor,
            color:getComputedStyle(el).color,border:getComputedStyle(el).borderColor,
            width:el.offsetWidth,height:el.offsetHeight}));
          const applied=McelSemanticChromePresentationLaw.apply({root:host,ir,bridge,styleLaw,
            profile:'chrome-tng-side-rails',theme:'theme-machine'});
          const materials=els.map(el=>el.getAttribute('data-mcel-chrome-material'));
          const after=els.map(el=>({bg:getComputedStyle(el).backgroundColor,
            color:getComputedStyle(el).color,border:getComputedStyle(el).borderColor,
            width:el.offsetWidth,height:el.offsetHeight}));
          const button=els[0], wear=!!button.querySelector('[data-mcel-tng-wear-surface="button"]');
          const replacement=button.cloneNode(false);
          button.replaceWith(replacement);
          return {source:source.status,applied,materials,before,after,wear};
        }''')
        # Keep DOM refs in the page, return only serializable information.
        assert result['source'] == 'source-bound', result
        assert result['applied']['status'] == 'applied' and result['applied']['rails'] == 0, result
        assert result['materials'] == ['raised-control','recessed-field','instrument-glass','housing'],result
        assert result['before'] == result['after'],result
        assert result['wear'],result
        # Runtime replacements reconcile automatically, without application callbacks.
        page.wait_for_function('''() => !!document.querySelector('#unfamiliar-run > [data-mcel-tng-wear-surface="button"]')''')
        cleanup=page.evaluate('''() => {
          const host=document.getElementById('unfamiliar-mounted-app');
          const before=host.querySelectorAll('[data-mcel-chrome-material]').length;
          const removed=McelSemanticChromePresentationLaw.clear(host);
          return {before,removed,remaining:host.querySelectorAll('[data-mcel-chrome-material],[data-mcel-tng-wear-surface]').length,
            active:host.hasAttribute('data-mcel-semantic-chrome')};
        }''')
        assert cleanup['before'] >= 5 and cleanup['removed'] == 0 and cleanup['remaining'] == 0 and not cleanup['active'], cleanup
        browser.close()
