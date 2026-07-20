from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "pretty_docs" / "mcel-lab-blueprint-studio.md"
PRETTY_DOCS_INDEX = ROOT / "pretty_docs" / "index.json"
README = ROOT / "README.md"


def test_mcel_lab_blueprint_studio_doc_is_registered() -> None:
    index = json.loads(PRETTY_DOCS_INDEX.read_text(encoding="utf-8"))
    documents = index.get("documents", [])
    entry = next(
        (item for item in documents if item.get("path") == "mcel-lab-blueprint-studio.md"),
        None,
    )

    assert entry is not None
    assert entry["title"] == "MCEL Lab Blueprint Studio"
    assert entry["kind"] == "markdown"
    assert isinstance(entry["order"], int)


def test_readme_points_to_mcel_lab_blueprint_studio_doc() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "pretty_docs/mcel-lab-blueprint-studio.md" in readme
    assert "self-hosting app-aspect inspector" in readme
    assert "good-looking solid apps" in readme
    assert "refactor annotations" in readme


def test_blueprint_studio_doc_defines_user_requirements_and_operations() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_phrases = [
        "Generate good-looking apps that are solid.",
        "Select and load an app",
        "Inspect every aspect of an app",
        "Edit the blueprint safely",
        "Preview the generated workbench",
        "Run acid tests",
        "Review findings and repair plans",
        "Generate patch artifacts safely",
        "replacement-file patch zip",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_blueprint_studio_doc_requires_generic_mcel_elements() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_phrases = [
        "must use generic MCEL elements",
        "element.core.app",
        "element.layout.navigation-zone",
        "element.layout.primary-work-zone",
        "element.layout.evidence-zone",
        "element.proof.specimen-model",
        "New reusable inspection elements",
        "element.inspection.aspect-map",
        "element.inspection.source-binding",
        "element.inspection.acid-test-result",
        "element.inspection.repair-finding",
        "not MCEL-Lab-only concepts",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_blueprint_studio_doc_requires_aspect_inspection_and_self_hosting() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_phrases = [
        "Product identity",
        "Object model",
        "Workflow map",
        "Layout binding",
        "Action and risk policy",
        "Capability projection",
        "Evidence model",
        "Source binding",
        "Self-hosting requirement",
        "selectedApp: mcel-lab",
        "MCEL Lab may edit its own blueprint draft.",
        "MCEL Lab must not directly rewrite or apply its own live implementation.",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_blueprint_studio_doc_defines_document_and_lab_targets() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_phrases = [
        "The first useful redesign should support two target blueprints",
        "Document Editor",
        "MCEL Lab",
        "dominant object: Document",
        "dominant object: AppBlueprint",
        "primary page is protected before side lanes are preserved",
        "select app -> inspect aspect -> preview -> acid test -> repair plan",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_blueprint_studio_doc_defines_acid_tests_and_acceptance_criteria() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_phrases = [
        "MCEL blueprint",
        "generic elements",
        "DOM layout binding",
        "CSS/geometry policy",
        "JS behavior policy",
        "source bindings",
        "repair plan",
        "The dominant object is visible.",
        "The primary work surface is visually protected.",
        "Debug/spec internals stay inside MCEL Lab or advanced drawers.",
        "No Lab-only hardcoded element is used where a generic MCEL element exists.",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_blueprint_studio_doc_defines_mount_annotate_and_refactor_export() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_phrases = [
        "Mount and annotate rendered elements",
        "mount app -> point at element -> capture evidence -> annotate intent -> save annotation -> export AI refactor context",
        "This element needs to be removed or reworked because it is not doing anything.",
        "investigation-backed refactor candidates, not blind deletion requests",
        "selector",
        "bounding box",
        "event-handler/source hints",
        "CSS ownership hints",
        "test ownership hints",
        "Export AI refactor context",
        "refactor export packet",
        "refactor-brief.md",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_blueprint_studio_doc_defines_reusable_refactor_annotation_elements() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_phrases = [
        "element.refactor.annotation-map",
        "element.refactor.element-annotation",
        "element.refactor.removal-candidate",
        "element.refactor.rework-candidate",
        "element.refactor.refactor-export-packet",
        "source references",
        "event handlers",
        "CSS selectors",
        "feature flags",
        "replacement path",
        "allowed fixes",
        "forbidden fixes",
        "tests should be added or updated",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_blueprint_studio_doc_requires_refactor_annotations_as_app_aspect() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_phrases = [
        "Refactor annotations",
        "removal, rework, move, hide, merge, and investigate candidates",
        "The Lab must distinguish user intent from verified implementation facts.",
        "Current zone: toolbar.",
        "Expected zone: advanced/dev-only.",
        "remove if unused",
        "move to Advanced if still useful",
        "leave in primary toolbar",
    ]

    for phrase in required_phrases:
        assert phrase in text

def test_blueprint_studio_doc_registers_mcel_lab_semantic_app_form_contract() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_phrases = [
        "```mcel-app",
        "id: mcel-lab",
        "```mcel-form-primitive",
        "mcel-lab.form.subject.app-blueprint",
        "mcel-lab.form.work-surface.blueprint-inspection",
        "mcel-lab.form.feedback.validation-and-mount-state",
        "mcel-lab.form.constraint.self-hosting-safety",
        "mcel-lab.form.transient.point-inspection",
        "mcel-lab.use-case.inspect-blueprint-from-doc-contract",
        "mcel-lab.contract.default.blueprint-studio-health",
    ]

    for phrase in required_phrases:
        assert phrase in text

