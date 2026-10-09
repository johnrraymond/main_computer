from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "playwright_chromium_code_smoke.py"
SPEC = importlib.util.spec_from_file_location("playwright_chromium_code_smoke", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_parse_viewport() -> None:
    assert MODULE.parse_viewport("1920x1080") == (1920, 1080)
    assert MODULE.parse_viewport("390X844") == (390, 844)


def test_headed_launch_disables_background_throttling() -> None:
    headed = MODULE.chromium_launch_args(headed=True)
    assert "--disable-background-timer-throttling" in headed
    assert "--disable-backgrounding-occluded-windows" in headed
    assert "--disable-renderer-backgrounding" in headed
    feature_arg = next(arg for arg in headed if arg.startswith("--disable-features="))
    assert "CalculateNativeWinOcclusion" in feature_arg

    headless = MODULE.chromium_launch_args(headed=False)
    assert "--disable-background-timer-throttling" not in headless
    assert "CalculateNativeWinOcclusion" not in " ".join(headless)


def test_result_contract() -> None:
    assert MODULE.classify_result(True) == (True, "boolean-return")
    assert MODULE.classify_result(False) == (False, "boolean-return")
    assert MODULE.classify_result({"ok": True, "value": 7}) == (True, "object-ok-field")
    assert MODULE.classify_result({"ok": False}) == (False, "object-ok-field")
    assert MODULE.classify_result({"value": 7}) == (True, "successful-execution")
    assert MODULE.classify_result(None) == (True, "successful-execution")


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False
    try:
        with sync_playwright() as playwright:
            launched = MODULE.launch_chromium(playwright, headed=False)
            launched.browser.close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _chromium_available(), reason="Playwright Chromium is not available in this environment")
def test_runs_async_javascript_in_real_chromium() -> None:
    report = MODULE.run_smoke(
        source="""
const node = document.createElement('div');
node.id = 'probe';
node.textContent = String(args.value);
document.body.appendChild(node);
await new Promise(resolve => setTimeout(resolve, 5));
console.log('probe-ready', node.textContent);
return {
  ok: document.querySelector('#probe')?.textContent === '42',
  userAgent: navigator.userAgent,
  text: node.textContent,
};
""",
        user_args={"value": 42},
        timeout_seconds=10.0,
    )
    assert report["ok"] is True
    assert report["browser"]["engine"] == "chromium"
    assert report["execution"]["result"]["text"] == "42"
    assert any(item["text"].startswith("probe-ready") for item in report["console"])


@pytest.mark.skipif(not _chromium_available(), reason="Playwright Chromium is not available in this environment")
def test_false_or_throwing_code_fails() -> None:
    false_report = MODULE.run_smoke(source="return false;", timeout_seconds=10.0)
    assert false_report["ok"] is False
    assert "result-signaled-failure" in false_report["execution"]["failedReasons"]

    throw_report = MODULE.run_smoke(source="throw new Error('boom');", timeout_seconds=10.0)
    assert throw_report["ok"] is False
    assert throw_report["execution"]["error"] is not None
    assert "boom" in throw_report["execution"]["error"]["message"]
