from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAB_SCRIPT = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-lab.js"


def _extract_function_source(script: str, function_name: str) -> str:
    match = re.search(rf"function\s+{re.escape(function_name)}\s*\([^)]*\)\s*\{{", script)
    assert match, f"missing function {function_name}"
    body_start = match.end() - 1
    depth = 0
    for index in range(body_start, len(script)):
        char = script[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return script[match.start() : index + 1]
    raise AssertionError(f"could not find end of function {function_name}")


def _isolated_site_css(tmp_path: Path) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; isolated-site CSS smoke test cannot run")

    lab_script = LAB_SCRIPT.read_text(encoding="utf-8")
    isolated_site_css = _extract_function_source(lab_script, "isolatedSiteCss")
    script_path = tmp_path / "extract-isolated-site-css.js"
    script_path.write_text(
        f"""
{isolated_site_css}
process.stdout.write(JSON.stringify({{css: isolatedSiteCss()}}));
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [node, str(script_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)["css"]


def test_runtime_action_anchor_reserves_control_box_in_accessible_spotlight(tmp_path: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is unavailable; runtime action geometry smoke test cannot run")

    chromium = shutil.which("chromium")
    if not chromium:
        pytest.skip("chromium is unavailable; runtime action geometry smoke test cannot run")

    css = _isolated_site_css(tmp_path)
    html = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <style>{css}</style>
  </head>
  <body class="mcel-site-theme theme-accessible" data-mcel-chrome="chrome-spotlight">
    <div class="mcel-runtime-preview theme-accessible">
      <main data-mc="component" data-mc-kind="hero">
        <section data-mc-slot="content">
          <p data-mc-slot="actions">
            <a href="#join" data-mc-action="join-neighborhood">Join the list</a>
          </p>
        </section>
      </main>
    </div>
  </body>
</html>"""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=chromium,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--no-zygote",
            ],
        )
        try:
            page = browser.new_page(viewport={"width": 900, "height": 700})
            page.set_content(html)
            geometry = page.evaluate(
                """() => {
                  const action = document.querySelector('[data-mc-action="join-neighborhood"]');
                  const parent = action.closest('[data-mc-slot="actions"]');
                  const actionRect = action.getBoundingClientRect();
                  const parentRect = parent.getBoundingClientRect();
                  const computed = getComputedStyle(action);
                  return {
                    display: computed.display,
                    actionHeight: actionRect.height,
                    parentHeight: parentRect.height,
                    parentReservesAction: parentRect.height + 0.5 >= actionRect.height,
                    lineHeight: computed.lineHeight,
                    maxInlineSize: computed.maxInlineSize,
                    boxSizing: computed.boxSizing
                  };
                }"""
            )
        finally:
            browser.close()

    assert geometry["display"] == "inline-flex"
    assert geometry["boxSizing"] == "border-box"
    assert geometry["parentReservesAction"], geometry
    assert geometry["parentHeight"] >= 52
