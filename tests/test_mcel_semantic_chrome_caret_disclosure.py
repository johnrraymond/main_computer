"""Native disclosure indicators must not be repainted as Chrome buttons."""

from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_target_navigation import target_tabs
from tests.test_mcel_semantic_chrome_universal_material import _page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome


def test_slh_navigation_disclosure_keeps_native_caret_under_shared_chrome():
    """The real SLH navigation note must not acquire a TNG bevel or wear."""
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        page = _page(browser)
        # Insert real SLH-authored navigation markup inside a source-bound
        # mounted MCEL region, so Chrome sees it as an eligible descendant.
        page.locator('[data-mcel-region][data-mcel-purpose="input"]').first.evaluate(
            '(host, markup) => host.insertAdjacentHTML("beforeend", markup)',
            target_tabs('local'),
        )
        data = page.evaluate('''() => {
          const root=document.getElementById('mcel-expanded-root');
          const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
          const applied=McelSemanticChromePresentationLaw.apply({
            root,ir,bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
            profile:'chrome-tng-side-rails',theme:'theme-machine'});
          const summary=root.querySelector('[data-mcel-navigation-note] > summary');
          const button=root.querySelector('button[data-mcel-chrome-material="raised-control"]');
          const style=getComputedStyle(summary);
          return {applied:applied.status,summary:summary.outerHTML,
            material:summary.getAttribute('data-mcel-chrome-material'),
            wear:!!summary.querySelector('[data-mcel-tng-wear-surface]'),
            image:style.backgroundImage,shadow:style.boxShadow,border:style.borderStyle,
            marker:style.listStyleType,display:style.display,
            button:button?.getAttribute('data-mcel-chrome-material')};
        }''')
        assert data['applied'] == 'applied', data
        assert data['material'] is None and not data['wear'], data
        assert data['image'] == 'none' and data['shadow'] == 'none' and data['border'] == 'none', data
        assert data['display'] == 'list-item' and 'disclosure' in data['marker'], data
        assert data['button'] == 'raised-control', data

        summary = page.locator('[data-mcel-navigation-note] > summary')
        note = page.locator('[data-mcel-navigation-note]')
        assert note.get_attribute('open') is None
        summary.click()
        assert note.get_attribute('open') is not None
        summary.focus()
        summary.press('Enter')
        assert note.get_attribute('open') is None

        # Changes to the mounted application's content reconcile without
        # promoting newly arrived native disclosure controls to buttons.
        page.locator('[data-mcel-region][data-mcel-purpose="input"]').first.evaluate('''host => {
          const details=document.createElement('details');
          details.innerHTML='<summary role="button">Another disclosure</summary><p>Details</p>';
          host.appendChild(details);
        }''')
        page.wait_for_timeout(100)
        dynamic = page.get_by_text('Another disclosure')
        assert dynamic.get_attribute('data-mcel-chrome-material') is None
        assert dynamic.locator('[data-mcel-tng-wear-surface]').count() == 0
        dynamic.click()
        assert dynamic.evaluate('el => el.parentElement.open') is True
        browser.close()
