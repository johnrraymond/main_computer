"""Generic IR-native intent-complete proof authority for MCEL applications.

Wave 9 removes application-specific proof commands from the authority path.
The generic authority discovers a registered application mechanics profile,
then owns dispatch, failure semantics, report identity, and reusable output.
Profiles do not decide truth status; they only supply mechanics that portable
Application IR cannot yet execute by itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_app_authoring_profiles import (
    AppAuthoringProfileError,
    get_app_authoring_profile,
)

REPORT_SCHEMA = "mcel.app-ir-native-intent-complete-proof.v1"
REPORT_VERSION = "mcel-app-ir-native-proof-wave9"


class AppIrNativeProofError(RuntimeError):
    """Raised when the generic IR-native proof cannot converge."""


def run_app_ir_native_intent_proof(
    *,
    app_id: str,
    repo: Path,
    record: Any,
    acceptance: Mapping[str, Any],
    observation: Mapping[str, Any],
    headed: bool = False,
    node_probe_runner: Any = None,
    browser_probe_runner: Any = None,
) -> dict[str, Any]:
    try:
        profile = get_app_authoring_profile(app_id)
    except AppAuthoringProfileError as exc:
        raise AppIrNativeProofError(str(exc)) from exc
    if record.app_id != app_id:
        raise AppIrNativeProofError("Package record identity does not match the requested application.")
    if profile.run_ir_native_proof is None:
        raise AppIrNativeProofError(
            f"Application {app_id!r} has no promoted IR-native proof mechanics; run its candidate portability proof instead."
        )
    try:
        native = profile.run_ir_native_proof(
            repo=repo,
            record=record,
            acceptance=acceptance,
            observation=observation,
            headed=headed,
            node_probe_runner=node_probe_runner,
            browser_probe_runner=browser_probe_runner,
        )
    except Exception as exc:  # profile exceptions are normalized at the authority boundary
        raise AppIrNativeProofError(str(exc)) from exc
    report = dict(native)
    report["schema"] = REPORT_SCHEMA
    report["version"] = REPORT_VERSION
    report["authority"] = "mcel.app-ir-native-proof.v1"
    report["applicationProfile"] = profile.profile_id
    report["projectionProfile"] = profile.projection_profile
    report["genericPipeline"] = True
    report["counterSpecificExecutionPathRequired"] = False
    report["legacyEvidenceRequired"] = False
    return report


def write_app_ir_native_report(
    report: Mapping[str, Any], output_directory: Path
) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "mcel-ir-native-intent-proof.json"
    markdown_path = output_directory / "mcel-ir-native-intent-proof.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        f"# {report.get('appId')} IR-Native Intent-Complete Proof",
        "",
        f"- Authority: `{report.get('authority')}`",
        f"- Application profile: `{report.get('applicationProfile')}`",
        f"- Status: `{report.get('status')}`",
        f"- Semantic fingerprint: `{report.get('semanticFingerprint')}`",
        f"- Intent coverage: `{report.get('coveredIntentCount')} / {report.get('declaredIntentCount')}`",
        f"- Scenario evidence: `{report.get('observedScenarioCount')} / {report.get('declaredScenarioCount')}`",
        f"- Effect accounting: `{(report.get('effectAccounting') or {}).get('status')}`",
        f"- Generic pipeline: `{str(bool(report.get('genericPipeline'))).lower()}`",
        f"- Application-specific execution authority required: `{str(bool(report.get('counterSpecificExecutionPathRequired'))).lower()}`",
        f"- Legacy evidence required: `{str(bool(report.get('legacyEvidenceRequired'))).lower()}`",
        "",
        "## Intents",
        "",
    ]
    for intent_id, entry in sorted((report.get("intents") or {}).items()):
        lines.append(f"- `{intent_id}`: `{'pass' if entry.get('passed') else 'fail'}`")
    lines.extend(["", "## Scenarios", ""])
    for scenario_id, entry in sorted((report.get("scenarios") or {}).items()):
        lines.append(f"- `{scenario_id}`: `{'pass' if entry.get('passed') else 'fail'}`")
    lines.append("")
    return "\n".join(lines)
