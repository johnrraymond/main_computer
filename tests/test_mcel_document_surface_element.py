"""Real Chromium witness for the shared document engine and registry-mounted element."""
from pathlib import Path
import shutil
import os
import pytest

WEB = Path(__file__).resolve().parents[1] / 'main_computer/web/applications'
ELEMENT_SCRIPTS = ('mcel-document-engine.js', 'mcel-element-registry.js', 'mcel-elements-core.js', 'mcel-document-surface-element.js')

@pytest.fixture
def browser_page():
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as p:
        executable = next((x for x in (shutil.which('chromium'), shutil.which('msedge'), shutil.which('google-chrome'), os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'Microsoft/Edge/Application/msedge.exe'), p.chromium.executable_path) if x and Path(x).is_file()), None)
        if not executable:
            pytest.skip('Chromium unavailable; browser acceptance not performed')
        browser = p.chromium.launch(executable_path=executable, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        yield page
        browser.close()
        assert not errors, errors


def load(page, scripts):
    for script in scripts:
        page.add_script_tag(path=str(WEB / 'scripts' / script))


def styles(page):
    for css in ('document.css', 'mcel-document-surface-element.css', 'mcel-lab.css'):
        page.add_style_tag(path=str(WEB / 'styles' / css))


def mount_two(page):
    page.set_content('<div id="a" style="width:560px;height:400px"></div><div id="b" style="width:560px;height:400px"></div>')
    styles(page)
    load(page, ELEMENT_SCRIPTS)
    page.evaluate('''() => {
      const mount = (id, html) => McelElementRegistry.mountElement('element.authoring.document-surface', document.querySelector(id), {html});
      window.a = mount('#a', '<p>Hello <strong>world</strong></p><p>second</p>');
      window.b = mount('#b', '<p>Other document</p>');
    }''')
    page.wait_for_timeout(100)


def test_two_independent_elements_and_revision_checked_ai_mutations(browser_page):
    page = browser_page
    mount_two(page)
    initial_b = page.evaluate('b.getState()')
    page.evaluate('''() => {
      const range = document.createRange(); range.selectNodeContents(a.root.querySelector('strong').firstChild);
      getSelection().removeAllRanges(); getSelection().addRange(range); window.anchor = a.getSelection();
    }''')
    assert page.evaluate('anchor.text') == 'world'
    assert page.evaluate('b.getSelection()') is None
    page.evaluate('''() => a.mutate({operation:'replace-selection',expectedRevision:anchor.revision,anchor,text:'planet'})''')
    assert '<strong>planet</strong>' in page.evaluate('a.getState().html')
    with pytest.raises(Exception, match='DOCUMENT_SURFACE_REVISION_CONFLICT'):
        page.evaluate('''() => a.mutate({operation:'replace-selection',expectedRevision:anchor.revision,anchor,text:'stale'})''')
    with pytest.raises(Exception, match='DOCUMENT_SURFACE_STALE_ANCHOR'):
        page.evaluate('''() => b.mutate({operation:'replace-selection',expectedRevision:b.getState().revision,anchor,text:'wrong instance'})''')
    assert page.evaluate('b.getState()') == initial_b
    page.evaluate('''() => a.mutate({operation:'append',expectedRevision:a.getState().revision,html:'<p>Safe<script>window.injection=1</script><img src=x onerror="window.injection=2"><strong>content</strong></p>'})''')
    assert page.evaluate('typeof window.injection') == 'undefined'
    assert '<script' not in page.evaluate('a.getState().html')
    assert '<img' not in page.evaluate('a.getState().html')
    assert 'Safe<strong>content</strong>' in page.evaluate('a.getState().html')
    after_a = page.evaluate('a.getState()')
    page.locator('#b .mc-page-content').click()
    page.keyboard.press('End')
    page.keyboard.type(' edited')
    page.wait_for_timeout(150)
    assert 'edited' in page.evaluate('b.getState().html')
    assert page.evaluate('a.getState()') == after_a


def test_math_pagination_history_and_destroy(browser_page):
    page = browser_page
    mount_two(page)
    assert page.evaluate("a.insertMath('E=mc^2','paragraph')")
    with_math = page.evaluate('a.getState().html')
    assert 'data-latex="E=mc^2"' in with_math
    page.evaluate("a.mutate({operation:'append',expectedRevision:a.getState().revision,html:'<p>long text line</p>'.repeat(100)})")
    page.wait_for_function("a.root.querySelectorAll('.mc-page').length > 1")
    long = page.evaluate('a.getState().html')
    assert long.count('long text line') == 100
    page.evaluate("() => {const layout=a.getState().layout;layout.view.mode='endless';a.setLayout(layout)}")
    page.wait_for_function("a.root.querySelectorAll('.mc-page').length === 1")
    assert page.evaluate('a.getState().html') == long
    assert page.evaluate('a.undo().html') == with_math
    assert page.evaluate('a.redo().html') == long
    assert page.evaluate('b.getState().revision') == 0
    page.evaluate('a.destroy()')
    assert page.locator('#a .mcel-document-surface-element').count() == 0
    with pytest.raises(Exception, match='DOCUMENT_SURFACE_DESTROYED'):
        page.evaluate('a.getState()')
    page.evaluate("window.c=McelElementRegistry.mountElement('element.authoring.document-surface',document.querySelector('#a'),{html:'<p>Fresh</p>'})")
    assert page.evaluate('c.getState().revision') == 0
    assert page.evaluate('b.getState().revision') == 0


def test_lab_mounts_real_elements_and_rerun_disposes(browser_page):
    page = browser_page
    page.set_content('<div id="mcel-element-acid-canvas"></div><div id="mcel-element-acid-summary"></div><div id="mcel-element-acid-report"></div>')
    styles(page)
    load(page, ELEMENT_SCRIPTS + ('mcel-toolkit-core.js', 'mcel-concern-core.js', 'mcel-file-basket-model.js', 'mcel-file-basket-controller.js', 'mcel-project-concern-workbench.js', 'mcel-git-file-basket-treegrid-lab.js', 'mcel-element-acid-test.js'))
    page.evaluate('McelElementAcidTest.run()')
    page.locator('[data-mcel-lab-mode="document-surface"]').click()
    assert page.locator('[data-mcel-document-surface]').count() == 2
    cards = page.locator('.mcel-document-surface-lab-card')
    cards.nth(0).get_by_role('button', name='Append via API').click()
    assert 'Inserted through' in cards.nth(0).locator('.mc-page-content').inner_text()
    assert 'Inserted through' not in cards.nth(1).locator('.mc-page-content').inner_text()
    old_ids = page.locator('[data-mcel-document-surface]').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.mcelDocumentSurface)')
    page.evaluate('McelElementAcidTest.run()')
    assert page.locator('[data-mcel-document-surface]').count() == 0
    page.locator('[data-mcel-lab-mode="document-surface"]').click()
    assert page.locator('[data-mcel-document-surface]').count() == 2
    new_ids = page.locator('[data-mcel-document-surface]').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.mcelDocumentSurface)')
    assert not set(old_ids) & set(new_ids)


def test_legacy_adapter_uses_same_engine_with_existing_ids(browser_page):
    page = browser_page
    page.set_content((WEB / 'apps/document.html').read_text(encoding='utf-8'))
    styles(page)
    page.add_script_tag(content='''window.saves=0; function saveDocumentDraft(){window.saves++;}''')
    # Production includes execute synchronously within one script.
    page.add_script_tag(content='\n'.join((WEB / 'scripts' / name).read_text(encoding='utf-8') for name in ('mcel-document-engine.js', 'dom-bindings/document.js', 'document-store.js', 'document-layout.js', 'document-pagination.js', 'document-objects.js')))
    page.evaluate('''() => {setDocumentEditorHtml('<p>Legacy <strong>document</strong></p>'); documentObjectRuntime.insertParagraphMath('x^2');}''')
    page.wait_for_timeout(120)
    assert '<strong>document</strong>' in page.evaluate('getDocumentEditorHtml()')
    assert 'data-latex="x^2"' in page.evaluate('getDocumentEditorHtml()')
    assert page.locator('#document-editor').count() == 1
