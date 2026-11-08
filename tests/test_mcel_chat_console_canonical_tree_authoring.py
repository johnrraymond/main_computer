"""P3: Chat Console has an authored canonical tree, without runtime cutover.

The paired HTML semantic surface owns only the static region hierarchy.
The canonical tree owns layout declarations, and P2's repeat node names
runtime cells by stable data-cell-id. Neither is a new execution authority.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from main_computer.mcel_chat_console_host import compile_chat_console, render_chat_console_page
from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools/fixtures/mcel_semantic_chat_console/application.js"
HTML = SOURCE.with_name("application.html")


def authored_tree():
    ir = compile_chat_console()
    assert not ir["states"] and not ir["capabilities"]
    assert not ir["intents"] and not ir["effects"]
    assert len(ir["layouts"]) == 1
    layout = ir["layouts"][0]
    assert layout["surface"] == {"ref": ir["surfaces"][0]["id"]}
    return layout["layoutTree"]


def _nodes(node):
    yield node
    for child in node.get("children", []):
        yield from _nodes(child)


def test_paired_semantic_html_compiles_one_canonical_tree_without_operations():
    tree = authored_tree()
    assert tree["schema"] == "mcel.layout-tree.v1"
    nodes = {node["id"]: node for node in _nodes(tree["root"])}
    assert tree["root"]["selector"] == ".chat-console-shell"
    assert set(nodes) == {
        "chat-shell", "chat-header", "thread-layout", "thread-rail",
        "thread-workspace", "active-thread", "notebook", "notebook-cells",
    }
    assert nodes["notebook-cells"]["role"] == "repeat"
    assert nodes["notebook-cells"]["keyAttribute"] == "data-cell-id"
    assert nodes["notebook-cells"]["maxColumns"] == 2
    assert len(nodes["thread-layout"]["variants"]) == 3
    assert len(nodes) < 64
    assert len(compile_chat_console()["surfaces"][0]["interfaceIntent"]["regions"]) == 8


def _compile_mutation(tmp_path, mutated_js):
    folder = tmp_path / "paired"
    folder.mkdir()
    (folder / "application.html").write_text(HTML.read_text(encoding="utf-8"), encoding="utf-8")
    path = folder / "application.js"
    path.write_text(mutated_js, encoding="utf-8")
    return compile_dsl_application(path)


def test_authored_tree_perturbation_changes_compiled_semantic_fingerprint(tmp_path):
    original = SOURCE.read_text(encoding="utf-8")
    assert "maxColumns: 2, gap: 12" in original
    baseline = compile_dsl_application(SOURCE)
    assert baseline.valid, baseline.diagnostics
    changed = _compile_mutation(tmp_path, original.replace("maxColumns: 2, gap: 12", "maxColumns: 2, gap: 24"))
    assert changed.valid, changed.diagnostics
    assert changed.normalized_ir["layouts"][0]["layoutTree"]["root"]["children"][1]["gap"] == 24
    assert changed.semantic_fingerprint != baseline.semantic_fingerprint


def test_paired_compiler_refuses_more_than_one_layout(tmp_path):
    original = SOURCE.read_text(encoding="utf-8")
    extra = original.replace("layouts: [layout]", "layouts: [layout, dsl.layout.define('extra', {surface, tree: CHAT_CONSOLE_LAYOUT_TREE})]")
    assert extra != original
    report = _compile_mutation(tmp_path, extra)
    assert not report.valid


def test_paired_compiler_refuses_noncanonical_layout(tmp_path):
    original = SOURCE.read_text(encoding="utf-8")
    changed = original.replace('schema: "mcel.layout-tree.v1"', 'schema: "invalid.layout"')
    assert changed != original
    report = _compile_mutation(tmp_path, changed)
    assert not report.valid


def test_canonical_host_embeds_authored_ir_and_mounts_tree():
    document = render_chat_console_page()
    match = re.search(r'<script id="mcel-chat-canonical-ir" type="application/json">(.*?)</script>', document, re.S)
    assert match
    payload = json.loads(match.group(1))
    assert payload["layouts"][0]["layoutTree"] == authored_tree()
    # P4 loads the established general engine and consumes the authored IR.
    assert "mountCanonicalLayoutTree(" in document
    assert 'MainComputerMCELChatConsole.mountCanonical(host,ir)' in document
    assert "initChatConsoleApp()" in document
    assert "chatConsoleMountEmbedded" in document


def _browser_page():
    playwright = pytest.importorskip("playwright.sync_api")
    binary = shutil.which("chromium") or shutil.which("chromium-browser")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=binary or None)
        try:
            page = browser.new_page(viewport={"width": 1040, "height": 850})
            page.evaluate('''() => {
              const contents = new Map();
              Object.defineProperty(window, 'localStorage', {configurable:true,value:{
                getItem:k=>contents.get(k)||null,
                setItem:(k,v)=>contents.set(k,String(v)),
                removeItem:k=>contents.delete(k),clear:()=>contents.clear()
              }});
            }''')
            page.set_content(render_chat_console_page(), wait_until="domcontentloaded")
            yield page
        finally:
            browser.close()


def test_tree_selectors_bind_to_real_notebook_structure_and_repeated_cells():
    tree = authored_tree()
    for page in _browser_page():
        assert page.locator("body").get_attribute("data-mcel-chat-ready") == "true"
        before = page.evaluate('''tree => {
          const root=document.querySelector('.chat-console-shell');
          const observed={};
          function walk(node,parent){
            const matches=node.role==='repeat'
              ? [...parent.children].filter(el=>el.matches(node.selector))
              : [...(parent ? parent.children : [root])].filter(el=>el.matches(node.selector));
            observed[node.id]={count:matches.length,ids:matches.map(el=>el.dataset.cellId||'')};
            if(node.role!=='repeat' && matches.length===1){
              for(const child of node.children||[])walk(child,matches[0]);
            }
          }
          walk(tree.root,null);
          return observed;
        }''', tree)
        assert all(x["count"] == 1 for name,x in before.items() if name != "notebook-cells"), before
        assert before["notebook-cells"]["count"] == 1, before
        page.evaluate('''() => {
          MainComputerChatConsole.addCell('javascript', 'console.log("P3 output lineage")');
        }''')
        assert page.locator('.chat-cell').count() == 2
        ids = page.locator('.chat-cell').evaluate_all('nodes=>nodes.map(n=>n.dataset.cellId)')
        assert len(set(ids)) == 2
        assert page.locator('.chat-cell').last.get_by_role('button', name='Run').count() >= 1


def test_output_source_relationship_is_dom_metadata_not_new_execution():
    for page in _browser_page():
        page.evaluate('''() => MainComputerChatConsole.addCell('javascript',
          'vars.p3value = 9; console.log("P3_OUTPUT");')''')
        input_cell = page.locator('.chat-cell-javascript').last
        input_id = input_cell.get_attribute('data-cell-id')
        input_cell.get_by_role('button',name='Run').click()
        page.wait_for_function('''() => chatConsoleState?.variables?.p3value === 9''',timeout=10000)
        output_cell = page.locator('.chat-cell-output').last
        assert output_cell.get_attribute('data-source-cell-id') == input_id
        assert input_cell.get_attribute('data-source-cell-id') is None
        assert 'P3_OUTPUT' in output_cell.inner_text()

@pytest.mark.parametrize("width", [320, 390, 500, 1040])
def test_authored_tree_is_consumable_by_p2_engine_in_isolated_standalone_browser(width):
    tree = authored_tree()
    for page in _browser_page():
        page.set_viewport_size({"width": width, "height": 850})
        assert page.locator('.chat-console-shell').get_attribute('data-mcel-canonical-layout-authority') == 'mcel-layout-tree.v1' 
        actual = page.evaluate('''tree => {
          const shell = document.querySelector('.chat-console-shell');
          const mounted = MainComputerMCELChatConsole.refreshCanonical(document.querySelector("#chat-console-app"), true);
          const regions = mounted.evaluation?.decision?.regions || [];
          const result = {status:mounted.status,
            authority:shell.getAttribute('data-mcel-canonical-layout-authority'),
            ids:regions.map(node=>node.id),
            cells:document.querySelectorAll('.chat-cell[data-cell-id]').length,
            overflow:document.documentElement.scrollWidth>window.innerWidth+1};
          return result;
        }''', tree)
        assert actual['status'] == 'pass', actual
        assert actual['authority'] == 'mcel-layout-tree.v1', actual
        assert len(actual['ids']) == 8 and 'notebook-cells' in actual['ids'], actual
        assert actual['cells'] >= 1 and not actual['overflow'], actual


def test_same_authored_tree_is_consumable_inside_calculator_style_500px_embed():
    tree = authored_tree()
    for page in _browser_page():
        actual = page.evaluate('''tree => {
          const host=document.createElement('section');
          host.id='p3-calculator-embed';host.style.width='500px';host.dataset.chatConsoleEmbed='calculator';
          document.body.append(host);
          const embedded=MainComputerChatConsole.mountEmbedded(host,
            {layout:'compact',activeApp:'calculator',showThreadRail:false,showCurrentThreadBar:false});
          const shell=host.querySelector('.chat-console-shell');
          if(!shell)return {status:'missing-shell',mounted:!!embedded};
          const mounted=MainComputerMCELChatConsole.mountCanonical(host,{application:{appId:"semantic-chat-console"},layouts:[{layoutTree:tree}]});
          const rail=host.querySelector('.chat-thread-rail');
          const result={status:mounted.status,mounted:!!embedded,
            railHidden:!!rail?.hidden,
            width:host.getBoundingClientRect().width,
            decision:mounted.initial?.evaluation?.decision?.regions?.find(r=>r.id==='thread-layout'),
            cells:host.querySelectorAll('.chat-cell[data-cell-id]').length};
          mounted.unmount();
          return result;
        }''', tree)
        assert actual['mounted'] and actual['status'] == 'pass', actual
        assert actual['railHidden'] and actual['width'] == 500, actual
        assert actual['cells'] >= 1 and actual['decision'], actual
