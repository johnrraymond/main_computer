from __future__ import annotations

import time
from types import SimpleNamespace

from main_computer.hub import HubServerHandler


class _BrokenPipeWriter:
    def write(self, _data: bytes) -> int:
        raise BrokenPipeError(32, "Broken pipe")


def _bare_handler() -> HubServerHandler:
    handler = object.__new__(HubServerHandler)
    handler.server = SimpleNamespace(server_port=8790, verbose=True)
    handler.client_address = ("10.0.1.7", 49970)
    handler.close_connection = False
    handler.send_response = lambda _status: None
    handler.send_header = lambda _key, _value: None
    handler.end_headers = lambda: None
    return handler


def test_json_response_swallows_client_broken_pipe_and_closes_connection() -> None:
    handler = _bare_handler()
    handler.wfile = _BrokenPipeWriter()

    assert handler._send_json({"ok": True}) is False
    assert handler.close_connection is True


def test_status_transport_disconnect_emits_concise_diagnostic(monkeypatch, capsys) -> None:
    handler = _bare_handler()
    monkeypatch.setenv("HUB_STATUS_SLOW_LOG_SECONDS", "60")

    handler._log_hub_status_timing(
        path="/api/hub/v1/status",
        started_at=time.perf_counter(),
        exact_payouts=False,
        response_written=False,
    )

    stderr = capsys.readouterr().err
    assert "[hub-status:8790]" in stderr
    assert '"event": "hub.status.request"' in stderr
    assert '"client_disconnected": true' in stderr
    assert '"client": "10.0.1.7"' in stderr
    assert '"path": "/api/hub/v1/status"' in stderr


def test_slow_status_response_is_logged_even_when_write_succeeds(monkeypatch, capsys) -> None:
    handler = _bare_handler()
    monkeypatch.setenv("HUB_STATUS_SLOW_LOG_SECONDS", "0")

    handler._log_hub_status_timing(
        path="/api/hub/v1/status",
        started_at=time.perf_counter(),
        exact_payouts=True,
        response_written=True,
    )

    stderr = capsys.readouterr().err
    assert '"slow": true' in stderr
    assert '"client_disconnected": false' in stderr
    assert '"exact_payouts": true' in stderr
