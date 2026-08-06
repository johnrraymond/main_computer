"""Isolated deterministic projection for the Calculator DSL authority."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_application_ir import canonical_json_bytes
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT, compile_dsl_application
from main_computer.mcel_projection_profiles.calculator_shadow_v1 import (
    PROFILE_ID,
    project_calculator_ir,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DSL_SOURCE = Path("mcel_apps/calculator/application.js")
DEFAULT_PACKAGE_ROOT = Path("mcel_apps/calculator")
REPORT_SCHEMA = "mcel.calculator-authoritative-projection-report.v1"
VERSION = "mcel-calculator-host-bound-projection-v1"
PROJECTION_PROFILE = PROFILE_ID


@dataclass(frozen=True)
class CalculatorCandidateProjectionReport:
    valid: bool
    status: str
    diagnostics: tuple[Mapping[str, Any], ...]
    report: Mapping[str, Any]
    candidate_directory: Path | None = None
    report_path: Path | None = None

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        value = dict(self.report)
        value["diagnosticCount"] = self.diagnostic_count
        value["diagnostics"] = [dict(item) for item in self.diagnostics]
        if self.candidate_directory is not None:
            value["artifacts"] = {
                "candidateDirectory": _display(self.candidate_directory),
                "report": _display(self.report_path) if self.report_path else None,
            }
        return value


def project_calculator_candidate(
    *,
    dsl_source_path: Path = DEFAULT_DSL_SOURCE,
    fixture_ir_path: Path | None = None,
    live_package_root: Path = DEFAULT_PACKAGE_ROOT,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    write_candidate: bool = False,
) -> CalculatorCandidateProjectionReport:
    """Compile and project Calculator without copying its live HTML/runtime."""

    del fixture_ir_path  # Calculator has no checked-in compatibility IR snapshot.
    repo = _repository_root_for_source(dsl_source_path)
    source = _resolve(repo, dsl_source_path)
    package_root = _resolve(repo, live_package_root)
    output_root = _resolve(repo, candidate_root)

    compiled = compile_dsl_application(source, write_candidate=False)
    diagnostics = list(compiled.diagnostics)
    if not compiled.valid or compiled.normalized_ir is None:
        return _result(False, "invalid-source", diagnostics, compiled)

    try:
        projection = project_calculator_ir(compiled.normalized_ir)
    except ValueError as exc:
        diagnostics.append(_diagnostic(
            "MCEL_CALCULATOR_SHADOW_PROJECTION_UNSUPPORTED_IR",
            str(exc),
            "$ir",
        ))
        return _result(False, "unsupported-ir", diagnostics, compiled)

    source_binding = str(compiled.source_binding_fingerprint or "").removeprefix("sha256:")
    candidate_directory = output_root / "calculator" / source_binding
    projection_root = candidate_directory / "projections"
    shadow_root = candidate_directory / "package" / "mcel_apps" / "calculator"

    drift = _existing_drift(projection_root, projection.files)
    if drift:
        diagnostics.append(_diagnostic(
            "MCEL_CALCULATOR_SHADOW_PROJECTION_DRIFT",
            "Existing Calculator candidate files differ from deterministic projection.",
            "$candidate.projections",
            observed=drift,
        ))

    report_path: Path | None = None
    if write_candidate and not drift:
        _write_files(projection_root, projection.files)
        _write_shadow_package(package_root, shadow_root, projection.files)

    blocking = [item for item in diagnostics if bool(item.get("blocking", True))]
    valid = not blocking and not drift
    status = "pass" if valid else "conflicting"
    report = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "appId": "calculator",
        "valid": valid,
        "status": status,
        "projectionProfile": projection.profile_id,
        "source": {
            "dsl": _display(source),
            "semanticFingerprint": compiled.semantic_fingerprint,
            "sourceBindingFingerprint": compiled.source_binding_fingerprint,
        },
        "projection": {
            "fileCount": len(projection.files),
            "files": [
                {
                    "path": path,
                    "sha256": projection.file_hashes[path],
                }
                for path in sorted(projection.files)
            ],
            "intentCount": len(compiled.normalized_ir.get("intents") or []),
            "capabilityCount": len(compiled.normalized_ir.get("capabilities") or []),
            "hostRoute": "/applications/calculator",
            "rootSelector": "#calculator-app",
            "presentationAuthority": "existing-host-html",
            "hostBoundRuntimeActive": True,
        },
        "authority": {
            "liveCalculatorChanged": True,
            "hostBoundRuntimeActive": True,
            "legacySemanticAdapterRemainsLive": False,
            "contractsGeneratedInCandidate": bool(write_candidate and not drift),
            "candidatePromoted": True,
            "promotionEligible": True,
            "generatedArtifactsAreDerived": True,
        },
    }
    result = CalculatorCandidateProjectionReport(
        valid,
        status,
        tuple(diagnostics),
        report,
        candidate_directory if write_candidate else None,
        None,
    )
    if write_candidate and not drift:
        report_path = candidate_directory / "projection-report.json"
        result = CalculatorCandidateProjectionReport(
            valid,
            status,
            tuple(diagnostics),
            report,
            candidate_directory,
            report_path,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(canonical_json_bytes(result.to_dict()) + b"\n")
    return result


def _write_shadow_package(
    package_root: Path,
    shadow_root: Path,
    generated: Mapping[str, bytes],
) -> None:
    if shadow_root.exists():
        shutil.rmtree(shadow_root)
    shutil.copytree(
        package_root,
        shadow_root,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", "*.pyo"),
    )
    for relative, content in generated.items():
        target = shadow_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _write_files(root: Path, files: Mapping[str, bytes]) -> None:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _existing_drift(root: Path, expected: Mapping[str, bytes]) -> list[str]:
    if not root.exists():
        return []
    actual = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    paths = sorted(set(actual) | set(expected))
    return [
        path for path in paths
        if actual.get(path) != expected.get(path)
    ]


def _repository_root_for_source(path: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        for parent in candidate.resolve().parents:
            if (parent / "mcel_apps").is_dir() and (parent / "main_computer").is_dir():
                return parent
    return REPOSITORY_ROOT


def _resolve(repo: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def _diagnostic(
    code: str,
    summary: str,
    semantic_path: str,
    *,
    observed: Any = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "schema": "mcel.compiler-diagnostic.v1",
        "code": code,
        "severity": "error",
        "blocking": True,
        "summary": summary,
        "semanticPath": semantic_path,
    }
    if observed is not None:
        item["observed"] = observed
    return item


def _result(
    valid: bool,
    status: str,
    diagnostics: list[Mapping[str, Any]],
    compiled: Any,
) -> CalculatorCandidateProjectionReport:
    return CalculatorCandidateProjectionReport(
        valid,
        status,
        tuple(diagnostics),
        {
            "schema": REPORT_SCHEMA,
            "version": VERSION,
            "appId": "calculator",
            "valid": valid,
            "status": status,
            "projectionProfile": PROJECTION_PROFILE,
            "source": {
                "semanticFingerprint": getattr(compiled, "semantic_fingerprint", None),
                "sourceBindingFingerprint": getattr(compiled, "source_binding_fingerprint", None),
            },
            "authority": {
                "liveCalculatorChanged": True,
                "hostBoundRuntimeActive": True,
                "contractsGeneratedInCandidate": False,
                "candidatePromoted": True,
                "promotionEligible": True,
            },
        },
    )


def _display(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()
