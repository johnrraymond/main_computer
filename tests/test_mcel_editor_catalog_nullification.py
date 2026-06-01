from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"


def _script(relative_path: str) -> str:
    return (SCRIPTS / relative_path).read_text(encoding="utf-8")


def _run_node_json(tmp_path: Path, script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; MCEL catalog smoke test cannot run")

    script_path = tmp_path / "mcel-editor-catalog-smoke.js"
    script_path.write_text(script, encoding="utf-8")

    result = subprocess.run(
        [node, str(script_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_editor_catalog_pins_default_enrichment_and_nullification_vocabulary(tmp_path: Path) -> None:
    script = f"""
const window = {{}};
{_script("mcel-contract.js")}

const catalog = McelLabContract.editorCatalog();
process.stdout.write(JSON.stringify({{
  schemaTypes: catalog.schemaTypes,
  blockTemplates: catalog.blockTemplates,
  slots: catalog.slots,
  nullifyAttribute: catalog.nullifyAttribute,
  regionTags: catalog.defaultEnrichments.regions.map((rule) => rule.tag),
  generatedParts: catalog.defaultEnrichments.generatedParts,
  nullifyGenerated: catalog.nullifiableEnrichments.generated,
  nullifyAction: catalog.nullifiableEnrichments.action,
  nullifyRegion: catalog.nullifiableEnrichments.region
}}));
"""
    data = _run_node_json(tmp_path, script)

    assert data["schemaTypes"] == [
        "panel",
        "feed",
        "command-row",
        "proof-surface",
        "smart-region",
    ]
    assert data["blockTemplates"] == [
        "panel",
        "hero",
        "signal",
        "work",
        "feed",
        "command-row",
        "proof",
        "smart-region",
        "component",
        "form",
        "route",
    ]
    assert data["slots"] == ["title", "body", "actions", "media", "meta", "fallback"]
    assert data["nullifyAttribute"] == "mcel-nullify"
    assert data["regionTags"] == ["main", "section", "article", "form", "nav", "aside"]
    assert data["generatedParts"] == ["rail", "copy", "meta", "field"]
    assert data["nullifyGenerated"] == ["rail", "copy", "meta", "field"]
    assert data["nullifyAction"] == ["data-mc-action", "data-mc-event-policy"]
    assert "data-mc" in data["nullifyRegion"]


def test_editor_enrichment_merge_order_is_default_then_user_then_nullify() -> None:
    editor = _script("mcel-editor.js")

    assert "const nullifyAttribute = attributes.nullify || \"mcel-nullify\";" in editor
    assert "function defaultsForElement(element)" in editor
    assert "function authoredSourceAttributes(element)" in editor
    assert "function applyNullification(element, merged)" in editor

    merge_match = re.search(
        r"function\s+mergeDefaultEnrichment\s*\([^)]*\)\s*\{(?P<body>.*?)\n      \}",
        editor,
        re.S,
    )
    assert merge_match, "missing mergeDefaultEnrichment"
    body = merge_match.group("body")

    default_index = body.index("defaultsForElement(element)")
    authored_index = body.index("authoredSourceAttributes(element)")
    nullify_index = body.index("applyNullification(element, merged)")
    set_index = body.index("element.setAttribute(attribute, normalized)")

    assert default_index < authored_index < nullify_index < set_index
    assert "delete merged[attribute]" in editor
    assert "element.removeAttribute(attribute)" in editor


def test_engine_preserves_nullify_marker_while_skipping_nullified_runtime_enrichments() -> None:
    contract = _script("mcel-contract.js")
    engine = _script("mcel-engine.js")

    assert 'nullify: "mcel-nullify"' in contract
    assert "function nullifiesAttribute(element, attribute)" in engine
    assert "function nullifiesGeneratedPart(element, part)" in engine
    assert "function generatedPartsFor(element, elementSchema)" in engine
    assert "applyNullificationToTree(doc.body)" in engine

    source_elements = re.search(
        r"function\s+sourceElements\s*\([^)]*\)\s*\{(?P<body>.*?)\n      \}",
        engine,
        re.S,
    )
    assert source_elements, "missing sourceElements"
    assert "!nullifiesAttribute(element, attributes.type)" in source_elements.group("body")

    generated_loop_count = engine.count("generatedPartsFor(element, elementSchema).slice().reverse().forEach")
    assert generated_loop_count >= 2

    runtime_owned_match = re.search(
        r"const\s+runtimeOwnedAttributes\s*=\s*Object\.freeze\(\[(?P<body>.*?)\]\);",
        contract,
        re.S,
    )
    assert runtime_owned_match, "missing runtimeOwnedAttributes"
    assert "attributes.nullify" not in runtime_owned_match.group("body")


def test_default_lab_source_is_minimal_source_contract_not_machine_policy_markup() -> None:
    contract = _script("mcel-contract.js")
    default_match = re.search(r"const\s+defaultSource\s*=\s*`(?P<html>.*?)`;", contract, re.S)
    assert default_match, "missing defaultSource"
    default_source = default_match.group("html")

    assert '<main data-mc="smart-region">' in default_source
    assert '<section data-mc="panel" data-mc-kind="hero">' in default_source
    assert '<p data-mc-slot="actions"><a href="#join" data-mc-action="join-neighborhood" data-mc-event-policy="audited">Join the list</a></p>' in default_source
    assert '<form id="join" data-mc="smart-region" data-mc-submit="lead.create" data-mc-validation="native">' in default_source
    assert '<button type="submit" data-mc-action="signup" data-mc-event-policy="audited">Notify me</button>' in default_source

    machine_policy_attributes = [
        "data-mc-flow",
        "data-mc-rank",
        "data-mc-state",
        "data-mc-density",
        "data-mc-size-policy",
        "data-mc-overflow-policy",
        "data-mc-scroll-policy",
        "data-mc-words",
        "data-mc-connects",
        "data-mc-component",
        "data-mc-component-kind",
        "data-mc-state-owner",
        "data-mc-state-policy",
        "data-mc-render",
        "data-mc-hydration",
        "data-mc-a11y-policy",
        "data-mc-performance-budget",
        "data-mc-security-policy",
    ]
    for attribute in machine_policy_attributes:
        assert attribute not in default_source

    assert "mcel-nullify" not in default_source
    assert "NeighborhoodMarketSite" not in default_source
    assert "HeroSection" not in default_source
    assert "TrustCluster" not in default_source
    assert "SignupForm" not in default_source
    assert "FooterCta" not in default_source


def test_lab_compile_uses_enriched_working_source_without_rewriting_human_seed_pane() -> None:
    ui = _script("mcel-lab.js")
    compile_match = re.search(
        r"function\s+compileMcelLabSource\s*\([^)]*\)\s*\{(?P<body>.*?)\n    \}",
        ui,
        re.S,
    )
    assert compile_match, "missing compileMcelLabSource"
    body = compile_match.group("body")

    assert "const cleanSource = currentMcelSource();" in body
    assert "MCEL.compile(cleanSource" in body
    assert "McelLabEditor.sourceList(cleanSource)" in body
    assert "mcelSourceHtml.value = cleanSource" not in body
