from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
SHELL = ROOT / "main_computer" / "web" / "applications.html"


def test_calculator_runtime_does_not_abort_viewport_when_core_is_temporarily_unavailable() -> None:
    runtime = (SCRIPTS / "calculator.js").read_text(encoding="utf-8")
    shell = SHELL.read_text(encoding="utf-8")

    core_include = "<!-- @include applications/scripts/calculator-core.js -->"
    view_model_include = "<!-- @include applications/scripts/calculator-view-model.js -->"
    runtime_include = "<!-- @include applications/scripts/calculator.js -->"
    assert shell.index(core_include) < shell.index(view_model_include) < shell.index(runtime_include)

    assert "function requireCalculatorCore()" in runtime
    assert "function requireCalculatorViewModel()" in runtime
    assert "const calculatorCore = window.MainComputerCalculatorCore" not in runtime
    assert 'throw new Error("Calculator core must load before Calculator runtime")' not in runtime
    assert "requireCalculatorCore().evaluateCalculatorArithmeticExpression" in runtime
    assert "requireCalculatorViewModel().buildCalculatorGraphRenderModel" in runtime


def test_application_routing_does_not_read_website_builder_lexical_state_before_initialization() -> None:
    routing = (SCRIPTS / "app-routing.js").read_text(encoding="utf-8")
    website_builder = (SCRIPTS / "website-builder.js").read_text(encoding="utf-8")

    assert 'typeof websiteBuilderStateModel !== "undefined"' not in routing
    assert "window.MainComputerWebsiteBuilderRouteState?.selectedSiteId" in routing
    assert "window.MainComputerWebsiteBuilderRouteState = Object.freeze({" in website_builder
    assert "return websiteBuilderStateModel.selectedSiteId;" in website_builder
