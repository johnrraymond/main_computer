import json
from urllib.error import HTTPError

import pytest

import main_computer.viewport_routes_energy as energy_routes
from main_computer.viewport_routes_energy import ViewportEnergyRoutesMixin


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _ErrorBody:
    def __init__(self, body: str):
        self.body = body.encode("utf-8")

    def read(self) -> bytes:
        return self.body

    def close(self) -> None:
        return None


def _http_error(url: str, status: int, body: str) -> HTTPError:
    return HTTPError(url, status, "error", hdrs=None, fp=_ErrorBody(body))


def test_worker_wallet_funding_import_falls_back_to_legacy_deposit_route(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_urlopen(request, timeout=0):
        payload = json.loads(request.data.decode("utf-8"))
        calls.append((request.full_url, payload))
        if request.full_url.endswith("/api/hub/v1/credits/wallet-funding/import"):
            raise _http_error(request.full_url, 404, "missing newest route")
        assert request.full_url.endswith("/api/hub/v1/credits/deposits/import")
        return _Response(
            {
                "ok": True,
                "idempotent": False,
                "account": {"account_id": payload["account_id"], "available_credits": "100"},
            }
        )

    monkeypatch.setattr(energy_routes, "urlopen", fake_urlopen)

    result = ViewportEnergyRoutesMixin()._post_worker_wallet_funding_import_to_hub(
        hub_url="http://127.0.0.1:8770/",
        payload={
            "wallet_address": "0x7780097b4756ed08176d288b9acb8d9e878a5269",
            "chain_id": 42424242,
            "contract_address": "0x0000000000000000000000000000000000000001",
            "tx_hash": "0x" + "1" * 64,
            "log_index": 0,
            "block_number": 4,
            "payer_address": "0x7780097b4756ed08176d288b9acb8d9e878a5269",
            "payment_asset": "native",
            "payment_amount_base_units": 100,
            "credits_granted": 100,
        },
    )

    assert [url.rsplit("/api/", 1)[1] for url, _payload in calls] == [
        "hub/v1/credits/wallet-funding/import",
        "hub/v1/credits/deposits/import",
    ]
    assert calls[1][1]["tx_hash"] == "0x" + "1" * 64
    assert calls[1][1]["account_id"] == "0x7780097b4756ed08176d288b9acb8d9e878a5269"
    assert result["ok"] is True
    assert result["wallet_address"] == "0x7780097b4756ed08176d288b9acb8d9e878a5269"
    assert result["account_id"] == "0x7780097b4756ed08176d288b9acb8d9e878a5269"
    assert result["funding_model"] == "hub_credit_bridge_escrow_wallet_v1"
    assert result["wallet_funding_import_fallback"] is True
    assert result["wallet_funding_import_endpoint"] == "/api/hub/v1/credits/deposits/import"


def test_worker_wallet_funding_import_legacy_payload_adds_account_id(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_urlopen(request, timeout=0):
        payload = json.loads(request.data.decode("utf-8"))
        calls.append((request.full_url, payload))
        if request.full_url.endswith("/api/hub/v1/credits/wallet-funding/import"):
            raise _http_error(request.full_url, 404, "missing newest route")
        if "account_id" not in payload:
            raise _http_error(request.full_url, 400, '{"error": "account_id is required."}')
        return _Response({"ok": True, "idempotent": False})

    monkeypatch.setattr(energy_routes, "urlopen", fake_urlopen)

    result = ViewportEnergyRoutesMixin()._post_worker_wallet_funding_import_to_hub(
        hub_url="http://127.0.0.1:8770/",
        payload={
            "wallet_address": "0x7780097b4756ed08176d288b9acb8d9e878a5269",
            "chain_id": 42424242,
            "contract_address": "0x0000000000000000000000000000000000000001",
            "tx_hash": "0x" + "2" * 64,
            "log_index": 0,
            "block_number": 4,
            "payer_address": "0x7780097b4756ed08176d288b9acb8d9e878a5269",
            "payment_asset": "native",
            "payment_amount_base_units": 100,
            "credits_granted": 100,
        },
    )

    assert [url.rsplit("/api/", 1)[1] for url, _payload in calls] == [
        "hub/v1/credits/wallet-funding/import",
        "hub/v1/credits/deposits/import",
    ]
    assert calls[0][1].get("account_id") is None
    assert calls[1][1]["account_id"] == "0x7780097b4756ed08176d288b9acb8d9e878a5269"
    assert result["ok"] is True
    assert result["account_id"] == "0x7780097b4756ed08176d288b9acb8d9e878a5269"


def test_worker_wallet_funding_import_does_not_fallback_on_bad_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_urlopen(request, timeout=0):
        calls.append(request.full_url)
        raise _http_error(request.full_url, 400, "bad receipt")

    monkeypatch.setattr(energy_routes, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="HTTP 400 from /api/hub/v1/credits/wallet-funding/import"):
        ViewportEnergyRoutesMixin()._post_worker_wallet_funding_import_to_hub(
            hub_url="http://127.0.0.1:8770",
            payload={
                "wallet_address": "0x7780097b4756ed08176d288b9acb8d9e878a5269",
                "tx_hash": "not-a-real-receipt",
            },
        )

    assert len(calls) == 1
