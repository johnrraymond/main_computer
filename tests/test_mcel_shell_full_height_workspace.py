"""Applications shell must not allocate a redundant masthead above child apps.

The Apps launcher remains shell-owned, and the canonical child retains its own
heading and operational controls. No service actions are invoked here.
"""
from __future__ import annotations

import shutil

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_applications_shell_host import (
    compile_applications_shell,
    render_applications_shell_candidate,
)


def test_masthead_is_removed_from_the_canonical_semantic_contract():
    ir, registry = compile_applications_shell()
    regions = ir["surfaces"][0]["interfaceIntent"]["regions"]
    names = {region["id"].rsplit(".", 1)[-1] for region in regions}
    assert names == {"application", "shell", "launcher", "workspace", "application-mount", "status"}
    assert "masthead" not in names
    layout = ir["layouts"][0]["layoutTree"]["root"]
    assert "masthead" not in {node["id"] for node in layout["children"]}
    assert any(entry["id"] == "slh" and entry["route"] == "/applications/slh"
               for entry in registry["apps"])
    html = render_applications_shell_candidate(canonical_promoted=True)
    assert 'class="mcel-shell-masthead"' not in html
    assert '<h1>Main Computer Applications</h1>' not in html
    assert 'id="mcel-shell-launcher-panel"' in html
    assert 'id="mcel-shell-status"' in html


@pytest.mark.parametrize("width", [390, 500, 1040, 1440])
def test_child_mount_and_apps_rail_use_full_available_height(width):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=shutil.which("chromium") or None,
            args=["--no-sandbox"],
        )
        try:
            page = browser.new_page(viewport={"width": width, "height": 840}, reduced_motion="reduce")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.set_content(render_applications_shell_candidate(canonical_promoted=True), wait_until="load")
            assert page.locator("body").get_attribute("data-mcel-shell-ready") == "true"
            assert page.locator("#mcel-shell-masthead").count() == 0
            root = page.locator(".mcel-applications-shell").bounding_box()
            work = page.locator(".mcel-shell-application-mount").bounding_box()
            rail = page.locator("#mcel-shell-launcher-panel").bounding_box()
            status = page.locator("#mcel-shell-status").bounding_box()
            assert root["y"] == 0
            assert abs(work["y"]) <= 1
            assert abs(rail["y"]) <= 1
            assert abs(work["height"] - (840 - status["height"])) <= 1
            assert abs(work["y"] + work["height"] - status["y"]) <= 1
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            # SLH remains a real application with the native child URL. The
            # parent shell's removal must not change app routing or remount it.
            page.evaluate("history.pushState = () => {}")
            assert page.evaluate('window.__mcelApplicationsShell.select("slh", true)') is True
            assert page.locator("#mcel-shell-frame").get_attribute("src") == "/applications/slh?mcel-shell-child=1"
            assert page.locator('.mcel-shell-app-card[aria-current="page"]').get_attribute("data-app") == "slh"
            assert abs(page.locator("#mcel-shell-frame").bounding_box()["y"]) <= 1
            assert errors == []
        finally:
            browser.close()
