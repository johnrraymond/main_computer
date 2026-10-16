#!/usr/bin/env python3
r"""Test the single-writer Hub harness failure and WHETHER REAL retries repair it.

The smoke imports and invokes the current repository's Harness._write_state().
It runs two isolated phases in ONE Python process on the SAME filesystem:

  BASELINE: unmodified harness, repeated verified -> final-inspect, stop on
            the first os.replace failure (or --baseline-max-cycles).
  RETRY:    the SAME harness method, SAME two writes, but with ONLY its
            os.replace call wrapped to retry real WinError 5/32 denials.
            Keep running for --retry-cycles or until unrecoverable failure.

NO injected errors, file locks, competing processes, deliberate readers, Hub
calls, deployment, or changes to the real historical state. The retry phase
cannot claim a demonstrated recovery unless Windows actually refuses a replace
and the SAME replacement subsequently succeeds.

PowerShell:
    python .\tools\hub_harness_state_replace_smoke.py `
      --run-dir .\runtime\state\hub\harness-runs\20261015T000046Z-24780-533041400 `
      --baseline-max-cycles 10000 --retry-cycles 10000 --keep
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_harness(repo_root: Path) -> Any:
    source = repo_root / "hub_mutate_harness.py"
    if not source.is_file():
        raise RuntimeError(f"Missing Hub harness source: {source}")
    spec = importlib.util.spec_from_file_location("hub_mutate_harness_smoke_source", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def state_bytes(state: dict[str, Any]) -> bytes:
    return (json.dumps(state, sort_keys=True, indent=2) + "\n").encode("utf-8")


def file_evidence(path: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {"exists": False}
    except OSError as exc:
        return {"exists": "unknown", "read_error": str(exc)}
    result: dict[str, Any] = {
        "exists": True,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    try:
        state = json.loads(data)
        result["last_completed_step"] = (
            state.get("last_completed_step") if isinstance(state, dict) else None
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        result["json_error"] = str(exc)
    return result


def error_details(exc: OSError) -> dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "winerror": getattr(exc, "winerror", None),
        "errno": getattr(exc, "errno", None),
        "message": str(exc),
    }


class ReplaceRetryProxy:
    """Substitutes ONLY module-global os.replace in Harness._write_state().

    The real implementation still writes its own temp file and calls os.replace.
    The wrapper invokes the original OS primitive, retries WinError 5/32 against
    the very same temporary file, and records ACTUAL kernel-returned failures.
    No monkeypatch of the process-wide os module and no synthetic exceptions.
    """

    def __init__(self, real_os: Any, *, max_retries: int, base_ms: float,
                 cap_ms: float) -> None:
        self.real_os = real_os
        self.max_retries = max_retries
        self.base_ms = base_ms
        self.cap_ms = cap_ms
        self.denials: Counter[int] = Counter()
        self.recovered_replacements = 0
        self.exhausted_replacements = 0
        self.total_retries = 0
        self.highest_retries_on_one_replace = 0
        self.examples: list[dict[str, Any]] = []
        self.recovered_examples: list[dict[str, Any]] = []
        self.cycle = 0
        self.stage = ""

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real_os, name)

    def replace(self, src: Any, dst: Any) -> None:
        retries = 0
        first_denial_at: float | None = None
        while True:
            try:
                self.real_os.replace(src, dst)
            except OSError as exc:
                winerror = getattr(exc, "winerror", None)
                # Do not disguise permanent permission errors or unexpected IO.
                if self.real_os.name != "nt" or winerror not in (5, 32):
                    raise
                self.denials[winerror] += 1
                if first_denial_at is None:
                    first_denial_at = time.monotonic()
                if len(self.examples) < 50:
                    self.examples.append({
                        "cycle": self.cycle,
                        "stage": self.stage,
                        "attempt": retries + 1,
                        "winerror": winerror,
                        "message": str(exc),
                    })
                if retries >= self.max_retries:
                    self.exhausted_replacements += 1
                    raise
                retries += 1
                self.total_retries += 1
                delay_ms = min(self.cap_ms, self.base_ms * (2 ** min(retries - 1, 20)))
                time.sleep(delay_ms / 1000.0)
            else:
                if retries:
                    self.recovered_replacements += 1
                    if len(self.recovered_examples) < 50:
                        self.recovered_examples.append({
                            "cycle": self.cycle, "stage": self.stage,
                            "same_temp_path_retried": str(src),
                            "destination": str(dst),
                            "retries_before_success": retries,
                            "recovery_ms": round(1000.0 * (time.monotonic() - first_denial_at), 3)
                                if first_denial_at is not None else None,
                        })
                    self.highest_retries_on_one_replace = max(
                        self.highest_retries_on_one_replace, retries
                    )
                return

    def summary(self) -> dict[str, Any]:
        return {
            "actual_windows_denials": dict(self.denials),
            "total_retry_attempts": self.total_retries,
            "recovered_replacements": self.recovered_replacements,
            "exhausted_replacements": self.exhausted_replacements,
            "max_retries_needed_for_one_replacement": self.highest_retries_on_one_replace,
            "denial_examples": self.examples,
            "recovery_examples": self.recovered_examples,
        }


def phase(module: Any, initial_state: dict[str, Any], scratch: Path, *,
          name: str, cycles_limit: int, report_every: int,
          proxy: ReplaceRetryProxy | None = None) -> dict[str, Any]:
    target_dir = scratch / name
    target_dir.mkdir(parents=True, exist_ok=False)
    target = target_dir / "harness-state.json"
    temp = target.with_name(target.name + f".tmp-{os.getpid()}")
    harness = object.__new__(module.Harness)  # no Harness.__init__: no deployment
    harness.run_dir = target_dir
    harness.state = json.loads(json.dumps(initial_state))
    real_os = module.os
    if proxy is not None:
        module.os = proxy  # module-local substitution, NOT global os.replace
    cycles = 0
    written = 0
    failure: dict[str, Any] | None = None
    last_marker: str | None = None
    started = time.monotonic()
    interrupted = False
    try:
        for cycle in range(1, cycles_limit + 1):
            for stage, marker in (("verified", None), ("final-inspect", "final-inspect")):
                harness.state["last_completed_step"] = marker
                if proxy is not None:
                    proxy.cycle, proxy.stage = cycle, stage
                try:
                    harness._write_state()  # ACTUAL source method; same twice per cycle
                except OSError as exc:
                    failure = {
                        "cycle": cycle,
                        "stage": stage,
                        "write_number": written + 1,
                        **error_details(exc),
                        "destination_evidence": file_evidence(target),
                        "temporary_evidence": file_evidence(temp),
                    }
                    print(f"{name.upper()}_FAILURE: cycle={cycle} stage={stage} "
                          f"write={written + 1} {type(exc).__name__} "
                          f"WinError={getattr(exc, 'winerror', None)}", flush=True)
                    break
                written += 1
                last_marker = marker
            if failure:
                break
            cycles += 1
            if cycles % report_every == 0:
                recovery = "" if proxy is None else (
                    f" real_denials={sum(proxy.denials.values())} "
                    f"recovered={proxy.recovered_replacements}"
                )
                print(f"{name.upper()}_PROGRESS: cycles={cycles} writes={written}" +
                      recovery, flush=True)
    except KeyboardInterrupt:
        interrupted = True
        print(f"{name.upper()}_INTERRUPTED: Ctrl+C", flush=True)
    finally:
        module.os = real_os

    try:
        final_bytes = target.read_bytes() if target.is_file() else None
    except OSError:
        final_bytes = None  # Evidence below retains the read failure.
    # If failure occurs, harness.state describes the failed write, so
    # compare against the LAST SUCCESSFUL marker, not the failed state.
    saved_state = dict(harness.state)
    saved_state["last_completed_step"] = last_marker
    committed_match = (
        final_bytes == state_bytes(saved_state) if written else final_bytes is None
    )
    # If a failed replacement somehow changed the destination, flag that too.
    success = not failure and not interrupted and cycles == cycles_limit
    result = {
        "name": name,
        "status": "FAIL" if failure else ("INTERRUPTED" if interrupted else "LIMIT_REACHED"),
        "cycles_completed": cycles,
        "successful_writes": written,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "failure": failure,
        "destination": str(target),
        "destination_evidence": file_evidence(target),
        "temporary_evidence": file_evidence(temp),
        "last_successful_marker": last_marker,
        "last_committed_bytes_exact": committed_match,
        "completed_target": success,
    }
    if proxy is not None:
        result["retries"] = proxy.summary()
    if not committed_match:
        print(f"{name.upper()}_INTEGRITY_FAILURE: destination does not match last committed state", flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="Historical run directory, accessed READ-ONLY")
    parser.add_argument("--repo-root", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--baseline-max-cycles", type=int, default=10000)
    parser.add_argument("--retry-cycles", type=int, default=10000)
    parser.add_argument("--max-retries", type=int, default=12,
                        help="Retry count AFTER the first failed replace attempt")
    parser.add_argument("--retry-base-ms", type=float, default=2.0)
    parser.add_argument("--retry-cap-ms", type=float, default=50.0)
    parser.add_argument("--report-every", type=int, default=1000)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    if (args.baseline_max_cycles < 1 or args.retry_cycles < 1 or
            args.max_retries < 0 or args.retry_base_ms < 0 or
            args.retry_cap_ms < args.retry_base_ms or args.report_every < 1):
        parser.error("invalid cycles, retry, backoff, or reporting limits")
    root = args.run_dir.resolve()
    source = root / "harness-state.json"
    if not source.is_file():
        parser.error(f"Original state missing: {source}")
    state = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        parser.error("Historical state must be a JSON object")
    before_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    module = load_harness(args.repo_root.resolve())
    scratch = Path(tempfile.mkdtemp(prefix=".write-state-retry-proof-", dir=root))
    print(f"HARNESS_SOURCE: {args.repo_root.resolve() / 'hub_mutate_harness.py'}", flush=True)
    print(f"SOURCE_STATE: {source} (READ ONLY)", flush=True)
    print(f"SCRATCH: {scratch}", flush=True)
    print("MODE: ONE process; original Harness._write_state(); NO injected interference", flush=True)
    print("PATTERN: verified -> final-inspect -> repeat", flush=True)
    print("BASELINE: real unmodified write, until first error or cycle limit", flush=True)
    baseline = phase(module, state, scratch, name="baseline",
                     cycles_limit=args.baseline_max_cycles, report_every=args.report_every)
    proxy = ReplaceRetryProxy(os, max_retries=args.max_retries,
                              base_ms=args.retry_base_ms, cap_ms=args.retry_cap_ms)
    print(f"RETRY: same actual method, intercepting ONLY os.replace; "
          f"max_retries={args.max_retries} backoff_ms={args.retry_base_ms}..{args.retry_cap_ms}",
          flush=True)
    retry = phase(module, state, scratch, name="retry",
                  cycles_limit=args.retry_cycles, report_every=args.report_every,
                  proxy=proxy)
    after_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    historical_unchanged = before_sha == after_sha
    baseline_reproduced = bool(baseline["failure"] and
                               baseline["failure"].get("winerror") in (5, 32))
    retry_recovered_real_error = proxy.recovered_replacements > 0
    retry_passed = (retry["completed_target"] and retry["last_committed_bytes_exact"] and
                    proxy.exhausted_replacements == 0)
    if not historical_unchanged or not baseline["last_committed_bytes_exact"] or not retry["last_committed_bytes_exact"]:
        verdict = "FAIL_INTEGRITY"
    elif not retry_passed:
        verdict = "FAIL_RETRY"
    elif baseline_reproduced and retry_recovered_real_error:
        verdict = "PASS_REAL_WINDOWS_DENIALS_RECOVERED"
    elif baseline_reproduced:
        verdict = "INCONCLUSIVE_RETRY_NO_DENIAL_OBSERVED"
    elif retry_recovered_real_error:
        verdict = "PASS_REAL_WINDOWS_DENIALS_RECOVERED_BASELINE_NO_FAILURE"
    else:
        verdict = "INCONCLUSIVE_NO_REAL_DENIAL_OBSERVED"
    report = {
        "utc_finished": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "source_state": str(source),
        "historical_state_unchanged": historical_unchanged,
        "scratch": str(scratch),
        "pid": os.getpid(),
        "windows": os.name == "nt",
        "baseline": baseline,
        "retry": retry,
        "configuration": {
            "baseline_max_cycles": args.baseline_max_cycles,
            "retry_cycles": args.retry_cycles,
            "max_retries": args.max_retries,
            "retry_base_ms": args.retry_base_ms,
            "retry_cap_ms": args.retry_cap_ms,
        },
    }
    report_file = scratch / "smoke-report.json"
    report_file.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"BASELINE_RESULT: {baseline['status']} writes={baseline['successful_writes']} "
          f"integrity={baseline['last_committed_bytes_exact']}", flush=True)
    print(f"RETRY_RESULT: {retry['status']} writes={retry['successful_writes']} "
          f"real_denials={sum(proxy.denials.values())} "
          f"recovered_replacements={proxy.recovered_replacements} "
          f"exhausted={proxy.exhausted_replacements} "
          f"integrity={retry['last_committed_bytes_exact']}", flush=True)
    print(f"VERDICT: {verdict}", flush=True)
    print(f"REPORT: {report_file}", flush=True)
    if not args.keep and verdict.startswith("PASS_"):
        shutil.rmtree(scratch)
        print("CLEANUP: disposable files removed; report was inside scratch", flush=True)
    if verdict.startswith("PASS_"):
        return 0
    if verdict.startswith("INCONCLUSIVE_"):
        return 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
