"""One-line application launcher: no new icon/color registry or app remount."""
from __future__ import annotations

import shutil

import pytest

from main_computer.mcel_applications_shell_host import (
    compile_applications_shell, render_applications_shell_candidate,
)


@pytest.mark.parametrize("width", [390, 1280])
def test_compact_launcher_uses_authoritative_icons_and_accents(width):
    from playwright.sync_api import sync_playwright

    _, registry = compile_applications_shell()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = browser.new_page(viewport={'width': width, 'height': 840}, reduced_motion='reduce')
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.set_content(render_applications_shell_candidate(), wait_until='domcontentloaded')
            assert page.locator('body').get_attribute('data-mcel-shell-ready') == 'true'
            assert page.locator('.mcel-shell-app-card').count() == len(registry['apps']) == 22
            checks = page.evaluate('''() => [...document.querySelectorAll('.mcel-shell-app-card')].map(card => {
              const desktop=document.querySelector('.desktop-icon[data-app="'+card.dataset.app+'"]');
              const glyph=card.querySelector('.mcel-shell-app-icon');
              const svg=glyph?.querySelector('svg');
              const label=card.querySelector('strong');
              return {
                app:card.dataset.app, title:label?.textContent,
                height:card.getBoundingClientRect().height,
                labelHeight:label.getBoundingClientRect().height,
                singleSpan:card.querySelectorAll('span').length===1,
                accent:getComputedStyle(card).getPropertyValue('--mcel-app-accent').trim(),
                glyphColor:getComputedStyle(glyph).color,
                sourceAccent:desktop?getComputedStyle(desktop).getPropertyValue('--desktop-icon-accent').trim():null,
                sourceSvg:desktop?.querySelector('svg')?.innerHTML.trim(),
                glyphSvg:svg?.innerHTML.trim(),
                iconId:svg?.getAttribute('data-mcel-icon-id'),
                tooltip:card.title,
                href:card.getAttribute('href')
              };
            })''')
            desktop_order = page.eval_on_selector_all(
                '#mcel-shell-desktop-overlay .desktop-icon[data-app]',
                '(icons) => icons.map(icon => icon.dataset.app)',
            )
            assert [item['app'] for item in checks] == ['desktop', *desktop_order]
            registered_by_id = {app['id']: app for app in registry['apps']}
            for row in checks:
                app = registered_by_id[row['app']]
                assert row['height'] == 38, row
                assert row['labelHeight'] <= 24, row
                assert row['singleSpan'], row
                assert row['title'] == app['title'], row
                assert row['tooltip'] == app['title'] + ' — ' + app['description'], row
                assert row['href'] == app['route'], row
                if row['app'] == 'desktop':
                    assert row['iconId'] == 'navigation.home', row
                else:
                    assert row['iconId'] == 'application.' + row['app'], row
                    assert row['accent'] == row['sourceAccent'], row
                    assert row['glyphSvg'] == row['sourceSvg'], row
                assert row['glyphColor'] != 'rgb(201, 192, 170)', row
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.evaluate('history.pushState = () => {}')
            page.locator('.mcel-shell-app-card[data-app="calculator"]').click()
            assert page.locator('body').get_attribute('data-mcel-shell-active-app') == 'calculator'
            assert page.locator('#mcel-shell-frame').get_attribute('src') == '/applications/calculator?mcel-shell-child=1'
            assert page.locator('.mcel-shell-app-card[aria-current="page"]').get_attribute('data-app') == 'calculator'
            assert not errors, errors
        finally:
            browser.close()
