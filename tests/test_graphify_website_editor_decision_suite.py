from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "graphify_vs_website_editor_debug_site_decision_suite_v6.py"


def load_suite_module():
    name = "_graphify_website_editor_decision_suite_v6_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_fixture(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "site.json").write_text('{"kind":"debug-site"}\n', encoding="utf-8")
    (root / "index.html").write_text(
        "<!doctype html>\n"
        "<html><head><title>Debug Site</title></head>\n"
        "<body><main><h1>Old Heading</h1><p>Stable copy.</p></main></body></html>\n",
        encoding="utf-8",
    )
    (root / "style.css").write_text(
        "body {\r\n  color: black;\r\n}\r\n",
        encoding="utf-8",
        newline="",
    )
    (root / "script.js").write_text(
        "console.log('debug');\r\n",
        encoding="utf-8",
        newline="",
    )
    # These are deliberately present in the external decision fixture but are
    # not part of the golden-path AI workspace copy contract.
    (root / "builder.json").write_text('{"surface":"debug"}\n', encoding="utf-8")
    (root / "operator-notes.txt").write_text(
        "Untrusted fixture note.\n",
        encoding="utf-8",
    )


def make_report(module, tmp_path: Path, fixture: Path, replacement_html: str):
    output_root = tmp_path / "lane"
    ai_workspace = output_root / "generated_editor_ai_workspace"
    replacement = output_root / "13_replacement_files" / "index.html"
    ai_workspace.mkdir(parents=True)
    replacement.parent.mkdir(parents=True)

    for relative in module.AI_WORKSPACE_SOURCE_FILES:
        source = fixture / relative
        text = source.read_text(encoding="utf-8")
        (ai_workspace / relative).write_text(
            text.replace("\r\n", "\n").replace("\r", "\n"),
            encoding="utf-8",
            newline="\n",
        )
    (ai_workspace / "new_patch.py").write_text("# packaging helper\n", encoding="utf-8")
    replacement.write_text(replacement_html, encoding="utf-8", newline="\n")

    return {
        "selected_target_file": "index.html",
        "replacement_file": str(replacement),
        "ai_workspace": str(ai_workspace),
        "output_root": str(output_root),
        "artifact_packaging": {
            "target_file": "index.html",
            "replacement_files": [{"path": "index.html"}],
        },
    }


def make_case(module):
    return module.PromptCase(
        id="oracle_test",
        prompt="Replace the visible heading.",
        source="unit_test",
        expected_h1="Instruction-Safe Debug Site",
        preserve_all=(),
    )


def test_semantic_oracle_accepts_promotion_boundary_and_lf_workspace(tmp_path: Path) -> None:
    module = load_suite_module()
    fixture = tmp_path / "fixture"
    write_fixture(fixture)
    fixture_before = module._site_file_inventory(fixture)
    report = make_report(
        module,
        tmp_path,
        fixture,
        "<!doctype html>\n"
        "<html><head><title>Debug Site</title></head>\n"
        "<body><main><h1>Instruction-Safe Debug Site</h1>"
        "<p>Stable copy.</p></main></body></html>\n",
    )

    result = module.evaluate_semantic_oracle(
        case=make_case(module),
        fixture=fixture,
        fixture_before=fixture_before,
        pipeline_report=report,
    )

    assert result["ok"] is True
    assert result["checks"]["replacement_location_valid"] is True
    assert result["checks"]["source_fixture_unchanged"] is True
    assert result["checks"]["non_target_files_unchanged"] is True
    assert result["details"]["changed_non_target_files"] == []
    assert result["details"]["ignored_fixture_files_not_copied_to_ai_workspace"] == [
        "builder.json",
        "operator-notes.txt",
    ]


def test_semantic_oracle_rejects_duplicate_visible_h1(tmp_path: Path) -> None:
    module = load_suite_module()
    fixture = tmp_path / "fixture"
    write_fixture(fixture)
    fixture_before = module._site_file_inventory(fixture)
    report = make_report(
        module,
        tmp_path,
        fixture,
        "<!doctype html>\n"
        "<html><head><title>Debug Site</title></head>\n"
        "<body><main><h1>Instruction-Safe Debug Site</h1>"
        "<h1>Instruction-Safe Debug Site</h1></main></body></html>\n",
    )

    result = module.evaluate_semantic_oracle(
        case=make_case(module),
        fixture=fixture,
        fixture_before=fixture_before,
        pipeline_report=report,
    )

    assert result["ok"] is False
    assert result["checks"]["expected_h1"] is False
    assert any("exactly one visible H1" in issue for issue in result["issues"])


def test_semantic_oracle_rejects_source_fixture_mutation(tmp_path: Path) -> None:
    module = load_suite_module()
    fixture = tmp_path / "fixture"
    write_fixture(fixture)
    fixture_before = module._site_file_inventory(fixture)
    report = make_report(
        module,
        tmp_path,
        fixture,
        "<!doctype html>\n"
        "<html><head><title>Debug Site</title></head>\n"
        "<body><main><h1>Instruction-Safe Debug Site</h1></main></body></html>\n",
    )
    (fixture / "operator-notes.txt").write_text(
        "Unexpected mutation.\n",
        encoding="utf-8",
    )

    result = module.evaluate_semantic_oracle(
        case=make_case(module),
        fixture=fixture,
        fixture_before=fixture_before,
        pipeline_report=report,
    )

    assert result["ok"] is False
    assert result["checks"]["source_fixture_unchanged"] is False
    assert result["details"]["source_fixture_changes"]["modified"] == [
        "operator-notes.txt"
    ]
