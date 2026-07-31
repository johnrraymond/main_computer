from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.mcel_requirements_registry import build_lab_payload, build_registry  # noqa: E402


WEB_APP = ROOT / "main_computer" / "web" / "applications"
MCEL_LAB_HTML = WEB_APP / "apps" / "mcel-lab.html"
MCEL_LAB_JS = WEB_APP / "scripts" / "mcel-lab.js"
MCEL_LAB_CSS = WEB_APP / "styles" / "mcel-lab.css"
MCEL_LAB_DOC = ROOT / "pretty_docs" / "mcel-lab-blueprint-studio.md"
DEPLOYED_FIXTURE = ROOT / "main_computer" / "mcel_lab_deployed_conformance.py"


def test_lab_payload_preserves_exact_form_primitive_provenance() -> None:
    payload = build_lab_payload(build_registry(ROOT))
    primitives = [
        primitive
        for contract in payload["app_contracts"].values()
        for primitive in contract["form_primitives"]
    ]

    assert len(primitives) == 40
    assert all(
        primitive["source"]["file"].startswith("pretty_docs/")
        and primitive["source"]["start_line"] > 0
        and primitive["source"]["end_line"] >= primitive["source"]["start_line"]
        for primitive in primitives
    )

    lab_primitives = payload["app_contracts"]["mcel-lab"]["form_primitives"]
    assert len(lab_primitives) == 9
    assert {
        primitive["primitive"] for primitive in lab_primitives
    } == {
        "subject",
        "action",
        "work-surface",
        "context",
        "feedback",
        "constraint",
        "transient",
        "interruption",
    }
    assert sum(primitive["primitive"] == "context" for primitive in lab_primitives) == 2


def test_static_shell_and_viewer_expose_existing_form_contract_consistently() -> None:
    html = MCEL_LAB_HTML.read_text(encoding="utf-8")
    script = MCEL_LAB_JS.read_text(encoding="utf-8")
    css = MCEL_LAB_CSS.read_text(encoding="utf-8")

    aspect_select = re.search(
        r'<select id="mcel-blueprint-aspect-select".*?</select>',
        html,
        flags=re.DOTALL,
    )
    assert aspect_select is not None
    assert '<option value="form">Form</option>' in aspect_select.group(0)

    assert 'viewer.dataset.mcelFormPrimitiveMode = options.compact ? "compact" : "work-surface"' in script
    assert 'card.dataset.mcelFormPrimitiveSourceFile = source.file' in script
    assert 'card.dataset.mcelFormPrimitiveSourceStart' in script
    assert 'card.dataset.mcelFormPrimitiveSourceEnd' in script
    assert 'mcelBlueprintShellAppendFact(facts, "Contract status"' in script
    assert 'mcelBlueprintShellAppendFact(facts, "Source", source.label)' in script
    assert 'mcelBlueprintShellAppendFact(facts, "Status", primitive?.status' not in script
    assert "No parsed mcel-form-primitive blocks are available for this app yet." in script
    assert "overflow-wrap: anywhere;" in css
    form_surface_rule = re.search(
        r'\.mcel-lab-work-surface\[data-mcel-mount-state="form-primitives"\]\s*\{(?P<body>.*?)\}',
        css,
        flags=re.DOTALL,
    )
    assert form_surface_rule is not None
    form_surface_body = form_surface_rule.group("body")
    assert "align-content: start;" in form_surface_body
    assert "overflow: hidden auto;" in form_surface_body
    assert "overscroll-behavior: contain;" in form_surface_body
    assert "scrollbar-gutter: stable;" in form_surface_body


def test_stale_first_class_form_finding_is_deprecated_and_proof_linked() -> None:
    text = MCEL_LAB_DOC.read_text(encoding="utf-8")
    match = re.search(
        r"```mcel-finding\s*\n"
        r"(?P<body>.*?^id:\s*mcel-lab\.finding\.form-primitives-not-yet-first-class-ui\s*$.*?)"
        r"\n```",
        text,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match is not None
    body = match.group("body")

    assert re.search(r"^status:\s*deprecated\s*$", body, flags=re.MULTILINE)
    assert "Historical gap" in body
    assert "tests/test_mcel_lab_semantic_form_inspection.py" in body
    assert "tests/test_mcel_lab_deployed_conformance.py" in body
    assert "Contract status describes the authored requirement block" in text
    assert "it is not a runtime implementation verdict" in text


def test_deployed_fixture_selects_and_measures_form_aspect_read_only() -> None:
    source = DEPLOYED_FIXTURE.read_text(encoding="utf-8")

    assert 'appSelect.value = "mcel-lab"' in source
    assert 'aspectSelect.value = "form"' in source
    assert 'selectedAspect: aspectSelect ? aspectSelect.value : ""' in source
    assert "EXPECTED_FORM_CARD_COUNT = 9" in source
    assert "EXPECTED_FORM_GROUP_COUNT = 8" in source
    assert '"context": 2' in source
    assert "sourceBoundCardCount" in source
    assert "contractStatusCardCount" in source
    assert "internallyClippedCardIds" in source
    assert "cardOverlaps" in source
    assert "workSurfaceScrollableWhenRequired" in source
