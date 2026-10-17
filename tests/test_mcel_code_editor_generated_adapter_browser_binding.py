"""Chromium probe: real Code Editor facade via real generated ESM adapter.

This proves dispatch and binding in a browser, not the complete Code Editor UI
or the source-write acceptance obligations. No writes or network effects occur.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from main_computer.mcel_application_package_browser_catalog import build_repository_browser_catalog_payload
from main_computer.mcel_application_virtual_assets import build_virtual_mcel_browser_assets

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer/web/applications/scripts"


def test_code_editor_generated_adapter_mounts_and_dispatches_in_chromium() -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    assets = build_virtual_mcel_browser_assets(ROOT)
    browser_catalog = build_repository_browser_catalog_payload(ROOT)
    record = next(item for item in browser_catalog["packages"] if item["appId"] == "code-editor")
    manifest = json.loads(assets.read("applications/mcel-packages/code-editor/mcel.runtime.json"))
    sources = {
        entry["path"]: assets.read(f"applications/mcel-packages/code-editor/{entry['path']}").decode("utf-8")
        for entry in manifest["modules"].values()
    }
    with playwright.sync_playwright() as api:
        browser_options = {"headless": True}
        executable = (os.environ.get("MCEL_CHROMIUM_EXECUTABLE")
                      or os.environ.get("CHROMIUM_EXECUTABLE")
                      or shutil.which("chromium")
                      or shutil.which("chromium-browser"))
        if executable:
            browser_options.update({"executable_path": executable, "args": ["--no-sandbox", "--disable-dev-shm-usage"]})
        browser = api.chromium.launch(**browser_options)
        try:
            page = browser.new_page()
            page.set_content("<!doctype html><html><body></body></html>")
            # The real facade is instantiated before the fixture creates the
            # element; this avoids exercising the unrelated Monaco UI shell.
            for name in ("code-editor-core.js", "code-editor-view-model.js", "code-editor-capabilities.js", "code-editor.js"):
                page.add_script_tag(path=str(SCRIPTS / name))
            page.evaluate("""() => {window.McelApplicationPackages = {catalogFingerprint: 'fixture', getPackage: () => null, listPackages: () => []}}""")
            page.add_script_tag(path=str(SCRIPTS / "mcel-host-bound-application-runtime.js"))
            result = page.evaluate("""async ({record, manifest, catalogFingerprint, sources}) => {
              const root = document.createElement('div');
              root.id = 'code-editor-app';
              document.body.appendChild(root);
              const catalog = {
                catalogFingerprint,
                listPackages: () => [record],
                getPackage: id => id === record.appId ? record : null
              };
              const mount = await window.McelHostBoundApplicationRuntime.mountApplication({
                appId: 'code-editor', manifest, root, packageCatalog: catalog,
                moduleLoader: async (_url, entry) => {
                  const source = sources[entry.path];
                  if (typeof source !== 'string') throw new Error('Missing module source');
                  const bytes = new TextEncoder().encode(source);
                  let binary = '';
                  for (const byte of bytes) binary += String.fromCharCode(byte);
                  return import(`data:text/javascript;base64,${btoa(binary)}`);
                }
              });
              const receipt = await mount.invoke('closeFile', {});
              return {
                active: mount.active,
                bindingStatus: mount.bindingReport.status,
                count: mount.bindingReport.intentCount,
                facade: window.MainComputerCodeEditorRuntime.facade,
                closeOk: receipt?.ok,
                closeReceipt: receipt?.state?.receipts?.at(-1)?.intent,
                rootStatus: root.dataset.mcelHostBoundStatus
              };
            }""", {
                "record": record,
                "manifest": manifest,
                "catalogFingerprint": browser_catalog["catalogFingerprint"],
                "sources": sources,
            })
            assert result == {
                "active": True,
                "bindingStatus": "bound",
                "count": 8,
                "facade": "MainComputerCodeEditorRuntime",
                "closeOk": True,
                "closeReceipt": "closeFile",
                "rootStatus": "mounted",
            }
        finally:
            browser.close()
