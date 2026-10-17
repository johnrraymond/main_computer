#!/usr/bin/env python3
"""Run two independent, explicitly scoped Phase One MCEL application checks.

Calculator: existing host-bound IR-native authority.
Code Editor: generated-adapter UI browser observation with real isolated
transaction-backed persistence and stale-write refusal.
Neither check establishes the full semantic-runtime truth gate for both apps.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

SCHEMA = "mcel.phase-one-multi-application-behavior.v1"
CODE_EDITOR_TESTS = (
    "tests/test_mcel_code_editor_ui_behavior_proof.py",
    "tests/test_mcel_code_editor_authorized_write_proof.py",
    "tests/test_mcel_code_editor_write_receipt_validation.py",
    "tests/test_mcel_host_bound_generic_runtime_binding.py",
    "tests/test_mcel_code_editor_generated_adapter_browser_binding.py",
)


def _invoke(repo: Path, args: Sequence[str], timeout: int = 240) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, check=False)


def _json_pass(result: subprocess.CompletedProcess[str], app_id: str) -> dict[str, Any]:
    if result.returncode != 0:
        raise ValueError(f"{app_id} command exited {result.returncode}: "
                         + (result.stderr or result.stdout)[-1800:])
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{app_id} authority did not return JSON.") from exc
    if not isinstance(report, dict) or report.get("appId") != app_id or report.get("status") != "pass":
        raise ValueError(f"{app_id} authority returned nonpassing or mismatched evidence.")
    return report


def _require_pytest_proof(result: subprocess.CompletedProcess[str]) -> int:
    output = result.stdout + "\n" + result.stderr
    if result.returncode:
        raise ValueError("Code Editor independent browser proof failed: " + output[-2000:])
    # A missing browser or Node must not turn the proof green via pytest skip.
    match = re.search(r"(?:^|\s)(\d+) passed\b", output)
    if not match or re.search(r"(?:^|\s)\d+ skipped\b", output):
        raise ValueError("Code Editor browser proof did not execute every required test: " + output[-1800:])
    count = int(match.group(1))
    if count < 13:
        raise ValueError("Code Editor proof ran fewer than thirteen required behavioral/binding checks.")
    return count


def run(repo: Path, headed: bool = False) -> dict[str, Any]:
    repo = repo.resolve()
    report: dict[str, Any] = {
        "schema": SCHEMA, "status": "fail", "passed": False,
        "applications": {"calculator": {"status": "not-run"}, "code-editor": {"status": "not-run"}},
        "fullSemanticRuntimeProven": False,
    }
    try:
        for phase, tool in (("compile", "tools/mcel_app_compile.py"),
                            ("project", "tools/mcel_app_project.py")):
            command = [sys.executable, str(repo / tool), "--app", "code-editor", "--json"]
            payload = _json_pass(_invoke(repo, command), "code-editor")
            fingerprint = payload.get("semanticFingerprint")
            if payload.get("valid") is not True or not isinstance(fingerprint, str) or not fingerprint.startswith("sha256:"):
                raise ValueError(f"Code Editor {phase} lacks valid semantic fingerprint.")
            if phase == "compile":
                compiled_fingerprint = fingerprint
                if payload.get("sourceAuthority") != "mcel.dsl.v1":
                    raise ValueError("Code Editor source is not DSL authoritative.")
            elif fingerprint != compiled_fingerprint:
                raise ValueError("Code Editor compiler and projector fingerprints disagree.")
        # Run actual Chromium and generated ESM adapter tests, not a synthetic
        # result substituted for application behavior.
        suite = _invoke(repo, [sys.executable, "-m", "pytest", "-q", *CODE_EDITOR_TESTS])
        count = _require_pytest_proof(suite)
        report["applications"]["code-editor"] = {
            "status": "pass", "semanticFingerprint": compiled_fingerprint,
            "proofScope": "isolated-generated-adapter-ui-with-real-transaction-backed-save",
            "testsPassed": count, "externalWriteProven": True,
            "writeReceiptVerified": True, "sourceDriftRefusalVerified": True,
            "writeTransport": "isolated-browser-bridge-to-real-viewport-server",
        }
        calculator = [sys.executable, str(repo / "tools/mcel_phase1_gate.py"),
                      "--app", "calculator", "--json"]
        if headed:
            calculator.append("--headed")
        payload = _json_pass(_invoke(repo, calculator), "calculator")
        if (payload.get("passed") is not True
                or payload.get("proofScope") != "host-bound-ir-native-authoritative"
                or payload.get("fullSemanticRuntimeProven") is not False
                or not str(payload.get("semanticFingerprint", "")).startswith("sha256:")):
            raise ValueError("Calculator host-bound IR-native proof scope is not established.")
        report["applications"]["calculator"] = {
            "status": "pass", "semanticFingerprint": payload["semanticFingerprint"],
            "proofScope": payload["proofScope"],
        }
        report["passed"] = True
        report["status"] = "pass"
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        report["error"] = str(exc)[-2500:]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args()
    report = run(options.repo_root, options.headed)
    if options.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"MCEL multi-application behavior: {report['status']}")
        for app_id, details in report["applications"].items():
            print(f"  {app_id}: {details['status']}")
        if report.get("error"):
            print(report["error"])
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
