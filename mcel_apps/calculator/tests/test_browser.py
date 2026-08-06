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


def test_calculator_host_bound_runtime_loads_after_virtual_catalog() -> None:
    shell = (REPO / "main_computer/web/applications.html").read_text(encoding="utf-8")
    catalog = "<!-- @include applications/scripts/mcel-application-package-catalog.js -->"
    host_runtime = "<!-- @include applications/scripts/mcel-host-bound-application-runtime.js -->"
    assert catalog in shell
    assert host_runtime in shell
    assert shell.index(catalog) < shell.index(host_runtime)
