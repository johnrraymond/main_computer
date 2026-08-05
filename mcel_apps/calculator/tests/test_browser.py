from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[3]


def test_calculator_core_loads_before_runtime_in_viewport_source() -> None:
    shell = (REPO / "main_computer/web/applications.html").read_text(encoding="utf-8")
    core = "<!-- @include applications/scripts/calculator-core.js -->"
    runtime = "<!-- @include applications/scripts/calculator.js -->"
    assert core in shell
    assert runtime in shell
    assert shell.index(core) < shell.index(runtime)


def test_calculator_host_document_and_runtime_facade_remain_stable() -> None:
    html = (REPO / "main_computer/web/applications/apps/calculator.html").read_text(encoding="utf-8")
    runtime = (REPO / "main_computer/web/applications/scripts/calculator.js").read_text(encoding="utf-8")
    assert 'id="calculator-app"' in html
    assert "window.MainComputerCalculatorRuntime" in runtime
