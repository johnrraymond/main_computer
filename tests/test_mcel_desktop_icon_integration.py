"""Live MCEL desktop consumes the canonical icon registry, not copied SVGs."""
from __future__ import annotations

from copy import deepcopy
import re
import shutil
import xml.etree.ElementTree as ET

import pytest

import main_computer.mcel_icon as mcel_icon
from main_computer.mcel_applications_shell_host import (
    legacy_desktop_reference, render_applications_shell_candidate,
)


def _desktop_svgs(html: str) -> dict[str, ET.Element]:
    matches = re.findall(
        r'(<svg\b[^>]*\bdata-mcel-icon-id="application\.([a-z0-9-]+)"[^>]*>.*?</svg>)',
        html, flags=re.S,
    )
    assert len(matches) == 21, f"Desktop must render 21 MCEL icons, got {len(matches)}"
    result = {app: ET.fromstring(markup) for markup, app in matches}
    assert len(result) == 21, "Duplicate application icon identities"
    return result


def test_canonical_desktop_svg_geometry_inherits_registry_and_original_styling():
    html = render_applications_shell_candidate(canonical_promoted=True)
    icons = _desktop_svgs(html)
    reference = legacy_desktop_reference()
    assert set(icons) == {entry["app"] for entry in reference["icons"]}
    assert html.count('class="desktop-icon"') == len(icons)
    assert html.count('class="desktop-cluster"') == 5
    for app, element in icons.items():
        assert element.attrib["data-mcel-icon-id"] == "application." + app
        assert element.attrib["data-mcel-icon-family"] == "application"
        assert element.attrib["class"] == "mcel-icon-svg desktop-glyph-svg"
        assert element.attrib["aria-hidden"] == "true"
        assert [(node.tag.removeprefix("{http://www.w3.org/2000/svg}"), node.attrib)
                for node in element] == [
                    (shape["tag"], shape["attrs"])
                    for shape in mcel_icon.get_icon("application." + app)["shapes"]
                ]
        assert f'data-shell-launch="{app}"' in html
        assert f'href="/applications/{app}"' in html


def test_desktop_drawing_changes_only_when_canonical_registry_changes(monkeypatch):
    original_reference = legacy_desktop_reference()
    original = _desktop_svgs(render_applications_shell_candidate(canonical_promoted=True))
    changed = deepcopy(mcel_icon.load_registry())
    calculator = next(icon for icon in changed["icons"] if icon["id"] == "application.calculator")
    calculator["shapes"][0]["attrs"]["x"] = "6"
    monkeypatch.setattr(mcel_icon, "load_registry", lambda: changed)
    updated = _desktop_svgs(render_applications_shell_candidate(canonical_promoted=True))
    assert original["calculator"][0].attrib["x"] == "5"
    assert updated["calculator"][0].attrib["x"] == "6"
    assert all(ET.tostring(original[id]) == ET.tostring(updated[id])
               for id in original if id != "calculator")
    assert legacy_desktop_reference()["svg"] == original_reference["svg"]


def test_desktop_missing_canonical_icon_refuses_legacy_fallback(monkeypatch):
    incomplete = deepcopy(mcel_icon.load_registry())
    incomplete["icons"] = [i for i in incomplete["icons"] if i["id"] != "application.calculator"]
    monkeypatch.setattr(mcel_icon, "load_registry", lambda: incomplete)
    with pytest.raises(mcel_icon.IconRegistryError, match="application.calculator"):
        render_applications_shell_candidate(canonical_promoted=True)


@pytest.mark.parametrize("width", [390, 1040, 1440])
def test_browser_desktop_icons_have_semantic_ids_and_still_navigate(width):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("Playwright unavailable")
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=True, executable_path=shutil.which("chromium") or None,
            )
        except Exception as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        try:
            page = browser.new_page(viewport={"width": width, "height": 840}, reduced_motion="reduce")
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.set_content(render_applications_shell_candidate(canonical_promoted=True), wait_until="load")
            page.wait_for_selector('body[data-mcel-shell-ready="true"]')
            cards = page.locator('#mcel-shell-desktop-overlay .desktop-icon')
            assert cards.count() == 21
            assert page.locator('svg[data-mcel-icon-family="application"]').count() == 21
            for app in ("calculator", "file-explorer", "mcel-lab", "slh"):
                card = cards.filter(has=page.locator(f'svg[data-mcel-icon-id="application.{app}"]'))
                assert card.count() == 1
                assert card.get_attribute("href") == "/applications/" + app
            # about:blank has no /applications history origin. Record the
            # requested canonical route while exercising the actual click.
            page.evaluate("""() => { window.__desktopNavigations = [];
                history.pushState = (_state, _title, url) => window.__desktopNavigations.push(url);
            }""")
            page.locator('.desktop-icon[data-app="calculator"]').click()
            assert page.locator('body').get_attribute('data-mcel-shell-active-app') == 'calculator'
            assert page.locator('#mcel-shell-frame').get_attribute('src') == '/applications/calculator?mcel-shell-child=1'
            assert page.evaluate('window.__desktopNavigations.at(-1)')
            assert not errors, errors
        finally:
            browser.close()
