#!/usr/bin/env python3
"""Run package-bound, operation-linked MCEL browser observation evidence."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

try:
    from .mcel_application_package_browser_catalog import check_browser_catalog
    from .mcel_application_packages import build_application_package_catalog, repository_root
    from .mcel_application_runtime_projection import check_runtime_projections
    from .mcel_evidence_provenance import build_repository_provenance
except ImportError:
    _ROOT = Path(__file__).resolve().parents[1]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from main_computer.mcel_application_package_browser_catalog import check_browser_catalog
    from main_computer.mcel_application_packages import build_application_package_catalog, repository_root
    from main_computer.mcel_application_runtime_projection import check_runtime_projections
    from main_computer.mcel_evidence_provenance import build_repository_provenance


RUNNER_VERSION = "mcel-application-observation-runner-v1"
REPORT_SCHEMA = "mcel.application-operation-observation-report.v1"
DEFAULT_OUTPUT_ROOT = Path("runtime/reports/mcel-observation")


class ObservationRunnerError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value.lower()).strip("-")


class _QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


class _StaticServer:
    def __init__(self, web_root: Path) -> None:
        handler = partial(_QuietStaticHandler, directory=str(web_root))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://localhost:{port}"

    def __enter__(self) -> "_StaticServer":
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _package_record(catalog: Any, app_id: str) -> Any:
    matches = [record for record in catalog.packages if record.app_id == app_id]
    if len(matches) != 1:
        raise ObservationRunnerError(f"MCEL application package {app_id!r} was not discovered exactly once.")
    record = matches[0]
    if not record.valid or not record.fingerprint:
        raise ObservationRunnerError(f"MCEL application package {app_id!r} is invalid.")
    return record


def _run_browser(*, repo: Path, app_id: str, repository_fingerprint: str, headed: bool) -> dict[str, Any]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ObservationRunnerError(
            "Playwright is required. Install it and Chromium with: "
            "python -m pip install playwright; python -m playwright install chromium"
        ) from exc

    web_root = repo / "main_computer" / "web"
    with _StaticServer(web_root) as server, sync_playwright() as playwright:
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
                ],
            })
        browser = None
        launch_errors: list[str] = []
        for launch_options in launch_attempts:
            try:
                browser = playwright.chromium.launch(**launch_options)
                break
            except PlaywrightError as exc:
                launch_errors.append(str(exc))
        if browser is None:
            raise ObservationRunnerError(
                "Playwright Chromium is unavailable. Run: python -m playwright install chromium. "
                + " | ".join(launch_errors[-2:])
            )
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            page = context.new_page()
            url = f"{server.base_url}/mcel-package-host.html?app={app_id}"
            try:
                page.goto(url, wait_until="networkidle")
                page.wait_for_function(
                    "window.McelApplicationPackageHost && "
                    "(window.McelApplicationPackageHost.ready === true || window.McelApplicationPackageHost.error)",
                    timeout=30_000,
                )
            except PlaywrightError as exc:
                raise ObservationRunnerError(f"Could not open the MCEL package host: {exc}") from exc
            host_state = page.evaluate(
                "() => ({ready: window.McelApplicationPackageHost.ready, error: window.McelApplicationPackageHost.error || null})"
            )
            if host_state.get("ready") is not True:
                raise ObservationRunnerError(f"MCEL package host failed: {host_state.get('error')}")

            captured_at = _utc_now()
            operation_id = f"{app_id}.increment.observation-1"
            browser_meta = {
                "engine": "playwright-chromium",
                "version": browser.version,
                "headless": not headed,
            }
            result = page.evaluate(
                """async ({operationId, repositoryFingerprint, capturedAt, browser}) => {
                  const host = window.McelApplicationPackageHost;
                  const result = await host.dispatchAndObserve("increment", {}, {
                    operationId,
                    expectedRevision: host.mount.application.revision,
                    repositoryFingerprint,
                    capturedAt,
                    codeFingerprint: host.record.runtimeProjection.fingerprint,
                    browser,
                    viewport: {width: window.innerWidth, height: window.innerHeight, deviceScaleFactor: window.devicePixelRatio}
                  });

                  const root = host.mount.root;
                  const surface = host.mount.surface;
                  const layout = host.mount.layout;
                  const rect = (element) => {
                    const box = element.getBoundingClientRect();
                    const style = window.getComputedStyle(element);
                    return {
                      left: box.left,
                      top: box.top,
                      right: box.right,
                      bottom: box.bottom,
                      width: box.width,
                      height: box.height,
                      visible: style.display !== "none" && style.visibility !== "hidden" && box.width > 0 && box.height > 0
                    };
                  };
                  const surfaceRoots = Array.from(document.querySelectorAll("[data-mcel-surface-id]"))
                    .filter((element) => element.getAttribute("data-mcel-surface-id") === surface.surfaceId);
                  const rootBox = rect(root);
                  const controls = Array.from(root.querySelectorAll("[data-mcel-intent-id]"));
                  const controlBoxes = controls.map((element) => ({
                    nodeId: element.getAttribute("data-mcel-node-id") || "",
                    intentId: element.getAttribute("data-mcel-intent-id") || "",
                    ...rect(element)
                  }));
                  const controlsUsable = controlBoxes.length > 0 && controlBoxes.every((box) => box.visible && box.width >= 44 && box.height >= 44);
                  const controlsOverlap = controlBoxes.some((left, index) => controlBoxes.slice(index + 1).some((right) =>
                    left.left < right.right && left.right > right.left && left.top < right.bottom && left.bottom > right.top
                  ));
                  const horizontalOverflow = document.documentElement.scrollWidth > window.innerWidth + 1 || root.scrollWidth > root.clientWidth + 1;
                  const requiredRegionIds = (surface.regions || []).map((region) => region.id);
                  const declaredLayoutRegions = Object.keys(layout.regions || {});
                  const layoutComplete = requiredRegionIds.every((id) => declaredLayoutRegions.includes(id));
                  const semanticSurfacePass = result.observation?.comparison?.surfaceMatches === true;
                  const runtimeOwnershipPass = surfaceRoots.length === 1 && surfaceRoots[0] === root && rootBox.visible;
                  const runtimeVisualFitPass = rootBox.visible && rootBox.width <= window.innerWidth + 1 && !horizontalOverflow && controlsUsable && !controlsOverlap;
                  const layers = [
                    {id: "semantic-surface", status: semanticSurfacePass ? "pass" : "fail"},
                    {id: "layout-grammar", status: layoutComplete ? "pass" : "fail"},
                    {id: "runtime-ownership", status: runtimeOwnershipPass ? "pass" : "fail"},
                    {id: "runtime-visual-fit", status: runtimeVisualFitPass ? "pass" : "fail"},
                    {id: "diagnostic-no-throw", status: "pass"}
                  ];
                  const failedLayerIds = layers.filter((layer) => layer.status !== "pass").map((layer) => layer.id);
                  result.surfaceConformance = {
                    contractVersion: "mcel.app-surface-conformance.v1",
                    appId: host.record.appId,
                    surfaceId: surface.surfaceId,
                    status: failedLayerIds.length ? "fail" : "pass",
                    valid: failedLayerIds.length === 0,
                    conformanceRequired: true,
                    requiredLayerIds: layers.map((layer) => layer.id),
                    requiredLayerStatuses: Object.fromEntries(layers.map((layer) => [layer.id, layer.status])),
                    layers,
                    failedLayerIds,
                    unavailableLayerIds: [],
                    measurements: {
                      viewport: {width: window.innerWidth, height: window.innerHeight, deviceScaleFactor: window.devicePixelRatio},
                      root: rootBox,
                      surfaceRootCount: surfaceRoots.length,
                      controlBoxes,
                      controlsUsable,
                      controlsOverlap,
                      horizontalOverflow,
                      requiredRegionIds,
                      declaredLayoutRegions
                    }
                  };
                  return result;
                }""",
                {
                    "operationId": operation_id,
                    "repositoryFingerprint": repository_fingerprint,
                    "capturedAt": captured_at,
                    "browser": browser_meta,
                },
            )
            return {
                "url": url,
                "browser": browser_meta,
                "operationResult": result["operationResult"],
                "observation": result["observation"],
                "surfaceConformance": result["surfaceConformance"],
            }
        finally:
            browser.close()


def _render_markdown(report: Mapping[str, Any]) -> str:
    observation = report.get("observation") or {}
    package = report.get("package") or {}
    lines = [
        "# MCEL Application Operation Observation",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Application: `{report.get('appId')}`",
        f"- Browser: `{(report.get('browser') or {}).get('engine', '')}`",
        f"- Operation: `{observation.get('operationId', '')}`",
        f"- Package fingerprint: `{package.get('fingerprint', '')}`",
        f"- Runtime projection fingerprint: `{observation.get('runtimeProjectionFingerprint', '')}`",
        f"- Repository fingerprint: `{(report.get('repositoryProvenance') or {}).get('fingerprint', '')}`",
        "",
        "## Surface conformance",
        "",
        f"- Status: `{(report.get('surfaceConformance') or {}).get('status', '')}`",
        f"- Required layers: `{(report.get('surfaceConformance') or {}).get('requiredLayerStatuses', {})}`",
        "",
        "## Comparison",
        "",
        f"- Canonical/browser state: `{(observation.get('comparison') or {}).get('stateMatches')}`",
        f"- Visible receipt: `{(observation.get('comparison') or {}).get('receiptMatches')}`",
        f"- Surface identity: `{(observation.get('comparison') or {}).get('surfaceMatches')}`",
        "",
    ]
    return "\n".join(lines)


def run_observation(*, repo: Path, app_id: str, headed: bool = False) -> dict[str, Any]:
    catalog = build_application_package_catalog(repo)
    if not catalog.ok or catalog.invalid_count:
        raise ObservationRunnerError("The repository MCEL application-package catalog is invalid.")
    record = _package_record(catalog, app_id)

    browser_fresh, _browser_output, _browser_payload = check_browser_catalog(repo)
    projection_fresh, _projection_output, projection_set = check_runtime_projections(repo)
    if not browser_fresh:
        raise ObservationRunnerError("The browser application-package catalog is stale.")
    if not projection_fresh:
        raise ObservationRunnerError("The browser-safe application runtime projection is stale.")
    projections = [entry for entry in projection_set.projections if entry.app_id == app_id]
    if len(projections) != 1:
        raise ObservationRunnerError(f"Runtime projection for {app_id!r} was not found exactly once.")

    provenance = build_repository_provenance(repo)
    browser_result = _run_browser(
        repo=repo,
        app_id=app_id,
        repository_fingerprint=provenance["fingerprint"],
        headed=headed,
    )
    observation = browser_result["observation"]
    expected_projection = projections[0]
    if observation.get("packageFingerprint") != record.fingerprint:
        raise ObservationRunnerError("Browser observation package fingerprint does not match the package authority.")
    if observation.get("runtimeProjectionFingerprint") != expected_projection.fingerprint:
        raise ObservationRunnerError("Browser observation runtime projection fingerprint does not match the projection authority.")
    if observation.get("repositoryFingerprint") != provenance["fingerprint"]:
        raise ObservationRunnerError("Browser observation repository fingerprint does not match current provenance.")
    if observation.get("status") != "pass" or observation.get("ok") is not True:
        raise ObservationRunnerError("Browser operation observation did not pass.")
    surface_conformance = browser_result.get("surfaceConformance") or {}
    if surface_conformance.get("status") != "pass" or surface_conformance.get("valid") is not True:
        raise ObservationRunnerError("Browser application surface conformance did not pass.")

    return {
        "schema": REPORT_SCHEMA,
        "runner": RUNNER_VERSION,
        "status": "pass",
        "ok": True,
        "evidenceScope": "app-scoped",
        "generatedAt": _utc_now(),
        "appId": app_id,
        "operations": 1,
        "passedOperations": 1,
        "failedOperations": 0,
        "browser": browser_result["browser"],
        "url": browser_result["url"],
        "package": {
            "root": record.package_root,
            "fingerprint": record.fingerprint,
            "fingerprintAlgorithm": record.fingerprint_algorithm,
        },
        "catalogFingerprint": catalog.fingerprint,
        "repositoryProvenance": provenance,
        "operationResult": browser_result["operationResult"],
        "observation": observation,
        "surfaceConformance": surface_conformance,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=repository_root())
    parser.add_argument("--app", required=True)
    parser.add_argument("--check", action="store_true", help="Run the current app-scoped observation gate.")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repo_root.resolve()
    app_id = args.app.strip()
    output_dir = args.output_dir or (DEFAULT_OUTPUT_ROOT / "apps" / _slug(app_id))
    if not output_dir.is_absolute():
        output_dir = repo / output_dir
    json_path = output_dir / "mcel-operation-observation-report.json"
    markdown_path = output_dir / "mcel-operation-observation-report.md"

    try:
        report = run_observation(repo=repo, app_id=app_id, headed=args.headed)
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    except ObservationRunnerError as exc:
        payload = {
            "schema": REPORT_SCHEMA,
            "runner": RUNNER_VERSION,
            "status": "fail",
            "ok": False,
            "evidenceScope": "app-scoped",
            "appId": app_id,
            "error": str(exc),
        }
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else f"{RUNNER_VERSION}\nstatus: fail\napp: {app_id}\nerror: {exc}\n")
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(RUNNER_VERSION)
        print("status: pass")
        print("evidence_scope: app-scoped")
        print(f"app: {app_id}")
        print("operations: 1")
        print("passed_operations: 1")
        print("failed_operations: 0")
        print("browser: playwright-chromium")
        print(f"json: {json_path.relative_to(repo).as_posix() if json_path.is_relative_to(repo) else json_path}")
        print(f"markdown: {markdown_path.relative_to(repo).as_posix() if markdown_path.is_relative_to(repo) else markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
