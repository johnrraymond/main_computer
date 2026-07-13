from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "pretty_docs" / "mcel-lab-blueprint-studio.md"
INDEX = ROOT / "pretty_docs" / "index.json"
README = ROOT / "README.md"


def test_mcel_lab_blueprint_studio_doc_is_registered() -> None:
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    entry = next(
        (
            item
            for item in index.get("documents", [])
            if item.get("path") == "mcel-lab-blueprint-studio.md"
        ),
        None,
    )

    assert entry is not None
    assert entry["title"] == "MCEL Lab Blueprint Studio Requirements"
    assert entry["kind"] == "markdown"
    assert isinstance(entry["order"], int)


def test_mcel_lab_blueprint_studio_doc_defines_user_requirements_operations_and_app_spec() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_phrases = [
        "MCEL Blueprint Studio",
        "primary goal is to generate good-looking apps that are solid",
        "Product purpose",
        "Dominant object",
        "App Blueprint",
        "Primary user operations",
        "Required app layout",
        "MCEL data model",
        "MCEL contract rules",
        "Required acid tests for the redesigned lab",
        "Operations manual",
        "First implementation target",
        "Acceptance criteria for the first implementation",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_mcel_lab_blueprint_studio_doc_keeps_mcel_visible_only_in_the_lab() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_phrases = [
        "Product apps such as Document Editor, Git Tools, Code Editor, Wallet, Spreadsheet, and Website Builder should not display MCEL debug cards or raw contract prose.",
        "Raw MCEL contracts, acid-test internals, and debug scaffolding are allowed in MCEL Lab.",
        "They must not appear in ordinary product app primary flows.",
        "No MCEL cards or contract prose are added to ordinary product apps.",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_mcel_lab_blueprint_studio_doc_links_blueprints_to_layout_quality_and_acid_tests() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_phrases = [
        "dominant object",
        "layout zones",
        "capabilities consumed",
        "action risk",
        "evidence model",
        "geometry policy",
        "responsive policy",
        "repair findings",
        "The primary work surface must be protected before side lanes are preserved.",
        "Every risky, generated, or state-changing action must have a visible evidence location.",
        "center: generated workbench preview",
        "right: acid-test findings and repair plan",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_readme_points_to_mcel_lab_blueprint_studio_doc() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "pretty_docs/mcel-lab-blueprint-studio.md" in readme
    assert "workflow for generating good-looking solid apps" in readme
