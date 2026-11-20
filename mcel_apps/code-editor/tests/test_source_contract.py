"""Canonical paired-source Code Editor authoring contract."""
from pathlib import Path
from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]

def test_canonical_pair_compiles_and_has_full_operational_coverage():
    report = compile_dsl_application(ROOT / "application.js", write_candidate=False)
    assert report.valid and report.app_id == "code-editor"
    ir = report.normalized_ir
    assert ir is not None
    assert {x["id"] for x in ir["capabilities"]} == {
        "capability:" + name for name in (
          "source-reader", "source-writer", "reviewer", "review-applier",
          "aider-preview", "aider-context", "aider-control", "component-docs",
          "sandboxed-viewport", "scm-definition", "studio-source-tools",
          "scm-runtime", "scm-proof")
    }
    assert len(ir["intents"]) == 39
    assert len(ir["effects"]) == 36
    assert all(i["risk"] == "source-write" for i in ir["intents"] if i["id"] in {
        "intent:save-source", "intent:apply-reviewed-change"})
    assert {x["id"]:x["risk"] for x in ir["capabilities"]}["capability:source-writer"] == "source-write"
    assert "application.html" in {Path(x["path"]).name for x in ir["provenance"]["frontend"]["sourceFiles"]}
