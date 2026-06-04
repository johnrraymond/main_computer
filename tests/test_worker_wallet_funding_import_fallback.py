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


def test_worker_wallet_funding_completion_posts_only_deposit_id_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_urlopen(request, timeout=0):
        payload = json.loads(request.data.decode("utf-8"))
        calls.append((request.full_url, payload))
        assert request.full_url.endswith("/api/hub/v1/credits/wallet-funding/complete")
        return _Response(
            {
                "ok": True,
                "idempotent": False,
                "account_id": payload["wallet_address"],
                "account": {"account_id": payload["wallet_address"], "available_credits": 1},
                "delta_credits": 1,
            }
        )

    monkeypatch.setattr(energy_routes, "urlopen", fake_urlopen)

    result = ViewportEnergyRoutesMixin()._post_worker_wallet_funding_completion_to_hub(
        hub_url="http://127.0.0.1:8770/",
        payload={
            "wallet_address": "0x7780097b4756ed08176d288b9acb8d9e878a5269",
            "chain_id": 42424242,
            "contract_address": "0x0000000000000000000000000000000000000001",
            "tx_hash": "0x" + "1" * 64,
            "deposit_id": "0x" + "2" * 64,
        },
    )

    assert len(calls) == 1
    assert [url.rsplit("/api/", 1)[1] for url, _payload in calls] == [
        "hub/v1/credits/wallet-funding/complete",
    ]
    forwarded = calls[0][1]
    assert forwarded["deposit_id"] == "0x" + "2" * 64
    assert forwarded["wallet_address"] == "0x7780097b4756ed08176d288b9acb8d9e878a5269"
    assert "credits_granted" not in forwarded
    assert "payment_amount_base_units" not in forwarded
    assert result["ok"] is True
    assert result["wallet_address"] == "0x7780097b4756ed08176d288b9acb8d9e878a5269"
    assert result["funding_model"] == "hub_credit_bridge_escrow_wallet_v2"
    assert result["wallet_funding_completion_endpoint"] == "/api/hub/v1/credits/wallet-funding/complete"


def test_worker_wallet_funding_completion_does_not_fallback_on_hub_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_urlopen(request, timeout=0):
        calls.append(request.full_url)
        raise _http_error(request.full_url, 400, "unknown deposit")

    monkeypatch.setattr(energy_routes, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="HTTP 400 from /api/hub/v1/credits/wallet-funding/complete"):
        ViewportEnergyRoutesMixin()._post_worker_wallet_funding_completion_to_hub(
            hub_url="http://127.0.0.1:8770",
            payload={
                "wallet_address": "0x7780097b4756ed08176d288b9acb8d9e878a5269",
                "deposit_id": "0x" + "2" * 64,
            },
        )

    assert len(calls) == 1


def test_worker_wallet_funding_completion_rejects_bad_deposit_id() -> None:
    mixin = ViewportEnergyRoutesMixin()

    with pytest.raises(ValueError, match="deposit_id"):
        mixin._post_worker_wallet_funding_completion_to_hub(
            hub_url="http://127.0.0.1:8770",
            payload={
                "wallet_address": "0x7780097b4756ed08176d288b9acb8d9e878a5269",
                "deposit_id": "not-a-deposit-id",
            },
        )
