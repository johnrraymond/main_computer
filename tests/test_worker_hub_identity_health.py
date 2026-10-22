"""Read-only Worker Hub health contract tests: Stable Hub identity, network isolation, legacy fallback."""
from __future__ import annotations

from io import BytesIO
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest


class _Reply:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _routes(tmp_path, monkeypatch):
    from main_computer import game_web_loader
    monkeypatch.setattr(game_web_loader, "read_game_bundle", lambda *args, **kwargs: "")
    from main_computer.config import MainComputerConfig
    from main_computer.viewport_routes_energy import WorkerRuntimeService
    server = SimpleNamespace(
        debug_root=tmp_path,
        config=MainComputerConfig(workspace=tmp_path),
        signal=lambda *args, **kwargs: None,
    )
    return WorkerRuntimeService(server)


def _identity(*, network="testnet", chain="42424241", ok=True, service="main_computer.stable_hub", hub_id="testnetb-hub1"):
    return {"ok": ok, "service": service, "hub_id": hub_id,
            "network": {"network_key": network, "chain_id": chain},
            "hub": {"hub_id": hub_id}}


def _wire(monkeypatch, payload=None, *, status=200, legacy=None):
    from main_computer import viewport_routes_energy as module
    requests = []

    def fake_open(request, timeout=0):
        requests.append((request.full_url, dict(request.headers), timeout))
        if request.full_url.endswith("/api/hub/v1/hub-identity"):
            if status != 200:
                raise HTTPError(request.full_url, status, "test response", {}, BytesIO(b'{"error":"not found"}'))
            return _Reply(payload)
        if request.full_url.endswith("/api/hub/status"):
            if legacy is None:
                raise AssertionError("legacy fallback was not expected")
            return _Reply(legacy)
        raise AssertionError(request.full_url)

    monkeypatch.setattr(module, "urlopen", fake_open)
    return requests


def test_live_testnet_identity_marks_hub_reachable(tmp_path, monkeypatch):
    service = _routes(tmp_path, monkeypatch)
    requests = _wire(monkeypatch, _identity())
    result = service._worker_network_session_payload({"selectedNetwork": "testnet", "workerRequestedRing": "3"}, check_hub=True)
    session = result["session"]
    assert session["connection_status"] == "connected"
    assert session["connection_error"] == ""
    assert session["hub_status"]["protocol"] == "stable-hub-identity-v1"
    assert session["hub_status"]["status"]["hub_id"] == "testnetb-hub1"
    assert requests[0][0] == "https://testnet-hub.greatlibrary.io/api/hub/v1/hub-identity"
    assert len(requests) == 1


@pytest.mark.parametrize("identity, error", [
    (_identity(network="mainnet"), "network mismatch"),
    (_identity(chain="42424240"), "chain ID mismatch"),
    (_identity(ok=False), "not a verified Stable Hub"),
    (_identity(service="other"), "not a verified Stable Hub"),
    (_identity(hub_id=""), "no hub_id"),
    ({"ok": True, "service": "main_computer.stable_hub", "hub_id": "x"}, "no network contract"),
])
def test_rejects_identity_mismatch(tmp_path, monkeypatch, identity, error):
    service = _routes(tmp_path, monkeypatch)
    _wire(monkeypatch, identity)
    result = service._worker_network_session_payload({"selectedNetwork": "testnet"}, check_hub=True)["session"]
    assert result["connection_status"] == "failed"
    assert error in result["connection_error"]
    assert result["hub_status"]["reachable"] is False


def test_prod_404_does_not_downgrade_to_legacy_status(tmp_path, monkeypatch):
    service = _routes(tmp_path, monkeypatch)
    requests = _wire(monkeypatch, status=404, legacy={"ok": True})
    result = service._worker_network_session_payload({"selectedNetwork": "testnet"}, check_hub=True)["session"]
    assert result["connection_status"] == "failed"
    assert "require a verified Stable Hub" in result["connection_error"]
    assert len(requests) == 2


def test_legacy_test_hub_still_uses_old_status_route(tmp_path, monkeypatch):
    service = _routes(tmp_path, monkeypatch)
    requests = _wire(monkeypatch, status=404, legacy={"workers": []})
    result = service._worker_network_session_payload({"selectedNetwork": "test"}, check_hub=True)["session"]
    assert result["connection_status"] == "connected"
    assert result["hub_status"]["protocol"] == "legacy-hub-status"
    assert requests[-1][0].endswith("/api/hub/status")


def test_503_never_downgrades(tmp_path, monkeypatch):
    service = _routes(tmp_path, monkeypatch)
    requests = _wire(monkeypatch, status=503)
    result = service._worker_network_session_payload({"selectedNetwork": "test"}, check_hub=True)["session"]
    assert result["connection_status"] == "failed"
    assert len(requests) == 1


def test_unexpected_html_rejected(tmp_path, monkeypatch):
    service = _routes(tmp_path, monkeypatch)
    _wire(monkeypatch, payload=["not an identity"])
    result = service._worker_network_session_payload({"selectedNetwork": "testnet"}, check_hub=True)["session"]
    assert result["connection_status"] == "failed"
    assert "non-object" in result["connection_error"]
