from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
from main_computer.multisession_key_signing import (
    _SECP256K1_G,
    _SECP256K1_N,
    _inverse,
    _point_multiply,
    ethereum_address_from_public_key_xy,
    keccak256,
    personal_sign_message_hash,
    recover_personal_sign_address,
)
from main_computer.viewport_server import ViewportServer


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        data = json.loads(response.read().decode("utf-8"))
    assert isinstance(data, dict)
    return data



_TEST_WORKER_PRIVATE_KEY = 1
_TEST_WORKER_WALLET = "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf"


def _sign_personal_message(message_text: str, private_key: int = _TEST_WORKER_PRIVATE_KEY) -> str:
    message_hash = personal_sign_message_hash(message_text)
    digest = int.from_bytes(message_hash, "big")
    seed = keccak256(int(private_key).to_bytes(32, "big") + message_hash)
    nonce = int.from_bytes(seed, "big") % _SECP256K1_N or 1
    while True:
        point = _point_multiply(nonce, _SECP256K1_G)
        assert point is not None
        r = point[0] % _SECP256K1_N
        if r:
            s = (_inverse(nonce, _SECP256K1_N) * (digest + r * private_key)) % _SECP256K1_N
            if s:
                recovery_id = (point[1] & 1) | (2 if point[0] >= _SECP256K1_N else 0)
                if s > _SECP256K1_N // 2:
                    s = _SECP256K1_N - s
                    recovery_id ^= 1
                signature = "0x" + r.to_bytes(32, "big").hex() + s.to_bytes(32, "big").hex() + bytes([27 + recovery_id]).hex()
                assert recover_personal_sign_address(message_text, signature) == ethereum_address_from_public_key_xy(
                    *_point_multiply(private_key, _SECP256K1_G)  # type: ignore[arg-type]
                )
                return signature
        nonce = (nonce + 1) % _SECP256K1_N or 1


def _worker_connect_message(
    *,
    hub_url: str,
    network: str = "dev",
    requested_ring: str = "2",
    worker_node_id: str = "signed-ui-worker-001",
) -> str:
    return json.dumps(
        {
            "kind": "main_computer_worker_connect_order",
            "purpose": "connect_worker_to_hub",
            "version": "main-computer-worker-connect-order-v1",
            "network": network,
            "hub_url": hub_url,
            "chain_id": "42424242",
            "requested_ring": requested_ring,
            "wallet_address": _TEST_WORKER_WALLET,
            "credit_wallet": _TEST_WORKER_WALLET,
            "worker_node_id": worker_node_id,
            "issued_at": "2026-01-01T00:00:00+00:00",
            "expires_at": "2999-01-01T00:00:00+00:00",
        },
        separators=(",", ":"),
    )


def test_worker_app_proxy_registers_phase12_seller_offer_with_hub() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hub_config = MainComputerConfig(
            workspace=root / "workspace",
            hub_root=root / "hub",
            hub_allow_insecure_dev_network=False,
        )
        hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
        hub_thread = threading.Thread(target=hub.serve_forever, daemon=True)
        hub_thread.start()

        hub_url = f"http://127.0.0.1:{hub.server_port}"
        viewport_config = MainComputerConfig(workspace=root / "workspace", hub_url=hub_url)
        viewport = ViewportServer(("127.0.0.1", 0), viewport_config, verbose=False)
        viewport_thread = threading.Thread(target=viewport.serve_forever, daemon=True)
        viewport_thread.start()

        try:
            payload = {
                "hub_url": hub_url,
                "worker": {
                    "node_id": "phase12-ui-worker-001",
                    "endpoint": "http://127.0.0.1:8771",
                    "model": "mock-ai-model-phase12",
                    "models": ["mock-ai-model-phase12"],
                    "credits_per_token": "0.001",
                    "credits_per_token_wei": "1000000000000000",
                    "estimated_credits_per_request": "1.024",
                    "estimated_credits_per_request_wei": "1024000000000000000",
                    "credits_per_request": "1.024",
                    "credits_per_request_wei": "1024000000000000000",
                    "target_output_tokens": 1024,
                    "max_concurrency": 1,
                    "pricing": {
                        "pricing_type": "approx_per_token_v0",
                        "credits_per_token": "0.001",
                        "credits_per_token_wei": "1000000000000000",
                        "target_output_tokens": 1024,
                        "estimated_credits_per_request": "1.024",
                        "estimated_credits_per_request_wei": "1024000000000000000",
                        "credits_per_request": "1.024",
                        "credits_per_request_wei": "1024000000000000000",
                        "unit": "compute_credit",
                    },
                    "execution": {
                        "mode": "worker_pull_v0",
                        "max_concurrency": 1,
                    },
                    "capabilities": {
                        "capabilities": ["chat.completions"],
                    },
                },
            }
            viewport_url = f"http://127.0.0.1:{viewport.server_port}"
            data = _post_json(f"{viewport_url}/api/applications/worker/register-offer", payload)

            assert data["ok"] is True
            worker = data["worker"]
            assert isinstance(worker, dict)
            assert worker["node_id"] == "phase12-ui-worker-001"
            assert worker["models"] == ["mock-ai-model-phase12"]
            assert worker["credits_per_token"] == "0.001"
            assert worker["credits_per_token_wei"] == "1000000000000000"
            assert worker["estimated_credits_per_request"] == "1.024"
            assert worker["estimated_credits_per_request_wei"] == "1024000000000000000"
            assert worker["credits_per_request"] == "1.024"
            assert worker["credits_per_request_wei"] == "1024000000000000000"
            assert worker["capabilities"]["pricing"]["pricing_type"] == "approx_per_token_v0"
            assert worker["capabilities"]["pricing"]["credits_per_token"] == "0.001"
            assert worker["capabilities"]["pricing"]["target_output_tokens"] == 1024
            assert worker["max_concurrency"] == 1

            offer = data["offer"]
            assert isinstance(offer, dict)
            assert offer["worker_node_id"] == "phase12-ui-worker-001"
            assert offer["pricing_type"] == "approx_per_token_v0"
            assert offer["credits_per_token"] == "0.001"
            assert offer["credits_per_token_wei"] == "1000000000000000"
            assert offer["credits_per_token_display"] == "0.001"
            assert offer["estimated_credits_per_request"] == "1.024"
            assert offer["estimated_credits_per_request_wei"] == "1024000000000000000"
            assert offer["credits_per_request"] == "1.024"
            assert offer["credits_per_request_wei"] == "1024000000000000000"
            assert offer["credits_per_request_display"] == "1.024"
            assert offer["target_output_tokens"] == 1024
            assert offer["unit"] == "compute_credit"
            assert offer["execution_mode"] == "worker_pull_v0"
            assert offer["price_source"] == "worker_registration"

            health = _post_json(f"{viewport_url}/api/applications/worker/hub-health", {"hub_url": hub_url})
            assert health["ok"] is True
            assert health["reachable"] is True
        finally:
            viewport.shutdown()
            hub.shutdown()
            viewport.server_close()
            hub.server_close()


def test_worker_connect_order_proxy_registers_signed_worker_with_selected_hub() -> None:
    previous_networks_file = os.environ.get("MAIN_COMPUTER_HUB_NETWORKS_FILE")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hub_config = MainComputerConfig(
            workspace=root / "workspace",
            hub_root=root / "hub",
            hub_allow_insecure_dev_network=False,
        )
        hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
        hub_thread = threading.Thread(target=hub.serve_forever, daemon=True)
        hub_thread.start()

        hub_url = f"http://127.0.0.1:{hub.server_port}"
        networks_path = root / "hub_networks.json"
        networks_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "default_network": "dev",
                    "networks": {
                        "dev": {
                            "display_name": "Unit Test Dev",
                            "kind": "dev",
                            "chain_id": 42424242,
                            "chain_rpc_url": "http://127.0.0.1:18545",
                            "hub_bind_host": "127.0.0.1",
                            "hub_bind_port": hub.server_port,
                            "hub_public_url": hub_url,
                            "hub_runtime_dir": str(root / "hub"),
                            "deployment_manifest_path": "runtime/deployments/dev/latest.json",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        os.environ["MAIN_COMPUTER_HUB_NETWORKS_FILE"] = str(networks_path)

        viewport_config = MainComputerConfig(workspace=root / "workspace", hub_url=hub_url)
        viewport = ViewportServer(("127.0.0.1", 0), viewport_config, verbose=False)
        viewport_thread = threading.Thread(target=viewport.serve_forever, daemon=True)
        viewport_thread.start()

        try:
            viewport_url = f"http://127.0.0.1:{viewport.server_port}"
            selected = _post_json(
                f"{viewport_url}/api/applications/worker/network-session",
                {"network": "dev", "requested_ring": "2"},
            )
            assert selected["session"]["connection_status"] == "connected"  # type: ignore[index]

            message = _worker_connect_message(hub_url=hub_url)
            signature = _sign_personal_message(message)
            data = _post_json(
                f"{viewport_url}/api/applications/worker/network-connect-order",
                {
                    "hub_url": hub_url,
                    "network": "dev",
                    "requested_ring": "2",
                    "wallet_address": _TEST_WORKER_WALLET,
                    "message": message,
                    "signature": signature,
                    "worker": {
                        "node_id": "signed-ui-worker-001",
                        "endpoint": "http://127.0.0.1:8771",
                        "model": "mock-ai-model-signed-worker",
                        "models": ["mock-ai-model-signed-worker"],
                        "credits_per_token": "0.001",
                        "credits_per_token_wei": "1000000000000000",
                        "estimated_credits_per_request": "1.024",
                        "estimated_credits_per_request_wei": "1024000000000000000",
                        "credits_per_request": "1.024",
                        "credits_per_request_wei": "1024000000000000000",
                        "target_output_tokens": 1024,
                        "max_concurrency": 1,
                        "pricing": {
                            "pricing_type": "approx_per_token_v0",
                            "credits_per_token": "0.001",
                            "credits_per_token_wei": "1000000000000000",
                            "target_output_tokens": 1024,
                            "estimated_credits_per_request": "1.024",
                            "estimated_credits_per_request_wei": "1024000000000000000",
                            "credits_per_request": "1.024",
                            "credits_per_request_wei": "1024000000000000000",
                            "unit": "compute_credit",
                        },
                        "execution": {
                            "mode": "worker_pull_v0",
                            "max_concurrency": 1,
                        },
                        "capabilities": {
                            "capabilities": ["chat.completions"],
                        },
                    },
                },
            )

            session = data["session"]
            assert session["assigned_ring"] == "2"  # type: ignore[index]
            assert session["worker_id"] == "signed-ui-worker-001"  # type: ignore[index]
            signed = session["signed_connection"]  # type: ignore[index]
            assert signed["status"] == "hub-registered"
            assert signed["hub_registered"] is True
            assert signed["pricing_policy"] == "public-dev"
            assert signed["worker"]["wallet_address"] == _TEST_WORKER_WALLET
            assert signed["pool"]["worker_count"] == 1
            assert signed["pool"]["available_worker_count"] == 1
            assert signed["pool"]["ring_worker_count"] == 1
        finally:
            viewport.shutdown()
            hub.shutdown()
            viewport.server_close()
            hub.server_close()
            if previous_networks_file is None:
                os.environ.pop("MAIN_COMPUTER_HUB_NETWORKS_FILE", None)
            else:
                os.environ["MAIN_COMPUTER_HUB_NETWORKS_FILE"] = previous_networks_file
