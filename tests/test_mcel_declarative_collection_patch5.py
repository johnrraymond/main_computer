"""Patch 5: public keyed composition and scoped inventory application integration."""
from __future__ import annotations

import json
from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_declarative_collection_package import ASSETS, RUNTIME, STYLE, build
from tools import mcel_promise_gap_frob as frob


def test_public_composition_probes_compile_without_promise_promotion() -> None:
    for probe in frob.PROBES:
        observation = frob._compiler_probe(*probe)
        assert observation['status'] in {'PASS', 'AVAILABLE_UNPROVEN'}, observation
    assert frob._compiler_probe(*frob.PROBES[1])['diagnosticCodes'] == ['MCEL_DSL_ACTION_INTENT_REQUIRED']


def test_inventory_candidate_has_declared_keyed_actions_and_effects() -> None:
    result = compile_dsl_application(frob.FIXTURES / 'inventory_application.js', write_candidate=False)
    assert result.valid, result.diagnostics
    ir = result.normalized_ir
    assert ir['application']['appId'] == 'promise-inventory'
    items = next(state for state in ir['states'] if state['id'] == 'state:items')
    assert items['schema']['kind'] == 'list'
    assert items['schema']['key'] == 'sku'
    assert items['schema']['items']['kind'] == 'record'
    assert len([intent for intent in ir['intents'] if intent['operationKind'] == 'mutation']) == 3
    assert len([effect for effect in ir['effects'] if effect['effectKind'] == 'canonical-write']) == 3
    assert any(n['nodeKind'] == 'collection' for s in ir['surfaces'] for n in s['nodes'])
    assert any(n['nodeKind'] == 'form' for s in ir['surfaces'] for n in s['nodes'])


def test_package_is_unchanged_shared_assets_and_compiler_ir(tmp_path: Path) -> None:
    source = frob.FIXTURES / 'inventory_application.js'
    destination = tmp_path / 'candidate'
    result = build(source, destination)
    assert result['status'] == 'BUILT_UNVERIFIED'
    assert result['files'] == ['index.html', 'app.ir.json', RUNTIME, STYLE]
    assert (destination / RUNTIME).read_bytes() == (ASSETS / RUNTIME).read_bytes()
    assert (destination / STYLE).read_bytes() == (ASSETS / STYLE).read_bytes()
    ir = json.loads((destination / 'app.ir.json').read_text(encoding='utf-8'))
    assert ir['application']['appId'] == 'promise-inventory'
    html = (destination / 'index.html').read_text(encoding='utf-8')
    assert 'McelCollectionApplication.mount' in html
    assert 'mcelMounted' in html
    assert 'Promise fulfilled' not in html


def test_invalid_candidate_refuses_to_build(tmp_path: Path) -> None:
    report = build(frob.FIXTURES / 'public_invalid_action.js', tmp_path / 'bad')
    assert report['status'] == 'REFUSED'
    assert not (tmp_path / 'bad' / 'index.html').exists()


def test_composition_is_only_partially_verified_by_a_single_browser_slice() -> None:
    probes = [frob._compiler_probe(*item) for item in frob.PROBES]
    cases = [
        {'id': 'inventory-c2', 'compositionSlice': {'status': 'PASS'}},
        {'id': 'work-orders-independent', 'compositionSlice': {'status': 'UNVERIFIED'}},
    ]
    result = {x['id']: x for x in frob._barriers(probes, cases)}
    assert result['composition']['status'] == 'PARTIAL_VERIFIED'
    assert result['functional']['status'] == 'PARTIAL_VERIFIED'
    for key in ('effects', 'layout', 'aesthetics', 'flexibility', 'economy'):
        assert result[key]['status'] == 'UNVERIFIED'


def test_generic_schema_driven_non_inventory_browser_mount(tmp_path: Path) -> None:
    """A different domain exercises boolean/enum/text row editing without a domain renderer.

    This is an implementation sanity check, *not* the pinned Work Orders benchmark.
    """
    import shutil
    import pytest
    try:
        from playwright.sync_api import sync_playwright
        from tools.mcel_promise_gap_frob import _chromium
    except ImportError:
        pytest.skip('Playwright not installed')
    source = tmp_path / 'asset_library.js'
    source.write_text('''"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp({id:"asset-library-test",title:"Asset Library"}, dsl => {
  const id = dsl.field.text().minLength(1);
  const title = dsl.field.text().minLength(1);
  const active = dsl.field.boolean();
  const status = dsl.field.text().oneOf(["ready","held"]);
  const model = dsl.field.record({id,title,active,status});
  const state = dsl.state.canonical("assets",dsl.field.list(model,{key:"id"}),{initial:[]});
  const ops = dsl.intent.keyedCollection("asset",state);
  const form = dsl.surface.form("asset-form",{fields:{id,title,active,status},intent:ops.add});
  const col = dsl.surface.collection("asset-list",{key:"id",state,fields:["id","title","active","status"],actions:ops});
  const view = dsl.surface.define("view",{root:dsl.surface.region("root",{children:[form,col]})});
  const layout = dsl.layout.define("main",{surface:view,orderedChildren:[view.node("asset-form"),view.node("asset-list")]});
  return {states:[state],intents:[ops.add,ops.update,ops.remove],surfaces:[view],layouts:[layout]};
});
''', encoding='utf-8')
    dest = tmp_path / 'package'
    assert build(source, dest)['status'] == 'BUILT_UNVERIFIED'
    ir = json.loads((dest / 'app.ir.json').read_text(encoding='utf-8'))
    with sync_playwright() as playwright:
        try:
            browser = _chromium(playwright)
        except Exception:
            pytest.skip('Chromium unavailable')
        try:
            page = browser.new_page(viewport={'width': 390, 'height': 800})
            page.set_content('<main id="root"></main>')
            page.add_style_tag(content=(dest / STYLE).read_text(encoding='utf-8'))
            page.add_script_tag(content=(dest / RUNTIME).read_text(encoding='utf-8'))
            page.evaluate('ir => {window.app=McelCollectionApplication.mount(document.getElementById("root"),ir)}', ir)
            page.locator('[data-mcel="field-id"]').fill('BL-41')
            page.locator('[data-mcel="field-title"]').fill('Calibration guide')
            page.locator('[data-mcel="field-active"]').check()
            page.locator('[data-mcel="field-status"]').select_option('ready')
            page.locator('[data-mcel="create"]').click()
            assert page.evaluate('window.app.snapshot()') == [
                {'id':'BL-41','title':'Calibration guide','active':True,'status':'ready'}]
            page.locator('[data-mcel="edit-title"]').fill('Calibration checklist')
            page.locator('[data-mcel="edit-active"]').uncheck()
            page.locator('[data-mcel="edit-status"]').select_option('held')
            page.locator('[data-mcel="update"]').click()
            assert page.evaluate('window.app.snapshot()') == [
                {'id':'BL-41','title':'Calibration checklist','active':False,'status':'held'}]
            assert page.evaluate('window.app.receipts().length') == 2
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        finally:
            browser.close()
