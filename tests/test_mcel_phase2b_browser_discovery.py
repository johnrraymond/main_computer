"""Cross-platform browser discovery must not mistake PATH names for availability."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.mcel_phase2b_browser import (
    BrowserUnavailable,
    browser_launch_options,
    launch_semantic_browser,
)


def test_default_launches_playwright_then_windows_channels(monkeypatch):
    monkeypatch.delenv('MCEL_PHASE2B_BROWSER_EXECUTABLE', raising=False)
    monkeypatch.delenv('MCEL_CHROMIUM_EXECUTABLE', raising=False)
    options = browser_launch_options()
    assert options[:3] == [
        ('playwright-chromium', {}),
        ('msedge', {'channel': 'msedge'}),
        ('chrome', {'channel': 'chrome'}),
    ]


def test_explicit_override_must_exist_and_never_falls_back(monkeypatch, tmp_path):
    exe = tmp_path / 'test-browser.exe'
    monkeypatch.setenv('MCEL_PHASE2B_BROWSER_EXECUTABLE', str(exe))
    with pytest.raises(BrowserUnavailable, match='does not name an existing'):
        browser_launch_options()
    exe.write_bytes(b'test')
    assert browser_launch_options() == [
        (f'MCEL_PHASE2B_BROWSER_EXECUTABLE={exe}', {'executable_path': str(exe)})
    ]


def test_edge_fallback_uses_real_launch_attempt_not_name_lookup(monkeypatch):
    monkeypatch.delenv('MCEL_PHASE2B_BROWSER_EXECUTABLE', raising=False)
    monkeypatch.delenv('MCEL_CHROMIUM_EXECUTABLE', raising=False)
    calls = []
    sentinel = object()

    def launch(**kwargs):
        calls.append(kwargs)
        if kwargs.get('channel') == 'msedge':
            return sentinel
        raise RuntimeError('no Playwright-managed Chromium')

    result, label = launch_semantic_browser(SimpleNamespace(chromium=SimpleNamespace(launch=launch)))
    assert result is sentinel
    assert label == 'msedge'
    assert calls[0].get('channel') is None
    assert calls[1]['channel'] == 'msedge'


def test_failure_preserves_actionable_install_diagnostic(monkeypatch):
    monkeypatch.delenv('MCEL_PHASE2B_BROWSER_EXECUTABLE', raising=False)
    monkeypatch.delenv('MCEL_CHROMIUM_EXECUTABLE', raising=False)
    def fail(**kwargs):
        raise RuntimeError('browser-unavailable')
    with pytest.raises(BrowserUnavailable, match='python -m playwright install chromium'):
        launch_semantic_browser(SimpleNamespace(chromium=SimpleNamespace(launch=fail)))
