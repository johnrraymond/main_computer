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
    python main_computer/flog_mcel_runtime_smoke.py --scenario document.default-load --headed
    python main_computer/flog_mcel_runtime_smoke.py --emit-events
    python main_computer/flog_mcel_runtime_smoke.py --viewport 1920x1200
    python main_computer/flog_mcel_runtime_smoke.py --json

FLOG v2 is registry-driven.  It proves the desktop green contract baseline for every conformance-required app:
page loads, the app-surface registry policy is carried into the scenario, the diagnosis API and widget payload are available, primary surface
is usable, active warnings and errors are zero, visual-integrity violations are
absent, required app-surface conformance layers pass, and verdict/counts agree.  The default viewport is an explicit desktop baseline; responsive viewport scenarios can be added separately as each app contract becomes more detailed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

try:
    from .mcel_evidence_provenance import build_repository_provenance
except ImportError:
    try:
        from main_computer.mcel_evidence_provenance import build_repository_provenance
    except ImportError:  # Direct script execution from the repository root.
        from mcel_evidence_provenance import build_repository_provenance


REPORT_KIND = "mcel.flog.runtime-contracts.report"
REPORT_SCHEMA = "mcel-runtime-flog-report-v2"
REPORT_VERSION = "mcel-runtime-flog-v2"
DEFAULT_OUTPUT_DIR = Path("runtime/reports/flog/mcel-runtime")
DEFAULT_STARTUP_WAIT_MS = 6500
DEFAULT_TIMEOUT_MS = 15000
DEFAULT_VIEWPORT = {"width": 1920, "height": 1200}

APP_SURFACE_REGISTRY_JS = Path("main_computer/web/applications/scripts/mcel-app-surface-registry.js")

BASELINE_LAYER_IDS = (
    "semantic-surface",
    "layout-grammar",
    "runtime-ownership",
    "runtime-visual-fit",
    "diagnostic-no-throw",
)
RUNTIME_LAYER_IDS = (
    "runtime-ownership",
    "runtime-visual-fit",
    "diagnostic-no-throw",
)

ROUTE_OVERRIDES = {
    "calculator": "/applications/calculator",
    "document": "/applications/document",
    "file-explorer": "/applications/file-explorer",
    "git-tools": "/applications/git-tools",
    "code-editor": "/applications/code-editor",
    "website-builder": "/applications/website-builder/hub-site",
    "mcel-lab": "/applications/mcel-lab",
}

SCENARIO_INTENTS = {
    "calculator": "Verify the calculator workspace satisfies its runtime app-surface baseline.",
    "document": "Verify Document Editor satisfies its parked runtime-baseline ownership, visual-fit, and diagnostic conformance.",
    "file-explorer": "Verify File Explorer satisfies semantic-runtime surface, layout, ownership, and visual-fit conformance.",
    "git-tools": "Verify Git Tools satisfies semantic-runtime repository workflow, governed-publish, and runtime conformance when explicitly requested.",
    "code-editor": "Verify the Code Editor host/workbench exposes one usable selected-source editor.",
    "website-builder": "Verify Website Builder exposes a usable preview/design surface for the selected site.",
    "mcel-lab": "Verify MCEL Lab opens with a usable blueprint inspection workspace when explicitly requested.",
}


@dataclass(frozen=True)
class AppSurfacePolicy:
    app_id: str
    label: str
    state: str
    conformance_required: bool
    maturity: str
    surface_id: str = ""
    contract_id: str = ""
    required_layer_ids: tuple[str, ...] = ()
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "appId": self.app_id,
            "label": self.label,
            "state": self.state,
            "conformanceRequired": self.conformance_required,
            "maturity": self.maturity,
            "surfaceId": self.surface_id,
            "contractId": self.contract_id,
            "requiredLayerIds": list(self.required_layer_ids),
            "notes": self.notes,
        }


FALLBACK_APP_SURFACE_POLICIES = {
    "calculator": AppSurfacePolicy(
        app_id="calculator",
        label="Calculator",
        state="surface-aware",
        conformance_required=True,
        maturity="runtime-baseline",
        surface_id="calculator.surface.workspace",
        contract_id="calculator.contract.default.app-health",
        required_layer_ids=RUNTIME_LAYER_IDS,
    ),
    "code-editor": AppSurfacePolicy(
        app_id="code-editor",
        label="Code Editor",
        state="surface-aware",
        conformance_required=True,
        maturity="semantic-runtime",
        surface_id="code-editor.surface.monaco-selected-file-editor",
        contract_id="code-editor.contract.authoring.monaco-golden-path",
        required_layer_ids=RUNTIME_LAYER_IDS,
    ),
    "document": AppSurfacePolicy(
        app_id="document",
        label="Document Editor",
        state="surface-aware",
        conformance_required=True,
        maturity="runtime-baseline",
        surface_id="document-editor.surface.primary",
        contract_id="document-editor.contract.default.app-health",
        required_layer_ids=RUNTIME_LAYER_IDS,
    ),
    "file-explorer": AppSurfacePolicy(
        app_id="file-explorer",
        label="File Explorer",
        state="surface-aware",
        conformance_required=True,
        maturity="semantic-runtime",
        surface_id="file-explorer.surface.primary",
        contract_id="file-explorer.contract.default.app-health",
        required_layer_ids=BASELINE_LAYER_IDS,
    ),
    "website-builder": AppSurfacePolicy(
        app_id="website-builder",
        label="Website Builder",
        state="surface-aware",
        conformance_required=True,
        maturity="runtime-baseline",
        surface_id="website-builder.surface.preview",
        contract_id="website-builder.contract.default.app-health",
        required_layer_ids=RUNTIME_LAYER_IDS,
    ),
    "git-tools": AppSurfacePolicy(
        app_id="git-tools",
        label="Git Tools",
        state="surface-aware",
        conformance_required=True,
        maturity="semantic-runtime",
        surface_id="git-tools.surface.workflow",
        contract_id="git-tools.contract.default.app-health",
        required_layer_ids=RUNTIME_LAYER_IDS,
    ),
    "mcel-lab": AppSurfacePolicy(
        app_id="mcel-lab",
        label="MCEL Lab",
        state="surface-aware",
        conformance_required=True,
        maturity="semantic-runtime",
        surface_id="mcel-lab.form.work-surface.blueprint-inspection",
        contract_id="mcel-lab.contract.default.blueprint-studio-health",
        required_layer_ids=RUNTIME_LAYER_IDS,
    ),
}


@dataclass(frozen=True)
class RuntimeScenario:
    id: str
    app: str
    route: str
    intent: str
    startup_wait_ms: int = DEFAULT_STARTUP_WAIT_MS
    conformance_required: bool = False
    registry_state: str = "unregistered"
    maturity: str = "unregistered"
    surface_id: str = ""
    contract_id: str = ""
    required_layer_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "app": self.app,
            "route": self.route,
            "intent": self.intent,
            "startupWaitMs": self.startup_wait_ms,
            "appSurfacePolicy": {
                "conformanceRequired": self.conformance_required,
                "registryState": self.registry_state,
                "maturity": self.maturity,
                "surfaceId": self.surface_id,
                "contractId": self.contract_id,
                "requiredLayerIds": list(self.required_layer_ids),
            },
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


def _matching_brace_index(text: str, start: int) -> int:
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'", "`"}:
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("unclosed JavaScript object literal")


def _object_literal_for_constant(source: str, constant_name: str) -> str:
    marker = f"const {constant_name}"
    marker_index = source.index(marker)
    open_index = source.index("{", marker_index)
    close_index = _matching_brace_index(source, open_index)
    return source[open_index + 1:close_index]


def _read_js_string(source: str, start: int) -> tuple[str, int]:
    quote = source[start]
    index = start + 1
    escaped = False
    chars: list[str] = []
    while index < len(source):
        char = source[index]
        if escaped:
            chars.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == quote:
            return "".join(chars), index + 1
        else:
            chars.append(char)
        index += 1
    raise ValueError("unclosed JavaScript string literal")


def _iter_policy_blocks(object_source: str) -> list[tuple[str, str]]:
    """Yield top-level policy entries from a simple JS object literal."""

    results: list[tuple[str, str]] = []
    index = 0
    length = len(object_source)
    while index < length:
        while index < length and object_source[index] in " \t\r\n,":
            index += 1
        if index >= length:
            break

        if object_source[index] in {'"', "'"}:
            key, index = _read_js_string(object_source, index)
        else:
            start = index
            while index < length and (object_source[index].isalnum() or object_source[index] in "_-$"):
                index += 1
            key = object_source[start:index].strip()
        if not key:
            index += 1
            continue

        while index < length and object_source[index].isspace():
            index += 1
        if index >= length or object_source[index] != ":":
            continue
        index += 1
        while index < length and object_source[index].isspace():
            index += 1
        if index >= length or object_source[index] != "{":
            continue
        close = _matching_brace_index(object_source, index)
        results.append((key, object_source[index + 1:close]))
        index = close + 1
    return results


def _const_array(source: str, name: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    match = re.search(rf"const\s+{re.escape(name)}\s*=\s*Object\.freeze\(\s*\[(.*?)\]\s*\)", source, re.S)
    if not match:
        return fallback
    values = re.findall(r'"([^"]+)"', match.group(1))
    return tuple(values) if values else fallback


def _field_string(block: str, field: str, fallback: str = "") -> str:
    match = re.search(rf"\b{re.escape(field)}\s*:\s*\"([^\"]*)\"", block)
    return match.group(1) if match else fallback


def _field_bool(block: str, field: str, fallback: bool = False) -> bool:
    match = re.search(rf"\b{re.escape(field)}\s*:\s*(true|false)\b", block)
    return (match.group(1) == "true") if match else fallback


def _field_layers(block: str, constants: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    match = re.search(r"\brequiredLayerIds\s*:\s*([A-Z_]+|\[[^\]]*\])", block, re.S)
    if not match:
        return ()
    value = match.group(1).strip()
    if value in constants:
        return constants[value]
    return tuple(re.findall(r'"([^"]+)"', value))


def load_app_surface_policies(repo: Path | None = None) -> dict[str, AppSurfacePolicy]:
    """Load conformance enrollment from McelAppSurfaceRegistry.

    The runtime smoke is intentionally registry-driven.  The parser stays small
    and local so ``--list-scenarios`` works even when Node is unavailable.
    """

    repo = repo or repo_root_from_script()
    registry_path = repo / APP_SURFACE_REGISTRY_JS
    if not registry_path.exists():
        return dict(FALLBACK_APP_SURFACE_POLICIES)

    try:
        source = registry_path.read_text(encoding="utf-8")
        constants = {
            "BASELINE_LAYER_IDS": _const_array(source, "BASELINE_LAYER_IDS", BASELINE_LAYER_IDS),
            "RUNTIME_LAYER_IDS": _const_array(source, "RUNTIME_LAYER_IDS", RUNTIME_LAYER_IDS),
        }
        policies: dict[str, AppSurfacePolicy] = {}
        for constant_name, default_required in (
            ("REQUIRED_APP_POLICIES", True),
            ("LEGACY_APP_POLICIES", False),
        ):
            object_source = _object_literal_for_constant(source, constant_name)
            for key, block in _iter_policy_blocks(object_source):
                app_id = _field_string(block, "appId", key)
                required = _field_bool(block, "conformanceRequired", default_required)
                policy = AppSurfacePolicy(
                    app_id=app_id,
                    label=_field_string(block, "label", app_id),
                    state=_field_string(block, "state", "surface-aware" if required else "legacy"),
                    conformance_required=required,
                    maturity=_field_string(block, "maturity", "runtime-baseline" if required else "legacy"),
                    surface_id=_field_string(block, "surfaceId", ""),
                    contract_id=_field_string(block, "contractId", ""),
                    required_layer_ids=_field_layers(block, constants) if required else (),
                    notes=_field_string(block, "notes", ""),
                )
                policies[policy.app_id] = policy
        return policies or dict(FALLBACK_APP_SURFACE_POLICIES)
    except Exception:
        return dict(FALLBACK_APP_SURFACE_POLICIES)


def unknown_policy(app: str) -> AppSurfacePolicy:
    return AppSurfacePolicy(
        app_id=app,
        label=app or "Unknown app",
        state="unregistered",
        conformance_required=False,
        maturity="unregistered",
    )


def scenario_for_app(
    app: str,
    *,
    policy: AppSurfacePolicy | None = None,
    startup_wait_ms: int = DEFAULT_STARTUP_WAIT_MS,
) -> RuntimeScenario:
    policy = policy or FALLBACK_APP_SURFACE_POLICIES.get(app) or unknown_policy(app)
    intent = SCENARIO_INTENTS.get(app)
    if not intent:
        if policy.conformance_required:
            layers = ", ".join(policy.required_layer_ids) or "registered runtime layers"
            intent = f"Verify {policy.label} satisfies its registered MCEL app-surface layers: {layers}."
        else:
            intent = f"Verify {policy.label} opens; conformance is not required for this registry state."
    return RuntimeScenario(
        id=f"{app}.default-load",
        app=app,
        route=app_route(app),
        intent=intent,
        startup_wait_ms=startup_wait_ms,
        conformance_required=policy.conformance_required,
        registry_state=policy.state,
        maturity=policy.maturity,
        surface_id=policy.surface_id,
        contract_id=policy.contract_id,
        required_layer_ids=tuple(policy.required_layer_ids),
    )


def build_scenarios(
    repo: Path | None = None,
    *,
    apps: list[str] | None = None,
    scenario_ids: list[str] | None = None,
    startup_wait_ms: int = DEFAULT_STARTUP_WAIT_MS,
) -> list[RuntimeScenario]:
    repo = repo or repo_root_from_script()
    policies = load_app_surface_policies(repo)
    if apps:
        app_names = sorted(dict.fromkeys(apps))
    else:
        app_names = sorted(policy.app_id for policy in policies.values() if policy.conformance_required)
    scenarios = [
        scenario_for_app(
            app,
            policy=policies.get(app) or FALLBACK_APP_SURFACE_POLICIES.get(app) or unknown_policy(app),
            startup_wait_ms=startup_wait_ms,
        )
        for app in app_names
    ]
    if scenario_ids:
        wanted = set(scenario_ids)
        scenarios = [scenario for scenario in scenarios if scenario.id in wanted]
        missing = sorted(wanted - {scenario.id for scenario in scenarios})
        if missing:
            available = build_scenarios(repo, apps=app_names, startup_wait_ms=startup_wait_ms)
            raise ValueError(
                "Unknown scenario(s): "
                + ", ".join(missing)
                + ". Available: "
                + ", ".join(scenario.id for scenario in available)
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


def _measurement_list(diagnosis: dict[str, Any], key: str) -> list[Any]:
    measurements = diagnosis.get("measurements") if isinstance(diagnosis, dict) else {}
    if not isinstance(measurements, dict):
        return []
    values = measurements.get(key)
    return values if isinstance(values, list) else []


def _visual_integrity_violations(diagnosis: dict[str, Any]) -> list[Any]:
    return _measurement_list(diagnosis, "visualIntegrityViolations")


def _layout_collisions(diagnosis: dict[str, Any]) -> list[Any]:
    return _measurement_list(diagnosis, "layoutCollisions")


def _content_fit_violations(diagnosis: dict[str, Any]) -> list[Any]:
    return _measurement_list(diagnosis, "contentFitViolations")


def _app_surface_conformance_summary(diagnosis: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(diagnosis, dict):
        return {}
    conformance = diagnosis.get("appSurfaceConformance")
    if isinstance(conformance, dict):
        return conformance
    summary = diagnosis.get("summary")
    if isinstance(summary, dict) and isinstance(summary.get("appSurfaceConformance"), dict):
        return summary["appSurfaceConformance"]
    measurements = diagnosis.get("measurements")
    if isinstance(measurements, dict) and isinstance(measurements.get("appSurfaceConformance"), dict):
        return measurements["appSurfaceConformance"]
    return {}


def _layer_statuses(conformance: dict[str, Any]) -> dict[str, str]:
    layers = conformance.get("layers")
    if not isinstance(layers, list):
        return {}
    result: dict[str, str] = {}
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        layer_id = str(layer.get("id") or "").strip()
        status = str(layer.get("status") or "").strip().lower()
        if layer_id:
            result[layer_id] = status
    return result


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _conformance_layer_ids(conformance: dict[str, Any], policy_key: str, fallback_key: str) -> list[str]:
    """Return policy-scoped layer ids when the payload exposes them.

    Runtime-baseline and host-workbench apps intentionally leave static
    semantic/layout layers failed or unavailable while still passing their
    registry policy.  An empty policy list is therefore meaningful and must not
    fall back to the broad failed/unavailable layer list.
    """

    if policy_key in conformance:
        return _string_list(conformance.get(policy_key))
    return _string_list(conformance.get(fallback_key))


def _conformance_policy_scope(
    conformance: dict[str, Any],
    required_layer_ids: tuple[str, ...] | list[str] | None,
) -> dict[str, Any]:
    """Summarize app-surface conformance through the registry policy lens.

    Raw conformance payloads can include static layer failures that are useful
    evidence but are not part of the app's current registry policy.  For
    example, a runtime-baseline app may pass runtime ownership/fit while static
    semantic extraction is unavailable.  This summary keeps those facts visible
    without making the report look contradictory.
    """

    required_layers = _unique_messages(
        [str(layer_id) for layer_id in (required_layer_ids or ()) if str(layer_id).strip()]
    )
    if not isinstance(conformance, dict) or not conformance:
        return {
            "status": "missing",
            "requiredLayerIds": required_layers,
            "requiredLayerStatuses": {},
            "failedLayerIds": [],
            "unavailableLayerIds": [],
            "nonRequiredFailedLayerIds": [],
            "nonRequiredUnavailableLayerIds": [],
        }

    layer_statuses = _layer_statuses(conformance)
    all_failed = _string_list(conformance.get("failedLayerIds"))
    all_unavailable = _string_list(conformance.get("unavailableLayerIds"))
    policy_failed = _conformance_layer_ids(conformance, "policyFailedLayerIds", "failedLayerIds")
    policy_unavailable = _conformance_layer_ids(
        conformance,
        "policyUnavailableLayerIds",
        "unavailableLayerIds",
    )

    required_failed: list[str] = []
    required_unavailable: list[str] = []
    required_statuses: dict[str, str] = {}
    for layer_id in required_layers:
        layer_status = layer_statuses.get(layer_id)
        required_statuses[layer_id] = layer_status or "missing"
        if layer_status in {"fail", "error"}:
            required_failed.append(layer_id)
        elif layer_status in {None, "", "unavailable", "partial"}:
            required_unavailable.append(layer_id)

    policy_failed = _unique_messages(policy_failed + required_failed)
    policy_unavailable = _unique_messages(policy_unavailable + required_unavailable)
    policy_layer_set = set(required_layers) | set(policy_failed) | set(policy_unavailable)

    non_required_failed = [
        layer_id for layer_id in all_failed if layer_id not in policy_layer_set
    ]
    non_required_unavailable = [
        layer_id for layer_id in all_unavailable if layer_id not in policy_layer_set
    ]

    status = str(conformance.get("status") or conformance.get("verdict") or "").lower()
    valid = conformance.get("valid")
    policy_status = "pass"
    if policy_failed or policy_unavailable or status != "pass" or valid is not True:
        policy_status = "fail"

    return {
        "status": policy_status,
        "rawStatus": status or "unknown",
        "rawValid": valid,
        "requiredLayerIds": required_layers,
        "requiredLayerStatuses": required_statuses,
        "failedLayerIds": policy_failed,
        "unavailableLayerIds": policy_unavailable,
        "nonRequiredFailedLayerIds": _unique_messages(non_required_failed),
        "nonRequiredUnavailableLayerIds": _unique_messages(non_required_unavailable),
    }


def _unique_messages(messages: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for message in messages:
        if message in seen:
            continue
        seen.add(message)
        result.append(message)
    return result


def classify_diagnosis(
    diagnosis: dict[str, Any],
    *,
    require_zero_warnings: bool = True,
    conformance_required: bool = False,
    required_layer_ids: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    counts = _summary_counts(diagnosis)
    primary = _primary_surface_summary(diagnosis)
    visual_integrity = _visual_integrity_violations(diagnosis)
    layout_collisions = _layout_collisions(diagnosis)
    content_fit = _content_fit_violations(diagnosis)
    conformance = _app_surface_conformance_summary(diagnosis)
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
    if layout_collisions:
        failures.append(f"{len(layout_collisions)} layout collision(s) are active")
    if content_fit:
        failures.append(f"{len(content_fit)} content-fit violation(s) are active")

    required_layers = tuple(str(layer_id) for layer_id in (required_layer_ids or ()) if str(layer_id).strip())
    policy_scope = _conformance_policy_scope(conformance, required_layers)
    if conformance_required and not conformance:
        failures.append("app-surface conformance summary is missing")
    elif conformance:
        status = str(conformance.get("status") or conformance.get("verdict") or "").lower()
        valid = conformance.get("valid")
        if conformance_required and not (status == "pass" and valid is True):
            failures.append(f"app-surface conformance status is {status or 'unknown'}")
        failed_layers = _conformance_layer_ids(conformance, "policyFailedLayerIds", "failedLayerIds")
        unavailable_layers = _conformance_layer_ids(conformance, "policyUnavailableLayerIds", "unavailableLayerIds")
        for layer_id in failed_layers:
            failures.append(f"app-surface layer failed: {layer_id}")
        if conformance_required:
            for layer_id in unavailable_layers:
                failures.append(f"required app-surface layer unavailable: {layer_id}")

        layer_statuses = _layer_statuses(conformance)
        for layer_id in required_layers:
            layer_status = layer_statuses.get(layer_id)
            if layer_status in {"fail", "error"}:
                failures.append(f"required app-surface layer failed: {layer_id}")
            elif conformance_required and layer_status in {None, "", "unavailable", "partial"}:
                failures.append(f"required app-surface layer unavailable: {layer_id}")

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
        "appSurfaceConformance": conformance,
        "appSurfacePolicyScope": policy_scope,
        "requiredLayerIds": list(required_layers),
        "visualIntegrityViolationCount": len(visual_integrity),
        "layoutCollisionCount": len(layout_collisions),
        "contentFitViolationCount": len(content_fit),
        "failures": _unique_messages(failures),
        "warnings": _unique_messages(warnings),
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
        "appSurfacePolicy": trial.get("appSurfacePolicy") or {},
        "appSurfaceConformance": classification.get("appSurfaceConformance") or {},
        "appSurfacePolicyScope": classification.get("appSurfacePolicyScope") or {},
        "requiredLayerIds": classification.get("requiredLayerIds") or [],
        "appTruth": trial.get("appTruth") if isinstance(trial.get("appTruth"), dict) else {},
        "appTruthAvailable": trial.get("appTruthAvailable") is True,
        "issueEvidence": issues[:evidence_limit],
        "visualIntegrityViolations": (measurements.get("visualIntegrityViolations") or [])[:evidence_limit],
        "layoutCollisions": (measurements.get("layoutCollisions") or [])[:evidence_limit],
        "contentFitViolations": (measurements.get("contentFitViolations") or [])[:evidence_limit],
    }


def app_truth_runtime_evidence_from_trial(trial: dict[str, Any]) -> dict[str, Any]:
    """Return the compact runtime-evidence shape consumed by McelAppTruthGate."""

    result = trial_result_summary(trial)
    result["appId"] = result.get("app") or ""
    result["finishedAt"] = trial.get("finishedAt") or ""
    result["generatedAt"] = trial.get("finishedAt") or trial.get("startedAt") or ""
    return result


def latest_app_truth_snapshot(trials: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the most complete gate-built snapshot captured during this run."""

    for trial in reversed(trials):
        snapshot = trial.get("appTruthSnapshot")
        if isinstance(snapshot, dict) and snapshot.get("schema"):
            return snapshot
    return {}


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
    app_surface_conformance = (
        diagnosis.get("appSurfaceConformance")
        or summary.get("appSurfaceConformance")
        or measurements.get("appSurfaceConformance")
        or {}
    )
    return {
        "schema": diagnosis.get("schema") or diagnosis.get("REPORT_SCHEMA") or "",
        "appId": diagnosis.get("appId") or "",
        "contractId": diagnosis.get("contractId") or "",
        "mode": diagnosis.get("mode") or "",
        "verdict": diagnosis.get("verdict") or "unknown",
        "summary": {
            **summary,
            **({"appSurfaceConformance": app_surface_conformance} if app_surface_conformance else {}),
        },
        "primarySurface": diagnosis.get("primarySurface") or summary.get("primarySurface") or {},
        "appSurfaceConformance": app_surface_conformance,
        "findings": findings[:25],
        "measurements": {
            "visualIntegrityViolations": measurements.get("visualIntegrityViolations") or [],
            "layoutCollisions": measurements.get("layoutCollisions") or [],
            "contentFitViolations": measurements.get("contentFitViolations") or [],
            "appSurfaceConformance": measurements.get("appSurfaceConformance") or app_surface_conformance or {},
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
    app_surface_conformance = payload.get("appSurfaceConformance") or measurements.get("appSurfaceConformance") or {}
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
        "appSurfaceConformance": app_surface_conformance,
        "measurements": {
            "visualIntegrityViolations": measurements.get("visualIntegrityViolations") or [],
            "layoutCollisions": measurements.get("layoutCollisions") or [],
            "contentFitViolations": measurements.get("contentFitViolations") or [],
            "appSurfaceConformance": measurements.get("appSurfaceConformance") or app_surface_conformance or {},
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
    event_conformance = classification.get("appSurfaceConformance") or evidence.get("appSurfaceConformance") or {}
    event_policy_scope = classification.get("appSurfacePolicyScope") or _conformance_policy_scope(
        event_conformance,
        classification.get("requiredLayerIds") or [],
    )
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
        "appSurfaceConformance": event_conformance,
        "appSurfacePolicyScope": event_policy_scope,
        "appTruth": trial.get("appTruth") if isinstance(trial.get("appTruth"), dict) else {},
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
                    "appSurfacePolicy": scenario.to_dict().get("appSurfacePolicy", {}),
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
                    prior_truth_evidence = [
                        app_truth_runtime_evidence_from_trial(evidence_trial)
                        for evidence_trial in trials
                    ]
                    result = page.evaluate(
                        """({appId, priorEvidence}) => {
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

                          const conformance =
                            payload?.appSurfaceConformance ||
                            raw?.appSurfaceConformance ||
                            raw?.summary?.appSurfaceConformance ||
                            raw?.measurements?.appSurfaceConformance ||
                            null;
                          const currentEvidence = {
                            appId,
                            status: payload?.verdict || raw?.verdict || conformance?.status || "unknown",
                            timestamp: payload?.timestamp || new Date().toISOString(),
                            widgetPayload: payload || null,
                            diagnosis: raw || {},
                            appSurfaceConformance: conformance || null
                          };
                          const runtimeEvidence = [
                            ...(Array.isArray(priorEvidence) ? priorEvidence : []),
                            currentEvidence
                          ];
                          const truthGate =
                            window.McelAppTruthGate ||
                            window.MCEL?.appTruthGate ||
                            null;
                          let appTruth = null;
                          let appTruthSnapshot = null;
                          let appTruthError = "";
                          if (
                            truthGate &&
                            typeof truthGate.evaluateAppTruth === "function" &&
                            typeof truthGate.buildTruthSnapshot === "function"
                          ) {
                            try {
                              appTruth = truthGate.evaluateAppTruth(appId, {runtimeEvidence});
                              appTruthSnapshot = truthGate.buildTruthSnapshot({runtimeEvidence});
                            } catch (error) {
                              appTruthError = String(error?.message || error || "truth-gate evaluation failed");
                            }
                          } else {
                            appTruthError = "McelAppTruthGate is unavailable";
                          }

                          return JSON.parse(JSON.stringify({
                            diagnosis: raw || {},
                            widgetPayload: payload || null,
                            widgetStatusAvailable: Boolean(status),
                            widgetPayloadAvailable: Boolean(payload),
                            appTruthAvailable: Boolean(appTruth),
                            appTruth: appTruth || null,
                            appTruthSnapshot: appTruthSnapshot || null,
                            appTruthError
                          }));
                        }""",
                        {
                            "appId": scenario.app,
                            "priorEvidence": prior_truth_evidence,
                        },
                    )
                    raw_diagnosis = result.get("diagnosis") if isinstance(result, dict) and isinstance(result.get("diagnosis"), dict) else {}
                    widget_payload = result.get("widgetPayload") if isinstance(result, dict) and isinstance(result.get("widgetPayload"), dict) else {}

                    trial["diagnosis"] = compact_diagnosis(raw_diagnosis)
                    trial["widgetPayload"] = compact_widget_payload(widget_payload) if widget_payload else {}
                    trial["widgetStatusAvailable"] = bool(result.get("widgetStatusAvailable")) if isinstance(result, dict) else False
                    trial["widgetPayloadAvailable"] = bool(result.get("widgetPayloadAvailable")) if isinstance(result, dict) else False
                    trial["appTruthAvailable"] = bool(result.get("appTruthAvailable")) if isinstance(result, dict) else False
                    trial["appTruth"] = (
                        result.get("appTruth")
                        if isinstance(result, dict) and isinstance(result.get("appTruth"), dict)
                        else {}
                    )
                    trial["appTruthSnapshot"] = (
                        result.get("appTruthSnapshot")
                        if isinstance(result, dict) and isinstance(result.get("appTruthSnapshot"), dict)
                        else {}
                    )
                    trial["appTruthError"] = str(result.get("appTruthError") or "") if isinstance(result, dict) else ""

                    classification_source = trial["widgetPayload"] or trial["diagnosis"]
                    trial["classification"] = classify_diagnosis(
                        classification_source,
                        require_zero_warnings=require_zero_warnings,
                        conformance_required=scenario.conformance_required,
                        required_layer_ids=scenario.required_layer_ids,
                    )
                    if not trial["widgetPayloadAvailable"]:
                        trial["classification"].setdefault("warnings", []).append(
                            "diagnostics widget payload was unavailable; classified raw MCEL diagnosis fallback"
                        )
                    if not trial["appTruthAvailable"]:
                        truth_error = trial.get("appTruthError") or "truth-gate result was unavailable"
                        trial["classification"].setdefault("warnings", []).append(
                            f"MCEL app truth was not attached: {truth_error}"
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
    truth_status_counts: dict[str, int] = {}
    truth_finding_counts: dict[str, int] = {}
    runtime_surface_proven = 0
    semantic_runtime_proven = 0
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

        app_truth = trial.get("appTruth") if isinstance(trial.get("appTruth"), dict) else {}
        if app_truth:
            truth_status = str(app_truth.get("overallStatus") or "unknown")
            truth_status_counts[truth_status] = truth_status_counts.get(truth_status, 0) + 1
            claims = app_truth.get("claims") if isinstance(app_truth.get("claims"), dict) else {}
            runtime_surface_proven += 1 if claims.get("runtimeSurfaceProven") is True else 0
            semantic_runtime_proven += 1 if claims.get("semanticRuntimeProven") is True else 0
            for code in app_truth.get("findingCodes") or []:
                code_text = str(code)
                truth_finding_counts[code_text] = truth_finding_counts.get(code_text, 0) + 1

    return {
        "status": "pass" if status_counts.get("fail", 0) == 0 else "fail",
        "scenarioCount": len(trials),
        "statusCounts": dict(sorted(status_counts.items())),
        "truthStatusCounts": dict(sorted(truth_status_counts.items())),
        "truthFindingCounts": dict(sorted(truth_finding_counts.items())),
        "runtimeSurfaceProvenCount": runtime_surface_proven,
        "semanticRuntimeProvenCount": semantic_runtime_proven,
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
        "repositoryProvenance": build_repository_provenance(repo),
        "baseUrl": normalize_base_url(base_url).rstrip("/"),
        "viewport": viewport or dict(DEFAULT_VIEWPORT),
        "source": {
            "scenarioSource": "mcel-app-surface-registry-conformance-required-apps-with-route-overrides",
            "diagnosisSource": "window.MCELDiagnosticsCounterWidget.refresh with appSurfaceConformance and window.MCEL.diagnose fallback",
            "truthSource": "window.McelAppTruthGate evaluateAppTruth/buildTruthSnapshot",
            "eventSource": "mcel-diagnostic-event-v1",
        },
        "scenarios": [scenario.to_dict() for scenario in scenarios],
        "summary": summarize_trials(trials),
        "results": [trial_result_summary(trial) for trial in trials],
        "appTruthSnapshot": latest_app_truth_snapshot(trials),
        "trials": [
            {key: value for key, value in trial.items() if key != "appTruthSnapshot"}
            for trial in trials
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# MCEL Runtime FLOG Report",
        "",
        f"- Schema: `{report.get('schema', '')}`",
        f"- Version: `{report.get('version', '')}`",
        f"- Generated: `{report.get('generatedAt', '')}`",
        f"- Repository fingerprint: `{(report.get('repositoryProvenance') or {}).get('fingerprint', '')}`",
        f"- Repository fingerprint scope: `{(report.get('repositoryProvenance') or {}).get('scope', '')}`",
        f"- Repository selection method: `{(report.get('repositoryProvenance') or {}).get('selectionMethod', '')}`",
        f"- Base URL: `{report.get('baseUrl', '')}`",
        f"- Viewport: `{viewport_label(report.get('viewport') or DEFAULT_VIEWPORT)}`",
        f"- Status: **{summary.get('status', 'unknown')}**",
        f"- Scenarios: {summary.get('scenarioCount', 0)}",
        "",
        "## Scenario results",
        "",
        "| Scenario | App | Status | Errors | Warnings | Primary usable | Conformance | Policy scope | Notes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for trial in report.get("trials") or []:
        classification = trial.get("classification") or {}
        counts = classification.get("counts") or {}
        primary = classification.get("primarySurface") or {}
        conformance = classification.get("appSurfaceConformance") or {}
        policy_scope = classification.get("appSurfacePolicyScope") or {}
        conformance_status = conformance.get("status") or "missing"
        policy_status = policy_scope.get("status") or "missing"
        note_items = list((classification.get("failures") or []) + (classification.get("warnings") or []))
        non_required_failed = policy_scope.get("nonRequiredFailedLayerIds") or []
        non_required_unavailable = policy_scope.get("nonRequiredUnavailableLayerIds") or []
        if non_required_failed:
            note_items.append("non-required failed layers: " + ", ".join(str(item) for item in non_required_failed))
        if non_required_unavailable:
            note_items.append("non-required unavailable layers: " + ", ".join(str(item) for item in non_required_unavailable))
        notes = "; ".join(note_items)
        lines.append(
            "| {scenario} | {app} | {status} | {errors} | {warnings} | {primary} | {conformance} | {policy} | {notes} |".format(
                scenario=trial.get("scenarioId", ""),
                app=trial.get("app", ""),
                status=classification.get("status", "unknown"),
                errors=counts.get("errors", 0),
                warnings=counts.get("warnings", 0),
                primary="yes" if primary.get("usable") else "no",
                conformance=str(conformance_status).replace("|", "\\|"),
                policy=str(policy_status).replace("|", "\\|"),
                notes=notes.replace("|", "\\|"),
            )
        )

    truth_results = [
        result for result in report.get("results") or []
        if isinstance(result.get("appTruth"), dict) and result.get("appTruth")
    ]
    if truth_results:
        lines.extend(
            [
                "",
                "## App truth",
                "",
                "FLOG runtime status remains the surface-smoke verdict. The truth gate keeps requirements, adapter, acceptance, and semantic readiness as separate claims.",
                "",
                "| App | Overall truth | Surface runtime proven | Acceptance proven | Semantic runtime proven | Findings |",
                "|---|---:|---:|---:|---:|---|",
            ]
        )
        for result in truth_results:
            app_truth = result.get("appTruth") or {}
            claims = app_truth.get("claims") if isinstance(app_truth.get("claims"), dict) else {}
            finding_codes = app_truth.get("findingCodes") or []
            lines.append(
                "| {app} | {overall} | {surface} | {acceptance} | {semantic} | {findings} |".format(
                    app=result.get("app") or app_truth.get("appId") or "",
                    overall=str(app_truth.get("overallStatus") or "unknown").replace("|", "\\|"),
                    surface="yes" if claims.get("runtimeSurfaceProven") is True else "no",
                    acceptance="yes" if claims.get("acceptanceProven") is True else "no",
                    semantic="yes" if claims.get("semanticRuntimeProven") is True else "no",
                    findings=", ".join(str(code) for code in finding_codes).replace("|", "\\|") or "none",
                )
            )

    failed_results = [result for result in report.get("results") or [] if result.get("status") == "fail"]
    if failed_results:
        lines.extend(["", "## Failed scenario evidence", ""])
        for result in failed_results:
            lines.append(f"### {result.get('scenarioId', '')}")
            for reason in result.get("failures") or []:
                lines.append(f"- Failure: {reason}")
            conformance = result.get("appSurfaceConformance") or {}
            if conformance:
                policy_failed = conformance.get("policyFailedLayerIds") or conformance.get("failedLayerIds") or []
                policy_unavailable = conformance.get("policyUnavailableLayerIds") or conformance.get("unavailableLayerIds") or []
                lines.append(f"- App-surface conformance: `{conformance.get('status', 'unknown')}`")
                if policy_failed:
                    lines.append(f"- Failed conformance layers: `{', '.join(str(item) for item in policy_failed)}`")
                if policy_unavailable:
                    lines.append(f"- Unavailable conformance layers: `{', '.join(str(item) for item in policy_unavailable)}`")
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
            "- FLOG v2 uses `McelAppSurfaceRegistry` as the default scenario source and verifies conformance-required apps only unless `--app` is supplied.",
            "- Each browser scenario asks `McelAppTruthGate` to attach app truth and a gate-built repository snapshot. Truth findings do not rewrite the FLOG surface verdict.",
            "- Use `--emit-events` to post the compact FLOG result, including attached app truth when available, to `/api/mcel/diagnostics/events`.",
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
        print(REPORT_VERSION)
        print(f"base_url: {report['baseUrl']}")
        print(f"viewport: {viewport_label(report.get('viewport') or DEFAULT_VIEWPORT)}")
        print(f"status: {summary['status']}")
        print(f"scenarios: {summary['scenarioCount']}")
        print(f"status_counts: {summary['statusCounts']}")
        print(f"truth_status_counts: {summary.get('truthStatusCounts', {})}")
        print(f"semantic_runtime_proven: {summary.get('semanticRuntimeProvenCount', 0)}")
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
