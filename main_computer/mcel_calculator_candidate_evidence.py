"""Candidate evidence for Calculator's host-bound shadow DSL authority.

This runner is intentionally non-promoting. It binds the Calculator DSL compile,
deterministic projection, package/runtime/catalog discovery, generated-vs-legacy
adapter parity, and shadow IR proof into one report without writing generated
contracts into ``mcel_apps/calculator``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_application_ir import canonical_json_bytes
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_calculator_candidate_projection import (
    DEFAULT_CANDIDATE_ROOT,
    DEFAULT_DSL_SOURCE,
    project_calculator_candidate,
)
from main_computer.mcel_calculator_ir_native_proof import run_calculator_ir_native_intent_proof
from main_computer.mcel_calculator_parity import (
    APP_ID,
    run_calculator_browser_parity_probe,
    run_calculator_generated_adapter_parity,
)
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_evidence_provenance import build_repository_provenance


REPORT_SCHEMA = "mcel.calculator-candidate-evidence-report.v1"
REPORT_VERSION = "mcel-calculator-candidate-evidence-v1"
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-compiler-candidates")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class CalculatorCandidateEvidenceError(RuntimeError):
    """Raised when Calculator shadow candidate evidence cannot be produced."""


@dataclass(frozen=True)
class CalculatorCandidateEvidenceResult:
    valid: bool
    status: str
    report: Mapping[str, Any]
    diagnostics: tuple[Mapping[str, Any], ...]
    output_directory: Path | None = None

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        value = dict(self.report)
        value["diagnosticCount"] = self.diagnostic_count
        value["diagnostics"] = [dict(item) for item in self.diagnostics]
        if self.output_directory is not None:
            value.setdefault("artifacts", {})["outputDirectory"] = _display_path(self.output_directory, REPOSITORY_ROOT)
        return value


def run_calculator_candidate_evidence(
    *,
    repo_root: Path = REPOSITORY_ROOT,
    dsl_source_path: Path = DEFAULT_DSL_SOURCE,
    fixture_ir_path: Path | None = None,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
    headed: bool = False,
    write_report: bool = True,
    command_runner: Any = None,
    node_probe_runner: Any = None,
    browser_probe_runner: Any = None,
) -> CalculatorCandidateEvidenceResult:
    del fixture_ir_path, command_runner, node_probe_runner
    repo = Path(repo_root).resolve()
    diagnostics: list[Mapping[str, Any]] = []
    package_root = repo / "mcel_apps" / "calculator"
    live_before = _tree_fingerprint(package_root)

    dsl_source = dsl_source_path if Path(dsl_source_path).is_absolute() else repo / dsl_source_path
    compiled = compile_dsl_application(dsl_source, write_candidate=False)
    diagnostics.extend(compiled.diagnostics)
    if not compiled.valid or compiled.normalized_ir is None:
        diagnostics.append(_diagnostic("MCEL_CALCULATOR_CANDIDATE_DSL_INVALID", "Calculator DSL did not compile.", "$source"))
        return _failure("invalid-dsl", diagnostics, compiled)

    projection = project_calculator_candidate(
        dsl_source_path=dsl_source,
        live_package_root=package_root,
        candidate_root=(candidate_root if Path(candidate_root).is_absolute() else repo / candidate_root),
        write_candidate=write_report,
    )
    diagnostics.extend(projection.diagnostics)
    if not projection.valid:
        diagnostics.append(_diagnostic("MCEL_CALCULATOR_CANDIDATE_PROJECTION_INVALID", "Calculator deterministic projection failed.", "$projection"))
        return _failure("invalid-projection", diagnostics, compiled)

    parity = run_calculator_generated_adapter_parity(repo_root=repo, operation_prefix="candidate")
    diagnostics.extend(parity.diagnostics)
    record = _calculator_record(repo)
    ir_proof = run_calculator_ir_native_intent_proof(
        repo=repo,
        record=record,
        acceptance={},
        observation={},
        headed=headed,
        browser_probe_runner=browser_probe_runner or run_calculator_browser_parity_probe,
    )
    catalog = build_application_package_catalog(repo)
    runtime = build_runtime_projection_set(repo)
    runtime_records = [item for item in runtime.projections if item.app_id == APP_ID]
    provenance = build_repository_provenance(repo)
    live_after = _tree_fingerprint(package_root)

    output_directory = _output_directory(repo, report_root, compiled.source_binding_fingerprint)
    stage_checks = {
        "dslCompilation": compiled.valid and compiled.normalized_ir is not None,
        "candidateProjection": projection.valid,
        "packageValidation": record.valid is True,
        "runtimeProjection": len(runtime_records) == 1 and runtime_records[0].mount_mode == "host-bound",
        "generatedLegacyParity": parity.valid is True,
        "freshBrowserParity": ((ir_proof.get("parityEvidence") or {}).get("freshChromiumObservation") is True),
        "irNativeShadowProof": ir_proof.get("passed") is True,
        "repositoryBinding": bool(provenance.get("fingerprint")),
        "livePackageUnchanged": live_before == live_after,
    }
    for stage, passed in stage_checks.items():
        if not passed:
            diagnostics.append(_diagnostic("MCEL_CALCULATOR_CANDIDATE_STAGE_FAILED", f"Calculator candidate evidence stage failed: {stage}.", f"$stages.{stage}"))

    valid = all(stage_checks.values()) and not any(item.get("blocking", True) for item in diagnostics)
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "appId": APP_ID,
        "status": "pass" if valid else "fail",
        "valid": valid,
        "truthStatus": "fresh-browser-shadow-ir-native-parity",
        "candidate": {
            "semanticFingerprint": compiled.semantic_fingerprint,
            "sourceBindingFingerprint": compiled.source_binding_fingerprint,
            "repositoryProvenance": provenance,
            "packageFingerprint": record.fingerprint,
            "catalogFingerprint": catalog.fingerprint,
            "candidateDirectory": _display_path(projection.candidate_directory, repo) if projection.candidate_directory else None,
        },
        "stages": {name: {"status": "pass" if passed else "fail"} for name, passed in stage_checks.items()},
        "authority": {
            "liveAuthority": "existing-html-calculator-runtime",
            "candidateAuthority": "mcel.dsl.shadow.v1",
            "hostBoundRuntimeActive": True,
            "legacySemanticAdapterRemainsLive": True,
            "liveCalculatorChanged": live_before != live_after,
            "contractsGeneratedInCandidate": bool(write_report),
            "candidatePromoted": False,
            "promotionEligible": False,
            "freshChromiumObservation": ((ir_proof.get("parityEvidence") or {}).get("freshChromiumObservation") is True),
        },
        "parityEvidence": parity.report,
        "irNativeShadowProof": ir_proof,
    }

    if write_report:
        output_directory.mkdir(parents=True, exist_ok=True)
        _write_json(output_directory / "mcel-calculator-candidate-evidence-report.json", report)
        (output_directory / "mcel-calculator-candidate-evidence-report.md").write_text(_render_markdown(report), encoding="utf-8")
        candidate_ir = output_directory / "mcel.application.ir.json"
        candidate_ir.write_bytes(canonical_json_bytes(compiled.normalized_ir) + b"\n")
        report.setdefault("artifacts", {})["candidateIr"] = _display_path(candidate_ir, repo)

    return CalculatorCandidateEvidenceResult(valid, "pass" if valid else "fail", report, tuple(diagnostics), output_directory if write_report else None)


def _calculator_record(repo: Path) -> Any:
    catalog = build_application_package_catalog(repo)
    matches = [item for item in catalog.packages if item.app_id == APP_ID]
    if len(matches) != 1:
        raise CalculatorCandidateEvidenceError("Calculator package was not discovered exactly once.")
    return matches[0]


def _output_directory(repo: Path, report_root: Path, source_binding_fingerprint: str | None) -> Path:
    root = report_root if Path(report_root).is_absolute() else repo / report_root
    source = str(source_binding_fingerprint or "unknown").removeprefix("sha256:")
    return root / APP_ID / source


def _tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    digest.update(b"calculator-tree-fingerprint-v1\0")
    if not root.exists():
        digest.update(b"@missing")
        return "sha256:" + digest.hexdigest()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "__pycache__" or path.suffix in {".pyc", ".pyo"}:
            continue
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def _failure(status: str, diagnostics: list[Mapping[str, Any]], compiled: Any) -> CalculatorCandidateEvidenceResult:
    return CalculatorCandidateEvidenceResult(
        False,
        status,
        {
            "schema": REPORT_SCHEMA,
            "version": REPORT_VERSION,
            "appId": APP_ID,
            "status": status,
            "valid": False,
            "candidate": {
                "semanticFingerprint": getattr(compiled, "semantic_fingerprint", None),
                "sourceBindingFingerprint": getattr(compiled, "source_binding_fingerprint", None),
            },
            "authority": {
                "candidatePromoted": False,
                "promotionEligible": False,
            },
        },
        tuple(diagnostics),
        None,
    )


def _diagnostic(code: str, summary: str, semantic_path: str) -> dict[str, Any]:
    return {
        "schema": "mcel.compiler-diagnostic.v1",
        "code": code,
        "severity": "error",
        "blocking": True,
        "summary": summary,
        "semanticPath": semantic_path,
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _display_path(path: Path | None, repo: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Calculator Candidate Evidence",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Truth status: `{report.get('truthStatus')}`",
        f"- Semantic fingerprint: `{(report.get('candidate') or {}).get('semanticFingerprint')}`",
        f"- Promotion eligible: `{str((report.get('authority') or {}).get('promotionEligible')).lower()}`",
        f"- Fresh Chromium observation: `{str((report.get('authority') or {}).get('freshChromiumObservation')).lower()}`",
        "",
        "## Stages",
        "",
    ]
    for name, stage in sorted((report.get("stages") or {}).items()):
        lines.append(f"- `{name}`: `{stage.get('status')}`")
    lines.append("")
    return "\n".join(lines)
