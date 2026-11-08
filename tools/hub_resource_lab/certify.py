"""Fail-closed cumulative Hub Network Resource Lab acceptance (Patch 2F).

Runs only disposable loopback protocol, admission, relay and paid-work labs.
Full certification additionally requires the pre-existing LOCAL FoundationDB crash
lab and a separate deployed-revision audit. It cannot report PASS without both.

    python -m tools.hub_resource_lab.certify --mode preflight
    python -m tools.hub_resource_lab.certify --mode local
    python -m tools.hub_resource_lab.certify --mode full

No live blockchain operations, production credentials or production endpoints.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any

from main_computer.hub_resource_limits import load_hub_resource_policy

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "main-computer.hub-resource-certification.v1"
PINNED_FILES = (
    "main_computer/hub.py",
    "main_computer/stable_hub.py",
    "main_computer/exp_fdb_hub.py",
    "main_computer/hub_resource_limits.py",
    "main_computer/stable_hub_msk.py",
    "main_computer/stable_hub_worker_sessions.py",
    "main_computer/model_execution_contract.py",
    "main_computer/seh/relay.py",
    "main_computer/seh/stream_writer.py",
    "main_computer/slh/managed_gateway.py",
    "main_computer/slh/authenticator_store.py",
    "main_computer/slh/seh_transport.py",
    "tools/hub_resource_lab/run_lab.py",
    "tools/hub_resource_lab/relay_lab.py",
    "tools/slh_seh_lab/integrated_stress.py",
    "tools/hub_crash_lab/run_lab.py",
)
MANDATORY_LOCAL = ("R1_R2_R3_BOUNDARIES", "R3_R4_ADMISSION", "R7_RELAY",
                   "R5_R6_PAID_AND_RECONNECT", "SUPPORTING_REGRESSIONS")
MANDATORY_FULL = (*MANDATORY_LOCAL, "R6_DURABLE_CRASH_FAILOVER", "DEPLOYED_REVISION")
REGRESSION_FILES = (
    "tests/test_hub_resource_limits.py",
    "tests/test_hub_resource_admission.py",
    "tests/test_hub_resource_relay.py",
    "tests/test_seh_stream_write_queues.py",
    "tests/test_hub_resource_certification.py",
    "tests/test_stable_msk_lifecycle_security.py",
    "tests/test_stable_hub_financial_privacy.py",
    "tests/test_stable_hub_payload_retention.py",
    "tests/test_worker_execution_trust_boundary.py",
    "tests/test_slh_private_key_acl.py",
    "tests/test_financial_authority_closure.py",
)


def source_fingerprints(root: Path = ROOT) -> dict[str, str]:
    result = {}
    for rel in PINNED_FILES:
        path = root / rel
        if not path.is_file():
            raise FileNotFoundError(f"Required acceptance source absent: {rel}")
        result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def acceptance_status(gates: dict[str, dict[str, Any]], *, full: bool = True) -> str:
    required = MANDATORY_FULL if full else MANDATORY_LOCAL
    values = [gates.get(key, {}).get("status", "INCOMPLETE") for key in required]
    if any(value == "FAIL" for value in values):
        return "FAIL"
    if any(value != "PASS" for value in values):
        return "INCOMPLETE"
    return "PASS"


def _run_stage(label: str, cmd: list[str], output: Path, timeout: int,
               *, evidence: Path | None = None, json_evidence: bool = False, paid_evidence: bool = False) -> dict[str, Any]:
    # Child subprocesses are fully waited for, with a hard timeout; no detached
    # watchdog or asynchronous work survives this invocation.
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              timeout=timeout, check=False, errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "FAIL", "reason": type(exc).__name__, "label": label}
    output.parent.mkdir(parents=True, exist_ok=True)
    # The per-stage log is lab-local. Restrict its size to avoid unauthenticated
    # Worker diagnostic output being copied into a potentially shared report.
    log = (proc.stdout + "\n" + proc.stderr)[-12000:]
    output.write_text(log, encoding="utf-8")
    result = {"status": "FAIL", "exit_code": proc.returncode,
              "log_path": str(output.relative_to(ROOT)) if output.is_relative_to(ROOT) else str(output),
              "label": label}
    if proc.returncode != 0:
        result["reason"] = "CHILD_EXIT_NONZERO"
        # Preserve precise failures in the certification summary. Previously
        # Windows' connection-admission failures appeared only as a generic
        # exit code, although the child had produced an assertion-level JSON.
        if json_evidence and evidence is not None and evidence.is_file():
            try:
                payload = json.loads(evidence.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
            else:
                if payload.get("result") == "FAIL" and isinstance(payload.get("tests"), list):
                    failed = [
                        {key: item[key] for key in ("hub", "name", "expected", "observed")
                         if key in item}
                        for item in payload["tests"] if item.get("passed") is False
                    ]
                    result.update(reason="LAB_ASSERTIONS_FAILED", checks=payload.get("passed"),
                                  total=payload.get("total"), failed_assertions=failed[:32],
                                  evidence=str(evidence))
        if label == "supporting_regressions":
            result["failed_tests"] = re.findall(r"^FAILED\s+(.+)$", log, re.MULTILINE)[:32]
        return result
    if json_evidence:
        try:
            payload = json.loads((evidence or output).read_text(encoding="utf-8"))
        except (ValueError, OSError):
            result["reason"] = "MISSING_OR_INVALID_LAB_EVIDENCE"
            return result
        if payload.get("result") != "PASS" or payload.get("passed") != payload.get("total") or not payload.get("total"):
            result["reason"] = "LAB_ASSERTIONS_NOT_ALL_PASSED"
            return result
        result.update(status="PASS", checks=payload["passed"], total=payload["total"],
                      evidence=str((evidence or output)))
    elif paid_evidence:
        # The paid-stress command writes a JSON report to the child report dir;
        # the parent must independently inspect it, not rely on an exit code.
        report_files = list((evidence or output.parent).glob("**/*.json"))
        reports = []
        for f in report_files:
            try:
                obj = json.loads(f.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if obj.get("mode") == "stress-integrated" and obj.get("schema", "").startswith("main-computer"):
                reports.append((f, obj))
        if not reports:
            result["reason"] = "PAID_STRESS_REPORT_MISSING"
        else:
            f, data = sorted(reports, key=lambda item: str(item[0]))[-1]
            if data.get("status") == "PASS" and data.get("pass") is True:
                result.update(status="PASS", evidence=str(f))
            else:
                result["reason"] = "PAID_STRESS_INVARIANT_FAILED"
    else:
        result["status"] = "PASS"
    return result


def _regressions(report_dir: Path) -> dict[str, Any]:
    if not all((ROOT / f).is_file() for f in REGRESSION_FILES):
        return {"status": "FAIL", "reason": "MANDATORY_TEST_SUITE_MISSING"}
    with tempfile.TemporaryDirectory(prefix="hub-resource-pytest-config-") as temp:
        config = Path(temp) / "pytest.ini"
        config.write_text("[pytest]\n", encoding="utf-8")
        cmd = [sys.executable, "-m", "pytest", "-q", "-c", str(config),
               "--confcutdir=tests", *REGRESSION_FILES]
        result = _run_stage("supporting_regressions", cmd,
                            report_dir / "supporting-regressions.log", 180)
    if result["status"] == "FAIL":
        return result
    log = (report_dir / "supporting-regressions.log").read_text(encoding="utf-8")
    match = re.search(r"(\d+) passed", log)
    if not match:
        return {**result, "status": "FAIL", "reason": "PYTEST_PASS_COUNT_MISSING"}
    return {**result, "status": "PASS", "tests_passed": int(match.group(1))}


def _crash(report_dir: Path, cluster_file: Path) -> dict[str, Any]:
    if not cluster_file.is_file():
        return {"status": "INCOMPLETE", "reason": "LOCAL_FOUNDATIONDB_CLUSTER_FILE_MISSING"}
    # The existing crash-lab tool additionally validates Docker locality and
    # uses only its disposable lab namespace. Never bypass that preflight.
    cmd = [sys.executable, "-m", "tools.hub_crash_lab.run_lab", "--mode", "failover",
           "--hub", "stable", "--cluster-file", str(cluster_file),
           "--report-dir", str(report_dir / "durable-failover"), "--json"]
    result = _run_stage("durable_crash_failover", cmd, report_dir / "r6-failover.log", 960)
    return result


def run_certification(*, mode: str, report_dir: Path,
                      cluster_file: Path = ROOT / ".foundationdb/docker.cluster") -> dict[str, Any]:
    if mode not in {"preflight", "local", "full"}:
        raise ValueError("mode must be preflight, local or full")
    report_dir = report_dir.resolve()
    pins = source_fingerprints()
    policy_fingerprint = load_hub_resource_policy().fingerprint()
    gates: dict[str, dict[str, Any]] = {}
    if mode != "preflight":
        for label, command in (
            ("R1_R2_R3_BOUNDARIES", "boundaries"),
            ("R3_R4_ADMISSION", "admission"),
            ("R7_RELAY", "relay"),
        ):
            evidence = report_dir / (command + ".json")
            cmd = [sys.executable, "-m", "tools.hub_resource_lab.run_lab",
                   command, "--output", str(evidence)]
            gates[label] = _run_stage(label, cmd, report_dir / (command + ".log"),
                                      120, evidence=evidence, json_evidence=True)
        paid_dir = report_dir / "paid-stress"
        cmd = [sys.executable, "-m", "tools.slh_seh_lab.run_lab", "stress-integrated",
               "--workers", "2", "--requests", "2", "--concurrency", "1",
               "--strict-acceptance", "--json", "--report-dir", str(paid_dir)]
        gates["R5_R6_PAID_AND_RECONNECT"] = _run_stage(
            "paid_stress", cmd, report_dir / "paid-stress.log", 240, evidence=paid_dir, paid_evidence=True)
        gates["SUPPORTING_REGRESSIONS"] = _regressions(report_dir)
    if mode == "full":
        gates["R6_DURABLE_CRASH_FAILOVER"] = _crash(report_dir, cluster_file.resolve())
    else:
        gates["R6_DURABLE_CRASH_FAILOVER"] = {
            "status": "INCOMPLETE", "reason": "NOT_EXECUTED_IN_THIS_MODE"}
    # Deployed acceptance must be a separately evidenced, exact-revision
    # promotion gate. Never infer production deployment from local tests.
    gates["DEPLOYED_REVISION"] = {"status": "INCOMPLETE",
                                  "reason": "DEPLOYED_REVISION_NOT_ATTESTED"}
    if mode == "preflight":
        status = "PLANNED_ONLY"
    else:
        status = acceptance_status(gates)
    result = {"schema": SCHEMA, "status": status, "mode": mode,
              "local_acceptance_status": ("NOT_RUN" if mode == "preflight"
                                          else acceptance_status(gates, full=False)),
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "source_sha256": pins, "policy_sha256": policy_fingerprint,
              "gates": gates, "full_promotion_allowed": status == "PASS"}
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "certification.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fail-closed Hub resource certification")
    p.add_argument("--mode", choices=("preflight", "local", "full"), default="local")
    p.add_argument("--report-dir", type=Path,
                   default=ROOT / "runtime/reports/hub-resource-lab/certification")
    p.add_argument("--cluster-file", type=Path, default=ROOT / ".foundationdb/docker.cluster")
    a = p.parse_args(argv)
    try:
        report = run_certification(mode=a.mode, report_dir=a.report_dir,
                                   cluster_file=a.cluster_file)
    except (OSError, ValueError) as exc:
        print("HUB_RESOURCE_CERTIFICATION_FAIL: " + str(exc))
        return 2
    for gate, value in report["gates"].items():
        print(f"{gate}: {value['status']} {value.get('reason', '')}")
    print("HUB_RESOURCE_LOCAL_ACCEPTANCE_" + report["local_acceptance_status"])
    print("HUB_RESOURCE_CERTIFICATION_" + report["status"])
    print("report:", (a.report_dir / "certification.json").resolve())
    return 0 if report["status"] == "PASS" else (2 if report["status"] == "FAIL" else 3)


if __name__ == "__main__":
    raise SystemExit(main())
