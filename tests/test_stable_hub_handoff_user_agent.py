"""Regression: Cloudflare must see Hub's explicit machine-to-machine User-Agent.

The real A->C cross-Hub smoke demonstrated that Cloudflare blocked urllib's
implicit Python-urllib User-Agent with HTTP 403 (1010), whereas this explicit
signature reached Hub C and returned its application JSON validation error.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import main_computer.stable_hub as stable_hub


def _handoff_handler():
    handler = stable_hub.StableHubRequestHandler.__new__(stable_hub.StableHubRequestHandler)
    handler.server = SimpleNamespace(hub=SimpleNamespace(hub_id="mainneta-hub1"))
    return handler


def _assert_outbound_request(request, timeout, expected_body):
    assert request.get_method() == "POST"
    assert request.full_url == "https://mainnetc-hub1.greatlibrary.io/api/hub/v1/work/requests"
    assert request.get_header("User-agent") == "main-computer-hub-handoff/1.0"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("X-main-computer-stable-hub-handoff-from") == "mainneta-hub1"
    assert json.loads(request.data.decode("utf-8")) == expected_body
    assert timeout == 15.0


def test_handoff_uses_verified_cloudflare_compatible_signature_on_success(monkeypatch):
    body = {"request_id": "req_test", "work": {"model": "gemma4"}}

    class FakeResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"ok": true, "accepted": true}'

    def fake_urlopen(request, timeout):
        _assert_outbound_request(request, timeout, body)
        return FakeResponse()

    monkeypatch.setattr(stable_hub, "urlopen", fake_urlopen)
    status, result = _handoff_handler()._post_same_request_to_owner_hub(
        owner_hub_id="mainnetc-hub1",
        owner_hub_url="https://mainnetc-hub1.greatlibrary.io",
        body=body,
    )
    assert status == 200
    assert result == {"ok": True, "accepted": True}


def test_handoff_preserves_hub_json_error_response(monkeypatch):
    # Live diagnostic with the explicit User-Agent got a Hub-owned HTTP 400,
    # not the Cloudflare 1010 block. The forwarded response must remain JSON.
    body = {}

    def fake_urlopen(request, timeout):
        _assert_outbound_request(request, timeout, body)
        raise HTTPError(
            request.full_url,
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"ok":false,"hub_id":"mainnetc-hub1","error":"request_id is required."}'),
        )

    monkeypatch.setattr(stable_hub, "urlopen", fake_urlopen)
    status, result = _handoff_handler()._post_same_request_to_owner_hub(
        owner_hub_id="mainnetc-hub1",
        owner_hub_url="https://mainnetc-hub1.greatlibrary.io",
        body=body,
    )
    assert status == 400
    assert result == {
        "ok": False,
        "hub_id": "mainnetc-hub1",
        "error": "request_id is required.",
    }
