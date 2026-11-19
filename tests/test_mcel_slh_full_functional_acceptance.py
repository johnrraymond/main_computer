"""Self-tests for the SLH whole-application evidence runner.

These verify that its completeness claim is earned rather than inferred from
mere test discovery. No real local/network service or deployment is touched.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from tools import mcel_slh_full_functional_acceptance as full


def test_inventory_matches_compiled_app_and_all_evidence_nodes_exist():
    inv = full.inventory()
    declared = {x["id"] for x in inv["declared"]}
    assert inv["declared_count"] == len(declared)
    assert len(declared) >= 30  # Catch accidental compilation of an abbreviated app.
    assert any(x["risk"] == "consequential" for x in inv["declared"])
    assert all(set(x.intents) <= declared for x in full.CASES)
    assert len({x.id for x in full.CASES}) == len(full.CASES)
    assert all("::test_" in x.node or "::Test" in x.node for x in full.CASES)


def test_inventory_does_not_claim_certification_without_executing_tests():
    result = full.report_for({}, mode="inventory")
    assert result["status"] == "INCOMPLETE"
    assert result["declared_intents"] >= 30
    assert result["counts"]["PASS"] == 0
    assert result["counts"]["NOT_TESTED"] == result["declared_intents"]
    assert result["safety"]["production_mutations"] == "NEVER_DISPATCHED"


def test_missing_ui_coverage_or_disposable_effect_cannot_be_dressed_up_as_pass():
    # Even a completely green suite of existing cases is insufficient when
    # required UI or disposable-service evidence is absent.
    everything_green = {case.id: {"status": "PASS"} for case in full.CASES}
    result = full.report_for(everything_green, mode="isolated")
    missing = {row["intent"]: row for row in result["intents"]
               if row["status"] == "NOT_TESTED"}
    assert "intent:disconnect-remote-seh" in missing
    assert "intent:seh-connection-start" in missing
    assert "intent:seh-deployment-register" in missing
    assert result["status"] == "INCOMPLETE"
    # Adapter mocks cannot substitute for real disposable service effects.
    assert missing["intent:seh-deployment-deploy"]["lanes"]["isolated-effect"]["status"] == "NOT_TESTED"


def test_failed_behavioral_case_cannot_be_overridden_by_another_pass():
    result_map = {item.id: {"status": "PASS"} for item in full.CASES}
    result_map["organization-notebook-ui"] = {"status": "FAIL"}
    result = full.report_for(result_map, mode="isolated")
    assert result["status"] == "FAIL"
    assert next(x for x in result["intents"] if x["intent"] == "intent:organization-accept")["status"] == "FAIL"


def test_new_semantic_intent_is_not_silently_omitted(monkeypatch):
    original = full.declared_intents()
    monkeypatch.setattr(full, "declared_intents", lambda: original + [
        {"id": "intent:new-unmapped-operation", "risk": "consequential", "operationKind": "capability"}])
    report = full.report_for({}, mode="inventory")
    extra = next(x for x in report["intents"] if x["intent"] == "intent:new-unmapped-operation")
    assert extra["status"] == "NOT_TESTED"
    assert set(extra["lanes"]) == {"ui", "adapter", "isolated-effect"}


def test_skips_and_timeouts_are_not_passing_evidence(monkeypatch):
    # Patch the subprocess creation boundary. A skip must not become PASS.
    class FakeProcess:
        pid = 12345
        def wait(self,timeout=None):
            return 0
    calls=[]
    def launched(cmd, **kwargs):
        calls.append((cmd,kwargs))
        kwargs["stdout"].write("1 skipped in 0.01s")
        kwargs["stdout"].flush()
        return FakeProcess()
    monkeypatch.setattr(full.subprocess, "Popen", launched)
    result=full.run_case(full.CASES[0],timeout=10)
    assert result["status"] == "NOT_TESTED"
    assert calls[0][0][:3] == [full.sys.executable, "-m", "pytest"]
    assert calls[0][1]["cwd"] == full.ROOT



def test_no_implicit_real_host_and_live_protection_requires_explicit_admin(tmp_path, capsys):
    report = tmp_path / "inventory.json"
    code = full.main(["--mode", "inventory", "--report", str(report)])
    assert code == 4  # Incomplete coverage is not success.
    data = json.loads(report.read_text())
    assert data["live"]["status"] == "NOT_RUN"
    assert data["safety"]["screenshots_committed"] is False
    with pytest.raises(SystemExit) as ex:
        full.main(["--mode", "inventory", "--live-protected-reads",
                   "--live-host-url", "http://127.0.0.1:8765"])
    assert ex.value.code == 2
