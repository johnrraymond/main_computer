"""TNG chrome must not turn File Manager navigation rows into raised keycaps."""
from pathlib import Path

from playwright.sync_api import sync_playwright

from tests.test_mcel_semantic_parity_recovery import page_for
from tools.mcel_phase2b_browser import launch_semantic_browser


SCRIPTS = Path(__file__).resolve().parents[1] / "main_computer/web/applications/scripts"
TREE = '[data-mcel-explorer-navigation] [role="treeitem"] :is(button[data-select-key], button[data-mcel-tree-toggle], button[data-mcel-inspect-folder])'
DIRECTORY = '[data-mcel-directory-entry] button[data-mcel-directory-open]'


def _mount_file_manager(browser, width=1100):
    page = page_for(browser, "file-manager", width)
    page.set_default_timeout(4500)
    page.get_by_role("button", name="Load Roots").click()
    page.locator('[data-record-key="workspace"] button').click()
    page.get_by_role("button", name="Browse", exact=True).click()
    page.locator(DIRECTORY).first.wait_for()
    for file in ("mcel-semantic-presentation-bridge.js", "mcel-semantic-chrome-presentation-law.js"):
        page.add_script_tag(path=str(SCRIPTS / file))
    return page


def _apply(page, profile="chrome-tng-side-rails"):
    return page.evaluate("""profile => {
      const root = document.getElementById('mcel-workbench');
      const ir = JSON.parse(document.getElementById('mcel-workbench-ir').textContent);
      return McelSemanticChromePresentationLaw.apply({root, ir,
        bridge:McelSemanticPresentationBridge, profile, theme:'theme-machine',
        styleLaw:McelLabStyleLaw});
    }""", profile)


def _appearance(page, selector):
    return page.locator(selector).first.evaluate("""el => {
      const c=getComputedStyle(el), r=el.getBoundingClientRect();
      return {material:el.getAttribute('data-mcel-chrome-material'),
        overlay:!!el.querySelector(':scope > [data-mcel-tng-wear-surface]'),
        image:c.backgroundImage, shadow:c.boxShadow, background:c.backgroundColor,
        border:c.borderWidth, width:r.width, height:r.height};
    }""")


def test_tng_leaves_explorer_rows_flat_but_real_commands_raised():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            for width in (390, 1100):
                page = _mount_file_manager(browser, width)
                before = {name:_appearance(page, selector) for name,selector in (
                    ('tree',TREE),('directory',DIRECTORY))}
                applied = _apply(page)
                assert applied['status']=='applied', applied
                for name,selector in (('tree',TREE),('directory',DIRECTORY)):
                    after = _appearance(page,selector)
                    assert after == before[name], (name,width,before[name],after)
                    assert after['material'] is None and not after['overlay'], (name,after)
                assert page.locator(TREE).count()>=2
                assert page.locator(DIRECTORY).count()>=2
                for name in ('Load Roots', 'Browse', 'Preview Entry'):
                    button = page.get_by_role('button',name=name,exact=True)
                    assert button.get_attribute('data-mcel-chrome-material') == 'raised-control', name
                    assert button.locator(':scope > [data-mcel-tng-wear-surface="button"]').count()==1, name
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.close()
        finally:
            browser.close()


def test_tng_reconciles_new_explorer_content_and_preserves_navigation():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page = _mount_file_manager(browser)
            assert _apply(page)['status']=='applied'
            page.locator('[data-mcel-directory-open="src"]').click()
            page.locator('[data-mcel-directory-open="src/nested"]').wait_for()
            for selector in (TREE,DIRECTORY):
                assert page.locator(selector).count()>=2
                for entry in page.locator(selector).all():
                    assert entry.get_attribute('data-mcel-chrome-material') is None
                    assert entry.locator(':scope > [data-mcel-tng-wear-surface]').count()==0
            # A directory row is still a fully functional navigation action.
            page.locator('[data-mcel-directory-open="src/nested"]').click()
            page.locator('[data-mcel-current-directory]').get_by_text('/src/nested').wait_for()
            page.locator('[data-record-key="src/nested/hi.js"]').wait_for()
            assert page.locator('[data-mcel-tree-toggle="src/nested"]').get_attribute('data-mcel-chrome-material') is None
            # The tree disclosure remains an operable button, without TNG wear.
            page.locator('[data-mcel-tree-toggle="src/nested"]').click()
            page.locator('[data-mcel-tree-toggle="src/nested"][aria-expanded="false"]').wait_for()
            page.locator('[data-mcel-tree-toggle="src/nested"]').click()
            page.locator('[data-record-key="src/nested/hi.js"]').wait_for()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.close()
        finally:
            browser.close()


def test_tng_exemption_does_not_change_vulcan_material_or_other_buttons():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page = _mount_file_manager(browser)
            assert _apply(page,'chrome-tng-side-rails')['status']=='applied'
            assert _appearance(page,TREE)['material'] is None
            vulcan = _apply(page,'chrome-vulcan')
            assert vulcan['status']=='applied', vulcan
            assert _appearance(page,TREE)['material']=='raised-control'
            assert _appearance(page,DIRECTORY)['material']=='raised-control'
            assert page.locator('[data-mcel-tng-wear-surface]').count()==0
            assert _apply(page,'chrome-tng-side-rails')['status']=='applied'
            assert _appearance(page,TREE)['material'] is None
            assert _appearance(page,DIRECTORY)['material'] is None
            page.get_by_role('button',name='Load Roots',exact=True).click()
            assert page.get_by_role('button',name='Load Roots',exact=True).get_attribute('data-mcel-chrome-material')=='raised-control'
            page.close()
        finally:
            browser.close()
