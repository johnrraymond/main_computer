"""MCEL Element Showcase: visual fixture fidelity and interactive presentation.

Evidence comes from real Chromium with the production element test JS and
mcel-lab.css.  No screenshot is approved merely because its mechanics pass.
"""
from __future__ import annotations

from pathlib import Path
import os
import shutil

import pytest

from tools import mcel_functional_acid as acid


def _find_chromium_class_browser():
    """Use a real browser on Windows/Linux, including a Playwright installation.

    The functional-acid browser runner accepts a Chromium-family executable;
    Windows normally provides msedge.exe rather than a `chromium` command.
    """
    binaries = (
        'chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable',
        'chrome', 'msedge',
    )
    candidates = [shutil.which(name) for name in binaries]
    install_roots = [os.environ.get(name) for name in (
        'PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA',
    )]
    for root in filter(None, install_roots):
        root_path = Path(root)
        candidates.extend([
            str(root_path / 'Microsoft/Edge/Application/msedge.exe'),
            str(root_path / 'Google/Chrome/Application/chrome.exe'),
        ])
    # playwright.chromium.executable_path returns the expected bundled path
    # even if that binary has not been installed, so check that it exists.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        candidates.append(pw.chromium.executable_path)
    for executable in filter(None, candidates):
        if Path(executable).is_file():
            return str(executable)
    return None


def test_visual_fixture_uses_real_lab_styles_and_markup():
    html = (acid.ROOT / 'main_computer/web/applications/apps/mcel-lab.html').read_text(encoding='utf-8')
    assert 'id="mcel-element-acid-summary" class="mcel-site-skeleton-cards"' in html
    assert 'id="mcel-element-acid-report" class="mcel-site-skeleton-health"' in html
    assert 'id="mcel-element-acid-canvas" class="mcel-element-acid-canvas"' in html
    assert (acid.ROOT / acid.BASE_STYLE).is_file()
    assert (acid.ROOT / acid.STYLE).is_file()


def test_browser_checks_showcase_presentation_and_keeps_baselines_unreviewed(tmp_path):
    pytest.importorskip('playwright.sync_api')
    executable = _find_chromium_class_browser()
    assert executable, (
        'No Chromium-family browser found; install one with '
        '`python -m playwright install chromium` or install Microsoft Edge/Chrome'
    )
    outcome = acid._run_browser(
        acid.ROOT, tmp_path, None, approve=False, chromium=executable
    )
    assert not outcome.get('error'), outcome.get('error')
    by_id = {check['id']: check for check in outcome['checks']}
    for width in (1280, 390):
        contract = by_id[f'elements-presentation-{width}']
        assert contract['status'] == 'PASS', contract['evidence']
        measurements = contract['evidence']
        assert measurements['primaryMetrics'] == 5
        assert measurements['additionalMetrics'] >= 10
        assert measurements['additionalCollapsed']
        assert measurements['reportCollapsed']
        assert measurements['reportStructured']
        assert not measurements['overlap']
        assert all(x['ratio'] >= 4.5 for x in measurements['contrasts'])
        disclosure = by_id[f'elements-details-toggle-{width}']
        assert disclosure['status'] == 'PASS', disclosure['evidence']
        assert disclosure['evidence']['scrollWidth'] <= width + 2
        assert by_id[f'elements-geometry-{width}']['status'] == 'PASS'
    assert {entry['name'] for entry in outcome['visuals']} == {
        'element-showcase-1280.png', 'element-showcase-390.png',
        'notebook-1280.png', 'notebook-390.png'
    }
    assert all(entry['status'] == 'UNVERIFIED' and entry['reviewRequired']
               for entry in outcome['visuals'])
    assert all(Path(entry['path']).is_file() for entry in outcome['visuals'])


def test_report_uses_generic_native_disclosure_not_app_specific_renderer():
    js = (acid.ROOT / acid.SOURCE / 'mcel-element-acid-test.js').read_text(encoding='utf-8')
    css = (acid.ROOT / acid.STYLE).read_text(encoding='utf-8')
    assert 'mcel-element-acid-report-details' in js
    assert 'mcel-element-acid-summary-details' in js
    assert 'createNode(document, "details"' in js
    assert '--mcel-theme-ink' in css
    # Business lifecycle and authorization are not part of this presentation repair.
    assert 'intent:seh-deployment-undeploy' not in js
