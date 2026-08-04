from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

from main_computer.mcel_application_ir import validate_application_ir
from main_computer.mcel_constrained_expression import (
    DomainOperatorDefinition,
    DomainOperatorRegistry,
    ExpressionSymbol,
    analyze_application_expressions,
    analyze_expression,
    build_expression_symbols,
    compare_equal,
    constant,
    domain_call,
    normalize_expression,
    number_add,
    number_increment,
    state_read,
    transition_assign,
    transition_sequence,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "mcel_application_ir" / "contract-counter.ir.json"
TOOL = ROOT / "tools" / "mcel_constrained_expression.py"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def codes(analysis) -> set[str]:
    return {item.code for item in analysis.diagnostics}


def test_builders_construct_typed_canonical_transition_without_execution() -> None:
    expression = transition_sequence(
        transition_assign("state:count", constant(0)),
        number_increment("state:revision"),
    )
    symbols = build_expression_symbols(load_fixture())

    analysis = analyze_expression(expression, context="mutation-transition", symbols=symbols)

    assert analysis.valid is True
    assert analysis.result_type == {"kind": "transition"}
    assert analysis.reads == ()
    assert analysis.writes == ("state:count", "state:revision")
    assert analysis.purity == "pure"
    assert analysis.determinism == "deterministic"
    assert analysis.normalized is not None
    assert analysis.normalized["type"] == {"kind": "transition"}
    assert expression.get("type") is None  # normalization never mutates authored input


def test_counter_application_has_fifteen_typed_expression_roots() -> None:
    report = analyze_application_expressions(load_fixture(), emit_reference_diagnostics=True)

    assert report.valid is True
    assert report.expression_count == 15
    assert report.diagnostics == ()
    by_path = dict(report.analyses)
    increment = by_path["$.intents[0].transition"]
    reset = by_path["$.intents[1].transition"]
    assert increment.writes == ("state:count", "state:revision")
    assert reset.writes == ("state:count", "state:revision")
    assert by_path["$.proof.invariants[0].check"].result_type == {"kind": "boolean"}


def test_expression_context_rejects_capability_result_inside_derivation() -> None:
    expression = {
        "kind": "capability.result.read",
        "field": {"ref": "field:Quote.sha256"},
    }
    symbols = {
        "field:Quote.sha256": ExpressionSymbol(
            "field:Quote.sha256", "field", {"kind": "string"}
        )
    }

    analysis = analyze_expression(expression, context="derivation", symbols=symbols)

    assert analysis.valid is False
    assert "MCEL_EXPR_CONTEXT_INVALID" in codes(analysis)


def test_numeric_operator_rejects_javascript_style_coercion() -> None:
    expression = number_add(constant("1"), constant(2))

    analysis = analyze_expression(expression, context="derivation")

    assert analysis.valid is False
    assert "MCEL_EXPR_NUMERIC_OPERAND_REQUIRED" in codes(analysis)


def test_expression_reference_kind_is_checked_against_symbol_table() -> None:
    expression = state_read("state:count")
    symbols = {
        "state:count": ExpressionSymbol(
            "state:count", "intent", {"kind": "integer"}
        )
    }

    analysis = analyze_expression(expression, context="derivation", symbols=symbols)

    assert analysis.valid is False
    assert "MCEL_EXPR_REFERENCE_KIND_MISMATCH" in codes(analysis)


def test_transition_rejects_noncanonical_write_authority() -> None:
    symbols = {
        "state:draft": ExpressionSymbol(
            "state:draft", "state", {"kind": "string"}, authority="renderer-local"
        )
    }
    expression = transition_assign("state:draft", constant("new"))

    analysis = analyze_expression(expression, context="mutation-transition", symbols=symbols)

    assert analysis.valid is False
    assert "MCEL_EXPR_WRITE_AUTHORITY_INVALID" in codes(analysis)


def test_constant_assignment_checks_target_schema_constraints() -> None:
    symbols = {
        "state:count": ExpressionSymbol(
            "state:count", "state", {"kind": "integer", "minimum": 0}, authority="canonical"
        )
    }

    analysis = analyze_expression(
        transition_assign("state:count", constant(-1)),
        context="mutation-transition",
        symbols=symbols,
    )

    assert analysis.valid is False
    assert "MCEL_EXPR_CONSTANT_SCHEMA_VIOLATION" in codes(analysis)


def test_domain_operator_registry_is_versioned_pure_and_typed() -> None:
    registry = DomainOperatorRegistry()
    registry.register(
        DomainOperatorDefinition(
            semantic_id="operator:path.is-inside-root",
            version="v1",
            input_types={
                "path": {"kind": "string"},
                "root": {"kind": "string"},
            },
            result_type={"kind": "boolean"},
            allowed_contexts=frozenset({"validation", "capability-request"}),
            description="Return whether a normalized relative path remains inside a root.",
        )
    )
    expression = domain_call(
        "operator:path.is-inside-root",
        "v1",
        path=constant("project/file.txt"),
        root=constant("project"),
    )

    analysis = analyze_expression(expression, context="validation", operators=registry)

    assert analysis.valid is True
    assert analysis.result_type == {"kind": "boolean"}
    assert analysis.referenced_semantic_ids == ("operator:path.is-inside-root",)
    record = registry.to_record()
    assert record["schema"] == "mcel.domain-operator-registry.v1"
    assert [item["name"] for item in record["operators"][0]["parameters"]] == ["path", "root"]
    assert record["operators"][0]["purity"] == "pure"
    assert record["operators"][0]["determinism"] == "deterministic"


def test_unregistered_domain_operator_fails_closed() -> None:
    expression = domain_call(
        "operator:git.ref-name-valid",
        "v1",
        constant("feature/test"),
    )

    analysis = analyze_expression(expression, context="validation")

    assert analysis.valid is False
    assert "MCEL_EXPR_DOMAIN_OPERATOR_UNREGISTERED" in codes(analysis)


def test_legacy_opaque_function_is_explicit_nonblocking_migration_debt() -> None:
    expression = {
        "kind": "legacy.opaque-function",
        "functionHash": "sha256:deadbeef",
        "type": {"kind": "boolean"},
    }

    analysis = analyze_expression(expression, context="validation")

    assert analysis.valid is True
    assert analysis.purity == "opaque"
    finding = analysis.diagnostics[0]
    assert finding.code == "MCEL_EXPR_LEGACY_OPAQUE_MIGRATION_DEBT"
    assert finding.blocking is False
    assert finding.severity == "warning"


def test_expression_normalization_and_fingerprint_ignore_object_key_order() -> None:
    left = {
        "kind": "compare.equal",
        "left": {"kind": "constant", "value": 1},
        "right": {"kind": "constant", "value": 1},
    }
    right = {
        "right": {"value": 1, "kind": "constant"},
        "left": {"value": 1, "kind": "constant"},
        "kind": "compare.equal",
    }

    left_analysis = analyze_expression(left, context="validation")
    right_analysis = analyze_expression(right, context="validation")

    assert left_analysis.valid and right_analysis.valid
    assert left_analysis.normalized == right_analysis.normalized
    assert left_analysis.fingerprint == right_analysis.fingerprint
    assert normalize_expression(left, context="validation") == left_analysis.normalized


def test_application_ir_validation_includes_expression_context_and_type_checks() -> None:
    candidate = load_fixture()
    candidate["derivations"] = [
        {
            "id": "derivation:invalid-result-read",
            "kind": "derivation",
            "schema": {"kind": "string"},
            "derive": {
                "kind": "capability.result.read",
                "field": {"ref": "field:missing"},
            },
        }
    ]

    report = validate_application_ir(candidate)

    assert report.valid is False
    report_codes = {item.code for item in report.diagnostics}
    assert "MCEL_EXPR_CONTEXT_INVALID" in report_codes


def test_application_ir_validation_rejects_transition_type_mismatch() -> None:
    candidate = load_fixture()
    reset = next(item for item in candidate["intents"] if item["id"] == "intent:reset")
    reset["transition"]["steps"][0]["value"] = constant("zero")

    report = validate_application_ir(candidate)

    assert report.valid is False
    assert "MCEL_EXPR_OPERAND_TYPE_MISMATCH" in {item.code for item in report.diagnostics}


def test_counter_semantic_fingerprint_is_unchanged_by_wave2a_analysis() -> None:
    report = validate_application_ir(load_fixture())

    assert report.valid is True
    assert report.semantic_fingerprint == "sha256:a9dbe6b7ec49978d313f18836b30c3394539c18f29430c3a7553837bc46eb0ef"
    assert report.source_binding_fingerprint == "sha256:47eb3d1888708ab67c0c4c5c6a5e284f7178f68cf4efb3d1e8b5c33f30236610"


def test_expression_cli_runs_without_site_packages() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            str(TOOL),
            "--input",
            str(FIXTURE),
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["valid"] is True
    assert payload["irValid"] is True
    assert payload["expressionCount"] == 15
    assert payload["diagnosticCount"] == 0


def test_domain_operator_registry_schema_and_fingerprint_are_stable() -> None:
    schema_path = ROOT / "main_computer" / "schemas" / "mcel.domain-operator-registry.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema"]["const"] == "mcel.domain-operator-registry.v1"

    first = DomainOperatorRegistry()
    second = DomainOperatorRegistry()
    operators = [
        DomainOperatorDefinition(
            semantic_id="operator:content.sha256",
            version="v1",
            input_types={"content": {"kind": "string"}},
            result_type={"kind": "string"},
            allowed_contexts=frozenset({"capability-request", "validation"}),
        ),
        DomainOperatorDefinition(
            semantic_id="operator:path.is-inside-root",
            version="v1",
            input_types={"path": {"kind": "string"}, "root": {"kind": "string"}},
            result_type={"kind": "boolean"},
            allowed_contexts=frozenset({"validation"}),
        ),
    ]
    for definition in operators:
        first.register(definition)
    for definition in reversed(operators):
        second.register(definition)

    assert first.to_record() == second.to_record()
    assert first.to_record()["fingerprint"].startswith("sha256:")
