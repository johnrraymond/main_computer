"""P4 live canonical-tree wiring for standalone and Calculator-embedded Chat Console."""
from __future__ import annotations
import shutil
import pytest
from playwright.sync_api import sync_playwright
from main_computer.mcel_chat_console_host import render_chat_console_page
from main_computer.mcel_calculator_expanded_host import render_expanded_page

@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        binary=shutil.which("chromium") or shutil.which("chromium-browser")
        b=pw.chromium.launch(headless=True,executable_path=binary or None)
        try:yield b
        finally:b.close()

def page_with_storage(browser, html, width=1272):
    page=browser.new_page(viewport={"width":width,"height":900})
    page.evaluate('''() => {
      const data=new Map();const store={getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,String(v)),
      removeItem:k=>data.delete(k),clear:()=>data.clear(),key:i=>[...data.keys()][i]||null,
      get length(){return data.size}};
      Object.defineProperty(window,'localStorage',{configurable:true,value:store});
      Object.defineProperty(window,'sessionStorage',{configurable:true,value:store});
    }''')
    page.route('**/*',lambda route:route.abort())
    page.set_content(html,wait_until='domcontentloaded',timeout=30000)
    return page

def test_standalone_refresh_refusal_and_recovery(browser):
    page=page_with_storage(browser,render_chat_console_page(),1040)
    try:
        host=page.locator('#chat-console-app');shell=host.locator('.chat-console-shell')
        assert page.locator('body').get_attribute('data-mcel-chat-ready')=='true'
        assert host.get_attribute('data-mcel-chat-layout-status')=='pass'
        assert shell.get_attribute('data-mcel-canonical-layout-authority')=='mcel-layout-tree.v1'
        page.evaluate("MainComputerChatConsole.addCell('javascript','vars.p4 = 42;')")
        assert page.locator('.chat-cell').count()==2
        keys=page.locator('.chat-cell').evaluate_all('els=>els.map(el=>el.dataset.cellId)')
        assert len(set(keys))==2
        page.set_viewport_size({'width':390,'height':900});page.wait_for_timeout(100)
        assert host.get_attribute('data-mcel-chat-layout-status')=='pass'
        assert page.locator('.chat-cell').evaluate_all('els=>els.map(el=>el.dataset.cellId)')==keys
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.locator('.chat-cell').last.evaluate('el=>el.setAttribute("data-cell-id","duplicate")')
        page.locator('.chat-cell').first.evaluate('el=>el.setAttribute("data-cell-id","duplicate")')
        assert page.evaluate("MainComputerMCELChatConsole.refreshCanonical(document.querySelector('#chat-console-app'),true).status")=='refused'
        assert host.get_attribute('data-mcel-chat-layout-status')=='refused'
        assert shell.get_attribute('inert') is not None
        assert page.locator('[data-chat-console-layout-refusal]').is_visible()
        page.locator('.chat-cell').first.evaluate('(el,key)=>el.setAttribute("data-cell-id",key)',keys[0])
        page.locator('.chat-cell').last.evaluate('(el,key)=>el.setAttribute("data-cell-id",key)',keys[1])
        assert page.evaluate("MainComputerMCELChatConsole.refreshCanonical(document.querySelector('#chat-console-app'),true).status")=='pass'
        assert shell.get_attribute('inert') is None
    finally:page.close()

def test_calculator_embedded_child_uses_own_width_and_tree(browser):
    page=page_with_storage(browser,render_expanded_page(promoted=True))
    try:
        assert page.locator('#mcel-expanded-diagnostic').inner_text()=='Semantic Calculator ready'
        assert 'semantic-chat-console' in page.locator('#mcel-chat-embedded-ir').text_content()
        page.locator('[data-mcel-mode-tabs] button[data-value="help"]').click()
        host=page.locator('[data-chat-console-embed="calculator"]')
        host.evaluate('el=>{el.style.width="500px";el.style.maxWidth="500px"}')
        page.wait_for_timeout(100)
        assert host.get_attribute('data-mcel-chat-layout-status')=='pass'
        assert host.locator('.chat-console-shell').get_attribute('data-mcel-canonical-layout-authority')=='mcel-layout-tree.v1'
        assert host.locator('.chat-thread-rail').get_attribute('hidden') is not None
        assert host.locator('.chat-cell[data-cell-id]').count()>=1
        assert abs(host.evaluate('el=>el.getBoundingClientRect().width')-500)<1
        host.evaluate('el=>{el.style.width="390px";el.style.maxWidth="390px"}')
        page.wait_for_timeout(100)
        assert host.get_attribute('data-mcel-chat-layout-status')=='pass'
    finally:page.close()

@pytest.mark.parametrize('context',['standalone','embedded'])
def test_engine_ablation_visibly_refuses(browser,monkeypatch,context):
    if context=='standalone':
        import main_computer.mcel_chat_console_host as module
        monkeypatch.setattr(module,'SCRIPTS',tuple(x for x in module.SCRIPTS if not x.endswith('mcel-semantic-layout-inference.js')))
        html=module.render_chat_console_page()
    else:
        import main_computer.mcel_calculator_expanded_host as module
        monkeypatch.setattr(module,'ASSETS',[x for x in module.ASSETS if not x.endswith('mcel-semantic-layout-inference.js')])
        html=module.render_expanded_page()
    page=page_with_storage(browser,html)
    try:
        if context=='standalone':
            assert page.locator('body').get_attribute('data-mcel-chat-ready') is None
            assert 'canonical-layout-engine-unavailable' in page.locator('#mcel-chat-canonical-status').inner_text()
        else:
            page.locator('[data-mcel-mode-tabs] button[data-value="help"]').click()
            host=page.locator('[data-chat-console-embed="calculator"]')
            assert host.get_attribute('data-mcel-chat-layout-status')=='refused'
            assert 'canonical-layout-engine-unavailable' in host.locator('[data-chat-console-layout-refusal]').inner_text()
            assert host.locator('.chat-console-shell').get_attribute('inert') is not None
    finally:page.close()
