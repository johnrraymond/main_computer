from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_canvas_module():
    path = Path(__file__).resolve().parents[1] / "micro_agent_canvas.py"
    spec = importlib.util.spec_from_file_location("micro_agent_canvas_for_tests", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_market_ring_accepts_worker_ui_values():
    canvas = _load_canvas_module()

    assert canvas.normalize_market_ring("3") == "ring-3"
    assert canvas.normalize_market_ring("ring-3") == "ring-3"
    assert canvas.normalize_market_ring("Ring 3 - Public untrusted") == "ring-3"


def test_default_work_payload_matches_worker_ui_market_contract():
    canvas = _load_canvas_module()
    args = SimpleNamespace(
        prompt="hello",
        ring="3",
        capability=None,
        client_node_id="micro-agent-local",
        model="micro-agent-local",
        max_credits="2",
        accept_timeout=10.0,
    )

    payload = canvas.build_work_payload(
        args,
        authorization={},
        hub_status={
            "ok": True,
            "network": {
                "network_key": "dev",
                "chain_id": 42424242,
            },
            "serving_hub": {
                "hub_id": "dev-hub1",
            },
        },
    )

    assert payload["ring"] == "ring-3"
    assert payload["partition"] == "ring-3"
    assert payload["capabilities"] == ["chat.completions"]
    assert payload["required_capabilities"] == ["chat.completions"]
    assert payload["max_price"] == {"amount": "2", "unit": "compute_credit"}
    assert payload["input"]["kind"] == "chat.completions"
    assert payload["metadata"]["hub_chain_id"] == "42424242"


def test_status_summary_handles_stable_topology_shape():
    canvas = _load_canvas_module()

    summary = canvas.extract_hub_status_summary(
        {
            "ok": True,
            "hub_id": {
                "hub_id": "dev-hub1",
                "display_name": "dev-hub1",
                "hub_url": "http://127.0.0.1:8871",
            },
            "serving_hub": {
                "hub_id": "dev-hub1",
            },
            "network": {
                "network_key": "dev",
                "chain_id": "0x28757b2",
            },
            "backend": "foundationdb",
        }
    )

    assert summary["hub_id"] == "dev-hub1"
    assert summary["serving_hub"] == "dev-hub1"
    assert summary["network_key"] == "dev"
    assert summary["chain_id"] == "42424242"
    assert summary["backend"] == "foundationdb"


def test_build_multisession_key_message_is_signed_request_contract():
    canvas = _load_canvas_module()
    from main_computer.multisession_key_signing import private_key_to_address, verify_personal_sign_blob

    private_key = "0x1"
    wallet = private_key_to_address(private_key)
    message = canvas.build_multisession_key_message(
        wallet_address=wallet,
        chain_id="42424242",
        hub_url="http://127.0.0.1:8871",
    )
    blob = canvas.build_personal_sign_blob(
        message=message,
        private_key=private_key,
        wallet_address=wallet,
        chain_id="42424242",
    )

    assert message["purpose"] == "request_multi_session_key"
    assert message["user_slug"].startswith("usr_")
    assert len(message["user_slug"]) >= 32
    assert message["origin"] == "micro-agent-canvas:http://127.0.0.1:8871"

    verified = verify_personal_sign_blob(blob, expected_chain_id="0x28757b2", max_age_minutes=15)
    assert verified["ok"] is True
    assert verified["wallet_address"] == wallet
    assert verified["message"]["user_slug"] == message["user_slug"]


def test_request_fresh_multisession_authorization_posts_request_then_validate(monkeypatch):
    canvas = _load_canvas_module()
    from main_computer.multisession_key_signing import private_key_to_address

    private_key = "0x1"
    wallet = private_key_to_address(private_key)
    calls = []

    def fake_http_json(method, url, payload=None, timeout=15.0):
        calls.append((method, url, payload))
        if url.endswith("/api/hub/v1/credits/multisession-keys/request"):
            signed = payload["signed_request"]
            assert signed["kind"] == "main_computer_multisession_key_request"
            assert signed["message"]["purpose"] == "request_multi_session_key"
            assert signed["message"]["user_slug"].startswith("usr_")
            return 200, {
                "ok": True,
                "cluster_id": "main-computer-dev-stable-hub",
                "key": {"id": "msk_new_active", "status": "active"},
                "multisession_authorization": {
                    "kind": "multisession_key",
                    "multisession_key_id": "msk_new_active",
                    "key_id": "msk_new_active",
                    "chain_id": "42424242",
                },
            }
        if url.endswith("/api/hub/v1/credits/wallet-funding/import"):
            assert payload["wallet_address"] == wallet
            assert payload["chain_id"] == 42424242
            assert payload["credits_granted_wei"] == "2000000000000000000"
            return 200, {
                "ok": True,
                "idempotent": False,
                "account": {"available_credit_wei": "2000000000000000000"},
            }
        if url.endswith("/api/hub/v1/credits/multisession-keys/validate"):
            assert payload["wallet_address"] == wallet
            assert payload["required_credit_wei"] == "2000000000000000000"
            assert payload["multisession_authorization"]["multisession_key_id"] == "msk_new_active"
            assert payload["payment_authorization"]["wallet_address"] == wallet
            return 200, {"ok": True, "valid": True, "ready": True}
        raise AssertionError(url)

    monkeypatch.setattr(canvas, "http_json", fake_http_json)
    args = SimpleNamespace(
        private_key=private_key,
        private_key_file="",
        wallet="",
        max_credits="2",
        msk_lifetime_minutes=10,
    )

    auth = canvas.request_fresh_multisession_authorization(
        args=args,
        hub_url="http://127.0.0.1:8871",
        hub_status={
            "ok": True,
            "network": {"network_key": "dev", "chain_id": "42424242"},
            "serving_hub": {"hub_id": "dev-hub1"},
        },
        settings={},
    )

    assert auth["wallet_address"] == wallet
    assert auth["multisession_key_id"] == "msk_new_active"
    assert auth["key_id"] == "msk_new_active"
    assert auth["chain_id"] == "42424242"
    assert auth["max_authorized_credit_wei"] == "2000000000000000000"
    assert [call[1].rsplit("/", 1)[-1] for call in calls] == ["request", "import", "validate"]


def test_resolve_requester_private_key_prefers_deployment_smoke_client_wallet(tmp_path: Path, monkeypatch) -> None:
    canvas = _load_canvas_module()
    from main_computer.multisession_key_signing import private_key_to_address

    root = tmp_path
    wallet_dir = root / "runtime" / "deployments" / "dev"
    wallet_dir.mkdir(parents=True)
    private_key = "0x" + "1".zfill(64)
    address = private_key_to_address(private_key)
    wallet_path = wallet_dir / "smoke-client-wallet-42424242.json"
    wallet_path.write_text(
        __import__("json").dumps(
            {
                "schema": "main-computer.smoke-client-wallet.v1",
                "chain_id": 42424242,
                "address": address,
                "private_key": private_key,
                "source": "generated-local-smoke-client",
            }
        ),
        encoding="utf-8",
    )
    (wallet_dir / "latest.json").write_text(
        __import__("json").dumps(
            {
                "schema": "main-computer.deployment.v1",
                "environment": "dev",
                "chain": {"chain_id": 42424242},
                "smoke_client": {
                    "address": address,
                    "wallet_path": "runtime/deployments/dev/smoke-client-wallet-42424242.json",
                    "source": "generated-local-smoke-client",
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("MICRO_AGENT_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_MICRO_AGENT_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_SMOKE_CLIENT_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_PAID_REQUESTER_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_REQUESTER_0_PRIVATE_KEY", raising=False)

    args = SimpleNamespace(private_key="", private_key_file="")
    resolved = canvas.resolve_requester_private_key(
        args,
        hub_status={
            "ok": True,
            "network": {"network_key": "dev", "chain_id": "42424242"},
            "serving_hub": {"hub_id": "dev-hub1"},
        },
        settings={},
        root=root,
    )

    assert resolved.private_key == private_key
    assert resolved.wallet_address == address
    assert resolved.source == "deployment smoke-client wallet"
    assert resolved.path.endswith("runtime/deployments/dev/smoke-client-wallet-42424242.json")
