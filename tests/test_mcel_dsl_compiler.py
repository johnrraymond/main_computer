from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from main_computer.mcel_application_ir import canonical_json_bytes
from main_computer.mcel_dsl_compiler import compile_dsl_application


ROOT = Path(__file__).resolve().parents[1]
DSL_FIXTURE = ROOT / "mcel_apps" / "calculator" / "application.js"
TOOL = ROOT / "tools" / "mcel_dsl_compile.py"


def _codes(report) -> set[str]:
    return {str(item.get("code") or "") for item in report.diagnostics}


def _write_source(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "application.js"
    path.write_text(body, encoding="utf-8")
    return path


def _write_compare_ir(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "comparison.ir.json"
    path.write_bytes(canonical_json_bytes(payload) + b"\n")
    return path


def _minimal_source(builder_body: str) -> str:
    return f"""\"use strict\";\nconst mcel = require(\"@mcel/app\");\nmodule.exports = mcel.defineApp(\n  {{id: \"test-app\", title: \"Test App\"}},\n  (dsl) => {{\n    {builder_body}\n  }}\n);\n"""


def test_real_dsl_app_compiles_to_exact_generated_semantics(tmp_path: Path) -> None:
    baseline = compile_dsl_application(DSL_FIXTURE)

    assert baseline.valid is True
    assert baseline.status == "pass"
    assert baseline.diagnostics == ()
    assert baseline.semantic_fingerprint
    assert baseline.source_binding_fingerprint
    assert baseline.normalized_ir is not None
    assert baseline.normalized_ir["application"]["appId"] == "calculator"
    assert baseline.normalized_ir["provenance"]["frontend"]["id"] == "mcel.dsl.v1"

    comparison_ir = _write_compare_ir(tmp_path, dict(baseline.normalized_ir))
    report = compile_dsl_application(DSL_FIXTURE, compare_ir_path=comparison_ir)

    assert report.valid is True
    assert report.status == "pass"
    assert report.comparison_status == "exact"
    assert report.semantic_fingerprint == baseline.semantic_fingerprint


def test_candidate_is_staged_outside_live_application(tmp_path: Path) -> None:
    live_files = sorted(DSL_FIXTURE.parent.rglob("*"))
    before = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in live_files
        if path.is_file()
    }

    baseline = compile_dsl_application(DSL_FIXTURE)
    assert baseline.valid is True and baseline.normalized_ir is not None
    comparison_ir = _write_compare_ir(tmp_path, dict(baseline.normalized_ir))

    report = compile_dsl_application(
        DSL_FIXTURE,
        compare_ir_path=comparison_ir,
        write_candidate=True,
        candidate_root=tmp_path / "compiler-candidates",
    )

    assert report.valid is True
    assert report.candidate_ir_path and report.candidate_ir_path.is_file()
    assert report.candidate_report_path and report.candidate_report_path.is_file()
    candidate = json.loads(report.candidate_ir_path.read_text(encoding="utf-8"))
    compiler_report = json.loads(report.candidate_report_path.read_text(encoding="utf-8"))
    assert candidate["fingerprints"]["semantic"] == baseline.semantic_fingerprint
    assert compiler_report["authority"] == {
        "candidatePromoted": False,
        "contractsGenerated": False,
        "evidenceReused": False,
        "liveApplicationChanged": False,
    }

    after = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in live_files
        if path.is_file()
    }
    assert after == before


def test_cli_runs_under_python_without_site_packages(tmp_path: Path) -> None:
    baseline = compile_dsl_application(DSL_FIXTURE)
    assert baseline.valid is True and baseline.normalized_ir is not None and baseline.semantic_fingerprint
    comparison_ir = _write_compare_ir(tmp_path, dict(baseline.normalized_ir))

    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            str(TOOL),
            "--input",
            str(DSL_FIXTURE),
            "--compare-ir",
            str(comparison_ir),
            "--write-candidate",
            "--candidate-root",
            str(tmp_path / "candidates"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "status: pass" in completed.stdout
    assert "comparison: exact" in completed.stdout
    assert baseline.semantic_fingerprint in completed.stdout


def test_generic_host_bound_application_builder_compiles(tmp_path: Path) -> None:
    source = _write_source(
        tmp_path,
        """\"use strict\";
const mcel = require(\"@mcel/app\");

function declareDemo(app) {
  app.presentation.hostBound(\"workspace\", {
    route: \"/applications/demo\",
    root: \"#demo-app\",
    presentationAuthority: \"existing-host-html\"
  });
  app.state.rendererLocal(\"mode\", app.field.string(), {initial: \"ready\"});
  app.intent.interaction(\"ping\", {
    runtimeMethod: \"ping\",
    binding: \"ping\",
    label: \"Ping host runtime\",
    lane: \"local-ui\",
    reads: [\"mode\"]
  });
  app.scenario.example(\"ping-completes\", {
    intent: \"ping\",
    expect: {ok: true}
  });
  app.layout.zones([\"main\"]);
  app.proof.semanticRuntimeProven();
}

module.exports = mcel.defineApp(
  {id: \"host-demo\", title: \"Host Demo\", semanticVersion: \"1\"},
  ({application}) => application.hostBound(declareDemo)
);
""",
    )

    report = compile_dsl_application(source)

    assert report.valid is True
    assert report.normalized_ir is not None
    ir = report.normalized_ir
    assert ir["application"]["appId"] == "host-demo"
    assert ir["states"][0]["id"] == "state:host-demo.mode"
    assert ir["surfaces"][0]["route"] == "/applications/demo"
    assert ir["surfaces"][0]["root"] == "#demo-app"
    assert ir["intents"][0]["executionBinding"] == "host-demo-runtime.ping"
    assert ir["proof"]["targetTruthStatus"] == "semantic-runtime-proven"


def test_forbidden_node_module_is_rejected(tmp_path: Path) -> None:
    source = _write_source(
        tmp_path,
        """\"use strict\";\nconst fs = require(\"node:fs\");\nconst mcel = require(\"@mcel/app\");\nmodule.exports = mcel.defineApp({id:\"test-app\",title:\"Test App\"}, () => ({}));\n""",
    )

    report = compile_dsl_application(source)

    assert report.valid is False
    assert "MCEL_DSL_REQUIRE_DENIED" in _codes(report)


def test_ambient_process_and_math_are_rejected(tmp_path: Path) -> None:
    process_source = _write_source(
        tmp_path,
        _minimal_source('process.cwd(); return {models:[],states:[],derivations:[],capabilities:[],invariants:[],intents:[],surfaces:[],layouts:[],scenarios:[]};'),
    )
    process_report = compile_dsl_application(process_source)
    assert process_report.valid is False
    assert "MCEL_DSL_AMBIENT_GLOBAL_FORBIDDEN" in _codes(process_report)

    math_path = tmp_path / "math.application.js"
    math_path.write_text(
        _minimal_source('Math.random(); return {models:[],states:[],derivations:[],capabilities:[],invariants:[],intents:[],surfaces:[],layouts:[],scenarios:[]};'),
        encoding="utf-8",
    )
    math_report = compile_dsl_application(math_path)
    assert math_report.valid is False
    assert "MCEL_DSL_AMBIENT_GLOBAL_FORBIDDEN" in _codes(math_report)


def test_nonportable_callback_result_is_rejected(tmp_path: Path) -> None:
    source = _write_source(
        tmp_path,
        _minimal_source('return {models:[() => 1],states:[],derivations:[],capabilities:[],invariants:[],intents:[],surfaces:[],layouts:[],scenarios:[]};'),
    )

    report = compile_dsl_application(source)

    assert report.valid is False
    assert "MCEL_DSL_NONPORTABLE_VALUE" in _codes(report) or "MCEL_DSL_DECLARATION_HANDLE_REQUIRED" in _codes(report)


def test_infinite_builder_is_terminated(tmp_path: Path) -> None:
    source = _write_source(
        tmp_path,
        """\"use strict\";\nconst mcel = require(\"@mcel/app\");\nwhile (true) {}\nmodule.exports = mcel.defineApp({id:\"test-app\",title:\"Test App\"}, () => ({}));\n""",
    )

    report = compile_dsl_application(source, timeout_ms=25)

    assert report.valid is False
    assert "MCEL_DSL_EVALUATION_TIMEOUT" in _codes(report)


def test_semantic_conflict_is_reported_and_not_promoted(tmp_path: Path) -> None:
    baseline = compile_dsl_application(DSL_FIXTURE)
    assert baseline.valid is True and baseline.normalized_ir is not None
    comparison_ir = _write_compare_ir(tmp_path, dict(baseline.normalized_ir))

    text = DSL_FIXTURE.read_text(encoding="utf-8").replace('initial: "basic"', 'initial: "graphing"', 1)
    source = _write_source(tmp_path, text)

    report = compile_dsl_application(
        source,
        compare_ir_path=comparison_ir,
        write_candidate=True,
        candidate_root=tmp_path / "candidates",
    )

    assert report.valid is False
    assert report.status == "semantic-conflict"
    assert report.comparison_status == "conflicting"
    assert "MCEL_DSL_SEMANTIC_EQUIVALENCE_FAILED" in _codes(report)
    assert report.candidate_ir_path and report.candidate_ir_path.is_file()
    assert report.to_dict()["authority"]["candidatePromoted"] is False


def test_missing_node_executable_fails_closed() -> None:
    report = compile_dsl_application(DSL_FIXTURE, node_executable="definitely-not-a-node-executable")

    assert report.valid is False
    assert "MCEL_DSL_NODE_EXECUTION_FAILED" in _codes(report)
