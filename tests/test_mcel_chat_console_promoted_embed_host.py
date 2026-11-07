"""Promoted Calculator loads the same semantic Chat Console as the standalone app."""
from __future__ import annotations

from pathlib import Path

from main_computer.mcel_calculator_expanded_host import ASSETS, render_expanded_page
from tools import mcel_chat_console_layout_oracle as oracle

ROOT = Path(__file__).resolve().parents[1]


def test_promoted_calculator_embeds_shared_semantic_renderer_assets():
    html = render_expanded_page(ROOT, promoted=True)
    # This is the actual canonical Calculator page, not the main SPA template.
    assert 'mcel.chat-console.composition.v1' in html
    assert 'data-mcel-chat-role' in html
    assert 'chat-console-cell-output.chat-console-output-indent' in html
    assert html.index('window.MainComputerChatConsole') < html.index('mcel.chat-console.composition.v1')
    assert html.index('mcel.chat-console.composition.v1') < html.index('Semantic Calculator ready')
    assert ASSETS.count('main_computer/web/applications/scripts/mcel-semantic-chat-console.js') == 1
    assert 'mcel_apps/calculator/application.js' not in ASSETS


def test_semantic_structural_gate_does_not_require_saved_ai_output():
    from tests.test_mcel_chat_console_layout_oracle import clean_observation
    observed = clean_observation()
    observed.update(cells=[], thinking=[], thinkingCards=[], outputs=[], tabButtons=[], tabs=[],
                    editorInputs=[], controlRows=[], controlButtons=[],
                    semanticContract=oracle.SEMANTIC_CONTRACT,
                    semanticRegions=['shell', 'thread-layout', 'workspace', 'notebook'])
    for context in ('standalone', 'embedded'):
        assert oracle.evaluate_layout(context, observed, populated=False, require_semantic=True)['status'] == 'PASS'
        fail = oracle.evaluate_layout(context, observed, populated=True, require_semantic=True)
        assert fail['status'] == 'FAIL'
        assert 'missing-populated-cells' in fail['failures']


def test_structural_gate_rejects_an_unmigrated_embedded_chat():
    from tests.test_mcel_chat_console_layout_oracle import clean_observation
    observed = clean_observation()
    observed.update(semanticMarker=False, semanticContract='',
                    semanticRegions=['shell', 'thread-layout', 'workspace', 'notebook'])
    fail = oracle.evaluate_layout('embedded', observed, populated=False, require_semantic=True)
    assert 'raw-chat-renderer-not-migrated' in fail['failures']


def test_promoted_calculator_actually_mounts_semantic_embedded_chat():
    """Browser acceptance of the canonical HTML, without a live host or provider."""
    import pytest
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = oracle._launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1272, 'height': 900})
            try:
                # page.set_content has an opaque origin, so supply a per-page,
                # nonpersistent storage interface solely for this browser test.
                page.evaluate('''() => {
                  const values=new Map();
                  const store={getItem:k=>values.has(k)?values.get(k):null,
                    setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k),
                    clear:()=>values.clear(),key:i=>[...values.keys()][i]||null,
                    get length(){return values.size}};
                  Object.defineProperty(window,'localStorage',{configurable:true,value:store});
                  Object.defineProperty(window,'sessionStorage',{configurable:true,value:store});
                }''')
                page.route('**/*', lambda route: route.abort())
                page.set_content(render_expanded_page(ROOT, promoted=True))
                assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
                host = page.locator('[data-chat-console-embed="calculator"]')
                assert host.count() == 1
                assert host.get_attribute('data-mcel-chat-contract') == oracle.SEMANTIC_CONTRACT
                assert page.locator('[data-chat-console-embed="calculator"] [data-mcel-chat-role="notebook"]').count() == 1
                assert page.evaluate('window.MainComputerMCELChatConsole.contract.id') == oracle.SEMANTIC_CONTRACT
                page.locator('[data-mcel-mode-tabs] button[data-value="help"]').click()
                host.evaluate('(element) => {element.style.width="500px";element.style.maxWidth="500px"}')
                page.evaluate("document.querySelector('[data-chat-console-embed=calculator]').setAttribute('data-chat-oracle-root','')")
                observed = page.evaluate(oracle.OBSERVE_JS, oracle.SELECTORS)
                result = oracle.evaluate_layout('embedded', observed, populated=False, require_semantic=True)
                assert result['status'] == 'PASS', result['failures']
            finally:
                page.close()
        finally:
            browser.close()
