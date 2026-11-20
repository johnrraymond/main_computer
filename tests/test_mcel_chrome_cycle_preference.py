"""Ctrl+Alt+N is an MCEL presentation-only user preference, shared across apps."""
from pathlib import Path
import shutil

from playwright.sync_api import sync_playwright
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from main_computer.mcel_slh_application import render_slh_page

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "main_computer/web/applications/scripts/mcel-unified-presentation-controller.js"
LAB = ROOT / "main_computer/web/applications/scripts/mcel-lab.js"


def _mock_storage(page, initial=None):
    page.evaluate("""initial => {
      const values=new Map(Object.entries(initial||{}));
      Object.defineProperty(window,'localStorage',{configurable:true,value:{
        getItem:key=>values.get(key)||null,
        setItem:(key,value)=>values.set(key,String(value)),
        removeItem:key=>values.delete(key), clear:()=>values.clear()
      }});
    }""", initial or {})


def test_shortcut_cycles_live_chrome_and_restores_preference_on_new_app():
    html = {
        "calculator": render_expanded_page(ROOT, promoted=True),
        "code-editor": render_semantic_page("code-editor", ROOT, promoted=True),
        "slh": render_slh_page("overview", ROOT),
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        try:
            _mock_storage(page)
            page.set_content(html["calculator"],wait_until="domcontentloaded")
            root = "#mcel-expanded-root"
            assert page.locator(root).get_attribute("data-mcel-semantic-chrome") == "chrome-tng-side-rails"
            before = page.evaluate("""() => {
              const root=document.querySelector('#mcel-expanded-root');
              root.__preserveMe={counter:43};
              return {children:root.children.length, html:root.querySelectorAll('button').length};
            }""")
            page.keyboard.press("Control+Alt+n")
            assert page.locator(root).get_attribute("data-mcel-semantic-chrome") == "chrome-vulcan"
            state = page.evaluate("""() => {
              const root=document.querySelector('#mcel-expanded-root');
              return {preference:localStorage.getItem('mcel.user.chrome.v1'),
                report:McelUnifiedPresentationController.operationalReport(root)?.profiles?.chrome,
                counter:root.__preserveMe?.counter, children:root.children.length,
                html:root.querySelectorAll('button').length};
            }""")
            assert state == {"preference":"chrome-vulcan", "report":"chrome-vulcan", "counter":43,
                             "children":before["children"], "html":before["html"]},state
            # New browser document: reconstruct storage from the previous
            # page's persisted key to test the restore-on-mount path.
            second = browser.new_page()
            _mock_storage(second,{"mcel.user.chrome.v1":state["preference"]})
            second.set_content(html["code-editor"],wait_until="domcontentloaded")
            assert second.locator("#mcel-workbench").get_attribute("data-mcel-semantic-chrome") == "chrome-vulcan"
            second.keyboard.press("Control+Alt+n")
            assert second.locator("#mcel-workbench").get_attribute("data-mcel-semantic-chrome") == "chrome-tng-side-rails"
            third = browser.new_page()
            _mock_storage(third,{"mcel.user.chrome.v1":"chrome-tng-side-rails"})
            third.set_content(html["slh"],wait_until="domcontentloaded")
            assert third.locator("#mcel-workbench").get_attribute("data-mcel-semantic-chrome") == "chrome-tng-side-rails"
            assert errors == [],errors
        finally:
            browser.close()


def test_storage_event_restyles_without_remount():
    html = render_semantic_page("file-manager", ROOT, promoted=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page()
        _mock_storage(page)
        page.set_content(html,wait_until="domcontentloaded")
        try:
            page.evaluate("""() => {
              document.querySelector('#mcel-workbench').__marker = 53;
              localStorage.setItem('mcel.user.chrome.v1','chrome-vulcan');
              dispatchEvent(new StorageEvent('storage',{key:'mcel.user.chrome.v1',newValue:'chrome-vulcan'}));
            }""")
            assert page.locator("#mcel-workbench").get_attribute("data-mcel-semantic-chrome") == "chrome-vulcan"
            assert page.evaluate("document.querySelector('#mcel-workbench').__marker") == 53
        finally:
            browser.close()


def test_rejected_shortcut_and_storage_failure_do_not_break_app():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page()
        page.set_content('<input id="input"><div id="host"></div>')
        page.add_script_tag(content="""() => {}""")
        page.add_script_tag(path=str(SCRIPT))
        result = page.evaluate("""() => {
          const c=McelUnifiedPresentationController;
          const previous=c.getPreferredChrome();
          const illegal=c.setPreferredChrome('chrome-unknown');
          const ignored = new KeyboardEvent('keydown',{key:'n',ctrlKey:true,altKey:true,shiftKey:true,bubbles:true});
          document.dispatchEvent(ignored);
          const ignoredPreference=c.getPreferredChrome();
          Object.defineProperty(window,'localStorage',{configurable:true,get(){throw new Error('blocked');}});
          const changed=c.setPreferredChrome('chrome-vulcan');
          return {previous,illegal:illegal.status,ignoredPreference,
            changed:changed.status,persisted:changed.persisted,preference:c.getPreferredChrome()};
        }""")
        assert result["illegal"] == "refused"
        assert result["ignoredPreference"] == result["previous"]
        assert result["changed"] == "applied"
        assert result["persisted"] is False
        assert result["preference"] == "chrome-vulcan"
        browser.close()


def test_lab_selector_reads_and_writes_shared_chrome_preference():
    lab = LAB.read_text(encoding="utf-8")
    assert 'subscribeChromePreference?.((chrome, reason)' in lab
    assert 'getPreferredChrome?.() || mcelLabState.chrome' in lab
    assert 'setPreferredChrome?.(mcelLabState.chrome,"lab-selector")' in lab
    assert 'if (reason === "chrome-select"' in lab


def test_all_promoted_applications_can_switch_without_changing_operational_mount():
    from main_computer.mcel_chat_console_host import render_chat_console_page
    sources = {
        "calculator": (render_expanded_page(ROOT, promoted=True), "#mcel-expanded-root"),
        "file-manager": (render_semantic_page("file-manager", ROOT, promoted=True), "#mcel-workbench"),
        "task-manager": (render_semantic_page("task-manager", ROOT, promoted=True), "#mcel-workbench"),
        "code-editor": (render_semantic_page("code-editor", ROOT, promoted=True), "#mcel-workbench"),
        "chat-console": (render_chat_console_page(ROOT, promoted=True), "#chat-console-app"),
        "slh": (render_slh_page("overview", ROOT), "#mcel-workbench"),
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        try:
            for name, (html, selector) in sources.items():
                page = browser.new_page()
                _mock_storage(page)
                page.set_content(html, wait_until="domcontentloaded")
                initial = page.locator(selector).get_attribute("data-mcel-semantic-chrome")
                assert initial == "chrome-tng-side-rails", (name,initial)
                page.keyboard.press("Control+Alt+n")
                state = page.evaluate("""selector => {
                  const root=document.querySelector(selector);
                  return {chrome:root?.getAttribute('data-mcel-semantic-chrome'),
                    presentation:window.__mcelPresentationReport?.profiles?.chrome,
                    operational:root?.hasAttribute('data-mcel-semantic-operational') ||
                      root?.id === 'mcel-expanded-root' || root?.id === 'chat-console-app',
                    policy:window.__mcelSystemPresentationSelection?.profiles?.chrome};
                }""",selector)
                assert state["chrome"] == state["presentation"] == "chrome-vulcan", (name,state)
                assert state["operational"] and state["policy"] == "chrome-tng-side-rails",(name,state)
                page.close()
        finally:
            browser.close()


def test_lab_selector_stays_in_sync_with_shortcut_and_saves_manual_choice():
    """Execute the actual Lab handler and binding fragments, not a mock copy."""
    source = LAB.read_text(encoding="utf-8")
    start = source.index("    function changeMcelChrome(")
    handler = source[start:source.index("    function applyMcelRuntimeStyleLaw(",start)]
    start = source.index('      mcelChromeSelect?.addEventListener("change",')
    binding = source[start:source.index('      mcelFormPresentationSelect?.addEventListener("change",',start)]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page()
        page.set_content('''<select id="chrome">
          <option value="chrome-tng-side-rails">TNG</option>
          <option value="chrome-vulcan">Vulcan</option>
        </select>''')
        _mock_storage(page)
        page.add_script_tag(path=str(SCRIPT))
        page.add_script_tag(content="""
          var mcelChromeSelect=document.getElementById('chrome');
          var mcelLabState={chrome:'chrome-tng-side-rails',compileEvents:[]};
          var McelLabChromeLaw={normalizeChrome:x=>x,chromeLabel:x=>x};
          var renderMcelCompilerLog=()=>{};
          var syncMcelRenderedSiteFrame=()=>{};
          var applyMcelSemanticChromePreview=()=>{};
          var renderMcelSemanticCompatibility=()=>{};
        """+handler+binding)
        try:
            page.select_option('#chrome','chrome-vulcan')
            assert page.evaluate("localStorage.getItem('mcel.user.chrome.v1')") == 'chrome-vulcan'
            page.keyboard.press('Control+Alt+n')
            result = page.evaluate("""() => ({
              chosen:document.querySelector('#chrome').value,
              lab:mcelLabState.chrome,
              persisted:localStorage.getItem('mcel.user.chrome.v1'),
              changeEvents:mcelLabState.compileEvents.length
            })""")
            assert result == {'chosen':'chrome-tng-side-rails','lab':'chrome-tng-side-rails',
                              'persisted':'chrome-tng-side-rails','changeEvents':2},result
        finally:
            browser.close()
