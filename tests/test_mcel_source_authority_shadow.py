"""Patch 3: non-promoting canonical/incumbent source authority observations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from main_computer import mcel_source_authority_shadow as shadow
from tools import mcel_source_authority_shadow as cli

ROOT = Path(__file__).resolve().parents[1]


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_code_editor_source_authorities_are_distinct_and_not_promoted():
    result = shadow.evaluate_source_authority("code-editor", ROOT)
    assert result["schema"] == shadow.SCHEMA
    assert result["mode"] == "offline-read-only-shadow"
    assert result["sourceResolution"]["liveAuthority"] == "incumbent-unchanged"
    assert result["canonicalCompile"]["valid"] is True
    assert result["incumbentCompile"]["valid"] is True
    assert result["incumbentHost"]["valid"] is True
    assert result["canonicalCompile"]["applicationId"] == "code-editor"
    assert result["incumbentCompile"]["applicationId"] == "semantic-code-editor"
    assert result["canonicalCompile"]["counts"]["intents"] == 8
    assert result["incumbentCompile"]["counts"]["intents"] == 39
    assert result["comparison"]["status"] == "conflicting"
    assert "APPLICATION_ID_DIVERGENCE" in result["promotion"]["blockers"]
    assert "IR_SEMANTICS_DIVERGENCE" in result["promotion"]["blockers"]
    assert "MISSING_CANONICAL_HTML_PAIR" in result["promotion"]["blockers"]
    assert result["declarationDelta"]["intents"]["incumbentOnly"]
    assert result["promotion"]["eligible"] is False
    assert result["promotion"]["performed"] is False
    assert not any(result["noninterference"].values())


def test_shadow_never_requests_candidate_staging(monkeypatch):
    original = shadow.compile_dsl_application
    calls = []

    def witness(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs.get("write_candidate") is False
        return original(*args, **kwargs)

    monkeypatch.setattr(shadow, "compile_dsl_application", witness)
    shadow.evaluate_source_authority("code-editor", ROOT)
    assert len(calls) == 2


def test_shadow_does_not_change_any_existing_source_or_routes():
    files = [
        ROOT / "mcel_apps/code-editor/application.js",
        ROOT / "tools/fixtures/mcel_semantic_other_apps/code-editor/application.js",
        ROOT / "tools/fixtures/mcel_semantic_other_apps/code-editor/application.html",
        ROOT / "main_computer/mcel_semantic_other_apps_host.py",
        ROOT / "main_computer/viewport_route_dispatch.py",
    ]
    baseline = [_hash(path) for path in files]
    shadow.evaluate_source_authority("code-editor", ROOT)
    assert baseline == [_hash(path) for path in files]
    host = files[3].read_text(encoding="utf-8")
    route = files[4].read_text(encoding="utf-8")
    assert "mcel_source_authority_shadow" not in host
    assert "mcel_source_authority_shadow" not in route


def test_file_manager_missing_canonical_is_not_resolved_to_a_fixture():
    result = shadow.evaluate_source_authority("file-manager", ROOT)
    assert result["canonicalCompile"] is None
    assert result["sourceResolution"]["canonical"]["javascript"]["exists"] is False
    assert result["sourceResolution"]["incumbent"]["javascript"]["exists"] is True
    assert "MISSING_CANONICAL_JAVASCRIPT" in result["promotion"]["blockers"]
    assert result["promotion"]["eligible"] is False


def test_task_manager_unpaired_canonical_never_promotes():
    result = shadow.evaluate_source_authority("task-manager", ROOT)
    assert result["canonicalCompile"]["valid"]
    assert result["incumbentCompile"]["valid"]
    assert "MISSING_CANONICAL_HTML_PAIR" in result["promotion"]["blockers"]
    assert result["promotion"]["eligible"] is False


def test_unsupported_app_is_refused_without_guessing_a_source():
    with pytest.raises(ValueError, match="No reviewed semantic-host"):
        shadow.resolve_sources("../../code-editor", ROOT)
    with pytest.raises(ValueError, match="No reviewed semantic-host"):
        shadow.evaluate_source_authority("slh", ROOT)


def test_corrupt_canonical_source_refuses_and_keeps_incumbent(tmp_path):
    repo = tmp_path
    canonical = repo / "mcel_apps/code-editor/application.js"
    incumbent = repo / "tools/fixtures/mcel_semantic_other_apps/code-editor/application.js"
    canonical.parent.mkdir(parents=True)
    incumbent.parent.mkdir(parents=True)
    canonical.write_text("this is not valid source (", encoding="utf-8")
    shutil.copyfile(ROOT / "tools/fixtures/mcel_semantic_other_apps/code-editor/application.js", incumbent)
    shutil.copyfile(ROOT / "tools/fixtures/mcel_semantic_other_apps/code-editor/application.html", incumbent.with_name("application.html"))
    result = shadow.evaluate_source_authority("code-editor", repo)
    assert result["canonicalCompile"]["valid"] is False
    assert "CANONICAL_COMPILE_FAILED" in result["promotion"]["blockers"]
    assert result["incumbentCompile"]["valid"] is True
    assert not result["promotion"]["eligible"]


def test_even_exact_ir_comparison_is_not_operational_certification(monkeypatch):
    monkeypatch.setattr(shadow, "compare_application_ir", lambda *args: {"status": "exact"})
    result = shadow.evaluate_source_authority("code-editor", ROOT)
    assert "IR_SEMANTICS_DIVERGENCE" not in result["promotion"]["blockers"]
    assert "LIVE_OPERATION_PARITY_UNVERIFIED" in result["promotion"]["blockers"]
    assert "LIVE_PRESENTATION_PARITY_UNVERIFIED" in result["promotion"]["blockers"]
    assert result["promotion"] == {
        "eligible": False,
        "performed": False,
        "blockers": result["promotion"]["blockers"],
    }


def test_cli_report_destination_is_confined_to_runtime_reports():
    okay = cli._report_destination(ROOT, "runtime/reports/mcel-source-authority-shadow/example.json")
    assert okay.is_relative_to(ROOT / "runtime/reports")
    for value in ("mcel_apps/code-editor/application.js", "/tmp/shadow.json",
                  "runtime/reports/../state/unwanted.json", "runtime/reports/bad.txt"):
        with pytest.raises(ValueError, match="runtime/reports"):
            cli._report_destination(ROOT, value)


def test_cli_can_write_only_a_diagnostic_report(tmp_path, capsys):
    # Simulate a repository marker, while compiling the fixed sources copied in.
    repo = tmp_path
    (repo / "main_computer").mkdir()
    (repo / "main_computer/mcel_semantic_other_apps_host.py").touch()
    src = ROOT / "tools/fixtures/mcel_semantic_other_apps/code-editor"
    inc = repo / "tools/fixtures/mcel_semantic_other_apps/code-editor"
    inc.mkdir(parents=True)
    for name in ("application.js", "application.html"):
        shutil.copyfile(src / name, inc / name)
    cp = repo / "mcel_apps/code-editor"
    cp.mkdir(parents=True)
    shutil.copyfile(ROOT / "mcel_apps/code-editor/application.js", cp / "application.js")
    assert cli.main(["--repo", str(repo), "--app", "code-editor",
                     "--output", "runtime/reports/mcel-source-authority-shadow/report.json"]) == 0
    reports = list((repo / "runtime").rglob("*"))
    report_files = [p for p in reports if p.is_file()]
    assert len(report_files) == 1
    report = json.loads(report_files[0].read_text(encoding="utf-8"))
    assert report["promotion"]["eligible"] is False
    assert "promotion=REFUSED" in capsys.readouterr().out
