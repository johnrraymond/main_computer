"""Accepted TNG wear twiddle promoted to Chrome, without taking Color/Form authority."""
from pathlib import Path

from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'main_computer/web/applications'
THEMES = ('theme-machine', 'theme-local', 'theme-saas', 'theme-editorial',
          'theme-luxury', 'theme-civic', 'theme-accessible', 'theme-debug')
SURFACE = '[data-mcel-tng-wear-surface]'


def _page(browser):
    page = browser.new_page(viewport={'width': 1440, 'height': 850}, device_scale_factor=.75)
    page.evaluate('''() => {for (const name of ['localStorage','sessionStorage']) {
      const values=new Map();Object.defineProperty(window,name,{configurable:true,value:{
        getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,String(v)),
        removeItem:k=>values.delete(k),clear:()=>values.clear()}});}}''')
    page.set_content(render_expanded_page(ROOT, promoted=True))
    assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
    for name in ('mcel-lab.css',):
        page.add_style_tag(path=str(WEB / 'styles' / name))
    for name in ('mcel-semantic-presentation-bridge.js', 'mcel-semantic-theme-preview.js',
                 'mcel-semantic-chrome-presentation-law.js'):
        page.add_script_tag(path=str(WEB / 'scripts' / name))
    return page


SETUP = '''theme => {
  const root=document.getElementById('mcel-expanded-root'),
    ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent),
    bridge=McelSemanticPresentationBridge,styleLaw=McelLabStyleLaw;
  const unit=root.querySelector('[data-mcel-working-unit][data-mcel-sizing="intrinsic"]');
  const buttons=[...root.querySelectorAll('[data-mcel-working-unit] button, [data-mcel-mode-tabs] button')];
  const rect=el=>{const r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height]};
  const original=buttons.map(el=>({rect:rect(el),color:getComputedStyle(el).color,
    bg:getComputedStyle(el).backgroundColor,border:getComputedStyle(el).borderColor,
    nativeChildren:el.children.length,position:el.style.getPropertyValue('position')}));
  const projected=McelSemanticThemePreview.apply({root,ir,bridge,styleLaw,
    bindingModel:'operational-runtime',theme,tokens:styleLaw.themePaletteFor(theme)});
  const before=buttons.map(el=>({rect:rect(el),color:getComputedStyle(el).color,
    bg:getComputedStyle(el).backgroundColor,border:getComputedStyle(el).borderColor}));
  const response=McelSemanticChromePresentationLaw.apply({root,ir,bridge,styleLaw,
    theme,profile:'chrome-tng-side-rails'});
  window.__wearFixture={root,ir,bridge,styleLaw,unit,buttons};
  const after=buttons.map(el=>({rect:rect(el),color:getComputedStyle(el).color,
    bg:getComputedStyle(el).backgroundColor,border:getComputedStyle(el).borderColor,
    nativeChildren:original[buttons.indexOf(el)].nativeChildren,
    surface:el.querySelector(':scope > [data-mcel-tng-wear-surface="button"]')!==null}));
  const rails=[...unit.querySelectorAll('[data-mcel-chrome-generated="true"]')];
  const railWear=rails.map(rail=>{
    const surface=rail.querySelector(':scope > [data-mcel-tng-wear-surface="rail"]');
    const glint=surface?.querySelector(':scope > [data-mcel-tng-wear-glint]');
    return {surface:!!surface, glint:!!glint, pointer:surface&&getComputedStyle(surface).pointerEvents,
      mask:surface&&getComputedStyle(surface).maskImage,
      texture:surface&&getComputedStyle(surface).backgroundImage,
      reflection:glint&&getComputedStyle(glint).backgroundImage,
      lightAngle:glint&&getComputedStyle(glint).maskImage,
      aria:surface?.getAttribute('aria-hidden'), inert:surface?.hasAttribute('inert')};
  });
  return {response,projected:projected.status,before,after,railWear,
    count:root.querySelectorAll('[data-mcel-tng-wear-surface]').length,
    buttons:buttons.length,rails:rails.length};
}'''


def test_wear_material_matches_twiddle_and_preserves_semantic_colors():
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = _page(browser)
        for theme in THEMES:
            data = page.evaluate('(' + SETUP + ')', theme)
            assert data['response']['status'] == 'applied', (theme, data)
            assert data['rails'] == 2, (theme, data)
            material = data['response']['materialCoverage']['materials']
            assert data['count'] == (material.get('raised-control', 0) +
                material.get('emissive-control', 0) + 2), (theme, data)
            assert data['count'] > data['buttons'] + 2, (theme, data)
            assert data['projected'] == 'applied-in-isolated-lab-frame', (theme, data)
            assert all(a['surface'] for a in data['after']), (theme, data)
            for before, after in zip(data['before'], data['after']):
                assert before['rect'][2:] == after['rect'][2:], (theme, before, after)
                assert (before['bg'], before['color'], before['border']) == (
                    after['bg'], after['color'], after['border']), (theme, before, after)
            for wear in data['railWear']:
                assert wear['surface'] and wear['glint'], (theme, wear)
                assert wear['pointer'] == 'none' and wear['aria'] == 'true' and wear['inert']
                assert 'linear-gradient' in wear['mask'] and 'rgba(0, 0, 0, 0)' in wear['mask']
                assert 'data:image/svg+xml' in wear['texture']
                assert 'data:image/svg+xml' in wear['reflection']
                assert '145deg' in wear['lightAngle']
        page.close()
        browser.close()


def test_wear_glints_respond_to_light_and_hover_without_intercepting_clicks():
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = _page(browser)
        page.evaluate('(' + SETUP + ')', 'theme-machine')
        button = page.locator('[data-mcel-expression-pad] button').first
        glint = button.locator('[data-mcel-tng-wear-glint]')
        assert glint.count() == 1
        initial = glint.evaluate('(el)=>Number(getComputedStyle(el).opacity)')
        assert .20 < initial < .30, initial
        button.hover()
        page.wait_for_timeout(190)
        hover = glint.evaluate('(el)=>Number(getComputedStyle(el).opacity)')
        assert .80 < hover < .90, (initial, hover)
        page.mouse.down()
        page.wait_for_timeout(190)
        active = glint.evaluate('(el)=>Number(getComputedStyle(el).opacity)')
        assert .80 < active < .90, (hover, active)
        page.mouse.up()
        page.locator('[data-mcel-node$="arithmetic-expression"] input').fill('2+3')
        page.locator('[data-mcel-node$="arithmetic-expression"] button[type="submit"]').click()
        page.wait_for_function("() => document.querySelector('[data-mcel-node$=calculated-value]')?.textContent?.trim()==='5'")
        page.emulate_media(forced_colors='active')
        assert page.locator(SURFACE).first.evaluate('el=>getComputedStyle(el).display') == 'none'
        page.close()
        browser.close()


def test_wear_cleans_on_profile_switch_and_reattaches_after_runtime_rerender():
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = _page(browser)
        page.evaluate('(' + SETUP + ')', 'theme-machine')
        data = page.evaluate('''() => {
          const {root,ir,bridge,styleLaw}=window.__wearFixture;
          const button=root.querySelector('[data-mcel-expression-pad] button');
          const oldInlinePosition=button.style.getPropertyValue('position');
          const replacement=button.cloneNode(true);
          replacement.querySelectorAll('[data-mcel-tng-wear-surface]').forEach(node=>node.remove());
          button.replaceWith(replacement);
          window.__replacementButton=replacement;
          return {oldInlinePosition,initialSurface:!!replacement.querySelector('[data-mcel-tng-wear-surface]')};
        }''')
        assert not data['initialSurface'], data
        page.wait_for_function('''() => !!window.__replacementButton.querySelector(':scope > [data-mcel-tng-wear-surface="button"]')''')
        cleanup = page.evaluate('''() => {
          const {root,ir,bridge,styleLaw}=window.__wearFixture;
          const replacement=window.__replacementButton;
          const before=root.querySelectorAll('[data-mcel-tng-wear-surface]').length;
          const switched=McelSemanticChromePresentationLaw.apply({root,ir,bridge,styleLaw,
            theme:'theme-machine',profile:'chrome-strict-hierarchy'});
          const after=root.querySelectorAll('[data-mcel-tng-wear-surface]').length;
          return {before,after,status:switched.status,
            rails:root.querySelectorAll('[data-mcel-chrome-generated="true"]').length,
            position:replacement.style.getPropertyValue('position')};
        }''')
        assert cleanup['before'] > 2 and cleanup['after'] == 0, cleanup
        assert cleanup['rails'] == 0 and cleanup['status'] == 'unchanged', cleanup
        assert cleanup['position'] == '', cleanup
        # Re-entering TNG must not retain the previous observer or overlays.
        result = page.evaluate('''() => {
          const {root,ir,bridge,styleLaw}=window.__wearFixture;
          const response=McelSemanticChromePresentationLaw.apply({root,ir,bridge,styleLaw,
            theme:'theme-machine',profile:'chrome-tng-side-rails'});
          return {status:response.status,
            surfaces:root.querySelectorAll('[data-mcel-tng-wear-surface]').length,
            nativeButtons:root.querySelectorAll('[data-mcel-working-unit] button, [data-mcel-mode-tabs] button').length,
            rails:root.querySelectorAll('[data-mcel-chrome-generated="true"]').length};
        }''')
        assert result['status'] == 'applied', result
        assert result['surfaces'] > result['nativeButtons'] + 2 and result['rails'] == 2, result
        page.close()
        browser.close()



def test_pressed_wear_reveals_same_defects_at_lower_right_across_material_controls():
    """Pressing reverses scratch illumination, not texture coordinates or light angle."""
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = _page(browser)
        for theme in ('theme-machine', 'theme-editorial', 'theme-debug'):
            page.evaluate('(' + SETUP + ')', theme)
            page.get_by_role('button', name='Graphing', exact=True).click()
            button = page.get_by_role('button', name='Draw Graph', exact=True)
            assert button.count() == 1
            assert button.get_attribute('data-mcel-chrome-material') in (
                'raised-control', 'emissive-control')
            surface = button.locator(':scope > [data-mcel-tng-wear-surface="button"]')
            glint = surface.locator(':scope > [data-mcel-tng-wear-glint]')
            assert surface.count() == glint.count() == 1
            button.hover()
            page.wait_for_timeout(150)

            def state():
                return button.evaluate('''el => {
                  const wear=el.querySelector(':scope > [data-mcel-tng-wear-surface="button"]');
                  const glint=wear.querySelector(':scope > [data-mcel-tng-wear-glint]');
                  const surfaceStyle=getComputedStyle(wear),glintStyle=getComputedStyle(glint),
                    controlStyle=getComputedStyle(el),r=el.getBoundingClientRect();
                  return {pressed:el.matches(':active'),wearOpacity:Number(surfaceStyle.opacity),
                    glintOpacity:Number(glintStyle.opacity),wearMask:surfaceStyle.maskImage,
                    glintMask:glintStyle.maskImage,wearTexture:surfaceStyle.backgroundImage,
                    glintTexture:glintStyle.backgroundImage,
                    wearPosition:surfaceStyle.backgroundPosition,
                    glintPosition:glintStyle.backgroundPosition,
                    background:controlStyle.backgroundColor,color:controlStyle.color,
                    border:controlStyle.borderColor,rect:[r.x,r.y,r.width,r.height],
                    hitTest:glintStyle.pointerEvents};
                }''')

            hovered = state()
            assert not hovered['pressed']
            assert '145deg' in hovered['glintMask']
            assert 'rgba(0, 0, 0, 0.05)' in hovered['glintMask'], hovered
            page.mouse.down()
            page.wait_for_timeout(65)  # An ordinary click, not a long hold.
            depressed = state()
            assert depressed['pressed'], (theme, depressed)
            assert '145deg' in depressed['glintMask'], depressed
            assert 'rgba(0, 0, 0, 0.02) 0%' in depressed['glintMask'], depressed
            assert 'rgb(0, 0, 0) 100%' in depressed['glintMask'], depressed
            assert 'rgba(0, 0, 0, 0.35) 0%' in depressed['wearMask'], depressed
            assert depressed['glintOpacity'] > .75
            assert depressed['wearOpacity'] > hovered['wearOpacity']
            # Color Law may legitimately change semantic colors on :active.
            # Chrome must preserve geometry and the exact defect coordinates.
            for field in ('rect', 'wearTexture', 'glintTexture',
                          'wearPosition', 'glintPosition'):
                assert depressed[field] == hovered[field], (theme, field, hovered, depressed)
            assert depressed['hitTest'] == 'none'
            page.mouse.up()
            page.wait_for_timeout(160)
            released = state()
            assert not released['pressed']
            assert released['glintMask'] == hovered['glintMask']
            assert released['wearMask'] == hovered['wearMask']
            assert released['glintOpacity'] == hovered['glintOpacity']
            page.get_by_role('button', name='Basic', exact=True).click()
        browser.close()
