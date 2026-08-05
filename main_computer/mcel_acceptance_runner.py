#!/usr/bin/env python3
"""Run repository-bound MCEL acceptance evidence.

The central requirements registry and validated MCEL application packages are
the authorities for declared ``mcel-acceptance`` contracts. This runner joins
legacy central bindings with package-local ``mcel.package-acceptance-bindings.v1``
files, executes pytest selectors, and writes repository-bound JSON/Markdown
evidence carrying package provenance where applicable.

A contract is proven only when it is currently enforceable, has one explicit
binding, collects tests, and every bound test passes. Planned/draft contracts
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
    from .mcel_application_packages import build_application_package_catalog
    from .mcel_evidence_provenance import build_repository_provenance
    from .mcel_node_runtime import prepend_node_to_path, resolve_node_executable
except ImportError:  # Direct script execution from the repository root.
    _REPOSITORY_IMPORT_ROOT = Path(__file__).resolve().parents[1]
    if str(_REPOSITORY_IMPORT_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPOSITORY_IMPORT_ROOT))
    from main_computer.mcel_application_packages import build_application_package_catalog
    from main_computer.mcel_evidence_provenance import build_repository_provenance
    from main_computer.mcel_node_runtime import prepend_node_to_path, resolve_node_executable


REPORT_SCHEMA = "mcel-acceptance-evidence-report-v1"
REPORT_VERSION = "mcel-acceptance-runner-v1"
BINDING_SCHEMA = "mcel-acceptance-bindings-v1"
PACKAGE_BINDING_SCHEMA = "mcel.package-acceptance-bindings.v1"
BINDING_SOURCES_SCHEMA = "mcel.acceptance-binding-sources.v1"
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
    source_kind: str = "central"
    source_path: str = ""
    package_root: str = ""
    package_fingerprint: str = ""
    declared_selectors: tuple[str, ...] = ()


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


def _scope_slug(values: Iterable[str]) -> str:
    normalized = [
        re.sub(r"[^a-z0-9._-]+", "-", safe_string(value).lower()).strip("-")
        for value in values
        if safe_string(value)
    ]
    return "__".join(value for value in normalized if value) or "selection"


def build_evidence_scope(
    *,
    selected_apps: Iterable[str],
    covered_apps: Iterable[str],
    all_apps: Iterable[str],
) -> dict[str, Any]:
    selected = sorted({safe_string(value) for value in selected_apps if safe_string(value)})
    covered = sorted({safe_string(value) for value in covered_apps if safe_string(value)})
    complete = sorted({safe_string(value) for value in all_apps if safe_string(value)})
    canonical = not selected
    return {
        "schema": "mcel-evidence-scope-v1",
        "kind": "canonical" if canonical else "app-scoped",
        "canonical": canonical,
        "selectedApps": selected,
        "coveredApps": covered,
        "canonicalAppIds": complete,
    }


def resolve_output_dir(
    *,
    requested_output_dir: Path | None,
    evidence_scope: Mapping[str, Any],
    overwrite_canonical: bool,
    repo: Path | None = None,
) -> Path:
    canonical = evidence_scope.get("canonical") is True
    selected_apps = [
        safe_string(value)
        for value in evidence_scope.get("selectedApps") or []
        if safe_string(value)
    ]
    if requested_output_dir is None:
        if canonical or overwrite_canonical:
            return DEFAULT_OUTPUT_DIR
        return DEFAULT_OUTPUT_DIR / "apps" / _scope_slug(selected_apps)

    requested = Path(requested_output_dir)
    comparison_root = (repo or Path.cwd()).resolve()
    requested_resolved = resolve_repo_path(requested, comparison_root).resolve()
    canonical_resolved = resolve_repo_path(DEFAULT_OUTPUT_DIR, comparison_root).resolve()
    if (
        not canonical
        and requested_resolved == canonical_resolved
        and not overwrite_canonical
    ):
        raise McelAcceptanceError(
            "App-scoped acceptance evidence cannot replace the canonical all-app report "
            "without --overwrite-canonical."
        )
    return requested


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
            source_kind="central",
            source_path=repo_display_path(resolved, repo),
            declared_selectors=selectors,
        )

    return result, {
        "path": repo_display_path(resolved, repo),
        "schema": safe_string(payload.get("schema")),
        "version": safe_string(payload.get("version")),
        "sha256": sha256_bytes(raw),
        "bindingCount": len(result),
    }



def _requirements_registry_tool(repo: Path):
    repo_text = str(repo.resolve())
    if repo_text not in sys.path:
        sys.path.insert(0, repo_text)
    try:
        from tools import mcel_requirements_registry as registry_tool
    except ImportError as exc:
        raise McelAcceptanceError(
            "Could not import tools.mcel_requirements_registry for package requirements."
        ) from exc
    return registry_tool


def _package_relative_selector(
    selector: str,
    *,
    package_root: PurePosixPath,
    tests_root: PurePosixPath,
) -> tuple[PurePosixPath, str]:
    raw = safe_string(selector).replace("\\", "/")
    source_text, separator, suffix = raw.partition("::")
    source = PurePosixPath(source_text)
    if (
        not source_text
        or source.is_absolute()
        or "." in source.parts
        or ".." in source.parts
        or source.suffix != ".py"
    ):
        raise McelAcceptanceError(
            f"Unsafe or unsupported package pytest selector: {selector!r}"
        )
    try:
        source.relative_to(tests_root)
    except ValueError as exc:
        raise McelAcceptanceError(
            f"Package pytest selector escapes the declared tests root: {selector!r}"
        ) from exc
    repository_source = PurePosixPath(package_root, source)
    executable = repository_source.as_posix()
    if separator:
        executable += "::" + suffix
    return repository_source, executable


def load_package_acceptance(
    repo: Path,
) -> tuple[list[Any], dict[str, Binding], dict[str, Any]]:
    catalog = build_application_package_catalog(repo)
    if not catalog.ok or catalog.invalid_count or catalog.errors:
        messages = [issue.message for issue in catalog.errors]
        for record in catalog.packages:
            messages.extend(issue.message for issue in record.errors)
        raise McelAcceptanceError(
            "Repository application-package catalog is invalid"
            + (f": {'; '.join(messages[:5])}" if messages else ".")
        )

    registry_tool = _requirements_registry_tool(repo)
    contracts: list[Any] = []
    bindings: dict[str, Binding] = {}
    binding_ids: set[str] = set()
    packages: list[dict[str, Any]] = []

    for record in catalog.packages:
        if not record.valid or not record.app_id or not record.fingerprint:
            raise McelAcceptanceError(
                f"Application package {record.package_root!r} is not valid for acceptance discovery."
            )
        if not record.requirements or not record.tests_root or not record.acceptance_bindings:
            raise McelAcceptanceError(
                f"Application package {record.app_id!r} does not declare package-local acceptance inputs."
            )

        requirements_path = repo / Path(record.requirements)
        package_blocks, package_errors = registry_tool.extract_blocks_from_file(
            requirements_path,
            repo,
        )
        if package_errors:
            raise McelAcceptanceError(
                f"Application package {record.app_id!r} requirements are invalid: "
                + "; ".join(issue.message for issue in package_errors[:5])
            )
        package_contracts = [
            block
            for block in package_blocks
            if block.block_type == "mcel-acceptance"
        ]
        if not package_contracts:
            raise McelAcceptanceError(
                f"Application package {record.app_id!r} declares no mcel-acceptance contract."
            )
        wrong_contract_apps = [
            block.block_id
            for block in package_contracts
            if safe_string(block.app) != record.app_id
        ]
        if wrong_contract_apps:
            raise McelAcceptanceError(
                f"Application package {record.app_id!r} contains acceptance contracts for another app: "
                + ", ".join(sorted(wrong_contract_apps))
            )
        contract_ids = {block.block_id for block in package_contracts}

        binding_path = repo / Path(record.acceptance_bindings)
        raw = binding_path.read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise McelAcceptanceError(
                f"Package acceptance binding file is not valid UTF-8 JSON: {record.acceptance_bindings}"
            ) from exc
        if not isinstance(payload, Mapping) or payload.get("schema") != PACKAGE_BINDING_SCHEMA:
            raise McelAcceptanceError(
                f"Package acceptance binding file must use schema {PACKAGE_BINDING_SCHEMA!r}: "
                f"{record.acceptance_bindings}"
            )
        if safe_string(payload.get("appId")) != record.app_id:
            raise McelAcceptanceError(
                f"Package acceptance binding appId disagrees with package {record.app_id!r}."
            )

        package_root = PurePosixPath(record.package_root)
        tests_root_repository = PurePosixPath(record.tests_root)
        try:
            tests_root = tests_root_repository.relative_to(package_root)
        except ValueError as exc:
            raise McelAcceptanceError(
                f"Package tests root escapes package root: {record.tests_root}"
            ) from exc

        package_binding_count = 0
        targeted_contracts: set[str] = set()
        for index, item in enumerate(payload.get("bindings") or []):
            if not isinstance(item, Mapping):
                raise McelAcceptanceError(
                    f"Package acceptance binding #{index + 1} for {record.app_id} is not an object."
                )
            binding_id = safe_string(item.get("id"))
            app_id = safe_string(item.get("appId") or payload.get("appId"))
            contract_id = safe_string(item.get("acceptanceContractId") or item.get("contractId"))
            runner = safe_string(item.get("runner") or "pytest").lower()
            declared_selectors = tuple(
                safe_string(value)
                for value in (item.get("selectors") or [])
                if safe_string(value)
            )
            if not binding_id or not app_id or not contract_id:
                raise McelAcceptanceError(
                    f"Package acceptance binding #{index + 1} requires id, appId, and acceptanceContractId."
                )
            if app_id != record.app_id:
                raise McelAcceptanceError(
                    f"Package acceptance binding {binding_id!r} appId disagrees with package identity."
                )
            if binding_id in binding_ids:
                raise McelAcceptanceError(
                    f"Duplicate package acceptance binding id: {binding_id}"
                )
            if contract_id in bindings or contract_id in targeted_contracts:
                raise McelAcceptanceError(
                    f"Multiple package acceptance bindings target the same contract: {contract_id}"
                )
            if contract_id not in contract_ids:
                raise McelAcceptanceError(
                    f"Package acceptance binding {binding_id!r} targets unknown contract {contract_id!r}."
                )
            if runner != "pytest":
                raise McelAcceptanceError(
                    f"Unsupported package acceptance runner {runner!r} for binding {binding_id}."
                )
            if not declared_selectors:
                raise McelAcceptanceError(
                    f"Package acceptance binding {binding_id} has no pytest selectors."
                )

            executable_selectors: list[str] = []
            for selector in declared_selectors:
                source, executable = _package_relative_selector(
                    selector,
                    package_root=package_root,
                    tests_root=tests_root,
                )
                if not (repo / Path(*source.parts)).is_file():
                    raise McelAcceptanceError(
                        f"Package acceptance binding {binding_id} references a missing test file: "
                        f"{source.as_posix()}"
                    )
                executable_selectors.append(executable)

            binding_ids.add(binding_id)
            targeted_contracts.add(contract_id)
            bindings[contract_id] = Binding(
                binding_id=binding_id,
                app_id=app_id,
                contract_id=contract_id,
                runner=runner,
                selectors=tuple(executable_selectors),
                notes=safe_string(item.get("notes")),
                source_kind="package",
                source_path=record.acceptance_bindings,
                package_root=record.package_root,
                package_fingerprint=record.fingerprint,
                declared_selectors=declared_selectors,
            )
            package_binding_count += 1

        contracts.extend(package_contracts)
        packages.append(
            {
                "appId": record.app_id,
                "packageRoot": record.package_root,
                "packageFingerprint": record.fingerprint,
                "packageFingerprintAlgorithm": record.fingerprint_algorithm,
                "requirements": record.requirements,
                "acceptanceBindings": record.acceptance_bindings,
                "acceptanceBindingsSha256": sha256_bytes(raw),
                "acceptanceContractCount": len(package_contracts),
                "bindingCount": package_binding_count,
            }
        )

    return contracts, bindings, {
        "schema": PACKAGE_BINDING_SCHEMA,
        "packageCount": len(packages),
        "bindingCount": len(bindings),
        "acceptanceContractCount": len(contracts),
        "packages": packages,
        "packageCatalogFingerprint": catalog.fingerprint,
        "packageCatalogFingerprintAlgorithm": catalog.fingerprint_algorithm,
    }


def combine_acceptance_sources(
    *,
    central_contracts: Sequence[Any],
    central_bindings: Mapping[str, Binding],
    central_metadata: Mapping[str, Any],
    package_contracts: Sequence[Any],
    package_bindings: Mapping[str, Binding],
    package_metadata: Mapping[str, Any],
) -> tuple[list[Any], dict[str, Binding], dict[str, Any]]:
    contracts = sorted(
        [*central_contracts, *package_contracts],
        key=lambda block: (safe_string(block.app), block.block_id),
    )
    contract_counts = Counter(block.block_id for block in contracts)
    duplicate_contracts = sorted(
        contract_id for contract_id, count in contract_counts.items() if count > 1
    )
    if duplicate_contracts:
        raise McelAcceptanceError(
            "Duplicate acceptance contract ids across central and package sources: "
            + ", ".join(duplicate_contracts)
        )

    binding_ids = Counter(
        binding.binding_id
        for binding in [*central_bindings.values(), *package_bindings.values()]
    )
    duplicate_binding_ids = sorted(
        binding_id for binding_id, count in binding_ids.items() if count > 1
    )
    if duplicate_binding_ids:
        raise McelAcceptanceError(
            "Duplicate acceptance binding ids across central and package sources: "
            + ", ".join(duplicate_binding_ids)
        )
    duplicate_targets = sorted(set(central_bindings).intersection(package_bindings))
    if duplicate_targets:
        raise McelAcceptanceError(
            "Acceptance contracts are bound by both central and package sources: "
            + ", ".join(duplicate_targets)
        )

    bindings = dict(central_bindings)
    bindings.update(package_bindings)
    metadata = {
        "schema": BINDING_SOURCES_SCHEMA,
        "bindingCount": len(bindings),
        "centralBindingCount": len(central_bindings),
        "packageBindingCount": len(package_bindings),
        "central": dict(central_metadata),
        "packages": dict(package_metadata),
    }
    return contracts, bindings, metadata


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


def build_pytest_environment(
    *,
    base_env: Mapping[str, str] | None = None,
    node_executable: str | os.PathLike[str] | None = None,
) -> tuple[dict[str, str], str | None]:
    """Build a pytest environment with Node available when it can be discovered."""

    env = dict(os.environ if base_env is None else base_env)
    resolved_node = resolve_node_executable(node_executable)
    if resolved_node:
        env = prepend_node_to_path(env, resolved_node)
    return env, resolved_node


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
    node_executable = None
    try:
        pytest_env, node_executable = build_pytest_environment()
        completed = subprocess.run(
            command,
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
            timeout=max(1.0, float(timeout_seconds)),
            env=pytest_env,
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
        "nodeExecutable": node_executable or "",
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
        "bindingSource": binding.source_kind if binding else "",
        "bindingSourcePath": binding.source_path if binding else "",
        "packageRoot": binding.package_root if binding else "",
        "packageFingerprint": binding.package_fingerprint if binding else "",
        "selectors": list(binding.selectors) if binding else [],
        "declaredSelectors": list(binding.declared_selectors) if binding else [],
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
        "evidenceScope": build_evidence_scope(
            selected_apps=selected_apps,
            covered_apps=grouped,
            all_apps=(safe_string(block.app) for block in contracts),
        ),
        "repositoryProvenance": build_repository_provenance(repo),
        "requirementsRegistry": {
            "version": safe_string(getattr(registry, "summary")().get("registry_version")),
            "valid": bool(registry.valid),
            "strictSchemaReady": bool(registry.strict_schema_ready),
            "acceptanceContractCount": len(contracts),
            "centralAcceptanceContractCount": sum(
                1 for block in contracts if not str(block.source_file).startswith("mcel_apps/")
            ),
            "packageAcceptanceContractCount": sum(
                1 for block in contracts if str(block.source_file).startswith("mcel_apps/")
            ),
        },
        "bindingCatalog": dict(binding_metadata),
        "applicationPackages": [
            dict(package)
            for package in ((binding_metadata.get("packages") or {}).get("packages") or [])
            if not selected_apps or safe_string(package.get("appId")) in selected_apps
        ],
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
        f"- Evidence scope: `{safe_string((report.get('evidenceScope') or {}).get('kind')) or 'unknown'}`",
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
                    f"- Binding source: `{safe_string(contract.get('bindingSource')) or 'none'}`",
                    f"- Package fingerprint: `{safe_string(contract.get('packageFingerprint')) or 'not-applicable'}`",
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
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Report directory. Full runs use the canonical report directory; app-scoped "
            "runs default to runtime/reports/mcel-acceptance/apps/<selection>."
        ),
    )
    parser.add_argument("--app", action="append", default=[], help="Run only this app; repeatable.")
    parser.add_argument(
        "--overwrite-canonical",
        action="store_true",
        help="Allow an app-scoped run to replace the canonical all-app report.",
    )
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
        central_contracts = acceptance_contracts(registry)
        central_bindings, central_binding_metadata = load_bindings(args.bindings, repo)
        package_contracts, package_bindings, package_binding_metadata = load_package_acceptance(repo)
        contracts, bindings, binding_metadata = combine_acceptance_sources(
            central_contracts=central_contracts,
            central_bindings=central_bindings,
            central_metadata=central_binding_metadata,
            package_contracts=package_contracts,
            package_bindings=package_bindings,
            package_metadata=package_binding_metadata,
        )
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

        planned_scope = build_evidence_scope(
            selected_apps=selected_apps,
            covered_apps=selected_apps or known_apps,
            all_apps=known_apps,
        )
        output_dir = resolve_output_dir(
            requested_output_dir=args.output_dir,
            evidence_scope=planned_scope,
            overwrite_canonical=bool(args.overwrite_canonical),
            repo=repo,
        )
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
        json_path, markdown_path = write_report(report, output_dir, repo)
    except McelAcceptanceError as exc:
        print(f"mcel acceptance error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        summary = report["summary"]
        print(REPORT_VERSION)
        print(f"status: {report['status']}")
        print(f"evidence_scope: {report['evidenceScope']['kind']}")
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
