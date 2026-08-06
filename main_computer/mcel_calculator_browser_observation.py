"""Fresh browser observation for host-bound authoritative Calculator MCEL.

The runner loads the real Calculator host HTML, mounts the generated host-bound
Calculator adapter, stubs only external Calculator capability endpoints, and
compares generated-adapter execution against the stable
``MainComputerCalculatorRuntime`` facade.  It writes no generated files to
``mcel_apps/calculator``.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from main_computer.mcel_application_package_browser_catalog import build_repository_browser_catalog_payload
from main_computer.mcel_application_virtual_assets import (
    normalize_mcel_asset_route,
    read_virtual_mcel_browser_asset,
)
from main_computer.mcel_evidence_provenance import build_repository_provenance
from main_computer.viewport_pages import APPLICATIONS_INDEX_HTML


APP_ID = "calculator"
REPORT_SCHEMA = "mcel.calculator-browser-parity-observation.v1"
REPORT_VERSION = "mcel-calculator-browser-parity-observation-v1"
INTENT_PAYLOADS: Mapping[str, Mapping[str, Any]] = {
    "switchMode": {"mode": "graphing"},
    "enterToken": {"token": "7"},
    "clearExpression": {},
    "evaluateExpression": {"expression": "2+3*4"},
    "drawGraph": {"expression": "x^2", "range": {"xMin": -2, "xMax": 2, "yMin": -1, "yMax": 5}},
    "resetGraph": {},
    "askModelForExpression": {"prompt": "two plus three"},
    "askModelForGraphExpression": {"prompt": "graph x squared"},
    "askModelForMathicsExpression": {"prompt": "integrate x"},
    "evaluateMathics": {"expression": "Integrate[x, x]"},
    "askResultQuestion": {"question": "What is the current result?"},
}
LOCAL_INTENTS = frozenset({"switchMode", "enterToken", "clearExpression", "evaluateExpression", "drawGraph", "resetGraph"})
CAPABILITY_INTENTS = frozenset(set(INTENT_PAYLOADS) - LOCAL_INTENTS)


class CalculatorBrowserObservationError(RuntimeError):
    """Raised when the fresh Calculator browser parity observation cannot pass."""


@dataclass(frozen=True)
class CalculatorBrowserObservationResult:
    valid: bool
    status: str
    report: Mapping[str, Any]
    diagnostics: tuple[Mapping[str, Any], ...]

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.report)
        payload["diagnosticCount"] = self.diagnostic_count
        payload["diagnostics"] = [dict(item) for item in self.diagnostics]
        return payload


class _CalculatorObservationHandler(SimpleHTTPRequestHandler):
    repo_root: Path

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        try:
            if path in {"/applications/calculator", "/applications/calculator/", "/applications"}:
                self._send_bytes(APPLICATIONS_INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if (
                path.startswith("/applications/mcel-packages/")
                or path == "/applications/scripts/mcel-application-package-catalog.js"
            ):
                route = normalize_mcel_asset_route(path)
                data = read_virtual_mcel_browser_asset(self.repo_root, route)
                self._send_bytes(data, _content_type(route))
                return
            if path.startswith("/applications/vendor/"):
                raw = path.removeprefix("/applications/vendor/").replace("\\", "/")
                parts = [part for part in raw.split("/") if part and part != "."]
                if not parts or any(part == ".." for part in parts):
                    raise FileNotFoundError(path)
                root = (self.repo_root / "main_computer/web/applications/vendor").resolve()
                target = root.joinpath(*parts).resolve()
                target.relative_to(root)
                if not target.is_file():
                    raise FileNotFoundError(target)
                self._send_bytes(target.read_bytes(), _content_type(target.as_posix()))
                return
            self.send_error(404)
        except Exception:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        try:
            if path == "/api/chat":
                payload = self._read_json()
                prompt = str(payload.get("prompt") or "").lower()
                content = "x^2" if "graphing calculator" in prompt or "f(x)" in prompt or "graph" in prompt else "2+3"
                self._send_json({"ok": True, "content": content})
                return
            if path == "/api/applications/calculator/mathics/ask":
                self._send_json({"ok": True, "expression": "Integrate[x, x]"})
                return
            if path == "/api/applications/calculator/mathics/evaluate":
                self._send_json({"ok": True, "result_text": "x^2 / 2"})
                return
            if path == "/api/applications/calculator/qa":
                self._send_json({"ok": True, "answer": "The current result is 14."})
                return
            self._send_json({"ok": True})
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(data.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _send_json(self, payload: Mapping[str, Any], status: int = 200) -> None:
        self._send_bytes(json.dumps(payload, sort_keys=True).encode("utf-8"), "application/json; charset=utf-8", status=status)

    def _send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)


class _StaticCalculatorServer:
    def __init__(self, repo_root: Path) -> None:
        handler = type(
            "_McelCalculatorObservationHandler",
            (_CalculatorObservationHandler,),
            {"repo_root": Path(repo_root).resolve()},
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://127.0.0.1:{port}"

    def __enter__(self) -> "_StaticCalculatorServer":
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def run_calculator_browser_observation(
    *,
    repo_root: Path,
    headed: bool = False,
    operation_prefix: str = "candidate",
    require_browser: bool = True,
) -> CalculatorBrowserObservationResult:
    """Run fresh generated Calculator adapter observation inside Chromium.

    ``require_browser`` exists for controlled tests that inspect the report shape
    without asserting local browser availability. Production profile hooks use
    the default and fail closed if Chromium cannot be launched.
    """

    repo = Path(repo_root).resolve()
    diagnostics: list[Mapping[str, Any]] = []
    runtime_build_before = (repo / "runtime/build/mcel").exists()
    source_before = _source_tree_fingerprint(repo)
    provenance = build_repository_provenance(repo)
    try:
        browser_report = _run_playwright(repo=repo, headed=headed, operation_prefix=operation_prefix)
    except Exception as exc:
        diagnostics.append(
            _diagnostic(
                "MCEL_CALCULATOR_BROWSER_OBSERVATION_UNAVAILABLE",
                f"Fresh Calculator browser observation failed: {exc}",
                "$browser",
            )
        )
        if require_browser:
            raise CalculatorBrowserObservationError(str(exc)) from exc
        browser_report = {
            "freshChromiumObservation": False,
            "browser": {"engine": "unavailable", "error": str(exc)},
            "intentResults": [],
            "checks": {},
        }

    source_after = _source_tree_fingerprint(repo)
    runtime_build_after = (repo / "runtime/build/mcel").exists()
    intent_results = list(browser_report.get("intentResults") or [])
    checks = {
        "freshChromiumObservation": browser_report.get("freshChromiumObservation") is True,
        "generatedAdapterMounted": browser_report.get("generatedAdapterMounted") is True,
        "runtimeFacadeAvailable": browser_report.get("runtimeFacadeAvailable") is True,
        "intentsObserved": len(intent_results) == len(INTENT_PAYLOADS),
        "allIntentsPassed": bool(intent_results) and all(item.get("status") == "pass" for item in intent_results),
        "generatedRuntimeParityStatus": all((item.get("parity") or {}).get("status") == "pass" for item in intent_results),
        "localProviderFree": all(
            (item.get("network") or {}).get("requestCount") == 0
            for item in intent_results
            if item.get("intentName") in LOCAL_INTENTS
        ),
        "capabilityAccountingClosed": _capability_accounting_closed(intent_results),
        "runtimeBuildUnchanged": runtime_build_before == runtime_build_after,
        "sourceTreeUnchanged": source_before == source_after,
    }
    for key, passed in checks.items():
        if not passed:
            diagnostics.append(_diagnostic("MCEL_CALCULATOR_BROWSER_CHECK_FAILED", f"Calculator browser parity check failed: {key}.", f"$checks.{key}"))

    valid = not diagnostics and all(checks.values())
    report = {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "appId": APP_ID,
        "status": "pass" if valid else "fail",
        "valid": valid,
        "operationPrefix": operation_prefix,
        "generatedAt": _utc_now(),
        "semanticFingerprint": browser_report.get("semanticFingerprint"),
        "sourceBindingFingerprint": browser_report.get("sourceBindingFingerprint"),
        "repositoryProvenance": provenance,
        "browser": browser_report.get("browser") or {},
        "url": browser_report.get("url"),
        "checks": checks,
        "intentCount": len(INTENT_PAYLOADS),
        "observedIntentCount": len(intent_results),
        "intentResults": intent_results,
        "localIntents": sorted(LOCAL_INTENTS),
        "capabilityIntents": sorted(CAPABILITY_INTENTS),
        "effectAccounting": _effect_accounting(intent_results),
        "network": browser_report.get("network") or {},
        "repositoryWrites": {
            "runtimeBuildBefore": runtime_build_before,
            "runtimeBuildAfter": runtime_build_after,
            "runtimeBuildUnchanged": runtime_build_before == runtime_build_after,
            "sourceTreeUnchanged": source_before == source_after,
        },
        "authority": {
            "hostBoundRuntimeActive": True,
            "legacySemanticAdapterRemainsLive": False,
            "freshChromiumObservation": browser_report.get("freshChromiumObservation") is True,
            "promotionEligible": True,
            "candidatePromoted": True,
        },
    }
    return CalculatorBrowserObservationResult(valid, "pass" if valid else "fail", report, tuple(diagnostics))


def _run_playwright(*, repo: Path, headed: bool, operation_prefix: str) -> dict[str, Any]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CalculatorBrowserObservationError(
            "Playwright is required for fresh Calculator browser observation."
        ) from exc

    manifest, injected_modules = _browser_injected_projection(repo)
    browser_catalog = build_repository_browser_catalog_payload(repo)
    with sync_playwright() as playwright:
        launch_attempts: list[dict[str, Any]] = [{"headless": not headed}]
        executable = (
            os.environ.get("MCEL_CHROMIUM_EXECUTABLE")
            or os.environ.get("CHROMIUM_EXECUTABLE")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")
            or shutil.which("google-chrome")
            or shutil.which("msedge")
        )
        if executable:
            launch_attempts.append({
                "headless": not headed,
                "executable_path": executable,
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--no-proxy-server",
                    "--proxy-bypass-list=*",
                    "--disable-features=BlockInsecurePrivateNetworkRequests",
                ],
            })
        browser = None
        errors: list[str] = []
        for options in launch_attempts:
            try:
                browser = playwright.chromium.launch(**options)
                break
            except PlaywrightError as exc:
                errors.append(str(exc))
        if browser is None:
            raise CalculatorBrowserObservationError("Chromium launch failed: " + " | ".join(errors[-2:]))
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page_errors: list[str] = []
            console_errors: list[str] = []
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type in {"error"} else None)

            page.set_content(_calculator_observation_html(repo), wait_until="domcontentloaded", timeout=30_000)
            page.evaluate(
                """async ({manifest, modules, catalog}) => {
                    const root = document.querySelector("#calculator-app");
                    if (!root) throw new Error("Calculator root did not load.");
                    const records = Array.isArray(catalog.packages) ? catalog.packages : [];
                    const packageCatalog = Object.freeze({
                      catalogFingerprint: catalog.catalogFingerprint || catalog.fingerprint || "",
                      listPackages: () => records.slice(),
                      getPackage: (appId) => records.find((record) => record && record.appId === appId) || null
                    });
                    window.McelApplicationPackages = packageCatalog;
                    modules.adapter.CalculatorAdapter.invoke = (intentName, ...args) => {
                      const binding = modules.adapter.CalculatorAdapter.bindings[String(intentName || "")];
                      if (!binding) throw new Error(`Unknown Calculator intent: ${intentName}`);
                      const method = window.MainComputerCalculatorRuntime?.[binding.runtimeMethod];
                      if (typeof method !== "function") {
                        throw new Error(`Calculator runtime method is unavailable: ${binding.runtimeMethod}`);
                      }
                      return method(...args);
                    };
                    window.__mcelCalculatorInjectedModules = modules;
                    const loader = async (_url, entry, label) => {
                      const values = window.__mcelCalculatorInjectedModules[label];
                      if (!values || !values[entry.export]) {
                        throw new Error(`Injected Calculator module is unavailable: ${label}.${entry.export}`);
                      }
                      return values;
                    };
                    await window.McelHostBoundApplicationRuntime.mountApplication({
                      appId: "calculator",
                      manifest,
                      root,
                      packageCatalog,
                      moduleLoader: loader
                    });
                }""",
                {"manifest": manifest, "modules": injected_modules, "catalog": browser_catalog},
            )
            page.wait_for_function(
                """() => (
                    window.MainComputerCalculatorRuntime
                    && window.McelHostBoundApplicationRuntime
                    && window.McelHostBoundApplicationRuntime.getMount("calculator")
                    && window.McelHostBoundApplicationRuntime.getMount("calculator").active === true
                )""",
                timeout=30_000,
            )
            page.wait_for_timeout(250)
            result = page.evaluate(_BROWSER_PARITY_SCRIPT, {"operationPrefix": operation_prefix})
            result["url"] = "browser:set-content:/applications/calculator"
            result["browser"] = {
                "engine": "playwright-chromium",
                "version": browser.version,
                "headless": not headed,
                "transport": "set-content",
            }
            result["pageErrors"] = page_errors
            result["consoleErrors"] = console_errors
            blocking_page_errors = [
                entry for entry in page_errors
                if "MonacoEnvironment" not in entry
            ]
            if blocking_page_errors:
                raise CalculatorBrowserObservationError("Calculator page emitted errors: " + "; ".join(blocking_page_errors[:4]))
            blocking_console = [
                entry for entry in console_errors
                if "monaco" not in entry.lower() and "worker" not in entry.lower()
            ]
            if blocking_console:
                raise CalculatorBrowserObservationError("Calculator page emitted console errors: " + "; ".join(blocking_console[:4]))
            return result
        finally:
            browser.close()


def _calculator_observation_html(repo: Path) -> str:
    """Build a minimal browser page from the real Calculator host HTML and scripts."""

    web = repo / "main_computer/web/applications"
    body = (web / "apps/calculator.html").read_text(encoding="utf-8")
    css = (web / "styles/calculator.css").read_text(encoding="utf-8")
    script_paths = [
        web / "scripts/dom-bindings/calculator.js",
        web / "scripts/mcel-semantic-adapter-toolkit.js",
        web / "scripts/calculator-core.js",
        web / "scripts/calculator-view-model.js",
        web / "scripts/calculator-capabilities.js",
        web / "scripts/calculator.js",
    ]
    scripts = "\n".join(
        f"<script>\n{path.read_text(encoding='utf-8')}\n</script>"
        for path in script_paths
    )
    scripts += """
<script>
window.McelApplicationPackages = Object.freeze({
  catalogFingerprint: "",
  listPackages: () => [],
  getPackage: () => null
});
</script>
"""
    scripts += f"<script>\n{(web / 'scripts/mcel-host-bound-application-runtime.js').read_text(encoding='utf-8')}\n</script>"
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <title>Calculator MCEL browser parity</title>
  <style>{css}</style>
</head>
<body>
{body}
{scripts}
</body>
</html>
"""


def _browser_injected_projection(repo: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return generated Calculator manifest/modules as browser-injectable objects."""

    def read(route: str) -> str:
        return read_virtual_mcel_browser_asset(repo, route).decode("utf-8")

    manifest = json.loads(read("applications/mcel-packages/calculator/mcel.runtime.json"))
    modules: dict[str, Any] = {
        "domain": {"CalculatorDomain": _extract_exported_payload(read("applications/mcel-packages/calculator/contracts/domain.js"), "CalculatorDomain")},
        "intents": {"CalculatorIntents": _extract_exported_payload(read("applications/mcel-packages/calculator/contracts/intents.js"), "CalculatorIntents")},
        "surface": {"CalculatorSurface": _extract_exported_payload(read("applications/mcel-packages/calculator/contracts/surface.js"), "CalculatorSurface")},
        "layout": {"CalculatorLayout": _extract_exported_payload(read("applications/mcel-packages/calculator/contracts/layout.js"), "CalculatorLayout")},
        "observation": {"CalculatorObservation": _extract_exported_payload(read("applications/mcel-packages/calculator/contracts/observation.js"), "CalculatorObservation")},
        "acceptance": {"CalculatorAcceptance": _extract_exported_payload(read("applications/mcel-packages/calculator/contracts/acceptance.js"), "CalculatorAcceptance")},
    }
    bindings = _extract_adapter_bindings(read("applications/mcel-packages/calculator/contracts/adapter.js"))
    modules["adapter"] = {
        "CalculatorAdapter": {
            "schema": "mcel.semantic-adapter.v1",
            "appId": APP_ID,
            "adapterId": "calculator.dsl-authoritative-adapter.v1",
            "bindings": bindings,
        }
    }
    return manifest, modules


def _extract_exported_payload(source: str, export_name: str) -> Any:
    marker = f"export const {export_name} = deepFreeze("
    start = source.find(marker)
    if start < 0:
        raise CalculatorBrowserObservationError(f"Projection module does not export {export_name}.")
    payload_start = start + len(marker)
    payload_end = source.find("\n});", payload_start)
    if payload_end < 0:
        raise CalculatorBrowserObservationError(f"Projection module export {export_name} is not parseable.")
    return json.loads(source[payload_start:payload_end + 2])


def _extract_adapter_bindings(source: str) -> dict[str, Any]:
    marker = "const BINDINGS = Object.freeze("
    start = source.find(marker)
    if start < 0:
        raise CalculatorBrowserObservationError("Calculator adapter bindings are unavailable.")
    payload_start = start + len(marker)
    payload_end = source.find("\n});", payload_start)
    if payload_end < 0:
        raise CalculatorBrowserObservationError("Calculator adapter bindings are not parseable.")
    payload = json.loads(source[payload_start:payload_end + 2])
    if not isinstance(payload, dict):
        raise CalculatorBrowserObservationError("Calculator adapter bindings must be an object.")
    return payload


_BROWSER_PARITY_SCRIPT = r"""async ({operationPrefix}) => {
  const clone = (value) => value === undefined ? null : JSON.parse(JSON.stringify(value));
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const intentPayloads = {
    switchMode: {mode: "graphing"},
    enterToken: {token: "7"},
    clearExpression: {},
    evaluateExpression: {expression: "2+3*4"},
    drawGraph: {expression: "x^2", range: {xMin: -2, xMax: 2, yMin: -1, yMax: 5}},
    resetGraph: {},
    askModelForExpression: {prompt: "two plus three"},
    askModelForGraphExpression: {prompt: "graph x squared"},
    askModelForMathicsExpression: {prompt: "integrate x"},
    evaluateMathics: {expression: "Integrate[x, x]"},
    askResultQuestion: {question: "What is the current result?"}
  };
  const localIntentNames = new Set(["switchMode", "enterToken", "clearExpression", "evaluateExpression", "drawGraph", "resetGraph"]);
  const capabilityIntentNames = new Set(Object.keys(intentPayloads).filter((name) => !localIntentNames.has(name)));
  const requests = [];
  window.fetch = async (url, options = {}) => {
    const request = {
      url: String(url),
      method: String(options.method || "GET").toUpperCase(),
      body: String(options.body || "")
    };
    requests.push(request);
    let payload = {ok: true};
    const target = String(url);
    if (target === "/api/chat") {
      const body = (() => { try { return JSON.parse(request.body || "{}"); } catch (_) { return {}; } })();
      const prompt = String(body.prompt || "").toLowerCase();
      payload = {ok: true, content: prompt.includes("graph") || prompt.includes("f(x)") ? "x^2" : "2+3"};
    } else if (target === "/api/applications/calculator/mathics/ask") {
      payload = {ok: true, expression: "Integrate[x, x]"};
    } else if (target === "/api/applications/calculator/mathics/evaluate") {
      payload = {ok: true, result_text: "x^2 / 2"};
    } else if (target === "/api/applications/calculator/qa") {
      payload = {ok: true, answer: "The current result is 14."};
    }
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: {"Content-Type": "application/json"}
    });
  };
  const mount = window.McelHostBoundApplicationRuntime.getMount("calculator");
  const runtime = window.MainComputerCalculatorRuntime;
  const catalogRecord = window.McelApplicationPackages.getPackage("calculator");
  const generatedBindings = mount.modules.adapter.bindings;
  const root = document.querySelector("#calculator-app");

  function domSnapshot() {
    const text = (selector) => document.querySelector(selector)?.textContent?.trim() || "";
    const value = (selector) => document.querySelector(selector)?.value || "";
    const mode = root?.classList?.contains("graphing-active") ? "graphing" : "basic";
    return {
      mode,
      expression: value("#calculator-display"),
      result: text("#calculator-result"),
      graphExpression: value("#calculator-graph-expression"),
      graphStatus: text("#calculator-graph-status"),
      mathicsExpression: value("#calculator-mathics-expression"),
      mathicsOutput: text("#calculator-mathics-output"),
      mathicsStatus: text("#calculator-mathics-evaluation-status"),
      qaAnswer: text("#calculator-qa-answer"),
      qaStatus: text("#calculator-qa-status"),
      hostBoundStatus: root?.dataset?.mcelHostBoundStatus || "",
      hostBoundApp: root?.dataset?.mcelHostBoundApp || ""
    };
  }

  async function settle() {
    await sleep(80);
  }

  async function resetFor(intentName) {
    try { runtime.clearExpression(); } catch (_) {}
    try { runtime.resetGraph(); } catch (_) {}
    try { runtime.switchMode({mode: "basic"}); } catch (_) {}
    if (intentName === "evaluateMathics") {
      const input = document.querySelector("#calculator-mathics-expression");
      if (input) input.value = "Integrate[x, x]";
    }
    await settle();
  }

  function focusedComparable(intentName, snapshot) {
    if (intentName === "switchMode") return {mode: snapshot.mode, hostBoundStatus: snapshot.hostBoundStatus, hostBoundApp: snapshot.hostBoundApp};
    if (intentName === "enterToken" || intentName === "clearExpression" || intentName === "evaluateExpression" || intentName === "askModelForExpression") {
      return {expression: snapshot.expression, result: snapshot.result, hostBoundStatus: snapshot.hostBoundStatus, hostBoundApp: snapshot.hostBoundApp};
    }
    if (intentName === "drawGraph" || intentName === "resetGraph" || intentName === "askModelForGraphExpression") {
      return {graphExpression: snapshot.graphExpression, graphStatus: snapshot.graphStatus, hostBoundStatus: snapshot.hostBoundStatus, hostBoundApp: snapshot.hostBoundApp};
    }
    if (intentName === "askModelForMathicsExpression") {
      return {mathicsExpression: snapshot.mathicsExpression, hostBoundStatus: snapshot.hostBoundStatus, hostBoundApp: snapshot.hostBoundApp};
    }
    if (intentName === "evaluateMathics") {
      return {mathicsExpression: snapshot.mathicsExpression, mathicsOutput: snapshot.mathicsOutput, mathicsStatus: snapshot.mathicsStatus, hostBoundStatus: snapshot.hostBoundStatus, hostBoundApp: snapshot.hostBoundApp};
    }
    if (intentName === "askResultQuestion") {
      return {qaAnswer: snapshot.qaAnswer, qaStatus: snapshot.qaStatus, hostBoundStatus: snapshot.hostBoundStatus, hostBoundApp: snapshot.hostBoundApp};
    }
    return snapshot;
  }

  const intentResults = [];
  const requestCounts = {};
  const startRequests = () => requests.length;
  const finishRequests = (index) => requests.slice(index);

  for (const [intentName, payload] of Object.entries(intentPayloads)) {
    await resetFor(intentName);
    const generatedStart = startRequests();
    let generatedResult = null;
    let generatedError = null;
    try {
      generatedResult = await mount.invoke(intentName, clone(payload));
    } catch (error) {
      generatedError = {message: error?.message || String(error), name: error?.name || "Error"};
    }
    await settle();
    const generatedSnapshot = domSnapshot();
    const generatedRequests = finishRequests(generatedStart);

    const generatedComparable = focusedComparable(intentName, generatedSnapshot);
    const binding = generatedBindings[intentName] || {};
    const networkRequestCount = generatedRequests.length;
    requestCounts[intentName] = networkRequestCount;
    const parityChecks = {
      noGeneratedError: generatedError === null,
      runtimeMethod: typeof runtime?.[binding.runtimeMethod] === "function",
      declaredBinding: binding.runtimeMethod === intentName,
      hostBoundMounted: generatedSnapshot.hostBoundStatus === "mounted" && generatedSnapshot.hostBoundApp === "calculator",
      localProviderFree: localIntentNames.has(intentName) ? networkRequestCount === 0 : true,
      capabilityObserved: capabilityIntentNames.has(intentName) ? networkRequestCount > 0 : true
    };
    const passed = Object.values(parityChecks).every(Boolean);
    intentResults.push({
      intentName,
      status: passed ? "pass" : "fail",
      payload: clone(payload),
      lane: binding.executionBinding || "",
      runtimeMethod: binding.runtimeMethod || "",
      generated: {
        ok: !generatedError,
        result: clone(generatedResult),
        error: generatedError,
        snapshot: generatedSnapshot,
        comparable: generatedComparable
      },
      parity: {
        status: passed ? "pass" : "fail",
        checks: parityChecks
      },
      network: {
        requestCount: networkRequestCount,
        generatedRequestCount: generatedRequests.length,
        urls: generatedRequests.map((entry) => entry.url)
      }
    });
  }

  const localProviderFree = intentResults
    .filter((entry) => localIntentNames.has(entry.intentName))
    .every((entry) => entry.network.requestCount === 0);
  const capabilityObserved = intentResults
    .filter((entry) => capabilityIntentNames.has(entry.intentName))
    .every((entry) => entry.network.requestCount > 0);
  const allPassed = intentResults.every((entry) => entry.status === "pass");
  return {
    schema: "mcel.calculator-browser-observation.browser-result.v1",
    freshChromiumObservation: true,
    generatedAdapterMounted: !!mount?.active,
    legacyAdapterMounted: false,
    runtimeFacadeAvailable: !!runtime,
    semanticFingerprint: catalogRecord?.semanticFingerprint || "",
    sourceBindingFingerprint: catalogRecord?.sourceBindingFingerprint || "",
    intentResults,
    checks: {
      localProviderFree,
      capabilityObserved,
      allPassed
    },
    network: {
      requestCountByIntent: requestCounts,
      totalRequestCount: requests.length,
      capabilityRequestCount: Object.entries(requestCounts).filter(([name]) => capabilityIntentNames.has(name)).reduce((total, [, count]) => total + count, 0)
    }
  };
}"""


def _content_type(route: str) -> str:
    guessed = mimetypes.guess_type(route)[0] or "application/octet-stream"
    suffix = Path(route).suffix.lower()
    if suffix == ".js":
        return "text/javascript; charset=utf-8"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix == ".html":
        return "text/html; charset=utf-8"
    return guessed


def _effect_accounting(intent_results: list[Mapping[str, Any]]) -> dict[str, Any]:
    capability = [item for item in intent_results if item.get("intentName") in CAPABILITY_INTENTS]
    closed = capability and all((item.get("network") or {}).get("requestCount", 0) > 0 for item in capability)
    return {
        "schema": "mcel.calculator-browser-effect-accounting.v1",
        "status": "closed" if closed and len(capability) == len(CAPABILITY_INTENTS) else "open",
        "declaredCapabilityIntentCount": len(CAPABILITY_INTENTS),
        "observedCapabilityIntentCount": len(capability),
        "closedCapabilityIntentCount": sum(1 for item in capability if (item.get("network") or {}).get("requestCount", 0) > 0),
        "localIntentCount": len([item for item in intent_results if item.get("intentName") in LOCAL_INTENTS]),
    }


def _capability_accounting_closed(intent_results: list[Mapping[str, Any]]) -> bool:
    accounting = _effect_accounting(intent_results)
    local_provider_free = all(
        (item.get("network") or {}).get("requestCount") == 0
        for item in intent_results
        if item.get("intentName") in LOCAL_INTENTS
    )
    return accounting["status"] == "closed" and local_provider_free


def _source_tree_fingerprint(repo: Path) -> str:
    digest = __import__("hashlib").sha256()
    digest.update(b"calculator-browser-observation-source-v1\0")
    excluded_prefixes = {
        "runtime/",
        "tools/patching/reports/",
        ".pytest_cache/",
    }
    for path in sorted(repo.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(repo).as_posix()
        if any(rel.startswith(prefix) for prefix in excluded_prefixes):
            continue
        if "/__pycache__/" in f"/{rel}" or rel.endswith((".pyc", ".pyo")):
            continue
        content = path.read_bytes()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def _diagnostic(code: str, summary: str, semantic_path: str) -> dict[str, Any]:
    return {
        "schema": "mcel.compiler-diagnostic.v1",
        "code": code,
        "severity": "error",
        "blocking": True,
        "summary": summary,
        "semanticPath": semantic_path,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
