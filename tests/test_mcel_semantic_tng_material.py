"""TNG v2: material finish is confined to existing Lab Chrome, not app geometry."""
from pathlib import Path

from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'main_computer/web/applications'
SCRIPT = WEB / 'scripts'
THEMES = ('theme-machine', 'theme-local', 'theme-saas', 'theme-editorial',
          'theme-luxury', 'theme-civic', 'theme-accessible', 'theme-debug')


def _open(browser, width):
    page = browser.new_page(viewport={'width': width, 'height': 850}, device_scale_factor=.75)
    page.evaluate('''() => {for (const name of ['localStorage','sessionStorage']) {
      const values=new Map();Object.defineProperty(window,name,{configurable:true,value:{
        getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,String(v)),
        removeItem:k=>values.delete(k),clear:()=>values.clear()}});}}''')
    page.set_content(render_expanded_page(ROOT, promoted=True))
    assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
    for stylesheet in ('mcel-lab.css',):
        page.add_style_tag(path=str(WEB / 'styles' / stylesheet))
    for js in ('mcel-semantic-presentation-bridge.js',
               'mcel-semantic-theme-preview.js',
               'mcel-semantic-chrome-presentation-law.js'):
        page.add_script_tag(path=str(SCRIPT / js))
    return page


TEST = r'''({theme, remove=false}) => {
 const root=document.getElementById('mcel-expanded-root'),
   ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent),
   bridge=McelSemanticPresentationBridge,styleLaw=McelLabStyleLaw;
 const main=root.firstElementChild,
   unit=root.querySelector('[data-mcel-working-unit][data-mcel-sizing="intrinsic"]'),
   nav=root.querySelector('[data-mcel-natural-scroller="navigation"]'),
   activity=root.querySelector('[data-mcel-natural-scroller="inspection"]'),
   evaluate=unit.querySelector('button[type="submit"]');
 const rect=el=>{const r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height]};
 const initial={unit:rect(unit),nav:rect(nav),activity:rect(activity),evaluate:rect(evaluate),
  rootImage:getComputedStyle(root).backgroundImage,
  mainImage:getComputedStyle(main).backgroundImage};
 const projected=McelSemanticThemePreview.apply({root,ir,bridge,styleLaw,
  bindingModel:'operational-runtime',theme,tokens:styleLaw.themePaletteFor(theme)});
 const result=McelSemanticChromePresentationLaw.apply({root,ir,bridge,styleLaw,
  profile:'chrome-tng-side-rails',theme});
 const rails=[...unit.querySelectorAll('[data-mcel-chrome-generated="true"]')];
 const rail=rails[0];
 const s=getComputedStyle(root),m=getComputedStyle(main),r=rail&&getComputedStyle(rail),
   before=rail&&getComputedStyle(rail,'::before'),after=rail&&getComputedStyle(rail,'::after');
 const state={projected:projected.status,result,
   glass:root.style.getPropertyValue('--mcel-tng-glass'),
   phosphor:root.style.getPropertyValue('--mcel-tng-phosphor'),
   palette:styleLaw.themePaletteFor(theme),
   rootImage:s.backgroundImage,mainImage:m.backgroundImage,
   pixelSize:m.backgroundSize,railBG:r?.backgroundImage,
   purpleBG:after?.backgroundImage,highlightMask:before?.maskImage,
   purpleRadius:after?.borderRadius,railShadow:r?.filter,
   width:rail?.getBoundingClientRect().width,
   rails:rails.length,
   noExtraChrome:root.querySelectorAll('[data-mcel-chrome-generated="true"]').length===2,
   oldButton:evaluate===unit.querySelector('button[type="submit"]'),
   before:initial,
   now:{unit:rect(unit),nav:rect(nav),activity:rect(activity),evaluate:rect(evaluate)}};
 if(remove){McelSemanticChromePresentationLaw.apply({root,ir,bridge,styleLaw,
   profile:'chrome-strict-hierarchy',theme});
  state.restored={rootImage:getComputedStyle(root).backgroundImage,
   mainImage:getComputedStyle(main).backgroundImage,
   rails:root.querySelectorAll('[data-mcel-chrome-generated="true"]').length,
   rootAttr:root.hasAttribute('data-mcel-semantic-chrome'),
   glass:root.style.getPropertyValue('--mcel-tng-glass'),
   phosphor:root.style.getPropertyValue('--mcel-tng-phosphor')};}
 return state;
}'''


def test_black_glass_and_clean_materials_follow_shared_theme_across_eight_palettes():
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = _open(browser, 1440)
        for theme in THEMES:
            report = page.evaluate(TEST, {'theme':theme})
            assert report['projected']=='applied-in-isolated-lab-frame', (theme,report)
            assert report['result']['status']=='applied', (theme,report)
            assert report['result']['displayMaterial']=='black-glass-pixel', (theme,report)
            assert report['result']['railMaterial']=='clean-molded', (theme,report)
            assert report['glass']==report['palette']['--mcel-theme-chrome-glass'],(theme,report)
            assert report['phosphor']==report['palette']['--mcel-theme-chrome-phosphor'],(theme,report)
            assert report['rails']==2 and report['noExtraChrome'] and report['oldButton'],(theme,report)
            assert 'radial-gradient' in report['mainImage'],(theme,report)
            assert 'linear-gradient' in report['mainImage'],(theme,report)
            assert '21px 6px' in report['pixelSize'],(theme,report)
            assert report['mainImage'].count('data:image/svg+xml,') == 1,(theme,report)
            assert report['glass'] in report['palette'].values(),(theme,report)
            assert report['railBG'] and report['purpleBG'] and report['highlightMask']!='none',(theme,report)
            assert report['purpleRadius']=='0px' and 'drop-shadow' in report['railShadow'],(theme,report)
            assert report['before']['unit'][2:]==report['now']['unit'][2:],(theme,report)
            assert report['before']['nav']==report['now']['nav'],(theme,report)
            assert report['before']['activity']==report['now']['activity'],(theme,report)
            assert report['before']['evaluate'][2:]==report['now']['evaluate'][2:],(theme,report)
        page.close()
        browser.close()


def test_material_fully_clears_and_does_not_modify_application_controls():
    with sync_playwright() as p:
        browser = _chrome(p, None)
        for width in (390, 846, 960, 1440):
            page = _open(browser, width)
            report = page.evaluate(TEST, {'theme':'theme-machine','remove':True})
            assert report['result']['status']=='applied',(width,report)
            assert report['restored']['rails']==0 and not report['restored']['rootAttr'],(width,report)
            assert report['restored']['glass']==report['restored']['phosphor']=='',(width,report)
            # The Lab's semantic theme projection remains after removing Chrome.
            # Only Chrome-owned paint must disappear.
            assert report['restored']['rootImage']==report['before']['rootImage'],(width,report)
            assert 'data:image/svg+xml,' not in report['restored']['mainImage'],(width,report)
            if width==390:
                assert report['width']==0,(width,report)
            page.close()
        browser.close()
