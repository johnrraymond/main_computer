"""Intentional MCEL desktop regrouping without changing navigation authority."""
from __future__ import annotations

import shutil

import pytest

from main_computer.mcel_applications_shell_host import (
    DESKTOP_GROUPS,
    legacy_desktop_reference,
    legacy_desktop_icon_markup,
    render_applications_shell_candidate,
)
from main_computer.mcel_phase2c_candidate_host import CandidateHostError


def test_cluster_inventory_is_exactly_original_apps_in_five_groups():
    reference = legacy_desktop_reference()
    old = {item["app"] for item in reference["icons"]}
    grouped = [app for _, _, apps in DESKTOP_GROUPS for app in apps]
    assert len(DESKTOP_GROUPS) == 5
    assert len(grouped) == len(set(grouped)) == 20
    assert set(grouped) == old
    html = render_applications_shell_candidate(canonical_promoted=True)
    assert html.count('class="desktop-cluster"') == 5
    assert html.count('class="desktop-icon"') == 20
    assert 'desktop-cluster-apps' in html
    assert 'desktop-cluster-title' not in html
    assert '<div class="desktop-caption">' not in html
    assert '<h3 id="mcel-desktop-cluster-' not in html
    for summary in (item["summary"] for item in reference["icons"]):
        assert f'<span>{summary}</span>' not in html
    for slug, title, apps in DESKTOP_GROUPS:
        assert f'data-desktop-group="{slug}"' in html
        assert title.replace("&", "&amp;") in html
        for app in apps:
            assert f'data-shell-launch="{app}"' in html
            assert f'href="/applications/{app}"' in html
    # Original ungrouped renderer remains available to the legacy oracle.
    original = legacy_desktop_icon_markup(reference, launch=True)
    assert 'desktop-cluster' not in original
    assert original.count('class="desktop-icon"') == 20


@pytest.mark.parametrize("width", [390, 500, 820, 1040, 1268, 1440])
def test_desktop_cluster_spatial_palette_and_native_navigation(width):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as api:
        browser = api.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        try:
            page = browser.new_page(viewport={"width": width, "height": 780}, reduced_motion="reduce")
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.set_content(render_applications_shell_candidate(canonical_promoted=True), wait_until="load")
            assert page.locator("body").get_attribute("data-mcel-shell-ready") == "true"
            checks = page.evaluate("""() => {
                const groups = [...document.querySelectorAll('#mcel-shell-desktop-overlay .desktop-cluster')];
                const rect = el => el.getBoundingClientRect();
                return {
                    overflow:document.documentElement.scrollWidth > innerWidth + 1,
                    groups:groups.map(group => {
                        const cards=[...group.querySelectorAll('.desktop-icon')];
                        const box=rect(group);
                        return {
                            id:group.dataset.desktopGroup,
                            label:group.getAttribute('aria-label'),
                            groupBorder:getComputedStyle(group).borderWidth,
                            groupBg:getComputedStyle(group).backgroundColor,
                            apps:cards.map(card=>card.dataset.app),
                            accents:cards.map(card=>getComputedStyle(card).getPropertyValue('--desktop-icon-accent').trim()),
                            hrefs:cards.map(card=>card.getAttribute('href')),
                            tags:cards.map(card=>card.tagName),
                            widths:cards.map(card=>Math.round(rect(card).width)),
                            borders:cards.map(card=>getComputedStyle(card).borderWidth),
                            backgrounds:cards.map(card=>getComputedStyle(card).backgroundColor),
                            summaries:cards.map(card=>card.querySelectorAll('span:not(.desktop-glyph)').length),
                            inBounds:cards.every(card=>{
                                const c=rect(card);
                                return c.left>=box.left-1 && c.right<=box.right+1 &&
                                       c.top>=box.top-1 && c.bottom<=box.bottom+1;
                            }),
                            noOverlap:cards.every((card,i)=>cards.every((other,j)=>{
                                if(i>=j) return true;
                                const a=rect(card),b=rect(other);
                                return a.right<=b.left+1 || b.right<=a.left+1 ||
                                       a.bottom<=b.top+1 || b.bottom<=a.top+1;
                            })),
                            horizontalOverflow:group.scrollWidth>group.clientWidth+1,
                        };
                    })
                };
            }""")
            assert not checks["overflow"]
            assert len(checks["groups"]) == 5
            unique_palettes = []
            for group, (slug, title, apps) in zip(checks["groups"], DESKTOP_GROUPS):
                assert (group["id"], group["label"], group["apps"]) == (slug, title, list(apps))
                assert group["groupBorder"] == "0px"
                assert group["groupBg"] == "rgba(0, 0, 0, 0)"
                assert group["widths"] == [100] * len(apps)
                assert group["borders"] == ["0px"] * len(apps)
                assert group["backgrounds"] == ["rgba(0, 0, 0, 0)"] * len(apps)
                assert group["summaries"] == [0] * len(apps)
                assert group["inBounds"] and group["noOverlap"] and not group["horizontalOverflow"]
                assert group["tags"] == ["A"] * len(apps)
                assert group["hrefs"] == [f"/applications/{app}" for app in apps]
                assert len(set(group["accents"])) == 1
                unique_palettes.append(group["accents"][0])
            assert len(set(unique_palettes)) == 5
            page.evaluate("history.pushState = function () {}")
            page.locator('.desktop-icon[data-app="code-editor"]').click()
            assert page.locator("body").get_attribute("data-mcel-shell-active-app") == "code-editor"
            assert page.locator("#mcel-shell-frame").get_attribute("src") == "/applications/code-editor?mcel-shell-child=1"
            assert errors == []
        finally:
            browser.close()
