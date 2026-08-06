from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTHORING_SURFACE_DOC = ROOT / "pretty_docs" / "mcel-dsl-app-authoring-surface.md"
AUTHORING_DOC = ROOT / "pretty_docs" / "mcel-application-authoring.md"
SOURCE_TREE_DOC = ROOT / "pretty_docs" / "mcel-source-tree-dematerialization.md"
README = ROOT / "README.md"
INDEX = ROOT / "pretty_docs" / "index.json"
CALCULATOR_REQUIREMENTS = ROOT / "pretty_docs" / "mcel-calculator-requirements.md"
CALCULATOR_PACKAGE_REQUIREMENTS = ROOT / "mcel_apps" / "calculator" / "requirements.md"
CALCULATOR_SURFACE_DOC = ROOT / "pretty_docs" / "mcel-calculator-surface.md"
CALCULATOR_ADAPTER_DOC = ROOT / "pretty_docs" / "mcel-calculator-semantic-adapter.md"


def test_generic_mcel_dsl_authoring_surface_is_indexed_and_linked() -> None:
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    entry = next(
        (item for item in index.get("documents", []) if item.get("path") == "mcel-dsl-app-authoring-surface.md"),
        None,
    )

    assert entry == {
        "path": "mcel-dsl-app-authoring-surface.md",
        "title": "MCEL DSL App Authoring Surface",
        "kind": "markdown",
        "order": 38,
    }

    canonical_path = "pretty_docs/mcel-dsl-app-authoring-surface.md"
    assert canonical_path in README.read_text(encoding="utf-8")
    assert canonical_path in AUTHORING_DOC.read_text(encoding="utf-8")
    assert canonical_path in SOURCE_TREE_DOC.read_text(encoding="utf-8")


def test_generic_mcel_dsl_authoring_surface_captures_current_authoring_contract() -> None:
    text = AUTHORING_SURFACE_DOC.read_text(encoding="utf-8")

    required_phrases = [
        "mcel_apps/<app-id>/application.js",
        "mcel_apps/<app-id>/mcel.app.json",
        "mcel_apps/<app-id>/blueprint.json",
        "mcel_apps/<app-id>/requirements.md",
        "Derived files are not source",
        "Presentation modes",
        "`package-document`",
        "`host-bound`",
        "presentationAuthority: existing-host-html",
        "runtimeFacade: SomeStableRuntimeFacade",
        "Calculator is the reference host-bound application",
        "Compilation is not promotion",
        "normal viewport mounting does not create source-tree files",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_generic_authoring_surface_names_generated_artifacts_as_non_source() -> None:
    text = AUTHORING_SURFACE_DOC.read_text(encoding="utf-8")

    for artifact in (
        "contracts/domain.js",
        "contracts/intents.js",
        "contracts/adapter.js",
        "contracts/surface.js",
        "contracts/layout.js",
        "contracts/observation.js",
        "contracts/acceptance.js",
        "generated/mcel.application.normalized.json",
        "mcel.runtime.json",
    ):
        assert artifact in text

    assert "A host-bound application must not copy the existing HTML/CSS into `mcel_apps/<app-id>/src/`." in text
    assert "They are not durable app source and must not be checked in beside `application.js`." in text


def test_calculator_docs_describe_current_host_bound_dsl_authority() -> None:
    current_docs = {
        CALCULATOR_REQUIREMENTS: CALCULATOR_REQUIREMENTS.read_text(encoding="utf-8"),
        CALCULATOR_PACKAGE_REQUIREMENTS: CALCULATOR_PACKAGE_REQUIREMENTS.read_text(encoding="utf-8"),
        CALCULATOR_SURFACE_DOC: CALCULATOR_SURFACE_DOC.read_text(encoding="utf-8"),
        CALCULATOR_ADAPTER_DOC: CALCULATOR_ADAPTER_DOC.read_text(encoding="utf-8"),
    }

    combined = "\n".join(current_docs.values())
    for phrase in (
        "dsl-authoritative",
        "host-bound",
        "/applications/calculator",
        "#calculator-app",
        "MainComputerCalculatorRuntime",
        "legacy",
        "retired",
    ):
        assert phrase in combined

    stale_current_state_phrases = (
        "unpromoted, authored-only shadow authority",
        "host-bound-shadow-runtime",
        "status: shadow-specified",
        "handwritten semantic adapter remains live",
        "browser observation, promotion rehearsal, and handwritten semantic adapter retirement remain open",
        "current semantic authority is the handwritten",
    )
    for phrase in stale_current_state_phrases:
        for path, text in current_docs.items():
            assert phrase not in text, f"{path.relative_to(ROOT)} still contains stale current-state phrase: {phrase}"


def test_source_tree_dematerialization_names_calculator_as_promoted_host_bound_app() -> None:
    text = SOURCE_TREE_DOC.read_text(encoding="utf-8")

    assert "contract-counter" in text
    assert "contract-workbench" in text
    assert "calculator" in text
    assert "`calculator` is a host-bound app" in text
    assert "Current de-materialized app boundary" in text
    assert "contract-counter\ncontract-workbench\ncalculator" in text
    assert "no generated contract tree under any promoted app package" in text

