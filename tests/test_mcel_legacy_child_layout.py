"""P5 MCEL shell: all incumbent apps must remain visible in the native child.

This independently tests the exact child chrome stylesheet without requiring
an AI service, a Hub, a game bundle, or a running Main Computer server.
"""
from __future__ import annotations

import ast
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROUTE = ROOT / "main_computer" / "viewport_route_dispatch.py"
BASE = ROOT / "main_computer" / "web" / "applications" / "styles" / "base.css"
RESPONSIVE = ROOT / "main_computer" / "web" / "applications" / "styles" / "status-and-responsive.css"


def child_markup(kind: str) -> str:
    """Exercise production _legacy_app_child_html, with a minimal app shell."""
    tree = ast.parse(ROUTE.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_legacy_app_child_html")
    source = ast.Module(body=[function], type_ignores=[])
    ns = {"APPLICATIONS_INDEX_HTML": (
        '<!doctype html><html><head><style>'
        + BASE.read_text(encoding="utf-8")
        + RESPONSIVE.read_text(encoding="utf-8")
        + '</style></head><body data-has-app-taskbar="true">'
        '<div class="viewport"><header>Existing application header</header>'
        '<main><aside class="launcher">Legacy launcher</aside>'
        '<section class="panel stage"><div class="stage-head">'
        '<h2>Existing application header</h2></div>'
        f'<div class="canvas-wrap"><div id="{kind}-app">{kind} controls</div></div>'
        '</section><aside class="panel status">Status</aside>'
        '</main></div></body></html>'
    )}
    exec(compile(source, str(ROUTE), "exec"), ns)
    return ns["_legacy_app_child_html"]()


def test_native_child_chrome_is_not_a_two_row_grid():
    html = child_markup("worker")
    assert 'id="mcel-native-child-chrome"' in html
    child_style = re.search(r'<style id="mcel-native-child-chrome">(.*?)</style>', html, re.S)
    assert child_style
    assert "grid-template-rows:0 minmax(0,1fr)" not in child_style.group(1)
    assert ".viewport>header,.launcher{display:none!important;}" in child_style.group(1)
    assert "grid-template-rows:minmax(0,1fr)!important" in child_style.group(1)


@pytest.mark.parametrize("app", ["worker", "terminal", "wallet", "spreadsheet"])
@pytest.mark.parametrize("width,height", [(1272, 800), (680, 740), (390, 800)])
def test_native_child_main_stage_has_viewport_height_in_chromium(app, width, height):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        try:
            page = browser.new_page(viewport={"width": width, "height": height}, reduced_motion="reduce")
            page.set_content(child_markup(app), wait_until="domcontentloaded")
            evidence = page.evaluate("""() => {
              const main = document.querySelector('main').getBoundingClientRect();
              const stage = document.querySelector('.stage').getBoundingClientRect();
              const chrome = document.querySelector('.viewport>header');
              const child = document.querySelector('.canvas-wrap').getBoundingClientRect();
              return {mainHeight: main.height, stageHeight: stage.height,
                childHeight: child.height, headerDisplay: getComputedStyle(chrome).display,
                stageTop: stage.top};
            }""")
            assert evidence["headerDisplay"] == "none"
            assert evidence["mainHeight"] >= height * 0.9, evidence
            assert evidence["stageHeight"] >= height * 0.7, evidence
            assert evidence["childHeight"] > 100, evidence
            assert evidence["stageTop"] < height / 5, evidence
        finally:
            browser.close()
