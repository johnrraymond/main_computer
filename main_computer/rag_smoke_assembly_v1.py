#!/usr/bin/env python3
"""Versioned contract-driven RAG smoke assembly.

This is a new assembly path, not a replacement for the existing ``rag_*_smoke``
crop.  The top-level runner does not perform the smoke work directly.  It
loads each subscript's contract, checks required state, executes the subscript
in a subprocess, validates the declared outputs, and writes a replayable trace.

Default run:

    python -m main_computer.rag_smoke_assembly_v1 --repo-dir . --output-dir diagnostics_output/rag_smoke_assembly_v1
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable

ASSEMBLY_VERSION = "rag_smoke_assembly_v1"

SCRIPT_MODULES: tuple[str, ...] = (
    "main_computer.rag_smoke_subscripts_v1.repo_shape",
    "main_computer.rag_smoke_subscripts_v1.retriever_contract",
    "main_computer.rag_smoke_subscripts_v1.framework_goldset",
    "main_computer.rag_smoke_subscripts_v1.harness_no_model",
)


@dataclass(frozen=True)
class AssemblyStepRecord:
    step_id: str
    module: str
    status: str
    contract: dict[str, Any]
    result: dict[str, Any] | None = None
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    validation_errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["validation_errors"] = list(self.validation_errors)
        return data


@dataclass(frozen=True)
class AssemblyRunResult:
    version: str
    ok: bool
    repo_dir: str
    output_dir: str
    trace_path: str
    final_state: dict[str, Any] = field(default_factory=dict)
    steps: tuple[AssemblyStepRecord, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "ok": self.ok,
            "repo_dir": self.repo_dir,
            "output_dir": self.output_dir,
            "trace_path": self.trace_path,
            "final_state": self.final_state,
            "steps": [step.as_dict() for step in self.steps],
        }


def _json_from_stdout(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        raise ValueError("subprocess emitted no JSON stdout")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("subprocess stdout JSON was not an object")
    return parsed


def _subprocess_env(repo_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    prefix = str(repo_dir)
    env["PYTHONPATH"] = prefix if not existing else prefix + os.pathsep + existing
    return env


def _run_json(module: str, args: Iterable[str], repo_dir: Path) -> tuple[dict[str, Any], str, str, int]:
    completed = subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=repo_dir,
        env=_subprocess_env(repo_dir),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    try:
        payload = _json_from_stdout(completed.stdout)
    except Exception as exc:
        payload = {
            "status": "fail",
            "step_id": module.rsplit(".", 1)[-1],
            "provided_state": {},
            "evidence": {},
            "details": {"json_parse_error": f"{type(exc).__name__}: {exc}"},
        }
    return payload, completed.stdout, completed.stderr, completed.returncode


def load_contract(module: str, repo_dir: Path) -> dict[str, Any]:
    # Contract loading is intentionally in-process: the contract is static data,
    # while the actual smoke work still runs through the subscript boundary.
    if str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))
    imported = importlib.import_module(module)
    raw_contract = getattr(imported, "CONTRACT", None)
    if raw_contract is None or not hasattr(raw_contract, "as_dict"):
        raise ValueError(f"{module} does not expose a CONTRACT with as_dict()")
    contract = raw_contract.as_dict()
    required = {"step_id", "version", "description", "requires", "provides", "evidence_required"}
    missing = sorted(required - set(contract))
    if missing:
        raise ValueError(f"contract for {module} is missing keys: {missing}")
    if contract.get("version") != ASSEMBLY_VERSION:
        raise ValueError(f"contract for {module} has unexpected version: {contract.get('version')!r}")
    return contract


def _contract_keys(contract: dict[str, Any], section: str) -> list[str]:
    values = contract.get(section)
    if not isinstance(values, list):
        return []
    keys: list[str] = []
    for item in values:
        if isinstance(item, dict) and item.get("key"):
            keys.append(str(item["key"]))
    return keys


def _validate_result(contract: dict[str, Any], result: dict[str, Any], state_before: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = _contract_keys(contract, "requires")
    missing_state = [key for key in required if key not in state_before]
    if missing_state:
        errors.append(f"missing required input state before run: {missing_state}")

    step_id = str(contract.get("step_id") or "")
    if result.get("step_id") != step_id:
        errors.append(f"step_id mismatch: expected {step_id!r}, got {result.get('step_id')!r}")
    if result.get("status") != "ok":
        errors.append(f"result status was not ok: {result.get('status')!r}")

    provided = result.get("provided_state")
    if not isinstance(provided, dict):
        errors.append("provided_state must be a JSON object")
        provided = {}

    expected_provides = _contract_keys(contract, "provides")
    missing_provides = [key for key in expected_provides if key not in provided]
    if missing_provides:
        errors.append(f"result did not provide declared state: {missing_provides}")

    undeclared_provides = sorted(set(provided) - set(expected_provides))
    if undeclared_provides:
        errors.append(f"result provided undeclared state keys: {undeclared_provides}")

    evidence = result.get("evidence")
    if not isinstance(evidence, dict):
        errors.append("evidence must be a JSON object")
        evidence = {}
    for key in contract.get("evidence_required", []) or []:
        if not evidence.get(key):
            errors.append(f"missing required evidence key: {key}")

    return errors


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_assembly(
    *,
    repo_dir: Path | str = ".",
    output_dir: Path | str | None = None,
    modules: tuple[str, ...] = SCRIPT_MODULES,
) -> AssemblyRunResult:
    repo_path = Path(repo_dir).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise ValueError(f"repo_dir does not exist or is not a directory: {repo_path}")

    out_path = Path(output_dir or (repo_path / "diagnostics_output" / ASSEMBLY_VERSION)).resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    state: dict[str, Any] = {}
    records: list[AssemblyStepRecord] = []
    ok = True

    for index, module in enumerate(modules, start=1):
        contract = load_contract(module, repo_path)
        step_id = str(contract["step_id"])
        step_dir = out_path / f"{index:02d}_{step_id}"
        step_dir.mkdir(parents=True, exist_ok=True)

        state_path = step_dir / "input_state.json"
        _write_json(state_path, state)

        preflight_errors = [
            f"missing required input state before run: {key}"
            for key in _contract_keys(contract, "requires")
            if key not in state
        ]
        if preflight_errors:
            record = AssemblyStepRecord(
                step_id=step_id,
                module=module,
                status="fail",
                contract=contract,
                result=None,
                validation_errors=tuple(preflight_errors),
            )
            records.append(record)
            ok = False
            break

        result, stdout, stderr, returncode = _run_json(
            module,
            ["--repo-dir", str(repo_path), "--state", str(state_path), "--output-dir", str(step_dir)],
            repo_path,
        )
        validation_errors = _validate_result(contract, result, state)
        if returncode != 0 and not validation_errors:
            validation_errors.append(f"subprocess returned non-zero exit code: {returncode}")

        status = "ok" if not validation_errors else "fail"
        record = AssemblyStepRecord(
            step_id=step_id,
            module=module,
            status=status,
            contract=contract,
            result=result,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            validation_errors=tuple(validation_errors),
        )
        records.append(record)
        _write_json(step_dir / "assembly_step_record.json", record.as_dict())

        if validation_errors:
            ok = False
            break

        provided_state = result.get("provided_state") if isinstance(result.get("provided_state"), dict) else {}
        state.update(provided_state)

    trace_path = out_path / "assembly_trace.json"
    trace_payload = {
        "schema_version": 1,
        "version": ASSEMBLY_VERSION,
        "ok": ok,
        "repo_dir": str(repo_path),
        "output_dir": str(out_path),
        "final_state": state,
        "steps": [record.as_dict() for record in records],
    }
    _write_json(trace_path, trace_payload)

    return AssemblyRunResult(
        version=ASSEMBLY_VERSION,
        ok=ok,
        repo_dir=str(repo_path),
        output_dir=str(out_path),
        trace_path=str(trace_path),
        final_state=state,
        steps=tuple(records),
    )


def list_contracts(repo_dir: Path | str = ".") -> list[dict[str, Any]]:
    repo_path = Path(repo_dir).resolve()
    return [load_contract(module, repo_path) for module in SCRIPT_MODULES]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-dir", default=".", help="Repository root to smoke-test.")
    parser.add_argument("--output-dir", default=None, help="Assembly output directory.")
    parser.add_argument("--list", action="store_true", help="Print subscript contracts and exit.")
    parser.add_argument("--json", action="store_true", help="Print the full assembly result JSON.")
    args = parser.parse_args(argv)

    if args.list:
        print(json.dumps(list_contracts(args.repo_dir), indent=2, sort_keys=True))
        return 0

    result = run_assembly(repo_dir=args.repo_dir, output_dir=args.output_dir)
    payload = result.as_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"version: {result.version}")
        print(f"ok: {result.ok}")
        print(f"steps: {len(result.steps)}")
        print(f"trace: {result.trace_path}")
        if not result.ok:
            failed = [step for step in result.steps if step.status != "ok"]
            if failed:
                print(f"failed_step: {failed[0].step_id}")
                print(f"errors: {list(failed[0].validation_errors)}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
