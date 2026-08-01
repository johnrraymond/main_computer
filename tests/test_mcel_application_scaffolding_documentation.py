from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCAFFOLDING_DOC = ROOT / "pretty_docs" / "mcel-application-scaffolding.md"
AUTHORING_DOC = ROOT / "pretty_docs" / "mcel-application-authoring.md"
SYSTEM_GUIDE = ROOT / "pretty_docs" / "mcel-system-guide.md"
STATUS_DOC = ROOT / "pretty_docs" / "mcel-status-and-roadmap.md"
PRETTY_DOCS_INDEX = ROOT / "pretty_docs" / "index.json"
TODO = ROOT / "TODO.md"


def test_mcel_application_scaffolding_document_is_indexed() -> None:
    index = json.loads(PRETTY_DOCS_INDEX.read_text(encoding="utf-8"))
    entries = [
        item
        for item in index.get("documents", [])
        if item.get("path") == "mcel-application-scaffolding.md"
    ]

    assert entries == [
        {
            "path": "mcel-application-scaffolding.md",
            "title": "MCEL Application Scaffolding",
            "kind": "markdown",
            "order": 66,
        }
    ]


def test_mcel_application_scaffolding_document_defines_the_complete_program() -> None:
    text = SCAFFOLDING_DOC.read_text(encoding="utf-8")

    required_phrases = [
        "mcel.canonical-application-template.v1",
        "tools/mcel_create_app.py",
        "tests/fixtures/mcel_application_template_v1/",
        "contract-counter",
        "mcel.application-package.v1",
        "Current-capability mode",
        "MCEL 1.0 target mode",
        "Adapter-to-SCM application runtime",
        "Generic semantic surface projection",
        "Package-local acceptance discovery",
        "Operation-linked browser observation",
        "App-oriented proof orchestration",
        "mcel app create contract-counter --prove",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_scaffolding_status_remains_truthful_and_non_authorizing() -> None:
    scaffolding = SCAFFOLDING_DOC.read_text(encoding="utf-8")
    status = STATUS_DOC.read_text(encoding="utf-8")

    assert "It is the implementation contract for the complete feature." in scaffolding
    assert "Development waves" in scaffolding
    assert "They do not authorize code work" in scaffolding

    assert "## Specified application-scaffolding program" in status
    assert "generator core: implemented" in status
    assert "structural package validator: implemented" in status
    assert "golden fixture: implemented" in status
    assert "repository package discovery: implemented" in status
    assert "browser-safe package catalog: implemented" in status
    assert "adapter-to-SCM application bridge: implemented" in status
    assert "browser-safe package loading and semantic projection: implemented" in status
    assert "package-local acceptance discovery: implemented" in status
    assert "checked-in browser-mountable reference application: implemented" in status
    assert "app-oriented proof command: implemented" in status
    assert "No later scaffolding code wave is authorized by this status entry" in status


def test_scaffolding_document_is_linked_from_current_authorities() -> None:
    canonical_path = "pretty_docs/mcel-application-scaffolding.md"

    assert canonical_path in AUTHORING_DOC.read_text(encoding="utf-8")
    assert canonical_path in SYSTEM_GUIDE.read_text(encoding="utf-8")
    assert canonical_path in STATUS_DOC.read_text(encoding="utf-8")
    assert canonical_path in TODO.read_text(encoding="utf-8")


def test_scaffolding_wave7_live_boundary_is_documented_with_independent_truth_promotion() -> None:
    scaffolding = SCAFFOLDING_DOC.read_text(encoding="utf-8")
    authoring = AUTHORING_DOC.read_text(encoding="utf-8")
    system = SYSTEM_GUIDE.read_text(encoding="utf-8")

    assert "## Current implementation checkpoint" in scaffolding
    assert "tools/mcel_create_app.py                 live" in scaffolding
    assert "repository package discovery             live" in scaffolding
    assert "app-oriented proof orchestration         implemented" in scaffolding
    assert "browser-safe package catalog             live" in scaffolding
    assert "adapter-to-SCM application runtime       live" in scaffolding
    assert "package-local acceptance discovery       live" in scaffolding
    assert "operation-linked browser observation     live" in scaffolding
    assert "mcel_apps/" in scaffolding
    assert "semantic-runtime-proven" in scaffolding

    assert "deterministic canonical scaffold generator" in authoring
    assert "generic package mounting with semantic state/control projection" in authoring
    assert "app-oriented proof orchestration are live" in system


def test_scaffolding_wave7_contract_names_live_observation_and_proof() -> None:
    scaffolding = SCAFFOLDING_DOC.read_text(encoding="utf-8")
    status = STATUS_DOC.read_text(encoding="utf-8")

    assert "### Wave 4: Adapter-to-SCM application runtime — implemented" in scaffolding
    assert "mcel-application-runtime.js" in scaffolding
    assert "app.dispatch({" in scaffolding
    assert "### Wave 5: Generic semantic surface projection — implemented" in scaffolding
    assert "mcel_application_runtime_projection.py" in scaffolding
    assert "MCEL.mountApplicationPackage()" in scaffolding
    assert "### Wave 6A: Package-local acceptance discovery" in scaffolding
    assert "mcel.package-acceptance-bindings.v1" in scaffolding
    assert "mcel_acceptance_runner.py --app contract-counter --check" in scaffolding
    assert "Wave 5A is complete." in status
    assert "Wave 6A is complete." in status
    assert "### Wave 6B: Operation-linked browser observation — implemented" in scaffolding
    assert "mcel_application_observation_runner.py --app contract-counter --check" in scaffolding
    assert "Wave 6B is complete." in status
    assert "### Wave 7: App-oriented proof orchestration — implemented" in scaffolding
    assert "mcel_app_prove.py --app contract-counter --check" in scaffolding
    assert "Wave 7 is complete." in status
    assert "canonical `semantic-runtime-proven` template fixture" in status
