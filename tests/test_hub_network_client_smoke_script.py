from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_smoke_client():
    spec = importlib.util.spec_from_file_location(
        "smoke_hub_network_client",
        ROOT / "scripts" / "smoke_hub_network_client.py",
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class Profile:
    network_key = "test"
    chain_id = 42424241
    chain_rpc_url = "http://127.0.0.1:30010"


def test_validate_manifest_requires_matching_network_chain_rpc_and_smoke_client() -> None:
    smoke = load_smoke_client()
    manifest = {
        "schema": "main-computer.deployment.v1",
        "environment": "test",
        "chain": {
            "chain_id": 42424241,
            "rpc_url": "http://127.0.0.1:30010",
        },
        "contracts": {
            "hub_credit_bridge_escrow": {
                "address": "0x1111111111111111111111111111111111111111",
            },
        },
        "smoke_client": {
            "address": "0x2222222222222222222222222222222222222222",
            "wallet_path": "runtime/deployments/test/smoke-client-wallet-42424241.json",
        },
    }

    validated = smoke.validate_manifest(Profile, manifest)

    assert validated["smoke_client"]["address"] == "0x2222222222222222222222222222222222222222"
    assert validated["contracts"]["hub_credit_bridge_escrow"]["address"] == "0x1111111111111111111111111111111111111111"


def test_validate_manifest_rejects_missing_smoke_client() -> None:
    smoke = load_smoke_client()
    manifest = {
        "schema": "main-computer.deployment.v1",
        "environment": "test",
        "chain": {
            "chain_id": 42424241,
            "rpc_url": "http://127.0.0.1:30010",
        },
        "contracts": {"hub_credit_bridge_escrow": {"address": "0x1111111111111111111111111111111111111111"}},
    }

    try:
        smoke.validate_manifest(Profile, manifest)
    except smoke.SmokeFailure as exc:
        assert "smoke_client.address" in str(exc)
    else:
        raise AssertionError("expected manifest without smoke client to fail")


def test_load_smoke_client_wallet_requires_chain_and_manifest_address(tmp_path: Path) -> None:
    smoke = load_smoke_client()
    wallet_path = tmp_path / "runtime" / "deployments" / "test" / "smoke-client-wallet-42424241.json"
    wallet_path.parent.mkdir(parents=True)
    wallet_path.write_text(
        json.dumps(
            {
                "schema": "main-computer.smoke-client-wallet.v1",
                "chain_id": 42424241,
                "address": "0x2222222222222222222222222222222222222222",
                "private_key": "0x" + "3" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    wallet = smoke.load_smoke_client_wallet(
        {
            "address": "0x2222222222222222222222222222222222222222",
            "wallet_path": "runtime/deployments/test/smoke-client-wallet-42424241.json",
        },
        expected_chain_id=42424241,
        repo_root=tmp_path,
    )

    assert wallet["address"] == "0x2222222222222222222222222222222222222222"
    assert wallet["private_key"] == "0x" + "3" * 64
    assert wallet["_path"] == str(wallet_path)


def test_load_deployment_manifest_prefers_selected_network_latest_over_current(tmp_path: Path) -> None:
    smoke = load_smoke_client()
    test_latest = tmp_path / "runtime" / "deployments" / "test" / "latest.json"
    current = tmp_path / "runtime" / "deployments" / "dev" / "latest.json"
    test_latest.parent.mkdir(parents=True)
    current.parent.mkdir(parents=True, exist_ok=True)
    test_latest.write_text(json.dumps({"environment": "test", "marker": "test-latest"}), encoding="utf-8")
    current.write_text(json.dumps({"environment": "dev", "marker": "current-dev"}), encoding="utf-8")

    path, payload = smoke.load_deployment_manifest("test", repo_root=tmp_path)

    assert path == test_latest
    assert payload["marker"] == "test-latest"


def test_level4_paid_credit_flow_uses_wallet_account_and_worker_claim(monkeypatch) -> None:
    smoke = load_smoke_client()
    calls: list[tuple[str, str, dict | None]] = []
    price = 1234

    def fake_http_json(method, url, *, body=None, timeout=10.0, allow_error=False):
        del timeout, allow_error
        calls.append((method, url, body))
        if url.endswith("/api/hub/v1/credits/admin/issue"):
            assert body["account_id"] == "0x2222222222222222222222222222222222222222"
            assert body["owner_address"] == "0x2222222222222222222222222222222222222222"
            return {"ok": True, "transaction": {"credits": body["credits"]}}
        if url.endswith("/api/hub/v1/workers/register"):
            return {"ok": True, "worker": {"offer": {"offer_id": "offer-1"}}}
        if url.endswith("/api/hub/v1/workers/heartbeat"):
            return {"ok": True, "worker": {"queue_depth": 1}}
        if url.endswith("/api/hub/v1/requests/quote"):
            return {"ok": True, "quote": {"quote_id": "quote-1", "quoted_credits": price}}
        if url.endswith("/api/hub/v1/requests"):
            return {"ok": True, "request": {"request_id": "request-1", "pricing": {"held_credits": price}}}
        if url.endswith("/api/hub/v1/workers/poll"):
            return {"ok": True, "lease": {"request_id": "request-1", "lease_id": "lease-1"}}
        if url.endswith("/api/hub/v1/workers/results"):
            return {"ok": True, "request": {"request_id": "request-1", "state": "completed", "charged_credits": price}}
        if url.endswith("/api/hub/v1/requests/request-1/charges"):
            return {"ok": True, "charge_count": 1, "charges": [{"charged_credits": price}]}
        if "/api/hub/v1/credits/worker-earnings?" in url:
            return {"ok": True, "worker_earning_count": 1, "earnings": [{"earned_credits": price}]}
        if url.endswith("/api/hub/v1/workers/claims"):
            assert body["claim_credits"] == price
            return {"ok": True, "claim": {"claim_id": "claim-1", "claim_credits": price}}
        if "/api/hub/v1/credits/balance?" in url:
            return {"ok": True, "account": {"balance_credits": "1000"}}
        raise AssertionError(url)

    monkeypatch.setattr(smoke, "http_json", fake_http_json)

    result = smoke.run_level4_paid_credit_flow(
        hub_url="http://127.0.0.1:8780",
        network="test",
        smoke_wallet_address="0x2222222222222222222222222222222222222222",
        scope="unit",
        timeout=1.0,
        funding_credits=10_000,
        worker_price=price,
    )

    assert result["account_id"] == "0x2222222222222222222222222222222222222222"
    assert result["charged_credits"] == price
    assert result["charge_count"] == 1
    assert result["worker_earning_count"] == 1
    assert result["claim"]["claim"]["claim_id"] == "claim-1"
    assert any(url.endswith("/api/hub/v1/workers/claims") for _method, url, _body in calls)
