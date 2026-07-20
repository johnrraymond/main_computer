#!/usr/bin/env python3
"""FLOG runtime MCEL app contract smoke.

This is the small active counterpart to the passive MCEL diagnostics widget.
It opens contracted MCEL apps in a real browser, waits past the startup grace
window, uses the same diagnostics widget payload the user sees, keeps the raw
``window.MCEL.diagnose(appId)`` report as evidence, and classifies the normalized
widget contract payload.

The script deliberately builds on the existing FLOG convention in this repo:
it writes reproducible JSON/Markdown evidence under ``runtime/reports/flog``
and treats the browser result as proof material, not as a CSS fixer.

Run from the repository root after the viewport is running:

    python main_computer/flog_mcel_runtime_smoke.py --base-url http://127.0.0.1:8765

Useful options:

    python main_computer/flog_mcel_runtime_smoke.py --app code-editor
    python main_computer/flog_mcel_runtime_smoke.py --scenario mcel-lab.default-load --headed
    python main_computer/flog_mcel_runtime_smoke.py --emit-events
    python main_computer/flog_mcel_runtime_smoke.py --viewport 1920x1200
    python main_computer/flog_mcel_runtime_smoke.py --json

FLOG v1 stays intentionally small.  It proves the desktop green contract baseline:
page loads, the diagnosis API and widget payload are available, primary surface
is usable, active warnings and errors are zero, visual-integrity violations are
absent, and verdict/counts agree.  The default viewport is an explicit desktop baseline; responsive viewport scenarios can be added separately as each app contract becomes more detailed.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin


REPORT_KIND = "mcel.flog.runtime-contracts.report"
REPORT_SCHEMA = "mcel-runtime-flog-report-v1"
REPORT_VERSION = "mcel-runtime-flog-v1"
DEFAULT_OUTPUT_DIR = Path("runtime/reports/flog/mcel-runtime")
DEFAULT_STARTUP_WAIT_MS = 6500
DEFAULT_TIMEOUT_MS = 15000
DEFAULT_VIEWPORT = {"width": 1920, "height": 1200}

ROUTE_OVERRIDES = {
    "calculator": "/applications/calculator",
    "file-explorer": "/applications/file-explorer",
    "git-tools": "/applications/git-tools",
    "code-editor": "/applications/code-editor",
    "website-builder": "/applications/website-builder/hub-site",
    "mcel-lab": "/applications/mcel-lab",
}

SCENARIO_INTENTS = {
    "calculator": "Verify the calculator workspace opens with a usable primary calculation surface.",
    "file-explorer": "Verify the file explorer opens with a usable navigation/listing surface.",
    "git-tools": "Verify Git Tools opens with a usable repository/status work surface.",
    "code-editor": "Verify the Code Editor authoring contract exposes one usable selected-source editor.",
    "website-builder": "Verify Website Builder exposes a usable preview/design surface for the selected site.",
    "mcel-lab": "Verify MCEL Lab opens with a usable blueprint inspection workspace.",
}


@dataclass(frozen=True)
class RuntimeScenario:
    id: str
    app: str
    route: str
    intent: str
    startup_wait_ms: int = DEFAULT_STARTUP_WAIT_MS

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "app": self.app,
            "route": self.route,
            "intent": self.intent,
            "startupWaitMs": self.startup_wait_ms,
        }


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def normalize_base_url(base_url: str) -> str:
    value = (base_url or "").strip()
    if not value:
        raise ValueError("base URL is required")
    if not value.startswith(("http://", "https://")):
        value = "http://" + value
    return value.rstrip("/") + "/"



def parse_viewport(value: str | None) -> dict[str, int]:
    """Parse a viewport string like ``1920x1200`` into a Playwright viewport."""

    raw = (value or "").strip().lower()
    if not raw:
        return dict(DEFAULT_VIEWPORT)
    if raw in {"desktop", "desktop-large", "default"}:
        return dict(DEFAULT_VIEWPORT)
    parts = raw.replace("×", "x").split("x", 1)
    if len(parts) != 2:
        raise ValueError("Viewport must be WIDTHxHEIGHT, for example 1920x1200")
    try:
        width = int(parts[0])
        height = int(parts[1])
    except ValueError as exc:
        raise ValueError("Viewport width and height must be integers") from exc
    if width < 320 or height < 240:
        raise ValueError("Viewport is too small for the MCEL desktop contract baseline")
    return {"width": width, "height": height}


def viewport_label(viewport: dict[str, int]) -> str:
    return f"{int(viewport.get('width') or 0)}x{int(viewport.get('height') or 0)}"


def app_route(app: str) -> str:
    return ROUTE_OVERRIDES.get(app, f"/applications/{app}")


def scenario_for_app(app: str, *, startup_wait_ms: int = DEFAULT_STARTUP_WAIT_MS) -> RuntimeScenario:
    return RuntimeScenario(
        id=f"{app}.default-load",
        app=app,
        route=app_route(app),
        intent=SCENARIO_INTENTS.get(app, f"Verify {app} opens and satisfies its MCEL runtime contract."),
        startup_wait_ms=startup_wait_ms,
    )


def _load_registry_apps(repo: Path) -> list[str]:
    """Return parsed MCEL app contracts when the requirements registry is available."""

    try:
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from tools.mcel_requirements_registry import build_registry  # type: ignore
    except Exception:
        return sorted(ROUTE_OVERRIDES)

    try:
        registry = build_registry(repo, strict_schema=True)
    except TypeError:
        try:
            registry = build_registry(repo, strict_schema=False)
        except Exception:
            return sorted(ROUTE_OVERRIDES)
    except Exception:
        return sorted(ROUTE_OVERRIDES)

    apps = [
        block.block_id
        for block in registry.blocks
        if block.block_type == "mcel-app" and block.block_id
    ]
    return sorted(apps) if apps else sorted(ROUTE_OVERRIDES)


def build_scenarios(
    repo: Path | None = None,
    *,
    apps: list[str] | None = None,
    scenario_ids: list[str] | None = None,
    startup_wait_ms: int = DEFAULT_STARTUP_WAIT_MS,
) -> list[RuntimeScenario]:
    repo = repo or repo_root_from_script()
    app_names = sorted(dict.fromkeys(apps or _load_registry_apps(repo)))
    scenarios = [scenario_for_app(app, startup_wait_ms=startup_wait_ms) for app in app_names]
    if scenario_ids:
        wanted = set(scenario_ids)
        scenarios = [scenario for scenario in scenarios if scenario.id in wanted]
        missing = sorted(wanted - {scenario.id for scenario in scenarios})
        if missing:
            raise ValueError(
                "Unknown scenario(s): "
                + ", ".join(missing)
                + ". Available: "
                + ", ".join(scenario.id for scenario in build_scenarios(repo, startup_wait_ms=startup_wait_ms))
            )
    return scenarios


def _summary_counts(diagnosis: dict[str, Any]) -> dict[str, int]:
    """Return critical/warning/info counts from a raw diagnosis or widget payload."""

    counts = {"critical": 0, "warning": 0, "info": 0}
    if not isinstance(diagnosis, dict):
        return counts

    # The diagnostics widget emits normalized user-facing counts.  FLOG should
    # trust those when a widget payload is available, because that is the same
    # truth surface the user sees and the backend event log stores.
    widget_counts = diagnosis.get("counts")
    if isinstance(widget_counts, dict):
        counts["critical"] = max(0, int(widget_counts.get("errors") or 0))
        counts["warning"] = max(0, int(widget_counts.get("warnings") or 0))
        counts["info"] = max(0, int(widget_counts.get("ok") or 0))
        return counts

    summary = diagnosis.get("summary")
    findings = diagnosis.get("findings")
    if isinstance(summary, dict):
        for key in counts:
            value = summary.get(key)
            if isinstance(value, (int, float)):
                counts[key] = max(0, int(value))

    if not any(counts.values()) and isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            severity = str(finding.get("severity") or finding.get("normalizedSeverity") or "").lower()
            if severity in {"critical", "error"}:
                counts["critical"] += 1
            elif severity == "warning":
                counts["warning"] += 1
            elif severity:
                counts["info"] += 1
    return counts


def _primary_surface_summary(diagnosis: dict[str, Any]) -> dict[str, Any]:
    """Extract primary-surface evidence from widget payloads and raw reports."""

    primary: Any = {}
    if isinstance(diagnosis, dict):
        primary = diagnosis.get("primarySurface")
        if not isinstance(primary, dict):
            summary = diagnosis.get("summary")
            if isinstance(summary, dict):
                primary = summary.get("primarySurface")
        if not isinstance(primary, dict):
            measurements = diagnosis.get("measurements")
            if isinstance(measurements, dict):
                surfaces = measurements.get("surfaces")
                if isinstance(surfaces, dict):
                    host = surfaces.get("primaryHost") or surfaces.get("monacoHost")
                    editor = surfaces.get("primaryEditor") or surfaces.get("monacoEditor") or host
                    primary = {
                        "expected": "",
                        "usable": bool(_is_useful_box(host) and _is_visible_box(host)),
                        "exactlyOneAuthoritativeSurface": bool(editor),
                        "host": host or {},
                        "editor": editor or {},
                    }
    if not isinstance(primary, dict):
        primary = {}
    return {
        "expected": primary.get("expected") or primary.get("id") or "",
        "usable": bool(primary.get("usable")),
        "exactlyOneAuthoritativeSurface": bool(primary.get("exactlyOneAuthoritativeSurface")),
        "hostExists": bool((primary.get("host") or {}).get("exists")) if isinstance(primary.get("host"), dict) else bool(primary.get("hostExists")),
        "hostVisible": bool((primary.get("host") or {}).get("visible")) if isinstance(primary.get("host"), dict) else bool(primary.get("hostVisible")),
    }


def _is_visible_box(box: Any) -> bool:
    if not isinstance(box, dict):
        return False
    return bool(box.get("exists") and box.get("visible"))


def _is_useful_box(box: Any) -> bool:
    if not _is_visible_box(box):
        return False
    return float(box.get("width") or 0) > 0 and float(box.get("height") or 0) > 0


def _visual_integrity_violations(diagnosis: dict[str, Any]) -> list[Any]:
    measurements = diagnosis.get("measurements") if isinstance(diagnosis, dict) else {}
    if not isinstance(measurements, dict):
        return []
    violations = measurements.get("visualIntegrityViolations")
    return violations if isinstance(violations, list) else []


def classify_diagnosis(
    diagnosis: dict[str, Any],
    *,
    require_zero_warnings: bool = True,
) -> dict[str, Any]:
    counts = _summary_counts(diagnosis)
    primary = _primary_surface_summary(diagnosis)
    visual_integrity = _visual_integrity_violations(diagnosis)
    verdict = str(diagnosis.get("verdict") or "unknown") if isinstance(diagnosis, dict) else "unknown"

    failures: list[str] = []
    warnings: list[str] = []

    if counts["critical"] > 0:
        failures.append(f"{counts['critical']} critical MCEL finding(s) are active")
    if require_zero_warnings and counts["warning"] > 0:
        failures.append(f"{counts['warning']} warning MCEL finding(s) are active")
    elif counts["warning"] > 0:
        warnings.append(f"{counts['warning']} warning MCEL finding(s) are active")

    if not primary["usable"]:
        failures.append("primary surface is not usable")
    if not primary["exactlyOneAuthoritativeSurface"]:
        failures.append("expected exactly one authoritative primary surface")
    if visual_integrity:
        failures.append(f"{len(visual_integrity)} visual-integrity violation(s) are active")

    normalized_verdict = "pass" if not failures else "fail"
    if verdict == "pass" and failures:
        failures.append("diagnosis verdict says pass but classified issues require failure")
    elif verdict == "fail" and not failures:
        warnings.append("diagnosis verdict says fail even though normalized counts are clean")

    return {
        "status": normalized_verdict,
        "verdict": verdict,
        "counts": {
            "errors": counts["critical"],
            "warnings": counts["warning"],
            "infos": counts["info"],
        },
        "primarySurface": primary,
        "visualIntegrityViolationCount": len(visual_integrity),
        "failures": failures,
        "warnings": warnings,
    }


def _evidence_payload_for_trial(trial: dict[str, Any]) -> dict[str, Any]:
    widget = trial.get("widgetPayload") if isinstance(trial.get("widgetPayload"), dict) else {}
    diagnosis = trial.get("diagnosis") if isinstance(trial.get("diagnosis"), dict) else {}
    return widget or diagnosis


def trial_result_summary(trial: dict[str, Any], *, evidence_limit: int = 5) -> dict[str, Any]:
    classification = trial.get("classification") if isinstance(trial.get("classification"), dict) else {}
    evidence = _evidence_payload_for_trial(trial)
    measurements = evidence.get("measurements") if isinstance(evidence.get("measurements"), dict) else {}
    issues = evidence.get("issues")
    if not isinstance(issues, list):
        current = evidence.get("current") if isinstance(evidence.get("current"), dict) else {}
        issues = current.get("issues") if isinstance(current.get("issues"), list) else evidence.get("findings")
    if not isinstance(issues, list):
        issues = []

    return {
        "scenarioId": trial.get("scenarioId") or trial.get("id") or "",
        "app": trial.get("app") or "",
        "route": trial.get("route") or "",
        "url": trial.get("url") or "",
        "status": classification.get("status") or "unknown",
        "counts": classification.get("counts") or {},
        "failures": classification.get("failures") or [],
        "warnings": classification.get("warnings") or [],
        "primarySurface": classification.get("primarySurface") or {},
        "issueEvidence": issues[:evidence_limit],
        "visualIntegrityViolations": (measurements.get("visualIntegrityViolations") or [])[:evidence_limit],
        "layoutCollisions": (measurements.get("layoutCollisions") or [])[:evidence_limit],
        "contentFitViolations": (measurements.get("contentFitViolations") or [])[:evidence_limit],
    }


def compact_diagnosis(diagnosis: dict[str, Any]) -> dict[str, Any]:
    findings = diagnosis.get("findings") if isinstance(diagnosis, dict) else []
    if not isinstance(findings, list):
        findings = []
    measurements = diagnosis.get("measurements") if isinstance(diagnosis, dict) else {}
    if not isinstance(measurements, dict):
        measurements = {}
    summary = diagnosis.get("summary") if isinstance(diagnosis, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    return {
        "schema": diagnosis.get("schema") or diagnosis.get("REPORT_SCHEMA") or "",
        "appId": diagnosis.get("appId") or "",
        "contractId": diagnosis.get("contractId") or "",
        "mode": diagnosis.get("mode") or "",
        "verdict": diagnosis.get("verdict") or "unknown",
        "summary": summary,
        "primarySurface": diagnosis.get("primarySurface") or summary.get("primarySurface") or {},
        "findings": findings[:25],
        "measurements": {
            "visualIntegrityViolations": measurements.get("visualIntegrityViolations") or [],
            "layoutCollisions": measurements.get("layoutCollisions") or [],
            "contentFitViolations": measurements.get("contentFitViolations") or [],
        },
    }


def compact_widget_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the same compact evidence shape as copied/logged widget payloads."""

    if not isinstance(payload, dict):
        return {}
    measurements = payload.get("measurements")
    if not isinstance(measurements, dict):
        measurements = {}
    current = payload.get("current")
    if not isinstance(current, dict):
        current = {}
    issues = payload.get("issues")
    if not isinstance(issues, list):
        issues = current.get("issues") if isinstance(current.get("issues"), list) else []
    return {
        "schema": payload.get("schema") or "mcel-diagnostics-counter-copy-v4",
        "widgetVersion": payload.get("widgetVersion") or "",
        "appId": payload.get("appId") or "",
        "contractId": payload.get("contractId") or "",
        "route": payload.get("route") or "",
        "timestamp": payload.get("timestamp") or "",
        "verdict": payload.get("verdict") or "unknown",
        "rawVerdict": payload.get("rawVerdict") or "unknown",
        "counts": payload.get("counts") or {},
        "current": {
            "counts": current.get("counts") or payload.get("counts") or {},
            "issues": issues[:25],
        },
        "primarySurface": payload.get("primarySurface") or {},
        "measurements": {
            "visualIntegrityViolations": measurements.get("visualIntegrityViolations") or [],
            "layoutCollisions": measurements.get("layoutCollisions") or [],
            "contentFitViolations": measurements.get("contentFitViolations") or [],
        },
        "issues": issues[:25],
    }


def diagnostic_event_from_trial(trial: dict[str, Any]) -> dict[str, Any]:
    widget_payload = trial.get("widgetPayload") if isinstance(trial.get("widgetPayload"), dict) else {}
    diagnosis = trial.get("diagnosis") if isinstance(trial.get("diagnosis"), dict) else {}
    evidence = widget_payload or diagnosis
    classification = trial.get("classification") if isinstance(trial.get("classification"), dict) else {}
    counts = classification.get("counts") if isinstance(classification.get("counts"), dict) else {}
    issues = evidence.get("issues") or evidence.get("findings") or []
    if not isinstance(issues, list):
        issues = []
    event = {
        "schema": "mcel-diagnostic-event-v1",
        "source": "mcel-runtime-flog",
        "flogVersion": REPORT_VERSION,
        "scenarioId": trial.get("scenarioId"),
        "appId": trial.get("app"),
        "contractId": evidence.get("contractId") or diagnosis.get("contractId") or "",
        "route": trial.get("route"),
        "timestamp": trial.get("finishedAt") or evidence.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        "verdict": classification.get("status") or evidence.get("verdict") or "unknown",
        "rawVerdict": evidence.get("rawVerdict") or diagnosis.get("verdict") or "unknown",
        "counts": {
            "errors": int(counts.get("errors") or 0),
            "warnings": int(counts.get("warnings") or 0),
            "ok": int(counts.get("infos") or 0),
        },
        "issues": issues[:25],
        "primarySurface": classification.get("primarySurface") or evidence.get("primarySurface") or {},
        "measurements": (evidence.get("measurements") or {}),
    }
    return event


def _absolute_url(base_url: str, route: str) -> str:
    return urljoin(normalize_base_url(base_url), route.lstrip("/"))


def run_browser_scenarios(
    scenarios: list[RuntimeScenario],
    *,
    base_url: str,
    headed: bool = False,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    emit_events: bool = False,
    require_zero_warnings: bool = True,
    viewport: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - depends on optional runtime setup.
        raise RuntimeError(
            "Playwright is required for runtime FLOG. Install it with "
            "`python -m pip install playwright` and `python -m playwright install chromium`."
        ) from exc

    viewport = viewport or dict(DEFAULT_VIEWPORT)
    trials: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        context = browser.new_context(viewport=viewport)
        try:
            for scenario in scenarios:
                started = datetime.now(timezone.utc).isoformat()
                page = context.new_page()
                route_url = _absolute_url(base_url, scenario.route)
                trial: dict[str, Any] = {
                    "scenarioId": scenario.id,
                    "app": scenario.app,
                    "route": scenario.route,
                    "url": route_url,
                    "intent": scenario.intent,
                    "startedAt": started,
                    "viewport": dict(viewport),
                }
                try:
                    page.goto(route_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_function(
                        "() => window.MCEL && typeof window.MCEL.diagnose === 'function'",
                        timeout=timeout_ms,
                    )
                    page.wait_for_function(
                        "() => window.MCELDiagnosticsCounterWidget && "
                        "typeof window.MCELDiagnosticsCounterWidget.refresh === 'function'",
                        timeout=timeout_ms,
                    )
                    if scenario.startup_wait_ms > 0:
                        page.wait_for_timeout(scenario.startup_wait_ms)
                    result = page.evaluate(
                        """(appId) => {
                          const widgetApi = window.MCELDiagnosticsCounterWidget || null;
                          const raw = window.MCEL.diagnose(appId, {silent: true});
                          let payload = null;
                          let status = null;
                          if (widgetApi && typeof widgetApi.refresh === "function") {
                            status = widgetApi.refresh(appId);
                            const priv = widgetApi._private || {};
                            if (status && typeof priv.compactPayload === "function") {
                              payload = priv.compactPayload(status.report, status.counts, status.history);
                            }
                          }
                          return JSON.parse(JSON.stringify({
                            diagnosis: raw || {},
                            widgetPayload: payload || null,
                            widgetStatusAvailable: Boolean(status),
                            widgetPayloadAvailable: Boolean(payload)
                          }));
                        }""",
                        scenario.app,
                    )
                    raw_diagnosis = result.get("diagnosis") if isinstance(result, dict) and isinstance(result.get("diagnosis"), dict) else {}
                    widget_payload = result.get("widgetPayload") if isinstance(result, dict) and isinstance(result.get("widgetPayload"), dict) else {}

                    trial["diagnosis"] = compact_diagnosis(raw_diagnosis)
                    trial["widgetPayload"] = compact_widget_payload(widget_payload) if widget_payload else {}
                    trial["widgetStatusAvailable"] = bool(result.get("widgetStatusAvailable")) if isinstance(result, dict) else False
                    trial["widgetPayloadAvailable"] = bool(result.get("widgetPayloadAvailable")) if isinstance(result, dict) else False

                    classification_source = trial["widgetPayload"] or trial["diagnosis"]
                    trial["classification"] = classify_diagnosis(
                        classification_source,
                        require_zero_warnings=require_zero_warnings,
                    )
                    if not trial["widgetPayloadAvailable"]:
                        trial["classification"].setdefault("warnings", []).append(
                            "diagnostics widget payload was unavailable; classified raw MCEL diagnosis fallback"
                        )
                    if emit_events:
                        event = diagnostic_event_from_trial({**trial, "finishedAt": datetime.now(timezone.utc).isoformat()})
                        event_result = page.evaluate(
                            """async (event) => {
                              const response = await fetch("/api/mcel/diagnostics/events", {
                                method: "POST",
                                headers: {"Content-Type": "application/json"},
                                body: JSON.stringify(event)
                              });
                              return {ok: response.ok, status: response.status, text: await response.text()};
                            }""",
                            event,
                        )
                        trial["eventEmission"] = event_result
                except Exception as exc:
                    trial["diagnosis"] = {}
                    trial["classification"] = {
                        "status": "fail",
                        "verdict": "runtime-error",
                        "counts": {"errors": 1, "warnings": 0, "infos": 0},
                        "primarySurface": {},
                        "visualIntegrityViolationCount": 0,
                        "failures": [f"{type(exc).__name__}: {exc}"],
                        "warnings": [],
                    }
                finally:
                    trial["finishedAt"] = datetime.now(timezone.utc).isoformat()
                    page.close()
                    trials.append(trial)
        finally:
            context.close()
            browser.close()
    return trials


def summarize_trials(trials: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    apps: dict[str, dict[str, Any]] = {}
    for trial in trials:
        classification = trial.get("classification") or {}
        status = str(classification.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        app = str(trial.get("app") or "")
        app_item = apps.setdefault(app, {"trials": 0, "failures": 0, "warnings": 0})
        app_item["trials"] += 1
        if status == "fail":
            app_item["failures"] += 1
        app_item["warnings"] += len(classification.get("warnings") or [])

    return {
        "status": "pass" if status_counts.get("fail", 0) == 0 else "fail",
        "scenarioCount": len(trials),
        "statusCounts": dict(sorted(status_counts.items())),
        "apps": dict(sorted(apps.items())),
    }


def build_report(
    *,
    repo: Path,
    base_url: str,
    scenarios: list[RuntimeScenario],
    trials: list[dict[str, Any]],
    viewport: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "kind": REPORT_KIND,
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "repoRoot": str(repo),
        "baseUrl": normalize_base_url(base_url).rstrip("/"),
        "viewport": viewport or dict(DEFAULT_VIEWPORT),
        "source": {
            "scenarioSource": "requirements-registry-app-contracts-with-route-overrides",
            "diagnosisSource": "window.MCELDiagnosticsCounterWidget.refresh with window.MCEL.diagnose fallback",
            "eventSource": "mcel-diagnostic-event-v1",
        },
        "scenarios": [scenario.to_dict() for scenario in scenarios],
        "summary": summarize_trials(trials),
        "results": [trial_result_summary(trial) for trial in trials],
        "trials": trials,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# MCEL Runtime FLOG Report",
        "",
        f"- Schema: `{report.get('schema', '')}`",
        f"- Version: `{report.get('version', '')}`",
        f"- Generated: `{report.get('generatedAt', '')}`",
        f"- Base URL: `{report.get('baseUrl', '')}`",
        f"- Viewport: `{viewport_label(report.get('viewport') or DEFAULT_VIEWPORT)}`",
        f"- Status: **{summary.get('status', 'unknown')}**",
        f"- Scenarios: {summary.get('scenarioCount', 0)}",
        "",
        "## Scenario results",
        "",
        "| Scenario | App | Status | Errors | Warnings | Primary usable | Notes |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for trial in report.get("trials") or []:
        classification = trial.get("classification") or {}
        counts = classification.get("counts") or {}
        primary = classification.get("primarySurface") or {}
        notes = "; ".join((classification.get("failures") or []) + (classification.get("warnings") or []))
        lines.append(
            "| {scenario} | {app} | {status} | {errors} | {warnings} | {primary} | {notes} |".format(
                scenario=trial.get("scenarioId", ""),
                app=trial.get("app", ""),
                status=classification.get("status", "unknown"),
                errors=counts.get("errors", 0),
                warnings=counts.get("warnings", 0),
                primary="yes" if primary.get("usable") else "no",
                notes=notes.replace("|", "\\|"),
            )
        )

    failed_results = [result for result in report.get("results") or [] if result.get("status") == "fail"]
    if failed_results:
        lines.extend(["", "## Failed scenario evidence", ""])
        for result in failed_results:
            lines.append(f"### {result.get('scenarioId', '')}")
            for reason in result.get("failures") or []:
                lines.append(f"- Failure: {reason}")
            for issue in result.get("issueEvidence") or []:
                code = issue.get("code", "") if isinstance(issue, dict) else ""
                finding = issue.get("finding", issue) if isinstance(issue, dict) else issue
                finding_text = str(finding).replace("|", "\\|")
                lines.append(f"- Issue: `{code}` {finding_text}")
            visual = result.get("visualIntegrityViolations") or []
            if visual:
                lines.append(f"- Visual integrity evidence: {len(visual)} sampled violation(s)")
                for item in visual[:3]:
                    if not isinstance(item, dict):
                        continue
                    owner = item.get("owner") or {}
                    selector = owner.get("selector") if isinstance(owner, dict) else ""
                    lines.append(f"  - `{item.get('type', '')}` owner `{selector}`")
            collisions = result.get("layoutCollisions") or []
            if collisions:
                lines.append(f"- Layout collision evidence: {len(collisions)} sampled collision(s)")
                for item in collisions[:3]:
                    if not isinstance(item, dict):
                        continue
                    owner = item.get("owner") or {}
                    selector = owner.get("selector") if isinstance(owner, dict) else item.get("container", "")
                    lines.append(f"  - `{item.get('type', '')}` owner `{selector}`")
            lines.append("")

    lines.extend(
        [
            "",
            "## Reproducibility notes",
            "",
            "- Start the viewport before running this FLOG.",
            "- The default viewport is the MCEL desktop baseline (`1920x1200`); use `--viewport WIDTHxHEIGHT` for explicit responsive probes.",
            "- The script uses the diagnostics widget payload (`MCELDiagnosticsCounterWidget.refresh`) and keeps the raw `window.MCEL.diagnose(appId)` report as fallback evidence.",
            "- FLOG v1 verifies the default-load contract health path only; app-specific interaction scenarios should be added as specs mature.",
            "- Use `--emit-events` to post the compact FLOG result to `/api/mcel/diagnostics/events`.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report_files(report: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "mcel-runtime-flog-report.json"
    markdown_path = output_dir / "mcel-runtime-flog-report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report) + "\n", encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the MCEL runtime FLOG contract smoke.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765", help="Viewport base URL.")
    parser.add_argument("--app", action="append", help="Run only this app. May be supplied multiple times.")
    parser.add_argument("--scenario", action="append", help="Run only this scenario id. May be supplied multiple times.")
    parser.add_argument("--startup-wait-ms", type=int, default=DEFAULT_STARTUP_WAIT_MS, help="Wait after load before diagnosis.")
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS, help="Browser navigation/API timeout.")
    parser.add_argument("--viewport", default=viewport_label(DEFAULT_VIEWPORT), help="Browser viewport, for example 1920x1200. Use explicit smaller viewports for responsive probes.")
    parser.add_argument("--headed", action="store_true", help="Show Chromium instead of running headless.")
    parser.add_argument("--allow-fail", action="store_true", help="Write reports but return success even when scenarios fail.")
    parser.add_argument("--allow-warnings", action="store_true", help="Do not fail on warning-level MCEL findings.")
    parser.add_argument("--emit-events", action="store_true", help="POST compact FLOG events to the MCEL diagnostics event endpoint.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Report output directory.")
    parser.add_argument("--list-scenarios", action="store_true", help="Print available scenarios and exit.")
    parser.add_argument("--json", action="store_true", help="Print the final report JSON to stdout.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = repo_root_from_script()
    viewport = parse_viewport(args.viewport)
    scenarios = build_scenarios(
        repo,
        apps=args.app,
        scenario_ids=args.scenario,
        startup_wait_ms=max(0, int(args.startup_wait_ms)),
    )

    if args.list_scenarios:
        for scenario in scenarios:
            print(f"{scenario.id}\t{scenario.app}\t{scenario.route}\t{scenario.intent}")
        return 0

    trials = run_browser_scenarios(
        scenarios,
        base_url=args.base_url,
        headed=bool(args.headed),
        timeout_ms=max(1000, int(args.timeout_ms)),
        emit_events=bool(args.emit_events),
        require_zero_warnings=not bool(args.allow_warnings),
        viewport=viewport,
    )
    report = build_report(repo=repo, base_url=args.base_url, scenarios=scenarios, trials=trials, viewport=viewport)
    paths = write_report_files(report, Path(args.output_dir))
    report["artifacts"] = paths

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        summary = report["summary"]
        print("mcel-runtime-flog-v1")
        print(f"base_url: {report['baseUrl']}")
        print(f"viewport: {viewport_label(report.get('viewport') or DEFAULT_VIEWPORT)}")
        print(f"status: {summary['status']}")
        print(f"scenarios: {summary['scenarioCount']}")
        print(f"status_counts: {summary['statusCounts']}")
        print(f"json: {paths['json']}")
        print(f"markdown: {paths['markdown']}")
        for trial in trials:
            classification = trial.get("classification") or {}
            status = classification.get("status", "unknown")
            failures = classification.get("failures") or []
            warnings = classification.get("warnings") or []
            suffix = ""
            if failures:
                suffix = " :: " + "; ".join(failures)
            elif warnings:
                suffix = " :: " + "; ".join(warnings)
            print(f"  {status}: {trial.get('scenarioId')}{suffix}")

    return 0 if args.allow_fail or report["summary"]["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
