"""Three-way Counter compatibility for the live package, IR fixture, and DSL.

Wave 3 keeps the explicit package live.  The compatibility report is read-only
unless explicitly written under ``runtime/reports`` and never promotes the DSL
candidate or reuses application evidence.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_application_ir import canonical_json_bytes, compare_application_ir, validate_application_ir
from main_computer.mcel_counter_legacy_importer import DEFAULT_COUNTER_ROOT, import_counter_legacy_package
from main_computer.mcel_dsl_compiler import compile_dsl_application

COMPATIBILITY_REPORT_SCHEMA = "mcel.application-compatibility-report.v1"
COMPATIBILITY_VERSION = "mcel-counter-compatibility-wave3"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_IR = REPOSITORY_ROOT / "tests" / "fixtures" / "mcel_application_ir" / "contract-counter.ir.json"
DEFAULT_DSL_SOURCE = REPOSITORY_ROOT / "mcel_apps" / "contract-counter" / "application.js"
DEFAULT_REPORT_ROOT = REPOSITORY_ROOT / "runtime" / "reports" / "mcel-application-compatibility" / "apps" / "contract-counter"
INCIDENTAL_KEYS = {"source", "sourceName", "authoringStatus", "normalization", "fingerprints", "provenance", "migration"}


@dataclass(frozen=True)
class CounterCompatibilityReport:
    valid: bool
    status: str
    diagnostics: tuple[Mapping[str, Any], ...]
    report: Mapping[str, Any]
    json_path: Path | None = None
    markdown_path: Path | None = None

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.report)
        result["diagnostics"] = [dict(item) for item in self.diagnostics]
        result["diagnosticCount"] = self.diagnostic_count
        if self.json_path or self.markdown_path:
            result["artifacts"] = {
                "json": _display_path(self.json_path) if self.json_path else None,
                "markdown": _display_path(self.markdown_path) if self.markdown_path else None,
            }
        return result


def compare_counter_representations(
    *,
    package_root: Path = DEFAULT_COUNTER_ROOT,
    fixture_ir_path: Path = DEFAULT_FIXTURE_IR,
    dsl_source_path: Path = DEFAULT_DSL_SOURCE,
    write_report: bool = False,
    report_root: Path = DEFAULT_REPORT_ROOT,
) -> CounterCompatibilityReport:
    diagnostics: list[Mapping[str, Any]] = []
    source_authority = _read_source_authority(package_root)
    effective_dsl_source = dsl_source_path
    promoted_source = package_root / "application.js"
    if source_authority == "mcel.dsl.v1" and promoted_source.is_file() and dsl_source_path == DEFAULT_DSL_SOURCE:
        effective_dsl_source = promoted_source
    live = import_counter_legacy_package(package_root)
    diagnostics.extend(live.diagnostics)

    fixture_document: Any = None
    try:
        fixture_document = json.loads(fixture_ir_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        diagnostics.append(_diagnostic("MCEL_COUNTER_FIXTURE_UNREADABLE", f"Unable to read Counter IR fixture: {exc}", "$fixture"))
    fixture = validate_application_ir(fixture_document) if fixture_document is not None else None
    if fixture is not None:
        diagnostics.extend(item.to_dict() for item in fixture.diagnostics)

    dsl = compile_dsl_application(effective_dsl_source)
    diagnostics.extend(dsl.diagnostics)

    live_ir = live.normalized_ir
    fixture_ir = fixture.normalized if fixture and fixture.valid else None
    dsl_ir = dsl.normalized_ir

    live_fixture = compare_application_ir(live_ir, fixture_ir) if live_ir is not None and fixture_ir is not None else {"status": "incomplete"}
    live_dsl = compare_application_ir(live_ir, dsl_ir) if live_ir is not None and dsl_ir is not None else {"status": "incomplete"}
    fixture_dsl = compare_application_ir(fixture_ir, dsl_ir) if fixture_ir is not None and dsl_ir is not None else {"status": "incomplete"}

    source_hash_status, source_hash_details = _compare_source_hashes(
        live.source_files, fixture_ir, package_root=package_root, promoted=source_authority == "mcel.dsl.v1"
    )
    if source_hash_status != "exact":
        diagnostics.append(_diagnostic("MCEL_COUNTER_FIXTURE_SOURCE_BINDING_STALE", "The Counter IR fixture source hashes do not exactly match the live explicit package.", "$fixture.provenance.frontend.sourceFiles", observed=source_hash_details))

    features = _compare_features(live_ir, fixture_ir, dsl_ir)
    nonexact = [item for item in features if item["status"] != "exact"]
    if nonexact:
        diagnostics.append(_diagnostic("MCEL_COUNTER_FEATURE_COMPATIBILITY_FAILED", "At least one Counter semantic feature is not exact across live, fixture, and DSL representations.", "$features", observed=[item["featureId"] for item in nonexact]))

    exact = (
        live.valid
        and bool(fixture and fixture.valid)
        and dsl.valid
        and live_fixture.get("status") == "exact"
        and live_dsl.get("status") == "exact"
        and fixture_dsl.get("status") == "exact"
        and source_hash_status == "exact"
        and not nonexact
        and not any(bool(item.get("blocking", True)) for item in diagnostics)
    )
    status = "exact" if exact else "conflicting"
    semantic_values = {
        "live": live.semantic_fingerprint,
        "fixture": fixture.semantic_fingerprint if fixture else None,
        "dsl": dsl.semantic_fingerprint,
    }
    promoted = source_authority == "mcel.dsl.v1"
    report: dict[str, Any] = {
        "schema": COMPATIBILITY_REPORT_SCHEMA,
        "version": COMPATIBILITY_VERSION,
        "appId": "contract-counter",
        "valid": exact,
        "status": status,
        "migrationState": ("promoted" if promoted else "dual-authored") if exact else "conflicting",
        "compatibility": status,
        "liveAuthority": "mcel.dsl.v1" if promoted else "legacy-explicit-package",
        "candidateAuthority": "none",
        "promotionEligible": False,
        "semanticFingerprints": semantic_values,
        "sourceBindingFingerprints": {
            "live": live.source_binding_fingerprint,
            "fixture": fixture.source_binding_fingerprint if fixture else None,
            "dsl": dsl.source_binding_fingerprint,
        },
        "sourceHashCompatibility": {"status": source_hash_status, "details": source_hash_details},
        "comparisons": {"liveToFixture": live_fixture, "liveToDsl": live_dsl, "fixtureToDsl": fixture_dsl},
        "features": features,
        "representations": {
            "live": {"status": live.status, "source": live.package_root, "importer": "mcel.counter.legacy-importer"},
            "fixture": {"status": "pass" if fixture and fixture.valid else "invalid", "source": _display_path(fixture_ir_path)},
            "dsl": {"status": dsl.status, "source": dsl.source, "compiler": "mcel.dsl.compiler"},
        },
        "authority": {
            "liveApplicationChanged": False,
            "contractsGenerated": promoted,
            "candidatePromoted": promoted,
            "evidenceReused": False,
        },
    }

    json_path: Path | None = None
    markdown_path: Path | None = None
    if write_report:
        report_root = report_root.resolve()
        report_root.mkdir(parents=True, exist_ok=True)
        json_path = report_root / "mcel-application-compatibility-report.json"
        markdown_path = report_root / "mcel-application-compatibility-report.md"
        provisional = CounterCompatibilityReport(exact, status, tuple(diagnostics), report, json_path, markdown_path)
        json_path.write_bytes(canonical_json_bytes(provisional.to_dict()) + b"\n")
        markdown_path.write_text(_render_markdown(provisional), encoding="utf-8")
    return CounterCompatibilityReport(exact, status, tuple(diagnostics), report, json_path, markdown_path)



def _read_source_authority(package_root: Path) -> str:
    manifest_path = package_root / "mcel.app.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "legacy-explicit-package"
    authoring = manifest.get("authoring")
    if isinstance(authoring, Mapping) and authoring.get("status") == "dsl-authoritative":
        return "mcel.dsl.v1"
    return "legacy-explicit-package"

def _canonical_counter_source_path(raw: Any) -> str:
    path = str(raw or "").replace("\\", "/")
    marker = "mcel_apps/contract-counter/"
    index = path.rfind(marker)
    if index >= 0:
        return path[index:]
    package_marker = "contract-counter/"
    index = path.rfind(package_marker)
    return "mcel_apps/" + path[index:] if index >= 0 else path


def _compare_source_hashes(
    live_source_files: tuple[Mapping[str, str], ...],
    fixture_ir: Mapping[str, Any] | None,
    *,
    package_root: Path,
    promoted: bool,
) -> tuple[str, Mapping[str, Any]]:
    frontend = ((fixture_ir or {}).get("provenance") or {}).get("frontend") or {}
    fixture = {
        _canonical_counter_source_path(item.get("path")): str(item.get("sha256") or "")
        for item in frontend.get("sourceFiles") or []
        if isinstance(item, Mapping)
    }
    if promoted:
        physical_text: dict[str, str] = {}
        for path in package_root.rglob("*"):
            if not path.is_file():
                continue
            try:
                physical_text[path.relative_to(package_root).as_posix()] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
        from main_computer.mcel_application_materialization import materialize_generated_package_files

        generated = materialize_generated_package_files(
            _repository_root_for_package(package_root), package_root, physical_text
        )
        live: dict[str, str] = {}
        for canonical_path in fixture:
            prefix = "mcel_apps/contract-counter/"
            relative = canonical_path[len(prefix):] if canonical_path.startswith(prefix) else canonical_path
            physical = package_root / relative
            if physical.is_file():
                content = physical.read_bytes()
            elif relative in generated:
                content = generated[relative]
            else:
                continue
            import hashlib
            live[canonical_path] = hashlib.sha256(content).hexdigest()
    else:
        live = {
            _canonical_counter_source_path(item.get("path")): str(item.get("sha256") or "")
            for item in live_source_files
        }
    missing = sorted(set(live) - set(fixture))
    unexpected = sorted(set(fixture) - set(live))
    mismatched = sorted(path for path in set(live) & set(fixture) if live[path] != fixture[path])
    status = "exact" if not missing and not unexpected and not mismatched else "conflicting"
    return status, {"missingInFixture": missing, "unexpectedInFixture": unexpected, "hashMismatches": mismatched}


def _repository_root_for_package(package_root: Path) -> Path:
    resolved = package_root.resolve()
    for parent in (resolved, *resolved.parents):
        if (parent / "main_computer").is_dir() and (parent / "mcel_apps").is_dir():
            return parent
    return REPOSITORY_ROOT


def _compare_features(live: Mapping[str, Any] | None, fixture: Mapping[str, Any] | None, dsl: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    live_index = _feature_index(live)
    fixture_index = _feature_index(fixture)
    dsl_index = _feature_index(dsl)
    feature_ids = sorted(set(live_index) | set(fixture_index) | set(dsl_index))
    results: list[dict[str, Any]] = []
    for feature_id in feature_ids:
        values = {"live": live_index.get(feature_id), "fixture": fixture_index.get(feature_id), "dsl": dsl_index.get(feature_id)}
        present = {name: value is not None for name, value in values.items()}
        exact = all(present.values()) and canonical_json_bytes(values["live"]) == canonical_json_bytes(values["fixture"]) == canonical_json_bytes(values["dsl"])
        results.append({"featureId": feature_id, "status": "exact" if exact else "conflicting", "present": present})
    return results


def _feature_index(ir: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ir, Mapping):
        return {}
    result: dict[str, Any] = {}
    application = ir.get("application")
    if isinstance(application, Mapping):
        result[str(application.get("id") or "app:unknown")] = _semantic_value(application)
    for key in ("states", "derivations", "intents", "capabilities", "effects", "layouts", "scenarios"):
        for item in ir.get(key) or []:
            if isinstance(item, Mapping) and item.get("id"):
                result[str(item["id"])] = _semantic_value(item)
    for surface in ir.get("surfaces") or []:
        if not isinstance(surface, Mapping):
            continue
        if surface.get("id"):
            surface_copy = dict(surface)
            surface_copy.pop("nodes", None)
            result[str(surface["id"])] = _semantic_value(surface_copy)
        for node in surface.get("nodes") or []:
            if isinstance(node, Mapping) and node.get("id"):
                result[str(node["id"])] = _semantic_value(node)
    proof = ir.get("proof")
    if isinstance(proof, Mapping):
        for invariant in proof.get("invariants") or []:
            if isinstance(invariant, Mapping) and invariant.get("id"):
                result[str(invariant["id"])] = _semantic_value(invariant)
    return result


def _semantic_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _semantic_value(child) for key, child in value.items() if str(key) not in INCIDENTAL_KEYS}
    if isinstance(value, list):
        return [_semantic_value(child) for child in value]
    return copy.deepcopy(value)


def _render_markdown(report: CounterCompatibilityReport) -> str:
    data = report.report
    lines = [
        "# Contract Counter Application Compatibility Report",
        "",
        f"- Status: `{data['status']}`",
        f"- Migration state: `{data['migrationState']}`",
        f"- Live authority: `{data['liveAuthority']}`",
        f"- Candidate authority: `{data['candidateAuthority']}`",
        f"- Promotion eligible: `{str(data['promotionEligible']).lower()}`",
        "",
        "## Semantic fingerprints",
        "",
    ]
    for name, value in data["semanticFingerprints"].items():
        lines.append(f"- {name}: `{value}`")
    lines += ["", "## Feature compatibility", "", "| Feature | Status |", "|---|---|"]
    for item in data["features"]:
        lines.append(f"| `{item['featureId']}` | `{item['status']}` |")
    lines += ["", "## Authority", "", "The legacy explicit package remains live. No contracts were generated, no candidate was promoted, and no evidence was reused.", ""]
    if report.diagnostics:
        lines += ["## Diagnostics", ""]
        for item in report.diagnostics:
            lines.append(f"- `{item.get('code')}` — {item.get('summary')}")
        lines.append("")
    return "\n".join(lines)


def _diagnostic(code: str, summary: str, semantic_path: str, *, observed: Any = None) -> Mapping[str, Any]:
    return {"schema": "mcel.compiler-diagnostic.v1", "code": code, "severity": "error", "blocking": True, "stage": "compatibility", "repairStage": "migration-mapping", "semanticPath": semantic_path, "summary": summary, "problem": summary, "observed": observed, "expected": {"status": "exact"}, "safeRepairs": [], "invalidations": [], "rerun": [], "relatedSemanticIds": []}


def _display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()
