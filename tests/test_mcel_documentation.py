from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MCEL_DOC = ROOT / "pretty_docs" / "mcel-system-guide.md"
AUTHORING_DOC = ROOT / "pretty_docs" / "mcel-application-authoring.md"
CODE_STUDIO_DOC = ROOT / "pretty_docs" / "mcel-code-studio-example.md"
PRETTY_DOCS_INDEX = ROOT / "pretty_docs" / "index.json"
README = ROOT / "README.md"


def test_mcel_system_guide_is_registered_in_pretty_docs() -> None:
    index = json.loads(PRETTY_DOCS_INDEX.read_text(encoding="utf-8"))
    documents = index.get("documents", [])
    entry = next((item for item in documents if item.get("path") == "mcel-system-guide.md"), None)

    assert entry is not None
    assert entry["title"] == "MCEL System Guide"
    assert entry["kind"] == "markdown"
    assert isinstance(entry["order"], int)


def test_mcel_system_guide_documents_the_required_boundaries() -> None:
    text = MCEL_DOC.read_text(encoding="utf-8")

    required_phrases = [
        "source meaning",
        "runtime machinery",
        "serialization",
        "serialization firewall",
        "subsumption lattice",
        "adoption case",
        "evidence packets",
        "proof obligations",
        "MCEL.buildAdoptionCase(options)",
        "python tools/build_mcel_runtime.py",
        "tests/test_mcel_runtime_package.py",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_mcel_system_guide_documents_each_law_domain() -> None:
    text = MCEL_DOC.read_text(encoding="utf-8")

    law_files = [
        "mcel-component-law.js",
        "mcel-state-law.js",
        "mcel-data-law.js",
        "mcel-form-law.js",
        "mcel-action-law.js",
        "mcel-render-law.js",
        "mcel-a11y-law.js",
        "mcel-performance-law.js",
        "mcel-layout-law.js",
        "mcel-style-law.js",
        "mcel-chrome-law.js",
    ]

    for law_file in law_files:
        assert law_file in text


def test_readme_points_to_mcel_system_guide() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "## MCEL documentation" in readme
    assert "pretty_docs/mcel-system-guide.md" in readme
    assert "adoption-case gate" in readme

def test_mcel_application_authoring_guide_is_registered_and_current() -> None:
    index = json.loads(PRETTY_DOCS_INDEX.read_text(encoding="utf-8"))
    documents = index.get("documents", [])
    entry = next(
        (item for item in documents if item.get("path") == "mcel-application-authoring.md"),
        None,
    )
    assert entry is not None
    assert entry["title"] == "MCEL Application Authoring"
    assert entry["kind"] == "markdown"

    text = AUTHORING_DOC.read_text(encoding="utf-8")
    required_phrases = [
        "MainComputerCodeEditorLayout",
        "MainComputerGitToolsLayout",
        "data-mc-layout-*",
        "data-mcel-layout-*",
        "owned center slot",
        "One scroll owner per recursive unit",
        "Adapt to owned capacity, not only the viewport",
        "Preserve live behavior while changing layout",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_code_studio_example_documents_the_live_aider_workbench() -> None:
    text = CODE_STUDIO_DOC.read_text(encoding="utf-8")
    required_phrases = [
        "Aider Control Surface",
        "Aider repository file map",
        "Evidence and History",
        "mcel-owned-track-containment.v1",
        "code-editor-layout-contract.js",
        "MainComputerCodeEditorLayout",
        "Raw `left`, `top`, `width`, and `height` coordinates are rejected",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_readme_points_to_application_authoring_docs() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "pretty_docs/mcel-application-authoring.md" in readme
    assert "pretty_docs/mcel-code-studio-example.md" in readme

