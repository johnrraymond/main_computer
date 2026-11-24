"""Shared document-workbench styling is sourced from Chrome, not an old palette.

The workbench's dock/scroll/resize layout is retained, and non-TNG profiles
keep their existing fallback. No application identities appear in the styling.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome


ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "main_computer/web/applications/styles/mcel-semantic-workbench.css"


def test_document_workbench_tng_surface_tokens_are_not_the_blue_gray_palette():
    text = CSS.read_text(encoding="utf-8")
    assert "--mcel-document-workbench-surface:" in text
    assert "--mcel-document-workbench-edge:" in text
    assert '[data-mcel-semantic-chrome="chrome-tng-side-rails"] [data-mcel-document-workbench]' in text
    assert 'var(--mcel-tng-glass,#03130f)' in text
    assert 'var(--mcel-tng-rail-primary,#ffcb22)' in text
    assert "data-mcel-semantic-operational=\"code-editor\"" not in text


def test_tng_document_workbench_uses_resolved_material_and_restores_fallback():
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        try:
            page = browser.new_page(viewport={"width": 1272, "height": 720})
            page.set_content(render_semantic_page("code-editor", ROOT, promoted=True))
            page.wait_for_timeout(120)
            result = page.evaluate(r'''() => {
              const root=document.querySelector('[data-mcel-semantic-chrome]');
              const work=root.querySelector('[data-mcel-document-workbench]');
              const names=['work','explorer','companion','bottom','editor'];
              const nodes={
                work,
                explorer:work.querySelector('[data-mcel-document-explorer]'),
                companion:work.querySelector('[data-mcel-document-companion]'),
                bottom:work.querySelector('[data-mcel-document-bottom]'),
                editor:work.querySelector('[data-mcel-document-surface]')
              };
              const snap=()=>Object.fromEntries(names.map(k=>{
                const e=nodes[k],s=getComputedStyle(e),r=e.getBoundingClientRect();
                return [k,{background:s.backgroundColor,image:s.backgroundImage,
                  rect:[r.x,r.y,r.width,r.height].map(x=>Math.round(x))}];
              }));
              const button=work.querySelector('[data-mcel-document-toolbar] button');
              const font=getComputedStyle(button).fontSize;
              const nativeNode=button.firstChild;
              const tng=snap();
              root.setAttribute('data-mcel-semantic-chrome','chrome-strict-hierarchy');
              const legacy=snap();
              root.setAttribute('data-mcel-semantic-chrome','chrome-tng-side-rails');
              const returned=snap();
              return {tng,legacy,returned,font,textUnchanged:nativeNode===button.firstChild,
                panes:['explorer','companion','bottom'].map(k=>nodes[k].getAttribute('data-mcel-document-'+k)!==null)};
            }''')
            assert result['panes'] == [True, True, True], result
            assert result['textUnchanged'], result
            assert float(result['font'].removesuffix('px')) >= 11.5, result
            for name in ('work', 'explorer', 'companion', 'bottom'):
                assert result['tng'][name]['background'] != 'rgb(26, 39, 49)', (name,result)
                assert result['legacy'][name]['background'] == 'rgb(26, 39, 49)', (name,result)
                assert result['returned'][name] == result['tng'][name], (name,result)
            assert result['tng']['editor']['background'] == 'rgb(3, 19, 15)', result
            assert result['legacy']['editor']['background'] == 'rgb(8, 22, 30)', result
            assert all(result['tng'][n]['rect'] == result['legacy'][n]['rect']
                       for n in ('work','explorer','companion','bottom','editor')), result
        finally:
            browser.close()


def test_other_apps_do_not_acquire_document_workbench_material():
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        try:
            for name in ("file-manager", "task-manager"):
                page = browser.new_page(viewport={"width": 1272, "height": 720})
                page.set_content(render_semantic_page(name, ROOT, promoted=True))
                result = page.evaluate(r'''() => ({
                  documentWorkbench:document.querySelectorAll('[data-mcel-document-workbench]').length,
                  app:document.querySelector('[data-mcel-semantic-operational]')?.getAttribute('data-mcel-semantic-operational'),
                  root:document.querySelector('[data-mcel-presentation="expert"]')?.getAttribute('data-mcel-semantic-chrome')
                })''')
                assert result['documentWorkbench'] == 0, result
                assert result['app'] == "semantic-" + name, result
                assert result['root'] == 'chrome-tng-side-rails', result
                page.close()
        finally:
            browser.close()
