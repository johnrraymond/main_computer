#!/usr/bin/env python3
"""
Regression smoke: generated-editor declared result modes must preserve promotability semantics.

This smoke does not call a model and does not modify the real repo.  It invokes
the generated-editor discovery/grounding smoke in offline-self-check mode and
codifies the four mode/exit-code combinations that should remain stable:

* default full_file_replacement is terminal but not patch-promotable
* default full_file_replacement plus --require-promotable exits nonzero
* declared patch_artifact packages a snapshot zip and is patch-promotable
* declared patch_artifact plus --require-promotable exits zero

The --require-promotable cases are simulated from the same offline report because
that flag only changes the final process exit decision; it must not change the
terminal result contract itself.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rag_generated_editor_discovery_grounding_smoke import run_offline_self_check


@dataclass(frozen=True)
class CommandCase:
    name: str
    result_mode: str
    require_promotable: bool
    expected_returncode: int
    expected_result_mode: str
    expected_promotable: bool
    expected_artifact_ready: bool


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def output_root(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = root / "debug_assets" / "rag_generated_editor_patch_artifact_mode_smoke" / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_mode(root: Path, result_mode: str) -> tuple[dict[str, Any], int, str]:
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        report, exit_code = run_offline_self_check(root, result_mode=result_mode)
    return report, exit_code, captured.getvalue()


def command_cases() -> list[CommandCase]:
    return [
        CommandCase(
            name="default_full_file_replacement_is_terminal_not_promotable",
            result_mode="full_file_replacement",
            require_promotable=False,
            expected_returncode=0,
            expected_result_mode="full_file_replacement",
            expected_promotable=False,
            expected_artifact_ready=False,
        ),
        CommandCase(
            name="default_full_file_replacement_fails_require_promotable",
            result_mode="full_file_replacement",
            require_promotable=True,
            expected_returncode=1,
            expected_result_mode="full_file_replacement",
            expected_promotable=False,
            expected_artifact_ready=False,
        ),
        CommandCase(
            name="declared_patch_artifact_is_promotable",
            result_mode="patch_artifact",
            require_promotable=False,
            expected_returncode=0,
            expected_result_mode="patch_artifact",
            expected_promotable=True,
            expected_artifact_ready=True,
        ),
        CommandCase(
            name="declared_patch_artifact_satisfies_require_promotable",
            result_mode="patch_artifact",
            require_promotable=True,
            expected_returncode=0,
            expected_result_mode="patch_artifact",
            expected_promotable=True,
            expected_artifact_ready=True,
        ),
    ]


def effective_exit_code(report: dict[str, Any], base_exit_code: int, require_promotable: bool) -> int:
    if require_promotable and not report.get("promotable"):
        return 1
    return base_exit_code


def evaluate_case(
    *,
    case: CommandCase,
    report: dict[str, Any],
    base_exit_code: int,
    captured_stdout: str,
) -> dict[str, Any]:
    returncode = effective_exit_code(report, base_exit_code, case.require_promotable)
    terminal_result = report.get("terminal_result")
    artifact_report = terminal_result.get("artifact_report") if isinstance(terminal_result, dict) else None
    artifact_contract_passed = (
        artifact_report.get("artifact_contract_passed") if isinstance(artifact_report, dict) else None
    )

    checks = {
        "returncode": returncode == case.expected_returncode,
        "base_exit_code_ok": base_exit_code == 0,
        "ok_field": report.get("ok") is True,
        "terminal_state": report.get("terminal_state") == "accepted_terminal_result",
        "result_contract_passed": report.get("result_contract_passed") is True,
        "result_mode": report.get("result_mode") == case.expected_result_mode,
        "declared_result_mode": report.get("declared_result_mode") == case.expected_result_mode,
        "promotable": report.get("promotable") is case.expected_promotable,
        "artifact_ready": report.get("artifact_ready") is case.expected_artifact_ready,
    }
    if case.expected_result_mode == "patch_artifact":
        checks.update(
            {
                "artifact_contract_passed": artifact_contract_passed is True,
                "artifact_report_promotable": artifact_report.get("promotable") is True
                if isinstance(artifact_report, dict)
                else False,
            }
        )
    else:
        checks["no_artifact_report"] = artifact_report is None

    args = ["--offline-self-check", "--result-mode", case.result_mode]
    if case.require_promotable:
        args.append("--require-promotable")

    return {
        "name": case.name,
        "ok": all(checks.values()),
        "simulated_command_args": args,
        "returncode": returncode,
        "base_exit_code": base_exit_code,
        "expected_returncode": case.expected_returncode,
        "checks": checks,
        "captured_stdout_bytes": len(captured_stdout.encode("utf-8")),
        "stdout_report": report,
    }


def main() -> int:
    root = repo_root()
    report_dir = output_root(root)

    mode_reports: dict[str, tuple[dict[str, Any], int, str]] = {}
    for result_mode in sorted({case.result_mode for case in command_cases()}):
        mode_reports[result_mode] = run_mode(root, result_mode)

    cases = [
        evaluate_case(
            case=case,
            report=mode_reports[case.result_mode][0],
            base_exit_code=mode_reports[case.result_mode][1],
            captured_stdout=mode_reports[case.result_mode][2],
        )
        for case in command_cases()
    ]
    failures = [case for case in cases if not case["ok"]]

    summary = {
        "mode": "rag_generated_editor_patch_artifact_mode_smoke",
        "ok": not failures,
        "repo_root": str(root),
        "report_dir": str(report_dir),
        "case_count": len(cases),
        "passed_case_count": len(cases) - len(failures),
        "failed_case_count": len(failures),
        "cases": cases,
    }

    report_path = report_dir / "final_report.json"
    report_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"\nWrote report: {report_path}")
    if failures:
        print(json.dumps({"ok": False, "failures": failures}, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
