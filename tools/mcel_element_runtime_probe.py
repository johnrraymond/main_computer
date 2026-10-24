#!/usr/bin/env python3
"""Bounded, real-Chromium element witnesses through shared MCEL runtime.

Observations are deliberately narrow: a family-level implementation is NOT a
per-element renderer registration and cannot grant full element certification.
"""
from __future__ import annotations
from typing import Any, Callable

TAB_IDS = (
    'element.toolkit.tabbed-workspace',
    'element.toolkit.tab-list',
    'element.toolkit.tab',
    'element.toolkit.tab-panel',
    'element.toolkit.tab-controller',
)


def assess_witnesses(observed: dict[str, Any], expected_ids: set[str]) -> dict[str, Any]:
    """Independently judge the DOM evidence, refusing absence, extras, and lies."""
    tab_ids = set(TAB_IDS)
    if not tab_ids.issubset(expected_ids):
        return {'status':'UNVERIFIED', 'reason':'declared-tab-family-incomplete', 'witnesses':{}}
    if not isinstance(observed, dict) or not observed:
        return {'status':'FAIL', 'reason':'missing-runtime-observation', 'witnesses':{}}
    selectors = {
        TAB_IDS[0]: ('rendererGenerated', 'oneSelected', 'oneVisiblePanel', 'panelModelPreserved'),
        TAB_IDS[1]: ('tablistRole', 'keyboardMovedFocus', 'keyboardSelected'),
        TAB_IDS[2]: ('clickSelected', 'tabAriaControlsValid', 'tabAriaSelectedValid'),
        TAB_IDS[3]: ('tabpanelBacklinkValid', 'inactivePanelHidden', 'panelModelPreserved'),
        TAB_IDS[4]: ('invalidRefused', 'invalidPreservedState', 'effectsEmpty', 'routeStateSynced'),
    }
    checks = {}
    for id_, keys in selectors.items():
        violations = [key for key in keys if observed.get(key) is not True]
        checks[id_] = {
            'status': 'FAIL' if violations else 'PASS',
            'scope': 'shared-tab-renderer-browser-observation',
            'witnessId': 'mcel.tab-workspace.chromium.v1',
            'requiredObservations':list(keys),
            'failedObservations':violations,
            'fullElementCertification':False,
        }
    # A single shared renderer's behavior has no authority to prove an
    # unrelated registry element or a broader product-level claim.
    return {'status':'FAIL' if any(v['status']=='FAIL' for v in checks.values()) else 'PASS',
            'reason':'executed-shared-runtime', 'witnesses':checks, 'observed':observed}


def run_tab_witness(browser: Any, html: str, definitions: list[dict[str, Any]],
                    mutate_for_negative_control: Callable[[Any], None] | None = None) -> dict[str, Any]:
    """Real clicks/keyboard/typed refusals on MCEL's standalone runtime."""
    page = browser.new_page(viewport={'width':840,'height':700}, device_scale_factor=1,
                            reduced_motion='reduce')
    errors, requests = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda request: requests.append(request.url))
    try:
        page.set_content(html, wait_until='load')
        if mutate_for_negative_control is not None:
            mutate_for_negative_control(page)
        tabs = page.locator('[role="tab"]')
        panels = page.locator('[role="tabpanel"]')
        if tabs.count() < 2 or tabs.count() != panels.count():
            return {'status':'FAIL', 'reason':'missing-or-unequal-tabs-panels',
                    'witnesses':{}, 'observed':{'tabs':tabs.count(), 'panels':panels.count()}}
        primary, secondary = tabs.nth(0), tabs.nth(1)
        primary_id = primary.get_attribute('id')
        secondary_id = secondary.get_attribute('id')
        panel_id = primary.get_attribute('aria-controls')
        retained = page.locator(f'[id="{panel_id}"]')
        original_text = retained.text_content()
        initial_state = page.evaluate('McelNotebookPreview.getState()')
        primary.focus()
        page.keyboard.press('ArrowRight')
        keyboard_moved = page.evaluate('''(id) => document.activeElement?.id === id''', secondary_id)
        keyboard_selected = secondary.get_attribute('aria-selected') == 'true'
        primary.click()
        click_selected = primary.get_attribute('aria-selected') == 'true'
        valid_tab_links = page.evaluate('''() => [...document.querySelectorAll('[role="tab"]')].every(t =>
            {const p=document.getElementById(t.getAttribute('aria-controls'));
             return p?.getAttribute('role')==='tabpanel' && p.getAttribute('aria-labelledby')===t.id})''')
        primary_selected = page.evaluate('''() => [...document.querySelectorAll('[role="tab"]')]
            .filter(t=>t.getAttribute('aria-selected')==='true').length===1''')
        secondary.click()
        inactive_hidden = retained.is_hidden()
        preserved = retained.count()==1 and retained.text_content()==original_text
        before_invalid = page.evaluate('McelNotebookPreview.getState()')
        refused = page.evaluate('McelNotebookPreview.select("__acid_unknown_tab__")')
        after_invalid = page.evaluate('McelNotebookPreview.getState()')
        route_synced = page.evaluate('''() => location.hash.startsWith('#tab=')''')
        observed = {
            'rendererGenerated':page.locator('[data-mcel-generated="tab-workspace-v1"]').count()==1,
            'oneSelected':primary_selected,
            'oneVisiblePanel':page.locator('[role="tabpanel"]:visible').count()==1,
            'panelModelPreserved':preserved,
            'tablistRole':page.locator('[role="tablist"]').count()==1,
            'keyboardMovedFocus':keyboard_moved,
            'keyboardSelected':keyboard_selected,
            'clickSelected':click_selected,
            'tabAriaControlsValid':valid_tab_links,
            'tabAriaSelectedValid':primary_selected,
            'tabpanelBacklinkValid':valid_tab_links,
            'inactivePanelHidden':inactive_hidden,
            'invalidRefused':refused == {'ok':False,'code':'invalid-selection','effects':[]},
            'invalidPreservedState':before_invalid==after_invalid,
            'effectsEmpty':before_invalid.get('effects')==[] and after_invalid.get('effects')==[],
            'routeStateSynced':route_synced,
        }
        result = assess_witnesses(observed, {d['id'] for d in definitions})
        result['provenance'] = {
            'renderer':'main_computer/web/applications/scripts/mcel-tab-workspace.js',
            'stateAuthority':'main_computer/web/applications/scripts/mcel-view-transition.js',
            'specimen':'official-valid-ir-standalone-generated-preview',
            'tabCount':tabs.count(),
            'networkRequests':requests,
            'browserExceptions':errors,
            'selectionBefore':initial_state,
            'selectionAfter':after_invalid,
        }
        if requests or errors:
            result['status'] = 'FAIL'
            result['reason']='unexpected-network-or-browser-error'
        return result
    finally:
        page.close()
