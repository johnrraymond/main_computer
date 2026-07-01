from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_start_v2_accepts_open_browser_argument_and_calls_helper() -> None:
    text = read("start_v2.bat")

    assert 'if /I "%~1"=="-OpenBrowser" goto mc_enable_open_browser' in text
    assert 'if /I "%~1"=="--open-browser" goto mc_enable_open_browser' in text
    assert 'if /I "%~1"=="/OpenBrowser" goto mc_enable_open_browser' in text
    assert 'set "MC_OPEN_BROWSER=1"' in text
    assert 'Usage: start_v2.bat [-OpenBrowser] [--no-dev-hub]' in text
    assert 'if /I "%~1"=="--no-dev-hub" goto mc_disable_dev_hub' in text
    assert 'set "MC_NO_DEV_HUB=1"' in text
    assert '-NoDevHub' in text
    assert 'scripts\\open-main-computer-browser.ps1' in text
    assert '-TimeoutSeconds 120' in text


def test_browser_helper_uses_installed_launcher_manifest_port() -> None:
    text = read("scripts/open-main-computer-browser.ps1")

    assert "runtime\\start_stop\\main-computer-launcher.json" in text
    assert "MAIN_COMPUTER_CONTROL_PORT" in text
    assert "http://127.0.0.1:$parsedPort/" in text
    assert '"safe" { return "38865" }' in text
    assert '"debug" { return "28865" }' in text
    assert 'default { return "8765" }' in text


def test_browser_helper_waits_for_http_readiness_before_opening() -> None:
    text = read("scripts/open-main-computer-browser.ps1")

    assert "function Wait-MainComputerBrowserReady" in text
    assert "[System.Net.WebRequest]::Create($TargetUrl)" in text
    assert "$request.Timeout = 3000" in text
    assert "$statusCode -ge 200 -and $statusCode -lt 500" in text
    assert "Start-Process $targetUrl" in text
    assert text.index("Wait-MainComputerBrowserReady") < text.index("Start-Process $targetUrl")

def test_start_v2_uses_crlf_line_endings_for_cmd_label_goto() -> None:
    data = (ROOT / "start_v2.bat").read_bytes()

    assert b"\r\n" in data
    assert data.count(b"\n") == data.count(b"\r\n")
    assert b"\r\n:mc_skip_open_browser\r\n" in data

