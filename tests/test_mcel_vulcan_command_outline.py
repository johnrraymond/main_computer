"""Vulcan action outlines: IR-owned command groups, no navigation or app spillover.

All SLH browser I/O is blocked. These tests exercise the real generated
application DOM, not actual lifecycle services or deployment commands.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import render_slh_page
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer/web/applications/scripts"
COMMANDS = ('[data-mcel-task-actions] > button, '
            '[data-mcel-task-actions] > [data-node-kind="control"] > button, '
            '[data-mcel-operation-actions] > button')


def _inject_law(page):
    for script in ("mcel-semantic-presentation-bridge.js", "mcel-semantic-chrome-presentation-law.js"):
        page.add_script_tag(path=str(SCRIPTS / script))


def _slh_page(browser):
    page = browser.new_page(viewport={"width": 1280, "height": 880})
    page.evaluate("""() => {
      window.fetch = async () => ({ok:false,status:503,
        json:async()=>({error:{code:'UNAVAILABLE'}})});
    }""")
    page.set_content(render_slh_page(), wait_until="domcontentloaded")
    page.wait_for_function("() => !!window.__mcelSlh?.app")
    _inject_law(page)
    return page


def _apply_slh(page, profile="chrome-vulcan"):
    return page.evaluate("""(profile) => {
      const {ir,composition}=JSON.parse(document.getElementById('mcel-generated-data').textContent);
      const root=document.getElementById('mcel-workbench');
      const bridge={observeRuntimePresentationTargets: args =>
        McelSemanticPresentationBridge.observeRuntimePresentationTargets({...args,composition})};
      return McelSemanticChromePresentationLaw.apply({root,ir,bridge,profile,
        theme:'theme-machine'});
    }""", profile)


def _styles(page, selector):
    return page.evaluate("""(selector) => [...document.querySelectorAll(selector)]
      .map(node=>({label:node.textContent.trim(),visible:!!node.getClientRects().length,
        border:getComputedStyle(node).borderTopWidth,
        borderColor:getComputedStyle(node).borderTopColor,
        borderStyle:getComputedStyle(node).borderTopStyle,
        radius:getComputedStyle(node).borderRadius,
        background:getComputedStyle(node).backgroundColor,
        disabled:node.disabled,
        group:node.closest('[data-mcel-task-actions]')?'task':
          node.closest('[data-mcel-operation-actions]')?'operation':'other'}))""",selector)


def test_slh_generated_task_and_deployment_commands_outline_only_in_vulcan():
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = _slh_page(browser)
        try:
            assert _apply_slh(page)['status'] == 'applied'
            # Switching the local notebook tab is UI-only; the unavailable
            # transport cannot authorize or execute deployment.
            page.locator('[data-mcel-operation-notebook] [data-mcel-operation-tab="deployment"]').first.click()
            commands = _styles(page, COMMANDS)
            visible = [c for c in commands if c['visible']]
            labels = {c['label'] for c in visible}
            assert {'Apply', 'Deploy / Redeploy', 'Undeploy'} <= labels, commands
            assert any(c['group']=='task' for c in visible)
            assert any(c['group']=='operation' for c in visible)
            assert all(c['border']=='1px' and c['borderStyle']=='solid' and
                       c['borderColor']=='rgb(200, 201, 205)' and
                       c['radius']=='12px' for c in visible), commands
            # The Stop/Start and Disable controls are generated inside the
            # authored task footer, so they must be treated as actions too.
            for intent in ('toggle-organization-enablement','toggle-organization'):
                node = page.locator('[data-mcel-task-actions] button[data-intent="intent:'+intent+'"]')
                assert node.count() == 1 and node.evaluate('el=>getComputedStyle(el).borderTopWidth') == '1px'
            # Navigation tabs, record tabs and activity-level toggles must not
            # inherit the operational command outline.
            nav = _styles(page, '[data-mcel-mode-tabs] button, [data-mcel-operation-tabs] button, '
                                '[data-mcel-notebook-tabs] button, [data-mcel-activity-filters] button')
            assert nav and all(item['border']=='0px' for item in nav), nav
            # Switching away restores the original application presentation.
            assert _apply_slh(page,'chrome-strict-hierarchy')['status']=='unchanged'
            assert page.locator('[data-mcel-semantic-chrome]').count() == 0
        finally:
            page.close()
            browser.close()


def test_vulcan_operational_keycap_hover_press_and_focus_remain_interactive():
    with sync_playwright() as p:
        browser = _chrome(p,None)
        page = _slh_page(browser)
        try:
            assert _apply_slh(page)['status']=='applied'
            # Do not invoke an SLH command. Reproduce a harmless UI-only button
            # in the real task group and check its native pointer interaction.
            page.evaluate("""() => {
              const actions=document.querySelector('[data-mcel-task-actions]');
              const probe=document.createElement('button');
              probe.id='vulcan-command-probe';probe.textContent='Probe';
              probe.type='button';window.__outlineClicks=0;
              probe.addEventListener('click',()=>++window.__outlineClicks);
              actions.append(probe);
            }""")
            button=page.locator('#vulcan-command-probe')
            button.hover()
            assert button.evaluate('el=>getComputedStyle(el).borderTopColor')=='rgb(191, 192, 197)'
            assert button.evaluate('el=>getComputedStyle(el).backgroundColor')=='rgb(245, 245, 246)'
            page.mouse.down()
            assert button.evaluate('el=>getComputedStyle(el).borderTopColor')=='rgb(174, 176, 182)'
            assert button.evaluate('el=>getComputedStyle(el).backgroundColor')=='rgb(236, 236, 238)'
            page.mouse.up()
            assert page.evaluate('window.__outlineClicks')==1
            # Keyboard-directed focus, not programmatic focus after a mouse click.
            button.press('Tab')
            page.keyboard.press('Shift+Tab')
            assert button.evaluate('el=>el.matches(":focus-visible")')
            assert button.evaluate('el=>getComputedStyle(el).outlineStyle')=='solid'
            button.evaluate('el=>{el.disabled=true}')
            assert button.evaluate('el=>getComputedStyle(el).borderTopWidth')=='1px'
        finally:
            page.close()
            browser.close()


def test_vulcan_command_selector_has_no_matches_in_other_mounted_apps():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        try:
            for app in ('file-manager','task-manager','code-editor'):
                page=browser.new_page(viewport={'width':1280,'height':820})
                page.set_content(render_semantic_page(app,ROOT,promoted=True))
                _inject_law(page)
                result=page.evaluate("""() => {
                  const root=document.getElementById('mcel-workbench');
                  const ir=JSON.parse(document.getElementById('mcel-workbench-ir').textContent);
                  const report=McelSemanticChromePresentationLaw.apply({root,ir,
                    bridge:McelSemanticPresentationBridge,theme:'theme-machine',profile:'chrome-vulcan'});
                  return {status:report.status,targets:root.querySelectorAll(
                    '[data-mcel-task-actions] > button, [data-mcel-task-actions] > [data-node-kind="control"] > button, [data-mcel-operation-actions] > button').length,
                    buttons:[...root.querySelectorAll('[data-mcel-node][data-node-kind="control"] button')]
                      .map(el=>getComputedStyle(el).borderTopWidth)};
                }""")
                assert result['status']=='applied',(app,result)
                assert result['targets']==0,(app,result)
                assert result['buttons'] and set(result['buttons'])=={'0px'},(app,result)
                page.close()
        finally:
            browser.close()
