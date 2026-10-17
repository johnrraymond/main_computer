"""Patch 10A: UI intents may not bypass an unavailable MCEL runtime.

Use the real Calculator HTML, runtime, and IR-generated adapter; never replace
adapter.invoke with a synthetic dispatcher. Standalone is explicitly opted in.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from main_computer.mcel_application_package_browser_catalog import build_repository_browser_catalog_payload
from main_computer.mcel_calculator_host_bound_profile import build_calculator_browser_observation_profile
from main_computer.mcel_host_bound_browser_observation import (
    _browser_injected_projection,
    _host_bound_observation_html,
)

ROOT = Path(__file__).resolve().parents[1]


def _browser(playwright):
    executable = (os.environ.get("MCEL_CHROMIUM_EXECUTABLE")
                  or os.environ.get("CHROMIUM_EXECUTABLE")
                  or shutil.which("chromium") or shutil.which("chromium-browser"))
    options = {"headless": True}
    if executable:
        options.update({"executable_path": executable,
                        "args": ["--no-sandbox", "--disable-dev-shm-usage"]})
    return playwright.chromium.launch(**options)


def test_calculator_governed_ui_refuses_missing_mount_and_uses_real_generated_adapter():
    sync_api = pytest.importorskip("playwright.sync_api")
    profile = build_calculator_browser_observation_profile()
    manifest, modules = _browser_injected_projection(profile, ROOT)
    catalog = build_repository_browser_catalog_payload(ROOT)
    assert len(modules["__generatedAdapterSource"]) > 100
    with sync_api.sync_playwright() as playwright:
        browser = _browser(playwright)
        try:
            page = browser.new_page()
            page.set_content(_host_bound_observation_html(profile, ROOT), wait_until="domcontentloaded")
            page.evaluate('''() => {
              window.__calculatorUiEvents = [];
              window.__calculatorNetwork = [];
              document.querySelector('#calculator-app').addEventListener('mcel:calculator-intent-result',
                event => window.__calculatorUiEvents.push(event.detail));
              window.fetch = async (url, options={}) => {
                window.__calculatorNetwork.push(String(url));
                const body = String(options.body || '');
                const response = String(url).endsWith('/api/chat')
                  ? {ok: true, content: '2+3'} : {ok: true, result_text: 'x^2 / 2', answer: '14'};
                return new Response(JSON.stringify(response),
                  {status: 200, headers:{'Content-Type':'application/json'}});
              };
            }''')
            # A missing mount never invokes a fallback, even for local math or
            # model-backed operations. This also covers the pre-mount race.
            pre = page.evaluate('''() => {
              const display = document.querySelector('#calculator-display');
              const before = display.value;
              document.querySelector('[data-calc-key="7"]').click();
              document.querySelector('#calculator-ask-model').click();
              const app=document.querySelector('#calculator-app');
              return {before, after:display.value, route:app.dataset.calculatorIntentRoute,
                state:app.dataset.calculatorLastIntentStatus,
                intents:window.__calculatorUiEvents.map(e=>[e.intentName,e.failureClass]),
                network:window.__calculatorNetwork.length};
            }''')
            assert pre["before"] == pre["after"]
            assert pre["route"] == "blocked-no-mcel-mount"
            assert pre["state"] == "refused"
            assert pre["intents"] == [
                ["enterToken", "MCEL_CALCULATOR_MOUNT_REQUIRED"],
                ["askModelForExpression", "MCEL_CALCULATOR_MOUNT_REQUIRED"],
            ]
            assert pre["network"] == 0

            mounted = page.evaluate('''async ({manifest, modules, catalog}) => {
              const records = catalog.packages;
              const packageCatalog = {
                catalogFingerprint: catalog.catalogFingerprint || catalog.fingerprint || '',
                getPackage: id => records.find(r => r.appId===id) || null,
                listPackages: () => records.slice()
              };
              window.McelApplicationPackages = packageCatalog;
              const source = modules.__generatedAdapterSource;
              const raw = new TextEncoder().encode(source);
              let binary = ''; for (const byte of raw) binary += String.fromCharCode(byte);
              const adapter = (await import(`data:text/javascript;base64,${btoa(binary)}`)).CalculatorAdapter;
              modules.adapter = {CalculatorAdapter: adapter};
              delete modules.__generatedAdapterSource;
              const mount = await window.McelHostBoundApplicationRuntime.mountApplication({
                appId:'calculator', manifest,
                root:document.querySelector('#calculator-app'), packageCatalog,
                moduleLoader: async (_url, entry, label) => modules[label]
              });
              return {bindings:mount.bindingReport.intentCount,
                status:mount.bindingReport.status, active:mount.active};
            }''', {"manifest": manifest, "modules": modules, "catalog": catalog})
            assert mounted == {"bindings": 11, "status": "bound", "active": True}

            page.evaluate('''() => {
              window.__calculatorNetwork.length = 0;
              document.querySelector('#calculator-display').value='2+3*4';
              document.querySelector('[data-calc-action="equals"]').click();
            }''')
            page.wait_for_function('''() => document.querySelector('#calculator-app')
              .dataset.calculatorLastIntentStatus === 'pass' ''', timeout=10_000)
            result = page.evaluate('''() => ({
              route: document.querySelector('#calculator-app').dataset.calculatorIntentRoute,
              value: document.querySelector('#calculator-result').textContent,
              network: window.__calculatorNetwork.length,
              event: window.__calculatorUiEvents.at(-1)
            })''')
            assert result["route"] == "generated-adapter"
            assert "14" in result["value"]
            assert result["network"] == 0
            assert result["event"]["intentName"] == "evaluateExpression"
            assert result["event"]["ok"] is True
            assert result["event"]["executionBinding"]

            # An explicit capability request does use its declared network lane.
            page.evaluate('''() => {
              document.querySelector('#calculator-prompt').value='two plus three';
              document.querySelector('#calculator-ask-model').click();
            }''')
            page.wait_for_function('''() => window.__calculatorUiEvents.some(
              e => e.intentName==='askModelForExpression' && e.route==='generated-adapter')''',
              timeout=10_000)
            event = page.evaluate('''() => ({
              event:window.__calculatorUiEvents.at(-1),
              network:window.__calculatorNetwork.slice()
            })''')
            assert event["event"]["ok"] is True
            assert event["network"] == ["/api/chat"]

            # An unmounted governed application may not silently execute math.
            page.evaluate('''() => {
              window.McelHostBoundApplicationRuntime.getMount('calculator').unmount();
              window.__calculatorNetwork.length = 0;
              document.querySelector('#calculator-display').value='6+7';
              document.querySelector('[data-calc-action="equals"]').click();
            }''')
            refused = page.evaluate('''() => ({
              expression:document.querySelector('#calculator-display').value,
              route:document.querySelector('#calculator-app').dataset.calculatorIntentRoute,
              failure:window.__calculatorUiEvents.at(-1).failureClass,
              network:window.__calculatorNetwork.length
            })''')
            assert refused == {
                "expression": "6+7", "route": "blocked-no-mcel-mount",
                "failure": "MCEL_CALCULATOR_MOUNT_REQUIRED", "network": 0,
            }
        finally:
            browser.close()


def test_calculator_standalone_is_explicit_and_not_the_default():
    sync_api = pytest.importorskip("playwright.sync_api")
    profile = build_calculator_browser_observation_profile()
    html = _host_bound_observation_html(profile, ROOT)
    # Set the opt-in before any host scripts initialize, and omit the generic
    # host runtime, as required by the standalone compatibility contract.
    runtime = (ROOT / profile.host_runtime_script_path).read_text(encoding="utf-8")
    html = html.replace(f"<script>\n{runtime}\n</script>", "")
    html = html.replace("<body>",
                        "<body><script>window.MainComputerCalculatorStandaloneMode=true;</script>")
    html = html.replace('window.McelApplicationPackages = Object.freeze({',
                        'window.__standaloneCatalogUnused = Object.freeze({')
    with sync_api.sync_playwright() as playwright:
        browser = _browser(playwright)
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="domcontentloaded")
            result = page.evaluate('''() => {
              document.querySelector('#calculator-display').value='4+5';
              document.querySelector('[data-calc-action="equals"]').click();
              const root=document.querySelector('#calculator-app');
              return {route:root.dataset.calculatorIntentRoute,
                status:root.dataset.calculatorLastIntentStatus,
                text:document.querySelector('#calculator-result').textContent};
            }''')
            page.wait_for_function('''() => document.querySelector('#calculator-app').dataset.calculatorIntentRoute
              === 'explicit-standalone' ''', timeout=10_000)
            result = page.evaluate('''() => ({
              route:document.querySelector('#calculator-app').dataset.calculatorIntentRoute,
              status:document.querySelector('#calculator-app').dataset.calculatorLastIntentStatus,
              text:document.querySelector('#calculator-result').textContent
            })''')
            assert result["route"] == "explicit-standalone"
            assert result["status"] == "pass"
            assert "9" in result["text"]
        finally:
            browser.close()
