"""Compatibility importer from normalized MCEL application definitions to Application IR.

This module imports normalized MCEL application definitions into stable
``mcel.application-ir.v1``.  Generic applications retain explicit compatibility
records for unsupported callbacks.  Contract Workbench additionally applies its
versioned Wave 11 domain-operator profile, replacing all callback execution
regions with native constrained expressions while retaining callback hashes only
as semantic-identity compatibility bindings.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_application_definition_normalizer import (
    NORMALIZER_VERSION,
    build_normalization_plan,
    check_normalization,
)
from main_computer.mcel_application_ir import validate_application_ir

FRONTEND_ID = "mcel.application-definition.v1"
IMPORTER_VERSION = "mcel-application-definition-ir-importer-wave11"


class ApplicationDefinitionIrError(RuntimeError):
    """Raised when a normalized definition cannot be represented safely."""


@dataclass(frozen=True)
class ApplicationDefinitionIrResult:
    valid: bool
    status: str
    app_id: str
    normalized_ir: Mapping[str, Any] | None
    diagnostics: tuple[Mapping[str, Any], ...]
    source_files: tuple[Mapping[str, str], ...]

    @property
    def semantic_fingerprint(self) -> str | None:
        if not self.normalized_ir:
            return None
        return str((self.normalized_ir.get("fingerprints") or {}).get("semantic") or "") or None

    @property
    def source_binding_fingerprint(self) -> str | None:
        if not self.normalized_ir:
            return None
        return str((self.normalized_ir.get("fingerprints") or {}).get("sourceBinding") or "") or None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "mcel.application-definition-ir-import-result.v1",
            "version": IMPORTER_VERSION,
            "appId": self.app_id,
            "valid": self.valid,
            "status": self.status,
            "diagnosticCount": len(self.diagnostics),
            "diagnostics": [dict(item) for item in self.diagnostics],
            "semanticFingerprint": self.semantic_fingerprint,
            "sourceBindingFingerprint": self.source_binding_fingerprint,
            "sourceFiles": [dict(item) for item in self.source_files],
            "normalizedIr": self.normalized_ir,
        }


def import_application_definition(
    app_id: str,
    repo_root: Path,
    *,
    require_fresh: bool = True,
) -> ApplicationDefinitionIrResult:
    repo = repo_root.resolve()
    diagnostics: list[Mapping[str, Any]] = []

    # Promoted applications no longer retain a normalized-definition file in
    # their source tree. Their authoritative DSL already compiles directly to
    # canonical Application IR, so use that authority instead of requiring a
    # materialized legacy normalization artifact.
    package_root = repo / "mcel_apps" / app_id
    manifest_path = package_root / "mcel.app.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    authoring = manifest.get("authoring") if isinstance(manifest.get("authoring"), Mapping) else {}
    if authoring.get("status") == "dsl-authoritative":
        from main_computer.mcel_dsl_compiler import compile_dsl_application
        source_reference = str(authoring.get("source") or "application.js")
        source_path = package_root / source_reference
        compiled = compile_dsl_application(source_path, write_candidate=False)
        diagnostics.extend(compiled.diagnostics)
        if not compiled.valid or compiled.normalized_ir is None:
            return ApplicationDefinitionIrResult(False, "invalid-dsl", app_id, None, tuple(diagnostics), ())
        source_files = ({
            "path": (Path("mcel_apps") / app_id / source_reference).as_posix(),
            "sha256": "sha256:" + hashlib.sha256(source_path.read_bytes()).hexdigest(),
        },)
        return ApplicationDefinitionIrResult(True, "pass", app_id, compiled.normalized_ir, tuple(diagnostics), source_files)
    try:
        plan = build_normalization_plan(app_id, repo)
    except Exception as exc:
        return _failure(app_id, "normalization-unavailable", f"Could not build normalized-definition plan: {exc}")

    fresh, stale = check_normalization(plan)
    if require_fresh and not fresh:
        diagnostics.append(
            _diagnostic(
                "MCEL_DEFINITION_IR_GENERATED_ARTIFACTS_STALE",
                "Generated application-definition artifacts are stale.",
                "$source",
                observed=stale,
            )
        )
        return ApplicationDefinitionIrResult(False, "stale", app_id, None, tuple(diagnostics), ())

    normalized_path = plan.package_root / plan.normalized_reference
    try:
        normalized_document = json.loads(normalized_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _failure(app_id, "normalized-definition-unreadable", f"Could not read normalized definition: {exc}")
    definition = normalized_document.get("definition") if isinstance(normalized_document, Mapping) else None
    if not isinstance(definition, Mapping):
        return _failure(app_id, "normalized-definition-invalid", "Normalized definition does not contain a definition object.")
    if definition.get("id") != app_id:
        return _failure(app_id, "identity-conflict", "Normalized definition app identity does not match the requested application.")

    source_files = _source_files(repo, plan.package_root, plan.definition_path, normalized_path)
    source = {
        "kind": "application-definition-source-binding",
        "frontend": FRONTEND_ID,
        "file": plan.definition_reference,
        "start": {"line": 1, "column": 1},
        "end": {"line": _line_count(plan.definition_path), "column": 1},
    }
    try:
        candidate = definition_to_application_ir(
            definition,
            app_id=app_id,
            source=source,
            source_files=source_files,
            definition_fingerprint=plan.definition_fingerprint,
            normalized_reference=(plan.package_root.relative_to(repo) / plan.normalized_reference).as_posix(),
        )
        validation = validate_application_ir(candidate)
    except Exception as exc:
        return _failure(app_id, "import-failed", f"Could not import normalized definition into Application IR: {exc}")

    diagnostics.extend(item.to_dict() for item in validation.diagnostics)
    valid = validation.valid and validation.normalized is not None
    return ApplicationDefinitionIrResult(
        valid,
        "pass" if valid else "invalid",
        app_id,
        validation.normalized,
        tuple(diagnostics),
        tuple(source_files),
    )


def definition_to_application_ir(
    definition: Mapping[str, Any],
    *,
    app_id: str,
    source: Mapping[str, Any],
    source_files: tuple[Mapping[str, str], ...] | list[Mapping[str, str]],
    definition_fingerprint: str,
    normalized_reference: str,
) -> dict[str, Any]:
    states_by_name = dict(definition.get("state") or {})
    state_records = [_state_record(name, entry, source) for name, entry in sorted(states_by_name.items())]
    derivations = [
        _derivation_record(name, entry, source)
        for name, entry in sorted(states_by_name.items())
        if str((entry or {}).get("authority")) == "derived"
    ]

    invariant_records = [
        {
            "id": _invariant_id(str(entry.get("id") or f"{app_id}.invariant.{index}")),
            "kind": "invariant",
            "sourceName": str(entry.get("id") or ""),
            "check": _opaque_expression(entry.get("check"), result_kind="boolean", declared_inputs=[f"state:{name}" for name in entry.get("reads") or []]),
            "reads": [{"ref": f"state:{name}"} for name in sorted(entry.get("reads") or [])],
            "source": source,
        }
        for index, entry in enumerate(definition.get("invariants") or [])
        if isinstance(entry, Mapping)
    ]

    capabilities = [
        _capability_record(alias, entry, source)
        for alias, entry in sorted((definition.get("capabilities") or {}).items())
        if isinstance(entry, Mapping)
    ]

    intents: list[dict[str, Any]] = []
    effects: list[dict[str, Any]] = []
    for name, operation in sorted((definition.get("operations") or {}).items()):
        if not isinstance(operation, Mapping):
            continue
        intent, owned_effects = _intent_and_effects(name, operation, states_by_name, invariant_records, source)
        intents.append(intent)
        effects.extend(owned_effects)

    surface = _surface_record(definition.get("surface") or {}, source)
    layout = _layout_record(definition.get("layout") or {}, surface["id"], source)
    scenarios = [
        _scenario_record(entry, source)
        for entry in definition.get("acceptance") or []
        if isinstance(entry, Mapping)
    ]

    app_source = dict(source)
    all_semantic_ids: list[str] = [f"app:{app_id}"]
    for collection in (state_records, derivations, intents, capabilities, effects, [surface], [layout], scenarios, invariant_records):
        all_semantic_ids.extend(str(item["id"]) for item in collection if isinstance(item, Mapping) and item.get("id"))
    for node in surface.get("nodes") or []:
        if isinstance(node, Mapping) and node.get("id"):
            all_semantic_ids.append(str(node["id"]))

    proof = definition.get("proof") if isinstance(definition.get("proof"), Mapping) else {}
    candidate = {
        "schema": "mcel.application-ir.v1",
        "application": {
            "id": f"app:{app_id}",
            "kind": "application",
            "appId": app_id,
            "semanticVersion": "1",
            "title": str(definition.get("title") or app_id),
            "targetTruthStatus": str(proof.get("runtimeStatus") or "semantic-runtime-proven"),
            "authoringStatus": "dual-authored",
            "source": app_source,
        },
        "models": [],
        "states": state_records,
        "derivations": derivations,
        "intents": intents,
        "capabilities": capabilities,
        "effects": effects,
        "surfaces": [surface],
        "layouts": [layout],
        "scenarios": scenarios,
        "proof": {
            "invariants": invariant_records,
            "requiredAuthorities": ["canonical-state", "visible-surface", "operation-receipt", "capability-receipt"],
            "targetTruthStatus": str(proof.get("runtimeStatus") or "semantic-runtime-proven"),
            "acceptanceStatus": str(proof.get("acceptanceStatus") or "verified"),
            "browserObservation": str(proof.get("browserObservation") or "scenario-linked"),
        },
        "migration": {
            "state": "dual-authored",
            "sourceFamily": "normalized-definition-compatibility",
            "knownGaps": [
                "candidate-not-promoted",
                "legacy-package-remains-live",
                "opaque-callbacks-require-constrained-expression-replacement",
            ],
            "definitionFingerprint": definition_fingerprint,
            "normalizedDefinition": normalized_reference,
        },
        "provenance": {
            "compiler": {"id": "mcel.application-definition-ir-importer", "version": IMPORTER_VERSION},
            "frontend": {"id": FRONTEND_ID, "version": "1", "sourceFiles": [dict(item) for item in source_files]},
            "nodeBindings": [
                {"id": f"binding:{semantic_id}", "semanticId": semantic_id, "source": app_source}
                for semantic_id in sorted(set(all_semantic_ids))
            ],
            "compatibility": {
                "normalizer": NORMALIZER_VERSION,
                "definitionFingerprint": definition_fingerprint,
                "normalizedDefinition": normalized_reference,
                "acceptanceCount": len(definition.get("acceptance") or []),
                "observationCount": len(definition.get("observations") or []),
                "requiredRuntimeFeatureCount": len(definition.get("requiredRuntimeFeatures") or []),
                "multiInstanceRequired": bool((definition.get("multiInstance") or {}).get("required")),
                "acceptanceFingerprint": _compatibility_fingerprint(definition.get("acceptance") or []),
                "observationFingerprint": _compatibility_fingerprint(definition.get("observations") or []),
            },
        },
    }
    if app_id == "contract-workbench":
        from main_computer.mcel_workbench_expression_profile import upgrade_application_ir

        candidate = upgrade_application_ir(candidate)
    return candidate


def _state_record(name: str, entry: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    authority = str(entry.get("authority") or "canonical")
    record: dict[str, Any] = {
        "id": f"state:{name}",
        "kind": "state",
        "sourceName": name,
        "authority": authority,
        "schema": _schema_record(entry.get("schema")),
        "source": source,
    }
    if authority != "derived":
        record["initial"] = _portable_value(entry.get("initial"))
    if entry.get("description"):
        record["description"] = str(entry.get("description"))
    return record


def _derivation_record(name: str, entry: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    dependencies = [str(value) for value in entry.get("dependencies") or entry.get("from") or []]
    return {
        "id": f"derivation:{name}",
        "kind": "derivation",
        "sourceName": name,
        "target": {"ref": f"state:{name}"},
        "dependsOn": [{"ref": f"state:{value}"} for value in sorted(dependencies)],
        "schema": _schema_record(entry.get("schema")),
        "derive": _opaque_expression(entry.get("compute"), result_kind=_schema_kind(entry.get("schema")), declared_inputs=[f"state:{value}" for value in dependencies]),
        "source": source,
    }


def _capability_record(alias: str, entry: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    operations = []
    for name, operation in sorted((entry.get("operations") or {}).items()):
        if not isinstance(operation, Mapping):
            continue
        operations.append(
            {
                "id": f"capability-operation:{alias}.{name}",
                "kind": "capability-operation",
                "sourceName": name,
                "requestSchema": _schema_record(operation.get("request")),
                "responseSchema": _schema_record(operation.get("response")),
                "stream": operation.get("stream") is True,
                "cancellable": operation.get("cancellable") is True,
            }
        )
    return {
        "id": f"capability:{entry.get('id') or alias}",
        "kind": "capability",
        "sourceName": alias,
        "risk": str(entry.get("risk") or "external"),
        "description": str(entry.get("description") or ""),
        "operations": operations,
        "source": source,
    }


def _intent_and_effects(
    name: str,
    operation: Mapping[str, Any],
    states_by_name: Mapping[str, Any],
    invariants: list[Mapping[str, Any]],
    source: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    kind = str(operation.get("operationKind") or "mutation")
    intent_id = f"intent:{name}"
    inputs = [
        {
            "id": f"input:{name}.{field_name}",
            "kind": "intent-input",
            "sourceName": field_name,
            "sourceClass": str((value or {}).get("sourceKind") or "literal"),
            "sourceBinding": _portable_value(value),
        }
        for field_name, value in sorted((operation.get("payload") or {}).items())
    ]
    reads = [f"state:{value}" for value in operation.get("reads") or []]
    raw_writes = [str(value) for value in operation.get("writes") or []]
    writes = [f"state:{value}" for value in raw_writes] if kind in {"mutation", "async", "capability", "reconciliation"} else []
    invariant_refs = [{"ref": str(item["id"])} for item in invariants]
    effects: list[dict[str, Any]] = []
    for state_name in operation.get("writes") or []:
        state_entry = states_by_name.get(state_name) or {}
        authority = str(state_entry.get("authority") or "canonical")
        effect_kind = {
            "canonical": "canonical-write",
            "renderer-local": "renderer-local-write",
            "provisional": "provisional-write",
            "derived": "surface-publication",
        }.get(authority, "canonical-write")
        effects.append(
            _effect_record(
                f"{name}.{state_name}-write",
                effect_kind,
                intent_id,
                operation,
                target={"kind": "state.read", "state": {"ref": f"state:{state_name}"}},
                authority={"ref": f"state:{state_name}"},
                source=source,
            )
        )
    for alias in operation.get("uses") or []:
        effects.append(
            _effect_record(
                f"{name}.{alias}-request",
                "capability-request",
                intent_id,
                operation,
                target={"kind": "constant", "value": str(alias)},
                authority={"ref": f"capability:{alias if '.' in str(alias) else 'contract-workbench.quote-service'}"},
                source=source,
            )
        )
    provisional = str(operation.get("provisionalPath") or "")
    if provisional:
        effects.append(
            _effect_record(
                f"{name}.{provisional}-provisional",
                "provisional-write",
                intent_id,
                operation,
                target={"kind": "state.read", "state": {"ref": f"state:{provisional}"}},
                authority={"ref": f"state:{provisional}"},
                source=source,
            )
        )
    if kind == "async":
        for suffix, effect_kind in (
            ("cancellation", "cancellation"),
            ("supersession", "supersession"),
            ("late-event-rejection", "late-event-rejection"),
        ):
            effects.append(
                _effect_record(
                    f"{name}.{suffix}",
                    effect_kind,
                    intent_id,
                    operation,
                    target={"kind": "constant", "value": name},
                    authority={"ref": f"intent:{name}"},
                    source=source,
                )
            )
    if kind == "cancel":
        effects.append(
            _effect_record(
                f"{name}.cancellation",
                "cancellation",
                intent_id,
                operation,
                target={"kind": "constant", "value": str(operation.get("cancels") or "")},
                authority={"ref": f"intent:{operation.get('cancels') or name}"},
                source=source,
            )
        )

    intent: dict[str, Any] = {
        "id": intent_id,
        "kind": "intent",
        "sourceName": name,
        "operationKind": kind,
        "risk": str(operation.get("risk") or "local-state"),
        "cancellable": operation.get("cancellable") is True,
        "concurrency": str(operation.get("concurrency") or "serial-per-application"),
        "input": inputs,
        "reads": [{"ref": value} for value in sorted(reads)],
        "writes": [{"ref": value} for value in sorted(writes)],
        "refusals": [],
        "invariants": invariant_refs,
        "effectRefs": [{"ref": str(item["id"])} for item in effects],
        "outcomes": _outcomes(kind),
        "source": source,
    }
    if operation.get("reason"):
        intent["reason"] = str(operation.get("reason"))
    if operation.get("cancels"):
        intent["cancels"] = {"ref": f"intent:{operation.get('cancels')}"}
    if kind == "cancel":
        intent["provisionalWrites"] = [{"ref": f"state:{value}"} for value in sorted(raw_writes)]
    if kind == "prohibited":
        intent["reasonCode"] = "MCEL_CANONICAL_ASSIGNMENT_BYPASSES_OPERATION_AUTHORITY"
    else:
        transition_source = operation.get("transition")
        if kind == "async":
            transition_source = operation.get("commit")
            intent["request"] = _opaque_expression(operation.get("run"), result_kind="record", declared_inputs=reads)
            intent["reconcile"] = _opaque_expression(operation.get("receive"), result_kind="transition", declared_inputs=reads)
            intent["commit"] = _opaque_expression(operation.get("commit"), result_kind="transition", declared_inputs=reads)
        elif kind == "cancel":
            transition_source = operation.get("cancel") if not _is_undefined(operation.get("cancel")) else operation
        if kind != "cancel":
            intent["transition"] = _opaque_write_transition(
                transition_source,
                writes=raw_writes,
                states_by_name=states_by_name,
                declared_inputs=reads,
            )
        if not _is_undefined(operation.get("ensures")):
            intent["ensures"] = _opaque_expression(operation.get("ensures"), result_kind="boolean", declared_inputs=reads + writes)
    return intent, effects


def _effect_record(
    suffix: str,
    effect_kind: str,
    owner: str,
    operation: Mapping[str, Any],
    *,
    target: Mapping[str, Any],
    authority: Mapping[str, Any],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "id": f"effect:{suffix}",
        "kind": "effect",
        "effectKind": effect_kind,
        "owner": {"ref": owner},
        "risk": str(operation.get("risk") or "local-state"),
        "target": dict(target),
        "authority": dict(authority),
        "cardinality": {"minimum": 0 if effect_kind in {"cancellation", "supersession", "late-event-rejection"} else 1, "maximum": 1},
        "allowedFinalDispositions": ["completed", "refused-before-attempt", "failed", "cancelled", "superseded"],
        "requiredEvidence": ["operation-receipt", "canonical-reconciliation", "visible-outcome"],
        "cleanupObligations": ["provisional-state-closed"] if effect_kind in {"cancellation", "supersession", "late-event-rejection"} else [],
        "source": source,
    }


def _surface_record(surface: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    raw_id = str(surface.get("id") or "contract-workbench.surface.primary")
    surface_id = f"surface:{raw_id}"
    nodes = []
    for entry in surface.get("nodes") or []:
        if not isinstance(entry, Mapping):
            continue
        node: dict[str, Any] = {
            "id": f"surface-node:{entry.get('id')}",
            "kind": "surface-node",
            "sourceName": str(entry.get("id") or ""),
            "nodeKind": str(entry.get("nodeKind") or "unknown"),
            "regionId": str(entry.get("regionId") or ""),
        }
        if entry.get("intentId"):
            node["intent"] = {"ref": f"intent:{entry.get('intentId')}"}
        if entry.get("statePath"):
            node["state"] = {"ref": f"state:{str(entry.get('statePath')).split('.', 1)[0]}"}
            node["statePath"] = str(entry.get("statePath"))
        for key in ("property", "transform", "inputType", "localPath", "templateId", "keyPath", "payload", "properties", "source", "when", "content", "accessibility", "item"):
            if key in entry and entry.get(key) not in (None, "", {}, []):
                node[key] = _portable_value(entry.get(key))
        nodes.append(node)
    return {
        "id": surface_id,
        "kind": "surface",
        "sourceName": raw_id,
        "root": _portable_value(surface.get("root")),
        "regions": _portable_value(surface.get("regions") or []),
        "nodes": nodes,
        "source": source,
    }


def _layout_record(layout: Mapping[str, Any], surface_id: str, source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": "layout:contract-workbench.primary",
        "kind": "layout",
        "surface": {"ref": surface_id},
        "grammar": _portable_value(layout),
        "orderedChildren": [
            {"ref": f"surface-node:{node_id}"}
            for node_id in _layout_node_order(layout)
        ],
        "source": source,
    }


def _scenario_record(entry: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    raw_id = str(entry.get("id") or "unknown")
    when = entry.get("when") if isinstance(entry.get("when"), Mapping) else {}
    intent_name = str(when.get("intentId") or entry.get("operationId") or "")
    record: dict[str, Any] = {
        "id": f"scenario:{raw_id}",
        "kind": "scenario",
        "sourceName": raw_id,
        "acceptanceKind": str(entry.get("acceptanceKind") or "workflow"),
        "given": _portable_value(entry.get("given") or {}),
        "when": _portable_value(when),
        "expect": _portable_value(entry.get("expect") or {}),
        "steps": [],
        "source": source,
    }
    if intent_name:
        record["intent"] = {"ref": f"intent:{intent_name}"}
    else:
        record["intent"] = {"ref": "intent:add-contract"}
        record["crossCutting"] = True
    return record



def _opaque_write_transition(
    value: Any,
    *,
    writes: list[str],
    states_by_name: Mapping[str, Any],
    declared_inputs: list[str],
) -> dict[str, Any]:
    function_hash = _function_hash(value)
    steps = []
    for state_name in writes:
        schema = _schema_record((states_by_name.get(state_name) or {}).get("schema"))
        steps.append({
            "kind": "transition.assign",
            "target": {"ref": f"state:{state_name}"},
            "value": {
                "kind": "legacy.opaque-function",
                "language": "javascript",
                "functionHash": function_hash,
                "declaredInputs": sorted(set(declared_inputs)),
                "declaredPurity": "unknown",
                "resultSlot": f"state:{state_name}",
                "type": schema,
                "migration": {
                    "owner": "frontend:contract-workbench-definition-v1",
                    "replacementStatus": "required",
                    "targetExpressionKinds": ["registered-domain-operator", "constrained-expression"],
                },
            },
        })
    if not steps:
        return _opaque_expression(value, result_kind="transition", declared_inputs=declared_inputs)
    return {"kind": "transition.sequence", "steps": steps, "implementationHash": function_hash}


def _compatibility_fingerprint(value: Any) -> str:
    payload = json.dumps(_portable_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()

def _opaque_expression(value: Any, *, result_kind: str, declared_inputs: list[str]) -> dict[str, Any]:
    function_hash = _function_hash(value)
    return {
        "kind": "legacy.opaque-function",
        "language": "javascript",
        "functionHash": function_hash,
        "declaredInputs": sorted(set(declared_inputs)),
        "declaredPurity": "unknown",
        "type": {"kind": result_kind or "unknown"},
        "migration": {
            "owner": "frontend:contract-workbench-definition-v1",
            "replacementStatus": "required",
            "targetExpressionKinds": ["registered-domain-operator", "constrained-expression"],
        },
    }


def _function_hash(value: Any) -> str:
    if isinstance(value, Mapping):
        raw = value.get("sha256") or value.get("functionHash")
        if isinstance(raw, str) and raw:
            return raw if raw.startswith("sha256:") else f"sha256:{raw}"
    payload = json.dumps(_portable_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _schema_record(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping) and value.get("kind") == "schema":
        name = str(value.get("name") or "unknown")
        describe = value.get("describe") if isinstance(value.get("describe"), Mapping) else {}
    elif isinstance(value, Mapping):
        name = str(value.get("name") or value.get("kind") or "unknown")
        describe = value.get("describe") if isinstance(value.get("describe"), Mapping) else dict(value)
    else:
        name, describe = "unknown", {}
    mapping = {"string": "string", "integer": "integer", "number": "number", "boolean": "boolean", "array": "list", "object": "record"}
    result: dict[str, Any] = {"kind": mapping.get(name, name)}
    if result["kind"] == "integer" and describe.get("minimum") is not None:
        result["minimum"] = describe.get("minimum")
    if result["kind"] == "string" and describe.get("minLength") is not None:
        result["minLength"] = describe.get("minLength")
    if describe.get("allowed"):
        result["enum"] = list(describe.get("allowed"))
    if result["kind"] == "list":
        result["items"] = {"kind": str(describe.get("item") or "unknown")}
    if result["kind"] == "record" and describe.get("fields"):
        result["fields"] = list(describe.get("fields"))
    return result


def _schema_kind(value: Any) -> str:
    return str(_schema_record(value).get("kind") or "unknown")


def _outcomes(kind: str) -> list[str]:
    if kind == "prohibited":
        return ["refused"]
    if kind == "cancel":
        return ["cancelled", "refused"]
    if kind == "async":
        return ["committed", "refused", "cancelled", "superseded", "failed"]
    return ["committed", "refused", "failed"]


def _invariant_id(raw: str) -> str:
    marker = ".invariant."
    if marker in raw:
        app, suffix = raw.split(marker, 1)
        return f"invariant:{app}.{suffix}"
    return f"invariant:{raw}"


def _layout_node_order(layout: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for region in layout.get("regions") or []:
        if not isinstance(region, Mapping):
            continue
        for value in region.get("orderedChildren") or region.get("children") or []:
            if isinstance(value, str):
                result.append(value)
            elif isinstance(value, Mapping) and value.get("id"):
                result.append(str(value.get("id")))
    return result


def _portable_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if value.get("$undefined") is True:
            return None
        return {str(key): _portable_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0])) if key != "$undefined"}
    if isinstance(value, list):
        return [_portable_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _is_undefined(value: Any) -> bool:
    return value is None or (isinstance(value, Mapping) and value.get("$undefined") is True)


def _source_files(repo: Path, package_root: Path, definition_path: Path, normalized_path: Path) -> tuple[Mapping[str, str], ...]:
    candidates = [definition_path, normalized_path]
    candidates.extend(sorted((package_root / "contracts").glob("*.js")))
    values = []
    for path in candidates:
        if path.is_file():
            values.append({"path": path.resolve().relative_to(repo).as_posix(), "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()})
    return tuple(values)


def _line_count(path: Path) -> int:
    try:
        return max(1, len(path.read_text(encoding="utf-8").splitlines()))
    except OSError:
        return 1


def _diagnostic(code: str, summary: str, semantic_path: str, *, observed: Any = None) -> dict[str, Any]:
    return {
        "schema": "mcel.compiler-diagnostic.v1",
        "code": code,
        "severity": "error",
        "blocking": True,
        "summary": summary,
        "problem": summary,
        "semanticPath": semantic_path,
        "observed": observed,
    }


def _failure(app_id: str, status: str, message: str) -> ApplicationDefinitionIrResult:
    return ApplicationDefinitionIrResult(False, status, app_id, None, (_diagnostic("MCEL_DEFINITION_IR_IMPORT_FAILED", message, "$source"),), ())
