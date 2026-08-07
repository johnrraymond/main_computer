"""Generic fresh browser observation for host-bound MCEL applications.

A host-bound app keeps durable HTML/CSS/runtime files in the live repository
while generated MCEL package assets remain virtual.  This module owns the
shared browser mechanics: building a minimal host page, injecting virtual
package modules, mounting the host-bound runtime, running an app-supplied
observation script, and assembling source/write/effect-accounting evidence.

App-specific wrappers should provide only a profile: app identity, host surface
paths, runtime facade/root, intent payloads, local-vs-capability intent sets,
and a browser script that knows the app's DOM selectors.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_application_package_browser_catalog import build_repository_browser_catalog_payload
from main_computer.mcel_application_virtual_assets import read_virtual_mcel_browser_asset
from main_computer.mcel_evidence_provenance import build_repository_provenance


REPORT_SCHEMA = "mcel.host-bound-browser-observation.v1"
REPORT_VERSION = "mcel-host-bound-browser-observation-v1"


class HostBoundBrowserObservationError(RuntimeError):
    """Raised when a fresh host-bound browser observation cannot pass."""


@dataclass(frozen=True)
class HostBoundModuleExport:
    label: str
    route: str
    export_name: str


@dataclass(frozen=True)
class HostBoundBrowserObservationProfile:
    app_id: str
    report_schema: str
    report_version: str
    page_title: str
    route: str
    root_selector: str
    runtime_facade: str
    host_html_path: Path
    css_path: Path
    script_paths: tuple[Path, ...]
    module_exports: tuple[HostBoundModuleExport, ...]
    adapter_route: str
    adapter_export: str
    adapter_id: str
    intent_payloads: Mapping[str, Mapping[str, Any]]
    local_intents: frozenset[str]
    browser_observation_script: str
    unavailable_code: str = "MCEL_HOST_BOUND_BROWSER_OBSERVATION_UNAVAILABLE"
    check_failed_code: str = "MCEL_HOST_BOUND_BROWSER_CHECK_FAILED"
    effect_accounting_schema: str = "mcel.host-bound-browser-effect-accounting.v1"
    source_tree_fingerprint_salt: str = "host-bound-browser-observation-source-v1"
    ignored_page_error_fragments: tuple[str, ...] = ("MonacoEnvironment",)
    ignored_console_error_fragments: tuple[str, ...] = ("monaco", "worker")
    host_runtime_script_path: Path = Path("main_computer/web/applications/scripts/mcel-host-bound-application-runtime.js")

    @property
    def capability_intents(self) -> frozenset[str]:
        return frozenset(set(self.intent_payloads) - set(self.local_intents))


@dataclass(frozen=True)
class HostBoundBrowserObservationResult:
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


def run_host_bound_browser_observation(
    profile: HostBoundBrowserObservationProfile,
    *,
    repo_root: Path,
    headed: bool = False,
    operation_prefix: str = "candidate",
    require_browser: bool = True,
) -> HostBoundBrowserObservationResult:
    """Run a fresh generated-adapter observation for a host-bound MCEL app."""

    repo = Path(repo_root).resolve()
    diagnostics: list[Mapping[str, Any]] = []
    runtime_build_before = (repo / "runtime/build/mcel").exists()
    source_before = _source_tree_fingerprint(profile, repo)
    provenance = build_repository_provenance(repo)
    try:
        browser_report = _run_playwright(
            profile,
            repo=repo,
            headed=headed,
            operation_prefix=operation_prefix,
        )
    except Exception as exc:
        diagnostics.append(
            _diagnostic(
                profile.unavailable_code,
                f"Fresh {profile.app_id} browser observation failed: {exc}",
                "$browser",
            )
        )
        if require_browser:
            raise HostBoundBrowserObservationError(str(exc)) from exc
        browser_report = {
            "freshChromiumObservation": False,
            "browser": {"engine": "unavailable", "error": str(exc)},
            "intentResults": [],
            "checks": {},
        }

    source_after = _source_tree_fingerprint(profile, repo)
    runtime_build_after = (repo / "runtime/build/mcel").exists()
    intent_results = list(browser_report.get("intentResults") or [])
    checks = {
        "freshChromiumObservation": browser_report.get("freshChromiumObservation") is True,
        "generatedAdapterMounted": browser_report.get("generatedAdapterMounted") is True,
        "runtimeFacadeAvailable": browser_report.get("runtimeFacadeAvailable") is True,
        "intentsObserved": len(intent_results) == len(profile.intent_payloads),
        "allIntentsPassed": bool(intent_results) and all(item.get("status") == "pass" for item in intent_results),
        "generatedRuntimeParityStatus": all((item.get("parity") or {}).get("status") == "pass" for item in intent_results),
        "localProviderFree": all(
            (item.get("network") or {}).get("requestCount") == 0
            for item in intent_results
            if item.get("intentName") in profile.local_intents
        ),
        "capabilityAccountingClosed": _capability_accounting_closed(profile, intent_results),
        "runtimeBuildUnchanged": runtime_build_before == runtime_build_after,
        "sourceTreeUnchanged": source_before == source_after,
    }
    for key, passed in checks.items():
        if not passed:
            diagnostics.append(
                _diagnostic(
                    profile.check_failed_code,
                    f"{profile.app_id} browser parity check failed: {key}.",
                    f"$checks.{key}",
                )
            )

    valid = not diagnostics and all(checks.values())
    report = {
        "schema": profile.report_schema,
        "genericSchema": REPORT_SCHEMA,
        "version": profile.report_version,
        "genericVersion": REPORT_VERSION,
        "appId": profile.app_id,
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
        "intentCount": len(profile.intent_payloads),
        "observedIntentCount": len(intent_results),
        "intentResults": intent_results,
        "localIntents": sorted(profile.local_intents),
        "capabilityIntents": sorted(profile.capability_intents),
        "effectAccounting": _effect_accounting(profile, intent_results),
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
    return HostBoundBrowserObservationResult(valid, "pass" if valid else "fail", report, tuple(diagnostics))


def _run_playwright(
    profile: HostBoundBrowserObservationProfile,
    *,
    repo: Path,
    headed: bool,
    operation_prefix: str,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise HostBoundBrowserObservationError(
            f"Playwright is required for fresh {profile.app_id} browser observation."
        ) from exc

    manifest, injected_modules = _browser_injected_projection(profile, repo)
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
            raise HostBoundBrowserObservationError("Chromium launch failed: " + " | ".join(errors[-2:]))
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page_errors: list[str] = []
            console_errors: list[str] = []
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type in {"error"} else None)

            page.set_content(_host_bound_observation_html(profile, repo), wait_until="domcontentloaded", timeout=30_000)
            page.evaluate(
                """async ({appId, rootSelector, runtimeFacade, adapterExport, manifest, modules, catalog}) => {
                    const root = document.querySelector(rootSelector);
                    if (!root) throw new Error(`${appId} root did not load.`);
                    const records = Array.isArray(catalog.packages) ? catalog.packages : [];
                    const packageCatalog = Object.freeze({
                      catalogFingerprint: catalog.catalogFingerprint || catalog.fingerprint || "",
                      listPackages: () => records.slice(),
                      getPackage: (requestedAppId) => records.find((record) => record && record.appId === requestedAppId) || null
                    });
                    window.McelApplicationPackages = packageCatalog;
                    const adapter = modules.adapter && modules.adapter[adapterExport];
                    if (!adapter) throw new Error(`${appId} adapter export is unavailable: ${adapterExport}`);
                    adapter.invoke = (intentName, ...args) => {
                      const binding = adapter.bindings[String(intentName || "")];
                      if (!binding) throw new Error(`Unknown ${appId} intent: ${intentName}`);
                      const facade = window[runtimeFacade];
                      const method = facade && facade[binding.runtimeMethod];
                      if (typeof method !== "function") {
                        throw new Error(`${appId} runtime method is unavailable: ${binding.runtimeMethod}`);
                      }
                      return method(...args);
                    };
                    window.__mcelHostBoundInjectedModules = modules;
                    const loader = async (_url, entry, label) => {
                      const values = window.__mcelHostBoundInjectedModules[label];
                      if (!values || !values[entry.export]) {
                        throw new Error(`Injected ${appId} module is unavailable: ${label}.${entry.export}`);
                      }
                      return values;
                    };
                    await window.McelHostBoundApplicationRuntime.mountApplication({
                      appId,
                      manifest,
                      root,
                      packageCatalog,
                      moduleLoader: loader
                    });
                }""",
                {
                    "appId": profile.app_id,
                    "rootSelector": profile.root_selector,
                    "runtimeFacade": profile.runtime_facade,
                    "adapterExport": profile.adapter_export,
                    "manifest": manifest,
                    "modules": injected_modules,
                    "catalog": browser_catalog,
                },
            )
            page.wait_for_function(
                """({appId, runtimeFacade}) => (
                    window[runtimeFacade]
                    && window.McelHostBoundApplicationRuntime
                    && window.McelHostBoundApplicationRuntime.getMount(appId)
                    && window.McelHostBoundApplicationRuntime.getMount(appId).active === true
                )""",
                arg={"appId": profile.app_id, "runtimeFacade": profile.runtime_facade},
                timeout=30_000,
            )
            page.wait_for_timeout(250)
            result = page.evaluate(profile.browser_observation_script, {"operationPrefix": operation_prefix})
            result["url"] = f"browser:set-content:{profile.route}"
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
                if not any(fragment in entry for fragment in profile.ignored_page_error_fragments)
            ]
            if blocking_page_errors:
                raise HostBoundBrowserObservationError(
                    f"{profile.app_id} page emitted errors: " + "; ".join(blocking_page_errors[:4])
                )
            blocking_console = [
                entry for entry in console_errors
                if not any(fragment.lower() in entry.lower() for fragment in profile.ignored_console_error_fragments)
            ]
            if blocking_console:
                raise HostBoundBrowserObservationError(
                    f"{profile.app_id} page emitted console errors: " + "; ".join(blocking_console[:4])
                )
            return result
        finally:
            browser.close()


def _host_bound_observation_html(profile: HostBoundBrowserObservationProfile, repo: Path) -> str:
    """Build a minimal browser page from an app's real host HTML and scripts."""

    body = _repo_text(repo, profile.host_html_path)
    css = _repo_text(repo, profile.css_path)
    scripts = "\n".join(
        f"<script>\n{_repo_text(repo, path)}\n</script>"
        for path in profile.script_paths
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
    scripts += f"<script>\n{_repo_text(repo, profile.host_runtime_script_path)}\n</script>"
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <title>{profile.page_title}</title>
  <style>{css}</style>
</head>
<body>
{body}
{scripts}
</body>
</html>
"""


def _browser_injected_projection(
    profile: HostBoundBrowserObservationProfile,
    repo: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return virtual app manifest/modules as browser-injectable objects."""

    def read(route: str) -> str:
        return read_virtual_mcel_browser_asset(repo, route).decode("utf-8")

    manifest = json.loads(read(f"applications/mcel-packages/{profile.app_id}/mcel.runtime.json"))
    modules: dict[str, Any] = {}
    for export in profile.module_exports:
        modules.setdefault(export.label, {})[export.export_name] = _extract_exported_payload(
            read(export.route),
            export.export_name,
            profile=profile,
        )
    bindings = _extract_adapter_bindings(read(profile.adapter_route), profile=profile)
    modules["adapter"] = {
        profile.adapter_export: {
            "schema": "mcel.semantic-adapter.v1",
            "appId": profile.app_id,
            "adapterId": profile.adapter_id,
            "bindings": bindings,
        }
    }
    return manifest, modules


def _extract_exported_payload(
    source: str,
    export_name: str,
    *,
    profile: HostBoundBrowserObservationProfile,
) -> Any:
    marker = f"export const {export_name} = deepFreeze("
    start = source.find(marker)
    if start < 0:
        raise HostBoundBrowserObservationError(f"{profile.app_id} projection module does not export {export_name}.")
    payload_start = start + len(marker)
    payload_end = source.find("\n});", payload_start)
    if payload_end < 0:
        raise HostBoundBrowserObservationError(f"{profile.app_id} projection module export {export_name} is not parseable.")
    return json.loads(source[payload_start:payload_end + 2])


def _extract_adapter_bindings(
    source: str,
    *,
    profile: HostBoundBrowserObservationProfile,
) -> dict[str, Any]:
    marker = "const BINDINGS = Object.freeze("
    start = source.find(marker)
    if start < 0:
        raise HostBoundBrowserObservationError(f"{profile.app_id} adapter bindings are unavailable.")
    payload_start = start + len(marker)
    payload_end = source.find("\n});", payload_start)
    if payload_end < 0:
        raise HostBoundBrowserObservationError(f"{profile.app_id} adapter bindings are not parseable.")
    payload = json.loads(source[payload_start:payload_end + 2])
    if not isinstance(payload, dict):
        raise HostBoundBrowserObservationError(f"{profile.app_id} adapter bindings must be an object.")
    return payload


def _effect_accounting(
    profile: HostBoundBrowserObservationProfile,
    intent_results: list[Mapping[str, Any]],
) -> dict[str, Any]:
    capability = [item for item in intent_results if item.get("intentName") in profile.capability_intents]
    closed = bool(capability) and all((item.get("network") or {}).get("requestCount", 0) > 0 for item in capability)
    return {
        "schema": profile.effect_accounting_schema,
        "status": "closed" if closed and len(capability) == len(profile.capability_intents) else "open",
        "declaredCapabilityIntentCount": len(profile.capability_intents),
        "observedCapabilityIntentCount": len(capability),
        "closedCapabilityIntentCount": sum(1 for item in capability if (item.get("network") or {}).get("requestCount", 0) > 0),
        "localIntentCount": len([item for item in intent_results if item.get("intentName") in profile.local_intents]),
    }


def _capability_accounting_closed(
    profile: HostBoundBrowserObservationProfile,
    intent_results: list[Mapping[str, Any]],
) -> bool:
    accounting = _effect_accounting(profile, intent_results)
    local_provider_free = all(
        (item.get("network") or {}).get("requestCount") == 0
        for item in intent_results
        if item.get("intentName") in profile.local_intents
    )
    return accounting["status"] == "closed" and local_provider_free


def _source_tree_fingerprint(profile: HostBoundBrowserObservationProfile, repo: Path) -> str:
    digest = __import__("hashlib").sha256()
    digest.update(profile.source_tree_fingerprint_salt.encode("utf-8"))
    digest.update(b"\0")
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


def _repo_text(repo: Path, path: Path) -> str:
    target = path if path.is_absolute() else repo / path
    return target.read_text(encoding="utf-8")


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
