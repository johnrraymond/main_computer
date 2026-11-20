"""Patch 4A read-only, conservative mapping and VS Code role coverage gates."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from main_computer import mcel_code_editor_reconciliation_audit as audit
from tools import mcel_code_editor_reconciliation_audit as cli

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def report():
    return audit.evaluate_reconciliation(ROOT)


def test_every_intent_is_classified_without_guessing_equivalence(report):
    reconciled = report["functionalReconciliation"]
    assert reconciled["canonicalIntents"] == 8
    assert reconciled["incumbentIntents"] == 39
    assert reconciled["bridgeMappedIncumbentIntents"] == 8
    assert reconciled["incumbentAdditionalIntents"] == 31
    assert reconciled["coverageComplete"]
    assert sum(len(group) for group in reconciled["incumbentAdditionalGroups"].values()) == 31
    assert all(not row["behaviorallyEquivalent"] for row in reconciled["bridge"])


def test_nonintent_document_behaviors_are_not_falsely_missing(report):
    rows = {i["canonical"]["id"]: i for i in report["functionalReconciliation"]["bridge"]}
    for key in ("edit-draft", "discard-draft", "close-file"):
        row = rows[f"intent:code-editor.{key}"]
        assert row["classification"] == "runtime-generated-interaction"
        assert not row["incumbent"]


def test_separate_review_before_apply_is_preserved(report):
    row = next(i for i in report["functionalReconciliation"]["bridge"]
               if i["canonical"]["id"] == "intent:code-editor.apply-reviewed-patch")
    assert {i["id"] for i in row["incumbent"]} == {
        "intent:prepare-reviewed-change", "intent:apply-reviewed-change"}
    assert row["canonical"]["risk"] == "source-write"
    assert row["canonical"]["invariants"]
    applied = next(x for x in row["incumbent"] if x["id"] == "intent:apply-reviewed-change")
    assert {x["name"] for x in applied["requestArguments"]} == {"path", "review", "text"}
    assert applied["capabilityRisk"] == "source-write"
    assert next(x for x in row["incumbent"] if x["id"] == "intent:apply-reviewed-change")["risk"] == "source-write"


def test_host_mutation_explains_fingerprint_discrepancy(report):
    host = report["hostProjection"]
    assert host["onlyExpectedExternalJobAttachment"]
    assert len(host["sourceVsHostChangedPaths"]) == 1
    assert host["sourceVsHostChangedPaths"][0].endswith(".externalJob")


def test_all_vscode_roles_have_grounded_classifications(report):
    roles = report["vscodeSemanticWorkspace"]["roles"]
    assert len(roles) == 12
    assert len({r["id"] for r in roles}) == len(roles)
    for key in ("activity-rail", "explorer", "document-tabs", "primary-editor",
                "bottom-panel", "secondary-sidebar", "status-bar", "resizable-panes", "integrated-terminal"):
        assert key in {r["id"] for r in roles}
    assert "Theme/Form/Chrome" in report["vscodeSemanticWorkspace"]["appearanceRule"]


def test_existing_folder_tree_tabs_monaco_and_layout_are_recognized(report):
    witnesses = report["vscodeSemanticWorkspace"]["witnesses"]
    assert all(w["exists"] and all(w["witnesses"].values()) for w in witnesses.values())
    by_id = {r["id"]: r for r in report["vscodeSemanticWorkspace"]["roles"]}
    assert by_id["explorer"]["incumbentEvidence"] == "generated-folder-tree-supported"
    assert by_id["document-tabs"]["incumbentEvidence"] == "generated-document-tabs-present"
    assert by_id["integrated-terminal"]["incumbentEvidence"] != "interactive-terminal-present"


def test_capability_risk_mismatch_remains_explicit(report):
    warning = report["functionalReconciliation"]["authorityWarning"]
    assert "external-read" in warning and "source-write" in warning
    assert report["functionalReconciliation"]["canonicalCapabilityRisks"]["capability:code-editor.source-workspace"] == "external-read"
    save = next(x for x in report["functionalReconciliation"]["bridge"] if x["canonical"]["id"] == "intent:code-editor.save-file")
    assert save["canonical"]["capabilityRisk"] == "external-read"
    assert save["canonical"]["risk"] == "source-write"
    assert save["incumbent"][0]["capabilityRisk"] == "source-write"
    assert {v["name"] for v in save["incumbent"][0]["requestArguments"]} == {"path", "text"}


def test_4a_requires_separate_promotion_and_execution_evidence(report):
    assert report["mode"] == "offline-read-only-no-promotion"
    assert report["promotion"]["eligible"] is False
    assert report["promotion"]["performed"] is False
    assert not any(report["noninterference"].values())
    assert "VS_CODE_WORKSPACE_SEMANTIC_PARITY_UNVERIFIED" in report["promotion"]["blockers"]


def test_read_only_audit_does_not_modify_sources():
    paths = [ROOT / p for p in (
        "mcel_apps/code-editor/application.js",
        "tools/fixtures/mcel_semantic_other_apps/code-editor/application.js",
        "tools/fixtures/mcel_semantic_other_apps/code-editor/application.html",
        "main_computer/mcel_semantic_other_apps_host.py",
        "main_computer/viewport_route_dispatch.py",
    )]
    def hashes(): return [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    before = hashes()
    audit.evaluate_reconciliation(ROOT)
    assert hashes() == before


def test_inventory_drift_blocks_unreviewed_equivalence(monkeypatch):
    incomplete = dict(audit.BRIDGE)
    incomplete.pop("save-file")
    monkeypatch.setattr(audit, "BRIDGE", incomplete)
    with pytest.raises(ValueError, match="Canonical intent inventory drift"):
        audit.evaluate_reconciliation(ROOT)


def test_incumbent_extra_intent_is_never_silently_discarded(monkeypatch):
    incomplete = dict(audit.EXTRA_GROUPS)
    incomplete.pop("proof-regression-and-repair")
    monkeypatch.setattr(audit, "EXTRA_GROUPS", incomplete)
    with pytest.raises(ValueError, match="Unaccounted incumbent intent"):
        audit.evaluate_reconciliation(ROOT)


def test_cli_disallows_unsafe_output_locations():
    from tools.mcel_source_authority_shadow import _report_destination
    for name in ("mcel_apps/code-editor/application.html", "/tmp/output.json", "runtime/state/out.json"):
        with pytest.raises(ValueError):
            _report_destination(ROOT, name)


def test_cli_generates_machine_readable_report(tmp_path, capsys):
    dest = ROOT / "runtime/reports/mcel-code-editor-reconciliation"
    # CLI root path is intentionally output-confined; verify stdout without
    # introducing output files into the source checkout.
    assert cli.main(["--repo", str(ROOT)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == audit.SCHEMA
    assert payload["promotion"]["eligible"] is False
    assert payload["functionalReconciliation"]["coverageComplete"]
