"""Patch 6: existing Task Manager promise probe; no operational authority."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import mcel_task_manager_promise_frob as frob


def test_existing_task_manager_is_the_target_and_not_a_new_app():
    assert frob.TAB_IDS == ("server-processes", "all-processes", "connections", "hardware")
    assert frob.check_host()["status"] == "PASS"
    assert frob.check_host()["implementation"]["legacyTabController"] is True
    assert frob.check_host()["implementation"]["phase3GeneratedMountInHostEntry"] is False


def test_frozen_optional_dsl_is_not_reconstructed_from_fixture(tmp_path: Path):
    missing = tmp_path / "missing.js"
    candidate, html = frob.notebook_candidate(missing, None)
    assert candidate["status"] == "UNVERIFIED"
    assert html is None
    witness, html = frob.shared_witness()
    assert witness["status"] == "PASS"
    assert "McelTabWorkspace.mount" in html
    assert witness["identity"].startswith("pinned independent")


def test_present_invalid_candidate_fails_instead_of_disappearing(tmp_path: Path):
    bad = tmp_path / "application.js"
    bad.write_text('module.exports = {}', encoding='utf-8')
    candidate, html = frob.notebook_candidate(bad, None)
    assert candidate["status"] == "FAIL"
    assert html is None


def test_frob_incomplete_pinned_evidence_and_no_mutation(tmp_path: Path):
    protected = [frob.HOST, frob.HOST_STYLE, frob.HOST_ENTRY, frob.HOST_ADAPTER,
                 frob.HOST_BINDINGS, frob.HOST_TABS]
    before = {p: frob.sha(p) for p in protected}
    result = frob.run(output=tmp_path, source=tmp_path / "missing.js", fixture=None, browser=False)
    assert result["status"] == "INCOMPLETE" and result["promiseFulfilled"] is False
    assert result["primaryApplication"] == "existing-task-manager"
    assert result["secondaryApplication"] == "existing-file-manager-if-required"
    assert result["notebookCandidate"]["status"] == "UNVERIFIED"
    assert result["independentGenericRendererWitness"]["status"] == "PASS"
    assert result["browser"]["status"] == "UNVERIFIED"
    assert result["releaseAuthority"]["taskManagerModified"] is True
    assert all(value is False for name, value in result["releaseAuthority"].items() if name != "taskManagerModified")
    assert {p: frob.sha(p) for p in protected} == before
    assert (tmp_path / "report.json").is_file() and (tmp_path / "barriers.md").is_file()
    parsed = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert parsed["orderedBarriers"][0]["id"] == "frozen-notebook-source"
    assert parsed["orderedBarriers"][1]["id"] == "real-app-mount"
    assert "work-orders" in parsed["historicalPromiseGapPacket"] or "promise_gap" in parsed["historicalPromiseGapPacket"]


def test_missing_static_host_does_not_become_pass(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(frob, "HOST", tmp_path / "missing.html")
    assert frob.check_host()["status"] == "BLOCKED"


def test_executed_browser_witness_is_inert_and_visible(tmp_path: Path):
    pytest.importorskip("playwright.sync_api")
    result = frob.run(output=tmp_path, source=tmp_path / "missing.js", fixture=None, browser=True)
    if result["browser"]["status"] == "UNVERIFIED":
        pytest.skip("Browser not installed")
    assert result["browser"]["status"] == "PASS", result["browser"]["errors"]
    assert len(result["browser"]["cases"]) == 8
    assert {x["case"] for x in result["browser"]["cases"]} == {
        "existing-app-static", "generic-tab-witness"}
    for case in result["browser"]["cases"]:
        assert case["status"] == "PASS"
        assert not case["requests"]
        assert (tmp_path / case["screenshot"]).is_file()
        assert (tmp_path / case["screenshot"]).stat().st_size > 1000
    statics = [x for x in result["browser"]["cases"] if x["case"] == "existing-app-static"]
    assert all(x["geometry"]["rootVisible"] for x in statics)
    assert all(x["geometry"]["notebookHeight"] >= 60 for x in statics)
    assert result["status"] == "INCOMPLETE" and not result["promiseFulfilled"]
    observed = {x["id"]: x["status"] for x in result["orderedBarriers"]}
    assert observed["monitoring-first-presentation"] == "PARTIAL_VERIFIED"
    assert observed["real-app-mount"] == "PARTIAL_VERIFIED"
    assert result["guardedLabMount"]["status"] == "PASS"


def test_existing_promise_frob_defaults_to_task_manager(tmp_path: Path, capsys):
    from tools import mcel_promise_gap_frob as primary
    code = primary.main(["--no-browser", "--output", str(tmp_path)])
    assert code == 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["primaryApplication"] == "existing-task-manager"
    assert "focus: existing-task-manager" in capsys.readouterr().out
    assert primary.main(["--no-browser", "--candidate", "inventory-c2=unexpected.js"]) == 2


def test_old_promise_packet_is_still_opt_in_and_pinned(tmp_path: Path):
    from tools import mcel_promise_gap_frob as primary
    code = primary.main(["--focus", "historical", "--no-browser", "--output", str(tmp_path)])
    assert code == 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert {c["id"] for c in report["cases"]} == {"inventory-c2", "work-orders-independent"}
    assert report["packet"]["sha256"] == primary.TASK_PACKET_SHA256
