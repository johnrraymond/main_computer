"""TNG wordmark-gold lettering is scoped to chrome, not data or tactile paint."""
from pathlib import Path

from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'main_computer/web/applications/scripts'


def test_logo_gold_headings_controls_navigation_and_profile_cleanup():
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.set_content(render_expanded_page(ROOT, promoted=True))
        for script in ('mcel-semantic-presentation-bridge.js',
                       'mcel-semantic-chrome-presentation-law.js'):
            page.add_script_tag(path=str(SCRIPTS / script))
        result = page.evaluate('''() => {
          const root = document.getElementById('mcel-expanded-root');
          const ir = JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
          const header = root.querySelector('h1');
          const button = root.querySelector('button');
          const label = root.querySelector('label');
          const field = root.querySelector('input:not([type=hidden])');
          const paint = e => {
            const style = getComputedStyle(e), r = e.getBoundingClientRect();
            return {color:style.color,fill:style.webkitTextFillColor,clip:style.backgroundClip,
              background:style.backgroundImage,shadow:style.textShadow,
              dimensions:[r.width,r.height]};
          };
          const baseline = {header:paint(header),button:paint(button),
            label:paint(label),field:paint(field)};
          const applied = McelSemanticChromePresentationLaw.apply({root,ir,
            bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
            profile:'chrome-tng-side-rails',theme:'theme-machine'});
          const gold = {header:paint(header),button:paint(button),
            label:paint(label),field:paint(field)};
          const reset = McelSemanticChromePresentationLaw.apply({root,ir,
            bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
            profile:'chrome-strict-hierarchy',theme:'theme-machine'});
          const restored = {header:paint(header),button:paint(button),
            label:paint(label),field:paint(field)};
          return {baseline,applied,gold,reset,restored};
        }''')
        assert result['applied']['status'] == 'applied', result
        assert result['reset']['status'] == 'unchanged', result
        baseline, gold, restored = (result['baseline'], result['gold'], result['restored'])
        assert gold['header']['clip'] == 'text', gold['header']
        assert 'linear-gradient' in gold['header']['background'], gold['header']
        assert gold['header']['fill'] == 'rgba(0, 0, 0, 0)', gold['header']
        assert gold['button']['fill'] == gold['label']['fill'] == 'rgb(243, 189, 88)', gold
        assert 'gradient(' in gold['button']['background'], gold['button']
        assert '145deg' in gold['button']['background'], gold['button']
        assert gold['field']['fill'] == baseline['field']['fill'], (gold,baseline)
        assert gold['field']['color'] == baseline['field']['color'], (gold,baseline)
        for kind in ('header','button','label','field'):
            assert gold[kind]['dimensions'] == baseline[kind]['dimensions'], (kind,gold,baseline)
            assert restored[kind] == baseline[kind], (kind,restored,baseline)
        browser.close()


def test_gold_does_not_override_forced_colors_or_unselected_chrome():
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        page = browser.new_page()
        page.set_content(render_expanded_page(ROOT, promoted=True))
        for script in ('mcel-semantic-presentation-bridge.js',
                       'mcel-semantic-chrome-presentation-law.js'):
            page.add_script_tag(path=str(SCRIPTS / script))
        page.evaluate('''() => {
          const root=document.getElementById('mcel-expanded-root');
          const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
          McelSemanticChromePresentationLaw.apply({root,ir,
            bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
            profile:'chrome-tng-side-rails',theme:'theme-machine'});
        }''')
        page.emulate_media(forced_colors='active')
        colors = page.evaluate('''() => {
          const root=document.getElementById('mcel-expanded-root');
          const elements=[root.querySelector('h1'),root.querySelector('button'),root.querySelector('label')];
          return elements.map(el=>({fill:getComputedStyle(el).webkitTextFillColor,
            color:getComputedStyle(el).color,shadow:getComputedStyle(el).textShadow,
            background:getComputedStyle(el).backgroundImage}));
        }''')
        assert all(item['fill'] == item['color'] and item['shadow'] == 'none'
                   for item in colors), colors
        assert colors[0]['background'] == 'none', colors
        browser.close()
