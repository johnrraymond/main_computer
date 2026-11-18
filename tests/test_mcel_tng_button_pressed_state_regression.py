"""The same MCEL material must depress for mouse-down and latched selections.

An accidental whitespace/combinator in the shared Chrome selector once targeted
*descendants* of buttons, disabling visible physical depression everywhere.
"""
from playwright.sync_api import sync_playwright

from tests.test_mcel_semantic_chrome_universal_material import _page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome


def test_tng_momentary_pressed_matches_latched_and_release_restores_raised():
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        page = _page(browser)
        mounted = page.evaluate('''() => {
          const root = document.getElementById('mcel-expanded-root');
          const region = root.querySelector('[data-mcel-region][data-mcel-purpose="input"]');
          region.insertAdjacentHTML('beforeend', `
            <button type="button" id="chrome-momentary">Momentary</button>
            <button type="button" id="chrome-latched" aria-pressed="false">Latch</button>
            <button type="submit" id="chrome-emissive">Apply</button>`);
          window.chromeRegressionClicks = 0;
          root.querySelector('#chrome-momentary').addEventListener('click', () => window.chromeRegressionClicks++);
          const ir = JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
          const result = McelSemanticChromePresentationLaw.apply({
            root, ir, bridge: McelSemanticPresentationBridge,
            styleLaw: McelLabStyleLaw, theme:'theme-machine', profile:'chrome-tng-side-rails'
          });
          return {status: result.status, materials: ['chrome-momentary','chrome-latched','chrome-emissive']
            .map(id => root.querySelector('#'+id)?.getAttribute('data-mcel-chrome-material'))};
        }''')
        assert mounted['status'] == 'applied', mounted
        assert mounted['materials'] == ['raised-control', 'raised-control', 'emissive-control'], mounted

        def shadow(id):
            return page.locator('#' + id).evaluate('el => getComputedStyle(el).boxShadow')

        momentary = page.locator('#chrome-momentary')
        momentary.hover()
        page.wait_for_timeout(180)
        hover_shadow = shadow('chrome-momentary')
        page.mouse.down()
        assert momentary.evaluate('el => el.matches(":active")')
        page.wait_for_timeout(180)
        held_shadow = shadow('chrome-momentary')
        assert held_shadow != hover_shadow, ('physical mouse-down did not depress', hover_shadow, held_shadow)
        assert held_shadow.count('inset') == 2 and held_shadow.count(',') == 1, held_shadow
        page.mouse.up()
        page.wait_for_timeout(180)
        assert shadow('chrome-momentary') == hover_shadow, 'mouse-up must restore hover material'
        assert page.evaluate('window.chromeRegressionClicks') == 1, 'Chrome must not block button actions'

        latched = page.locator('#chrome-latched')
        latched.hover()
        page.wait_for_timeout(180)
        raised_off_shadow = shadow('chrome-latched')
        latched.evaluate('el => el.setAttribute("aria-pressed", "true")')
        page.wait_for_timeout(180)
        selected_shadow = shadow('chrome-latched')
        assert selected_shadow == held_shadow, ('latched state must look physically pressed', selected_shadow, held_shadow)
        latched.evaluate('el => el.setAttribute("aria-pressed", "false")')
        page.wait_for_timeout(180)
        assert shadow('chrome-latched') == raised_off_shadow, 'released latch must look raised while hovered'

        emissive = page.locator('#chrome-emissive')
        emissive.hover()
        page.wait_for_timeout(180)
        emissive_hover = shadow('chrome-emissive')
        page.mouse.down()
        assert emissive.evaluate('el => el.matches(":active")')
        page.wait_for_timeout(180)
        emissive_held = shadow('chrome-emissive')
        assert emissive_held == held_shadow, ('emissive action must depress like ordinary action', emissive_held, held_shadow)
        assert emissive_held != emissive_hover, ('emissive action stuck at hover style', emissive_held, emissive_hover)
        page.mouse.up()
        page.wait_for_timeout(180)
        assert shadow('chrome-emissive') == emissive_hover
        browser.close()
