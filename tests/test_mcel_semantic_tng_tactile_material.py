"""TNG v3: tactile lighting on existing controls, no new color or layout owner."""
from pathlib import Path

from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'main_computer/web/applications'
THEMES = ('theme-machine', 'theme-local', 'theme-saas', 'theme-editorial',
          'theme-luxury', 'theme-civic', 'theme-accessible', 'theme-debug')


def _open(browser, width=1440):
    page = browser.new_page(viewport={'width': width, 'height': 850}, device_scale_factor=.75)
    page.evaluate('''() => {for (const name of ['localStorage','sessionStorage']) {
      const values=new Map();Object.defineProperty(window,name,{configurable:true,value:{
        getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,String(v)),
        removeItem:k=>values.delete(k),clear:()=>values.clear()}});}}''')
    page.set_content(render_expanded_page(ROOT, promoted=True))
    assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
    for stylesheet in ('mcel-semantic-expert.css', 'mcel-lab.css'):
        page.add_style_tag(path=str(WEB / 'styles' / stylesheet))
    for name in ('mcel-semantic-presentation-bridge.js', 'mcel-semantic-theme-preview.js',
                 'mcel-semantic-chrome-presentation-law.js'):
        page.add_script_tag(path=str(WEB / 'scripts' / name))
    return page


SETUP = '''theme => {
 const root=document.getElementById('mcel-expanded-root'),
   ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent),
   bridge=McelSemanticPresentationBridge,styleLaw=McelLabStyleLaw;
 const unit=root.querySelector('[data-mcel-working-unit][data-mcel-sizing="intrinsic"]');
 const buttons={
   key:unit.querySelector('[data-mcel-expression-pad] button'),
   evaluate:unit.querySelector('button[type="submit"]'),
   equals:unit.querySelector('[data-pad-action="submit"]'),
   mode:root.querySelector('[data-mcel-mode-tabs] button[aria-pressed="true"]')};
 const group=root.querySelector('[data-mcel-mode-tabs]');
 const rect=el=>{let r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height]};
 const baseline=Object.fromEntries(Object.entries(buttons).map(([name,el])=>{
   const c=getComputedStyle(el);return [name,{rect:rect(el),background:c.backgroundColor,
     foreground:c.color,border:c.borderColor,backgroundImage:c.backgroundImage,
     shadow:c.boxShadow,node:el}];}));
 const projected=McelSemanticThemePreview.apply({root,ir,bridge,styleLaw,
   bindingModel:'operational-runtime',theme,tokens:styleLaw.themePaletteFor(theme)});
 const original=Object.fromEntries(Object.entries(buttons).map(([name,el])=>{
   const c=getComputedStyle(el);return [name,{rect:rect(el),background:c.backgroundColor,
     foreground:c.color,border:c.borderColor,backgroundImage:c.backgroundImage,
     shadow:c.boxShadow}];}));
 const groupStyle=getComputedStyle(group),beforeGroup={bg:groupStyle.backgroundColor,
     image:groupStyle.backgroundImage,shadow:groupStyle.boxShadow};
 const applied=McelSemanticChromePresentationLaw.apply({root,ir,bridge,styleLaw,
   profile:'chrome-tng-side-rails',theme});
 const after=Object.fromEntries(Object.entries(buttons).map(([name,el])=>{
   const c=getComputedStyle(el);return [name,{rect:rect(el),background:c.backgroundColor,
     foreground:c.color,border:c.borderColor,backgroundImage:c.backgroundImage,
     shadow:c.boxShadow,sameNode:el===buttons[name]}];}));
 const rail=unit.querySelector('[data-mcel-chrome-part="tng-side-left"]');
 const railStyle=getComputedStyle(rail),purple=getComputedStyle(rail,'::after');
 const result={applied,projected:projected.status,original,after,
   group:{before:beforeGroup,after:{bg:getComputedStyle(group).backgroundColor,
     image:getComputedStyle(group).backgroundImage,shadow:getComputedStyle(group).boxShadow}},
   rail:{filter:railStyle.filter,highlight:getComputedStyle(rail,'::before').backgroundImage,
     purpleShadow:purple.boxShadow,purpleRadius:purple.borderRadius},
   rails:unit.querySelectorAll('[data-mcel-chrome-generated="true"]').length,
   palette:styleLaw.themePaletteFor(theme),unit:rect(unit)};
 window.__tngFixture={root,ir,bridge,styleLaw,buttons,unit,group};
 return result;
}'''


def test_tactile_finish_preserves_semantic_colors_and_geometry_for_all_themes():
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = _open(browser)
        for theme in THEMES:
            result = page.evaluate('(' + SETUP + ')', theme)
            assert result['applied']['status'] == 'applied', (theme, result)
            assert result['projected'] == 'applied-in-isolated-lab-frame', (theme, result)
            assert result['rails'] == 2, (theme, result)
            for kind in ('key', 'evaluate', 'equals', 'mode'):
                old, new = result['original'][kind], result['after'][kind]
                # The established TNG profile centers the unit horizontally;
                # the material pass must not resize any original button.
                assert old['rect'][2:] == new['rect'][2:], (theme, kind, old, new)
                for field in ('background', 'foreground', 'border'):
                    assert old[field] == new[field], (theme, kind, field, old, new)
                if theme == 'theme-machine':
                    assert old['backgroundImage'] != new['backgroundImage'], (theme, kind)
                assert new['sameNode'] and 'linear-gradient' in new['backgroundImage'], (theme, kind)
            # Chrome may light interactive buttons, never the MODE group wrapper.
            assert result['group']['before'] == result['group']['after'], (theme, result['group'])
            rail = result['rail']
            assert 'drop-shadow' in rail['filter'] and 'linear-gradient' in rail['highlight'], (theme, rail)
            assert rail['purpleRadius'] == '0px' and 'inset' in rail['purpleShadow'], (theme, rail)
            assert result['palette']['--mcel-theme-chrome-phosphor'] in ('#397d68',
                '#3e8468', '#4c8299', '#627963', '#577862', '#397b8a', '#7fe0c5', '#447ca6')
        page.close()
        browser.close()


def test_hover_press_restore_and_profile_reset_do_not_change_operations():
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = _open(browser)
        page.evaluate('(' + SETUP + ')', 'theme-machine')
        selector = '[data-mcel-expression-pad] button'
        button = page.locator(selector).first
        def paint():
            return button.evaluate('''el=>{
              const c=getComputedStyle(el),r=el.getBoundingClientRect();
              return {background:c.backgroundColor,foreground:c.color,
                image:c.backgroundImage,shadow:c.boxShadow,rect:[r.x,r.y,r.width,r.height]};
            }''')
        initial = paint()
        button.hover()
        page.wait_for_timeout(150)
        hovered = paint()
        assert initial['image'] != hovered['image'], (initial, hovered)
        assert hovered['rect'] == initial['rect']
        page.mouse.down()
        page.wait_for_timeout(160)
        pressed = paint()
        assert pressed['image'] != hovered['image'], (pressed, hovered)
        assert pressed['rect'] == initial['rect']
        page.mouse.up()
        page.mouse.move(1, 1)
        page.wait_for_timeout(160)
        restored = paint()
        assert restored['image'] == initial['image'], (initial, restored)
        # Real calculation still operates on the same existing native elements.
        page.locator('[data-mcel-node$="arithmetic-expression"] input').fill('2+3')
        page.locator('[data-mcel-node$="arithmetic-expression"] button[type="submit"]').click()
        page.wait_for_function("() => document.querySelector('[data-mcel-node$=calculated-value]')?.textContent?.trim()==='5'")
        cleared = page.evaluate('''() => {
          const {root,ir,bridge,styleLaw,unit}=window.__tngFixture;
          const prior=unit.querySelector('button[type="submit"]');
          const result=McelSemanticChromePresentationLaw.apply({root,ir,bridge,styleLaw,
            profile:'chrome-strict-hierarchy',theme:'theme-machine'});
          return {result,rails:root.querySelectorAll('[data-mcel-chrome-generated="true"]').length,
            sameButton:prior===unit.querySelector('button[type="submit"]'),
            image:getComputedStyle(prior).backgroundImage};
        }''')
        assert cleared['result']['status'] == 'unchanged' and cleared['rails'] == 0, cleared
        assert cleared['sameButton'] and cleared['image'] == 'none', cleared
        page.close()
        browser.close()


def test_forced_colors_keeps_native_button_treatments():
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = _open(browser)
        page.evaluate('(' + SETUP + ')', 'theme-machine')
        page.emulate_media(forced_colors='active')
        styles = page.locator('[data-mcel-expression-pad] button').first.evaluate('''el=>{
          const c=getComputedStyle(el);return {image:c.backgroundImage,shadow:c.boxShadow,
            textShadow:c.textShadow};}''')
        assert styles == {'image':'none', 'shadow':'none','textShadow':'none'}, styles
        page.close()
        browser.close()


def test_directional_light_rotates_material_without_recoloring_semantic_surfaces():
    """TNG owns reflection direction; Theme and Color Law keep physical colors."""
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = _open(browser)
        for theme in THEMES:
            result = page.evaluate('(' + SETUP + ')', theme)
            assert result['applied']['status'] == 'applied', (theme, result)
            lighting = page.evaluate('''() => {
              const {root,unit,buttons,styleLaw}=window.__tngFixture;
              const rail=unit.querySelector('[data-mcel-chrome-part="tng-side-left"]');
              const right=unit.querySelector('[data-mcel-chrome-part="tng-side-right"]');
              const get=el=>getComputedStyle(el);
              const original={
                angle:get(root).getPropertyValue('--mcel-tng-light-angle').trim(),
                buttons:Object.fromEntries(Object.entries(buttons).map(([k,el])=>[k,{
                  background:get(el).backgroundColor,foreground:get(el).color,
                  border:get(el).borderColor,gradient:get(el).backgroundImage,
                  width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height}])),
                rails:[rail,right].map(el=>({
                  base:get(el).backgroundImage,
                  light:getComputedStyle(el,'::before').backgroundImage,
                  inset:getComputedStyle(el,'::after').backgroundImage,
                  width:el.getBoundingClientRect().width}))
              };
              root.style.setProperty('--mcel-tng-light-angle','125deg');
              const changed={buttons:Object.fromEntries(Object.entries(buttons).map(([k,el])=>[k,{
                  background:get(el).backgroundColor,foreground:get(el).color,
                  border:get(el).borderColor,gradient:get(el).backgroundImage,
                  width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height}])),
                rails:[rail,right].map(el=>({
                  base:get(el).backgroundImage,
                  light:getComputedStyle(el,'::before').backgroundImage,
                  inset:getComputedStyle(el,'::after').backgroundImage,
                  width:el.getBoundingClientRect().width}))
              };
              root.style.removeProperty('--mcel-tng-light-angle');
              return {original,changed};
            }''')
            original,changed=lighting['original'],lighting['changed']
            assert original['angle']=='145deg', (theme, lighting)
            for name in ('key','mode','evaluate','equals'):
                a,b=original['buttons'][name],changed['buttons'][name]
                assert '145deg' in a['gradient'] and '125deg' in b['gradient'],(theme,name,a,b)
                for field in ('background','foreground','border','width','height'):
                    assert a[field]==b[field],(theme,name,field,a,b)
            for a,b in zip(original['rails'],changed['rails']):
                assert a['base']==b['base'], (theme,a,b)
                assert a['inset']==b['inset'], (theme,a,b)
                assert '145deg' in a['light'] and '125deg' in b['light'], (theme,a,b)
                assert a['width']==b['width'], (theme,a,b)
        page.close()
        browser.close()
