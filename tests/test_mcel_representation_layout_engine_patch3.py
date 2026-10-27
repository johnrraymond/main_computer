"""Patch 3: prove the actual MCEL shared layout authority owns collection layout.

These browser checks are independent of the Frob's aggregate PASS calculation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.mcel_representation_layout_contract import page_for_layout, project_layout
from tools.mcel_text_shrinker_adapter import load_flat_file

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js'
FIXTURE=ROOT/'tools/fixtures/mcel_text_shrinker_widgets.json'


def specimen(width=560):
    fixture=load_flat_file(FIXTURE)
    return page_for_layout(project_layout(fixture['widgets']['downloads'],fixture['value_sets'],
                                         viewport_px=width,enabled=True),'downloads','after')


@pytest.fixture(scope='module')
def browser():
    sync=pytest.importorskip('playwright.sync_api')
    from tools.mcel_browser_launch import launch_chromium
    with sync.sync_playwright() as p:
        b,_=launch_chromium(p,headless=True)
        try: yield b
        finally: b.close()


def test_shared_engine_read_only_evaluation_rollback_and_stale_candidate(browser):
    page=browser.new_page(viewport={'width':560,'height':800})
    try:
        page.set_content(specimen())
        result=page.evaluate('''() => {
          const host=document.querySelector('#panel'), data=window.MCEL_INFORMATION_CONTRACT;
          const engine=window.McelSemanticLayoutInference;
          const registry=window.McelElementRegistry;
          if(window.__MCEL_REPRESENTATION_LAYOUT.engine!==engine) throw Error('wrong engine');
          window.__MCEL_REPRESENTATION_LAYOUT.unmount();
          const original=host.outerHTML;
          const values=JSON.stringify(data.source_rows);
          const evaluation=engine.evaluateSemanticCollectionLayout(host,data,registry);
          const readOnly=host.outerHTML===original;
          const selected=evaluation.status;
          host.style.width='500px';
          const stale=engine.applySemanticCollectionLayout(host,data,registry,evaluation);
          const deniedWithoutMutation=!host.querySelector('mcel-row').hasAttribute('style');
          host.style.removeProperty('width');
          const fresh=engine.evaluateSemanticCollectionLayout(host,data,registry);
          const applied=engine.applySemanticCollectionLayout(host,data,registry,fresh);
          const authority=host.dataset.mcelCollectionLayoutAuthority;
          const tracks=host.querySelector('mcel-row').style.gridTemplateColumns;
          engine.clearSemanticCollectionLayout(host);
          const finalRowStyle=host.querySelector('mcel-row').getAttribute('style');
          const finalAuthority=host.getAttribute('data-mcel-collection-layout-authority');
          const restored=!host.querySelector('mcel-row').hasAttribute('style') &&
            !host.hasAttribute('data-mcel-collection-layout-authority');
          return {readOnly,selected,stale:stale.status,staleReason:stale.reason,
            deniedWithoutMutation,applied:applied.status,authority,tracks,restored,
            finalRowStyle,finalAuthority,sourceUnchanged:JSON.stringify(data.source_rows)===values};
        }''')
        assert result['readOnly'] and result['selected']=='candidate-verified',result
        assert result['stale']=='refused' and result['staleReason']=='stale semantic collection candidate',result
        assert result['deniedWithoutMutation'] and result['applied']=='pass',result
        assert result['authority']=='mcel-semantic-layout-inference.v1',result
        assert result['tracks'].count('px')==3,result
        assert result['restored'] and result['sourceUnchanged'],result
    finally:page.close()


def test_malformed_identity_and_unregistered_binding_fail_closed(browser):
    page=browser.new_page(viewport={'width':560,'height':800})
    try:
        page.set_content(specimen())
        result=page.evaluate('''() => {
          const host=document.querySelector('#panel');
          const data=window.MCEL_INFORMATION_CONTRACT;
          const engine=window.McelSemanticLayoutInference;
          const registry=window.McelElementRegistry;
          const check=()=>engine.evaluateSemanticCollectionLayout(host,data,registry);
          const initial=check().status;
          data.layout_link.required_keys.push('phantom');
          const malformed=check(); data.layout_link.required_keys.pop();
          const second=data.projection.rows[1].cells.name;
          const old=second.display;
          second.display=data.projection.rows[0].cells.name.display;
          const collision=check(); second.display=old;
          const field=host.querySelector('mcel-field');
          const id=field.dataset.mcelElementId;
          field.dataset.mcelElementId='unregistered';
          const unregistered=check();field.dataset.mcelElementId=id;
          const original=field.dataset.full;
          field.dataset.full='tampered';
          const sourceMismatch=check();field.dataset.full=original;
          const restored=check().status;
          return {initial,malformed,collision,unregistered,sourceMismatch,restored};
        }''')
        assert result['initial']==result['restored']=='candidate-verified'
        for key in ('malformed','collision','unregistered','sourceMismatch'):
            assert result[key]['status']=='refused',(key,result)
    finally:page.close()


def test_without_shared_engine_there_is_no_experimental_fallback(browser):
    html=specimen()
    source=ENGINE.read_text(encoding='utf-8')
    assert html.count('<script>'+source+'</script>')==1
    page=browser.new_page(viewport={'width':560,'height':800})
    errors=[]
    page.on('pageerror',lambda error: errors.append(str(error)))
    try:
        page.set_content(html.replace('<script>'+source+'</script>',''))
        assert any('MCEL_SHARED_LAYOUT_AUTHORITY_UNAVAILABLE' in error for error in errors),errors
        assert not page.evaluate('!!window.__MCEL_REPRESENTATION_LAYOUT')
        assert not page.evaluate('document.querySelector("#panel").hasAttribute("data-mcel-collection-layout-authority")')
    finally:page.close()
