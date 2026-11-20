"""The compact launcher follows the *rendered* desktop, not registry order."""
from __future__ import annotations

import shutil

import pytest

from main_computer.mcel_applications_shell_host import (
    DESKTOP_GROUPS,
    compile_applications_shell,
    render_applications_shell_candidate,
)


def _desktop_order():
    return [app for _, _, apps in DESKTOP_GROUPS for app in apps]


def test_desktop_order_is_not_the_old_launcher_inventory_order():
    _, catalog = compile_applications_shell()
    assert catalog["apps"][0]["id"] == "desktop"
    assert [app["id"] for app in catalog["apps"]][1:] != _desktop_order()
    assert set(_desktop_order()) == {app["id"] for app in catalog["apps"]} - {"desktop"}


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("promoted", [False, True])
def test_compact_launcher_follows_desktop_dom_and_preserves_navigation(width, promoted):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=shutil.which("chromium") or None
        )
        try:
            page = browser.new_page(viewport={"width": width, "height": 840}, reduced_motion="reduce")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.set_content(render_applications_shell_candidate(canonical_promoted=promoted), wait_until="load")
            assert page.locator("body").get_attribute("data-mcel-shell-ready") == "true"
            result = page.evaluate("""() => {
                const rail = [...document.querySelectorAll('#mcel-shell-launcher-items .mcel-shell-app-card')];
                const desktop = [...document.querySelectorAll('#mcel-shell-desktop-overlay .desktop-icon[data-app]')];
                return {
                    rail:rail.map(a=>a.dataset.app),
                    desktop:desktop.map(a=>a.dataset.app),
                    hrefs:rail.map(a=>a.getAttribute('href')),
                    icons:rail.map(a=>a.querySelectorAll('svg').length),
                    heights:rail.map(a=>Math.round(a.getBoundingClientRect().height)),
                    overflows:document.documentElement.scrollWidth > window.innerWidth + 1,
                };
            }""")
            assert result["desktop"] == _desktop_order()
            assert result["rail"] == ["desktop", *result["desktop"]]
            assert len(result["rail"]) == 22
            assert result["hrefs"] == ["/applications" if app == "desktop" else f"/applications/{app}" for app in result["rail"]]
            assert result["icons"] == [1] * 22
            assert max(result["heights"]) <= 42
            assert not result["overflows"]
            # Verify that reordering did not decouple selection and its canonical destination.
            page.evaluate("history.pushState = () => {}")
            page.locator('.mcel-shell-app-card[data-app="slh"]').click()
            assert page.locator("body").get_attribute("data-mcel-shell-active-app") == "slh"
            assert page.locator('.mcel-shell-app-card[aria-current="page"]').get_attribute("data-app") == "slh"
            assert page.locator("#mcel-shell-frame").get_attribute("src") == "/applications/slh?mcel-shell-child=1"
            assert errors == []
        finally:
            browser.close()


def test_launcher_refuses_incomplete_desktop_inventory():
    from playwright.sync_api import sync_playwright

    html = render_applications_shell_candidate()
    original = 'class="desktop-icon" data-app="calculator"'
    assert html.count(original) == 1
    html = html.replace(original, 'class="desktop-icon" data-app="unregistered"', 1)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=shutil.which("chromium") or None
        )
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            assert page.locator("body").get_attribute("data-mcel-shell-ready") == "false"
            assert "launcher-desktop-order-mismatch" in page.locator("#mcel-shell-status").inner_text()
        finally:
            browser.close()
