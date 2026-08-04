"""Typed, inspectable expression kernel for ``mcel.application-ir.v1``.

Wave 2A implements the semantic expression graph beneath the future official
vanilla-JavaScript DSL.  It constructs and analyzes portable JSON records.  It
never evaluates application behavior, performs capabilities, projects runtime
contracts, edits applications, or changes application authority.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, MutableMapping, Sequence


EXPRESSION_ANALYSIS_SCHEMA = "mcel.constrained-expression-analysis.v1"
EXPRESSION_REPORT_SCHEMA = "mcel.application-expression-report.v1"
DOMAIN_OPERATOR_REGISTRY_SCHEMA = "mcel.domain-operator-registry.v1"
DOMAIN_OPERATOR_SCHEMA = "mcel.domain-operator.v1"
EXPRESSION_NORMALIZER_VERSION = "mcel-constrained-expression-normalizer-v1"
EXPRESSION_FINGERPRINT_ALGORITHM = "sha256-mcel-constrained-expression-v1"
DOMAIN_OPERATOR_REGISTRY_FINGERPRINT_ALGORITHM = "sha256-mcel-domain-operator-registry-v1"

SEMANTIC_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._:@/-]*$")
OPERATOR_VERSION_PATTERN = re.compile(r"^v?[1-9][0-9]*(?:\.[0-9]+){0,2}$")

EXPRESSION_CONTEXTS = frozenset(
    {
        "schema-default",
        "input-normalization",
        "validation",
        "invariant",
        "derivation",
        "mutation-transition",
        "postcondition",
        "provisional-receive",
        "capability-request",
        "capability-reconciliation",
        "surface-binding",
        "scenario-setup",
        "scenario-claim",
        "effect-target",
    }
)

READ_KINDS: Mapping[str, tuple[str, str]] = {
    "state.read": ("state", "state"),
    "input.read": ("input", "intent-input"),
    "item.read": ("field", "field"),
    "context.read": ("context", "context"),
    "transition.before.read": ("state", "state"),
    "transition.after.read": ("state", "state"),
    "capability.event.read": ("field", "field"),
    "capability.result.read": ("field", "field"),
    "scenario.output.read": ("output", "scenario-output"),
    "binding.read": ("binding", "binding"),
}

WRITE_KINDS = frozenset(
    {
        "transition.assign",
        "list.append",
        "list.remove-by-key",
        "list.update-by-key",
        "map.put",
        "map.remove",
        "number.increment",
        "number.add-to-state",
    }
)
PROVISIONAL_WRITE_KINDS = frozenset({"provisional.put-by-key", "provisional.remove-by-key"})

BOOLEAN_KINDS = frozenset({"boolean.not", "boolean.and", "boolean.or", "boolean.all", "boolean.any"})
COMPARE_KINDS = frozenset(
    {
        "compare.equal",
        "compare.not-equal",
        "compare.less-than",
        "compare.less-than-or-equal",
        "compare.greater-than",
        "compare.greater-than-or-equal",
        "compare.in-set",
        "compare.is-null",
    }
)
NUMBER_KINDS = frozenset(
    {
        "number.add",
        "number.subtract",
        "number.multiply",
        "number.divide",
        "number.minimum",
        "number.maximum",
        "number.round",
        "number.absolute",
        "number.is-integer",
    }
)
TEXT_KINDS = frozenset(
    {
        "text.trim",
        "text.lowercase",
        "text.uppercase",
        "text.length",
        "text.is-empty",
        "text.contains",
        "text.starts-with",
        "text.ends-with",
        "text.compare",
        "text.concat",
        "text.normalize-search",
    }
)
QUERY_KINDS = frozenset(
    {
        "query.filter",
        "query.sort",
        "query.map",
        "query.find-by-key",
        "query.find-first",
        "query.any",
        "query.every",
        "query.count",
        "query.sum",
        "query.average",
        "query.group-by",
        "query.distinct-by",
        "query.take",
        "query.skip",
        "query.order-ascending",
        "query.order-descending",
        "query.dynamic-order",
    }
)
CLAIM_KINDS = frozenset(
    {
        "claim.exists",
        "claim.equal",
        "claim.surface-row-exists",
        "claim.receipt-disposition",
        "claim.receipt-field",
        "claim.effect-accounted",
    }
)
SURFACE_KINDS = frozenset({"surface.text", "surface.property", "surface.visibility", "surface.status"})

EXPRESSION_KINDS = frozenset(
    {
        "constant",
        *READ_KINDS,
        "item.current",
        "let",
        "record.construct",
        "record.get",
        "record.set",
        "list.construct",
        "map.construct",
        "conditional",
        *BOOLEAN_KINDS,
        *COMPARE_KINDS,
        *NUMBER_KINDS,
        *TEXT_KINDS,
        "optional.is-present",
        "optional.unwrap",
        "optional.default",
        *QUERY_KINDS,
        "refusal.when",
        "invariant.assert",
        "collection.keys-unique",
        "schema.valid",
        "transition.assign",
        "transition.sequence",
        "list.append",
        "list.remove-by-key",
        "list.update-by-key",
        "map.put",
        "map.remove",
        "number.increment",
        "number.add-to-state",
        "provisional.get-by-key",
        "provisional.put-by-key",
        "provisional.remove-by-key",
        "provisional.current",
        "event.switch",
        "event.ignore",
        *SURFACE_KINDS,
        *CLAIM_KINDS,
        "id.next",
        "domain.call",
        "legacy.opaque-function",
    }
)

ALL_CONTEXTS = EXPRESSION_CONTEXTS
WRITE_CONTEXTS = frozenset({"mutation-transition", "capability-reconciliation"})
PROVISIONAL_CONTEXTS = frozenset({"provisional-receive", "capability-reconciliation"})
CLAIM_CONTEXTS = frozenset({"scenario-claim"})
SURFACE_CONTEXTS = frozenset({"surface-binding"})

SOURCE_CONTEXTS: Mapping[str, frozenset[str]] = {
    "state.read": frozenset(
        {
            "validation",
            "invariant",
            "derivation",
            "mutation-transition",
            "postcondition",
            "capability-request",
            "capability-reconciliation",
            "surface-binding",
            "scenario-setup",
            "scenario-claim",
            "effect-target",
        }
    ),
    "input.read": frozenset(
        {
            "input-normalization",
            "validation",
            "mutation-transition",
            "postcondition",
            "provisional-receive",
            "capability-request",
            "capability-reconciliation",
            "scenario-setup",
        }
    ),
    "item.read": frozenset(
        {
            "validation",
            "derivation",
            "mutation-transition",
            "provisional-receive",
            "capability-request",
            "capability-reconciliation",
            "surface-binding",
            "scenario-claim",
        }
    ),
    "item.current": frozenset(
        {"derivation", "mutation-transition", "provisional-receive", "capability-reconciliation", "surface-binding"}
    ),
    "context.read": frozenset(
        {
            "input-normalization",
            "validation",
            "mutation-transition",
            "postcondition",
            "provisional-receive",
            "capability-request",
            "capability-reconciliation",
            "surface-binding",
            "scenario-setup",
            "scenario-claim",
        }
    ),
    "transition.before.read": frozenset({"postcondition", "capability-reconciliation"}),
    "transition.after.read": frozenset({"postcondition", "capability-reconciliation"}),
    "capability.event.read": frozenset({"provisional-receive", "capability-reconciliation"}),
    "capability.result.read": frozenset({"capability-reconciliation", "scenario-claim"}),
    "scenario.output.read": frozenset({"scenario-setup", "scenario-claim"}),
    "binding.read": ALL_CONTEXTS,
}


@dataclass(frozen=True)
class ExpressionSymbol:
    semantic_id: str
    kind: str
    value_type: Mapping[str, Any]
    authority: str | None = None


@dataclass(frozen=True)
class ExpressionFinding:
    code: str
    semantic_path: str
    summary: str
    problem: str
    observed: Any = None
    expected: Any = None
    related_semantic_ids: tuple[str, ...] = ()
    severity: str = "error"
    blocking: bool = True
    repair_stage: str = "intent"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "semanticPath": self.semantic_path,
            "summary": self.summary,
            "problem": self.problem,
            "observed": _canonicalize(self.observed),
            "expected": _canonicalize(self.expected),
            "relatedSemanticIds": sorted(self.related_semantic_ids),
            "severity": self.severity,
            "blocking": self.blocking,
            "repairStage": self.repair_stage,
        }


@dataclass(frozen=True)
class ExpressionAnalysis:
    valid: bool
    context: str
    result_type: Mapping[str, Any]
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    referenced_semantic_ids: tuple[str, ...]
    purity: str
    determinism: str
    totality: str
    diagnostics: tuple[ExpressionFinding, ...]
    normalized: Mapping[str, Any] | None
    fingerprint: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": EXPRESSION_ANALYSIS_SCHEMA,
            "valid": self.valid,
            "context": self.context,
            "resultType": _canonicalize(self.result_type),
            "reads": list(self.reads),
            "writes": list(self.writes),
            "referencedSemanticIds": list(self.referenced_semantic_ids),
            "purity": self.purity,
            "determinism": self.determinism,
            "totality": self.totality,
            "diagnosticCount": len(self.diagnostics),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "fingerprint": self.fingerprint,
            "normalized": _canonicalize(self.normalized) if self.normalized is not None else None,
        }


@dataclass(frozen=True)
class ApplicationExpressionReport:
    valid: bool
    app_id: str
    expression_count: int
    analyses: tuple[tuple[str, ExpressionAnalysis], ...]

    @property
    def diagnostics(self) -> tuple[tuple[str, ExpressionFinding], ...]:
        return tuple(
            (path, diagnostic)
            for path, analysis in self.analyses
            for diagnostic in analysis.diagnostics
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": EXPRESSION_REPORT_SCHEMA,
            "valid": self.valid,
            "appId": self.app_id,
            "expressionCount": self.expression_count,
            "diagnosticCount": len(self.diagnostics),
            "expressions": [
                {"semanticPath": path, "analysis": analysis.to_dict()}
                for path, analysis in self.analyses
            ],
        }


@dataclass(frozen=True)
class DomainOperatorDefinition:
    semantic_id: str
    version: str
    input_types: Mapping[str, Mapping[str, Any]] | tuple[Mapping[str, Any], ...]
    result_type: Mapping[str, Any]
    allowed_contexts: frozenset[str]
    totality: str = "total"
    description: str = ""

    def __post_init__(self) -> None:
        if not SEMANTIC_ID_PATTERN.fullmatch(self.semantic_id) or not self.semantic_id.startswith("operator:"):
            raise ValueError(f"Domain operator ID must be a stable operator:* semantic ID: {self.semantic_id!r}")
        if not OPERATOR_VERSION_PATTERN.fullmatch(self.version):
            raise ValueError(f"Domain operator version must be numeric and explicit: {self.version!r}")
        unknown = set(self.allowed_contexts) - EXPRESSION_CONTEXTS
        if unknown:
            raise ValueError(f"Unknown expression context(s): {sorted(unknown)}")
        if self.totality not in {"total", "partial-with-refusal"}:
            raise ValueError("Domain operators must be total or partial-with-refusal.")
        _assert_json(self.to_record())

    @property
    def versioned_key(self) -> str:
        return f"{self.semantic_id}@{self.version}"

    @property
    def parameters(self) -> tuple[tuple[str, Mapping[str, Any]], ...]:
        if isinstance(self.input_types, Mapping):
            return tuple((str(name), _normalize_type(value)) for name, value in sorted(self.input_types.items()))
        return tuple((f"arg{index}", _normalize_type(value)) for index, value in enumerate(self.input_types))

    def to_record(self) -> dict[str, Any]:
        return {
            "schema": DOMAIN_OPERATOR_SCHEMA,
            "id": self.semantic_id,
            "version": self.version,
            "parameters": [
                {"name": name, "schema": _canonicalize(value_type)}
                for name, value_type in self.parameters
            ],
            "resultType": _canonicalize(self.result_type),
            "allowedContexts": sorted(self.allowed_contexts),
            "purity": "pure",
            "determinism": "deterministic",
            "totality": self.totality,
            "description": self.description,
        }


@dataclass
class DomainOperatorRegistry:
    _operators: MutableMapping[str, DomainOperatorDefinition] = field(default_factory=dict)

    def register(self, definition: DomainOperatorDefinition) -> None:
        key = definition.versioned_key
        existing = self._operators.get(key)
        if existing is not None and existing != definition:
            raise ValueError(f"Domain operator {key!r} is already registered with different semantics.")
        self._operators[key] = definition

    def resolve(self, semantic_id: str, version: str) -> DomainOperatorDefinition | None:
        return self._operators.get(f"{semantic_id}@{version}")

    def to_record(self) -> dict[str, Any]:
        operators = [self._operators[key].to_record() for key in sorted(self._operators)]
        payload = {
            "schema": DOMAIN_OPERATOR_REGISTRY_SCHEMA,
            "operators": operators,
        }
        return {
            **payload,
            "fingerprintAlgorithm": DOMAIN_OPERATOR_REGISTRY_FINGERPRINT_ALGORITHM,
            "fingerprint": _fingerprint(DOMAIN_OPERATOR_REGISTRY_FINGERPRINT_ALGORITHM, payload),
        }

    @classmethod
    def from_records(cls, values: Iterable[Mapping[str, Any]]) -> "DomainOperatorRegistry":
        registry = cls()
        for value in values:
            registry.register(
                DomainOperatorDefinition(
                    semantic_id=str(value["id"]),
                    version=str(value["version"]),
                    input_types=(
                        {
                            str(item["name"]): _normalize_type(item.get("schema"))
                            for item in value.get("parameters", [])
                            if isinstance(item, Mapping) and isinstance(item.get("name"), str)
                        }
                        if isinstance(value.get("parameters"), list)
                        else tuple(_normalize_type(item) for item in value.get("inputTypes", []))
                    ),
                    result_type=_normalize_type(value.get("resultType")),
                    allowed_contexts=frozenset(str(item) for item in value.get("allowedContexts", [])),
                    totality=str(value.get("totality") or "total"),
                    description=str(value.get("description") or ""),
                )
            )
        return registry


# ---------------------------------------------------------------------------
# Public expression constructors.  They construct records only; they do not
# evaluate or mutate application state.
# ---------------------------------------------------------------------------


def constant(value: Any, value_type: Mapping[str, Any] | str | None = None) -> dict[str, Any]:
    _assert_json(value)
    return {"kind": "constant", "value": copy.deepcopy(value), "type": _normalize_type(value_type) if value_type else _infer_constant_type(value)}


def state_read(state_id: str) -> dict[str, Any]:
    return {"kind": "state.read", "state": _ref(state_id, "state")}


def input_read(input_id: str) -> dict[str, Any]:
    return {"kind": "input.read", "input": _ref(input_id, "input")}


def item_read(field_id: str) -> dict[str, Any]:
    return {"kind": "item.read", "field": _ref(field_id, "field")}


def context_read(context_id: str) -> dict[str, Any]:
    return {"kind": "context.read", "context": _ref(context_id, "context")}


def number_add(*operands: Mapping[str, Any]) -> dict[str, Any]:
    if len(operands) < 2:
        raise ValueError("number_add requires at least two operands.")
    return {"kind": "number.add", "operands": [copy.deepcopy(dict(value)) for value in operands]}


def compare_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    return {"kind": "compare.equal", "left": copy.deepcopy(dict(left)), "right": copy.deepcopy(dict(right))}


def transition_assign(state_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
    return {"kind": "transition.assign", "target": _ref(state_id, "state"), "value": copy.deepcopy(dict(value))}


def number_increment(state_id: str, amount: int = 1) -> dict[str, Any]:
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise TypeError("number_increment amount must be an integer.")
    return {"kind": "number.increment", "target": _ref(state_id, "state"), "amount": amount}


def transition_sequence(*steps: Mapping[str, Any]) -> dict[str, Any]:
    if not steps:
        raise ValueError("transition_sequence requires at least one transition.")
    return {"kind": "transition.sequence", "steps": [copy.deepcopy(dict(value)) for value in steps]}


def domain_call(
    operator_id: str,
    version: str,
    *arguments: Mapping[str, Any],
    **named_arguments: Mapping[str, Any],
) -> dict[str, Any]:
    _ref(operator_id, "operator")
    if not OPERATOR_VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"Domain operator version must be numeric and explicit: {version!r}")
    if arguments and named_arguments:
        raise ValueError("domain_call accepts positional or named arguments, not both.")
    payload: Any
    if named_arguments:
        payload = {name: copy.deepcopy(dict(value)) for name, value in sorted(named_arguments.items())}
    else:
        payload = [copy.deepcopy(dict(value)) for value in arguments]
    return {
        "kind": "domain.call",
        "operator": {"ref": f"{operator_id}@{version}"},
        "arguments": payload,
    }


def normalize_expression(
    expression: Mapping[str, Any],
    *,
    context: str,
    symbols: Mapping[str, ExpressionSymbol] | None = None,
    operators: DomainOperatorRegistry | None = None,
    expected_type: Mapping[str, Any] | str | None = None,
) -> Mapping[str, Any]:
    analysis = analyze_expression(
        expression,
        context=context,
        symbols=symbols,
        operators=operators,
        expected_type=expected_type,
    )
    if not analysis.valid or analysis.normalized is None:
        first = analysis.diagnostics[0] if analysis.diagnostics else None
        raise ValueError(first.problem if first else "Expression is invalid.")
    return analysis.normalized


def analyze_expression(
    expression: Any,
    *,
    context: str,
    symbols: Mapping[str, ExpressionSymbol] | None = None,
    operators: DomainOperatorRegistry | None = None,
    expected_type: Mapping[str, Any] | str | None = None,
    semantic_path: str = "$",
    emit_reference_diagnostics: bool = True,
) -> ExpressionAnalysis:
    if context not in EXPRESSION_CONTEXTS:
        raise ValueError(f"Unknown expression context: {context!r}")
    try:
        _assert_json(expression)
    except (TypeError, ValueError) as exc:
        finding = ExpressionFinding(
            code="MCEL_EXPR_NONDETERMINISTIC_VALUE",
            semantic_path=semantic_path,
            summary="Expression contains a non-JSON or nondeterministic value.",
            problem=str(exc),
            observed=type(expression).__name__,
            expected={"kind": "finite-json-expression"},
            repair_stage=_repair_stage_for_context(context),
        )
        return ExpressionAnalysis(
            False,
            context,
            {"kind": "unknown"},
            (),
            (),
            (),
            "unknown",
            "unknown",
            "unknown",
            (finding,),
            None,
            None,
        )

    normalized_input = _canonicalize(expression)
    state = _AnalysisState(
        context=context,
        symbols=dict(symbols or {}),
        operators=operators or DomainOperatorRegistry(),
        emit_reference_diagnostics=emit_reference_diagnostics,
    )
    result_type = state.infer(normalized_input, semantic_path)
    if expected_type is not None:
        expected = _normalize_type(expected_type)
        if not _types_compatible(result_type, expected):
            state.find(
                "MCEL_EXPR_RESULT_TYPE_MISMATCH",
                semantic_path,
                "Expression result type does not match its owning semantic slot.",
                "The constrained graph returns a different type than the context requires.",
                observed=result_type,
                expected=expected,
            )

    diagnostics = tuple(_dedupe_findings(state.findings))
    valid = not any(item.blocking for item in diagnostics)
    normalized = None
    fingerprint = None
    if valid:
        normalized = copy.deepcopy(normalized_input)
        if isinstance(normalized, MutableMapping):
            normalized["type"] = _canonicalize(result_type)
            normalized = _canonicalize(normalized)
        fingerprint = _fingerprint(EXPRESSION_FINGERPRINT_ALGORITHM, normalized)
    return ExpressionAnalysis(
        valid=valid,
        context=context,
        result_type=_canonicalize(result_type),
        reads=tuple(sorted(state.reads)),
        writes=tuple(sorted(state.writes)),
        referenced_semantic_ids=tuple(sorted(state.references)),
        purity="opaque" if state.opaque else "pure",
        determinism="opaque" if state.opaque else "deterministic",
        totality="partial-with-refusal" if state.partial else "total",
        diagnostics=diagnostics,
        normalized=normalized,
        fingerprint=fingerprint,
    )


def build_expression_symbols(document: Mapping[str, Any]) -> dict[str, ExpressionSymbol]:
    symbols: dict[str, ExpressionSymbol] = {
        "schema:null": ExpressionSymbol("schema:null", "schema", {"kind": "null"}),
        "schema:boolean": ExpressionSymbol("schema:boolean", "schema", {"kind": "boolean"}),
        "schema:integer": ExpressionSymbol("schema:integer", "schema", {"kind": "integer"}),
        "schema:number": ExpressionSymbol("schema:number", "schema", {"kind": "number"}),
        "schema:string": ExpressionSymbol("schema:string", "schema", {"kind": "string"}),
    }

    def add(node: Any, default_kind: str | None = None) -> None:
        if not isinstance(node, Mapping):
            return
        semantic_id = node.get("id")
        if not isinstance(semantic_id, str):
            return
        kind = str(node.get("kind") or default_kind or "unknown")
        value_type = _type_for_semantic_node(node, kind)
        symbols[semantic_id] = ExpressionSymbol(
            semantic_id=semantic_id,
            kind=kind,
            value_type=value_type,
            authority=str(node.get("authority")) if node.get("authority") is not None else None,
        )

    add(document.get("application"), "application")
    for collection, kind in (
        ("models", "model"),
        ("states", "state"),
        ("derivations", "derivation"),
        ("intents", "intent"),
        ("capabilities", "capability"),
        ("effects", "effect"),
        ("surfaces", "surface"),
        ("layouts", "layout"),
        ("scenarios", "scenario"),
    ):
        for node in document.get(collection, []) if isinstance(document.get(collection), list) else []:
            add(node, kind)
            if collection == "models" and isinstance(node, Mapping):
                for field_node in node.get("fields", []) if isinstance(node.get("fields"), list) else []:
                    add(field_node, "field")
            if collection == "intents" and isinstance(node, Mapping):
                for input_node in node.get("input", []) if isinstance(node.get("input"), list) else []:
                    add(input_node, "intent-input")
            if collection == "surfaces" and isinstance(node, Mapping):
                for surface_node in node.get("nodes", []) if isinstance(node.get("nodes"), list) else []:
                    add(surface_node, "surface-node")
    proof = document.get("proof")
    if isinstance(proof, Mapping):
        for invariant in proof.get("invariants", []) if isinstance(proof.get("invariants"), list) else []:
            add(invariant, "invariant")
        for claim in proof.get("claims", []) if isinstance(proof.get("claims"), list) else []:
            add(claim, "claim")
    return symbols


def analyze_application_expressions(
    document: Mapping[str, Any],
    *,
    operators: DomainOperatorRegistry | None = None,
    emit_reference_diagnostics: bool = False,
) -> ApplicationExpressionReport:
    app = document.get("application") if isinstance(document.get("application"), Mapping) else {}
    app_id = str(app.get("appId") or "unknown")
    symbols = build_expression_symbols(document)
    analyses: list[tuple[str, ExpressionAnalysis]] = []
    for path, context, expression, expected_type in _iter_expression_roots(document):
        analysis = analyze_expression(
            expression,
            context=context,
            symbols=symbols,
            operators=operators,
            expected_type=expected_type,
            semantic_path=path,
            emit_reference_diagnostics=emit_reference_diagnostics,
        )
        analyses.append((path, analysis))
    analyses.sort(key=lambda item: item[0])
    return ApplicationExpressionReport(
        valid=all(analysis.valid for _, analysis in analyses),
        app_id=app_id,
        expression_count=len(analyses),
        analyses=tuple(analyses),
    )


@dataclass
class _AnalysisState:
    context: str
    symbols: Mapping[str, ExpressionSymbol]
    operators: DomainOperatorRegistry
    emit_reference_diagnostics: bool
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)
    references: set[str] = field(default_factory=set)
    findings: list[ExpressionFinding] = field(default_factory=list)
    opaque: bool = False
    partial: bool = False

    def find(
        self,
        code: str,
        path: str,
        summary: str,
        problem: str,
        *,
        observed: Any = None,
        expected: Any = None,
        related: Iterable[str] = (),
        severity: str = "error",
        blocking: bool = True,
    ) -> None:
        self.findings.append(
            ExpressionFinding(
                code=code,
                semantic_path=path,
                summary=summary,
                problem=problem,
                observed=observed,
                expected=expected,
                related_semantic_ids=tuple(sorted(set(related))),
                severity=severity,
                blocking=blocking,
                repair_stage=_repair_stage_for_context(self.context),
            )
        )

    def infer(self, expression: Any, path: str) -> Mapping[str, Any]:
        if not isinstance(expression, Mapping):
            self.find(
                "MCEL_EXPR_RECORD_REQUIRED",
                path,
                "Expression must be a JSON object.",
                "A constrained expression is identified by an object containing a registered kind.",
                observed=expression,
                expected={"kind": "expression-record"},
            )
            return {"kind": "unknown"}
        kind = expression.get("kind")
        if not isinstance(kind, str) or kind not in EXPRESSION_KINDS:
            self.find(
                "MCEL_EXPR_KIND_UNKNOWN",
                path,
                "Expression kind is not registered in the v1 vocabulary.",
                f"Expression kind {kind!r} cannot be analyzed.",
                observed=kind,
                expected={"allowed": sorted(EXPRESSION_KINDS)},
            )
            return {"kind": "unknown"}
        self._check_context(kind, path)

        if kind == "constant":
            inferred = _infer_constant_type(expression.get("value"))
            declared = expression.get("type")
            if declared is not None:
                declared_type = _normalize_type(declared)
                if not _types_compatible(inferred, declared_type):
                    self.find(
                        "MCEL_EXPR_CONSTANT_TYPE_MISMATCH",
                        path,
                        "Constant value does not satisfy its declared expression type.",
                        "The declared type and JSON value have incompatible primitive shapes.",
                        observed={"value": expression.get("value"), "inferredType": inferred},
                        expected=declared_type,
                    )
                return declared_type
            return inferred

        if kind in READ_KINDS:
            field_name, expected_kind = READ_KINDS[kind]
            return self._read_type(expression, field_name, expected_kind, path)
        if kind == "item.current":
            return _normalize_type(expression.get("type") or {"kind": "unknown"})
        if kind == "id.next":
            namespace = expression.get("namespace")
            if not isinstance(namespace, str) or not namespace:
                self.find(
                    "MCEL_EXPR_ID_NAMESPACE_REQUIRED",
                    path,
                    "Deterministic ID allocation requires an explicit namespace.",
                    "id.next cannot derive stable identity from a missing namespace.",
                    observed=namespace,
                    expected={"type": "nonempty-string"},
                )
            return {"kind": "string"}

        if kind == "let":
            bindings = expression.get("bindings")
            if not isinstance(bindings, list):
                self.find(
                    "MCEL_EXPR_LET_BINDINGS_REQUIRED",
                    path,
                    "let requires an ordered bindings array.",
                    "Bindings must be explicit constrained expressions.",
                    observed=bindings,
                    expected={"type": "array"},
                )
            else:
                for index, binding in enumerate(bindings):
                    if isinstance(binding, Mapping):
                        self.infer(binding.get("value"), f"{path}.bindings[{index}].value")
            return self.infer(expression.get("body"), f"{path}.body")

        if kind == "record.construct":
            model_ref = _extract_ref(expression.get("model"))
            if model_ref:
                self._resolve_ref(model_ref, "model", f"{path}.model")
            fields = expression.get("fields")
            if not isinstance(fields, Mapping):
                self.find(
                    "MCEL_EXPR_RECORD_FIELDS_REQUIRED",
                    path,
                    "Record construction requires a fields object.",
                    "The constructor must expose every supplied field expression.",
                    observed=fields,
                    expected={"type": "object"},
                )
            else:
                for name, value in fields.items():
                    self.infer(value, f"{path}.fields.{name}")
            return {"kind": "model", "ref": model_ref} if model_ref else {"kind": "record"}

        if kind == "record.get":
            self.infer(expression.get("record"), f"{path}.record")
            field_ref = _extract_ref(expression.get("field"))
            if field_ref:
                symbol = self._resolve_ref(field_ref, "field", f"{path}.field")
                return symbol.value_type if symbol else {"kind": "unknown"}
            return _normalize_type(expression.get("type") or {"kind": "unknown"})

        if kind == "record.set":
            record_type = self.infer(expression.get("record"), f"{path}.record")
            fields = expression.get("fields")
            if isinstance(fields, Mapping):
                for name, value in fields.items():
                    self.infer(value, f"{path}.fields.{name}")
            else:
                self.find(
                    "MCEL_EXPR_RECORD_FIELDS_REQUIRED",
                    path,
                    "Record update requires a fields object.",
                    "record.set is immutable but still requires explicit replacement fields.",
                    observed=fields,
                    expected={"type": "object"},
                )
            return record_type

        if kind == "list.construct":
            item_type = _normalize_type(expression.get("itemType") or {"kind": "unknown"})
            items = expression.get("items")
            if not isinstance(items, list):
                self.find(
                    "MCEL_EXPR_LIST_ITEMS_REQUIRED",
                    path,
                    "List construction requires an items array.",
                    "The list contents must be explicit constrained expressions.",
                    observed=items,
                    expected={"type": "array"},
                )
            else:
                for index, item in enumerate(items):
                    actual = self.infer(item, f"{path}.items[{index}]")
                    self._require_type(actual, item_type, f"{path}.items[{index}]")
            return {"kind": "list", "items": item_type}

        if kind == "map.construct":
            key_type = _normalize_type(expression.get("keyType") or {"kind": "unknown"})
            value_type = _normalize_type(expression.get("valueType") or {"kind": "unknown"})
            entries = expression.get("entries")
            if isinstance(entries, list):
                for index, entry in enumerate(entries):
                    if isinstance(entry, Mapping):
                        self._require_type(self.infer(entry.get("key"), f"{path}.entries[{index}].key"), key_type, f"{path}.entries[{index}].key")
                        self._require_type(self.infer(entry.get("value"), f"{path}.entries[{index}].value"), value_type, f"{path}.entries[{index}].value")
            else:
                self.find(
                    "MCEL_EXPR_MAP_ENTRIES_REQUIRED",
                    path,
                    "Map construction requires an entries array.",
                    "Each entry must expose a key and value expression.",
                    observed=entries,
                    expected={"type": "array"},
                )
            return {"kind": "map", "keys": key_type, "values": value_type}

        if kind == "conditional":
            self._require_type(self.infer(expression.get("when"), f"{path}.when"), {"kind": "boolean"}, f"{path}.when")
            then_type = self.infer(expression.get("then"), f"{path}.then")
            else_type = self.infer(expression.get("else"), f"{path}.else")
            if not _types_compatible(then_type, else_type) and not _types_compatible(else_type, then_type):
                self.find(
                    "MCEL_EXPR_BRANCH_TYPE_MISMATCH",
                    path,
                    "Conditional branches return incompatible types.",
                    "Both branches must normalize to compatible result schemas.",
                    observed={"then": then_type, "else": else_type},
                    expected={"compatible": True},
                )
                return {"kind": "unknown"}
            return _common_type(then_type, else_type)

        if kind in BOOLEAN_KINDS:
            operands = _operand_values(expression)
            for index, operand in enumerate(operands):
                self._require_type(self.infer(operand, f"{path}.operands[{index}]"), {"kind": "boolean"}, f"{path}.operands[{index}]")
            return {"kind": "boolean"}

        if kind in COMPARE_KINDS:
            if kind == "compare.is-null":
                self.infer(expression.get("value"), f"{path}.value")
            elif kind == "compare.in-set":
                value_type = self.infer(expression.get("value"), f"{path}.value")
                set_type = self.infer(expression.get("set"), f"{path}.set")
                if set_type.get("kind") == "list":
                    self._require_type(value_type, _normalize_type(set_type.get("items")), f"{path}.value")
            else:
                left_type = self.infer(expression.get("left"), f"{path}.left")
                right_type = self.infer(expression.get("right"), f"{path}.right")
                if not _types_comparable(left_type, right_type):
                    self.find(
                        "MCEL_EXPR_COMPARISON_TYPE_MISMATCH",
                        path,
                        "Comparison operands have incompatible types.",
                        "MCEL comparisons do not use JavaScript coercion.",
                        observed={"left": left_type, "right": right_type},
                        expected={"comparable": True},
                    )
            return {"kind": "boolean"}

        if kind in NUMBER_KINDS:
            values = _numeric_values(expression)
            value_types: list[Mapping[str, Any]] = []
            for index, value in enumerate(values):
                inferred = self.infer(value, f"{path}.values[{index}]")
                value_types.append(inferred)
                self._require_numeric(inferred, f"{path}.values[{index}]")
            if kind == "number.is-integer":
                return {"kind": "boolean"}
            if kind == "number.round":
                return {"kind": "integer"}
            if kind == "number.divide":
                self.partial = True
                return {"kind": "number"}
            return {"kind": "integer"} if value_types and all(_type_kind(value) == "integer" for value in value_types) else {"kind": "number"}

        if kind in TEXT_KINDS:
            values = _text_values(expression)
            for index, value in enumerate(values):
                self._require_type(self.infer(value, f"{path}.values[{index}]"), {"kind": "string"}, f"{path}.values[{index}]")
            if kind == "text.length":
                return {"kind": "integer"}
            if kind in {"text.is-empty", "text.contains", "text.starts-with", "text.ends-with"}:
                return {"kind": "boolean"}
            if kind == "text.compare":
                return {"kind": "integer"}
            return {"kind": "string"}

        if kind == "optional.is-present":
            self.infer(expression.get("value"), f"{path}.value")
            return {"kind": "boolean"}
        if kind == "optional.unwrap":
            source_type = self.infer(expression.get("value"), f"{path}.value")
            self.partial = True
            return _normalize_type(source_type.get("value") if source_type.get("kind") == "optional" else {"kind": "unknown"})
        if kind == "optional.default":
            source_type = self.infer(expression.get("value"), f"{path}.value")
            default_type = self.infer(expression.get("default"), f"{path}.default")
            if source_type.get("kind") == "optional":
                inner = _normalize_type(source_type.get("value"))
                self._require_type(default_type, inner, f"{path}.default")
                return inner
            return _common_type(source_type, default_type)

        if kind in QUERY_KINDS:
            return self._infer_query(kind, expression, path)

        if kind == "refusal.when":
            self._require_type(self.infer(expression.get("when"), f"{path}.when"), {"kind": "boolean"}, f"{path}.when")
            self.partial = True
            return {"kind": "refusal"}
        if kind == "invariant.assert":
            predicate = expression.get("predicate", expression.get("check"))
            self._require_type(self.infer(predicate, f"{path}.predicate"), {"kind": "boolean"}, f"{path}.predicate")
            return {"kind": "claim"}
        if kind in {"collection.keys-unique", "schema.valid"}:
            for key in ("source", "value"):
                if key in expression:
                    self.infer(expression.get(key), f"{path}.{key}")
            return {"kind": "boolean"}

        if kind == "transition.sequence":
            steps = expression.get("steps")
            if not isinstance(steps, list) or not steps:
                self.find(
                    "MCEL_EXPR_TRANSITION_STEPS_REQUIRED",
                    path,
                    "Transition sequence requires at least one step.",
                    "A transition sequence cannot hide an empty or missing operation list.",
                    observed=steps,
                    expected={"minimumItems": 1},
                )
            else:
                for index, step in enumerate(steps):
                    self._require_type(self.infer(step, f"{path}.steps[{index}]"), {"kind": "transition"}, f"{path}.steps[{index}]")
            return {"kind": "transition"}

        if kind in WRITE_KINDS:
            return self._infer_write(kind, expression, path)

        if kind == "provisional.current":
            return _normalize_type(expression.get("type") or {"kind": "unknown"})
        if kind == "provisional.get-by-key":
            self.infer(expression.get("key"), f"{path}.key")
            return _normalize_type(expression.get("type") or {"kind": "unknown"})
        if kind in PROVISIONAL_WRITE_KINDS:
            self.infer(expression.get("key"), f"{path}.key")
            if "value" in expression:
                self.infer(expression.get("value"), f"{path}.value")
            return {"kind": "provisional-transition"}

        if kind == "event.switch":
            branches = expression.get("branches")
            result_types: list[Mapping[str, Any]] = []
            if isinstance(branches, list):
                for index, branch in enumerate(branches):
                    if isinstance(branch, Mapping):
                        result_types.append(self.infer(branch.get("then"), f"{path}.branches[{index}].then"))
            if "default" in expression:
                result_types.append(self.infer(expression.get("default"), f"{path}.default"))
            return _unify_types(result_types)
        if kind == "event.ignore":
            return {"kind": "event-disposition"}

        if kind in SURFACE_KINDS:
            for key in ("value", "when", "status"):
                if key in expression:
                    inferred = self.infer(expression.get(key), f"{path}.{key}")
                    if kind == "surface.visibility" and key == "when":
                        self._require_type(inferred, {"kind": "boolean"}, f"{path}.{key}")
            return {"kind": "boolean"} if kind == "surface.visibility" else {"kind": "string"}

        if kind in CLAIM_KINDS:
            if kind == "claim.equal":
                left = expression.get("actual", expression.get("left"))
                right = expression.get("expected", expression.get("right"))
                left_type = self.infer(left, f"{path}.actual")
                right_type = self.infer(right, f"{path}.expected")
                if not _types_comparable(left_type, right_type):
                    self.find(
                        "MCEL_EXPR_CLAIM_TYPE_MISMATCH",
                        path,
                        "Claim compares incompatible evidence values.",
                        "Independent proof claims still require schema-compatible values.",
                        observed={"actual": left_type, "expected": right_type},
                        expected={"comparable": True},
                    )
            elif kind == "claim.exists":
                target_ref = _extract_ref(expression.get("target"))
                if target_ref:
                    self._resolve_ref(target_ref, None, f"{path}.target")
            return {"kind": "claim"}

        if kind == "domain.call":
            operator = expression.get("operator")
            operator_ref = _extract_ref(operator)
            operator_id, version = _split_versioned_operator_ref(operator_ref)
            if isinstance(operator, Mapping) and operator.get("version"):
                version = str(operator.get("version"))
            definition = self.operators.resolve(operator_id or "", version)
            if definition is None:
                self.find(
                    "MCEL_EXPR_DOMAIN_OPERATOR_UNREGISTERED",
                    path,
                    "Domain expression operator is not registered.",
                    "domain.call requires an exact versioned pure operator definition.",
                    observed={"id": operator_id, "version": version},
                    expected={"registered": True},
                    related=(operator_id,) if operator_id else (),
                )
                return {"kind": "unknown"}
            if self.context not in definition.allowed_contexts:
                self.find(
                    "MCEL_EXPR_DOMAIN_OPERATOR_CONTEXT_INVALID",
                    path,
                    "Domain operator is not allowed in this expression context.",
                    "The registered operator limits the semantic slots in which it may be used.",
                    observed=self.context,
                    expected={"allowed": sorted(definition.allowed_contexts)},
                    related=(definition.semantic_id,),
                )
            raw_arguments = expression.get("arguments")
            parameters = definition.parameters
            if isinstance(raw_arguments, Mapping):
                expected_names = {name for name, _ in parameters}
                observed_names = set(raw_arguments)
                if expected_names != observed_names:
                    self.find(
                        "MCEL_EXPR_DOMAIN_OPERATOR_ARGUMENTS_MISMATCH",
                        path,
                        "Domain operator argument names do not match the versioned registration.",
                        "Named domain arguments must be complete and contain no undeclared values.",
                        observed=sorted(observed_names),
                        expected=sorted(expected_names),
                        related=(definition.semantic_id,),
                    )
                arguments = [(name, raw_arguments[name]) for name, _ in parameters if name in raw_arguments]
            elif isinstance(raw_arguments, list):
                arguments = [
                    (parameters[index][0] if index < len(parameters) else f"arg{index}", value)
                    for index, value in enumerate(raw_arguments)
                ]
                if len(arguments) != len(parameters):
                    self.find(
                        "MCEL_EXPR_DOMAIN_OPERATOR_ARITY_MISMATCH",
                        path,
                        "Domain operator argument count is incorrect.",
                        "The versioned registration fixes the operator arity.",
                        observed=len(arguments),
                        expected=len(parameters),
                        related=(definition.semantic_id,),
                    )
            else:
                arguments = []
                self.find(
                    "MCEL_EXPR_DOMAIN_OPERATOR_ARGUMENTS_REQUIRED",
                    path,
                    "Domain operator requires explicit arguments.",
                    "Arguments must be a named object or ordered array.",
                    observed=raw_arguments,
                    expected={"type": ["object", "array"]},
                    related=(definition.semantic_id,),
                )
            parameter_types = {name: value_type for name, value_type in parameters}
            for index, (name, argument) in enumerate(arguments):
                argument_path = f"{path}.arguments.{name}" if isinstance(raw_arguments, Mapping) else f"{path}.arguments[{index}]"
                actual = self.infer(argument, argument_path)
                if name in parameter_types:
                    self._require_type(actual, parameter_types[name], argument_path)
            if definition.totality == "partial-with-refusal":
                self.partial = True
            self.references.add(definition.semantic_id)
            return definition.result_type

        if kind == "legacy.opaque-function":
            self.opaque = True
            self.find(
                "MCEL_EXPR_LEGACY_OPAQUE_MIGRATION_DEBT",
                path,
                "Opaque legacy callback cannot establish DSL-v1 semantic equivalence.",
                "The compatibility importer may retain the callback hash, but the feature remains migration debt.",
                observed=expression.get("functionHash"),
                expected={"replaceWith": "constrained-expression"},
                severity="warning",
                blocking=False,
            )
            return _normalize_type(expression.get("type") or {"kind": "unknown"})

        # Every registered v1 kind is handled above.  This is a fail-closed guard
        # for future edits that add a kind without defining its semantics.
        self.find(
            "MCEL_EXPR_KIND_UNIMPLEMENTED",
            path,
            "Expression kind is registered but has no Wave 2A analyzer.",
            "The registry and analyzer must advance together.",
            observed=kind,
            expected={"implemented": True},
        )
        return {"kind": "unknown"}

    def _check_context(self, kind: str, path: str) -> None:
        allowed = ALL_CONTEXTS
        if kind in SOURCE_CONTEXTS:
            allowed = SOURCE_CONTEXTS[kind]
        elif kind in WRITE_KINDS:
            allowed = WRITE_CONTEXTS
        elif kind in PROVISIONAL_WRITE_KINDS or kind.startswith("provisional.") or kind.startswith("event."):
            allowed = PROVISIONAL_CONTEXTS
        elif kind in CLAIM_KINDS:
            allowed = CLAIM_CONTEXTS
        elif kind in SURFACE_KINDS:
            allowed = SURFACE_CONTEXTS
        elif kind == "refusal.when":
            allowed = frozenset({"input-normalization", "validation", "mutation-transition", "capability-reconciliation"})
        elif kind == "invariant.assert":
            allowed = frozenset({"invariant", "postcondition", "scenario-claim"})
        if self.context not in allowed:
            self.find(
                "MCEL_EXPR_CONTEXT_INVALID",
                path,
                "Expression kind is illegal in its owning semantic context.",
                f"{kind!r} cannot be used in context {self.context!r}.",
                observed={"kind": kind, "context": self.context},
                expected={"allowedContexts": sorted(allowed)},
            )

    def _read_type(self, expression: Mapping[str, Any], field_name: str, expected_kind: str, path: str) -> Mapping[str, Any]:
        ref = _extract_ref(expression.get(field_name))
        if not ref:
            self.find(
                "MCEL_EXPR_REFERENCE_REQUIRED",
                path,
                "Value-source expression requires an explicit semantic reference.",
                f"{field_name!r} is missing or malformed.",
                observed=expression.get(field_name),
                expected={"refPrefix": f"{expected_kind}:"},
            )
            return {"kind": "unknown"}
        symbol = self._resolve_ref(ref, expected_kind, f"{path}.{field_name}")
        self.reads.add(ref)
        return symbol.value_type if symbol else {"kind": "unknown"}

    def _resolve_ref(self, ref: str, expected_kind: str | None, path: str) -> ExpressionSymbol | None:
        self.references.add(ref)
        symbol = self.symbols.get(ref)
        if symbol is None:
            if self.emit_reference_diagnostics:
                self.find(
                    "MCEL_EXPR_REFERENCE_UNRESOLVED",
                    path,
                    "Expression reference does not resolve.",
                    f"Reference {ref!r} is absent from the application symbol table.",
                    observed={"ref": ref},
                    expected={"declared": True},
                    related=(ref,),
                )
            return None
        if expected_kind and symbol.kind != expected_kind:
            if self.emit_reference_diagnostics:
                self.find(
                    "MCEL_EXPR_REFERENCE_KIND_MISMATCH",
                    path,
                    "Expression reference resolves to the wrong semantic kind.",
                    f"Reference {ref!r} resolves to {symbol.kind!r}, expected {expected_kind!r}.",
                    observed={"ref": ref, "kind": symbol.kind},
                    expected={"kind": expected_kind},
                    related=(ref,),
                )
            return None
        return symbol

    def _require_type(self, actual: Mapping[str, Any], expected: Mapping[str, Any], path: str) -> None:
        if not _types_compatible(actual, expected):
            self.find(
                "MCEL_EXPR_OPERAND_TYPE_MISMATCH",
                path,
                "Expression operand has an incompatible type.",
                "Typed MCEL expressions do not rely on JavaScript coercion.",
                observed=actual,
                expected=expected,
            )

    def _require_numeric(self, actual: Mapping[str, Any], path: str) -> None:
        if _type_kind(actual) not in {"integer", "number", "unknown"}:
            self.find(
                "MCEL_EXPR_NUMERIC_OPERAND_REQUIRED",
                path,
                "Numeric operator received a nonnumeric operand.",
                "Only integer or number schemas may enter numeric expressions.",
                observed=actual,
                expected={"allowed": ["integer", "number"]},
            )

    def _infer_write(self, kind: str, expression: Mapping[str, Any], path: str) -> Mapping[str, Any]:
        target_ref = _extract_ref(expression.get("target"))
        target_symbol = None
        if target_ref:
            target_symbol = self._resolve_ref(target_ref, "state", f"{path}.target")
            self.writes.add(target_ref)
        else:
            self.find(
                "MCEL_EXPR_WRITE_TARGET_REQUIRED",
                path,
                "State transition requires an explicit target.",
                "Write authority cannot be inferred from the value expression.",
                observed=expression.get("target"),
                expected={"refPrefix": "state:"},
            )
        if target_symbol and target_symbol.authority != "canonical":
            self.find(
                "MCEL_EXPR_WRITE_AUTHORITY_INVALID",
                path,
                "Canonical transition targets noncanonical state.",
                "Mutation transitions may write only state with canonical authority.",
                observed={"state": target_ref, "authority": target_symbol.authority},
                expected={"authority": "canonical"},
                related=(target_ref,) if target_ref else (),
            )
        if kind == "transition.assign":
            value_expression = expression.get("value")
            value_type = self.infer(value_expression, f"{path}.value")
            if target_symbol:
                self._require_type(value_type, target_symbol.value_type, f"{path}.value")
                if isinstance(value_expression, Mapping) and value_expression.get("kind") == "constant":
                    if not _constant_satisfies_schema(value_expression.get("value"), target_symbol.value_type):
                        self.find(
                            "MCEL_EXPR_CONSTANT_SCHEMA_VIOLATION",
                            f"{path}.value",
                            "Constant transition value violates the target state schema.",
                            "Static constant assignments must satisfy target constraints before runtime.",
                            observed=value_expression.get("value"),
                            expected=target_symbol.value_type,
                            related=(target_ref,) if target_ref else (),
                        )
        elif kind in {"number.increment", "number.add-to-state"}:
            if target_symbol:
                self._require_numeric(target_symbol.value_type, f"{path}.target")
            amount = expression.get("amount")
            if isinstance(amount, Mapping):
                self._require_numeric(self.infer(amount, f"{path}.amount"), f"{path}.amount")
            elif isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(float(amount)):
                self.find(
                    "MCEL_EXPR_INCREMENT_AMOUNT_INVALID",
                    path,
                    "Numeric state update requires a finite numeric amount.",
                    "The amount may be a finite JSON number or constrained numeric expression.",
                    observed=amount,
                    expected={"type": "finite-number"},
                )
        elif kind == "list.append":
            value_type = self.infer(expression.get("value"), f"{path}.value")
            if target_symbol and target_symbol.value_type.get("kind") == "list":
                self._require_type(value_type, _normalize_type(target_symbol.value_type.get("items")), f"{path}.value")
        elif kind in {"list.remove-by-key", "list.update-by-key"}:
            self.infer(expression.get("key"), f"{path}.key")
            key_ref = _extract_ref(expression.get("keyField"))
            if key_ref:
                self._resolve_ref(key_ref, "field", f"{path}.keyField")
            if kind == "list.update-by-key":
                self.infer(expression.get("update"), f"{path}.update")
        elif kind == "map.put":
            self.infer(expression.get("key"), f"{path}.key")
            self.infer(expression.get("value"), f"{path}.value")
        elif kind == "map.remove":
            self.infer(expression.get("key"), f"{path}.key")
        return {"kind": "transition"}

    def _infer_query(self, kind: str, expression: Mapping[str, Any], path: str) -> Mapping[str, Any]:
        if kind in {"query.order-ascending", "query.order-descending", "query.dynamic-order"}:
            for key in ("value", "selector"):
                if key in expression:
                    self.infer(expression.get(key), f"{path}.{key}")
            return {"kind": "order"}
        source_type = self.infer(expression.get("source"), f"{path}.source")
        item_type = _normalize_type(source_type.get("items") if source_type.get("kind") == "list" else {"kind": "unknown"})
        if kind == "query.filter":
            self._require_type(self.infer(expression.get("predicate"), f"{path}.predicate"), {"kind": "boolean"}, f"{path}.predicate")
            return source_type
        if kind == "query.sort":
            order = expression.get("order")
            if isinstance(order, list):
                for index, value in enumerate(order):
                    self.infer(value, f"{path}.order[{index}]")
            return source_type
        if kind == "query.map":
            mapped = self.infer(expression.get("value"), f"{path}.value")
            return {"kind": "list", "items": mapped}
        if kind in {"query.find-by-key", "query.find-first"}:
            for key in ("key", "predicate"):
                if key in expression:
                    self.infer(expression.get(key), f"{path}.{key}")
            return {"kind": "optional", "value": item_type}
        if kind in {"query.any", "query.every"}:
            self._require_type(self.infer(expression.get("predicate"), f"{path}.predicate"), {"kind": "boolean"}, f"{path}.predicate")
            return {"kind": "boolean"}
        if kind == "query.count":
            return {"kind": "integer"}
        if kind in {"query.sum", "query.average"}:
            value_type = self.infer(expression.get("value"), f"{path}.value")
            self._require_numeric(value_type, f"{path}.value")
            if "empty" in expression:
                self._require_numeric(self.infer(expression.get("empty"), f"{path}.empty"), f"{path}.empty")
            return {"kind": "number"} if kind == "query.average" else value_type
        if kind == "query.group-by":
            key_type = self.infer(expression.get("key"), f"{path}.key")
            return {"kind": "map", "keys": key_type, "values": {"kind": "list", "items": item_type}}
        if kind in {"query.distinct-by", "query.take", "query.skip"}:
            for key in ("key", "count"):
                if key in expression:
                    self.infer(expression.get(key), f"{path}.{key}")
            return source_type
        return {"kind": "unknown"}


# ---------------------------------------------------------------------------
# Application traversal and deterministic helpers.
# ---------------------------------------------------------------------------


def _iter_expression_roots(document: Mapping[str, Any]) -> Iterable[tuple[str, str, Any, Any]]:
    for index, derivation in enumerate(document.get("derivations", []) if isinstance(document.get("derivations"), list) else []):
        if isinstance(derivation, Mapping):
            for key in ("derive", "expression", "value"):
                if isinstance(derivation.get(key), Mapping):
                    yield f"$.derivations[{index}].{key}", "derivation", derivation[key], derivation.get("schema") or derivation.get("type")
                    break
    for index, intent in enumerate(document.get("intents", []) if isinstance(document.get("intents"), list) else []):
        if not isinstance(intent, Mapping):
            continue
        for input_index, input_node in enumerate(intent.get("input", []) if isinstance(intent.get("input"), list) else []):
            if isinstance(input_node, Mapping) and isinstance(input_node.get("normalize"), Mapping):
                yield f"$.intents[{index}].input[{input_index}].normalize", "input-normalization", input_node["normalize"], input_node.get("schema")
        for refusal_index, refusal in enumerate(intent.get("refusals", []) if isinstance(intent.get("refusals"), list) else []):
            if isinstance(refusal, Mapping):
                predicate = refusal.get("when", refusal.get("predicate"))
                if isinstance(predicate, Mapping):
                    yield f"$.intents[{index}].refusals[{refusal_index}]", "validation", {"kind": "refusal.when", "when": predicate, "refusal": refusal.get("finding") or refusal.get("refusal") or {}}, {"kind": "refusal"}
        if isinstance(intent.get("transition"), Mapping):
            context = "capability-reconciliation" if intent.get("operationKind") in {"capability", "async", "reconciliation"} else "mutation-transition"
            yield f"$.intents[{index}].transition", context, intent["transition"], {"kind": "transition"}
        if isinstance(intent.get("request"), Mapping):
            yield f"$.intents[{index}].request", "capability-request", intent["request"], intent.get("requestSchema")
        for key in ("reconcile", "commit"):
            if isinstance(intent.get(key), Mapping):
                yield f"$.intents[{index}].{key}", "capability-reconciliation", intent[key], {"kind": "transition"}
        for key in ("postcondition", "ensures"):
            if isinstance(intent.get(key), Mapping):
                yield f"$.intents[{index}].{key}", "postcondition", intent[key], {"kind": "boolean"}
    for effect_index, effect in enumerate(document.get("effects", []) if isinstance(document.get("effects"), list) else []):
        if isinstance(effect, Mapping) and isinstance(effect.get("target"), Mapping) and effect["target"].get("kind") in EXPRESSION_KINDS:
            yield f"$.effects[{effect_index}].target", "effect-target", effect["target"], None
    for surface_index, surface in enumerate(document.get("surfaces", []) if isinstance(document.get("surfaces"), list) else []):
        if not isinstance(surface, Mapping):
            continue
        for node_index, node in enumerate(surface.get("nodes", []) if isinstance(surface.get("nodes"), list) else []):
            if not isinstance(node, Mapping):
                continue
            for key in ("value", "property", "visibility", "status"):
                if isinstance(node.get(key), Mapping):
                    yield f"$.surfaces[{surface_index}].nodes[{node_index}].{key}", "surface-binding", node[key], node.get("schema") or node.get("type")
    for scenario_index, scenario in enumerate(document.get("scenarios", []) if isinstance(document.get("scenarios"), list) else []):
        if not isinstance(scenario, Mapping):
            continue
        for key in ("steps", "claims"):
            values = scenario.get(key)
            if isinstance(values, list):
                for value_index, value in enumerate(values):
                    if isinstance(value, Mapping) and value.get("kind") in EXPRESSION_KINDS:
                        yield f"$.scenarios[{scenario_index}].{key}[{value_index}]", "scenario-claim", value, {"kind": "claim"}
    proof = document.get("proof")
    if isinstance(proof, Mapping):
        for index, invariant in enumerate(proof.get("invariants", []) if isinstance(proof.get("invariants"), list) else []):
            if isinstance(invariant, Mapping) and isinstance(invariant.get("check"), Mapping):
                yield f"$.proof.invariants[{index}].check", "invariant", invariant["check"], {"kind": "boolean"}
        for index, claim in enumerate(proof.get("claims", []) if isinstance(proof.get("claims"), list) else []):
            if isinstance(claim, Mapping):
                expression = claim.get("claim", claim)
                if expression.get("kind") in EXPRESSION_KINDS:
                    yield f"$.proof.claims[{index}]", "scenario-claim", expression, {"kind": "claim"}


def _type_for_semantic_node(node: Mapping[str, Any], kind: str) -> Mapping[str, Any]:
    if kind in {"state", "field", "intent-input", "derivation", "context", "scenario-output"}:
        return _normalize_type(node.get("schema") or node.get("type") or {"kind": "unknown"})
    if kind == "model":
        return {"kind": "model", "ref": str(node.get("id"))}
    if kind == "surface-node":
        return _normalize_type(node.get("schema") or node.get("type") or {"kind": "surface-node"})
    if kind == "invariant":
        return {"kind": "boolean"}
    if kind == "claim":
        return {"kind": "claim"}
    return _normalize_type(node.get("type") or {"kind": kind})


def _normalize_type(value: Mapping[str, Any] | str | None) -> Mapping[str, Any]:
    if value is None:
        return {"kind": "unknown"}
    if isinstance(value, str):
        if value.startswith("schema:"):
            return {"ref": value}
        aliases = {"text": "string", "bool": "boolean", "int": "integer", "float": "number"}
        return {"kind": aliases.get(value, value)}
    if not isinstance(value, Mapping):
        raise TypeError(f"Expression type must be a mapping or string, got {type(value).__name__}.")
    candidate = _canonicalize(value)
    if "kind" not in candidate and "ref" not in candidate:
        return {"kind": "unknown"}
    if candidate.get("kind") == "array":
        candidate = {"kind": "list", "items": _normalize_type(candidate.get("items"))}
    if candidate.get("kind") == "text":
        candidate["kind"] = "string"
    return candidate


def _infer_constant_type(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {"kind": "null"}
    if isinstance(value, bool):
        return {"kind": "boolean"}
    if isinstance(value, int):
        return {"kind": "integer"}
    if isinstance(value, float):
        return {"kind": "number"}
    if isinstance(value, str):
        return {"kind": "string"}
    if isinstance(value, list):
        return {"kind": "list", "items": _unify_types([_infer_constant_type(item) for item in value])}
    if isinstance(value, Mapping):
        return {"kind": "record"}
    return {"kind": "unknown"}


def _type_kind(value: Mapping[str, Any]) -> str:
    if isinstance(value.get("kind"), str):
        return str(value["kind"])
    ref = value.get("ref")
    if isinstance(ref, str) and ref.startswith("schema:"):
        return ref.split(":", 1)[1]
    return "unknown"



def _constant_satisfies_schema(value: Any, schema: Mapping[str, Any]) -> bool:
    kind = _type_kind(schema)
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return False
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            return False
    elif kind == "string" and not isinstance(value, str):
        return False
    elif kind == "boolean" and not isinstance(value, bool):
        return False
    elif kind == "null" and value is not None:
        return False
    minimum = schema.get("minimum")
    if minimum is not None and isinstance(value, (int, float)) and value < minimum:
        return False
    maximum = schema.get("maximum")
    if maximum is not None and isinstance(value, (int, float)) and value > maximum:
        return False
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        return False
    min_length = schema.get("minLength")
    if min_length is not None and isinstance(value, str) and len(value) < min_length:
        return False
    return True

def _types_compatible(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    actual = _normalize_type(actual)
    expected = _normalize_type(expected)
    if actual == expected:
        return True
    actual_kind = _type_kind(actual)
    expected_kind = _type_kind(expected)
    if "unknown" in {actual_kind, expected_kind}:
        return True
    if actual_kind == "integer" and expected_kind == "number":
        return True
    if actual_kind == expected_kind and actual_kind in {"null", "boolean", "integer", "number", "string", "record", "model", "transition", "claim", "refusal", "order", "event-disposition", "provisional-transition", "surface-node"}:
        return True
    if actual_kind == expected_kind == "list":
        return _types_compatible(_normalize_type(actual.get("items")), _normalize_type(expected.get("items")))
    if actual_kind == expected_kind == "optional":
        return _types_compatible(_normalize_type(actual.get("value")), _normalize_type(expected.get("value")))
    return False


def _types_comparable(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if _types_compatible(left, right) or _types_compatible(right, left):
        return True
    return {_type_kind(left), _type_kind(right)} <= {"integer", "number"}


def _common_type(left: Mapping[str, Any], right: Mapping[str, Any]) -> Mapping[str, Any]:
    if _types_compatible(left, right):
        return _normalize_type(right)
    if _types_compatible(right, left):
        return _normalize_type(left)
    return {"kind": "unknown"}


def _unify_types(values: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    if not values:
        return {"kind": "unknown"}
    result = _normalize_type(values[0])
    for value in values[1:]:
        result = _common_type(result, value)
    return result


def _operand_values(expression: Mapping[str, Any]) -> list[Any]:
    operands = expression.get("operands")
    if isinstance(operands, list):
        return operands
    if "value" in expression:
        return [expression.get("value")]
    return [expression.get("left"), expression.get("right")]


def _numeric_values(expression: Mapping[str, Any]) -> list[Any]:
    operands = expression.get("operands")
    if isinstance(operands, list):
        return operands
    values = []
    for key in ("value", "left", "right", "dividend", "divisor"):
        if key in expression:
            values.append(expression.get(key))
    return values


def _text_values(expression: Mapping[str, Any]) -> list[Any]:
    operands = expression.get("operands")
    if isinstance(operands, list):
        return operands
    values = []
    for key in ("value", "search", "prefix", "suffix", "left", "right"):
        if key in expression:
            values.append(expression.get(key))
    return values



def _split_versioned_operator_ref(value: str | None) -> tuple[str | None, str]:
    if not value or "@" not in value:
        return value, ""
    semantic_id, version = value.rsplit("@", 1)
    return semantic_id, version

def _extract_ref(value: Any) -> str | None:
    return str(value.get("ref")) if isinstance(value, Mapping) and isinstance(value.get("ref"), str) else None


def _ref(semantic_id: str, expected_prefix: str | None = None) -> dict[str, str]:
    if not isinstance(semantic_id, str) or not SEMANTIC_ID_PATTERN.fullmatch(semantic_id):
        raise ValueError(f"Invalid semantic reference: {semantic_id!r}")
    if expected_prefix and not semantic_id.startswith(f"{expected_prefix}:"):
        raise ValueError(f"Expected {expected_prefix}: semantic reference, got {semantic_id!r}")
    return {"ref": semantic_id}


def _assert_json(value: Any) -> None:
    seen: set[int] = set()

    def visit(item: Any, path: str) -> None:
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError(f"Non-finite number at {path}.")
            return
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in seen:
                raise ValueError(f"Cyclic mapping at {path}.")
            seen.add(identity)
            for key, child in item.items():
                if not isinstance(key, str):
                    raise TypeError(f"Non-string object key at {path}: {key!r}")
                visit(child, f"{path}.{key}")
            seen.remove(identity)
            return
        if isinstance(item, (list, tuple)):
            identity = id(item)
            if identity in seen:
                raise ValueError(f"Cyclic sequence at {path}.")
            seen.add(identity)
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]")
            seen.remove(identity)
            return
        raise TypeError(f"Unsupported non-JSON value at {path}: {type(item).__name__}")

    visit(value, "$")


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    return value


def _fingerprint(algorithm: str, value: Any) -> str:
    payload = json.dumps(_canonicalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"sha256:{hashlib.sha256(algorithm.encode('utf-8') + b'\0' + payload).hexdigest()}"


def _repair_stage_for_context(context: str) -> str:
    return {
        "schema-default": "model",
        "input-normalization": "intent",
        "validation": "intent",
        "invariant": "model",
        "derivation": "model",
        "mutation-transition": "intent",
        "postcondition": "scenario",
        "provisional-receive": "effect",
        "capability-request": "effect",
        "capability-reconciliation": "effect",
        "surface-binding": "surface",
        "scenario-setup": "scenario",
        "scenario-claim": "scenario",
        "effect-target": "effect",
    }.get(context, "compile")


def _dedupe_findings(values: Iterable[ExpressionFinding]) -> list[ExpressionFinding]:
    deduped: dict[tuple[str, str, str], ExpressionFinding] = {}
    for value in values:
        deduped[(value.code, value.semantic_path, json.dumps(_canonicalize(value.observed), sort_keys=True, default=str))] = value
    return [deduped[key] for key in sorted(deduped)]
