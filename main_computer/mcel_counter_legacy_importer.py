"""Read-only importer for the live explicit Contract Counter package.

Wave 3 derives ``mcel.application-ir.v1`` from the checked-in Counter
requirements and contract exports.  It does not read the Counter IR fixture,
compile DSL source, generate package contracts, mutate the live package, reuse
evidence, or promote a candidate.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_application_ir import canonical_json_bytes, validate_application_ir

COUNTER_LEGACY_IMPORT_REPORT_SCHEMA = "mcel.counter-legacy-import-report.v1"
COUNTER_LEGACY_RUNTIME_RESULT_SCHEMA = "mcel.counter-legacy-runtime-result.v1"
COUNTER_LEGACY_IMPORTER_VERSION = "mcel-counter-legacy-importer-wave3"
COUNTER_FRONTEND_ID = "legacy.explicit-package.contract-counter"
COUNTER_FRONTEND_VERSION = "mcel-explicit-package-v1"
DEFAULT_TIMEOUT_MS = 1_000

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COUNTER_ROOT = REPOSITORY_ROOT / "mcel_apps" / "contract-counter"
NODE_RUNTIME = Path(__file__).resolve().with_name("mcel_counter_legacy_runtime.js")
SOURCE_FILES = (
    "contracts/acceptance.js",
    "contracts/domain.js",
    "contracts/intents.js",
    "contracts/layout.js",
    "contracts/observation.js",
    "contracts/surface.js",
    "requirements.md",
)


@dataclass(frozen=True)
class CounterLegacyImportReport:
    valid: bool
    status: str
    app_id: str
    package_root: str
    diagnostics: tuple[Mapping[str, Any], ...]
    normalized_ir: Mapping[str, Any] | None
    semantic_fingerprint: str | None
    source_binding_fingerprint: str | None
    source_files: tuple[Mapping[str, str], ...]
    node_executable: str | None
    node_version: str | None

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def to_dict(self, *, include_ir: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": COUNTER_LEGACY_IMPORT_REPORT_SCHEMA,
            "importer": {"id": "mcel.counter.legacy-importer", "version": COUNTER_LEGACY_IMPORTER_VERSION},
            "valid": self.valid,
            "status": self.status,
            "appId": self.app_id,
            "packageRoot": self.package_root,
            "diagnosticCount": self.diagnostic_count,
            "diagnostics": [dict(item) for item in self.diagnostics],
            "semanticFingerprint": self.semantic_fingerprint,
            "sourceBindingFingerprint": self.source_binding_fingerprint,
            "sourceFiles": [dict(item) for item in self.source_files],
            "environment": {"nodeExecutable": self.node_executable, "nodeVersion": self.node_version},
            "authority": {
                "liveApplicationChanged": False,
                "contractsGenerated": False,
                "candidatePromoted": False,
                "evidenceReused": False,
            },
        }
        if include_ir:
            result["ir"] = self.normalized_ir
        return result


def import_counter_legacy_package(
    package_root: Path = DEFAULT_COUNTER_ROOT,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    node_executable: str | None = None,
) -> CounterLegacyImportReport:
    package_root = package_root.resolve()
    package_display = _display_path(package_root)
    diagnostics: list[Mapping[str, Any]] = []

    # After promotion the legacy contract files are virtual build artifacts, not
    # durable source. Preserve the compatibility API by compiling the live DSL
    # authority directly when the package declares dsl-authoritative status.
    try:
        manifest = json.loads((package_root / "mcel.app.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    authoring = manifest.get("authoring") if isinstance(manifest.get("authoring"), Mapping) else {}
    if authoring.get("status") == "dsl-authoritative":
        from main_computer.mcel_dsl_compiler import compile_dsl_application
        source = package_root / str(authoring.get("source") or "application.js")
        compiled = compile_dsl_application(source, write_candidate=False)
        diagnostics.extend(compiled.diagnostics)
        source_files = [
            {"path": _display_path(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
            {"path": _display_path(package_root / "requirements.md"), "sha256": hashlib.sha256((package_root / "requirements.md").read_bytes()).hexdigest()},
        ]
        if not compiled.valid or compiled.normalized_ir is None:
            return _failure(package_display, diagnostics, source_files=source_files)
        return CounterLegacyImportReport(
            valid=True, status="pass", app_id=compiled.app_id, package_root=package_display,
            diagnostics=tuple(diagnostics), normalized_ir=compiled.normalized_ir,
            semantic_fingerprint=compiled.semantic_fingerprint,
            source_binding_fingerprint=compiled.source_binding_fingerprint,
            source_files=tuple(source_files), node_executable=compiled.node_executable, node_version=compiled.node_version,
        )

    source_text: dict[str, str] = {}
    source_files: list[Mapping[str, str]] = []
    for relative in SOURCE_FILES:
        path = package_root / relative
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            diagnostics.append(_diagnostic("MCEL_COUNTER_LEGACY_SOURCE_UNREADABLE", f"Unable to read {relative}: {exc}", relative))
            continue
        source_text[relative] = content
        source_files.append({"path": _display_path(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    if diagnostics:
        return _failure(package_display, diagnostics, source_files=source_files)

    requirements = _parse_requirements(source_text["requirements.md"], diagnostics)
    node = node_executable or shutil.which("node")
    if not node:
        diagnostics.append(_diagnostic("MCEL_COUNTER_LEGACY_NODE_UNAVAILABLE", "The live explicit-package importer requires Node to read the checked-in JavaScript contract exports.", "$environment"))
        return _failure(package_display, diagnostics, source_files=source_files)
    node_version = _node_version(node)
    runtime_result = _load_contract_exports(node, package_root, timeout_ms, diagnostics)
    if runtime_result is None:
        return _failure(package_display, diagnostics, source_files=source_files, node=node, node_version=node_version)

    try:
        candidate = _build_counter_ir(
            package_root=package_root,
            source_text=source_text,
            source_files=source_files,
            requirements=requirements,
            exports=runtime_result,
            diagnostics=diagnostics,
        )
    except (KeyError, TypeError, ValueError) as exc:
        diagnostics.append(_diagnostic("MCEL_COUNTER_LEGACY_MAPPING_FAILED", str(exc), "$mapping"))
        return _failure(package_display, diagnostics, source_files=source_files, node=node, node_version=node_version)

    if diagnostics:
        return _failure(package_display, diagnostics, source_files=source_files, node=node, node_version=node_version)

    validation = validate_application_ir(candidate)
    diagnostics.extend(item.to_dict() for item in validation.diagnostics)
    if not validation.valid or validation.normalized is None:
        return _failure(package_display, diagnostics, source_files=source_files, node=node, node_version=node_version)

    return CounterLegacyImportReport(
        valid=True,
        status="pass",
        app_id=validation.app_id,
        package_root=package_display,
        diagnostics=tuple(diagnostics),
        normalized_ir=validation.normalized,
        semantic_fingerprint=validation.semantic_fingerprint,
        source_binding_fingerprint=validation.source_binding_fingerprint,
        source_files=tuple(source_files),
        node_executable=node,
        node_version=node_version,
    )


def _load_contract_exports(node: str, package_root: Path, timeout_ms: int, diagnostics: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    try:
        completed = subprocess.run(
            [node, str(NODE_RUNTIME)],
            input=json.dumps({"packageRoot": str(package_root)}, sort_keys=True),
            text=True,
            capture_output=True,
            cwd=REPOSITORY_ROOT,
            timeout=max(2.0, timeout_ms / 1_000 + 2.0),
            check=False,
        )
    except subprocess.TimeoutExpired:
        diagnostics.append(_diagnostic("MCEL_COUNTER_LEGACY_IMPORT_TIMEOUT", f"Contract export import exceeded {timeout_ms} ms.", "$environment"))
        return None
    except OSError as exc:
        diagnostics.append(_diagnostic("MCEL_COUNTER_LEGACY_NODE_EXECUTION_FAILED", str(exc), "$environment"))
        return None
    if completed.returncode != 0:
        diagnostics.append(_diagnostic("MCEL_COUNTER_LEGACY_NODE_EXECUTION_FAILED", (completed.stderr or completed.stdout or "Node importer failed.").strip(), "$environment"))
        return None
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        diagnostics.append(_diagnostic("MCEL_COUNTER_LEGACY_RUNTIME_PROTOCOL_INVALID", f"Node importer returned invalid JSON: {exc}", "$environment"))
        return None
    if result.get("schema") != COUNTER_LEGACY_RUNTIME_RESULT_SCHEMA or not result.get("valid"):
        for item in result.get("diagnostics") or ():
            diagnostics.append(dict(item) if isinstance(item, Mapping) else _diagnostic("MCEL_COUNTER_LEGACY_RUNTIME_PROTOCOL_INVALID", "Invalid runtime diagnostic.", "$environment"))
        if not result.get("diagnostics"):
            diagnostics.append(_diagnostic("MCEL_COUNTER_LEGACY_RUNTIME_PROTOCOL_INVALID", "Node importer returned an invalid result.", "$environment"))
        return None
    exports = result.get("exports")
    if not isinstance(exports, Mapping):
        diagnostics.append(_diagnostic("MCEL_COUNTER_LEGACY_RUNTIME_PROTOCOL_INVALID", "Node importer omitted contract exports.", "$environment"))
        return None
    return exports


def _parse_requirements(text: str, diagnostics: list[Mapping[str, Any]]) -> Mapping[str, str]:
    block_match = re.search(r"```mcel-app\s*\n(?P<body>.*?)\n```", text, flags=re.DOTALL)
    if not block_match:
        diagnostics.append(_diagnostic("MCEL_COUNTER_REQUIREMENTS_APP_BLOCK_MISSING", "requirements.md has no mcel-app block.", "requirements.md"))
        return {}
    values: dict[str, str] = {}
    for line in block_match.group("body").splitlines():
        if line.startswith("  ") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    for key in ("id", "title", "current_runtime_status"):
        if not values.get(key):
            diagnostics.append(_diagnostic("MCEL_COUNTER_REQUIREMENTS_FIELD_MISSING", f"mcel-app is missing {key}.", f"requirements.md:{key}"))
    declared_intents = set(re.findall(r"```mcel-intent.*?^intent:\s*([^\n]+)", text, flags=re.DOTALL | re.MULTILINE))
    if declared_intents != {"increment", "reset", "direct-set"}:
        diagnostics.append(_diagnostic("MCEL_COUNTER_REQUIREMENTS_INTENTS_CONFLICT", "requirements.md must declare increment, reset, and direct-set.", "requirements.md:intents", observed=sorted(declared_intents)))
    return values


def _build_counter_ir(
    *,
    package_root: Path,
    source_text: Mapping[str, str],
    source_files: list[Mapping[str, str]],
    requirements: Mapping[str, str],
    exports: Mapping[str, Any],
    diagnostics: list[Mapping[str, Any]],
) -> dict[str, Any]:
    domain = _mapping(exports, "domain")
    intents_export = _mapping(exports, "intents")
    surface_export = _mapping(exports, "surface")
    layout_export = _mapping(exports, "layout")
    acceptance_export = _mapping(exports, "acceptance")
    observation_export = _mapping(exports, "observation")

    app_id = str(requirements.get("id") or "")
    if app_id != "contract-counter":
        diagnostics.append(_diagnostic("MCEL_COUNTER_APP_ID_CONFLICT", "Live package must identify contract-counter.", "requirements.md:id", observed=app_id))
    for name, record in (("domain", domain), ("surface", surface_export), ("acceptance", acceptance_export), ("observation", observation_export)):
        if str(record.get("appId") or "") != "contract-counter":
            diagnostics.append(_diagnostic("MCEL_COUNTER_CONTRACT_APP_ID_CONFLICT", f"{name} contract appId is not contract-counter.", f"contracts/{name}.js:appId", observed=record.get("appId")))

    initial_state = _mapping(domain, "initialState")
    state_records = []
    for state_name in ("count", "revision"):
        initial = initial_state.get(state_name)
        if not isinstance(initial, int) or isinstance(initial, bool) or initial < 0:
            diagnostics.append(_diagnostic("MCEL_COUNTER_STATE_INVALID", f"{state_name} must be a nonnegative integer.", f"contracts/domain.js:initialState.{state_name}", observed=initial))
        state_records.append({
            "id": f"state:{state_name}", "kind": "state", "authority": "canonical", "sourceName": state_name,
            "schema": {"kind": "integer", "minimum": 0}, "initial": initial,
            "source": _source(package_root, "contracts/domain.js"),
        })

    invariant_records = []
    invariant_ids = set()
    for invariant in _sequence(domain.get("invariants"), "domain.invariants"):
        invariant_id = str(_mapping_value(invariant, "id"))
        check_source = str(_mapping(invariant, "check").get("$functionSource") or "")
        if invariant_id.endswith("count-nonnegative"):
            state_name = "count"
        elif invariant_id.endswith("revision-nonnegative"):
            state_name = "revision"
        else:
            diagnostics.append(_diagnostic("MCEL_COUNTER_INVARIANT_UNSUPPORTED", f"Unsupported Counter invariant {invariant_id}.", "contracts/domain.js:invariants"))
            continue
        if f"state?.{state_name}" not in check_source or f"state.{state_name} >= 0" not in check_source:
            diagnostics.append(_diagnostic("MCEL_COUNTER_INVARIANT_BODY_CONFLICT", f"{invariant_id} no longer proves nonnegative integer state.", f"contracts/domain.js:{invariant_id}"))
        semantic_id = f"invariant:{state_name}-nonnegative"
        invariant_ids.add(semantic_id)
        invariant_records.append({
            "id": semantic_id, "kind": "invariant",
            "check": {"kind": "compare.greater-than-or-equal", "left": {"kind": "state.read", "state": {"ref": f"state:{state_name}"}}, "right": {"kind": "constant", "value": 0}},
            "source": _source(package_root, "contracts/domain.js"),
        })
    if invariant_ids != {"invariant:count-nonnegative", "invariant:revision-nonnegative"}:
        diagnostics.append(_diagnostic("MCEL_COUNTER_INVARIANT_SET_CONFLICT", "Counter must retain both nonnegative invariants.", "contracts/domain.js:invariants", observed=sorted(invariant_ids)))

    intents: list[dict[str, Any]] = []
    effects: list[dict[str, Any]] = []
    for source_key, expected_source_name in (("increment", "increment"), ("reset", "reset"), ("directSet", "directSet")):
        record = _mapping(intents_export, source_key)
        intent_id = str(record.get("id") or "")
        operation_kind = str(record.get("kind") or "")
        risk = str(record.get("risk") or "")
        if source_key == "directSet":
            if intent_id != "direct-set" or operation_kind != "prohibited":
                diagnostics.append(_diagnostic("MCEL_COUNTER_PROHIBITED_INTENT_CONFLICT", "directSet must remain the prohibited direct-set intent.", "contracts/intents.js:directSet"))
            intents.append({
                "id": "intent:direct-set", "kind": "intent", "sourceName": expected_source_name,
                "operationKind": "prohibited", "risk": risk, "reads": [], "writes": [], "input": [], "refusals": [], "invariants": [], "effectRefs": [], "outcomes": ["refused"],
                "reasonCode": "MCEL_CANONICAL_ASSIGNMENT_BYPASSES_OPERATION_AUTHORITY",
                "source": _source(package_root, "contracts/intents.js"),
            })
            continue
        if operation_kind != "mutation" or intent_id != source_key:
            diagnostics.append(_diagnostic("MCEL_COUNTER_MUTATION_INTENT_CONFLICT", f"{source_key} must remain a mutation with matching id.", f"contracts/intents.js:{source_key}"))
        reads = [_state_ref_from_path(value, diagnostics, f"contracts/intents.js:{source_key}.reads") for value in _sequence(record.get("reads"), f"intents.{source_key}.reads")]
        writes = [_state_ref_from_path(value, diagnostics, f"contracts/intents.js:{source_key}.writes") for value in _sequence(record.get("writes"), f"intents.{source_key}.writes")]
        effect_names = [str(value) for value in _sequence(record.get("effects"), f"intents.{source_key}.effects")]
        steps: list[Mapping[str, Any]] = []
        effect_refs: list[Mapping[str, str]] = []
        for effect_name in effect_names:
            target_name, role, step = _counter_effect_mapping(source_key, effect_name, diagnostics)
            effect_id = f"effect:{source_key}.{target_name}-write"
            effect_refs.append({"ref": effect_id})
            steps.append(step)
            effects.append({
                "id": effect_id, "kind": "effect", "effectKind": "canonical-write", "owner": {"ref": f"intent:{source_key}"},
                "authority": {"ref": f"state:{target_name}"}, "target": {"kind": "state.read", "state": {"ref": f"state:{target_name}"}},
                "role": role, "risk": risk, "cardinality": {"minimum": 1, "maximum": 1},
                "allowedFinalDispositions": ["completed", "refused-before-attempt", "failed"],
                "requiredEvidence": ["operation-receipt", "canonical-reconciliation", "visible-outcome"], "cleanupObligations": [],
                "source": _source(package_root, "contracts/intents.js"),
            })
        intents.append({
            "id": f"intent:{source_key}", "kind": "intent", "sourceName": expected_source_name,
            "operationKind": "mutation", "risk": risk, "reads": reads, "writes": writes, "input": [], "refusals": [],
            "invariants": [{"ref": "invariant:count-nonnegative"}, {"ref": "invariant:revision-nonnegative"}],
            "effectRefs": effect_refs, "outcomes": ["committed", "refused"], "cancellable": False,
            "transition": {"kind": "transition.sequence", "steps": steps},
            "source": _source(package_root, "contracts/intents.js"),
        })

    surfaces, surface_node_map, region_nodes = _build_surface(package_root, surface_export, diagnostics)
    layouts = _build_layout(package_root, layout_export, surface_node_map, region_nodes, diagnostics)
    scenarios = _build_scenarios(package_root, acceptance_export, diagnostics)
    _validate_observation(observation_export, surface_export, diagnostics)

    proof = {
        "targetTruthStatus": str(requirements.get("current_runtime_status") or "semantic-runtime-proven"),
        "requiredAuthorities": ["canonical-state", "visible-surface", "operation-receipt"],
        "invariants": invariant_records,
    }
    candidate: dict[str, Any] = {
        "schema": "mcel.application-ir.v1",
        "application": {
            "id": "app:contract-counter", "kind": "application", "appId": "contract-counter",
            "title": str(requirements.get("title") or "Contract Counter"), "semanticVersion": "1",
            "authoringStatus": "legacy-compiled", "targetTruthStatus": proof["targetTruthStatus"],
            "source": _source(package_root, "requirements.md"),
        },
        "models": [], "states": state_records, "derivations": [], "intents": intents, "capabilities": [],
        "effects": effects, "surfaces": surfaces, "layouts": layouts, "scenarios": scenarios, "proof": proof,
        "normalization": {
            "schema": "mcel.application-ir-normalization.v1", "normalizer": "mcel-application-ir-normalizer-v1",
            "objectKeyOrder": "lexicographic", "unorderedSemanticCollections": "stable-id", "semanticSequences": "preserved",
            "canonicalJson": "utf8-json-sorted-keys-no-insignificant-whitespace", "undefinedValues": "rejected",
        },
        "migration": {"state": "legacy-compiled", "sourceFamily": "scaffolded-explicit-package", "knownGaps": ["intent-complete-proof-remains-legacy-evidence"]},
        "provenance": {
            "compiler": {"id": "mcel.counter.legacy-importer", "version": COUNTER_LEGACY_IMPORTER_VERSION},
            "frontend": {"id": COUNTER_FRONTEND_ID, "version": COUNTER_FRONTEND_VERSION, "sourceFiles": source_files},
            "nodeBindings": _node_bindings(candidate_ids=_candidate_semantic_ids(state_records, intents, effects, surfaces, layouts, scenarios, invariant_records), package_root=package_root),
        },
        "fingerprints": {},
    }
    return candidate


def _build_surface(package_root: Path, surface: Mapping[str, Any], diagnostics: list[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, list[str]]]:
    if surface.get("schema") != "mcel.semantic-surface-ir.v1" or surface.get("surfaceId") != "contract-counter.surface.primary":
        diagnostics.append(_diagnostic("MCEL_COUNTER_SURFACE_CONTRACT_CONFLICT", "Counter surface schema or identity changed.", "contracts/surface.js"))
    node_map: dict[str, str] = {}
    region_nodes: dict[str, list[str]] = {}
    nodes: list[dict[str, Any]] = []
    for node in _sequence(surface.get("nodes"), "surface.nodes"):
        record = _mapping_value(node, "id")
        source_id = str(record)
        kind = str(_mapping_value(node, "kind"))
        if source_id == "contract-counter.value" and kind == "state-value" and node.get("statePath") == "count":
            semantic_id = "surface-node:counter.value"
            ir_node = {"id": semantic_id, "kind": "surface-node", "nodeKind": "state-value", "value": {"kind": "state.read", "state": {"ref": "state:count"}}}
        elif source_id == "contract-counter.increment-control" and node.get("intentId") == "increment":
            semantic_id = "surface-node:counter.increment"
            ir_node = {"id": semantic_id, "kind": "surface-node", "nodeKind": "control", "intent": {"ref": "intent:increment"}}
        elif source_id == "contract-counter.reset-control" and node.get("intentId") == "reset":
            semantic_id = "surface-node:counter.reset"
            ir_node = {"id": semantic_id, "kind": "surface-node", "nodeKind": "control", "intent": {"ref": "intent:reset"}}
        elif source_id == "contract-counter.latest-receipt" and kind == "operation-evidence":
            semantic_id = "surface-node:counter.receipt"
            ir_node = {"id": semantic_id, "kind": "surface-node", "nodeKind": "operation-evidence"}
        else:
            diagnostics.append(_diagnostic("MCEL_COUNTER_SURFACE_NODE_UNSUPPORTED", f"Unsupported or changed Counter surface node {source_id}.", "contracts/surface.js:nodes"))
            continue
        node_map[source_id] = semantic_id
        region_nodes.setdefault(str(node.get("regionId") or ""), []).append(semantic_id)
        nodes.append(ir_node)
    expected = {"surface-node:counter.value", "surface-node:counter.increment", "surface-node:counter.reset", "surface-node:counter.receipt"}
    if {node["id"] for node in nodes} != expected:
        diagnostics.append(_diagnostic("MCEL_COUNTER_SURFACE_SET_CONFLICT", "Counter must retain its value, increment, reset, and receipt nodes.", "contracts/surface.js:nodes"))
    return ([{"id": "surface:contract-counter.primary", "kind": "surface", "sourceName": "ContractCounterSurface", "nodes": nodes, "source": _source(package_root, "contracts/surface.js")}], node_map, region_nodes)


def _build_layout(package_root: Path, layout: Mapping[str, Any], node_map: Mapping[str, str], region_nodes: Mapping[str, list[str]], diagnostics: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if layout.get("schema") != "mcel.layout-grammar.v1" or layout.get("surfaceId") != "contract-counter.surface.primary":
        diagnostics.append(_diagnostic("MCEL_COUNTER_LAYOUT_CONTRACT_CONFLICT", "Counter layout schema or surface identity changed.", "contracts/layout.js"))
    before: dict[str, str] = {}
    for constraint in _sequence(layout.get("constraints"), "layout.constraints"):
        if constraint.get("relation") == "before":
            before[str(constraint.get("first"))] = str(constraint.get("second"))
    ordered_regions: list[str] = []
    candidates = set(before) | set(before.values())
    second_values = set(before.values())
    current = next((value for value in candidates if value not in second_values), None)
    while current and current not in ordered_regions:
        ordered_regions.append(current)
        current = before.get(current)
    ordered_children: list[dict[str, str]] = []
    for region in ordered_regions:
        for node_id in region_nodes.get(region, []):
            ordered_children.append({"ref": node_id})
    if [item["ref"] for item in ordered_children] != ["surface-node:counter.value", "surface-node:counter.increment", "surface-node:counter.reset", "surface-node:counter.receipt"]:
        diagnostics.append(_diagnostic("MCEL_COUNTER_LAYOUT_ORDER_CONFLICT", "Counter layout order no longer resolves to value, increment, reset, receipt.", "contracts/layout.js:constraints", observed=ordered_children))
    return [{"id": "layout:contract-counter.primary", "kind": "layout", "surface": {"ref": "surface:contract-counter.primary"}, "orderedChildren": ordered_children, "source": _source(package_root, "contracts/layout.js")}]


def _build_scenarios(package_root: Path, acceptance: Mapping[str, Any], diagnostics: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if acceptance.get("schema") != "mcel.acceptance-suite.v1":
        diagnostics.append(_diagnostic("MCEL_COUNTER_ACCEPTANCE_SCHEMA_CONFLICT", "Counter acceptance suite schema changed.", "contracts/acceptance.js"))
    scenarios: list[dict[str, Any]] = []
    for scenario in _sequence(acceptance.get("scenarios"), "acceptance.scenarios"):
        source_id = str(scenario.get("id") or "")
        suffix = source_id.rsplit(".", 1)[-1]
        when = _mapping(scenario, "when")
        expect = _mapping(scenario, "expect")
        intent_id = str(when.get("intentId") or "")
        if suffix == "increment" and intent_id == "increment" and expect.get("count") == 1 and expect.get("revision") == 1 and expect.get("operationStatus") == "committed":
            steps = [
                {"kind": "claim.equal", "authority": "canonical-state", "actual": {"kind": "state.read", "state": {"ref": "state:count"}}, "expected": {"kind": "constant", "value": 1}},
                {"kind": "claim.equal", "authority": "canonical-state", "actual": {"kind": "state.read", "state": {"ref": "state:revision"}}, "expected": {"kind": "constant", "value": 1}},
                {"kind": "claim.exists", "authority": "visible-surface", "target": {"ref": "surface-node:counter.value"}},
            ]
        elif suffix == "reset" and intent_id == "reset" and expect.get("count") == 0 and expect.get("revision") == 5:
            steps = [{"kind": "claim.equal", "authority": "canonical-state", "actual": {"kind": "state.read", "state": {"ref": "state:count"}}, "expected": {"kind": "constant", "value": 0}}]
        elif suffix == "stale" and intent_id == "increment" and expect.get("code") == "REVISION_STALE" and expect.get("canonicalStateUnchanged") is True:
            steps = [{"kind": "claim.receipt-disposition", "authority": "operation-receipt", "expected": "refused", "code": "REVISION_STALE"}]
        elif suffix == "direct-set" and intent_id == "direct-set" and expect.get("code") == "INTENT_PROHIBITED" and expect.get("canonicalStateUnchanged") is True:
            steps = [{"kind": "claim.receipt-disposition", "authority": "operation-receipt", "expected": "refused", "code": "INTENT_PROHIBITED"}]
        else:
            diagnostics.append(_diagnostic("MCEL_COUNTER_SCENARIO_UNSUPPORTED", f"Unsupported or changed Counter acceptance scenario {source_id}.", "contracts/acceptance.js:scenarios"))
            continue
        scenarios.append({"id": f"scenario:contract-counter.{suffix}", "kind": "scenario", "intent": {"ref": f"intent:{intent_id}"}, "steps": steps, "source": _source(package_root, "contracts/acceptance.js")})
    if {item["id"] for item in scenarios} != {"scenario:contract-counter.increment", "scenario:contract-counter.reset", "scenario:contract-counter.stale", "scenario:contract-counter.direct-set"}:
        diagnostics.append(_diagnostic("MCEL_COUNTER_SCENARIO_SET_CONFLICT", "Counter must retain all four acceptance scenarios.", "contracts/acceptance.js:scenarios"))
    return scenarios


def _validate_observation(observation: Mapping[str, Any], surface: Mapping[str, Any], diagnostics: list[Mapping[str, Any]]) -> None:
    if observation.get("schema") != "mcel.observation-contract.v1" or observation.get("currentStatus") != "operation-linked":
        diagnostics.append(_diagnostic("MCEL_COUNTER_OBSERVATION_CONTRACT_CONFLICT", "Counter observation contract is no longer operation-linked v1.", "contracts/observation.js"))
    observations = _sequence(observation.get("observations"), "observation.observations")
    observed_ids = {str(item.get("semanticNodeId") or "") for item in observations if isinstance(item, Mapping)}
    if not {"contract-counter.value", "contract-counter.latest-receipt"}.issubset(observed_ids):
        diagnostics.append(_diagnostic("MCEL_COUNTER_OBSERVATION_COVERAGE_CONFLICT", "Counter observation must cover value and receipt nodes.", "contracts/observation.js:observations", observed=sorted(observed_ids)))
    known_nodes = {str(item.get("id") or "") for item in _sequence(surface.get("nodes"), "surface.nodes") if isinstance(item, Mapping)}
    unknown = sorted(observed_ids - known_nodes)
    if unknown:
        diagnostics.append(_diagnostic("MCEL_COUNTER_OBSERVATION_NODE_UNRESOLVED", "Counter observation references unknown surface nodes.", "contracts/observation.js:observations", observed=unknown))


def _counter_effect_mapping(intent_id: str, effect_name: str, diagnostics: list[Mapping[str, Any]]) -> tuple[str, str, Mapping[str, Any]]:
    mapping: dict[tuple[str, str], tuple[str, str, Mapping[str, Any]]] = {
        ("increment", "count plus one"): ("count", "count-plus-one", {"kind": "number.increment", "target": {"ref": "state:count"}, "amount": 1}),
        ("increment", "revision plus one"): ("revision", "revision-plus-one", {"kind": "number.increment", "target": {"ref": "state:revision"}, "amount": 1}),
        ("reset", "count zero"): ("count", "count-zero", {"kind": "transition.assign", "target": {"ref": "state:count"}, "value": {"kind": "constant", "value": 0}}),
        ("reset", "revision plus one"): ("revision", "revision-plus-one", {"kind": "number.increment", "target": {"ref": "state:revision"}, "amount": 1}),
    }
    result = mapping.get((intent_id, effect_name))
    if result is None:
        diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_UNSUPPORTED", f"Unsupported Counter effect {effect_name!r} on {intent_id}.", f"contracts/intents.js:{intent_id}.effects"))
        return "unknown", "unknown", {"kind": "constant", "value": None}
    return result


def _candidate_semantic_ids(states: list[dict[str, Any]], intents: list[dict[str, Any]], effects: list[dict[str, Any]], surfaces: list[dict[str, Any]], layouts: list[dict[str, Any]], scenarios: list[dict[str, Any]], invariants: list[dict[str, Any]]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = [("app:contract-counter", "requirements.md")]
    values += [(item["id"], "contracts/domain.js") for item in states]
    values += [(item["id"], "contracts/domain.js") for item in invariants]
    values += [(item["id"], "contracts/intents.js") for item in intents]
    values += [(item["id"], "contracts/intents.js") for item in effects]
    values += [(item["id"], "contracts/surface.js") for item in surfaces]
    values += [(node["id"], "contracts/surface.js") for surface in surfaces for node in surface.get("nodes", [])]
    values += [(item["id"], "contracts/layout.js") for item in layouts]
    values += [(item["id"], "contracts/acceptance.js") for item in scenarios]
    return sorted(values)


def _node_bindings(candidate_ids: list[tuple[str, str]], package_root: Path) -> list[dict[str, Any]]:
    return [{"id": f"binding:{semantic_id}", "semanticId": semantic_id, "source": _source(package_root, relative)} for semantic_id, relative in candidate_ids]


def _state_ref_from_path(value: Any, diagnostics: list[Mapping[str, Any]], semantic_path: str) -> Mapping[str, str]:
    text = str(value)
    if text not in {"state.count", "state.revision"}:
        diagnostics.append(_diagnostic("MCEL_COUNTER_STATE_PATH_UNSUPPORTED", f"Unsupported Counter state path {text}.", semantic_path))
    return {"ref": "state:" + text.removeprefix("state.")}


def _source(package_root: Path, relative: str) -> dict[str, Any]:
    path = package_root / relative
    line_count = max(1, len(path.read_text(encoding="utf-8").splitlines()))
    return {"file": _display_path(path), "kind": "legacy-source-binding", "frontend": COUNTER_FRONTEND_ID, "start": {"line": 1, "column": 1}, "end": {"line": line_count, "column": 1}}


def _mapping(container: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = container.get(key)
    if not isinstance(value, Mapping):
        raise TypeError(f"{key} must be an object.")
    return value


def _mapping_value(container: Any, key: str) -> Any:
    if not isinstance(container, Mapping) or key not in container:
        raise TypeError(f"Expected object field {key}.")
    return container[key]


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be an array.")
    return value


def _diagnostic(code: str, summary: str, semantic_path: str, *, observed: Any = None) -> Mapping[str, Any]:
    return {"schema": "mcel.compiler-diagnostic.v1", "code": code, "severity": "error", "blocking": True, "stage": "compile", "repairStage": "migration-mapping", "semanticPath": semantic_path, "summary": summary, "problem": summary, "observed": observed, "expected": None, "safeRepairs": [], "invalidations": [], "rerun": [], "relatedSemanticIds": []}


def _failure(package_display: str, diagnostics: list[Mapping[str, Any]], *, source_files: list[Mapping[str, str]], node: str | None = None, node_version: str | None = None) -> CounterLegacyImportReport:
    return CounterLegacyImportReport(False, "invalid-legacy-package", "contract-counter", package_display, tuple(diagnostics), None, None, None, tuple(source_files), node, node_version)


def _node_version(node: str) -> str | None:
    try:
        completed = subprocess.run([node, "--version"], text=True, capture_output=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() or None


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()
