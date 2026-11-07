"""Read-only V1 four-app visual/source acceptance evidence.

Two deliberately distinct evidence modes:

* preview: generated HTML rendered directly in a local Playwright page; no
  backend, no network, and no claim of live hosted acceptance.
* hosted: read-only observation of the existing production and semantic routes
  on an explicitly supplied 127.0.0.1 server. Non-GET and generic off-origin
  requests are refused; Code Editor may GET pinned Monaco 0.52.2 assets from
  jsDelivr if its local loader is absent. No live effects or human approval follow.

Neither mode promotes an application, changes route registration, or certifies
RUNTIME_PASS, DESIGN_ACCEPTED, V1_APP_CERTIFIED, or V1_SUITE_CERTIFIED.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from uuid import uuid4
from pathlib import Path
from urllib.parse import urlparse
import re

from main_computer.mcel_calculator_expanded_host import compile_expanded, render_expanded_page
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser
from tools.mcel_semantic_reference_parity_frob import APPLICATIONS, run as source_audit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "runtime/reports/mcel-v1-four-app-hosted-acceptance"
WIDTHS = (320, 390, 560, 1100, 1272)
PRODUCTION_ROOT_IDS = {
    "file-manager": "file-explorer-app",
    "task-manager": "task-manager-app",
    "code-editor": "code-editor-app",
    "calculator": "calculator-app",
}
MONACO_CDN_HOST = "cdn.jsdelivr.net"
MONACO_CDN_PATH = "/npm/monaco-editor@0.52.2/min/vs/"
MONACO_LOCAL_LOADER_PATH = "/applications/vendor/monaco-editor/min/vs/loader.js"
MONACO_CDN_LOADER_PATH = MONACO_CDN_PATH + "loader.js"

APP_IDS = {
    "file-manager": "semantic-file-manager",
    "task-manager": "semantic-task-manager",
    "code-editor": "semantic-code-editor",
    "calculator": "semantic-calculator-expanded",
}


class AcceptanceInputError(ValueError):
    pass


def validate_loopback_base(url: str) -> str:
    """Never silently target a production/remote host or follow a URL path."""
    parsed = urlparse(url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password or not parsed.port
            or parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment):
        raise AcceptanceInputError("Host must be http://127.0.0.1:<port> with no path, query, credentials or fragment")
    return f"http://127.0.0.1:{parsed.port}"


def json_fingerprint(value: dict) -> str:
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def candidate_ir(app: str, repo: Path = ROOT) -> dict:
    return compile_expanded(repo) if app == "calculator" else compile_semantic_app(app, repo)


def candidate_html(app: str, repo: Path = ROOT) -> str:
    return render_expanded_page(repo) if app == "calculator" else render_semantic_page(app, repo)


def _survey(page, app: str, expected_fingerprint: str | None) -> dict:
    selector = "#mcel-expanded-diagnostic" if app == "calculator" else "#mcel-workbench-status"
    text = page.locator(selector).inner_text(timeout=6000) if page.locator(selector).count() else "MISSING_STATUS"
    snap = page.evaluate("""() => {
      const r = document.documentElement;
      const visible = el => {
        const rc=el.getBoundingClientRect(), style=getComputedStyle(el);
        return rc.width>0 && rc.height>0 && style.visibility!=='hidden' && style.display!=='none';
      };
      return {
        title: document.title, viewportWidth: innerWidth,
        scrollWidth:r.scrollWidth, scrollHeight:r.scrollHeight,
        regions:document.querySelectorAll('[data-mcel-region]').length,
        visibleButtons:[...document.querySelectorAll('button')].filter(visible).length,
        offscreenButtons:[...document.querySelectorAll('button')].filter(e => {
          if(!visible(e))return false;
          const rc=e.getBoundingClientRect();return rc.left < -1 || rc.right > innerWidth + 1;
        }).length,
        headingCount:document.querySelectorAll('h1,h2,h3').length,
        irText:document.querySelector('#mcel-expanded-ir,#mcel-workbench-ir')?.textContent || '',
        bodyHasContent: (document.body?.innerText||'').trim().length>20
      };
    }""")
    ir_text = snap.pop("irText")
    ir_ok = False
    observed_fingerprint = None
    observed_id = None
    if ir_text:
        try:
            observed_ir = json.loads(ir_text)
            observed_id = observed_ir.get("application", {}).get("appId")
            observed_fingerprint = json_fingerprint(observed_ir)
            ir_ok = observed_id == APP_IDS[app] and observed_fingerprint == expected_fingerprint
        except (ValueError, TypeError):
            pass
    return {
        "status_text": text,
        "ready": text == ("Semantic Calculator ready" if app == "calculator" else "Semantic candidate ready"),
        "candidate_id": observed_id,
        "source_fingerprint_matches_snapshot": ir_ok,
        "served_ir_sha256": observed_fingerprint,
        "horizontal_overflow": snap["scrollWidth"] > snap["viewportWidth"] + 1,
        "offscreen_buttons": snap["offscreenButtons"],
        "page_height_over_four_viewports": snap["scrollHeight"] > 4 * 900,
        **snap,
    }


def _is_pinned_monaco_asset(url: str) -> bool:
    """Allow only static Monaco 0.52.2 VS assets on the existing jsDelivr host.

    Never open a generic external origin or accept redirects to a new CDN.
    """
    u = urlparse(url)
    path = u.path
    return bool(
        u.scheme == "https" and u.hostname == MONACO_CDN_HOST
        and u.netloc == MONACO_CDN_HOST and not u.username and not u.password
        and not u.params and not u.query and not u.fragment
        and path.startswith(MONACO_CDN_PATH) and len(path) > len(MONACO_CDN_PATH)
        and "%" not in path and "\\" not in path
        and all(segment not in ("", ".", "..") for segment in path.split("/")[1:])
        and re.fullmatch(r"[A-Za-z0-9_./@-]+", path) is not None
        and path.endswith((".js", ".css", ".json", ".ttf", ".woff", ".woff2", ".svg", ".png"))
    )


def _route_guard(route, request, allowed_origin: str | None, blocked: list[dict],
                 *, allow_monaco_cdn: bool = False,
                 permitted_cdn: list[dict] | None = None) -> None:
    """Read-only loopback, plus the pinned Monaco fallback for Code Editor only."""
    u = urlparse(request.url)
    origin = f"{u.scheme}://{u.netloc}"
    method = request.method.upper()
    is_local_get = method == "GET" and allowed_origin is not None and origin == allowed_origin
    is_monaco_get = (method == "GET" and allow_monaco_cdn
                     and allowed_origin is not None and _is_pinned_monaco_asset(request.url))
    if is_local_get or is_monaco_get:
        if is_monaco_get and permitted_cdn is not None and len(permitted_cdn) < 100:
            permitted_cdn.append({"url": request.url[:300],
                                  "resource_type": getattr(request, "resource_type", "unknown")})
        route.continue_()
    else:
        blocked.append({"method": request.method, "url": request.url[:300],
                        "reason": "non-GET" if method != "GET" else "off-origin-or-preview"})
        route.abort()


def _monaco_mount_snapshot(page) -> dict:
    """Inspect the real Code Editor adapter, not merely a downloaded loader."""
    return page.evaluate("""() => {
        const runtime = window.MainComputerCodeEditorRuntime;
        const adapter = window.MainComputerMonacoAdapter;
        const debug = typeof runtime?.monacoDebug === 'function' ? runtime.monacoDebug() : null;
        const mounted = typeof adapter?.inspect === 'function' ? adapter.inspect() : null;
        const receipt = debug?.receipt || {};
        return {runtime_mounted: debug?.mounted === true,
                adapter_mounted: mounted?.mounted === true,
                mount_outcome: receipt.externalOutcome || null,
                mount_ok: receipt.ok === true,
                mount_source: receipt.source || null,
                mount_message: String(receipt.message || '').slice(0, 250)};
    }""")


def _monaco_pathway(snapshot: dict, loader_responses: list[dict]) -> str:
    """Only a real successful mount can qualify; local/CDN are both valid."""
    if not (snapshot.get("runtime_mounted") and snapshot.get("adapter_mounted")
            and snapshot.get("mount_ok") and snapshot.get("mount_outcome") == "model-mounted"):
        return "MONACO_UNAVAILABLE"
    source = snapshot.get("mount_source")
    if source == "local-vendor":
        return "LOCAL_MOUNTED"
    if source == "cdn":
        return "CDN_MOUNTED"
    if source == "window.monaco":
        for entry in reversed(loader_responses):
            if entry.get("status") == 200:
                if entry.get("source") == "cdn":
                    return "CDN_MOUNTED"
                if entry.get("source") == "local":
                    return "LOCAL_MOUNTED"
    return "MOUNT_SOURCE_UNVERIFIED"


def _deny_websocket(websocket, blocked: list[dict]) -> None:
    blocked.append({"method": "WEBSOCKET", "url": websocket.url[:300], "reason": "websocket-disabled"})
    websocket.close()


def _replace_checkpoint_with_retry(tmp: Path, target: Path) -> None:
    """Retry Windows replace races with a concurrent report/phase reader.

    The observer polls phase JSON while the child replaces it. Windows can
    deny replacement while the reader holds the destination open. This is a
    short-lived telemetry-file conflict, not a browser-capture failure.
    """
    for attempt in range(8):
        try:
            tmp.replace(target)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.02 * (attempt + 1))


def _checkpoint(report_dir: Path, result: dict) -> None:
    """Preserve completed observations even if a later page stalls or is interrupted."""
    target = report_dir / "report.json"
    tmp = report_dir / "report.json.tmp"
    tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _replace_checkpoint_with_retry(tmp, target)


class _CaptureTrace:
    """Durable, per-phase progress readable even after a killed browser worker."""

    def __init__(self, file: Path | None, app: str, width: int):
        self.file = file
        self.started = time.perf_counter()
        self.current_phase = "worker_start"
        self.phase_state = "RUNNING"
        self.phases: list[dict] = []
        self.app = app
        self.width = width
        self.checkpoint_warnings: list[str] = []
        self._save()

    def _save(self) -> None:
        if self.file is None:
            return
        try:
            payload = self.snapshot()
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(self.file.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _replace_checkpoint_with_retry(tmp, self.file)
        except OSError as error:
            # Phase polling is supplementary; the child returns its final
            # result and trace on stdout. Never turn a successful screenshot
            # into a false browser failure because Windows locked a JSON file.
            if len(self.checkpoint_warnings) < 10:
                self.checkpoint_warnings.append(
                    f"PHASE_CHECKPOINT_WRITE_FAILED: {type(error).__name__}: {error}")

    def snapshot(self) -> dict:
        return {"application": self.app, "width": self.width,
                "current_phase": self.current_phase, "phase_state": self.phase_state,
                "elapsed_ms": round((time.perf_counter() - self.started) * 1000),
                "completed_phases": list(self.phases),
                "checkpoint_warnings": list(self.checkpoint_warnings)}

    @contextmanager
    def phase(self, name: str):
        self.current_phase = name
        self.phase_state = "RUNNING"
        self._save()
        started = time.perf_counter()
        try:
            yield
        except BaseException as error:
            self.phases.append({"phase": name,
                                "duration_ms": round((time.perf_counter() - started) * 1000),
                                "result": "FAILED", "error_type": type(error).__name__})
            self.phase_state = "FAILED"
            self._save()
            raise
        else:
            self.phases.append({"phase": name,
                                "duration_ms": round((time.perf_counter() - started) * 1000),
                                "result": "PASS"})
            self.phase_state = "PASS"
            self._save()


def _load_capture_trace(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _production_worker_probe(base: str, app: str, width: int, report_dir: Path,
                             phase_file: Path | None = None,
                             screenshot_mode: str = "viewport") -> dict:
    """Child-process read-only capture; every potentially blocking phase is traced."""
    from playwright.sync_api import sync_playwright

    base = validate_loopback_base(base)
    if app not in APPLICATIONS or width not in WIDTHS:
        raise AcceptanceInputError("Invalid production capture target")
    if screenshot_mode not in ("viewport", "full"):
        raise AcceptanceInputError("Invalid production screenshot mode")
    trace = _CaptureTrace(phase_file, app, width)
    blocked: list[dict] = []
    page_errors: list[str] = []
    permitted_cdn: list[dict] = []
    monaco_loader_responses: list[dict] = []
    driver = browser = context = page = None
    outcome: dict = {"production_observation": "UNVERIFIED_ERROR"}
    try:
        with trace.phase("browser_launch"):
            driver = sync_playwright().start()
            browser, browser_source = launch_semantic_browser(driver)
        with trace.phase("browser_context"):
            context = browser.new_context(
                viewport={"width": width, "height": 900}, device_scale_factor=1,
                accept_downloads=False, service_workers="block",
            )
            context.route("**/*", lambda route, request: _route_guard(
                route, request, base, blocked, allow_monaco_cdn=(app == "code-editor"),
                permitted_cdn=permitted_cdn))
            context.route_web_socket("**/*", lambda websocket: _deny_websocket(websocket, blocked))
            page = context.new_page()
            page.set_default_timeout(6000)
            page.on("pageerror", lambda error: page_errors.append(str(error)[:400]))
            if app == "code-editor":
                def record_monaco_loader(response):
                    u = urlparse(response.url)
                    if u.path in (MONACO_LOCAL_LOADER_PATH, MONACO_CDN_LOADER_PATH):
                        source = ("cdn" if u.hostname == MONACO_CDN_HOST else "local")
                        if len(monaco_loader_responses) < 20:
                            monaco_loader_responses.append({"source": source,
                                                            "status": response.status,
                                                            "url": response.url[:300]})
                page.on("response", record_monaco_loader)
        with trace.phase("navigation"):
            route = APPLICATIONS[app]["production_route"]
            response = page.goto(base + route, wait_until="domcontentloaded", timeout=12000)
            outcome["production_http_status"] = response.status if response else None
        with trace.phase("dom_readiness"):
            # Do not traverse the entire 11 MB applications document with innerText.
            dom = page.evaluate("""() => ({ title: document.title,
                hasBody: !!document.body, state: document.readyState })""")
            outcome["production_title"] = dom["title"]
            outcome["production_dom_state"] = dom["state"]
        with trace.phase("application_initialization"):
            selector = "#" + PRODUCTION_ROOT_IDS[app]
            evidence = page.evaluate("""(selector) => {
                const root = document.querySelector(selector);
                if (!root) return { present: false, visible: false, hasContent: false };
                const style = getComputedStyle(root), bounds = root.getBoundingClientRect();
                return { present: true,
                    visible: style.display !== 'none' && style.visibility !== 'hidden' &&
                        bounds.width > 0 && bounds.height > 0,
                    hasContent: !!root.firstElementChild || (root.textContent || '').trim().length > 20 };
            }""", selector)
            outcome["production_app_root_id"] = PRODUCTION_ROOT_IDS[app]
            outcome["production_app_dom_present"] = evidence["present"]
            outcome["production_app_visible"] = evidence["visible"]
            outcome["production_body_nonempty"] = evidence["hasContent"]
        if app == "code-editor":
            with trace.phase("monaco_mount"):
                # The existing adapter attempts the local vendor first, then CDN.
                # Do not call mount manually: observe the actual user-facing boot.
                page.wait_for_function("""() => {
                    const runtime = window.MainComputerCodeEditorRuntime;
                    const adapter = window.MainComputerMonacoAdapter;
                    return runtime?.monacoDebug?.()?.mounted === true &&
                           adapter?.inspect?.()?.mounted === true;
                }""", timeout=18000)
                mount = _monaco_mount_snapshot(page)
                outcome["production_monaco_mount"] = mount
                outcome["production_monaco_pathway"] = _monaco_pathway(mount, monaco_loader_responses)
                if outcome["production_monaco_pathway"] not in ("LOCAL_MOUNTED", "CDN_MOUNTED"):
                    raise RuntimeError("Monaco mounted without a verified local/CDN loader source")
        with trace.phase("screenshot"):
            shot = f"{app}-production-{width}.png"
            page.screenshot(path=str(report_dir / shot),
                            full_page=(screenshot_mode == "full"),
                            animations="disabled", timeout=12000)
            outcome["production_screenshot"] = shot
            outcome["production_screenshot_mode"] = screenshot_mode
        outcome["production_page_errors"] = page_errors
        outcome["production_network_requests_blocked"] = blocked
        outcome["production_monaco_cdn_requests_allowed"] = permitted_cdn
        outcome["production_monaco_loader_responses"] = monaco_loader_responses
        outcome["production_observation"] = "OBSERVED" if (
            outcome["production_http_status"] == 200 and
            outcome["production_app_dom_present"] and
            outcome["production_app_visible"] and
            outcome["production_body_nonempty"] and
            (app != "code-editor" or outcome.get("production_monaco_pathway")
             in ("LOCAL_MOUNTED", "CDN_MOUNTED"))) else "UNVERIFIED_RENDER_STATE"
    except Exception as exc:
        outcome["production_error"] = ("PRODUCTION_CAPTURE_" + trace.current_phase.upper()
                                      + "_FAILED: " + str(exc)[:250])
        outcome["production_page_errors"] = page_errors
        outcome["production_network_requests_blocked"] = blocked
        outcome["production_monaco_cdn_requests_allowed"] = permitted_cdn
        outcome["production_monaco_loader_responses"] = monaco_loader_responses
        if app == "code-editor":
            outcome.setdefault("production_monaco_pathway", "MONACO_UNAVAILABLE")
            # Preserve a loader-error receipt on failure when the page remains responsive.
            try:
                outcome.setdefault("production_monaco_mount", _monaco_mount_snapshot(page))
            except Exception:
                pass
    finally:
        try:
            with trace.phase("browser_shutdown"):
                if page is not None:
                    page.close()
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()
                if driver is not None:
                    driver.stop()
        except Exception as exc:
            outcome["production_observation"] = "UNVERIFIED_ERROR"
            outcome["production_error"] = "PRODUCTION_CAPTURE_BROWSER_SHUTDOWN_FAILED: " + str(exc)[:250]
        outcome["production_capture_trace"] = trace.snapshot()
    return outcome


def _terminate_probe_tree(proc) -> None:
    """Stop only the worker and browser processes launched by this capture."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=6, check=False)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _bounded_production_probe(base: str, app: str, width: int, report_dir: Path,
                              repo: Path = ROOT, deadline_seconds: int = 40,
                              screenshot_mode: str = "viewport") -> dict:
    """Hard outer deadline with durable phase evidence on success and timeout."""
    base = validate_loopback_base(base)
    if app not in APPLICATIONS or width not in WIDTHS:
        raise AcceptanceInputError("Invalid production capture target")
    if screenshot_mode not in ("viewport", "full"):
        raise AcceptanceInputError("Invalid production screenshot mode")
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    phase_file = report_dir / f"production-capture-{app}-{width}-{uuid4().hex[:12]}.json"
    command = [
        sys.executable, "-m", "tools.mcel_v1_four_app_hosted_acceptance",
        "--_production-probe", "--host-url", base,
        "--app", app, "--width", str(width), "--report-dir", str(report_dir),
        "--_phase-file", str(phase_file), "--production-screenshot", screenshot_mode,
    ]
    popen_kwargs = {"cwd": str(repo), "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE, "text": True}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(command, **popen_kwargs)
    deadline = time.monotonic() + deadline_seconds
    last_announced_phase = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_probe_tree(proc)
            trace = _load_capture_trace(phase_file)
            phase = trace.get("current_phase", "worker_start")
            return {"production_error": f"PRODUCTION_CAPTURE_TIMEOUT_DURING_{phase.upper()}_AFTER_{deadline_seconds}s",
                    "production_observation": "UNVERIFIED_TIMEOUT",
                    "production_capture_trace": trace,
                    "production_phase_checkpoint": phase_file.name}
        try:
            stdout, stderr = proc.communicate(timeout=min(1.0, remaining))
            break
        except subprocess.TimeoutExpired:
            # Poll a small phase checkpoint instead of waiting silently for 40 s.
            trace = _load_capture_trace(phase_file)
            phase = trace.get("current_phase")
            if phase and phase != last_announced_phase:
                print(f"    production browser phase={phase}"
                      f" elapsed={trace.get('elapsed_ms', 0)}ms", flush=True)
                last_announced_phase = phase
    trace = _load_capture_trace(phase_file)
    if trace.get("completed_phases"):
        print("    production phase timings: " + ", ".join(
            f"{entry['phase']}={entry['duration_ms']}ms"
            for entry in trace["completed_phases"]), flush=True)
    if proc.returncode != 0:
        return {"production_error": f"PRODUCTION_BROWSER_WORKER_FAILED: {stderr[-350:]}",
                "production_observation": "UNVERIFIED_ERROR",
                "production_capture_trace": trace,
                "production_phase_checkpoint": phase_file.name}
    try:
        payload = json.loads(stdout.strip().splitlines()[-1])
        if not isinstance(payload, dict) or "production_http_status" not in payload:
            # Failure before HTTP response may still be a valid phase-specific result.
            if not isinstance(payload, dict) or "production_error" not in payload:
                raise ValueError("Malformed worker result")
        # The worker supplies the authoritative final phase trace. The JSON
        # file is a best-effort progress signal polled while the worker runs;
        # it can be stale if Windows temporarily locks the destination.
        payload["production_capture_trace"] = payload.get("production_capture_trace") or trace
        payload["production_phase_checkpoint"] = phase_file.name
        return payload
    except (ValueError, IndexError):
        return {"production_error": "PRODUCTION_BROWSER_INVALID_WORKER_RESULT",
                "production_observation": "UNVERIFIED_ERROR",
                "production_capture_trace": trace,
                "production_phase_checkpoint": phase_file.name}


def observe(mode: str, base_url: str | None = None, report_dir: Path = DEFAULT_REPORT,
            widths: tuple[int, ...] = WIDTHS, repo: Path = ROOT,
            apps: tuple[str, ...] | None = None,
            production_screenshot: str = "viewport") -> dict:
    if production_screenshot not in ("viewport", "full"):
        raise AcceptanceInputError("Unsupported production screenshot mode")
    if mode not in ("preview", "hosted"):
        raise AcceptanceInputError("Mode must be 'preview' or 'hosted'")
    base = validate_loopback_base(base_url) if mode == "hosted" and base_url else None
    if mode == "hosted" and not base:
        raise AcceptanceInputError("Hosted mode requires --host-url http://127.0.0.1:<port>")
    if mode == "preview" and base_url:
        raise AcceptanceInputError("Preview mode cannot use a live host")
    selected_apps = tuple(APPLICATIONS) if apps is None else tuple(apps)
    if not selected_apps or any(app not in APPLICATIONS for app in selected_apps):
        raise AcceptanceInputError("Apps must be chosen from " + ", ".join(APPLICATIONS))
    if not widths or any(width not in WIDTHS for width in widths):
        raise AcceptanceInputError("Widths must be chosen from " + ", ".join(map(str, WIDTHS)))
    if len(set(selected_apps)) != len(selected_apps) or len(set(widths)) != len(widths):
        raise AcceptanceInputError("Duplicate applications or widths are not allowed")
    from playwright.sync_api import sync_playwright

    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    print("MCEL acceptance: source audit starting", flush=True)
    audit = source_audit(repo)
    print("MCEL acceptance: source audit completed; launching browser", flush=True)
    full_scope = selected_apps == tuple(APPLICATIONS) and tuple(widths) == WIDTHS
    result = {
        "schema": "mcel.v1-four-app-observation.v1",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "provenance": ("local-loopback-host-read-only" if mode == "hosted" else "generated-html-offline-preview"),
        "host_url": base,
        "browser": None,
        "source_audit_pass": audit["source_audit_pass"],
        "source_audit_verdict": audit["verdict"],
        "widths": list(widths),
        "selected_applications": list(selected_apps),
        "scope": "full" if full_scope else "diagnostic_subset",
        "observations_completed": 0,
        "observations_expected": len(selected_apps) * len(widths),
        "state": "IN_PROGRESS",
        "applications": {},
        "promotion_authorized": False,
        "runtime_pass": "NOT_CERTIFIED",
        "human_design_approval": "REQUIRED_NOT_GIVEN",
        "v1_suite_certified": False,
        "limits": [
            "No live effect, filesystem write, process/server action, scheduling action or AI provider call is initiated",
            "Non-GET requests, WebSockets, service workers and external requests other than pinned Monaco 0.52.2 CDN fallback for Code Editor are blocked",
            "Runtime functionality requires separate authorized isolated-host acceptance",
            "Visual and geometric evidence is not human design approval",
            "Legacy-host route rendering alone does not prove feature-by-feature production parity",
        ],
    }
    _checkpoint(report_dir, result)
    with sync_playwright() as playwright:
        browser, source = launch_semantic_browser(playwright)
        print(f"MCEL acceptance: browser={source}", flush=True)
        result["browser"] = source
        _checkpoint(report_dir, result)
        try:
            production_timed_out_apps: set[str] = set()
            for app in selected_apps:
                spec = APPLICATIONS[app]
                ir = candidate_ir(app, repo)
                fp = json_fingerprint(ir)
                app_result = {
                    "production_route": spec["production_route"],
                    "semantic_route": spec["semantic_route"],
                    "expected_app_id": APP_IDS[app],
                    "local_ir_sha256": fp,
                    "source_status": "SOURCE_CONTRACT_PASS" if audit["applications"][app]["missing_source_contracts"] == [] else "SOURCE_CONTRACT_FAIL",
                    "viewports": [],
                    "hosted_functional_parity": "NOT_EXECUTED",
                    "design_acceptance": "HUMAN_REVIEW_REQUIRED",
                }
                if mode == "preview":
                    html = candidate_html(app, repo)
                for width in widths:
                    ordinal = result["observations_completed"] + 1
                    print(f"MCEL acceptance [{ordinal}/{result['observations_expected']}]: "
                          f"{app} width={width} starting", flush=True)
                    browser_context = browser.new_context(viewport={"width": width, "height": 900}, device_scale_factor=1,
                                                          accept_downloads=False, service_workers="block")
                    blocked = []
                    errors = []
                    browser_context.route("**/*", lambda route, request, b=blocked: _route_guard(
                        route, request, base, b, allow_monaco_cdn=(mode == "hosted" and app == "code-editor")))
                    browser_context.route_web_socket("**/*", lambda websocket, b=blocked: _deny_websocket(websocket, b))
                    page = browser_context.new_page()
                    page.on("pageerror", lambda exception, e=errors: e.append(str(exception)[:400]))
                    item = {"width": width, "network_requests_blocked": blocked, "page_errors": errors}
                    try:
                        if mode == "hosted":
                            url = base + spec["semantic_route"]
                            print(f"  semantic GET {url}", flush=True)
                            response = page.goto(url, wait_until="domcontentloaded", timeout=15000)
                            item["semantic_http_status"] = response.status if response else None
                        else:
                            if app == "calculator":
                                # Offline preview uses an opaque origin. Embedded
                                # Calculator Chat needs storage on mount; use a
                                # document-local map, never real profile storage.
                                # Hosted mode is deliberately untouched.
                                page.evaluate("""() => {const values=new Map();
                                    Object.defineProperty(window,'localStorage',{configurable:true,value:{
                                      getItem:k=>values.get(String(k))??null,
                                      setItem:(k,v)=>values.set(String(k),String(v)),
                                      removeItem:k=>values.delete(String(k)),
                                      clear:()=>values.clear(),key:i=>[...values.keys()][i]??null,
                                      get length(){return values.size;}
                                    }});
                                }""")
                            page.set_content(html, wait_until="domcontentloaded", timeout=15000)
                        page.wait_for_timeout(200)
                        item.update(_survey(page, app, fp))
                        shot = f"{app}-semantic-{width}.png"
                        print(f"  semantic screenshot {shot}", flush=True)
                        page.screenshot(path=str(report_dir / shot), full_page=True,
                                        animations="disabled", timeout=15000)
                        item["semantic_screenshot"] = shot
                    except Exception as exc:
                        item["observation_error"] = str(exc)[:350]
                    finally:
                        page.close()
                        browser_context.close()
                    if mode == "hosted" and not item.get("observation_error"):
                        if app in production_timed_out_apps:
                            print("  production capture skipped: prior width exceeded hard deadline", flush=True)
                            item.update({"production_observation": "UNVERIFIED_SKIPPED_AFTER_TIMEOUT",
                                         "production_error": "Earlier production capture exceeded hard deadline"})
                        else:
                            print(f"  production GET {base + spec['production_route']}"
                                  " (isolated, 40s hard deadline)", flush=True)
                            observation = _bounded_production_probe(
                                base, app, width, report_dir, repo,
                                screenshot_mode=production_screenshot)
                            item.update(observation)
                            trace = observation.get("production_capture_trace", {})
                            if trace:
                                print(f"  production last phase={trace.get('current_phase')} "
                                      f"state={trace.get('phase_state')} "
                                      f"elapsed={trace.get('elapsed_ms')}ms", flush=True)
                            if observation.get("production_observation") == "UNVERIFIED_TIMEOUT":
                                production_timed_out_apps.add(app)
                    app_result["viewports"].append(item)
                    result["applications"][app] = app_result
                    result["observations_completed"] += 1
                    _checkpoint(report_dir, result)
                    print(f"MCEL acceptance [{ordinal}/{result['observations_expected']}]: "
                          f"{app} width={width} completed "
                          f"semantic={item.get('semantic_http_status', 'preview')} "
                          f"production={item.get('production_http_status', 'unverified')} "
                          f"error={item.get('observation_error') or item.get('production_error') or 'none'}",
                          flush=True)
                app_result["candidate_render_observation_pass"] = all(
                    v.get("ready") and v.get("source_fingerprint_matches_snapshot") and
                    not v.get("horizontal_overflow") and not v.get("page_errors") and
                    not v.get("observation_error") for v in app_result["viewports"])
                app_result["production_route_observed"] = (
                    all(v.get("production_observation") == "OBSERVED" and
                        v.get("production_http_status") == 200 and v.get("production_app_dom_present")
                        and v.get("production_app_visible") and v.get("production_screenshot")
                        for v in app_result["viewports"]) if mode == "hosted" else False)
                result["applications"][app] = app_result
        finally:
            browser.close()
    result["rendered_viewports_passed"] = sum(
        int(v.get("ready") and v.get("source_fingerprint_matches_snapshot") and
            not v.get("horizontal_overflow") and not v.get("page_errors") and not v.get("observation_error"))
        for app in result["applications"].values() for v in app["viewports"])
    result["total_viewports"] = len(selected_apps) * len(widths)
    result["state"] = "COMPLETE"
    result["verdict"] = (
        "HOSTED_OBSERVATION_RECORDED_NOT_ACCEPTED" if mode == "hosted"
        else "OFFLINE_PREVIEW_RECORDED_NOT_HOSTED")
    if not full_scope and (mode == "hosted" or selected_apps != tuple(APPLICATIONS)):
        result["verdict"] = "DIAGNOSTIC_SUBSET_RECORDED_NOT_ACCEPTED"
    _checkpoint(report_dir, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("preview", "hosted"), default="preview")
    parser.add_argument("--host-url", help="Required only for --mode hosted, e.g. http://127.0.0.1:8765")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--app", action="append", choices=tuple(APPLICATIONS),
                        help="Optional diagnostic subset (repeat to select several apps)")
    parser.add_argument("--width", action="append", type=int, choices=WIDTHS,
                        help="Optional diagnostic viewport subset (repeat for more widths)")
    parser.add_argument("--production-screenshot", choices=("viewport", "full"), default="viewport",
                        help="Production page capture: viewport (default) or full page")
    parser.add_argument("--_production-probe", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_phase-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._production_probe:
        try:
            if not args.host_url or not args.app or len(args.app) != 1 or not args.width or len(args.width) != 1:
                raise AcceptanceInputError("Worker requires one app and viewport and the local host")
            payload = _production_worker_probe(args.host_url, args.app[0], args.width[0],
                                               args.report_dir, args._phase_file,
                                               screenshot_mode=args.production_screenshot)
            print(json.dumps(payload, ensure_ascii=False), flush=True)
            return
        except Exception as exc:
            print(f"Production browser capture failed: {exc}", file=sys.stderr, flush=True)
            raise SystemExit(2)
    try:
        result = observe(args.mode, args.host_url, args.report_dir,
                         widths=tuple(args.width) if args.width else WIDTHS,
                         apps=tuple(args.app) if args.app else None,
                         production_screenshot=args.production_screenshot)
    except AcceptanceInputError as exc:
        parser.error(str(exc))
    print("MCEL_V1_FOUR_APP", result["verdict"])
    print("  observed renders:", f'{result["rendered_viewports_passed"]}/{result["total_viewports"]}')
    for name, app in result["applications"].items():
        print(" ", name, "candidate:", "PASS_BOUNDED" if app["candidate_render_observation_pass"] else "INCOMPLETE",
              "production:", "OBSERVED" if app["production_route_observed"] else "UNVERIFIED")
    print("  hosted runtime effects: NOT_CERTIFIED; human design: NOT_APPROVED; promotion: BLOCKED")
    print("  report:", args.report_dir / "report.json")
    all_routes_observed = all(app["production_route_observed"] for app in result["applications"].values())
    complete = (result["source_audit_pass"] and
                result["rendered_viewports_passed"] == result["total_viewports"] and
                (args.mode != "hosted" or all_routes_observed))
    raise SystemExit(0 if complete else 1)


if __name__ == "__main__":
    main()
