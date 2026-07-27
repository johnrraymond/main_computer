#!/usr/bin/env python3
"""Repository-wide MCEL app-truth audit and CI gate.

The browser-side ``McelAppTruthGate`` remains the authority for app truth.
This CLI loads the same registries and truth gate under Node, supplies optional
runtime and acceptance evidence, writes deterministic JSON/Markdown reports,
and optionally enforces only declared policy violations.

Typical usage from the repository root::

    python main_computer/mcel_truth_audit.py

    python main_computer/mcel_truth_audit.py \
      --runtime-evidence runtime/reports/flog/mcel-runtime/mcel-runtime-flog-report.json \
      --check

    python main_computer/mcel_truth_audit.py --release-gate

``--check`` fails on truth-gate findings explicitly marked blocking. Missing or
stale proof remains visible but non-blocking unless opt-in evidence policies
are enabled. ``--release-gate`` discovers the latest runtime and acceptance
reports, requires fresh proof, and binds evidence to the exact repository
source fingerprint.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import select
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from .mcel_evidence_provenance import (
        build_repository_provenance,
        compare_repository_provenance,
        extract_repository_provenance,
    )
except ImportError:  # Direct script execution from the repository root.
    from mcel_evidence_provenance import (
        build_repository_provenance,
        compare_repository_provenance,
        extract_repository_provenance,
    )


REPORT_SCHEMA = "mcel-repository-truth-audit-v1"
REPORT_VERSION = "mcel-repository-truth-audit-v1"
DEFAULT_MAX_EVIDENCE_AGE_HOURS = 7 * 24
DEFAULT_OUTPUT_DIR = Path("runtime/reports/mcel-truth-audit")
DEFAULT_RUNTIME_EVIDENCE = Path(
    "runtime/reports/flog/mcel-runtime/mcel-runtime-flog-report.json"
)
DEFAULT_ACCEPTANCE_EVIDENCE = Path(
    "runtime/reports/mcel-acceptance/mcel-acceptance-report.json"
)
RUNTIME_EVIDENCE_SEARCH_ROOT = Path("runtime/reports/flog")
ACCEPTANCE_EVIDENCE_SEARCH_ROOT = Path("runtime/reports/mcel-acceptance")
RUNTIME_EVIDENCE_SCHEMAS = frozenset({"mcel-runtime-flog-report-v2"})
ACCEPTANCE_EVIDENCE_SCHEMAS = frozenset({"mcel-acceptance-evidence-report-v1"})

CORE_SCRIPT_NAMES = (
    "mcel-requirements-registry.js",
    "mcel-domain-adapter-registry.js",
    "mcel-app-surface-registry.js",
    "mcel-app-truth-gate.js",
)

DECLARED_LEVELS = ("legacy", "runtime-baseline", "semantic-runtime")


class McelTruthAuditError(RuntimeError):
    """Raised when the repository truth audit cannot produce trustworthy output."""


@dataclass(frozen=True)
class EvidenceInput:
    """Loaded evidence plus report-safe source metadata."""

    value: Any
    metadata: dict[str, Any]


NODE_TRUTH_BRIDGE = r"""
const fs = require("fs");
const vm = require("vm");

function readInput() {
  const text = fs.readFileSync(0, "utf8");
  return text.trim() ? JSON.parse(text) : {};
}

function safeMessage(error) {
  return String(error && error.message ? error.message : error || "unknown error");
}

const input = readInput();
const sandbox = {
  setTimeout,
  clearTimeout,
  URL,
  URLSearchParams,
  TextEncoder,
  TextDecoder
};
sandbox.console = {
  log() {},
  info() {},
  warn() {},
  error(...args) {
    process.stderr.write(args.map(String).join(" ") + "\n");
  }
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.location = {
  href: "http://127.0.0.1/",
  origin: "http://127.0.0.1/"
};
sandbox.localStorage = {
  getItem() { return null; },
  setItem() {},
  removeItem() {}
};
sandbox.fetch = async function unavailableFetch() {
  throw new Error("Network access is unavailable during the repository truth audit.");
};

vm.createContext(sandbox);

function loadRequired(path) {
  vm.runInContext(fs.readFileSync(path, "utf8"), sandbox, {filename: path});
}

const diagnostics = [];
for (const path of input.coreScripts || []) {
  loadRequired(path);
  diagnostics.push({kind: "core-authority", path, status: "loaded"});
}

for (const path of input.adapterScripts || []) {
  try {
    loadRequired(path);
    diagnostics.push({kind: "domain-adapter", path, status: "loaded"});
  } catch (error) {
    diagnostics.push({
      kind: "domain-adapter",
      path,
      status: "failed",
      error: {
        name: String(error && error.name ? error.name : "Error"),
        message: safeMessage(error)
      }
    });
  }
}

const authorities = {
  requirementsRegistry: Boolean(sandbox.McelRequirementsRegistry),
  domainAdapterRegistry: Boolean(sandbox.McelDomainAdapterRegistry),
  appSurfaceRegistry: Boolean(sandbox.McelAppSurfaceRegistry),
  appTruthGate: Boolean(sandbox.McelAppTruthGate)
};
if (Object.values(authorities).some((present) => !present)) {
  throw new Error("One or more required MCEL truth authorities failed to load.");
}

const options = {
  requirementsRegistry: sandbox.McelRequirementsRegistry,
  domainAdapterRegistry: sandbox.McelDomainAdapterRegistry,
  appSurfaceRegistry: sandbox.McelAppSurfaceRegistry,
  runtimeEvidence: input.runtimeEvidence || null,
  acceptanceEvidence: input.acceptanceEvidence || null,
  now: input.now,
  maxEvidenceAgeMs: input.maxEvidenceAgeMs
};
const truthSnapshot = sandbox.McelAppTruthGate.buildTruthSnapshot(options);

process.stdout.write(JSON.stringify({
  truthSnapshot,
  loaderDiagnostics: diagnostics,
  authorities
}));
"""


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_string(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _safe_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _repo_display_path(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _resolve_repo_path(path: Path, repo: Path) -> Path:
    return path if path.is_absolute() else repo / path


def _parse_timestamp(value: Any) -> float:
    text = _safe_string(value)
    if not text:
        return 0.0
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return 0.0


def _matches_evidence_schema(value: Any, *, label: str) -> bool:
    if not isinstance(value, Mapping):
        return False
    schema = _safe_string(value.get("schema") or value.get("reportSchema")).lower()
    if label == "runtime":
        return schema in RUNTIME_EVIDENCE_SCHEMAS
    return schema in ACCEPTANCE_EVIDENCE_SCHEMAS


def discover_latest_evidence_path(
    *,
    repo: Path,
    search_root: Path,
    label: str,
) -> Path | None:
    """Return the newest schema-matching evidence report under *search_root*."""

    root = _resolve_repo_path(search_root, repo)
    if not root.exists():
        return None

    candidates: list[tuple[float, float, str, Path]] = []
    for path in root.rglob("*.json"):
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not _matches_evidence_schema(value, label=label):
            continue
        top = value if isinstance(value, Mapping) else {}
        generated = _parse_timestamp(
            top.get("generatedAt") or top.get("finishedAt") or top.get("timestamp")
        )
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = 0.0
        candidates.append((generated, modified, path.as_posix(), path))

    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1], item[2]))[3]


def select_evidence_path(
    *,
    repo: Path,
    requested_path: Path | None,
    use_latest: bool,
    default_path: Path,
    search_root: Path,
    label: str,
) -> tuple[Path | None, str, bool]:
    """Resolve explicit, latest-discovered, or conventional evidence selection."""

    if requested_path is not None and use_latest:
        raise McelTruthAuditError(
            f"--{label}-evidence cannot be combined with --latest-{label}-evidence."
        )
    if requested_path is not None:
        return requested_path, "explicit", True
    if use_latest:
        return (
            discover_latest_evidence_path(repo=repo, search_root=search_root, label=label),
            "latest",
            False,
        )
    return default_path, "default", False


def load_evidence(
    *,
    selected_path: Path | None,
    selection: str,
    explicit: bool,
    fallback_display_path: Path,
    repo: Path,
    label: str,
) -> EvidenceInput:
    """Load optional evidence selected explicitly, by discovery, or by convention."""

    if selected_path is None:
        return EvidenceInput(
            value=None,
            metadata={
                "label": label,
                "path": _repo_display_path(_resolve_repo_path(fallback_display_path, repo), repo),
                "selection": selection,
                "present": False,
                "sha256": "",
                "schema": "",
                "generatedAt": "",
            },
        )

    resolved = _resolve_repo_path(selected_path, repo)
    if not resolved.exists():
        if explicit:
            raise McelTruthAuditError(
                f"{label} evidence file does not exist: {_repo_display_path(resolved, repo)}"
            )
        return EvidenceInput(
            value=None,
            metadata={
                "label": label,
                "path": _repo_display_path(resolved, repo),
                "selection": selection,
                "present": False,
                "sha256": "",
                "schema": "",
                "generatedAt": "",
            },
        )

    try:
        data = resolved.read_bytes()
        value = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise McelTruthAuditError(
            f"{label} evidence is not UTF-8 JSON: {_repo_display_path(resolved, repo)}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise McelTruthAuditError(
            f"{label} evidence is malformed JSON at line {exc.lineno}, column {exc.colno}: "
            f"{_repo_display_path(resolved, repo)}"
        ) from exc

    if not isinstance(value, (dict, list, bool)):
        raise McelTruthAuditError(
            f"{label} evidence must be a JSON object, array, or boolean."
        )

    top = value if isinstance(value, dict) else {}
    provenance = extract_repository_provenance(value)
    return EvidenceInput(
        value=value,
        metadata={
            "label": label,
            "path": _repo_display_path(resolved, repo),
            "selection": selection,
            "present": True,
            "sha256": _sha256_bytes(data),
            "schema": _safe_string(top.get("schema") or top.get("reportSchema")),
            "generatedAt": _safe_string(
                top.get("generatedAt") or top.get("timestamp") or top.get("finishedAt")
            ),
            "repositoryProvenance": dict(provenance or {}),
        },
    )


def evidence_binding(
    evidence: EvidenceInput,
    current_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    if evidence.metadata.get("present") is not True:
        return {
            "status": "absent",
            "exact": False,
            "currentFingerprint": _safe_string(current_provenance.get("fingerprint")),
            "evidenceFingerprint": "",
            "reason": "No evidence report was selected.",
        }
    return compare_repository_provenance(
        extract_repository_provenance(evidence.value),
        current_provenance,
    )


def truth_eligible_evidence(evidence: EvidenceInput, binding: Mapping[str, Any]) -> Any:
    """Never allow explicitly mismatched or unsupported evidence to prove truth."""

    if binding.get("status") in {"mismatch", "unsupported"}:
        return None
    return evidence.value


def discover_adapter_scripts(repo: Path) -> list[Path]:
    """Find static scripts that register with the domain-adapter authority."""

    scripts_dir = repo / "main_computer" / "web" / "applications" / "scripts"
    discovered: list[Path] = []
    for path in sorted(scripts_dir.glob("*.js"), key=lambda item: item.name):
        if path.name == "mcel-domain-adapter-registry.js":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if (
            "McelDomainAdapterRegistry" in text
            and "registerAdapter" in text
        ):
            discovered.append(path)
    return discovered


def source_inventory(repo: Path, core_scripts: Sequence[Path], adapter_scripts: Sequence[Path]) -> dict[str, Any]:
    def entry(path: Path) -> dict[str, str]:
        return {
            "path": _repo_display_path(path, repo),
            "sha256": _sha256_path(path),
        }

    return {
        "coreAuthorities": [entry(path) for path in core_scripts],
        "domainAdapters": [entry(path) for path in adapter_scripts],
    }


def _is_usable_node_file(path: Path) -> bool:
    """Return whether *path* can be invoked as a Node executable."""

    return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))


def _resolve_node_candidate(value: str) -> str | None:
    """Resolve a command name or filesystem path to an executable."""

    expanded = os.path.expandvars(os.path.expanduser(value.strip()))
    if not expanded:
        return None

    discovered = shutil.which(expanded)
    if discovered:
        return str(Path(discovered).resolve())

    path = Path(expanded)
    if _is_usable_node_file(path):
        return str(path.resolve())
    return None


def playwright_bundled_node_candidates() -> tuple[Path, ...]:
    """Return likely Playwright-bundled Node paths without importing Playwright."""

    try:
        spec = importlib.util.find_spec("playwright")
    except (ImportError, AttributeError, ValueError):
        return ()

    if spec is None or not spec.origin:
        return ()

    driver_dir = Path(spec.origin).resolve().parent / "driver"
    names = ("node.exe", "node") if os.name == "nt" else ("node", "node.exe")
    return tuple(driver_dir / name for name in names)


def resolve_node_executable(node_executable: str | None = None) -> str | None:
    """Resolve Node from an explicit override, PATH, or Playwright's driver."""

    explicit = _safe_string(node_executable)
    if explicit:
        resolved = _resolve_node_candidate(explicit)
        if resolved:
            return resolved
        raise McelTruthAuditError(
            f"Explicit Node.js executable was not found or is not runnable: {explicit}"
        )

    system_node = shutil.which("node")
    if system_node:
        return str(Path(system_node).resolve())

    for candidate in playwright_bundled_node_candidates():
        if _is_usable_node_file(candidate):
            return str(candidate.resolve())
    return None


def run_truth_gate(
    *,
    repo: Path,
    runtime_evidence: Any,
    acceptance_evidence: Any,
    now: str,
    max_evidence_age_ms: int,
    node_executable: str | None = None,
) -> dict[str, Any]:
    """Execute the browser truth authority under a small Node VM bridge."""

    scripts_dir = repo / "main_computer" / "web" / "applications" / "scripts"
    core_scripts = [scripts_dir / name for name in CORE_SCRIPT_NAMES]
    missing = [path for path in core_scripts if not path.exists()]
    if missing:
        raise McelTruthAuditError(
            "Required MCEL truth authority file(s) are missing: "
            + ", ".join(_repo_display_path(path, repo) for path in missing)
        )

    adapter_scripts = discover_adapter_scripts(repo)
    node = resolve_node_executable(node_executable)
    if not node:
        raise McelTruthAuditError(
            "Node.js is required to execute the canonical MCEL JavaScript truth authority. "
            "Install Node.js, pass --node, or install Playwright so its bundled Node runtime "
            "can be discovered automatically."
        )

    payload = {
        "coreScripts": [str(path.resolve()) for path in core_scripts],
        "adapterScripts": [str(path.resolve()) for path in adapter_scripts],
        "runtimeEvidence": runtime_evidence,
        "acceptanceEvidence": acceptance_evidence,
        "now": now,
        "maxEvidenceAgeMs": max(0, int(max_evidence_age_ms)),
    }
    completed = subprocess.run(
        [node, "-e", NODE_TRUTH_BRIDGE],
        cwd=repo,
        input=json.dumps(payload, sort_keys=True),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown Node failure"
        raise McelTruthAuditError(f"MCEL truth authority execution failed: {detail}")

    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise McelTruthAuditError(
            "MCEL truth authority returned malformed JSON."
        ) from exc

    snapshot = _safe_dict(result.get("truthSnapshot"))
    if snapshot.get("schema") != "mcel-app-truth-snapshot-v1":
        raise McelTruthAuditError(
            "MCEL truth authority returned an unsupported snapshot schema: "
            f"{snapshot.get('schema')!r}"
        )

    normalized_diagnostics: list[dict[str, Any]] = []
    for item in _safe_list(result.get("loaderDiagnostics")):
        if not isinstance(item, Mapping):
            continue
        normalized = dict(item)
        raw_path = _safe_string(normalized.get("path"))
        if raw_path:
            normalized["path"] = _repo_display_path(Path(raw_path), repo)
        normalized_diagnostics.append(normalized)
    result["loaderDiagnostics"] = normalized_diagnostics
    result["sourceInventory"] = source_inventory(repo, core_scripts, adapter_scripts)
    return result


def declared_level(truth: Mapping[str, Any]) -> str:
    surface = _safe_dict(truth.get("surface"))
    maturity = _safe_string(surface.get("maturity")).lower()
    if maturity == "semantic-runtime":
        return "semantic-runtime"
    if maturity in {"runtime-baseline", "host-workbench"}:
        return "runtime-baseline"
    return "legacy"


def _blocking_truth_findings(truth: Mapping[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        [
            dict(item)
            for item in _safe_list(truth.get("findings"))
            if isinstance(item, Mapping) and item.get("blocking") is True
        ],
        key=lambda item: (
            _safe_string(item.get("code")),
            _safe_string(item.get("message")),
        ),
    )


def _audit_reason(
    code: str,
    message: str,
    *,
    category: str,
    source: str,
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "category": category,
        "source": source,
        "message": message,
        "detail": dict(detail or {}),
    }


def app_enforcement(
    truth: Mapping[str, Any],
    *,
    require_fresh_runtime: bool,
    require_acceptance: bool,
) -> dict[str, Any]:
    """Translate authoritative truth into CI reasons without rewriting findings."""

    reasons: list[dict[str, Any]] = []
    app_id = _safe_string(truth.get("appId"))
    surface = _safe_dict(truth.get("surface"))
    requirements = _safe_dict(truth.get("requirements"))
    claims = _safe_dict(truth.get("claims"))
    runtime = _safe_dict(_safe_dict(truth.get("evidence")).get("runtime"))
    acceptance = _safe_dict(_safe_dict(truth.get("evidence")).get("acceptance"))

    for item in _blocking_truth_findings(truth):
        reasons.append(
            _audit_reason(
                _safe_string(item.get("code")),
                _safe_string(item.get("message")),
                category="declared-policy-violation",
                source="mcel-app-truth-gate",
                detail=_safe_dict(item.get("detail")),
            )
        )

    if (
        require_fresh_runtime
        and surface.get("conformanceRequired") is True
        and claims.get("runtimeSurfaceProven") is not True
    ):
        reasons.append(
            _audit_reason(
                "audit-required-runtime-proof-missing",
                "The audit policy requires fresh passing runtime proof for every conformance-required app.",
                category="required-evidence-gap",
                source="mcel-truth-audit-policy",
                detail={
                    "appId": app_id,
                    "present": runtime.get("present") is True,
                    "freshness": _safe_string(runtime.get("freshness")),
                    "diagnosisCompleted": runtime.get("diagnosisCompleted") is True,
                    "policyPassed": runtime.get("policyPassed") is True,
                },
            )
        )

    if (
        require_acceptance
        and int(requirements.get("acceptanceContractCount") or 0) > 0
        and claims.get("acceptanceProven") is not True
        and acceptance.get("present") is not True
    ):
        reasons.append(
            _audit_reason(
                "audit-required-acceptance-proof-missing",
                "The audit policy requires repository-bound acceptance evidence for every declared acceptance contract.",
                category="required-evidence-gap",
                source="mcel-truth-audit-policy",
                detail={
                    "appId": app_id,
                    "present": False,
                    "status": _safe_string(acceptance.get("status")),
                },
            )
        )

    reasons.sort(key=lambda item: (item["code"], item["message"]))
    return {
        "status": "fail" if reasons else "pass",
        "blocking": bool(reasons),
        "reasonCount": len(reasons),
        "reasons": reasons,
        "reasonCodes": [item["code"] for item in reasons],
    }


def promotion_readiness(truth: Mapping[str, Any]) -> dict[str, Any]:
    """Describe the next explicit maturity promotion without changing registry state."""

    level = declared_level(truth)
    requirements = _safe_dict(truth.get("requirements"))
    adapter = _safe_dict(truth.get("adapter"))
    surface = _safe_dict(truth.get("surface"))
    claims = _safe_dict(truth.get("claims"))
    runtime = _safe_dict(_safe_dict(truth.get("evidence")).get("runtime"))
    blocking = bool(_blocking_truth_findings(truth))

    if level == "semantic-runtime":
        missing: list[str] = []
        if claims.get("semanticRuntimeProven") is not True:
            missing.append("semantic-runtime-proof")
        return {
            "currentLevel": level,
            "registryMaturity": _safe_string(surface.get("maturity")),
            "nextLevel": None,
            "eligible": None,
            "currentLevelHealthy": claims.get("semanticRuntimeProven") is True,
            "missing": missing,
            "ruleId": "mcel-promotion.semantic-runtime.current-v1",
        }

    if level == "runtime-baseline":
        checks = {
            "requirements-specified": claims.get("specified") is True,
            "full-application-semantic-adapter-ready": adapter.get("fullApplicationSemanticReady") is True,
            "runtime-surface-proven": claims.get("runtimeSurfaceProven") is True,
            "acceptance-proven": claims.get("acceptanceProven") is True,
            "no-blocking-findings": not blocking,
        }
        next_level = "semantic-runtime"
        rule_id = "mcel-promotion.runtime-baseline-to-semantic-runtime-v1"
    else:
        runtime_status = _safe_string(runtime.get("status")).lower()
        checks = {
            "requirements-specified": claims.get("specified") is True,
            "runtime-core-ready": adapter.get("runtimeCoreReady") is True,
            "runtime-evidence-fresh": runtime.get("fresh") is True,
            "runtime-diagnosis-complete": runtime.get("diagnosisCompleted") is True,
            "runtime-scenario-pass": runtime_status == "pass",
            "surface-policy-registered": surface.get("registered") is True,
            "no-blocking-findings": not blocking,
        }
        next_level = "runtime-baseline"
        rule_id = "mcel-promotion.legacy-to-runtime-baseline-v1"

    missing = sorted(key for key, passed in checks.items() if not passed)
    return {
        "currentLevel": level,
        "registryMaturity": _safe_string(surface.get("maturity")),
        "nextLevel": next_level,
        "eligible": not missing,
        "currentLevelHealthy": True,
        "missing": missing,
        "checks": checks,
        "ruleId": rule_id,
    }


def _count_values(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = _safe_string(value) or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def build_audit_report(
    *,
    truth_snapshot: Mapping[str, Any],
    loader_diagnostics: Sequence[Mapping[str, Any]],
    authorities: Mapping[str, Any],
    source_inventory_data: Mapping[str, Any],
    runtime_metadata: Mapping[str, Any],
    acceptance_metadata: Mapping[str, Any],
    repository_provenance: Mapping[str, Any],
    check: bool,
    require_fresh_runtime: bool,
    require_acceptance: bool,
    require_repo_match: bool,
    max_evidence_age_hours: float,
) -> dict[str, Any]:
    """Build the repository audit envelope around the canonical truth snapshot."""

    truth_apps = sorted(
        [dict(item) for item in _safe_list(truth_snapshot.get("apps")) if isinstance(item, Mapping)],
        key=lambda item: _safe_string(item.get("appId")),
    )
    audit_apps: list[dict[str, Any]] = []
    for truth in truth_apps:
        enforcement = app_enforcement(
            truth,
            require_fresh_runtime=require_fresh_runtime,
            require_acceptance=require_acceptance,
        )
        findings = [item for item in _safe_list(truth.get("findings")) if isinstance(item, Mapping)]
        audit_apps.append(
            {
                "appId": _safe_string(truth.get("appId")),
                "declaredLevel": declared_level(truth),
                "registryMaturity": _safe_string(_safe_dict(truth.get("surface")).get("maturity")),
                "overallStatus": _safe_string(truth.get("overallStatus")),
                "claims": dict(_safe_dict(truth.get("claims"))),
                "findingCodes": sorted(_safe_string(item.get("code")) for item in findings),
                "blockingFindingCodes": sorted(
                    _safe_string(item.get("code"))
                    for item in findings
                    if item.get("blocking") is True
                ),
                "enforcement": enforcement,
                "promotion": promotion_readiness(truth),
            }
        )

    evidence_audit_reasons: list[dict[str, Any]] = []
    for label, metadata in (
        ("runtime", runtime_metadata),
        ("acceptance", acceptance_metadata),
    ):
        binding = _safe_dict(metadata.get("repositoryBinding"))
        status = _safe_string(binding.get("status")) or "absent"
        if status == "mismatch":
            evidence_audit_reasons.append(
                _audit_reason(
                    "audit-evidence-repository-mismatch",
                    f"{label.title()} evidence was produced from a different repository source state.",
                    category="evidence-integrity-failure",
                    source="mcel-evidence-provenance",
                    detail={
                        "label": label,
                        "path": _safe_string(metadata.get("path")),
                        "currentFingerprint": _safe_string(binding.get("currentFingerprint")),
                        "evidenceFingerprint": _safe_string(binding.get("evidenceFingerprint")),
                    },
                )
            )
        elif status == "unsupported":
            evidence_audit_reasons.append(
                _audit_reason(
                    "audit-evidence-provenance-unsupported",
                    f"{label.title()} evidence declares unsupported repository provenance.",
                    category="evidence-integrity-failure",
                    source="mcel-evidence-provenance",
                    detail={
                        "label": label,
                        "path": _safe_string(metadata.get("path")),
                        "reason": _safe_string(binding.get("reason")),
                    },
                )
            )
        elif (
            require_repo_match
            and metadata.get("present") is True
            and status != "exact"
        ):
            evidence_audit_reasons.append(
                _audit_reason(
                    "audit-evidence-repository-unbound",
                    f"{label.title()} evidence is not bound to the current repository source state.",
                    category="required-evidence-gap",
                    source="mcel-truth-audit-policy",
                    detail={
                        "label": label,
                        "path": _safe_string(metadata.get("path")),
                        "bindingStatus": status,
                    },
                )
            )

    loader_failures = sorted(
        [
            dict(item)
            for item in loader_diagnostics
            if isinstance(item, Mapping) and item.get("status") == "failed"
        ],
        key=lambda item: _safe_string(item.get("path")),
    )
    audit_level_reasons = evidence_audit_reasons + [
        _audit_reason(
            "audit-authority-load-failed",
            "A statically discovered MCEL domain adapter failed to load during the repository audit.",
            category="audit-integrity-failure",
            source="mcel-truth-audit-loader",
            detail={
                "path": _safe_string(item.get("path")),
                "error": _safe_dict(item.get("error")),
            },
        )
        for item in loader_failures
    ]

    blocking_apps = [app for app in audit_apps if app["enforcement"]["blocking"]]
    app_reason_count = sum(app["enforcement"]["reasonCount"] for app in audit_apps)
    check_failed = bool(audit_level_reasons or blocking_apps)

    promotion_ready = [
        app
        for app in audit_apps
        if app["promotion"].get("eligible") is True
    ]
    nonblocking_finding_count = 0
    blocking_finding_count = 0
    for truth in truth_apps:
        for item in _safe_list(truth.get("findings")):
            if not isinstance(item, Mapping):
                continue
            if item.get("blocking") is True:
                blocking_finding_count += 1
            else:
                nonblocking_finding_count += 1

    summary = {
        "status": "fail" if check_failed else "pass",
        "mode": "check" if check else "report-only",
        "appCount": len(audit_apps),
        "blockingAppCount": len(blocking_apps),
        "blockingReasonCount": app_reason_count + len(audit_level_reasons),
        "blockingFindingCount": blocking_finding_count,
        "nonBlockingFindingCount": nonblocking_finding_count,
        "truthStatusCounts": _count_values(app["overallStatus"] for app in audit_apps),
        "declaredLevelCounts": _count_values(app["declaredLevel"] for app in audit_apps),
        "runtimeSurfaceProvenCount": sum(
            app["claims"].get("runtimeSurfaceProven") is True for app in audit_apps
        ),
        "semanticRuntimeProvenCount": sum(
            app["claims"].get("semanticRuntimeProven") is True for app in audit_apps
        ),
        "promotionReadyCount": len(promotion_ready),
        "promotionReadyAppIds": [app["appId"] for app in promotion_ready],
        "evidenceBindingStatuses": {
            "runtime": _safe_string(
                _safe_dict(runtime_metadata.get("repositoryBinding")).get("status")
            ) or "absent",
            "acceptance": _safe_string(
                _safe_dict(acceptance_metadata.get("repositoryBinding")).get("status")
            ) or "absent",
        },
    }

    return {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "generatedAt": _safe_string(truth_snapshot.get("generatedAt")),
        "configuration": {
            "check": bool(check),
            "maxEvidenceAgeHours": float(max_evidence_age_hours),
            "requireFreshRuntime": bool(require_fresh_runtime),
            "requireAcceptance": bool(require_acceptance),
            "requireRepositoryMatch": bool(require_repo_match),
            "enforcementAuthority": "McelAppTruthGate findings with blocking=true",
            "legacyAppsBlockingByDefault": False,
        },
        "repositoryProvenance": dict(repository_provenance),
        "evidenceInputs": {
            "runtime": dict(runtime_metadata),
            "acceptance": dict(acceptance_metadata),
        },
        "authorities": dict(authorities),
        "sourceInventory": dict(source_inventory_data),
        "loaderDiagnostics": [dict(item) for item in loader_diagnostics],
        "auditLevelReasons": audit_level_reasons,
        "summary": summary,
        "apps": audit_apps,
        "truthSnapshot": dict(truth_snapshot),
    }


def _markdown_bool(value: Any) -> str:
    return "yes" if value is True else "no"


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = _safe_dict(report.get("summary"))
    configuration = _safe_dict(report.get("configuration"))
    evidence = _safe_dict(report.get("evidenceInputs"))
    apps = [item for item in _safe_list(report.get("apps")) if isinstance(item, Mapping)]

    lines = [
        "# MCEL repository truth audit",
        "",
        f"- Schema: `{_safe_string(report.get('schema'))}`",
        f"- Generated: `{_safe_string(report.get('generatedAt'))}`",
        f"- Mode: `{_safe_string(summary.get('mode'))}`",
        f"- Audit status: **{_safe_string(summary.get('status')).upper()}**",
        f"- Repository fingerprint: `{_safe_string(_safe_dict(report.get('repositoryProvenance')).get('fingerprint'))}`",
        f"- Repository fingerprint scope: `{_safe_string(_safe_dict(report.get('repositoryProvenance')).get('scope'))}`",
        f"- Repository selection method: `{_safe_string(_safe_dict(report.get('repositoryProvenance')).get('selectionMethod'))}`",
        f"- Freshness window: `{configuration.get('maxEvidenceAgeHours')} hours`",
        "",
        "## Evidence inputs",
        "",
    ]

    for key in ("runtime", "acceptance"):
        item = _safe_dict(evidence.get(key))
        binding = _safe_dict(item.get("repositoryBinding"))
        lines.append(
            f"- {key.title()}: "
            + (
                f"`{_safe_string(item.get('path'))}` "
                f"(selection `{_safe_string(item.get('selection'))}`, "
                f"schema `{_safe_string(item.get('schema')) or 'unknown'}`, "
                f"repository binding `{_safe_string(binding.get('status')) or 'unknown'}`, "
                f"sha256 `{_safe_string(item.get('sha256'))}`)"
                if item.get("present") is True
                else f"not supplied; selection `{_safe_string(item.get('selection'))}`, "
                f"checked `{_safe_string(item.get('path'))}`"
            )
        )

    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Apps evaluated: `{summary.get('appCount', 0)}`",
            f"- Blocking apps: `{summary.get('blockingAppCount', 0)}`",
            f"- Blocking reasons: `{summary.get('blockingReasonCount', 0)}`",
            f"- Runtime surfaces proven: `{summary.get('runtimeSurfaceProvenCount', 0)}`",
            f"- Semantic runtimes proven: `{summary.get('semanticRuntimeProvenCount', 0)}`",
            f"- Promotion-ready apps: `{summary.get('promotionReadyCount', 0)}`",
            "",
            "## App truth",
            "",
            "| App | Declared level | Truth status | Surface proof | Semantic proof | CI | Next promotion |",
            "|---|---|---|---:|---:|---|---|",
        ]
    )

    for app in apps:
        claims = _safe_dict(app.get("claims"))
        enforcement = _safe_dict(app.get("enforcement"))
        promotion = _safe_dict(app.get("promotion"))
        next_level = _safe_string(promotion.get("nextLevel")) or "—"
        if promotion.get("eligible") is True:
            next_label = f"{next_level} (ready)"
        elif promotion.get("eligible") is False:
            next_label = f"{next_level} ({len(_safe_list(promotion.get('missing')))} gap(s))"
        else:
            next_label = next_level
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{_safe_string(app.get('appId'))}`",
                    _safe_string(app.get("declaredLevel")),
                    _safe_string(app.get("overallStatus")),
                    _markdown_bool(claims.get("runtimeSurfaceProven")),
                    _markdown_bool(claims.get("semanticRuntimeProven")),
                    _safe_string(enforcement.get("status")),
                    next_label,
                ]
            )
            + " |"
        )

    blocking_apps = [
        app for app in apps if _safe_dict(app.get("enforcement")).get("blocking") is True
    ]
    lines.extend(["", "## Blocking reasons", ""])
    if not blocking_apps and not _safe_list(report.get("auditLevelReasons")):
        lines.append("None.")
    else:
        for reason in _safe_list(report.get("auditLevelReasons")):
            if isinstance(reason, Mapping):
                lines.append(
                    f"- **repository** `{_safe_string(reason.get('code'))}` — "
                    f"{_safe_string(reason.get('message'))}"
                )
        for app in blocking_apps:
            for reason in _safe_list(_safe_dict(app.get("enforcement")).get("reasons")):
                if isinstance(reason, Mapping):
                    lines.append(
                        f"- **{_safe_string(app.get('appId'))}** "
                        f"`{_safe_string(reason.get('code'))}` — "
                        f"{_safe_string(reason.get('message'))}"
                    )

    lines.extend(["", "## Promotion readiness", ""])
    for app in apps:
        promotion = _safe_dict(app.get("promotion"))
        next_level = promotion.get("nextLevel")
        if not next_level:
            health = "healthy" if promotion.get("currentLevelHealthy") is True else "proof incomplete"
            lines.append(
                f"- `{_safe_string(app.get('appId'))}` is already declared "
                f"`semantic-runtime` ({health})."
            )
            continue
        missing = _safe_list(promotion.get("missing"))
        if promotion.get("eligible") is True:
            lines.append(
                f"- `{_safe_string(app.get('appId'))}` is ready for review toward "
                f"`{_safe_string(next_level)}`."
            )
        else:
            lines.append(
                f"- `{_safe_string(app.get('appId'))}` → `{_safe_string(next_level)}`: "
                + ", ".join(f"`{_safe_string(item)}`" for item in missing)
            )

    lines.extend(
        [
            "",
            "## Enforcement semantics",
            "",
            "- The `McelAppTruthGate` remains the authority for requirements, adapter, surface, runtime, and acceptance findings.",
            "- Default `--check` fails only on truth-gate findings marked `blocking=true` or audit-loader integrity failures.",
            "- Legacy and unenrolled apps are non-blocking merely because they are incomplete.",
            "- Missing or stale runtime evidence is reported separately. Use `--require-fresh-runtime` to make it an explicit CI requirement.",
            "- Missing acceptance proof is reported separately. Use `--require-acceptance` to make it an explicit CI requirement.",
            "- Evidence carrying a different repository fingerprint is rejected and cannot prove app truth.",
            "- Use `--require-repo-match` to reject otherwise unbound legacy evidence.",
            "- `--release-gate` selects the latest reports and enables check, freshness, acceptance, and exact-repository policies together.",
            "- Promotion readiness is advisory and never mutates the app-surface registry.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report_files(report: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "mcel-repository-truth-audit.json"
    markdown_path = output_dir / "mcel-repository-truth-audit.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report) + "\n", encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and optionally enforce the repository-wide MCEL app truth audit."
    )
    parser.add_argument(
        "--runtime-evidence",
        type=Path,
        help=(
            "FLOG/runtime evidence JSON. When omitted, the audit uses "
            f"{DEFAULT_RUNTIME_EVIDENCE} if present."
        ),
    )
    parser.add_argument(
        "--acceptance-evidence",
        type=Path,
        help=(
            "Acceptance evidence JSON. When omitted, the audit uses "
            f"{DEFAULT_ACCEPTANCE_EVIDENCE} if present."
        ),
    )
    parser.add_argument(
        "--latest-runtime-evidence",
        action="store_true",
        help=(
            "Select the newest schema-valid FLOG report under "
            f"{RUNTIME_EVIDENCE_SEARCH_ROOT}."
        ),
    )
    parser.add_argument(
        "--latest-acceptance-evidence",
        action="store_true",
        help=(
            "Select the newest acceptance report under "
            f"{ACCEPTANCE_EVIDENCE_SEARCH_ROOT}."
        ),
    )
    parser.add_argument(
        "--max-evidence-age-hours",
        type=float,
        default=float(DEFAULT_MAX_EVIDENCE_AGE_HOURS),
        help="Freshness window for runtime evidence (default: 168 hours / 7 days).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Report directory (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Return exit code 1 when declared MCEL policy violations are blocking.",
    )
    parser.add_argument(
        "--require-fresh-runtime",
        action="store_true",
        help="In addition to declared violations, require fresh passing runtime proof for enrolled apps.",
    )
    parser.add_argument(
        "--require-acceptance",
        action="store_true",
        help="In addition to declared violations, require passing evidence for declared acceptance contracts.",
    )
    parser.add_argument(
        "--require-repo-match",
        action="store_true",
        help="Require selected evidence to declare the exact current repository fingerprint.",
    )
    parser.add_argument(
        "--release-gate",
        action="store_true",
        help=(
            "Release-grade shorthand: enable --check, latest runtime/acceptance discovery, "
            "fresh runtime, acceptance, and exact repository matching."
        ),
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Do not write JSON/Markdown artifacts.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the complete JSON audit to stdout.",
    )
    parser.add_argument(
        "--now",
        default="",
        help="ISO-8601 evaluation time for reproducible audits; defaults to current UTC.",
    )
    parser.add_argument(
        "--node",
        default="",
        help=(
            "Explicit Node.js executable path. When omitted, the audit searches PATH "
            "and then Playwright's bundled Node runtime."
        ),
    )
    return parser.parse_args(argv)


def run_audit(args: argparse.Namespace, *, repo: Path | None = None) -> dict[str, Any]:
    repo = (repo or repo_root_from_script()).resolve()
    if args.max_evidence_age_hours < 0:
        raise McelTruthAuditError("--max-evidence-age-hours must be non-negative.")

    release_gate = bool(getattr(args, "release_gate", False))
    check = bool(args.check or release_gate)
    require_fresh_runtime = bool(args.require_fresh_runtime or release_gate)
    require_acceptance = bool(args.require_acceptance or release_gate)
    require_repo_match = bool(getattr(args, "require_repo_match", False) or release_gate)
    latest_runtime = bool(
        getattr(args, "latest_runtime_evidence", False)
        or (release_gate and args.runtime_evidence is None)
    )
    latest_acceptance = bool(
        getattr(args, "latest_acceptance_evidence", False)
        or (release_gate and args.acceptance_evidence is None)
    )

    runtime_path, runtime_selection, runtime_explicit = select_evidence_path(
        repo=repo,
        requested_path=args.runtime_evidence,
        use_latest=latest_runtime,
        default_path=DEFAULT_RUNTIME_EVIDENCE,
        search_root=RUNTIME_EVIDENCE_SEARCH_ROOT,
        label="runtime",
    )
    acceptance_path, acceptance_selection, acceptance_explicit = select_evidence_path(
        repo=repo,
        requested_path=args.acceptance_evidence,
        use_latest=latest_acceptance,
        default_path=DEFAULT_ACCEPTANCE_EVIDENCE,
        search_root=ACCEPTANCE_EVIDENCE_SEARCH_ROOT,
        label="acceptance",
    )

    runtime = load_evidence(
        selected_path=runtime_path,
        selection=runtime_selection,
        explicit=runtime_explicit,
        fallback_display_path=RUNTIME_EVIDENCE_SEARCH_ROOT,
        repo=repo,
        label="runtime",
    )
    acceptance = load_evidence(
        selected_path=acceptance_path,
        selection=acceptance_selection,
        explicit=acceptance_explicit,
        fallback_display_path=ACCEPTANCE_EVIDENCE_SEARCH_ROOT,
        repo=repo,
        label="acceptance",
    )

    repository_provenance = build_repository_provenance(repo)
    runtime_binding = evidence_binding(runtime, repository_provenance)
    acceptance_binding = evidence_binding(acceptance, repository_provenance)
    runtime_metadata = dict(runtime.metadata)
    runtime_metadata["repositoryBinding"] = runtime_binding
    acceptance_metadata = dict(acceptance.metadata)
    acceptance_metadata["repositoryBinding"] = acceptance_binding

    now = _safe_string(args.now) or _utc_now_iso()
    bridge = run_truth_gate(
        repo=repo,
        runtime_evidence=truth_eligible_evidence(runtime, runtime_binding),
        acceptance_evidence=truth_eligible_evidence(acceptance, acceptance_binding),
        now=now,
        max_evidence_age_ms=round(args.max_evidence_age_hours * 60 * 60 * 1000),
        node_executable=_safe_string(args.node) or None,
    )
    report = build_audit_report(
        truth_snapshot=_safe_dict(bridge.get("truthSnapshot")),
        loader_diagnostics=[
            item
            for item in _safe_list(bridge.get("loaderDiagnostics"))
            if isinstance(item, Mapping)
        ],
        authorities=_safe_dict(bridge.get("authorities")),
        source_inventory_data=_safe_dict(bridge.get("sourceInventory")),
        runtime_metadata=runtime_metadata,
        acceptance_metadata=acceptance_metadata,
        repository_provenance=repository_provenance,
        check=check,
        require_fresh_runtime=require_fresh_runtime,
        require_acceptance=require_acceptance,
        require_repo_match=require_repo_match,
        max_evidence_age_hours=float(args.max_evidence_age_hours),
    )
    report["configuration"]["releaseGate"] = release_gate
    return report


def _print_summary(report: Mapping[str, Any], paths: Mapping[str, str] | None) -> None:
    summary = _safe_dict(report.get("summary"))
    print(REPORT_VERSION)
    print(f"status: {summary.get('status', 'unknown')}")
    print(f"mode: {summary.get('mode', 'unknown')}")
    print(f"apps: {summary.get('appCount', 0)}")
    print(f"blocking_apps: {summary.get('blockingAppCount', 0)}")
    print(f"blocking_reasons: {summary.get('blockingReasonCount', 0)}")
    print(f"truth_status_counts: {summary.get('truthStatusCounts', {})}")
    print(f"declared_level_counts: {summary.get('declaredLevelCounts', {})}")
    print(f"runtime_surface_proven: {summary.get('runtimeSurfaceProvenCount', 0)}")
    print(f"semantic_runtime_proven: {summary.get('semanticRuntimeProvenCount', 0)}")
    print(f"promotion_ready: {summary.get('promotionReadyAppIds', [])}")
    print(f"evidence_bindings: {summary.get('evidenceBindingStatuses', {})}")
    if paths:
        print(f"json: {paths.get('json', '')}")
        print(f"markdown: {paths.get('markdown', '')}")


def _write_stdout(text: str) -> None:
    """Write potentially large JSON safely even when stdout is non-blocking."""

    data = text.encode(getattr(sys.stdout, "encoding", None) or "utf-8", errors="replace")
    try:
        fd = sys.stdout.fileno()
    except (AttributeError, OSError):
        sys.stdout.write(text)
        sys.stdout.flush()
        return

    offset = 0
    while offset < len(data):
        try:
            written = os.write(fd, data[offset : offset + 65536])
            if written <= 0:
                raise McelTruthAuditError("stdout closed while writing the audit report.")
            offset += written
        except BlockingIOError:
            select.select([], [fd], [], 1.0)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run_audit(args)
        paths: dict[str, str] | None = None
        if not args.no_write:
            output_dir = _resolve_repo_path(args.output_dir, repo_root_from_script())
            paths = write_report_files(report, output_dir)
            report["artifacts"] = paths

        if args.json:
            _write_stdout(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            _print_summary(report, paths)

        if (
            _safe_dict(report.get("configuration")).get("check") is True
            and _safe_dict(report.get("summary")).get("status") == "fail"
        ):
            return 1
        return 0
    except McelTruthAuditError as exc:
        print(f"mcel truth audit error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
