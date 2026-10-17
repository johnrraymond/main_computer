#!/usr/bin/env python3
"""Fail-closed MCEL Phase One gate: compile -> project -> independent app proof.

This is orchestration, NOT an additional proof authority. Every verdict is
issued by the existing MCEL authorities, and the final proof always refreshes
acceptance and browser observation rather than reusing historical evidence.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

SCHEMA = "mcel.phase-one-execution.v1"
STAGES = (
    ("compile", "tools/mcel_app_compile.py"),
    ("project", "tools/mcel_app_project.py"),
    ("prove", "tools/mcel_app_prove.py"),
)


def _execute(command: Sequence[str], repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), cwd=repo, text=True, encoding="utf-8",
        errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )


def _parse_payload(output: str) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def run_phase_one(
    *,
    repo_root: Path,
    app_id: str,
    headed: bool = False,
    execute: Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]] = _execute,
) -> dict[str, Any]:
    """Return a full verdict only after three independent commands succeed."""
    repo = repo_root.resolve()
    stages: dict[str, dict[str, Any]] = {name: {"status": "not-run"} for name, _ in STAGES}
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "appId": app_id,
        "status": "fail",
        "passed": False,
        "stages": stages,
    }
    fingerprint: str | None = None
    host_bound = False
    for stage, source in STAGES:
        if stage == "prove" and host_bound:
            source = "tools/mcel_phase1_host_bound_prove.py"
        command = [sys.executable, str(repo / source), "--repo-root", str(repo), "--app", app_id]
        if stage == "prove":
            command.append("--check")
            if headed:
                command.append("--headed")
        command.append("--json")
        try:
            completed = execute(command, repo)
        except OSError as exc:
            stages[stage] = {"status": "fail", "error": str(exc)}
            report["failedStage"] = stage
            return report
        payload = _parse_payload(completed.stdout)
        stage_report: dict[str, Any] = {"status": "fail", "exitCode": completed.returncode}
        if payload is None:
            stage_report["error"] = (completed.stderr or completed.stdout).strip()[-3000:] or "No machine-readable report."
        elif payload.get("appId") != app_id:
            stage_report["error"] = "MCEL authority returned evidence for a different application."
        elif completed.returncode != 0:
            stage_report["error"] = str(payload.get("error") or payload.get("diagnostics") or "Authority exited nonzero.")[-3000:]
        elif stage in ("compile", "project"):
            observed = str(payload.get("semanticFingerprint") or "")
            if payload.get("valid") is not True or payload.get("status") != "pass":
                stage_report["error"] = "Compile/project authority did not report valid pass."
            elif not observed.startswith("sha256:"):
                stage_report["error"] = "The semantic fingerprint is missing."
            elif stage == "compile" and payload.get("sourceAuthority") != "mcel.dsl.v1":
                stage_report["error"] = "The application does not have authoritative DSL source."
            elif stage == "project" and observed != fingerprint:
                stage_report["error"] = "Compiler and projector disagree on the semantic fingerprint."
            else:
                fingerprint = observed
                if stage == "project":
                    projected = (payload.get("projection") or {})
                    runtime = (projected.get("projection") or {})
                    authority = (projected.get("authority") or {})
                    host_bound = (runtime.get("hostBoundRuntimeActive") is True
                                  and authority.get("hostBoundRuntimeActive") is True)
                stage_report = {"status": "pass", "exitCode": 0, "semanticFingerprint": observed}
        elif stage == "prove":
            expected_truth = (
                "host-bound-ir-native-authoritative" if host_bound
                else "semantic-runtime-proven"
            )
            coverage = payload.get("intentCoverage")
            observed = str(payload.get("semanticFingerprint") or "")
            if (payload.get("status") != "pass"
                    or payload.get("truthStatus") != expected_truth
                    or not isinstance(coverage, dict) or coverage.get("passed") is not True
                    or not isinstance(payload.get("evidence"), dict)
                    or (host_bound and observed != fingerprint)):
                stage_report["error"] = f"Independent {expected_truth} proof was not established."
            else:
                stage_report = {
                    "status": "pass", "exitCode": 0,
                    "truthStatus": expected_truth,
                    "evidence": payload["evidence"],
                }
        else:
            stage_report["error"] = "Unsupported Phase One stage."
        stages[stage] = stage_report
        if stage_report["status"] != "pass":
            report["failedStage"] = stage
            return report
    report["status"] = "pass"
    report["passed"] = True
    report["semanticFingerprint"] = fingerprint
    report["proofScope"] = (
        "host-bound-ir-native-authoritative" if host_bound else "package-host-semantic-runtime-proven"
    )
    # An IR-native host-bound parity proof is narrower than the full package-host
    # surface and truth-gate proof. Never silently report the two as equivalent.
    report["fullSemanticRuntimeProven"] = not host_bound
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", default="calculator")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--headed", action="store_true", help="Use headed Chromium observation in the proof step.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = run_phase_one(repo_root=args.repo_root, app_id=args.app, headed=args.headed)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(f"MCEL Phase One: {args.app} — {result['status']}")
        for stage, details in result["stages"].items():
            print(f"  {stage}: {details['status']}")
            if details.get("error"):
                print(f"    {details['error']}")
        if result["passed"]:
            print(f"  Verified proof scope: {result['proofScope']}")
        else:
            print("  Semantic runtime proof: NOT ESTABLISHED")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
