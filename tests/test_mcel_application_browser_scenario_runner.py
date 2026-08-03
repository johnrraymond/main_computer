from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "main_computer/web/applications/mcel-packages/contract-workbench"
SCM = ROOT / "main_computer/web/applications/scripts/mcel-scm.js"
RUNTIME = ROOT / "main_computer/web/applications/scripts/mcel-application-runtime.js"
SCENARIO_RUNNER = ROOT / "main_computer/web/applications/scripts/mcel-application-browser-scenario-runner.js"


def _chromium() -> str:
    executable = (
        shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
        or shutil.which("msedge")
    )
    if not executable:
        pytest.skip("A Chromium executable is unavailable for inline browser scenario testing.")
    return executable


def _run_contract_workbench_browser_scenarios() -> dict:
    playwright = pytest.importorskip("playwright.sync_api")
    manifest = json.loads((PACKAGE_ROOT / "mcel.runtime.json").read_text(encoding="utf-8"))
    document = (PACKAGE_ROOT / "src/index.html").read_text(encoding="utf-8")
    stylesheet = (PACKAGE_ROOT / "src/app.css").read_text(encoding="utf-8")
    module_urls: dict[str, str] = {}
    for entry in manifest["modules"].values():
        source = (PACKAGE_ROOT / entry["path"]).read_bytes()
        module_urls[entry["export"]] = "data:text/javascript;base64," + base64.b64encode(source).decode("ascii")

    with playwright.sync_playwright() as browser_api:
        try:
            browser = browser_api.chromium.launch(
                headless=True,
                executable_path=_chromium(),
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
        except Exception as exc:  # pragma: no cover - environment-specific browser policy
            pytest.skip(f"Chromium could not launch: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.set_content(f"<style>{stylesheet}</style><div id='content'></div>")
            page.add_script_tag(content=SCM.read_text(encoding="utf-8"))
            page.add_script_tag(content=RUNTIME.read_text(encoding="utf-8"))
            page.add_script_tag(content=SCENARIO_RUNNER.read_text(encoding="utf-8"))
            result = page.evaluate(
                """async ({documentSource, manifest, moduleUrls}) => {
                  const record = {
                    appId: "contract-workbench",
                    fingerprint: manifest.source.packageFingerprint,
                    runtimeProjection: {
                      manifestUrl: "https://example.test/mcel.runtime.json",
                      fingerprint: manifest.projection.fingerprint
                    }
                  };
                  const catalog = {
                    catalogFingerprint: manifest.source.catalogFingerprint,
                    getPackage: (appId) => appId === record.appId ? record : null,
                    listPackages: () => [record]
                  };
                  const modules = {};
                  for (const [name, url] of Object.entries(moduleUrls)) modules[name] = await import(url);
                  const moduleLoader = async (_url, entry) => modules[entry.export];
                  function createRoot(instanceId) {
                    const template = document.createElement("template");
                    template.innerHTML = documentSource;
                    const root = template.content.querySelector("main");
                    root.id = `contract-workbench-app-${instanceId}`;
                    root.dataset.mcelInstanceRoot = instanceId;
                    document.querySelector("#content").appendChild(root);
                    return root;
                  }
                  const plans = new Map();
                  const harness = {
                    reset() { plans.clear(); },
                    enqueueQuotePlan(contractId, plan) {
                      const queue = plans.get(contractId) || [];
                      queue.push(JSON.parse(JSON.stringify(plan)));
                      plans.set(contractId, queue);
                    },
                    enqueueQuotePlans(value = {}) {
                      for (const [contractId, queue] of Object.entries(value)) {
                        for (const plan of queue) this.enqueueQuotePlan(contractId, plan);
                      }
                    }
                  };
                  const delay = (milliseconds, signal) => new Promise((resolve, reject) => {
                    const timer = setTimeout(resolve, milliseconds || 0);
                    if (!signal) return;
                    const abort = () => {
                      clearTimeout(timer);
                      const error = new Error("aborted");
                      error.name = "AbortError";
                      reject(error);
                    };
                    if (signal.aborted) abort();
                    else signal.addEventListener("abort", abort, {once: true});
                  });
                  harness.provider = {
                    async *requestQuote(request, context = {}) {
                      const queue = plans.get(request.contractId) || [];
                      const plan = queue.shift() || [];
                      if (queue.length) plans.set(request.contractId, queue);
                      else plans.delete(request.contractId);
                      for (const step of plan) {
                        await delay(step.delayMs, context.signal);
                        yield JSON.parse(JSON.stringify(step.event));
                      }
                    }
                  };
                  const mounts = new Set();
                  let nextInstance = 1;
                  async function createIsolatedMount(options = {}) {
                    const instanceId = options.instanceId || `inline-${nextInstance++}`;
                    const root = createRoot(instanceId);
                    const mount = await McelApplicationRuntime.mountApplicationPackage({
                      appId: record.appId,
                      packageCatalog: catalog,
                      packageRecord: record,
                      manifest,
                      manifestUrl: record.runtimeProjection.manifestUrl,
                      root,
                      moduleLoader,
                      instanceId,
                      capabilities: {quotes: harness.provider}
                    });
                    mounts.add(mount);
                    return mount;
                  }
                  function disposeIsolatedMount(mount) {
                    if (!mounts.has(mount)) return false;
                    mount.unmount();
                    mount.root.remove();
                    mounts.delete(mount);
                    return true;
                  }
                  const primary = await createIsolatedMount({instanceId: "primary"});
                  mounts.delete(primary);
                  const acceptanceExport = manifest.modules.acceptance.export;
                  const host = {
                    ready: true,
                    appId: record.appId,
                    record,
                    manifest,
                    mount: primary,
                    acceptance: modules[acceptanceExport][acceptanceExport],
                    observation: primary.observation,
                    observationHarness: harness,
                    createIsolatedMount,
                    disposeIsolatedMount
                  };
                  return await McelApplicationBrowserScenarioRunner.run(host, {
                    repositoryFingerprint: "inline-browser-test",
                    capturedAt: "2026-08-03T00:00:00Z",
                    browser: {engine: "playwright-chromium", headless: true},
                    viewport: {width: 1280, height: 800, deviceScaleFactor: 1}
                  });
                }""",
                {"documentSource": document, "manifest": manifest, "moduleUrls": module_urls},
            )
        finally:
            browser.close()

    return result


def test_contract_workbench_browser_scenarios_and_multi_instance_isolation() -> None:
    result = _run_contract_workbench_browser_scenarios()
    observation = result["observation"]
    assert result["operationResult"]["status"] == "pass"
    assert observation["status"] == "pass"
    assert observation["scenarioCount"] == 14
    assert observation["passedScenarioCount"] == 14
    assert observation["failedScenarioCount"] == 0
    assert all(entry["passed"] is True for entry in observation["scenarioResults"])
    assert all(entry["passed"] is True for entry in observation["observationCoverage"])
    multi = observation["multiInstanceProof"]
    assert multi["passed"] is True
    assert all(multi["checks"].values())
    assert result["surfaceConformance"]["status"] == "pass"
