#!/usr/bin/env python3
"""Run repository-bound MCEL acceptance evidence.

The requirements registry is the authority for declared ``mcel-acceptance``
contracts.  This runner loads those contracts, joins them to the narrow
execution mappings in ``mcel_acceptance_bindings.json``, executes pytest
selectors, and writes repository-bound JSON/Markdown evidence.

A contract is proven only when it is currently enforceable, has an explicit
binding, collects tests, and every bound test passes.  Planned/draft contracts
remain visible as future obligations but do not block current acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

try:
    from .mcel_evidence_provenance import build_repository_provenance
except ImportError:  # Direct script execution from the repository root.
    from mcel_evidence_provenance import build_repository_provenance


REPORT_SCHEMA = "mcel-acceptance-evidence-report-v1"
REPORT_VERSION = "mcel-acceptance-runner-v1"
BINDING_SCHEMA = "mcel-acceptance-bindings-v1"
DEFAULT_BINDINGS = Path("main_computer/mcel_acceptance_bindings.json")
DEFAULT_OUTPUT_DIR = Path("runtime/reports/mcel-acceptance")
ENFORCEABLE_STATUSES = frozenset(
    {"specified", "partially-implemented", "implemented", "verified", "current-plus-planned"}
)
NON_ENFORCEABLE_STATUSES = frozenset({"draft", "planned", "open"})
PYTEST_SUMMARY_PATTERN = re.compile(
    r"(?P<count>\d+)\s+(?P<kind>passed|failed|skipped|xfailed|xpassed|errors?|warnings?)"
)


class McelAcceptanceError(RuntimeError):
    """Raised when acceptance evidence cannot be produced truthfully."""


@dataclass(frozen=True)
class Binding:
    binding_id: str
    app_id: str
    contract_id: str
    runner: str
    selectors: tuple[str, ...]
    notes: str


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_string(value: Any) -> str:
    return "" if value is None else str(value).strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def repo_display_path(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def resolve_repo_path(path: Path, repo: Path) -> Path:
    return path if path.is_absolute() else repo / path


def load_requirements_registry(repo: Path):
    tools_dir = repo / "tools"
    if not (tools_dir / "mcel_requirements_registry.py").exists():
        raise McelAcceptanceError(
            "tools/mcel_requirements_registry.py is required to discover acceptance contracts."
        )
    repo_text = str(repo.resolve())
    if repo_text not in sys.path:
        sys.path.insert(0, repo_text)
    try:
        from tools import mcel_requirements_registry as registry_tool
    except ImportError as exc:
        raise McelAcceptanceError(
            "Could not import tools.mcel_requirements_registry."
        ) from exc
    registry = registry_tool.build_registry(repo)
    if not registry.valid:
        messages = "; ".join(issue.message for issue in registry.errors[:5])
        raise McelAcceptanceError(
            "The MCEL requirements registry is invalid"
            + (f": {messages}" if messages else ".")
        )
    return registry


def acceptance_contracts(registry: Any) -> list[Any]:
    return sorted(
        (block for block in registry.blocks if block.block_type == "mcel-acceptance" and block.app),
        key=lambda block: (block.app or "", block.block_id),
    )


def _selector_source_path(selector: str) -> PurePosixPath:
    source = safe_string(selector).split("::", 1)[0].replace("\\", "/")
    path = PurePosixPath(source)
    if (
        not source
        or path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or path.parts[0] != "tests"
        or path.suffix != ".py"
    ):
        raise McelAcceptanceError(
            f"Unsafe or unsupported pytest selector in acceptance binding: {selector!r}"
        )
    return path


def load_bindings(path: Path, repo: Path) -> tuple[dict[str, Binding], dict[str, Any]]:
    resolved = resolve_repo_path(path, repo)
    if not resolved.exists():
        raise McelAcceptanceError(
            f"Acceptance binding catalog does not exist: {repo_display_path(resolved, repo)}"
        )
    raw = resolved.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise McelAcceptanceError("Acceptance binding catalog is not valid UTF-8 JSON.") from exc
    if not isinstance(payload, Mapping) or payload.get("schema") != BINDING_SCHEMA:
        raise McelAcceptanceError(
            f"Acceptance binding catalog must use schema {BINDING_SCHEMA!r}."
        )

    result: dict[str, Binding] = {}
    ids: set[str] = set()
    for index, item in enumerate(payload.get("bindings") or []):
        if not isinstance(item, Mapping):
            raise McelAcceptanceError(f"Acceptance binding #{index + 1} is not an object.")
        binding_id = safe_string(item.get("id"))
        app_id = safe_string(item.get("appId") or item.get("app"))
        contract_id = safe_string(item.get("acceptanceContractId") or item.get("contractId"))
        runner = safe_string(item.get("runner") or "pytest").lower()
        selectors = tuple(safe_string(value) for value in (item.get("selectors") or []) if safe_string(value))
        if not binding_id or not app_id or not contract_id:
            raise McelAcceptanceError(
                f"Acceptance binding #{index + 1} requires id, appId, and acceptanceContractId."
            )
        if binding_id in ids:
            raise McelAcceptanceError(f"Duplicate acceptance binding id: {binding_id}")
        if contract_id in result:
            raise McelAcceptanceError(
                f"Multiple acceptance bindings target the same contract: {contract_id}"
            )
        if runner != "pytest":
            raise McelAcceptanceError(
                f"Unsupported acceptance runner {runner!r} for binding {binding_id}."
            )
        if not selectors:
            raise McelAcceptanceError(
                f"Acceptance binding {binding_id} has no pytest selectors."
            )
        for selector in selectors:
            source = _selector_source_path(selector)
            if not (repo / Path(*source.parts)).is_file():
                raise McelAcceptanceError(
                    f"Acceptance binding {binding_id} references a missing test file: {source.as_posix()}"
                )
        ids.add(binding_id)
        result[contract_id] = Binding(
            binding_id=binding_id,
            app_id=app_id,
            contract_id=contract_id,
            runner=runner,
            selectors=selectors,
            notes=safe_string(item.get("notes")),
        )

    return result, {
        "path": repo_display_path(resolved, repo),
        "schema": safe_string(payload.get("schema")),
        "version": safe_string(payload.get("version")),
        "sha256": sha256_bytes(raw),
        "bindingCount": len(result),
    }


def validate_bindings(contracts: Sequence[Any], bindings: Mapping[str, Binding]) -> None:
    by_id = {block.block_id: block for block in contracts}
    unknown = sorted(set(bindings) - set(by_id))
    if unknown:
        raise McelAcceptanceError(
            "Acceptance bindings reference unknown contracts: " + ", ".join(unknown)
        )
    wrong_apps = [
        binding.binding_id
        for contract_id, binding in bindings.items()
        if safe_string(by_id[contract_id].app) != binding.app_id
    ]
    if wrong_apps:
        raise McelAcceptanceError(
            "Acceptance binding app ids disagree with requirements contracts: "
            + ", ".join(sorted(wrong_apps))
        )


def parse_pytest_summary(output: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for match in PYTEST_SUMMARY_PATTERN.finditer(output):
        kind = match.group("kind").lower()
        if kind == "error":
            kind = "errors"
        elif kind == "warning":
            kind = "warnings"
        counts[kind] += int(match.group("count"))
    return dict(sorted(counts.items()))


def run_pytest_binding(
    *,
    binding: Binding,
    repo: Path,
    extra_pytest_args: Sequence[str] = (),
    timeout_seconds: float = 900.0,
) -> dict[str, Any]:
    started_at = utc_now_iso()
    started = time.monotonic()
    command = [
        sys.executable,
        "-m",
        "pytest",
        *binding.selectors,
        "-q",
        *extra_pytest_args,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
            timeout=max(1.0, float(timeout_seconds)),
        )
        return_code = int(completed.returncode)
        stdout = completed.stdout
        stderr = completed.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        return_code = 124
        stdout = safe_string(exc.stdout)
        stderr = safe_string(exc.stderr)
        timed_out = True

    duration_ms = round((time.monotonic() - started) * 1000)
    summary = parse_pytest_summary("\n".join([stdout, stderr]))
    collected = sum(
        summary.get(key, 0)
        for key in ("passed", "failed", "skipped", "xfailed", "xpassed", "errors")
    )
    if timed_out:
        status = "execution-error"
        reason = "pytest timed out"
    elif return_code == 0 and summary.get("passed", 0) > 0:
        status = "pass"
        reason = ""
    elif return_code == 5 or collected == 0:
        status = "no-tests"
        reason = "pytest did not collect any tests"
    elif return_code == 1 or summary.get("failed", 0) or summary.get("errors", 0):
        status = "fail"
        reason = "one or more bound tests failed"
    else:
        status = "execution-error"
        reason = f"pytest exited with code {return_code}"

    return {
        "status": status,
        "passed": status == "pass",
        "startedAt": started_at,
        "finishedAt": utc_now_iso(),
        "durationMs": duration_ms,
        "returnCode": return_code,
        "command": command,
        "summary": summary,
        "testCount": collected,
        "stdout": stdout[-12000:],
        "stderr": stderr[-12000:],
        "reason": reason,
    }


def contract_result(
    *,
    block: Any,
    binding: Binding | None,
    repo: Path,
    extra_pytest_args: Sequence[str],
    timeout_seconds: float,
    execution_cache: dict[tuple[str, ...], dict[str, Any]],
) -> dict[str, Any]:
    declaration_status = safe_string(block.status).lower()
    enforceable = declaration_status in ENFORCEABLE_STATUSES
    source = {
        "file": block.source_file,
        "startLine": block.start_line,
        "endLine": block.end_line,
    }
    base = {
        "contractId": block.block_id,
        "appId": safe_string(block.app),
        "declarationStatus": declaration_status,
        "enforceable": enforceable,
        "requirementCount": len(block.fields.get("requires") or []),
        "target": safe_string(block.fields.get("target") or block.fields.get("scope")),
        "source": source,
        "bindingId": binding.binding_id if binding else "",
        "selectors": list(binding.selectors) if binding else [],
        "notes": binding.notes if binding else "",
    }
    if not enforceable:
        return {
            **base,
            "status": "not-due",
            "passed": False,
            "executed": False,
            "reason": f"declaration status {declaration_status or 'unknown'} is not currently enforceable",
            "testCount": 0,
        }
    if binding is None:
        return {
            **base,
            "status": "missing-binding",
            "passed": False,
            "executed": False,
            "reason": "no executable acceptance binding is registered",
            "testCount": 0,
        }

    key = binding.selectors
    execution = execution_cache.get(key)
    if execution is None:
        execution = run_pytest_binding(
            binding=binding,
            repo=repo,
            extra_pytest_args=extra_pytest_args,
            timeout_seconds=timeout_seconds,
        )
        execution_cache[key] = execution
    return {
        **base,
        **execution,
        "executed": True,
    }


def app_result(app_id: str, contracts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    enforceable = [item for item in contracts if item.get("enforceable") is True]
    blocking = [
        item for item in enforceable
        if item.get("status") != "pass"
    ]
    if blocking:
        status = "fail"
    else:
        status = "pass"

    status_counts = Counter(safe_string(item.get("status")) for item in contracts)
    return {
        "appId": app_id,
        "status": status,
        "passed": status == "pass",
        "testCount": sum(int(item.get("testCount") or 0) for item in enforceable),
        "declaredContractCount": len(contracts),
        "enforceableContractCount": len(enforceable),
        "passedContractCount": sum(item.get("status") == "pass" for item in enforceable),
        "notDueContractCount": sum(item.get("status") == "not-due" for item in contracts),
        "missingBindingContractIds": [
            safe_string(item.get("contractId"))
            for item in contracts
            if item.get("status") == "missing-binding"
        ],
        "failedContractIds": [
            safe_string(item.get("contractId"))
            for item in blocking
        ],
        "statusCounts": dict(sorted(status_counts.items())),
        "contracts": list(contracts),
    }


def build_report(
    *,
    repo: Path,
    registry: Any,
    contracts: Sequence[Any],
    bindings: Mapping[str, Binding],
    binding_metadata: Mapping[str, Any],
    selected_apps: set[str],
    extra_pytest_args: Sequence[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    generated_at = utc_now_iso()
    grouped: dict[str, list[Any]] = defaultdict(list)
    for block in contracts:
        app_id = safe_string(block.app)
        if selected_apps and app_id not in selected_apps:
            continue
        grouped[app_id].append(block)

    execution_cache: dict[tuple[str, ...], dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for app_id in sorted(grouped):
        contract_results = [
            contract_result(
                block=block,
                binding=bindings.get(block.block_id),
                repo=repo,
                extra_pytest_args=extra_pytest_args,
                timeout_seconds=timeout_seconds,
                execution_cache=execution_cache,
            )
            for block in grouped[app_id]
        ]
        results.append(app_result(app_id, contract_results))

    status = "pass" if all(item["passed"] for item in results) else "fail"
    app_status_counts = Counter(item["status"] for item in results)
    contract_status_counts = Counter(
        contract["status"]
        for item in results
        for contract in item["contracts"]
    )
    return {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "generatedAt": generated_at,
        "status": status,
        "passed": status == "pass",
        "repositoryProvenance": build_repository_provenance(repo),
        "requirementsRegistry": {
            "version": safe_string(getattr(registry, "summary")().get("registry_version")),
            "valid": bool(registry.valid),
            "strictSchemaReady": bool(registry.strict_schema_ready),
            "acceptanceContractCount": len(contracts),
        },
        "bindingCatalog": dict(binding_metadata),
        "summary": {
            "appCount": len(results),
            "appStatusCounts": dict(sorted(app_status_counts.items())),
            "declaredContractCount": sum(item["declaredContractCount"] for item in results),
            "enforceableContractCount": sum(item["enforceableContractCount"] for item in results),
            "passedContractCount": sum(item["passedContractCount"] for item in results),
            "notDueContractCount": sum(item["notDueContractCount"] for item in results),
            "contractStatusCounts": dict(sorted(contract_status_counts.items())),
            "pytestExecutionCount": len(execution_cache),
        },
        "results": results,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    provenance = report.get("repositoryProvenance") or {}
    summary = report.get("summary") or {}
    lines = [
        "# MCEL Acceptance Evidence Report",
        "",
        f"- Schema: `{safe_string(report.get('schema'))}`",
        f"- Version: `{safe_string(report.get('version'))}`",
        f"- Generated: `{safe_string(report.get('generatedAt'))}`",
        f"- Status: **{safe_string(report.get('status'))}**",
        f"- Repository fingerprint: `{safe_string(provenance.get('fingerprint'))}`",
        f"- Fingerprint scope: `{safe_string(provenance.get('scope'))}`",
        f"- Selection method: `{safe_string(provenance.get('selectionMethod'))}`",
        f"- Apps: `{int(summary.get('appCount') or 0)}`",
        f"- Enforceable contracts: `{int(summary.get('enforceableContractCount') or 0)}`",
        f"- Passed contracts: `{int(summary.get('passedContractCount') or 0)}`",
        f"- Future/not-due contracts: `{int(summary.get('notDueContractCount') or 0)}`",
        "",
        "## App results",
        "",
        "| App | Status | Enforceable | Passed | Not due | Missing bindings |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for app in report.get("results") or []:
        lines.append(
            "| {app} | {status} | {enforceable} | {passed} | {not_due} | {missing} |".format(
                app=safe_string(app.get("appId")),
                status=safe_string(app.get("status")),
                enforceable=int(app.get("enforceableContractCount") or 0),
                passed=int(app.get("passedContractCount") or 0),
                not_due=int(app.get("notDueContractCount") or 0),
                missing=len(app.get("missingBindingContractIds") or []),
            )
        )

    for app in report.get("results") or []:
        lines.extend(["", f"## {safe_string(app.get('appId'))}", ""])
        for contract in app.get("contracts") or []:
            lines.extend(
                [
                    f"### `{safe_string(contract.get('contractId'))}`",
                    "",
                    f"- Declaration status: `{safe_string(contract.get('declarationStatus'))}`",
                    f"- Enforceable: `{str(contract.get('enforceable') is True).lower()}`",
                    f"- Evidence status: `{safe_string(contract.get('status'))}`",
                    f"- Binding: `{safe_string(contract.get('bindingId')) or 'none'}`",
                    f"- Tests collected: `{int(contract.get('testCount') or 0)}`",
                ]
            )
            if contract.get("selectors"):
                lines.append(
                    "- Selectors: " + ", ".join(f"`{item}`" for item in contract.get("selectors") or [])
                )
            if contract.get("reason"):
                lines.append(f"- Reason: {safe_string(contract.get('reason'))}")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `pass` means every currently enforceable contract for the app had an explicit binding and a successful pytest execution.",
            "- `not-due` means the contract is declared as draft, planned, or open and is preserved as a future obligation.",
            "- `missing-binding`, `no-tests`, `fail`, and `execution-error` never count as acceptance proof.",
            "- Repository provenance binds this evidence to the same source-state fingerprint used by FLOG and the truth audit.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(report: Mapping[str, Any], output_dir: Path, repo: Path) -> tuple[Path, Path]:
    resolved = resolve_repo_path(output_dir, repo)
    resolved.mkdir(parents=True, exist_ok=True)
    json_path = resolved / "mcel-acceptance-report.json"
    markdown_path = resolved / "mcel-acceptance-report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--bindings", type=Path, default=DEFAULT_BINDINGS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--app", action="append", default=[], help="Run only this app; repeatable.")
    parser.add_argument("--pytest-arg", action="append", default=[], help="Additional pytest argument; repeatable.")
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--list-contracts", action="store_true")
    parser.add_argument("--check", action="store_true", help="Exit nonzero when any app lacks passing acceptance proof.")
    parser.add_argument("--json", action="store_true", help="Print the complete report JSON.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo = (args.repo_root or repo_root_from_script()).resolve()
    try:
        registry = load_requirements_registry(repo)
        contracts = acceptance_contracts(registry)
        bindings, binding_metadata = load_bindings(args.bindings, repo)
        validate_bindings(contracts, bindings)
        selected_apps = {safe_string(value) for value in args.app if safe_string(value)}
        known_apps = {safe_string(block.app) for block in contracts}
        unknown_apps = sorted(selected_apps - known_apps)
        if unknown_apps:
            raise McelAcceptanceError(
                "Unknown app(s) for acceptance execution: " + ", ".join(unknown_apps)
            )

        if args.list_contracts:
            for block in contracts:
                if selected_apps and safe_string(block.app) not in selected_apps:
                    continue
                binding = bindings.get(block.block_id)
                print(
                    f"{safe_string(block.app)}\t{block.block_id}\t"
                    f"{safe_string(block.status)}\t{binding.binding_id if binding else 'unbound'}"
                )
            return 0

        report = build_report(
            repo=repo,
            registry=registry,
            contracts=contracts,
            bindings=bindings,
            binding_metadata=binding_metadata,
            selected_apps=selected_apps,
            extra_pytest_args=tuple(args.pytest_arg),
            timeout_seconds=args.timeout_seconds,
        )
        json_path, markdown_path = write_report(report, args.output_dir, repo)
    except McelAcceptanceError as exc:
        print(f"mcel acceptance error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        summary = report["summary"]
        print(REPORT_VERSION)
        print(f"status: {report['status']}")
        print(f"apps: {summary['appCount']}")
        print(f"enforceable_contracts: {summary['enforceableContractCount']}")
        print(f"passed_contracts: {summary['passedContractCount']}")
        print(f"not_due_contracts: {summary['notDueContractCount']}")
        print(f"contract_status_counts: {summary['contractStatusCounts']}")
        print(f"json: {repo_display_path(json_path, repo)}")
        print(f"markdown: {repo_display_path(markdown_path, repo)}")
        for item in report["results"]:
            print(f"  {item['status']}: {item['appId']}")

    return 1 if args.check and report["status"] != "pass" else 0


if __name__ == "__main__":
    raise SystemExit(main())
