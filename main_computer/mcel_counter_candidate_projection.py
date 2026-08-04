"""Isolated Counter explicit-package projection from canonical MCEL IR.

Wave 4 generates a candidate package beneath runtime state, compares every
projection with the live explicit package, re-imports the candidate package to
prove semantic round-trip equivalence, and verifies package/runtime
fingerprints.  It never changes the live package, promotes a candidate, or
reuses evidence.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_application_ir import canonical_json_bytes, compare_application_ir
from main_computer.mcel_application_packages import (
    CATALOG_FINGERPRINT_ALGORITHM,
    PACKAGE_FINGERPRINT_ALGORITHM,
    ApplicationPackageRecord,
    build_application_package_catalog,
    fingerprint_package_files,
)
from main_computer.mcel_application_runtime_projection import (
    RUNTIME_PROJECTION_FINGERPRINT_ALGORITHM,
    build_application_runtime_projection,
)
from main_computer.mcel_counter_compatibility import DEFAULT_DSL_SOURCE, DEFAULT_FIXTURE_IR
from main_computer.mcel_counter_legacy_importer import DEFAULT_COUNTER_ROOT, import_counter_legacy_package
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT, compile_dsl_application

REPORT_SCHEMA = "mcel.counter-candidate-projection-report.v1"
VERSION = "mcel-counter-candidate-projection-wave4"
PROJECTION_PROFILE = "mcel.counter.explicit-projection.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GENERATED_CONTRACTS = (
    "contracts/domain.js",
    "contracts/intents.js",
    "contracts/adapter.js",
    "contracts/surface.js",
    "contracts/layout.js",
    "contracts/acceptance.js",
    "contracts/observation.js",
)


@dataclass(frozen=True)
class CounterCandidateProjectionReport:
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
        if self.candidate_directory:
            value["artifacts"] = {
                "candidateDirectory": _display_path(self.candidate_directory),
                "report": _display_path(self.report_path) if self.report_path else None,
            }
        return value


def project_counter_candidate(
    *,
    dsl_source_path: Path = DEFAULT_DSL_SOURCE,
    fixture_ir_path: Path = DEFAULT_FIXTURE_IR,
    live_package_root: Path = DEFAULT_COUNTER_ROOT,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    write_candidate: bool = False,
) -> CounterCandidateProjectionReport:
    diagnostics: list[Mapping[str, Any]] = []
    dsl = compile_dsl_application(dsl_source_path, compare_ir_path=fixture_ir_path, write_candidate=False)
    diagnostics.extend(dsl.diagnostics)
    live = import_counter_legacy_package(live_package_root)
    diagnostics.extend(live.diagnostics)
    if not dsl.valid or dsl.normalized_ir is None or not live.valid or live.normalized_ir is None:
        return _result(False, "invalid-source", diagnostics, dsl=dsl, live=live)

    comparison = compare_application_ir(dsl.normalized_ir, live.normalized_ir)
    if comparison.get("status") != "exact":
        diagnostics.append(_diagnostic("MCEL_COUNTER_PROJECTION_SOURCE_CONFLICT", "DSL and live Counter semantics must be exact before projection.", "$comparison", observed=comparison))
        return _result(False, "semantic-conflict", diagnostics, dsl=dsl, live=live, comparison=comparison)

    try:
        generated = generate_counter_contracts(dsl.normalized_ir)
    except ValueError as exc:
        diagnostics.append(_diagnostic("MCEL_COUNTER_PROJECTION_UNSUPPORTED_IR", str(exc), "$ir"))
        return _result(False, "unsupported-ir", diagnostics, dsl=dsl, live=live, comparison=comparison)

    live_root = live_package_root.resolve()
    live_files = _read_package_files(live_root)
    file_results: list[dict[str, Any]] = []
    for relative in GENERATED_CONTRACTS:
        candidate_bytes = generated[relative]
        live_bytes = live_files.get(relative)
        exact = live_bytes == candidate_bytes
        file_results.append({
            "path": relative,
            "status": "exact" if exact else "conflicting",
            "byteStatus": "exact" if exact else "different",
            "candidateSha256": _sha(candidate_bytes),
            "liveSha256": _sha(live_bytes) if live_bytes is not None else None,
        })
        if not exact:
            diagnostics.append(_diagnostic("MCEL_COUNTER_PROJECTION_FILE_CONFLICT", f"Generated projection differs from live {relative}.", f"$projections.{relative}", observed=file_results[-1]))

    candidate_package_files = dict(live_files)
    candidate_package_files.update(generated)
    candidate_package_fingerprint = fingerprint_package_files(candidate_package_files)

    catalog = build_application_package_catalog(REPOSITORY_ROOT)
    live_record = next((item for item in catalog.packages if item.app_id == "contract-counter"), None)
    if live_record is None or not live_record.valid or not live_record.fingerprint:
        diagnostics.append(_diagnostic("MCEL_COUNTER_LIVE_PACKAGE_RECORD_INVALID", "The live Counter package record is unavailable or invalid.", "$package"))
        return _result(False, "invalid-live-package", diagnostics, dsl=dsl, live=live, comparison=comparison)

    candidate_catalog_fingerprint = _candidate_catalog_fingerprint(catalog.packages, candidate_package_fingerprint)
    candidate_runtime_fingerprint = _runtime_fingerprint(
        generated=generated,
        live_root=live_root,
        package_fingerprint=candidate_package_fingerprint,
        catalog_fingerprint=candidate_catalog_fingerprint,
    )
    live_projection = build_application_runtime_projection(REPOSITORY_ROOT, catalog, live_record)
    manifest = _candidate_runtime_manifest(
        live_projection.manifest,
        package_fingerprint=candidate_package_fingerprint,
        catalog_fingerprint=candidate_catalog_fingerprint,
        runtime_fingerprint=candidate_runtime_fingerprint,
    )
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")

    package_exact = candidate_package_fingerprint == live_record.fingerprint
    runtime_exact = candidate_runtime_fingerprint == live_projection.fingerprint
    if not package_exact:
        diagnostics.append(_diagnostic("MCEL_COUNTER_CANDIDATE_PACKAGE_FINGERPRINT_CONFLICT", "Candidate package fingerprint differs from the live package.", "$fingerprints.package"))
    if not runtime_exact:
        diagnostics.append(_diagnostic("MCEL_COUNTER_CANDIDATE_RUNTIME_FINGERPRINT_CONFLICT", "Candidate runtime projection fingerprint differs from the live projection.", "$fingerprints.runtime"))

    source_binding = str(dsl.source_binding_fingerprint or "").removeprefix("sha256:")
    candidate_directory = candidate_root.resolve() / "contract-counter" / source_binding
    expected_outputs = {**generated, "mcel.runtime.json": manifest_bytes}
    drift = _existing_generated_drift(candidate_directory / "projections", expected_outputs)
    if drift:
        diagnostics.append(_diagnostic("MCEL_COUNTER_CANDIDATE_GENERATED_DRIFT", "Existing candidate-generated files differ from the deterministic projection.", "$candidate.projections", observed=drift))

    roundtrip_status = "not-run"
    roundtrip_fingerprint: str | None = None
    report_path: Path | None = None
    if write_candidate and not drift:
        projections_root = candidate_directory / "projections"
        shadow_root = candidate_directory / "package" / "mcel_apps" / "contract-counter"
        _write_outputs(projections_root, expected_outputs)
        _write_shadow_package(live_root, shadow_root, generated)
        roundtrip = import_counter_legacy_package(shadow_root)
        diagnostics.extend(roundtrip.diagnostics)
        roundtrip_fingerprint = roundtrip.semantic_fingerprint
        if roundtrip.valid and roundtrip.normalized_ir is not None:
            roundtrip_comparison = compare_application_ir(dsl.normalized_ir, roundtrip.normalized_ir)
            roundtrip_status = str(roundtrip_comparison.get("status") or "conflicting")
        else:
            roundtrip_status = "invalid"
        if roundtrip_status != "exact":
            diagnostics.append(_diagnostic("MCEL_COUNTER_CANDIDATE_ROUNDTRIP_CONFLICT", "Generated candidate package does not import back to the canonical Counter semantics.", "$roundtrip"))

    all_files_exact = all(item["status"] == "exact" for item in file_results)
    exact = all_files_exact and package_exact and runtime_exact and (roundtrip_status in {"not-run", "exact"}) and not any(bool(item.get("blocking", True)) for item in diagnostics)
    status = "exact" if exact else "conflicting"
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "appId": "contract-counter",
        "valid": exact,
        "status": status,
        "projectionProfile": PROJECTION_PROFILE,
        "source": {
            "dsl": _display_path(dsl_source_path.resolve()),
            "semanticFingerprint": dsl.semantic_fingerprint,
            "sourceBindingFingerprint": dsl.source_binding_fingerprint,
        },
        "comparison": comparison,
        "projections": file_results,
        "roundtrip": {"status": roundtrip_status, "semanticFingerprint": roundtrip_fingerprint},
        "fingerprints": {
            "package": {"candidate": candidate_package_fingerprint, "live": live_record.fingerprint, "status": "exact" if package_exact else "conflicting"},
            "catalog": {"candidate": candidate_catalog_fingerprint, "live": catalog.fingerprint, "status": "exact" if candidate_catalog_fingerprint == catalog.fingerprint else "conflicting"},
            "runtimeProjection": {"candidate": candidate_runtime_fingerprint, "live": live_projection.fingerprint, "status": "exact" if runtime_exact else "conflicting"},
        },
        "ownership": {
            "generated": list(GENERATED_CONTRACTS) + ["mcel.runtime.json"],
            "shadowCopiedFromLive": sorted(set(candidate_package_files) - set(GENERATED_CONTRACTS)),
        },
        "authority": {
            "liveApplicationChanged": False,
            "contractsGeneratedInCandidate": bool(write_candidate and not drift),
            "candidatePromoted": False,
            "evidenceReused": False,
            "promotionEligible": False,
        },
    }
    result = CounterCandidateProjectionReport(exact, status, tuple(diagnostics), report, candidate_directory if write_candidate else None, None)
    if write_candidate and not drift:
        report_path = candidate_directory / "projection-report.json"
        result = CounterCandidateProjectionReport(exact, status, tuple(diagnostics), report, candidate_directory, report_path)
        report_path.write_bytes(canonical_json_bytes(result.to_dict()) + b"\n")
    return result


def generate_counter_contracts(ir: Mapping[str, Any]) -> dict[str, bytes]:
    _assert_supported_counter_ir(ir)
    files = {
        "contracts/domain.js": _DOMAIN,
        "contracts/intents.js": _INTENTS,
        "contracts/adapter.js": _ADAPTER,
        "contracts/surface.js": _SURFACE,
        "contracts/layout.js": _LAYOUT,
        "contracts/acceptance.js": _ACCEPTANCE,
        "contracts/observation.js": _OBSERVATION,
    }
    return {path: text.encode("utf-8") for path, text in files.items()}


def _assert_supported_counter_ir(ir: Mapping[str, Any]) -> None:
    app = ir.get("application") or {}
    if app.get("appId") != "contract-counter":
        raise ValueError("Wave 4 only supports appId contract-counter.")
    states = {str(item.get("id")): item for item in ir.get("states") or [] if isinstance(item, Mapping)}
    if set(states) != {"state:count", "state:revision"} or any(item.get("authority") != "canonical" for item in states.values()):
        raise ValueError("Counter projection requires canonical count and revision states.")
    intents = {str(item.get("id")): item for item in ir.get("intents") or [] if isinstance(item, Mapping)}
    if set(intents) != {"intent:increment", "intent:reset", "intent:direct-set"}:
        raise ValueError("Counter projection requires increment, reset, and direct-set intents.")
    if intents["intent:direct-set"].get("operationKind") != "prohibited":
        raise ValueError("Counter direct-set must remain prohibited.")
    expected_effects = {
        "effect:increment.count-write", "effect:increment.revision-write",
        "effect:reset.count-write", "effect:reset.revision-write",
    }
    effects = {str(item.get("id")) for item in ir.get("effects") or [] if isinstance(item, Mapping)}
    if effects != expected_effects:
        raise ValueError("Counter projection requires the four canonical-write effects.")
    scenarios = {str(item.get("id")) for item in ir.get("scenarios") or [] if isinstance(item, Mapping)}
    if scenarios != {"scenario:contract-counter.increment", "scenario:contract-counter.reset", "scenario:contract-counter.stale", "scenario:contract-counter.direct-set"}:
        raise ValueError("Counter projection requires all four compatibility scenarios.")


def _read_package_files(root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def _candidate_catalog_fingerprint(records: tuple[ApplicationPackageRecord, ...], package_fingerprint: str) -> str:
    items: list[tuple[str, bytes]] = []
    for record in records:
        fingerprint = package_fingerprint if record.app_id == "contract-counter" else record.fingerprint
        payload = {
            "directoryName": record.directory_name,
            "appId": record.app_id,
            "packageRoot": record.package_root,
            "fingerprint": fingerprint,
            "valid": record.valid,
            "errors": [issue.to_dict() for issue in record.errors],
        }
        items.append((record.package_root, json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")))
    return _framed_hash(CATALOG_FINGERPRINT_ALGORITHM, items)


def _runtime_fingerprint(*, generated: Mapping[str, bytes], live_root: Path, package_fingerprint: str, catalog_fingerprint: str) -> str:
    files: dict[str, bytes] = {}
    for relative in GENERATED_CONTRACTS:
        files[relative] = generated[relative]
    files["src/index.html"] = (live_root / "src/index.html").read_bytes()
    files["src/app.js"] = (live_root / "src/app.js").read_bytes()
    files["src/app.css"] = (live_root / "src/app.css").read_bytes()
    inputs = dict(files)
    inputs["@source-package-fingerprint"] = package_fingerprint.encode("utf-8")
    inputs["@catalog-fingerprint"] = catalog_fingerprint.encode("utf-8")
    return _framed_hash(RUNTIME_PROJECTION_FINGERPRINT_ALGORITHM, [(path, inputs[path]) for path in sorted(inputs)])


def _candidate_runtime_manifest(live_manifest: Mapping[str, Any], *, package_fingerprint: str, catalog_fingerprint: str, runtime_fingerprint: str) -> dict[str, Any]:
    manifest = copy.deepcopy(dict(live_manifest))
    manifest["source"]["packageFingerprint"] = package_fingerprint
    manifest["source"]["catalogFingerprint"] = catalog_fingerprint
    manifest["projection"]["fingerprint"] = runtime_fingerprint
    return manifest


def _framed_hash(marker: str, items: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    digest.update(marker.encode("utf-8")); digest.update(b"\0")
    for name, content in items:
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big")); digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big")); digest.update(content)
    return "sha256:" + digest.hexdigest()


def _existing_generated_drift(root: Path, expected: Mapping[str, bytes]) -> list[str]:
    if not root.exists():
        return []
    drift: list[str] = []
    for relative, content in expected.items():
        path = root / relative
        if path.exists() and path.read_bytes() != content:
            drift.append(relative)
    return sorted(drift)


def _write_outputs(root: Path, files: Mapping[str, bytes]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _write_shadow_package(live_root: Path, shadow_root: Path, generated: Mapping[str, bytes]) -> None:
    if shadow_root.exists():
        shutil.rmtree(shadow_root)
    shutil.copytree(live_root, shadow_root, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", "*.pyo"))
    for relative, content in generated.items():
        path = shadow_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _result(valid: bool, status: str, diagnostics: list[Mapping[str, Any]], *, dsl: Any, live: Any, comparison: Mapping[str, Any] | None = None) -> CounterCandidateProjectionReport:
    return CounterCandidateProjectionReport(valid, status, tuple(diagnostics), {
        "schema": REPORT_SCHEMA, "version": VERSION, "appId": "contract-counter", "valid": valid, "status": status,
        "projectionProfile": PROJECTION_PROFILE, "comparison": comparison,
        "authority": {"liveApplicationChanged": False, "contractsGeneratedInCandidate": False, "candidatePromoted": False, "evidenceReused": False, "promotionEligible": False},
    })


def _diagnostic(code: str, summary: str, semantic_path: str, *, observed: Any = None) -> dict[str, Any]:
    item = {"schema": "mcel.compiler-diagnostic.v1", "code": code, "severity": "error", "blocking": True, "summary": summary, "semanticPath": semantic_path}
    if observed is not None: item["observed"] = observed
    return item


def _sha(content: bytes | None) -> str | None:
    return "sha256:" + hashlib.sha256(content).hexdigest() if content is not None else None


def _display_path(path: Path | None) -> str | None:
    if path is None: return None
    try: return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError: return path.resolve().as_posix()


_DOMAIN = '''export const ContractCounterDomain = Object.freeze({
  schema: "mcel.application-domain.v1",
  appId: "contract-counter",
  initialState: Object.freeze({ count: 0, revision: 0 }),
  invariantReads: Object.freeze(["state.count", "state.revision"]),
  invariants: Object.freeze([
    Object.freeze({
      id: "contract-counter.invariant.count-nonnegative",
      check(state) {
        return Number.isInteger(state?.count) && state.count >= 0;
      }
    }),
    Object.freeze({
      id: "contract-counter.invariant.revision-nonnegative",
      check(state) {
        return Number.isInteger(state?.revision) && state.revision >= 0;
      }
    })
  ])
});
'''
_INTENTS = '''export const ContractCounterIntents = Object.freeze({
  increment: Object.freeze({
    id: "increment",
    kind: "mutation",
    risk: "local-state",
    reads: Object.freeze(["state.count", "state.revision"]),
    writes: Object.freeze(["state.count", "state.revision"]),
    effects: Object.freeze(["count plus one", "revision plus one"])
  }),
  reset: Object.freeze({
    id: "reset",
    kind: "mutation",
    risk: "local-state",
    reads: Object.freeze(["state.count", "state.revision"]),
    writes: Object.freeze(["state.count", "state.revision"]),
    effects: Object.freeze(["count zero", "revision plus one"])
  }),
  directSet: Object.freeze({
    id: "direct-set",
    kind: "prohibited",
    risk: "prohibited",
    reason: "Arbitrary canonical assignment bypasses the MCEL application operation authority."
  })
});
'''
_ADAPTER = '''export const ContractCounterAdapter = Object.freeze({
  schema: "mcel.semantic-adapter.v1",
  appId: "contract-counter",
  adapterId: "contract-counter.adapter.v1",
  currentRuntimeStatus: "scm-controlled",
  targetRuntimeStatus: "fullApplicationSemanticReady",

  preflight({ intentId, input, state }) {
    if (intentId === "direct-set") {
      return Object.freeze({ ok: false, code: "INTENT_PROHIBITED" });
    }
    if (!Object.prototype.hasOwnProperty.call({ increment: true, reset: true }, intentId)) {
      return Object.freeze({ ok: false, code: "INTENT_UNKNOWN" });
    }
    if (input?.expectedRevision !== state?.revision) {
      return Object.freeze({ ok: false, code: "REVISION_STALE" });
    }
    return Object.freeze({ ok: true });
  },

  transition({ intentId, state }) {
    if (intentId === "increment") {
      return Object.freeze({ count: state.count + 1, revision: state.revision + 1 });
    }
    if (intentId === "reset") {
      return Object.freeze({ count: 0, revision: state.revision + 1 });
    }
    throw new Error(`Unsupported authorized intent: ${intentId}`);
  },

  validateEffects({ intentId, before, after }) {
    if (intentId === "increment") {
      return after.count === before.count + 1 && after.revision === before.revision + 1;
    }
    if (intentId === "reset") {
      return after.count === 0 && after.revision === before.revision + 1;
    }
    return false;
  }
});
'''
_SURFACE = '''export const ContractCounterSurface = Object.freeze({
  schema: "mcel.semantic-surface-ir.v1",
  appId: "contract-counter",
  surfaceId: "contract-counter.surface.primary",
  regions: Object.freeze([
    Object.freeze({ id: "contract-counter.region.shell", role: "application" }),
    Object.freeze({ id: "contract-counter.region.value", role: "result" }),
    Object.freeze({ id: "contract-counter.region.controls", role: "toolbar" }),
    Object.freeze({ id: "contract-counter.region.evidence", role: "status" })
  ]),
  nodes: Object.freeze([
    Object.freeze({ id: "contract-counter.value", kind: "state-value", statePath: "count", regionId: "contract-counter.region.value" }),
    Object.freeze({ id: "contract-counter.increment-control", kind: "control", intentId: "increment", regionId: "contract-counter.region.controls" }),
    Object.freeze({ id: "contract-counter.reset-control", kind: "control", intentId: "reset", regionId: "contract-counter.region.controls" }),
    Object.freeze({ id: "contract-counter.latest-receipt", kind: "operation-evidence", regionId: "contract-counter.region.evidence" })
  ])
});
'''
_LAYOUT = '''export const ContractCounterLayout = Object.freeze({
  schema: "mcel.layout-grammar.v1",
  surfaceId: "contract-counter.surface.primary",
  regions: Object.freeze({
    "contract-counter.region.shell": Object.freeze({ direction: "column", alignment: "center", gap: "medium", padding: "large", minInlineSize: 240, maxInlineSize: 640 }),
    "contract-counter.region.value": Object.freeze({ inlineSize: "fill", blockSize: "content", textAlignment: "center" }),
    "contract-counter.region.controls": Object.freeze({ direction: "row", wrap: true, alignment: "center", gap: "small" }),
    "contract-counter.region.evidence": Object.freeze({ inlineSize: "fill", blockSize: "content", scrollOwner: false })
  }),
  constraints: Object.freeze([
    Object.freeze({ id: "contract-counter.layout.value-before-controls", relation: "before", first: "contract-counter.region.value", second: "contract-counter.region.controls" }),
    Object.freeze({ id: "contract-counter.layout.controls-before-evidence", relation: "before", first: "contract-counter.region.controls", second: "contract-counter.region.evidence" }),
    Object.freeze({ id: "contract-counter.layout.controls-usable", relation: "minimum-control-size", target: "contract-counter.region.controls", inline: 44, block: 44 })
  ])
});
'''
_ACCEPTANCE = '''export const ContractCounterAcceptance = Object.freeze({
  schema: "mcel.acceptance-suite.v1",
  appId: "contract-counter",
  currentStatus: "package-local-discovery-live",
  scenarios: Object.freeze([
    Object.freeze({ id: "contract-counter.acceptance.increment", given: Object.freeze({ count: 0, revision: 0 }), when: Object.freeze({ intentId: "increment", expectedRevision: 0 }), expect: Object.freeze({ count: 1, revision: 1, operationStatus: "committed" }) }),
    Object.freeze({ id: "contract-counter.acceptance.reset", given: Object.freeze({ count: 4, revision: 4 }), when: Object.freeze({ intentId: "reset", expectedRevision: 4 }), expect: Object.freeze({ count: 0, revision: 5, operationStatus: "committed" }) }),
    Object.freeze({ id: "contract-counter.acceptance.stale", given: Object.freeze({ count: 2, revision: 2 }), when: Object.freeze({ intentId: "increment", expectedRevision: 1 }), expect: Object.freeze({ code: "REVISION_STALE", canonicalStateUnchanged: true }) }),
    Object.freeze({ id: "contract-counter.acceptance.direct-set", given: Object.freeze({ count: 2, revision: 2 }), when: Object.freeze({ intentId: "direct-set", expectedRevision: 2 }), expect: Object.freeze({ code: "INTENT_PROHIBITED", canonicalStateUnchanged: true }) })
  ])
});
'''
_OBSERVATION = '''export const ContractCounterObservation = Object.freeze({
  schema: "mcel.observation-contract.v1",
  appId: "contract-counter",
  currentStatus: "operation-linked",
  observations: Object.freeze([
    Object.freeze({
      id: "contract-counter.observe.value",
      source: "browser-dom",
      semanticNodeId: "contract-counter.value",
      property: "textContent",
      compareToStatePath: "count",
      normalization: "string"
    }),
    Object.freeze({
      id: "contract-counter.observe.value-visible",
      source: "browser-geometry",
      semanticNodeId: "contract-counter.value",
      property: "visible",
      expected: true,
      normalization: "boolean"
    }),
    Object.freeze({
      id: "contract-counter.observe.receipt",
      source: "browser-dom",
      semanticNodeId: "contract-counter.latest-receipt",
      property: "textContent",
      compareToOperationReceipt: true
    })
  ])
});
'''
