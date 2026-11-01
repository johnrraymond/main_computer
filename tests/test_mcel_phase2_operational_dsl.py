"""Public paired-source operational DSL contracts; no runtime-execution claims."""
from __future__ import annotations

import copy
from pathlib import Path

from main_computer.mcel_application_ir import validate_application_ir
from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tools/fixtures/mcel_phase2_operations"


def codes(report):
    return {d.get("code") for d in report.diagnostics}


def compile_changed(tmp_path: Path, change=None, html_change=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    js = (FIX / "application.js").read_text(encoding="utf-8")
    html = (FIX / "application.html").read_text(encoding="utf-8")
    if change:
        js = change(js)
    if html_change:
        html = html_change(html)
    (tmp_path / "application.js").write_text(js, encoding="utf-8")
    (tmp_path / "application.html").write_text(html, encoding="utf-8")
    return compile_dsl_application(tmp_path / "application.js")


def test_real_paired_public_operational_compilation():
    report = compile_dsl_application(FIX / "application.js")
    assert report.valid, report.diagnostics
    ir = report.normalized_ir
    assert len(ir["capabilities"]) == 1
    assert len(ir["intents"]) == 3
    assert len(ir["effects"]) == 2
    assert len(ir["states"]) == 3
    assert all(x["authority"] in {"derived", "renderer-local"} for x in ir["states"])
    assert all(x["effectKind"] == "capability-request" for x in ir["effects"])
    assert all(x["risk"] == "external-read" for x in ir["effects"])
    assert all(not x["writes"] for x in ir["intents"])
    assert {x["target"]["ref"] for intent in ir["intents"] for x in intent.get("resultBindings", [])} == {"state:entries", "state:preview"}
    assert all(x["source"] == "capability-response" and x["disposition"] == "completed"
               for intent in ir["intents"] for x in intent.get("resultBindings", []))
    collection = next(x for x in ir["surfaces"][0]["nodes"] if x.get("sourceName") == "entries")
    assert collection["selectionIntent"]["ref"] == "intent:select-entry"
    selected = next(x for x in ir["intents"] if x["id"] == "intent:select-entry")
    assert selected["collectionSelection"]["source"]["ref"] == "state:entries"
    assert selected["collectionSelection"]["membership"] == "required"
    assert selected["effectRefs"] == [] and selected["writes"] == []
    assert ir["surfaces"][0]["interfaceIntent"]["regions"]
    assert len(ir["provenance"]["frontend"]["sourceFiles"]) == 2


def test_determinism_and_layout_only_change(tmp_path):
    first = compile_dsl_application(FIX / "application.js")
    second = compile_dsl_application(FIX / "application.js")
    assert first.valid and first.semantic_fingerprint == second.semantic_fingerprint
    changed = compile_changed(tmp_path, html_change=lambda s: s.replace('data-mcel-priority="primary"','data-mcel-priority="secondary"'))
    assert changed.valid, changed.diagnostics
    def without_source(items):
        return [{k: v for k, v in x.items() if k != "source"} for x in items]
    for name in ("intents", "effects", "capabilities"):
        assert without_source(changed.normalized_ir[name]) == without_source(first.normalized_ir[name])


def test_public_capability_rejects_invalid_request_authority(tmp_path):
    replacements = [
        ('{risk: "external-read", description:', '{risk: "superuser", description:', "MCEL_DSL_CAPABILITY_RISK_REQUIRED"),
        ('{operation: "preview", arguments:', '{operation: "missing", arguments:', "MCEL_DSL_CAPABILITY_OPERATION_UNKNOWN"),
        ('resultTo: entries', 'resultTo: selectedEntry', "MCEL_DSL_RESULT_AUTHORITY_INVALID"),
        ('resultTo: entries', 'resultTo: preview', "MCEL_DSL_RESULT_SCHEMA_MISMATCH"),
        ('"listDirectory", {result:', '"listDirectory", {risk: "source-write", result:', "MCEL_DSL_CAPABILITY_RISK_MISMATCH"),
        ('{operation: "list", resultTo: entries}', '{operation: "list", resultTo: entries, unsafe: true}', "MCEL_DSL_CAPABILITY_REQUEST_OPTION_UNKNOWN"),
    ]
    for n,(needle, replacement, expected) in enumerate(replacements):
        report = compile_changed(tmp_path / str(n), change=lambda s: s.replace(needle, replacement, 1))
        assert not report.valid and expected in codes(report), (n, report.diagnostics)


def test_selection_rejects_wrong_source_and_wrong_identity(tmp_path):
    report = compile_changed(tmp_path / "bad-source", change=lambda s: s.replace('state: entries, select}', 'state: preview, select}'))
    assert not report.valid, report.diagnostics
    report = compile_changed(tmp_path / "bad-target", change=lambda s: s.replace('const selectedEntry = dsl.state.rendererLocal', 'const selectedEntry = dsl.state.derived'))
    assert not report.valid and "MCEL_DSL_SELECTION_STATE_INVALID" in codes(report)
    report = compile_changed(tmp_path / "bad-initial", change=lambda s: s.replace('{initial: ""}', '{initial: "invented"}'))
    assert not report.valid and "MCEL_DSL_SELECTION_EMPTY_INVALID" in codes(report)


def test_ir_tampering_rejected():
    report = compile_dsl_application(FIX / "application.js")
    assert report.valid
    ir = report.normalized_ir
    mutations = [
        ("result-tamper", lambda x: x["intents"][0]["resultBindings"][0]["target"].__setitem__("ref", "state:selected-entry"), "MCEL_IR_PUBLIC_CAPABILITY_INVALID"),
        ("risk-tamper", lambda x: x["intents"][0].__setitem__("risk", "source-write"), "MCEL_IR_PUBLIC_CAPABILITY_INVALID"),
        ("method-tamper", lambda x: x["intents"][0].__setitem__("runtimeMethod", "secretAccess"), "MCEL_IR_PUBLIC_CAPABILITY_INVALID"),
        ("argument-tamper", lambda x: next(i for i in x["intents"] if i["id"]=="intent:read-preview")["requestArguments"][0]["state"].__setitem__("ref", "state:entries"), "MCEL_IR_PUBLIC_CAPABILITY_INVALID"),
        ("selection-tamper", lambda x: next(i for i in x["intents"] if i["id"]=="intent:select-entry")["collectionSelection"].__setitem__("membership", "optional"), "MCEL_IR_COLLECTION_SELECTION_INVALID"),
        ("collection-tamper", lambda x: next(n for n in x["surfaces"][0]["nodes"] if n.get("selectionIntent"))["state"].__setitem__("ref", "state:preview"), "MCEL_IR_COLLECTION_SELECTION_INVALID"),
    ]
    for name, change, code in mutations:
        copy_ir = copy.deepcopy(ir)
        change(copy_ir)
        verdict = validate_application_ir(copy_ir)
        assert not verdict.valid and any(d.code == code for d in verdict.diagnostics), (name, [d.code for d in verdict.diagnostics])


def test_existing_hostbound_and_phase1_still_compile():
    for filename in ("mcel_apps/calculator/application.js", "mcel_apps/code-editor/application.js",
                     "tools/fixtures/mcel_html_pair/application.js"):
        r = compile_dsl_application(ROOT / filename)
        assert r.valid, (filename, r.diagnostics)


def test_four_operational_authoring_probes_compile_without_layout():
    folder = ROOT / "tools/fixtures/mcel_phase2_reference_apps"
    fingerprints = set()
    for app in ("file-manager", "task-manager", "code-editor", "calculator"):
        report = compile_dsl_application(folder / app / "application.js")
        assert report.valid, (app, report.diagnostics)
        ir = report.normalized_ir
        assert ir["layouts"] == [], app
        assert len(ir["provenance"]["frontend"]["sourceFiles"]) == 2, app
        assert ir["surfaces"][0]["interfaceIntent"]["regions"], app
        assert ir["capabilities"], app
        assert ir["intents"], app
        assert all(x["effectKind"] in {"canonical-write", "capability-request"} for x in ir["effects"]), app
        assert report.semantic_fingerprint not in fingerprints, app
        fingerprints.add(report.semantic_fingerprint)
    task = compile_dsl_application(folder / "task-manager/application.js").normalized_ir
    assert any(x["risk"] == "consequential" for x in task["effects"])
    editor = compile_dsl_application(folder / "code-editor/application.js").normalized_ir
    assert any(x["risk"] == "source-write" for x in editor["effects"])
    assert any(x["effectKind"] == "canonical-write" for x in editor["effects"])
    for app in ("task-manager", "calculator"):
        ir = compile_dsl_application(folder / app / "application.js").normalized_ir
        assert any("viewTransition" in intent for intent in ir["intents"]), app
