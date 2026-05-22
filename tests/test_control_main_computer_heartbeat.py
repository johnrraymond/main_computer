from __future__ import annotations

from pathlib import Path


CONTROL_SCRIPT = Path(__file__).resolve().parents[1] / "control-main-computer.ps1"


def _function_body(text: str, name: str, next_name: str) -> str:
    start = text.index(f"function {name} {{")
    end = text.index(f"function {next_name} {{", start)
    return text[start:end]


def test_start_viewport_preserves_and_ensures_heartbeat_for_existing_server() -> None:
    text = CONTROL_SCRIPT.read_text(encoding="utf-8")
    body = _function_body(text, "Start-Viewport", "Show-Status")
    existing_check = body.index("$existing = @(Get-ViewportProcesses)")

    assert "Stop-Heartbeat | Out-Null" not in body[:existing_check]
    assert "Assert-NoForeignPortListeners" not in body[:existing_check]
    assert body.count("Ensure-HeartbeatReady | Out-Null") >= 2


def test_control_status_reports_heartbeat_endpoint_and_logs() -> None:
    text = CONTROL_SCRIPT.read_text(encoding="utf-8")

    assert "$heartbeatControlUrl" in text
    assert "heartbeat: ready at" in text
    assert "heartbeat: missing at" in text
    assert "main_computer_heartbeat.err.log" in text
