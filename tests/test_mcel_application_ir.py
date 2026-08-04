from __future__ import annotations

import copy
import json
import math
import subprocess
import sys
from pathlib import Path

from main_computer.mcel_application_ir import (
    APPLICATION_IR_SCHEMA,
    APPLICATION_IR_SCHEMA_ID,
    SEMANTIC_FINGERPRINT_ALGORITHM,
    SOURCE_BINDING_FINGERPRINT_ALGORITHM,
    check_application_ir_schema,
    compare_application_ir,
    load_application_ir_schema,
    normalize_application_ir,
    validate_application_ir,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "mcel_application_ir" / "contract-counter.ir.json"
TOOL = ROOT / "tools" / "mcel_application_ir.py"

EXPECTED_SEMANTIC_FINGERPRINT = "sha256:a9dbe6b7ec49978d313f18836b30c3394539c18f29430c3a7553837bc46eb0ef"
EXPECTED_SOURCE_BINDING_FINGERPRINT = "sha256:47eb3d1888708ab67c0c4c5c6a5e284f7178f68cf4efb3d1e8b5c33f30236610"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def diagnostic_codes(report) -> set[str]:
    return {diagnostic.code for diagnostic in report.diagnostics}


def find_node(document: dict, collection: str, node_id: str) -> dict:
    return next(node for node in document[collection] if node["id"] == node_id)


def test_application_ir_json_schema_is_valid_draft_2020_12() -> None:
    schema = load_application_ir_schema()
    check_application_ir_schema(schema)
    assert schema["$id"] == APPLICATION_IR_SCHEMA_ID
    assert schema["properties"]["schema"]["const"] == APPLICATION_IR_SCHEMA


def test_schema_checker_rejects_unsupported_keywords_and_unresolved_refs() -> None:
    unsupported = copy.deepcopy(load_application_ir_schema())
    unsupported["unevaluatedProperties"] = False
    try:
        check_application_ir_schema(unsupported)
    except ValueError as exc:
        assert "Unsupported JSON Schema keyword" in str(exc)
    else:
        raise AssertionError("unsupported schema keyword was accepted")

    unresolved = copy.deepcopy(load_application_ir_schema())
    unresolved["properties"]["application"] = {"$ref": "#/$defs/missing"}
    try:
        check_application_ir_schema(unresolved)
    except ValueError as exc:
        assert "Unresolved local JSON Schema reference" in str(exc)
    else:
        raise AssertionError("unresolved schema reference was accepted")


def test_counter_fixture_normalizes_with_stable_fingerprints() -> None:
    report = validate_application_ir(load_fixture())

    assert report.valid is True
    assert report.diagnostics == ()
    assert report.semantic_fingerprint == EXPECTED_SEMANTIC_FINGERPRINT
    assert report.source_binding_fingerprint == EXPECTED_SOURCE_BINDING_FINGERPRINT
    assert report.normalized is not None
    assert report.normalized["normalization"]["normalizer"] == "mcel-application-ir-normalizer-v1"
    assert report.normalized["fingerprints"] == {
        "semantic": EXPECTED_SEMANTIC_FINGERPRINT,
        "semanticAlgorithm": SEMANTIC_FINGERPRINT_ALGORITHM,
        "sourceBinding": EXPECTED_SOURCE_BINDING_FINGERPRINT,
        "sourceBindingAlgorithm": SOURCE_BINDING_FINGERPRINT_ALGORITHM,
    }


def test_counter_fixture_source_hashes_bind_to_current_repository_files() -> None:
    import hashlib

    fixture = load_fixture()
    source_files = fixture["provenance"]["frontend"]["sourceFiles"]

    for record in source_files:
        path = ROOT / record["path"]
        assert path.is_file(), record["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]


def test_unordered_declarations_and_reference_lists_normalize_deterministically() -> None:
    original = load_fixture()
    reordered = copy.deepcopy(original)
    for key in ("states", "intents", "effects", "surfaces", "layouts", "scenarios"):
        reordered[key].reverse()
    find_node(reordered, "intents", "intent:increment")["reads"].reverse()
    find_node(reordered, "intents", "intent:increment")["writes"].reverse()
    reordered["provenance"]["frontend"]["sourceFiles"].reverse()
    reordered["provenance"]["nodeBindings"].reverse()

    left = normalize_application_ir(original)
    right = normalize_application_ir(reordered)

    assert left == right
    assert compare_application_ir(left, right)["status"] == "exact"


def test_source_line_movement_changes_source_binding_not_semantics() -> None:
    original = load_fixture()
    moved = copy.deepcopy(original)
    find_node(moved, "intents", "intent:increment")["source"]["start"]["line"] += 10
    for binding in moved["provenance"]["nodeBindings"]:
        if binding.get("semanticId") == "intent:increment":
            binding["source"]["start"]["line"] += 10

    left = validate_application_ir(original)
    right = validate_application_ir(moved)

    assert left.valid and right.valid
    assert left.semantic_fingerprint == right.semantic_fingerprint
    assert left.source_binding_fingerprint != right.source_binding_fingerprint


def test_migration_status_and_source_name_do_not_change_semantic_fingerprint() -> None:
    original = load_fixture()
    changed = copy.deepcopy(original)
    changed["application"]["authoringStatus"] = "dual-authored"
    find_node(changed, "intents", "intent:increment")["sourceName"] = "incrementCount"

    left = validate_application_ir(original)
    right = validate_application_ir(changed)

    assert left.valid and right.valid
    assert left.semantic_fingerprint == right.semantic_fingerprint
    assert left.source_binding_fingerprint == right.source_binding_fingerprint


def test_duplicate_semantic_id_emits_stable_diagnostic_key() -> None:
    first = load_fixture()
    duplicate = copy.deepcopy(first["states"][0])
    duplicate["source"]["start"]["line"] = 999
    first["states"].append(duplicate)

    second = copy.deepcopy(first)
    second["states"][-1]["source"]["start"]["line"] = 1001

    first_report = validate_application_ir(first)
    second_report = validate_application_ir(second)

    first_diag = next(item for item in first_report.diagnostics if item.code == "MCEL_IR_DUPLICATE_SEMANTIC_ID")
    second_diag = next(item for item in second_report.diagnostics if item.code == "MCEL_IR_DUPLICATE_SEMANTIC_ID")
    assert first_report.valid is False
    assert first_diag.diagnostic_key == second_diag.diagnostic_key
    assert first_diag.semantic_path == "state:count"
    assert first_diag.to_dict()["schema"] == "mcel.compiler-diagnostic.v1"


def test_unresolved_reference_fails_before_normalization() -> None:
    candidate = load_fixture()
    find_node(candidate, "intents", "intent:increment")["reads"][0] = {"ref": "state:missing"}

    report = validate_application_ir(candidate)

    assert report.valid is False
    assert "MCEL_IR_REFERENCE_UNRESOLVED" in diagnostic_codes(report)
    assert report.normalized is None


def test_wrong_kind_reference_is_rejected_contextually() -> None:
    candidate = load_fixture()
    find_node(candidate, "intents", "intent:increment")["reads"][0] = {"ref": "intent:reset"}

    report = validate_application_ir(candidate)

    assert report.valid is False
    assert "MCEL_IR_REFERENCE_KIND_MISMATCH" in diagnostic_codes(report)


def test_state_authority_is_required() -> None:
    candidate = load_fixture()
    del find_node(candidate, "states", "state:count")["authority"]

    report = validate_application_ir(candidate)

    assert report.valid is False
    assert "MCEL_IR_STATE_AUTHORITY_REQUIRED" in diagnostic_codes(report)


def test_unknown_expression_and_effect_kinds_are_rejected() -> None:
    candidate = load_fixture()
    find_node(candidate, "intents", "intent:increment")["transition"]["kind"] = "javascript.callback"
    find_node(candidate, "effects", "effect:increment.count-write")["effectKind"] = "run-callback"

    report = validate_application_ir(candidate)

    assert report.valid is False
    assert {
        "MCEL_IR_EXPRESSION_KIND_UNKNOWN",
        "MCEL_IR_EFFECT_KIND_UNKNOWN",
    } <= diagnostic_codes(report)


def test_transition_write_set_must_match_declared_authority() -> None:
    candidate = load_fixture()
    find_node(candidate, "intents", "intent:increment")["writes"] = [{"ref": "state:count"}]

    report = validate_application_ir(candidate)

    assert report.valid is False
    mismatch = next(item for item in report.diagnostics if item.code == "MCEL_IR_INTENT_WRITE_SET_MISMATCH")
    assert mismatch.observed == {
        "actual": ["state:count", "state:revision"],
        "declared": ["state:count"],
    }


def test_nonfinite_and_non_json_values_are_rejected() -> None:
    for invalid in (math.nan, math.inf, object()):
        candidate = load_fixture()
        candidate["application"]["invalid"] = invalid
        report = validate_application_ir(candidate)
        assert report.valid is False
        assert diagnostic_codes(report) == {"MCEL_IR_NONDETERMINISTIC_VALUE"}


def test_cli_runs_without_site_packages() -> None:
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
    assert payload["semanticFingerprint"] == EXPECTED_SEMANTIC_FINGERPRINT


def test_cli_validates_and_writes_canonical_ir(tmp_path: Path) -> None:
    output = tmp_path / "contract-counter.normalized.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--input",
            str(FIXTURE),
            "--write-normalized",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "status: pass" in completed.stdout
    assert EXPECTED_SEMANTIC_FINGERPRINT in completed.stdout
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["fingerprints"]["semantic"] == EXPECTED_SEMANTIC_FINGERPRINT
    assert output.read_bytes().endswith(b"\n")


def test_cli_returns_nonzero_and_machine_diagnostics_for_invalid_ir(tmp_path: Path) -> None:
    candidate = load_fixture()
    candidate["states"][0]["id"] = "not a semantic id"
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(candidate), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(TOOL), "--input", str(invalid), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 3
    payload = json.loads(completed.stdout)
    assert payload["valid"] is False
    assert any(item["code"] == "MCEL_IR_SEMANTIC_ID_INVALID" for item in payload["diagnostics"])
