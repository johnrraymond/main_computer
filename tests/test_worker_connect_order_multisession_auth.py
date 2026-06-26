from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
from main_computer.multisession_key_signing import (
    build_personal_sign_blob,
    sign_personal_message,
)
from main_computer.viewport_server import ViewportServer


_TEST_WORKER_PRIVATE_KEY = 1
_TEST_WORKER_WALLET = "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf"
_DEV_CHAIN_ID_HEX = "0x28757b2"


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


def _post_json_error(url: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            assert isinstance(data, dict)
            return int(response.status), data
    except HTTPError as exc:
        data = json.loads(exc.read().decode("utf-8"))
        assert isinstance(data, dict)
        return exc.code, data


def _signed_msk_blob(*, request_id: str = "worker_ui_connect_msk") -> dict[str, object]:
    message = {
        "purpose": "request_multi_session_key",
        "request_id": request_id,
        "wallet_address": _TEST_WORKER_WALLET,
        "chain_id": _DEV_CHAIN_ID_HEX,
        "origin": "worker-ui-connect-pytest",
        "version": "main-computer-multisession-key-request-v1",
    }
    return build_personal_sign_blob(message=message, private_key=_TEST_WORKER_PRIVATE_KEY, chain_id=_DEV_CHAIN_ID_HEX)


def _worker_connect_message(
    *,
    hub_url: str,
    network: str = "dev",
    requested_ring: str = "3",
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


def _worker_payload(worker_node_id: str) -> dict[str, object]:
    return {
        "node_id": worker_node_id,
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
        "availability": {"only_when_idle": False},
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
    }


def _signed_worker_connect_payload(
    *,
    hub_url: str,
    worker_node_id: str = "signed-ui-worker-001",
    requested_ring: str = "3",
    key_id: str = "",
) -> dict[str, object]:
    message = _worker_connect_message(
        hub_url=hub_url,
        worker_node_id=worker_node_id,
        requested_ring=requested_ring,
    )
    payload: dict[str, object] = {
        "signed_connection": {
            "network": "dev",
            "requested_ring": requested_ring,
            "wallet_address": _TEST_WORKER_WALLET,
            "credit_wallet": _TEST_WORKER_WALLET,
            "hub_url": hub_url,
            "chain_id": "42424242",
            "message": message,
            "signature": sign_personal_message(message, _TEST_WORKER_PRIVATE_KEY),
        },
        "worker": _worker_payload(worker_node_id),
    }
    if key_id:
        payload["multisession_authorization"] = {
            "kind": "multisession_key",
            "wallet_address": _TEST_WORKER_WALLET,
            "multisession_key_id": key_id,
            "key_id": key_id,
            "chain_id": _DEV_CHAIN_ID_HEX,
        }
    return payload


def _write_dev_network_config(root: Path, *, hub_url: str, hub_port: int) -> Path:
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
                        "hub_bind_port": hub_port,
                        "hub_public_url": hub_url,
                        "hub_runtime_dir": str(root / "hub"),
                        "deployment_manifest_path": "runtime/deployments/dev/latest.json",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return networks_path


def test_hub_worker_connect_requires_multisession_auth_in_strict_mode() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hub_config = MainComputerConfig(
            workspace=root / "workspace",
            hub_root=root / "hub",
            hub_bridge_backend="mock-chain",
            hub_allow_insecure_dev_network=False,
            hub_require_multisession_auth=True,
        )
        hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
        hub_thread = threading.Thread(target=hub.serve_forever, daemon=True)
        hub_thread.start()

        try:
            hub_url = f"http://127.0.0.1:{hub.server_port}"
            unsigned_status, unsigned_payload = _post_json_error(
                f"{hub_url}/api/hub/v1/workers/connect",
                _signed_worker_connect_payload(hub_url=hub_url, worker_node_id="strict-connect-no-msk"),
            )
            assert unsigned_status == 403
            assert "multi-session key" in str(unsigned_payload.get("error", ""))

            key_response = _post_json(
                f"{hub_url}/api/hub/v1/credits/multisession-keys/request",
                {"signed_request": _signed_msk_blob(request_id="strict_worker_connect")},
            )
            key = key_response["key"]
            assert isinstance(key, dict)
            key_id = str(key["id"])

            accepted = _post_json(
                f"{hub_url}/api/hub/v1/workers/connect",
                _signed_worker_connect_payload(
                    hub_url=hub_url,
                    worker_node_id="strict-connect-with-msk",
                    key_id=key_id,
                ),
            )

            assert accepted["ok"] is True
            worker = accepted["worker"]
            assert isinstance(worker, dict)
            assert worker["node_id"] == "strict-connect-with-msk"
            assert worker["multisession_key_authorized"] is True
            assert worker["multisession_key_id"] == key_id
            capabilities = worker["capabilities"]
            assert capabilities["multisession_key_authorized"] is True
            assert capabilities["multisession_key_id"] == key_id
            assert capabilities["auth_mode"] == "multisession-wallet"
            assert capabilities["credit_wallet"] == _TEST_WORKER_WALLET
        finally:
            hub.shutdown()
            hub.server_close()
            hub_thread.join(timeout=2)


def test_worker_connect_order_proxy_attaches_cached_multisession_key() -> None:
    previous_networks_file = os.environ.get("MAIN_COMPUTER_HUB_NETWORKS_FILE")
    previous_cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hub: HubHttpServer | None = None
        viewport: ViewportServer | None = None
        hub_thread: threading.Thread | None = None
        viewport_thread: threading.Thread | None = None
        try:
            os.chdir(root)
            hub_config = MainComputerConfig(
                workspace=root / "workspace",
                hub_root=root / "hub",
                hub_bridge_backend="mock-chain",
                hub_allow_insecure_dev_network=False,
                hub_require_multisession_auth=True,
            )
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            hub_thread = threading.Thread(target=hub.serve_forever, daemon=True)
            hub_thread.start()

            hub_url = f"http://127.0.0.1:{hub.server_port}"
            os.environ["MAIN_COMPUTER_HUB_NETWORKS_FILE"] = str(
                _write_dev_network_config(root, hub_url=hub_url, hub_port=hub.server_port)
            )

            viewport_config = MainComputerConfig(workspace=root / "workspace", hub_url=hub_url)
            viewport = ViewportServer(("127.0.0.1", 0), viewport_config, verbose=False)
            viewport_thread = threading.Thread(target=viewport.serve_forever, daemon=True)
            viewport_thread.start()

            viewport_url = f"http://127.0.0.1:{viewport.server_port}"
            selected = _post_json(
                f"{viewport_url}/api/applications/worker/network-session",
                {"network": "dev", "requested_ring": "3"},
            )
            assert selected["session"]["connection_status"] == "connected"  # type: ignore[index]

            key_result = _post_json(
                f"{viewport_url}/api/applications/worker/multisession-key/request",
                {
                    "hub_url": hub_url,
                    "signed_request": _signed_msk_blob(request_id="viewport_cached_connect"),
                    "client_metadata": {"source": "pytest"},
                },
            )
            key = key_result["key"]
            assert isinstance(key, dict)
            key_id = str(key["id"])

            worker_node_id = "strict-ui-worker-001"
            message = _worker_connect_message(hub_url=hub_url, worker_node_id=worker_node_id)
            data = _post_json(
                f"{viewport_url}/api/applications/worker/network-connect-order",
                {
                    "hub_url": hub_url,
                    "network": "dev",
                    "requested_ring": "3",
                    "wallet_address": _TEST_WORKER_WALLET,
                    "message": message,
                    "signature": sign_personal_message(message, _TEST_WORKER_PRIVATE_KEY),
                    "worker": _worker_payload(worker_node_id),
                },
            )

            signed = data["session"]["signed_connection"]  # type: ignore[index]
            assert signed["status"] == "hub-registered"
            registration = signed["hub_registration"]
            worker = registration["worker"]
            assert worker["multisession_key_authorized"] is True
            assert worker["multisession_key_id"] == key_id
            assert worker["capabilities"]["auth_mode"] == "multisession-wallet"
        finally:
            if viewport is not None:
                viewport.shutdown()
                viewport.server_close()
            if hub is not None:
                hub.shutdown()
                hub.server_close()
            if viewport_thread is not None:
                viewport_thread.join(timeout=2)
            if hub_thread is not None:
                hub_thread.join(timeout=2)
            os.chdir(previous_cwd)
            if previous_networks_file is None:
                os.environ.pop("MAIN_COMPUTER_HUB_NETWORKS_FILE", None)
            else:
                os.environ["MAIN_COMPUTER_HUB_NETWORKS_FILE"] = previous_networks_file


def test_worker_connect_order_proxy_reports_stale_saved_multisession_key_clearly() -> None:
    previous_networks_file = os.environ.get("MAIN_COMPUTER_HUB_NETWORKS_FILE")
    previous_cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hub: HubHttpServer | None = None
        viewport: ViewportServer | None = None
        hub_thread: threading.Thread | None = None
        viewport_thread: threading.Thread | None = None
        try:
            os.chdir(root)
            hub_config = MainComputerConfig(
                workspace=root / "workspace",
                hub_root=root / "hub",
                hub_bridge_backend="mock-chain",
                hub_allow_insecure_dev_network=False,
                hub_require_multisession_auth=True,
            )
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            hub_thread = threading.Thread(target=hub.serve_forever, daemon=True)
            hub_thread.start()

            hub_url = f"http://127.0.0.1:{hub.server_port}"
            os.environ["MAIN_COMPUTER_HUB_NETWORKS_FILE"] = str(
                _write_dev_network_config(root, hub_url=hub_url, hub_port=hub.server_port)
            )

            cache_path = root / ".main_computer" / "worker_multisession_keys.json"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "version": "main-computer-worker-multisession-key-cache-v1",
                        "keys": {
                            "msk_missing_on_hub": {
                                "id": "msk_missing_on_hub",
                                "status": "active",
                                "wallet_address": _TEST_WORKER_WALLET,
                                "chain_id": _DEV_CHAIN_ID_HEX,
                                "hub_url": hub_url,
                                "created_at": "2026-01-01T00:00:00+00:00",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            viewport_config = MainComputerConfig(workspace=root / "workspace", hub_url=hub_url)
            viewport = ViewportServer(("127.0.0.1", 0), viewport_config, verbose=False)
            viewport_thread = threading.Thread(target=viewport.serve_forever, daemon=True)
            viewport_thread.start()

            viewport_url = f"http://127.0.0.1:{viewport.server_port}"
            selected = _post_json(
                f"{viewport_url}/api/applications/worker/network-session",
                {"network": "dev", "requested_ring": "3"},
            )
            assert selected["session"]["connection_status"] == "connected"  # type: ignore[index]

            worker_node_id = "stale-msk-ui-worker-001"
            message = _worker_connect_message(hub_url=hub_url, worker_node_id=worker_node_id)
            status, payload = _post_json_error(
                f"{viewport_url}/api/applications/worker/network-connect-order",
                {
                    "hub_url": hub_url,
                    "network": "dev",
                    "requested_ring": "3",
                    "wallet_address": _TEST_WORKER_WALLET,
                    "message": message,
                    "signature": sign_personal_message(message, _TEST_WORKER_PRIVATE_KEY),
                    "active_multisession_key_id": "msk_missing_on_hub",
                    "worker": _worker_payload(worker_node_id),
                },
            )

            assert status == 400
            error = str(payload.get("error", ""))
            assert "saved multi-session key is not active on this Hub" in error
            assert "Request a new multi-session key" in error
            cache_after = json.loads(cache_path.read_text(encoding="utf-8"))
            stale_record = cache_after["keys"]["msk_missing_on_hub"]
            assert stale_record["status"] == "inactive_on_hub"
            assert "saved multi-session key is not active on this Hub" in stale_record["last_error"]
        finally:
            if viewport is not None:
                viewport.shutdown()
                viewport.server_close()
            if hub is not None:
                hub.shutdown()
                hub.server_close()
            if viewport_thread is not None:
                viewport_thread.join(timeout=2)
            if hub_thread is not None:
                hub_thread.join(timeout=2)
            os.chdir(previous_cwd)
            if previous_networks_file is None:
                os.environ.pop("MAIN_COMPUTER_HUB_NETWORKS_FILE", None)
            else:
                os.environ["MAIN_COMPUTER_HUB_NETWORKS_FILE"] = previous_networks_file
