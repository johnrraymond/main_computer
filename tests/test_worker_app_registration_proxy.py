from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
from main_computer.multisession_key_signing import build_personal_sign_blob
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


_DEV_CHAIN_ID_HEX = "0x28757b2"


def _signed_msk_blob(*, request_id: str = "worker_app_registration_proxy_msk") -> dict[str, object]:
    message = {
        "purpose": "request_multi_session_key",
        "request_id": request_id,
        "wallet_address": _TEST_WORKER_WALLET,
        "chain_id": _DEV_CHAIN_ID_HEX,
        "origin": "worker-app-registration-proxy-pytest",
        "version": "main-computer-multisession-key-request-v1",
    }
    return build_personal_sign_blob(message=message, private_key=_TEST_WORKER_PRIVATE_KEY, chain_id=_DEV_CHAIN_ID_HEX)


def test_worker_app_proxy_registers_phase12_seller_offer_with_hub() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hub_config = MainComputerConfig(
            workspace=root / "workspace",
            hub_root=root / "hub",
            hub_bridge_backend="mock-chain",
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
                    "availability": {
                        "accept_paid_jobs": True,
                        "availability_mode": "ai_idle",
                        "only_when_idle": False,
                        "idle_source": "local_ai_capacity_v1",
                        "ai_idle_required": True,
                    },
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
            pricing = worker["capabilities"]["pricing"]
            assert pricing["pricing_type"] == "approx_per_token_v0"
            assert pricing["credits_per_token"] == "0.001"
            assert pricing["credits_per_token_wei"] == "1000000000000000"
            assert pricing["estimated_credits_per_request"] == "1.024"
            assert pricing["estimated_credits_per_request_wei"] == "1024000000000000000"
            assert worker["credits_per_request"] == "1.024"
            assert pricing["credits_per_request_wei"] == "1024000000000000000"
            assert pricing["target_output_tokens"] == 1024
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


def test_worker_start_proxy_registers_msk_authorized_worker_with_selected_hub() -> None:
    previous_networks_file = os.environ.get("MAIN_COMPUTER_HUB_NETWORKS_FILE")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hub_config = MainComputerConfig(
            workspace=root / "workspace",
            hub_root=root / "hub",
            hub_bridge_backend="mock-chain",
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

            key_result = _post_json(
                f"{viewport_url}/api/applications/worker/multisession-key/request",
                {
                    "hub_url": hub_url,
                    "signed_request": _signed_msk_blob(request_id="worker_app_start_working"),
                    "client_metadata": {"source": "pytest"},
                },
            )
            key = key_result["key"]
            assert isinstance(key, dict)
            key_id = str(key["id"])

            data = _post_json(
                f"{viewport_url}/api/applications/worker/work-now",
                {
                    "hub_url": hub_url,
                    "network": "dev",
                    "chain_id": "42424242",
                    "requested_ring": "2",
                    "duration_seconds": 900,
                    "wallet_address": _TEST_WORKER_WALLET,
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
                        "availability": {
                            "accept_paid_jobs": True,
                            "availability_mode": "ai_idle",
                            "only_when_idle": False,
                            "idle_source": "local_ai_capacity_v1",
                            "ai_idle_required": True,
                        },
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
            assert session["assigned_ring"] == "3"  # type: ignore[index]
            assert session["worker_id"] == "signed-ui-worker-001"  # type: ignore[index]
            signed = session["signed_connection"]  # type: ignore[index]
            assert signed["status"] == "hub-registered"
            assert signed["hub_registered"] is True
            assert signed["worker_start_status"] == "ready"
            assert signed["pricing_policy"] == "public-dev"
            assert signed["multisession_key_id"] == key_id
            assert signed["worker"]["wallet_address"] == _TEST_WORKER_WALLET
            assert signed["pool"]["worker_count"] == 1
            assert signed["pool"]["available_worker_count"] == 1
            assert signed["pool"]["ring_worker_count"] == 0
        finally:
            viewport.shutdown()
            hub.shutdown()
            viewport.server_close()
            hub.server_close()
            if previous_networks_file is None:
                os.environ.pop("MAIN_COMPUTER_HUB_NETWORKS_FILE", None)
            else:
                os.environ["MAIN_COMPUTER_HUB_NETWORKS_FILE"] = previous_networks_file
