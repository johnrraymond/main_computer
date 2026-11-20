"""Read-only Patch 3 oracle for canonical-vs-incumbent MCEL source authority.

This module never serves a route, writes a compiler candidate, binds a port,
executes an application intent, or promotes an application. Existing host source
selection stays authoritative until separate operational/browser certification.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_application_ir import compare_application_ir
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_semantic_other_apps_host import (
    APPS,
    SOURCE_ROOT,
    compile_semantic_app,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "mcel.source-authority-shadow.v1"
COLLECTIONS = ("states", "intents", "capabilities", "effects", "surfaces", "layouts")


@dataclass(frozen=True)
class SourceResolution:
    """Paths are observations, not a change to the live host's authority."""

    app: str
    canonical_js: Path
    canonical_html: Path
    incumbent_js: Path
    incumbent_html: Path

    def to_dict(self, repo: Path) -> dict[str, Any]:
        def entry(path: Path) -> dict[str, Any]:
            return {"path": path.relative_to(repo).as_posix(), "exists": path.is_file()}
        return {
            "canonical": {"javascript": entry(self.canonical_js), "html": entry(self.canonical_html)},
            "incumbent": {"javascript": entry(self.incumbent_js), "html": entry(self.incumbent_html)},
            "liveAuthority": "incumbent-unchanged",
        }


def resolve_sources(app: str, repo: Path = ROOT) -> SourceResolution:
    """Resolve independently of the runtime route; no fallback or source rewriting."""
    if app not in APPS:
        raise ValueError(f"No reviewed semantic-host application: {app!r}")
    repo = Path(repo).resolve()
    canonical = repo / "mcel_apps" / app
    incumbent = repo / SOURCE_ROOT / app
    return SourceResolution(app, canonical / "application.js", canonical / "application.html",
                            incumbent / "application.js", incumbent / "application.html")


def _summary(report: Any) -> dict[str, Any]:
    ir = report.normalized_ir if report.valid else None
    return {
        "valid": bool(report.valid),
        "status": report.status,
        "applicationId": report.app_id,
        "semanticFingerprint": report.semantic_fingerprint,
        "sourceBindingFingerprint": report.source_binding_fingerprint,
        "diagnosticCodes": [str(item.get("code", "UNKNOWN")) for item in report.diagnostics],
        "counts": {key: len(ir.get(key, [])) for key in COLLECTIONS} if ir else None,
        "compilerCandidateWritten": report.candidate_directory is not None,
    }


def _ids(ir: Mapping[str, Any], collection: str) -> set[str]:
    # IR schema guarantees IDs, but retain an explicit conservative fallback.
    return {str(item.get("id", "")) for item in ir.get(collection, [])}


def _delta(canonical: Mapping[str, Any], incumbent: Mapping[str, Any]) -> dict[str, Any]:
    changes = {}
    for key in COLLECTIONS:
        left, right = _ids(canonical, key), _ids(incumbent, key)
        changes[key] = {
            "canonicalOnly": sorted(left - right),
            "incumbentOnly": sorted(right - left),
            "shared": len(left & right),
        }
    return changes


def evaluate_source_authority(app: str = "code-editor", repo: Path = ROOT) -> dict[str, Any]:
    """Compile both declarations, compare independently, and *never* certify promotion.

    All reads use fixed, reviewed repository-relative source paths. No HTML
    rendering, browser action, service request, runtime state, or write occurs.
    """
    repo = Path(repo).resolve()
    sources = resolve_sources(app, repo)
    blockers: list[str] = []
    canonical = None
    incumbent = None
    host_ir = None
    host_error = None
    if not sources.canonical_js.is_file():
        blockers.append("MISSING_CANONICAL_JAVASCRIPT")
    if not sources.canonical_html.is_file():
        blockers.append("MISSING_CANONICAL_HTML_PAIR")
    if not sources.incumbent_js.is_file():
        blockers.append("MISSING_INCUMBENT_JAVASCRIPT")
    if not sources.incumbent_html.is_file():
        blockers.append("MISSING_INCUMBENT_HTML_PAIR")

    if sources.canonical_js.is_file():
        canonical = compile_dsl_application(sources.canonical_js, write_candidate=False)
        if not canonical.valid:
            blockers.append("CANONICAL_COMPILE_FAILED")
    if sources.incumbent_js.is_file():
        incumbent = compile_dsl_application(sources.incumbent_js, write_candidate=False)
        if not incumbent.valid:
            blockers.append("INCUMBENT_COMPILE_FAILED")
        else:
            # Match the incumbent's production compilation AND host-side policy,
            # not merely the source fixture, so drift cannot claim parity.
            try:
                host_ir = compile_semantic_app(app, repo)
            except (ValueError, OSError, TypeError, KeyError) as exc:
                host_error = type(exc).__name__
                blockers.append("INCUMBENT_HOST_CONTRACT_REJECTED")

    comparison = None
    delta = None
    if canonical is not None and canonical.valid and incumbent is not None and incumbent.valid:
        canonical_ir = canonical.normalized_ir
        incumbent_ir = incumbent.normalized_ir
        assert canonical_ir is not None and incumbent_ir is not None
        delta = _delta(canonical_ir, incumbent_ir)
        if canonical.app_id != incumbent.app_id:
            blockers.append("APPLICATION_ID_DIVERGENCE")
        if host_ir is not None:
            comparison = compare_application_ir(canonical_ir, host_ir)
            if comparison["status"] != "exact":
                blockers.append("IR_SEMANTICS_DIVERGENCE")
        else:
            # Source-to-source parity is still useful if the incumbent host
            # contract is invalid; it cannot substitute for actual host parity.
            comparison = compare_application_ir(canonical_ir, incumbent_ir)
            blockers.append("HOST_SEMANTIC_PARITY_UNVERIFIED")

    # Intentionally unconditional. Even exact IR equality is not evidence of
    # capability receipts, UI geometry, runtime state preservation, or safety.
    blockers.extend(("LIVE_OPERATION_PARITY_UNVERIFIED", "LIVE_PRESENTATION_PARITY_UNVERIFIED"))
    return {
        "schema": SCHEMA,
        "mode": "offline-read-only-shadow",
        "app": app,
        "sourceResolution": sources.to_dict(repo),
        "canonicalCompile": _summary(canonical) if canonical else None,
        "incumbentCompile": _summary(incumbent) if incumbent else None,
        "incumbentHost": {"valid": host_ir is not None, "errorType": host_error},
        "comparison": comparison,
        "declarationDelta": delta,
        "outcome": "DIVERGED_OR_INCOMPLETE" if blockers[:-2] else "IR_MATCH_UNCERTIFIED",
        "promotion": {"eligible": False, "performed": False, "blockers": sorted(set(blockers))},
        "noninterference": {
            "productionRouteChanged": False,
            "runtimeMounted": False,
            "capabilityInvoked": False,
            "candidateWritten": False,
            "sourceFilesMutated": False,
        },
    }
