from __future__ import annotations

import re
from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_projection_profiles.calculator_shadow_v1 import project_calculator_ir


REPO = Path(__file__).resolve().parents[3]


def test_calculator_shadow_surface_preserves_existing_html_authority() -> None:
    compiled = compile_dsl_application(REPO / "mcel_apps/calculator/application.js", write_candidate=False)
    assert compiled.valid and compiled.normalized_ir
    surface = compiled.normalized_ir["surfaces"][0]
    assert surface["route"] == "/applications/calculator"
    assert surface["root"] == "#calculator-app"
    assert surface["presentationAuthority"] == "existing-host-html"
    assert len(surface["nodes"]) == 11

    projected = project_calculator_ir(compiled.normalized_ir)
    surface_module = projected.files["contracts/surface.js"].decode("utf-8")
    assert '"rootSelector": "#calculator-app"' in surface_module
    assert '"presentationAuthority": "existing-host-html"' in surface_module


def _selector_part_exists(selector: str, html: str) -> bool:
    selector = selector.strip()
    if not selector:
        return False
    if selector.startswith("#") and re.fullmatch(r"#[A-Za-z0-9_.:-]+", selector):
        return f'id="{selector[1:]}"' in html or f"id='{selector[1:]}'" in html
    if selector.startswith(".") and re.fullmatch(r"\.[A-Za-z0-9_-]+", selector):
        class_name = re.escape(selector[1:])
        return re.search(r'class=(?:"[^"]*\b' + class_name + r'\b[^"]*"|\'[^\']*\b' + class_name + r'\b[^\']*\')', html) is not None
    attr_match = re.fullmatch(r"\[([A-Za-z0-9_-]+)(?:=(?:'([^']*)'|\"([^\"]*)\"))?\]", selector)
    if attr_match:
        attr = attr_match.group(1)
        value = attr_match.group(2) if attr_match.group(2) is not None else attr_match.group(3)
        if value is None:
            return attr in html
        return f'{attr}="{value}"' in html or f"{attr}='{value}'" in html
    return selector in html


def _selector_exists(selector: str, html: str) -> bool:
    return any(_selector_part_exists(part, html) for part in selector.split(","))


def _calculator_normalized_ir() -> dict:
    compiled = compile_dsl_application(REPO / "mcel_apps/calculator/application.js", write_candidate=False)
    assert compiled.valid and compiled.normalized_ir
    return compiled.normalized_ir


def test_calculator_static_semantic_surface_is_declared_in_dsl_source() -> None:
    html = (REPO / "main_computer/web/applications/apps/calculator.html").read_text(encoding="utf-8")
    ir = _calculator_normalized_ir()

    surface = next(item for item in ir["surfaces"] if item["id"] == "surface:calculator.workspace")
    semantic = surface["semanticSurface"]

    assert semantic["id"] == "calculator.semantic-surface.semantic-runtime-workspace"
    assert semantic["surfaceId"] == "calculator.surface.workspace"
    assert semantic["presentationAuthority"] == "existing-host-html"
    assert semantic["runtimeFacade"] == "MainComputerCalculatorRuntime"

    region_ids = [region["id"] for region in semantic["regions"]]
    assert len(region_ids) == len(set(region_ids))
    assert {
        "root",
        "shell",
        "mode-switch",
        "workspace",
        "arithmetic",
        "graphing",
        "mathics",
        "result-qa",
        "chat",
    } <= set(region_ids)

    primary = next(region for region in semantic["regions"] if region["id"] == "workspace")
    assert primary["primary"] is True
    assert primary["selector"] == ".calculator-workspace"
    assert primary["surface"] == "workspace"

    for region in semantic["regions"]:
        assert _selector_exists(region["selector"], html), region

    intent_names = {item["sourceName"] for item in ir["intents"]}
    control_ids = [control["id"] for control in semantic["controls"]]
    assert len(control_ids) == len(set(control_ids))
    assert {
        "mode-basic",
        "mode-graphing",
        "enter-token",
        "clear-expression",
        "evaluate-expression",
        "ask-model-for-expression",
        "ask-model-for-graph-expression",
        "draw-graph",
        "reset-graph",
        "ask-model-for-mathics-expression",
        "evaluate-mathics",
        "ask-result-question",
    } <= set(control_ids)
    for control in semantic["controls"]:
        assert control["intent"] in intent_names
        assert _selector_exists(control["selector"], html), control

    assert semantic["forbiddenDefaultRegions"] == []


def test_calculator_static_layout_grammar_is_declared_in_dsl_source() -> None:
    html = (REPO / "main_computer/web/applications/apps/calculator.html").read_text(encoding="utf-8")
    ir = _calculator_normalized_ir()

    layout = next(item for item in ir["layouts"] if item["id"] == "layout:calculator.workspace")
    grammar = layout["layoutGrammar"]

    assert grammar["id"] == "calculator.layout.semantic-runtime-workspace"
    assert grammar["rootSelector"] == ".calculator-shell"
    assert grammar["presentationAuthority"] == "existing-host-html"

    region_ids = [region["id"] for region in grammar["regions"]]
    assert len(region_ids) == len(set(region_ids))
    assert {
        "shell",
        "mode-switch",
        "workspace",
        "arithmetic",
        "graphing",
        "mathics",
        "result-qa",
        "chat",
    } <= set(region_ids)
    assert next(region for region in grammar["regions"] if region["id"] == "shell")["children"] == [
        "mode-switch",
        "workspace",
    ]
    assert next(region for region in grammar["regions"] if region["id"] == "workspace")["children"] == [
        "arithmetic",
        "graphing",
        "mathics",
        "chat",
    ]
    assert next(region for region in grammar["regions"] if region["id"] == "arithmetic")["children"] == [
        "result-qa",
    ]

    for region in grammar["regions"]:
        assert _selector_exists(region["selector"], html), region

    constraints = {item["id"]: item for item in grammar["constraints"]}
    assert {
        "workspace-primary-nonzero",
        "mode-switch-visible",
        "arithmetic-lane-nonzero",
        "graphing-lane-nonzero",
        "mathics-lane-nonzero",
        "chat-companion-visible",
    } <= set(constraints)
    assert constraints["workspace-primary-nonzero"]["minWidth"] == 420
    assert constraints["workspace-primary-nonzero"]["minHeight"] == 320
    assert constraints["chat-companion-visible"]["minHeight"] == 180

    for constraint in constraints.values():
        assert _selector_exists(constraint["selector"], html), constraint
