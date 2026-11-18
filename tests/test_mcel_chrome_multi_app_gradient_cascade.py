"""TNG lighting must survive the Lab's projected Theme and Form reset layers.

Each app is an authentic, unrelated operational MCEL mount; assertions inspect
computed paint, not only Chrome material marker coverage.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'main_computer/web/applications/scripts'
HOSTS = ('file-manager', 'task-manager', 'code-editor')


def _bootstrap(page, app):
    html = render_semantic_page(app, ROOT, promoted=True)
    # In production the MCEL Lab supplies ?mcel_lab_theme_preview=1.
    html = html.replace('</head>', '<script>window.__mcelSemanticColorLabPreview=true;</script></head>')
    page.set_content(html)
    for filename in (
        'mcel-semantic-layout-inference.js',
        'mcel-semantic-presentation-bridge.js',
        'mcel-semantic-theme-preview.js',
        'mcel-semantic-form-presentation-law.js',
        'mcel-semantic-chrome-presentation-law.js',
    ):
        page.add_script_tag(path=str(SCRIPTS / filename))
    page.wait_for_timeout(350)  # allow the operational mount's initial layout to settle


APPLY = r'''({theme='theme-machine',form='form-current'}) => {
  const root=document.getElementById('mcel-workbench');
  const ir=JSON.parse(document.getElementById('mcel-workbench-ir').textContent);
  const bridge=McelSemanticPresentationBridge,styleLaw=McelLabStyleLaw;
  const params={root,ir,bridge,styleLaw,theme};
  const colors=styleLaw.themePaletteFor(theme);
  const selected=()=>root.querySelector('button[data-mcel-chrome-material="raised-control"]') ||
    root.querySelector('button[data-mcel-chrome-material="emissive-control"]');
  const themed=McelSemanticThemePreview.apply({...params,
    bindingModel:'operational-runtime',tokens:colors});
  const shaped=McelSemanticFormPresentationLaw.apply({root,ir,bridge,profile:form});
  const geometry=el=>{const b=el.getBoundingClientRect();return [b.x,b.y,b.width,b.height]};
  const styles=new Map([...root.querySelectorAll('button')].map(source=>[source,{
    bg:getComputedStyle(source).backgroundColor,
    fg:getComputedStyle(source).color,border:getComputedStyle(source).borderColor,
    rect:geometry(source)
  }]));
  const chrome=McelSemanticChromePresentationLaw.apply({...params,
    profile:'chrome-tng-side-rails'});
  const source=selected();
  const original=styles.get(source);
  return {theme:themed.status,form:shaped.status,chrome:chrome.status,
    railCount:chrome.rails,original,
    controlCount:root.querySelectorAll('[data-mcel-chrome-material="raised-control"],'+
      '[data-mcel-chrome-material="emissive-control"]').length,
    fieldCount:root.querySelectorAll('[data-mcel-chrome-material="recessed-field"]').length,
    housingCount:root.querySelectorAll('[data-mcel-chrome-material="housing"],'+
      '[data-mcel-chrome-material="instrument-glass"]').length,
    sourceMatchesMaterial:source.hasAttribute('data-mcel-chrome-material')};
}'''


MEASURE = r'''() => {
 const root=document.getElementById('mcel-workbench');
 const c=root.querySelector('button[data-mcel-chrome-material]');
 const f=root.querySelector('[data-mcel-chrome-material="recessed-field"]');
 const h=root.querySelector('[data-mcel-chrome-material="housing"]');
 const img=el=>el?getComputedStyle(el).backgroundImage:null;
 const shadow=el=>el?getComputedStyle(el).boxShadow:null;
 const rect=el=>{const b=el.getBoundingClientRect();return [b.x,b.y,b.width,b.height]};
 return {control:img(c),field:img(f),housing:img(h),
   shadow:shadow(c),housingShadow:shadow(h),latched:c.matches('[aria-pressed="true"], [aria-selected="true"]'),
   angle:getComputedStyle(c).getPropertyValue('--mcel-tng-light-angle'),
   fg:getComputedStyle(c).color,bg:getComputedStyle(c).backgroundColor,
   border:getComputedStyle(c).borderColor,rect:rect(c),
   surface:c?.querySelector(':scope > [data-mcel-tng-wear-surface]')?.getAttribute('data-mcel-tng-wear-surface')};
}'''


def test_three_real_mounted_apps_keep_tng_gradients_and_theme_colors():
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        for app in HOSTS:
            page = browser.new_page(viewport={'width': 1080, 'height': 780})
            _bootstrap(page, app)
            result = page.evaluate(APPLY, {'theme': 'theme-machine', 'form': 'form-current'})
            assert result['theme'] == 'applied-in-isolated-lab-frame', (app, result)
            assert result['form'] == 'applied-in-isolated-lab-frame', (app, result)
            assert result['chrome'] == 'applied', (app, result)
            assert result['railCount'] == 0, (app, result)
            assert result['controlCount'] and result['fieldCount'] and result['housingCount'], (app, result)
            page.wait_for_timeout(250)  # allow material shadows to finish their transition
            after = page.evaluate(MEASURE)
            for key in ('control', 'field', 'housing'):
                assert 'gradient(' in after[key], (app, key, after)
            assert '145deg' in after['control'], (app, after)
            # A selected/latched tab is already recessed; an unselected
            # control remains raised. Both must retain TNG lighting.
            expected_shadow_alpha = '0.68' if after['latched'] else '0.47'
            assert after['shadow'] != 'none' and expected_shadow_alpha in after['shadow'], (app, after)
            assert after['surface'] == 'button', (app, after)
            assert (after['bg'], after['fg'], after['border'], after['rect']) == (
                result['original']['bg'], result['original']['fg'],
                result['original']['border'], result['original']['rect']
            ), (app, result, after)

            # Theme and Form may reapply after Chrome has mounted. Their
            # optical resets must continue respecting Chrome material markers.
            page.evaluate('''() => {
                const root=document.getElementById('mcel-workbench');
                const ir=JSON.parse(document.getElementById('mcel-workbench-ir').textContent);
                McelSemanticThemePreview.apply({root,ir,bridge:McelSemanticPresentationBridge,
                  styleLaw:McelLabStyleLaw,theme:'theme-machine',bindingModel:'operational-runtime',
                  tokens:McelLabStyleLaw.themePaletteFor('theme-machine')});
                McelSemanticFormPresentationLaw.apply({root,ir,
                  bridge:McelSemanticPresentationBridge,profile:'form-current'});
            }''')
            page.wait_for_timeout(250)
            again = page.evaluate(MEASURE)
            for key in ('control', 'field', 'housing'):
                assert 'gradient(' in again[key], (app, key, again)
            # Turning Chrome off must restore the Theme/Form base paint.
            clean = page.evaluate('''() => {
              const root=document.getElementById('mcel-workbench');
              McelSemanticChromePresentationLaw.clear(root);
              const button=root.querySelector('button');
              return {chrome:root.hasAttribute('data-mcel-semantic-chrome'),
                markers:root.querySelectorAll('[data-mcel-chrome-material]').length,
                wear:root.querySelectorAll('[data-mcel-tng-wear-surface]').length,
                image:getComputedStyle(button).backgroundImage};
            }''')
            assert not clean['chrome'] and clean['markers'] == clean['wear'] == 0, (app, clean)
            assert clean['image'] == 'none', (app, clean)
            page.close()
        browser.close()


def test_other_theme_and_form_profile_keep_material_light():
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        page=browser.new_page(viewport={'width':1024,'height':760})
        _bootstrap(page,'task-manager')
        for theme in ('theme-machine','theme-luxury','theme-accessible'):
            for profile in ('form-soft','form-angular'):
                result=page.evaluate(APPLY,{'theme':theme,'form':profile})
                assert result['chrome']=='applied' and result['form']=='applied-in-isolated-lab-frame', (theme,profile,result)
                measure=page.evaluate(MEASURE)
                assert 'gradient(' in measure['control'] and 'gradient(' in measure['housing'], (theme,profile,measure)
        browser.close()


def test_source_bound_workspace_controls_and_all_named_regions_receive_chrome():
    """Regression: nested workspace tools must not lose physical materials.

    This failed on the original snapshot for Task Manager's refresh/tabs
    and Code Editor's document buttons. Native disclosure carets are not
    raised controls; their markers already indicate interactivity.
    No semantic intent is inferred from a native widget's label.
    """
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        for app in HOSTS:
            page = browser.new_page(viewport={'width': 1280, 'height': 800})
            _bootstrap(page, app)
            applied = page.evaluate(APPLY, {'theme': 'theme-machine', 'form': 'form-current'})
            assert applied['chrome'] == 'applied', (app, applied)
            report = page.evaluate(r'''() => {
              const root=document.getElementById('mcel-workbench');
              const ir=JSON.parse(document.getElementById('mcel-workbench-ir').textContent);
              const observed=McelSemanticPresentationBridge.observeRuntimePresentationTargets({root,ir});
              const regions=observed.targets.filter(x=>x.kind==='region').map(x=>x.element);
              const isBound=el=>regions.some(region=>region.contains(el));
              const isVisible=el=>el.getClientRects().length>0 &&
                getComputedStyle(el).visibility!=='hidden';
              const controls=[...root.querySelectorAll(
                'button,input,select,textarea,[role="button"],[role="tab"],'+
                '[role="switch"],[role="textbox"],[role="combobox"]')]
                .filter(el=>isBound(el) && isVisible(el) && el.localName!=='summary' &&
                  !(el.localName==='input' &&
                    ['hidden','checkbox','radio','file','range','color','image']
                      .includes((el.type||'text').toLowerCase())));
              const missing=controls.filter(el=>
                !['raised-control','emissive-control','recessed-field']
                  .includes(el.getAttribute('data-mcel-chrome-material')) ||
                !getComputedStyle(el).backgroundImage.includes('gradient('));
              const disclosures=[...root.querySelectorAll('details > summary')]
                .filter(el=>isBound(el) && isVisible(el));
              const boxedDisclosures=disclosures.filter(el=>
                el.hasAttribute('data-mcel-chrome-material') ||
                !!el.querySelector(':scope > [data-mcel-tng-wear-surface="button"]'));
              const surfaces=regions.filter(el=>
                el.getAttribute('data-mcel-purpose')!=='workspace');
              const unpainted=surfaces.filter(el=>
                !['housing','instrument-glass'].includes(el.getAttribute('data-mcel-chrome-material')) ||
                !getComputedStyle(el).backgroundImage.includes('gradient('));
              // The semantic workspace, rather than the whole root, is the
              // source-bound boundary. An unrelated root child is left alone.
              const probe=document.createElement('button');
              probe.id='out-of-ir-control';probe.textContent='Unrelated';
              root.appendChild(probe);
              return {controls:controls.length, missing:missing.map(el=>el.outerHTML.slice(0,150)),
                disclosures:disclosures.length,boxedDisclosures:boxedDisclosures.map(el=>el.outerHTML.slice(0,150)),
                surfaces:surfaces.length, unpainted:unpainted.map(el=>el.getAttribute('data-mcel-purpose')),
                probe:probe.id};
            }''')
            assert report['controls'] >= 10 and report['surfaces'] >= 3, (app, report)
            assert not report['missing'] and not report['unpainted'], (app, report)
            assert report['disclosures'] and not report['boxedDisclosures'], (app, report)
            page.wait_for_timeout(80)
            assert page.locator('#out-of-ir-control').get_attribute('data-mcel-chrome-material') is None
            page.close()
        browser.close()


def test_real_mounted_app_buttons_snap_recessed_on_mouse_down():
    """File Manager, Task Manager, and Code Editor get a real depressed face."""
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        try:
            for app in HOSTS:
                page = browser.new_page(viewport={'width': 1080, 'height': 780})
                try:
                    _bootstrap(page, app)
                    result = page.evaluate(APPLY, {'theme': 'theme-machine', 'form': 'form-current'})
                    assert result['chrome'] == 'applied', (app, result)
                    selected = page.evaluate('''() => {
                      const root=document.getElementById('mcel-workbench');
                      const button=[...root.querySelectorAll('button[data-mcel-chrome-material]')]
                        .find(el=>!el.disabled && el.getAttribute('aria-selected')!=='true' &&
                          el.getAttribute('aria-pressed')!=='true' &&
                          el.getClientRects().length &&
                          getComputedStyle(el).visibility!=='hidden');
                      if (!button) return null;
                      button.id='mcel-recess-probe';
                      const style=getComputedStyle(button);
                      return {width:button.offsetWidth,height:button.offsetHeight,
                        background:style.backgroundColor,color:style.color,
                        border:style.borderColor};
                    }''')
                    assert selected is not None, app
                    button=page.locator('#mcel-recess-probe')
                    button.hover()
                    page.wait_for_timeout(130)
                    hovered=button.evaluate('''el=>({shadow:getComputedStyle(el).boxShadow,
                      image:getComputedStyle(el).backgroundImage})''')
                    page.mouse.down()
                    depressed=button.evaluate('''el=>({active:el.matches(':active'),
                      shadow:getComputedStyle(el).boxShadow,
                      image:getComputedStyle(el).backgroundImage,
                      width:el.offsetWidth,height:el.offsetHeight,
                      background:getComputedStyle(el).backgroundColor,
                      color:getComputedStyle(el).color,
                      border:getComputedStyle(el).borderColor})''')
                    assert depressed['active'], (app, depressed)
                    assert depressed['shadow'] != hovered['shadow'], (app, hovered, depressed)
                    assert '3px 3px 6px' in depressed['shadow'], (app, depressed)
                    assert 'radial-gradient(' in depressed['image'], (app, depressed)
                    # Color Law may legitimately change pigment per state;
                    # Chrome must not move or resize the physical control.
                    assert (depressed['width'],depressed['height']) == (
                        selected['width'],selected['height']), (app, selected, depressed)
                    page.mouse.move(0, 0)
                    page.mouse.up()
                    page.wait_for_timeout(140)
                    raised=button.evaluate('''el=>({active:el.matches(':active'),
                      shadow:getComputedStyle(el).boxShadow})''')
                    assert not raised['active'] and '3px 3px 6px' not in raised['shadow'], (app, raised)
                finally:
                    page.close()
        finally:
            browser.close()
