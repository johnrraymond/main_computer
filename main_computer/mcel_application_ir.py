"""Structural kernel for ``mcel.application-ir.v1``.

Wave 1 deliberately stops at the stable semantic measuring instrument.  It
validates candidate IR, resolves stable semantic references, canonicalizes the
portable JSON representation, and computes separate semantic and source-binding
fingerprints.  It does not compile the MCEL DSL, project runtime contracts,
promote generated files, reuse evidence, or alter application authority.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from main_computer.mcel_constrained_expression import (
    WRITE_KINDS,
    analyze_application_expressions,
    application_domain_operator_registry,
)


APPLICATION_IR_SCHEMA = "mcel.application-ir.v1"
APPLICATION_IR_SCHEMA_ID = "https://maincomputer.local/schemas/mcel.application-ir.v1.schema.json"
APPLICATION_IR_NORMALIZATION_SCHEMA = "mcel.application-ir-normalization.v1"
APPLICATION_IR_NORMALIZER_VERSION = "mcel-application-ir-normalizer-v1"
COMPILER_DIAGNOSTIC_SCHEMA = "mcel.compiler-diagnostic.v1"
VALIDATION_REPORT_SCHEMA = "mcel.application-ir-validation-report.v1"
SEMANTIC_FINGERPRINT_ALGORITHM = "sha256-mcel-application-ir-semantics-v1"
SOURCE_BINDING_FINGERPRINT_ALGORITHM = "sha256-mcel-application-ir-source-binding-v1"

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "mcel.application-ir.v1.schema.json"


SUPPORTED_JSON_SCHEMA_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$defs",
        "$ref",
        "title",
        "type",
        "const",
        "enum",
        "pattern",
        "minLength",
        "minimum",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minItems",
        "allOf",
    }
)
SUPPORTED_JSON_SCHEMA_TYPES = frozenset(
    {"object", "array", "string", "integer", "number", "boolean", "null"}
)

TOP_LEVEL_ARRAY_KINDS: Mapping[str, str] = {
    "models": "model",
    "states": "state",
    "derivations": "derivation",
    "intents": "intent",
    "capabilities": "capability",
    "effects": "effect",
    "surfaces": "surface",
    "layouts": "layout",
    "scenarios": "scenario",
}

STATE_AUTHORITIES = frozenset({"canonical", "renderer-local", "provisional", "derived"})
EFFECT_KINDS = frozenset(
    {
        "canonical-write",
        "renderer-local-write",
        "provisional-write",
        "capability-request",
        "external-read",
        "external-mutation",
        "confirmation",
        "cancellation",
        "supersession",
        "late-event-rejection",
        "resource-acquire",
        "resource-release",
        "receipt-emission",
        "surface-publication",
        "recovery",
        "durable-retention",
    }
)

WRITE_EXPRESSION_KINDS = WRITE_KINDS

SEMANTIC_TOP_LEVEL_KEYS = (
    "schema",
    "application",
    "models",
    "states",
    "derivations",
    "intents",
    "capabilities",
    "effects",
    "surfaces",
    "layouts",
    "scenarios",
    "proof",
)

INCIDENTAL_SEMANTIC_KEYS = frozenset(
    {
        "source",
        "sourceName",
        "authoringStatus",
        "generatedAt",
        "absoluteRepositoryPath",
        "compilerProcessId",
        "temporaryRoutePort",
        "formattingComments",
        "normalization",
        "fingerprints",
        "provenance",
        "migration",
    }
)

SEMANTIC_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._:@/-]*$")
APP_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

PREFIX_KIND: Mapping[str, str] = {
    "app": "application",
    "model": "model",
    "field": "field",
    "schema": "schema",
    "state": "state",
    "derivation": "derivation",
    "intent": "intent",
    "input": "intent-input",
    "refusal": "refusal",
    "invariant": "invariant",
    "capability": "capability",
    "effect": "effect",
    "surface": "surface",
    "surface-node": "surface-node",
    "layout": "layout",
    "layout-node": "layout-node",
    "scenario": "scenario",
    "claim": "claim",
    "recovery": "recovery",
    "operator": "operator",
    "scope": "scope",
    "binding": "binding",
}

ORDER_INSENSITIVE_LIST_KEYS = frozenset(
    {
        "reads",
        "writes",
        "dependsOn",
        "effectRefs",
        "requiredEvidence",
        "allowedFinalDispositions",
        "relatedSemanticIds",
        "sourceFiles",
        "nodeBindings",
    }
)

NESTED_KIND_BY_PARENT_KEY: Mapping[str, str] = {
    "fields": "field",
    "input": "intent-input",
    "refusals": "refusal",
    "invariants": "invariant",
    "outcomes": "outcome",
    "nodes": "surface-node",
    "claims": "claim",
}


class ApplicationIRInvalid(ValueError):
    """Raised when a candidate cannot normalize into valid application IR."""

    def __init__(self, report: "ApplicationIRValidationReport") -> None:
        self.report = report
        first = report.diagnostics[0].summary if report.diagnostics else "Application IR is invalid."
        super().__init__(first)


@dataclass(frozen=True)
class CompilerDiagnostic:
    code: str
    rule_version: str
    severity: str
    blocking: bool
    stage: str
    repair_stage: str
    app_id: str
    semantic_path: str
    summary: str
    problem: str
    source: Mapping[str, Any]
    observed: Any = None
    expected: Any = None
    related_semantic_ids: tuple[str, ...] = ()
    safe_repairs: tuple[Mapping[str, Any], ...] = ()
    invalidations: tuple[Mapping[str, Any], ...] = ()
    rerun: tuple[Mapping[str, Any], ...] = ()
    migration_impact: Any = None

    @property
    def diagnostic_key(self) -> str:
        payload = {
            "ruleVersion": self.rule_version,
            "appId": self.app_id,
            "semanticPath": self.semantic_path,
            "violationClass": self.code,
            "relatedSemanticIds": sorted(self.related_semantic_ids),
        }
        return _fingerprint_payload("sha256-mcel-compiler-diagnostic-key-v1", payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": COMPILER_DIAGNOSTIC_SCHEMA,
            "code": self.code,
            "ruleVersion": self.rule_version,
            "severity": self.severity,
            "blocking": self.blocking,
            "stage": self.stage,
            "repairStage": self.repair_stage,
            "appId": self.app_id,
            "semanticPath": self.semantic_path,
            "summary": self.summary,
            "problem": self.problem,
            "source": _canonicalize_json_value(self.source),
            "observed": _canonicalize_json_value(self.observed),
            "expected": _canonicalize_json_value(self.expected),
            "relatedSemanticIds": sorted(self.related_semantic_ids),
            "safeRepairs": [_canonicalize_json_value(value) for value in self.safe_repairs],
            "invalidations": [_canonicalize_json_value(value) for value in self.invalidations],
            "rerun": [_canonicalize_json_value(value) for value in self.rerun],
            "migrationImpact": _canonicalize_json_value(self.migration_impact),
            "diagnosticKey": self.diagnostic_key,
        }


@dataclass(frozen=True)
class ApplicationIRValidationReport:
    valid: bool
    app_id: str
    diagnostics: tuple[CompilerDiagnostic, ...]
    normalized: Mapping[str, Any] | None = None

    @property
    def semantic_fingerprint(self) -> str | None:
        if not self.normalized:
            return None
        return str((self.normalized.get("fingerprints") or {}).get("semantic") or "") or None

    @property
    def source_binding_fingerprint(self) -> str | None:
        if not self.normalized:
            return None
        return str((self.normalized.get("fingerprints") or {}).get("sourceBinding") or "") or None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": VALIDATION_REPORT_SCHEMA,
            "valid": self.valid,
            "appId": self.app_id,
            "diagnosticCount": len(self.diagnostics),
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "semanticFingerprint": self.semantic_fingerprint,
            "sourceBindingFingerprint": self.source_binding_fingerprint,
            "normalized": _canonicalize_json_value(self.normalized) if self.normalized is not None else None,
        }


def load_application_ir_schema() -> Mapping[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def check_application_ir_schema(schema: Mapping[str, Any] | None = None) -> None:
    """Validate the repository-owned schema without an optional dependency.

    Wave 1 intentionally uses only a small, explicit Draft 2020-12 subset.  The
    checked-in JSON Schema remains the public structural contract, while this
    check prevents a schema edit from silently introducing a keyword the
    standard-library validator does not understand.
    """

    candidate = schema or load_application_ir_schema()
    if not isinstance(candidate, Mapping):
        raise ValueError("MCEL Application IR schema must be a JSON object.")
    if candidate.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ValueError("MCEL Application IR schema must declare Draft 2020-12.")
    if candidate.get("$id") != APPLICATION_IR_SCHEMA_ID:
        raise ValueError(f"MCEL Application IR schema $id must be {APPLICATION_IR_SCHEMA_ID!r}.")

    for schema_path, node in _iter_schema_nodes(candidate):
        unsupported = sorted(set(node) - SUPPORTED_JSON_SCHEMA_KEYWORDS)
        if unsupported:
            raise ValueError(
                f"Unsupported JSON Schema keyword(s) at {_json_path(schema_path)}: "
                + ", ".join(unsupported)
            )
        reference = node.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/"):
                raise ValueError(f"Only local JSON Schema references are supported: {reference!r}.")
            _resolve_schema_reference(candidate, reference)
        declared_type = node.get("type")
        type_values = [declared_type] if isinstance(declared_type, str) else declared_type
        if type_values is not None:
            if not isinstance(type_values, list) or not type_values:
                raise ValueError(f"Invalid JSON Schema type at {_json_path(schema_path)}.")
            unknown_types = sorted(set(type_values) - SUPPORTED_JSON_SCHEMA_TYPES)
            if unknown_types:
                raise ValueError(
                    f"Unsupported JSON Schema type(s) at {_json_path(schema_path)}: "
                    + ", ".join(unknown_types)
                )
        pattern = node.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str):
                raise ValueError(f"JSON Schema pattern must be a string at {_json_path(schema_path)}.")
            re.compile(pattern)


def canonical_json_bytes(value: Any) -> bytes:
    canonical = _canonicalize_json_value(value)
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def semantic_fingerprint(ir: Mapping[str, Any]) -> str:
    normalized = _normalized_without_fingerprints(ir)
    payload = {key: normalized[key] for key in SEMANTIC_TOP_LEVEL_KEYS if key in normalized}
    payload = _strip_incidental_semantic_metadata(payload)
    return _fingerprint_payload(SEMANTIC_FINGERPRINT_ALGORITHM, payload)


def source_binding_fingerprint(ir: Mapping[str, Any], semantic: str | None = None) -> str:
    normalized = _normalized_without_fingerprints(ir)
    provenance = normalized.get("provenance") if isinstance(normalized.get("provenance"), Mapping) else {}
    frontend = provenance.get("frontend") if isinstance(provenance.get("frontend"), Mapping) else {}
    payload = {
        "semanticFingerprint": semantic or semantic_fingerprint(normalized),
        "frontend": {
            "id": frontend.get("id"),
            "version": frontend.get("version"),
        },
        "sourceFiles": _normalize_source_files(frontend.get("sourceFiles") or ()),
        "nodeBindings": _normalize_node_bindings(provenance.get("nodeBindings") or ()),
    }
    return _fingerprint_payload(SOURCE_BINDING_FINGERPRINT_ALGORITHM, payload)


def validate_application_ir(document: Any) -> ApplicationIRValidationReport:
    diagnostics: list[CompilerDiagnostic] = []
    try:
        candidate = _prepare_candidate(document)
    except _NonDeterministicValue as exc:
        app_id = _best_effort_app_id(document)
        diagnostics.append(
            _diagnostic(
                code="MCEL_IR_NONDETERMINISTIC_VALUE",
                rule_version="mcel.application-ir.deterministic-json.v1",
                stage="compile",
                repair_stage="model",
                app_id=app_id,
                semantic_path=exc.path,
                summary="Application IR contains a nondeterministic or non-JSON value.",
                problem=exc.message,
                observed=exc.observed,
                expected={"kind": "finite-json-value"},
            )
        )
        return ApplicationIRValidationReport(False, app_id, tuple(diagnostics), None)

    app_id = _best_effort_app_id(candidate)
    schema = load_application_ir_schema()
    try:
        check_application_ir_schema(schema)
    except (ValueError, re.error) as exc:
        diagnostics.append(
            _diagnostic(
                code="MCEL_IR_SCHEMA_DEFINITION_INVALID",
                rule_version="mcel.application-ir.schema-definition.v1",
                stage="compile",
                repair_stage="compile",
                app_id=app_id,
                semantic_path="$schema",
                summary="The checked-in MCEL Application IR schema is unsupported or invalid.",
                problem=str(exc),
                expected={"schemaId": APPLICATION_IR_SCHEMA_ID, "draft": "2020-12"},
            )
        )
    else:
        for error in _iter_schema_validation_errors(candidate, schema):
            path = _json_path(error.absolute_path)
            diagnostics.append(
                _diagnostic(
                    code="MCEL_IR_SCHEMA_INVALID",
                    rule_version="mcel.application-ir.schema.v1",
                    stage="compile",
                    repair_stage=_repair_stage_from_path(path),
                    app_id=app_id,
                    semantic_path=path,
                    summary="Application IR does not match the v1 structural schema.",
                    problem=error.message,
                    source=_source_for_path(candidate, error.absolute_path),
                    observed=error.instance,
                    expected={"schemaPath": _json_path(error.absolute_schema_path)},
                )
            )

    diagnostics.extend(_semantic_diagnostics(candidate, app_id))
    diagnostics = _deduplicate_and_sort_diagnostics(diagnostics)
    if any(item.blocking for item in diagnostics):
        return ApplicationIRValidationReport(False, app_id, tuple(diagnostics), None)

    normalized = _finalize_normalized(candidate)
    return ApplicationIRValidationReport(True, app_id, tuple(diagnostics), normalized)


def normalize_application_ir(document: Any) -> Mapping[str, Any]:
    report = validate_application_ir(document)
    if not report.valid or report.normalized is None:
        raise ApplicationIRInvalid(report)
    return report.normalized


def compare_application_ir(left: Any, right: Any) -> Mapping[str, Any]:
    left_report = validate_application_ir(left)
    right_report = validate_application_ir(right)
    if not left_report.valid or not right_report.valid:
        return {
            "schema": "mcel.application-ir-comparison.v1",
            "status": "incomplete",
            "leftValid": left_report.valid,
            "rightValid": right_report.valid,
            "leftDiagnostics": [item.to_dict() for item in left_report.diagnostics],
            "rightDiagnostics": [item.to_dict() for item in right_report.diagnostics],
        }
    left_semantic = left_report.semantic_fingerprint
    right_semantic = right_report.semantic_fingerprint
    left_bytes = canonical_json_bytes(_semantic_payload(left_report.normalized or {}))
    right_bytes = canonical_json_bytes(_semantic_payload(right_report.normalized or {}))
    status = "exact" if left_semantic == right_semantic and left_bytes == right_bytes else "conflicting"
    return {
        "schema": "mcel.application-ir-comparison.v1",
        "status": status,
        "leftSemanticFingerprint": left_semantic,
        "rightSemanticFingerprint": right_semantic,
        "leftSourceBindingFingerprint": left_report.source_binding_fingerprint,
        "rightSourceBindingFingerprint": right_report.source_binding_fingerprint,
    }


@dataclass(frozen=True)
class _SchemaValidationError:
    message: str
    absolute_path: tuple[Any, ...]
    absolute_schema_path: tuple[Any, ...]
    instance: Any


def _iter_schema_nodes(
    value: Any,
    path: tuple[Any, ...] = (),
) -> Iterable[tuple[tuple[Any, ...], Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        yield path, value
        for key, child in value.items():
            if key in {"properties", "$defs"} and isinstance(child, Mapping):
                for child_key, child_schema in child.items():
                    yield from _iter_schema_nodes(child_schema, path + (key, child_key))
            elif key in {"items"} and isinstance(child, Mapping):
                yield from _iter_schema_nodes(child, path + (key,))
            elif key in {"allOf"} and isinstance(child, list):
                for index, child_schema in enumerate(child):
                    yield from _iter_schema_nodes(child_schema, path + (key, index))


def _resolve_schema_reference(root: Mapping[str, Any], reference: str) -> Mapping[str, Any]:
    current: Any = root
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or part not in current:
            raise ValueError(f"Unresolved local JSON Schema reference: {reference!r}.")
        current = current[part]
    if not isinstance(current, Mapping):
        raise ValueError(f"JSON Schema reference does not resolve to an object: {reference!r}.")
    return current


def _schema_reference_path(reference: str) -> tuple[Any, ...]:
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in reference[2:].split("/"))


def _iter_schema_validation_errors(
    instance: Any,
    schema: Mapping[str, Any],
) -> list[_SchemaValidationError]:
    errors = list(_validate_schema_value(instance, schema, schema, (), ()))
    return sorted(errors, key=lambda item: (list(map(str, item.absolute_path)), item.message))


def _validate_schema_value(
    instance: Any,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    path: tuple[Any, ...],
    schema_path: tuple[Any, ...],
) -> Iterable[_SchemaValidationError]:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        resolved = _resolve_schema_reference(root, reference)
        yield from _validate_schema_value(
            instance,
            resolved,
            root,
            path,
            _schema_reference_path(reference),
        )

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for index, child_schema in enumerate(all_of):
            if isinstance(child_schema, Mapping):
                yield from _validate_schema_value(
                    instance, child_schema, root, path, schema_path + ("allOf", index)
                )

    declared_type = schema.get("type")
    if declared_type is not None:
        accepted_types = [declared_type] if isinstance(declared_type, str) else list(declared_type)
        if not any(_json_schema_type_matches(instance, type_name) for type_name in accepted_types):
            yield _SchemaValidationError(
                message=f"{instance!r} is not of type {_format_schema_types(accepted_types)}",
                absolute_path=path,
                absolute_schema_path=schema_path + ("type",),
                instance=instance,
            )
            return

    if "const" in schema and instance != schema["const"]:
        yield _SchemaValidationError(
            message=f"{schema['const']!r} was expected",
            absolute_path=path,
            absolute_schema_path=schema_path + ("const",),
            instance=instance,
        )

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and instance not in enum_values:
        yield _SchemaValidationError(
            message=f"{instance!r} is not one of {enum_values!r}",
            absolute_path=path,
            absolute_schema_path=schema_path + ("enum",),
            instance=instance,
        )

    if isinstance(instance, str):
        minimum_length = schema.get("minLength")
        if isinstance(minimum_length, int) and len(instance) < minimum_length:
            yield _SchemaValidationError(
                message=f"{instance!r} is too short",
                absolute_path=path,
                absolute_schema_path=schema_path + ("minLength",),
                instance=instance,
            )
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, instance) is None:
            yield _SchemaValidationError(
                message=f"{instance!r} does not match {pattern!r}",
                absolute_path=path,
                absolute_schema_path=schema_path + ("pattern",),
                instance=instance,
            )

    minimum = schema.get("minimum")
    if minimum is not None and _is_json_number(instance) and instance < minimum:
        yield _SchemaValidationError(
            message=f"{instance!r} is less than the minimum of {minimum!r}",
            absolute_path=path,
            absolute_schema_path=schema_path + ("minimum",),
            instance=instance,
        )

    if isinstance(instance, Mapping):
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if key not in instance:
                    yield _SchemaValidationError(
                        message=f"{key!r} is a required property",
                        absolute_path=path,
                        absolute_schema_path=schema_path + ("required",),
                        instance=instance,
                    )
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            for key, child_schema in properties.items():
                if key in instance and isinstance(child_schema, Mapping):
                    yield from _validate_schema_value(
                        instance[key],
                        child_schema,
                        root,
                        path + (key,),
                        schema_path + ("properties", key),
                    )
            if schema.get("additionalProperties") is False:
                for key in sorted(set(instance) - set(properties), key=str):
                    yield _SchemaValidationError(
                        message=f"Additional property {key!r} is not allowed",
                        absolute_path=path,
                        absolute_schema_path=schema_path + ("additionalProperties",),
                        instance=instance,
                    )

    if isinstance(instance, (list, tuple)):
        minimum_items = schema.get("minItems")
        if isinstance(minimum_items, int) and len(instance) < minimum_items:
            yield _SchemaValidationError(
                message=f"{list(instance)!r} is too short",
                absolute_path=path,
                absolute_schema_path=schema_path + ("minItems",),
                instance=instance,
            )
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, child in enumerate(instance):
                yield from _validate_schema_value(
                    child,
                    item_schema,
                    root,
                    path + (index,),
                    schema_path + ("items",),
                )


def _json_schema_type_matches(value: Any, type_name: str) -> bool:
    if type_name == "object":
        return isinstance(value, Mapping)
    if type_name == "array":
        return isinstance(value, (list, tuple))
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return _is_json_number(value)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "null":
        return value is None
    return False


def _is_json_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _format_schema_types(values: Sequence[str]) -> str:
    return ", ".join(repr(value) for value in values)


def _prepare_candidate(document: Any) -> dict[str, Any]:
    _assert_deterministic_json(document, "$", set())
    if not isinstance(document, Mapping):
        return {"schema": None, "application": {}, "_invalidRoot": copy.deepcopy(document)}
    candidate = copy.deepcopy(dict(document))
    candidate.setdefault("schema", APPLICATION_IR_SCHEMA)
    for key in TOP_LEVEL_ARRAY_KINDS:
        candidate.setdefault(key, [])
    candidate.setdefault("proof", {})
    candidate.setdefault("migration", {})
    candidate.setdefault("provenance", {})
    candidate.setdefault("normalization", {})
    candidate.setdefault("fingerprints", {})
    _infer_nested_kinds(candidate)
    _expand_mechanical_defaults(candidate)
    _normalize_source_bindings_in_place(candidate)
    _sort_semantic_collections_in_place(candidate)
    return candidate


def _finalize_normalized(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    normalized = copy.deepcopy(dict(candidate))
    normalized["normalization"] = {
        "schema": APPLICATION_IR_NORMALIZATION_SCHEMA,
        "normalizer": APPLICATION_IR_NORMALIZER_VERSION,
        "canonicalJson": "utf8-json-sorted-keys-no-insignificant-whitespace",
        "objectKeyOrder": "lexicographic",
        "unorderedSemanticCollections": "stable-id",
        "semanticSequences": "preserved",
        "undefinedValues": "rejected",
    }
    normalized["fingerprints"] = {}
    semantic = semantic_fingerprint(normalized)
    source_binding = source_binding_fingerprint(normalized, semantic)
    normalized["fingerprints"] = {
        "semanticAlgorithm": SEMANTIC_FINGERPRINT_ALGORITHM,
        "semantic": semantic,
        "sourceBindingAlgorithm": SOURCE_BINDING_FINGERPRINT_ALGORITHM,
        "sourceBinding": source_binding,
    }
    return _canonicalize_json_value(normalized)


def _semantic_diagnostics(candidate: Mapping[str, Any], app_id: str) -> list[CompilerDiagnostic]:
    diagnostics: list[CompilerDiagnostic] = []
    nodes: dict[str, Mapping[str, Any]] = {}
    for path, node in _iter_semantic_nodes(candidate):
        node_id = node.get("id")
        kind = node.get("kind")
        if not isinstance(node_id, str):
            continue
        if not SEMANTIC_ID_PATTERN.fullmatch(node_id):
            diagnostics.append(
                _diagnostic(
                    code="MCEL_IR_SEMANTIC_ID_INVALID",
                    rule_version="mcel.application-ir.semantic-id.v1",
                    stage="compile",
                    repair_stage=_repair_stage_from_path(path),
                    app_id=app_id,
                    semantic_path=node_id or path,
                    summary="Semantic ID is not in the stable v1 form.",
                    problem=f"{node_id!r} is not a valid stable semantic ID.",
                    source=_node_source(node),
                    observed=node_id,
                    expected={"pattern": SEMANTIC_ID_PATTERN.pattern},
                )
            )
        if node_id in nodes:
            diagnostics.append(
                _diagnostic(
                    code="MCEL_IR_DUPLICATE_SEMANTIC_ID",
                    rule_version="mcel.application-ir.unique-id.v1",
                    stage="compile",
                    repair_stage=_repair_stage_from_path(path),
                    app_id=app_id,
                    semantic_path=node_id,
                    summary="Semantic IDs must be unique across the application IR.",
                    problem=f"Semantic ID {node_id!r} is declared more than once.",
                    source=_node_source(node),
                    observed={"id": node_id, "kind": kind},
                    expected={"unique": True},
                    related_semantic_ids=(node_id,),
                )
            )
        else:
            nodes[node_id] = node

    application = candidate.get("application") if isinstance(candidate.get("application"), Mapping) else {}
    declared_app_id = application.get("appId")
    if isinstance(declared_app_id, str) and not APP_ID_PATTERN.fullmatch(declared_app_id):
        diagnostics.append(
            _diagnostic(
                code="MCEL_IR_APPLICATION_ID_INVALID",
                rule_version="mcel.application-ir.application-id.v1",
                stage="compile",
                repair_stage="requirements",
                app_id=app_id,
                semantic_path="application.appId",
                summary="Application ID is not a stable repository-safe MCEL identifier.",
                problem=f"Application ID {declared_app_id!r} is invalid.",
                source=_node_source(application),
                observed=declared_app_id,
                expected={"pattern": APP_ID_PATTERN.pattern},
            )
        )

    for state in candidate.get("states", []):
        if not isinstance(state, Mapping):
            continue
        authority = state.get("authority")
        if authority not in STATE_AUTHORITIES:
            diagnostics.append(
                _diagnostic(
                    code="MCEL_IR_STATE_AUTHORITY_REQUIRED",
                    rule_version="mcel.application-ir.state-authority.v1",
                    stage="compile",
                    repair_stage="model",
                    app_id=app_id,
                    semantic_path=str(state.get("id") or "states"),
                    summary="Every state declaration requires an explicit MCEL authority.",
                    problem=f"State authority {authority!r} is missing or unsupported.",
                    source=_node_source(state),
                    observed=authority,
                    expected={"allowed": sorted(STATE_AUTHORITIES)},
                    related_semantic_ids=_one_id(state.get("id")),
                )
            )

    for effect in candidate.get("effects", []):
        if not isinstance(effect, Mapping):
            continue
        effect_kind = effect.get("effectKind")
        if effect_kind not in EFFECT_KINDS:
            diagnostics.append(
                _diagnostic(
                    code="MCEL_IR_EFFECT_KIND_UNKNOWN",
                    rule_version="mcel.application-ir.effect-kind.v1",
                    stage="compile",
                    repair_stage="effect",
                    app_id=app_id,
                    semantic_path=str(effect.get("id") or "effects"),
                    summary="Effect kind is not registered in the v1 effect taxonomy.",
                    problem=f"Effect kind {effect_kind!r} is unknown.",
                    source=_node_source(effect),
                    observed=effect_kind,
                    expected={"allowed": sorted(EFFECT_KINDS)},
                    related_semantic_ids=_one_id(effect.get("id")),
                )
            )
        dispositions = effect.get("allowedFinalDispositions")
        if not isinstance(dispositions, list) or not dispositions:
            diagnostics.append(
                _diagnostic(
                    code="MCEL_IR_EFFECT_TERMINAL_DISPOSITION_REQUIRED",
                    rule_version="mcel.application-ir.effect-disposition.v1",
                    stage="compile",
                    repair_stage="effect",
                    app_id=app_id,
                    semantic_path=str(effect.get("id") or "effects"),
                    summary="Consequential effects require at least one legal terminal disposition.",
                    problem="allowedFinalDispositions is empty or missing.",
                    source=_node_source(effect),
                    observed=dispositions,
                    expected={"minimumItems": 1},
                    related_semantic_ids=_one_id(effect.get("id")),
                )
            )

    diagnostics.extend(_reference_diagnostics(candidate, app_id, nodes))
    diagnostics.extend(_expression_diagnostics(candidate, app_id))
    diagnostics.extend(_write_set_diagnostics(candidate, app_id, nodes))
    return diagnostics


def _reference_diagnostics(
    candidate: Mapping[str, Any],
    app_id: str,
    nodes: Mapping[str, Mapping[str, Any]],
) -> list[CompilerDiagnostic]:
    diagnostics: list[CompilerDiagnostic] = []
    operator_registry = application_domain_operator_registry(candidate)
    for path, ref, container in _iter_references(candidate):
        if ref.startswith("context:"):
            # Context values are compiler-defined expression inputs, not
            # application semantic nodes.
            continue
        if ref.startswith("operator:") and "@" in ref:
            operator_id, version = ref.rsplit("@", 1)
            if operator_registry.resolve(operator_id, version) is not None:
                continue
        target = nodes.get(ref)
        source = _nearest_source(container)
        if target is None:
            diagnostics.append(
                _diagnostic(
                    code="MCEL_IR_REFERENCE_UNRESOLVED",
                    rule_version="mcel.application-ir.reference-resolution.v1",
                    stage="compile",
                    repair_stage=_repair_stage_from_path(path),
                    app_id=app_id,
                    semantic_path=path,
                    summary="Semantic reference does not resolve to a declared IR node.",
                    problem=f"Reference {ref!r} is unresolved.",
                    source=source,
                    observed={"ref": ref},
                    expected={"declaredSemanticId": True},
                    related_semantic_ids=(ref,),
                )
            )
            continue
        prefix = ref.split(":", 1)[0]
        expected_kind = _expected_reference_kind(path, prefix)
        observed_kind = str(target.get("kind") or "")
        if expected_kind and observed_kind != expected_kind:
            diagnostics.append(
                _diagnostic(
                    code="MCEL_IR_REFERENCE_KIND_MISMATCH",
                    rule_version="mcel.application-ir.reference-kind.v1",
                    stage="compile",
                    repair_stage=_repair_stage_from_path(path),
                    app_id=app_id,
                    semantic_path=path,
                    summary="Semantic reference resolves to the wrong node kind.",
                    problem=f"Reference {ref!r} names kind {observed_kind!r}, expected {expected_kind!r}.",
                    source=source,
                    observed={"ref": ref, "kind": observed_kind},
                    expected={"kind": expected_kind},
                    related_semantic_ids=(ref,),
                )
            )
    return diagnostics


def _expected_reference_kind(path: str, prefix: str) -> str | None:
    if ".reads[" in path or ".writes[" in path or ".dependsOn[" in path:
        return "state"
    if ".effectRefs[" in path:
        return "effect"
    if ".input[" in path and path.endswith(".schema"):
        return "schema"
    return PREFIX_KIND.get(prefix)


def _expression_diagnostics(candidate: Mapping[str, Any], app_id: str) -> list[CompilerDiagnostic]:
    diagnostics: list[CompilerDiagnostic] = []
    report = analyze_application_expressions(candidate, emit_reference_diagnostics=False)
    code_aliases = {
        "MCEL_EXPR_KIND_UNKNOWN": "MCEL_IR_EXPRESSION_KIND_UNKNOWN",
    }
    for expression_path, analysis in report.analyses:
        source = _source_for_path(candidate, _parse_json_path(expression_path))
        for finding in analysis.diagnostics:
            diagnostics.append(
                _diagnostic(
                    code=code_aliases.get(finding.code, finding.code),
                    rule_version="mcel.constrained-expression.wave2a.v1",
                    stage="compile",
                    repair_stage=finding.repair_stage,
                    app_id=app_id,
                    semantic_path=finding.semantic_path,
                    summary=finding.summary,
                    problem=finding.problem,
                    source=source,
                    observed=finding.observed,
                    expected=finding.expected,
                    related_semantic_ids=finding.related_semantic_ids,
                    severity=finding.severity,
                    blocking=finding.blocking,
                )
            )
    return diagnostics


def _write_set_diagnostics(
    candidate: Mapping[str, Any],
    app_id: str,
    nodes: Mapping[str, Mapping[str, Any]],
) -> list[CompilerDiagnostic]:
    diagnostics: list[CompilerDiagnostic] = []
    for intent in candidate.get("intents", []):
        if not isinstance(intent, Mapping):
            continue
        intent_id = str(intent.get("id") or "intent")
        declared = {
            value.get("ref")
            for value in intent.get("writes", [])
            if isinstance(value, Mapping) and isinstance(value.get("ref"), str)
        }
        actual = _collect_write_targets(intent.get("transition"))
        if actual != declared:
            diagnostics.append(
                _diagnostic(
                    code="MCEL_IR_INTENT_WRITE_SET_MISMATCH",
                    rule_version="mcel.application-ir.intent-write-set.v1",
                    stage="compile",
                    repair_stage="intent",
                    app_id=app_id,
                    semantic_path=intent_id,
                    summary="Declared canonical writes do not match the constrained transition graph.",
                    problem="The transition must neither broaden nor omit the intent's declared write authority.",
                    source=_node_source(intent),
                    observed={"declared": sorted(value for value in declared if value), "actual": sorted(actual)},
                    expected={"equal": True},
                    related_semantic_ids=tuple(sorted({intent_id, *(value for value in declared if value), *actual})),
                )
            )
        if actual and intent.get("operationKind") not in {"mutation", "capability", "async", "reconciliation"}:
            diagnostics.append(
                _diagnostic(
                    code="MCEL_IR_CANONICAL_WRITE_OUTSIDE_MUTATION",
                    rule_version="mcel.application-ir.canonical-write-authority.v1",
                    stage="compile",
                    repair_stage="intent",
                    app_id=app_id,
                    semantic_path=intent_id,
                    summary="Canonical transition exists outside an authorized operation kind.",
                    problem=f"Operation kind {intent.get('operationKind')!r} cannot own canonical writes.",
                    source=_node_source(intent),
                    observed=intent.get("operationKind"),
                    expected={"allowed": ["mutation", "capability", "async", "reconciliation"]},
                    related_semantic_ids=tuple(sorted({intent_id, *actual})),
                )
            )
        for target_id in actual:
            target = nodes.get(target_id)
            if target and target.get("kind") == "state" and target.get("authority") != "canonical":
                diagnostics.append(
                    _diagnostic(
                        code="MCEL_IR_CANONICAL_WRITE_AUTHORITY_INVALID",
                        rule_version="mcel.application-ir.canonical-write-target.v1",
                        stage="compile",
                        repair_stage="model",
                        app_id=app_id,
                        semantic_path=intent_id,
                        summary="Canonical transition targets state without canonical authority.",
                        problem=f"State {target_id!r} has authority {target.get('authority')!r}.",
                        source=_node_source(target),
                        observed=target.get("authority"),
                        expected={"authority": "canonical"},
                        related_semantic_ids=(intent_id, target_id),
                    )
                )
    return diagnostics


def _collect_write_targets(expression: Any) -> set[str]:
    targets: set[str] = set()
    if isinstance(expression, Mapping):
        kind = expression.get("kind")
        if kind in WRITE_EXPRESSION_KINDS:
            target = expression.get("target")
            if isinstance(target, Mapping) and isinstance(target.get("ref"), str):
                targets.add(str(target["ref"]))
        for child in expression.values():
            targets.update(_collect_write_targets(child))
    elif isinstance(expression, list):
        for child in expression:
            targets.update(_collect_write_targets(child))
    return targets


def _infer_nested_kinds(candidate: MutableMapping[str, Any]) -> None:
    application = candidate.get("application")
    if isinstance(application, MutableMapping):
        application.setdefault("kind", "application")
    for key, expected_kind in TOP_LEVEL_ARRAY_KINDS.items():
        values = candidate.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, MutableMapping):
                value.setdefault("kind", expected_kind)

    for model in candidate.get("models", []):
        _set_list_item_kind(model, "fields", "field")
    for intent in candidate.get("intents", []):
        _set_list_item_kind(intent, "input", "intent-input")
        _set_list_item_kind(intent, "refusals", "refusal")
        _set_list_item_kind(intent, "invariants", "invariant")
        _set_list_item_kind(intent, "outcomes", "outcome")
    for surface in candidate.get("surfaces", []):
        _set_list_item_kind(surface, "nodes", "surface-node")
    for layout in candidate.get("layouts", []):
        _set_list_item_kind(layout, "nodes", "layout-node")
    for scenario in candidate.get("scenarios", []):
        _set_list_item_kind(scenario, "claims", "claim")
    proof = candidate.get("proof")
    if isinstance(proof, MutableMapping):
        _set_list_item_kind(proof, "claims", "claim")
        _set_list_item_kind(proof, "invariants", "invariant")


def _set_list_item_kind(container: Any, key: str, kind: str) -> None:
    if not isinstance(container, MutableMapping):
        return
    values = container.get(key)
    if not isinstance(values, list):
        return
    for value in values:
        if isinstance(value, MutableMapping) and "id" in value:
            value.setdefault("kind", kind)


def _expand_mechanical_defaults(candidate: MutableMapping[str, Any]) -> None:
    for intent in candidate.get("intents", []):
        if not isinstance(intent, MutableMapping):
            continue
        if intent.get("operationKind") == "mutation":
            intent.setdefault("cancellable", False)
        for key in ("input", "reads", "writes", "refusals", "invariants", "effectRefs", "outcomes"):
            intent.setdefault(key, [])
    for effect in candidate.get("effects", []):
        if not isinstance(effect, MutableMapping):
            continue
        effect.setdefault("requiredEvidence", [])
        effect.setdefault("cleanupObligations", [])
        effect.setdefault("cardinality", {"minimum": 0, "maximum": 1})
    provenance = candidate.get("provenance")
    if isinstance(provenance, MutableMapping):
        provenance.setdefault("nodeBindings", [])
        frontend = provenance.get("frontend")
        if isinstance(frontend, MutableMapping):
            frontend.setdefault("sourceFiles", [])


def _sort_semantic_collections_in_place(candidate: MutableMapping[str, Any]) -> None:
    for key in TOP_LEVEL_ARRAY_KINDS:
        values = candidate.get(key)
        if isinstance(values, list):
            values.sort(key=_semantic_list_sort_key)

    for model in candidate.get("models", []):
        _sort_container_list(model, "fields")
    for intent in candidate.get("intents", []):
        for key in ("input", "reads", "writes", "refusals", "invariants", "effectRefs", "outcomes"):
            _sort_container_list(intent, key)
    for surface in candidate.get("surfaces", []):
        _sort_container_list(surface, "nodes")
    for scenario in candidate.get("scenarios", []):
        _sort_container_list(scenario, "claims")
    proof = candidate.get("proof")
    _sort_container_list(proof, "claims")
    _sort_container_list(proof, "invariants")
    provenance = candidate.get("provenance")
    _sort_container_list(provenance, "nodeBindings")
    if isinstance(provenance, Mapping):
        frontend = provenance.get("frontend")
        _sort_container_list(frontend, "sourceFiles")


def _sort_container_list(container: Any, key: str) -> None:
    if not isinstance(container, MutableMapping):
        return
    values = container.get(key)
    if isinstance(values, list):
        values.sort(key=_semantic_list_sort_key)


def _semantic_list_sort_key(value: Any) -> tuple[str, bytes]:
    if isinstance(value, Mapping):
        identifier = value.get("id") or value.get("ref") or value.get("path") or ""
        return str(identifier), canonical_json_bytes(value)
    return "", canonical_json_bytes(value)


def _normalize_source_bindings_in_place(value: Any) -> None:
    if isinstance(value, MutableMapping):
        source = value.get("source")
        if isinstance(source, MutableMapping) and isinstance(source.get("file"), str):
            source["file"] = _normalize_relative_path(source["file"])
        frontend = value.get("frontend")
        if isinstance(frontend, MutableMapping):
            source_files = frontend.get("sourceFiles")
            if isinstance(source_files, list):
                for item in source_files:
                    if isinstance(item, MutableMapping) and isinstance(item.get("path"), str):
                        item["path"] = _normalize_relative_path(item["path"])
        for child in value.values():
            _normalize_source_bindings_in_place(child)
    elif isinstance(value, list):
        for child in value:
            _normalize_source_bindings_in_place(child)


def _normalize_relative_path(raw: str) -> str:
    normalized = raw.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        return normalized
    return path.as_posix()


def _normalized_without_fingerprints(ir: Mapping[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(ir))
    candidate["fingerprints"] = {}
    return _canonicalize_json_value(candidate)


def _semantic_payload(ir: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = {key: ir[key] for key in SEMANTIC_TOP_LEVEL_KEYS if key in ir}
    return _strip_incidental_semantic_metadata(payload)


def _strip_incidental_semantic_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        # A native domain operator may carry the exact legacy callback identity
        # it replaces.  Semantic fingerprint v1 intentionally preserves that
        # behavior identity across representation migration, while expression
        # validation and expression fingerprints prove the new constrained
        # structure independently.
        if value.get("kind") == "domain.call":
            compatibility = value.get("compatibility")
            legacy = compatibility.get("legacyOpaqueFunction") if isinstance(compatibility, Mapping) else None
            if isinstance(legacy, Mapping):
                return _strip_incidental_semantic_metadata(legacy)
        return {
            str(key): _strip_incidental_semantic_metadata(child)
            for key, child in value.items()
            if str(key) not in INCIDENTAL_SEMANTIC_KEYS
        }
    if isinstance(value, list):
        return [_strip_incidental_semantic_metadata(child) for child in value]
    return value


def _normalize_source_files(values: Any) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return result
    for value in values:
        if not isinstance(value, Mapping):
            continue
        result.append(
            {
                "path": _normalize_relative_path(str(value.get("path") or "")),
                "sha256": value.get("sha256"),
            }
        )
    return sorted(result, key=lambda item: (str(item.get("path") or ""), str(item.get("sha256") or "")))


def _normalize_node_bindings(values: Any) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return result
    for value in values:
        if not isinstance(value, Mapping):
            continue
        binding = copy.deepcopy(dict(value))
        source = binding.get("source")
        if isinstance(source, MutableMapping) and isinstance(source.get("file"), str):
            source["file"] = _normalize_relative_path(source["file"])
        result.append(_canonicalize_json_value(binding))
    return sorted(result, key=_semantic_list_sort_key)


def _canonicalize_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonicalize_json_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonicalize_json_value(child) for child in value]
    return value


def _fingerprint_payload(algorithm: str, payload: Any) -> str:
    digest = hashlib.sha256()
    marker = algorithm.encode("utf-8")
    content = canonical_json_bytes(payload)
    digest.update(len(marker).to_bytes(8, "big"))
    digest.update(marker)
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)
    return "sha256:" + digest.hexdigest()


class _NonDeterministicValue(Exception):
    def __init__(self, path: str, message: str, observed: Any) -> None:
        self.path = path
        self.message = message
        self.observed = observed
        super().__init__(message)


def _assert_deterministic_json(value: Any, path: str, seen: set[int]) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _NonDeterministicValue(path, "Non-finite numbers are invalid IR values.", repr(value))
        return
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            raise _NonDeterministicValue(path, "Cyclic mappings are invalid IR values.", "cycle")
        seen.add(identity)
        for key, child in value.items():
            if not isinstance(key, str):
                raise _NonDeterministicValue(path, "IR object keys must be strings.", repr(key))
            _assert_deterministic_json(child, f"{path}.{key}", seen)
        seen.remove(identity)
        return
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in seen:
            raise _NonDeterministicValue(path, "Cyclic sequences are invalid IR values.", "cycle")
        seen.add(identity)
        for index, child in enumerate(value):
            _assert_deterministic_json(child, f"{path}[{index}]", seen)
        seen.remove(identity)
        return
    raise _NonDeterministicValue(
        path,
        f"Values of type {type(value).__name__!r} are not valid deterministic IR JSON.",
        repr(value),
    )


def _iter_semantic_nodes(value: Any, path: str = "$") -> Iterable[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        if isinstance(value.get("id"), str) and isinstance(value.get("kind"), str):
            yield path, value
        for key, child in value.items():
            yield from _iter_semantic_nodes(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_semantic_nodes(child, f"{path}[{index}]")


def _iter_references(value: Any, path: str = "$") -> Iterable[tuple[str, str, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        if isinstance(value.get("ref"), str):
            yield path, str(value["ref"]), value
        for key, child in value.items():
            yield from _iter_references(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_references(child, f"{path}[{index}]")


def _diagnostic(
    *,
    code: str,
    rule_version: str,
    stage: str,
    repair_stage: str,
    app_id: str,
    semantic_path: str,
    summary: str,
    problem: str,
    source: Mapping[str, Any] | None = None,
    observed: Any = None,
    expected: Any = None,
    related_semantic_ids: tuple[str, ...] = (),
    severity: str = "error",
    blocking: bool = True,
) -> CompilerDiagnostic:
    return CompilerDiagnostic(
        code=code,
        rule_version=rule_version,
        severity=severity,
        blocking=blocking,
        stage=stage,
        repair_stage=repair_stage,
        app_id=app_id,
        semantic_path=semantic_path,
        summary=summary,
        problem=problem,
        source=source or {"kind": "ir-source", "file": None},
        observed=observed,
        expected=expected,
        related_semantic_ids=related_semantic_ids,
    )


def _deduplicate_and_sort_diagnostics(values: Iterable[CompilerDiagnostic]) -> list[CompilerDiagnostic]:
    by_key: dict[str, CompilerDiagnostic] = {}
    for value in values:
        by_key.setdefault(value.diagnostic_key, value)
    return sorted(by_key.values(), key=lambda item: (item.repair_stage, item.semantic_path, item.code, item.diagnostic_key))


def _node_source(node: Mapping[str, Any]) -> Mapping[str, Any]:
    source = node.get("source")
    if isinstance(source, Mapping):
        return _canonicalize_json_value(source)
    return {"kind": "ir-source", "file": None}


def _nearest_source(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return _node_source(value)


def _parse_json_path(path: str) -> tuple[Any, ...]:
    if path == "$":
        return ()
    if not path.startswith("$"):
        return ()
    parts: list[Any] = []
    index = 1
    while index < len(path):
        if path[index] == ".":
            index += 1
            start = index
            while index < len(path) and path[index] not in ".[":
                index += 1
            if start < index:
                parts.append(path[start:index])
            continue
        if path[index] == "[":
            end = path.find("]", index)
            if end < 0:
                return tuple(parts)
            token = path[index + 1 : end]
            try:
                parts.append(int(token))
            except ValueError:
                return tuple(parts)
            index = end + 1
            continue
        index += 1
    return tuple(parts)


def _source_for_path(candidate: Mapping[str, Any], absolute_path: Iterable[Any]) -> Mapping[str, Any]:
    current: Any = candidate
    last_source: Mapping[str, Any] | None = _node_source(candidate)
    for part in absolute_path:
        if isinstance(current, Mapping) and isinstance(current.get("source"), Mapping):
            last_source = current["source"]
        try:
            current = current[part]
        except (KeyError, IndexError, TypeError):
            break
    if isinstance(current, Mapping) and isinstance(current.get("source"), Mapping):
        last_source = current["source"]
    return _canonicalize_json_value(last_source or {"kind": "ir-source", "file": None})


def _best_effort_app_id(value: Any) -> str:
    if isinstance(value, Mapping):
        application = value.get("application")
        if isinstance(application, Mapping) and isinstance(application.get("appId"), str):
            return str(application["appId"])
    return "unknown-app"


def _repair_stage_from_path(path: str) -> str:
    if ".states" in path or ".models" in path or ".derivations" in path:
        return "model"
    if ".intents" in path:
        return "intent"
    if ".effects" in path or ".capabilities" in path:
        return "effect"
    if ".surfaces" in path:
        return "surface"
    if ".layouts" in path:
        return "layout"
    if ".scenarios" in path or ".proof" in path:
        return "scenario"
    if ".provenance" in path or ".migration" in path:
        return "compatibility"
    return "requirements"


def _json_path(parts: Iterable[Any]) -> str:
    result = "$"
    for part in parts:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += f".{part}"
    return result


def _one_id(value: Any) -> tuple[str, ...]:
    return (str(value),) if isinstance(value, str) and value else ()
