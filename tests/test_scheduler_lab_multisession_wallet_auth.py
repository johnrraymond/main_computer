from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.multisession_key_signing import build_personal_sign_blob, private_key_to_address
from tools.scheduler_lab.hub_client import HubClient
from tools.scheduler_lab.http_transport import HubHttpResponse, HubTransport


_DEV_CHAIN_ID = "0x28757b2"
_TEST_PRIVATE_KEY = "0x" + "1" * 64


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
            return response.status, data
    except HTTPError as exc:
        data = json.loads(exc.read().decode("utf-8"))
        assert isinstance(data, dict)
        return exc.code, data


def _signed_msk_blob(private_key: str = _TEST_PRIVATE_KEY, *, request_id: str = "msk_req_scheduler_lab_unit") -> dict[str, object]:
    wallet_address = private_key_to_address(private_key)
    message = {
        "purpose": "request_multi_session_key",
        "request_id": request_id,
        "wallet_address": wallet_address,
        "chain_id": _DEV_CHAIN_ID,
        "origin": "scheduler-lab-pytest",
        "version": "main-computer-multisession-key-request-v1",
    }
    return build_personal_sign_blob(message=message, private_key=private_key, chain_id=_DEV_CHAIN_ID)


def _worker_registration_payload(worker_id: str, wallet_address: str, key_id: str) -> dict[str, object]:
    return {
        "node_id": worker_id,
        "endpoint": f"http://127.0.0.1:1/{worker_id}",
        "model": "mock-ai-model-phase9",
        "models": ["mock-ai-model-phase9"],
        "credits_per_request": 1,
        "queue_depth": 0,
        "active_requests": 0,
        "max_concurrency": 1,
        "pricing": {
            "pricing_type": "fixed_per_call_v0",
            "credits_per_request": 1,
            "minimum_accepted_credits": 1,
            "unit": "compute_credit",
            "execution_mode": "worker_pull_v0",
        },
        "execution": {
            "mode": "worker_pull_v0",
            "lab_node": True,
        },
        "capabilities": {
            "scheduler_lab": True,
            "ring": 2,
        },
        "multisession_authorization": {
            "kind": "multisession_key",
            "wallet_address": wallet_address,
            "multisession_key_id": key_id,
            "chain_id": _DEV_CHAIN_ID,
        },
    }


def test_hub_strict_mode_rejects_unsigned_worker_pull_and_derives_account_from_multisession_key() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hub_config = MainComputerConfig(
            workspace=root / "workspace",
            hub_root=root / "hub",
            hub_bridge_backend="mock-chain",
            hub_require_multisession_auth=True,
            hub_allow_insecure_dev_network=False,
        )
        hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
        hub_thread = threading.Thread(target=hub.serve_forever, daemon=True)
        hub_thread.start()

        hub_url = f"http://127.0.0.1:{hub.server_port}"
        wallet_address = private_key_to_address(_TEST_PRIVATE_KEY)
        expected_account_id = wallet_account_id(wallet_address)

        try:
            status, error = _post_json_error(
                f"{hub_url}/api/hub/v1/requests",
                {
                    "messages": [{"role": "user", "content": "hello"}],
                    "model": "mock-ai-model-phase9",
                    "client_node_id": "requester-unsigned",
                    "idempotency_key": "unsigned-worker-pull",
                    "execution_mode": "worker_pull_v0",
                    "account_id": expected_account_id,
                    "max_credits": 1,
                    "metadata": {"worker_pull_v0": True},
                },
            )
            assert status == 403
            assert "multi-session key" in str(error["error"])

            key_payload = _post_json(
                f"{hub_url}/api/hub/v1/credits/multisession-keys/request",
                {"signed_request": _signed_msk_blob()},
            )
            key_id = str(key_payload["key"]["id"])  # type: ignore[index]

            registered_worker = _post_json(
                f"{hub_url}/api/hub/v1/workers/register",
                _worker_registration_payload("lab-worker-for-request", wallet_address, key_id),
            )
            assert registered_worker["ok"] is True

            funding = _post_json(
                f"{hub_url}/api/hub/v1/credits/wallet-funding/import",
                {
                    "wallet_address": wallet_address,
                    "chain_id": int(_DEV_CHAIN_ID, 16),
                    "contract_address": "0x0000000000000000000000000000000000000001",
                    "tx_hash": "0x" + "ab" * 32,
                    "log_index": 0,
                    "block_number": 1,
                    "payment_asset": "native",
                    "payment_amount_base_units": 2,
                    "credits_granted_wei": 2 * 10**18,
                    "idempotency_key": "wallet-funding-scheduler-lab-unit",
                    "memo": "pytest wallet funding",
                },
            )
            assert funding["account_id"] == expected_account_id

            data = _post_json(
                f"{hub_url}/api/hub/v1/requests",
                {
                    "messages": [{"role": "user", "content": "hello"}],
                    "model": "mock-ai-model-phase9",
                    "client_node_id": "requester-signed",
                    "idempotency_key": "signed-worker-pull",
                    "execution_mode": "worker_pull_v0",
                    "account_id": "attacker-supplied-account",
                    "max_credits": 1,
                    "metadata": {"worker_pull_v0": True},
                    "multisession_authorization": {
                        "kind": "multisession_key",
                        "wallet_address": wallet_address,
                        "multisession_key_id": key_id,
                        "chain_id": _DEV_CHAIN_ID,
                        "max_authorized_credits": 1,
                    },
                },
            )
            assert data["ok"] is True
            assert data["request"]["account_id"] == expected_account_id  # type: ignore[index]
            assert data["request"]["hold_id"]  # type: ignore[index]
        finally:
            hub.shutdown()
            hub.server_close()


def test_hub_strict_worker_routes_require_multisession_key_and_bind_registered_wallet() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hub_config = MainComputerConfig(
            workspace=root / "workspace",
            hub_root=root / "hub",
            hub_bridge_backend="mock-chain",
            hub_require_multisession_auth=True,
            hub_allow_insecure_dev_network=False,
        )
        hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
        hub_thread = threading.Thread(target=hub.serve_forever, daemon=True)
        hub_thread.start()

        hub_url = f"http://127.0.0.1:{hub.server_port}"
        wallet_address = private_key_to_address(_TEST_PRIVATE_KEY)

        try:
            status, error = _post_json_error(
                f"{hub_url}/api/hub/v1/workers/register",
                {
                    "node_id": "lab-worker-unsigned",
                    "model": "mock-ai-model-phase9",
                    "capabilities": {"scheduler_lab": True},
                },
            )
            assert status == 403
            assert "multi-session key" in str(error["error"])

            key_payload = _post_json(
                f"{hub_url}/api/hub/v1/credits/multisession-keys/request",
                {"signed_request": _signed_msk_blob(request_id="msk_req_worker_unit")},
            )
            key_id = str(key_payload["key"]["id"])  # type: ignore[index]

            registered = _post_json(
                f"{hub_url}/api/hub/v1/workers/register",
                _worker_registration_payload("lab-worker-signed", wallet_address, key_id),
            )
            assert registered["ok"] is True
            worker = registered["worker"]  # type: ignore[index]
            assert worker["capabilities"]["wallet_address"] == wallet_address  # type: ignore[index]
            assert worker["capabilities"]["multisession_key_id"] == key_id  # type: ignore[index]

            heartbeat = _post_json(
                f"{hub_url}/api/hub/v1/workers/heartbeat",
                {
                    "worker_node_id": "lab-worker-signed",
                    "status": "available",
                    "multisession_authorization": {
                        "kind": "multisession_key",
                        "wallet_address": wallet_address,
                        "multisession_key_id": key_id,
                        "chain_id": _DEV_CHAIN_ID,
                    },
                },
            )
            assert heartbeat["ok"] is True
        finally:
            hub.shutdown()
            hub.server_close()


class _CaptureTransport(HubTransport):
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, object] | None]] = []

    def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, object] | None = None,
        timeout_seconds: float,
        headers: dict[str, str] | None = None,
    ) -> HubHttpResponse:
        self.requests.append((method, url, payload))
        return HubHttpResponse(ok=True, status=200, payload={"ok": True}, elapsed_ms=0.0, base_url="http://hub.test")


def test_scheduler_lab_client_uses_multisession_authorization_for_wallet_nodes() -> None:
    transport = _CaptureTransport()
    client = HubClient("http://hub.test", transport=transport)
    node = {
        "node_id": "requester-001",
        "role": "requester",
        "model": "mock-ai-model-phase9",
        "offered_credits": 3,
        "_wallet_address": "0x19e7e376e7c213b7e7e7e46cc70a5dd086daff2a",
        "_multisession_key_id": "msk_unit",
        "_multisession_chain_id": _DEV_CHAIN_ID,
        "account_id": "attacker-supplied-account",
    }

    client.submit_request(
        node,
        request_index=7,
        request_mode="worker_pull_v0",
        account_id_prefix="lab-requester",
        prompt="hello",
        scheduler_lab_run_id="run-unit",
    )

    _method, url, payload = transport.requests[-1]
    assert url.endswith("/api/hub/v1/requests")
    assert payload is not None
    assert payload["account_id"] == "attacker-supplied-account"
    assert payload["multisession_authorization"]["wallet_address"] == node["_wallet_address"]  # type: ignore[index]
    assert payload["multisession_authorization"]["multisession_key_id"] == "msk_unit"  # type: ignore[index]
    assert payload["payment_authorization"] == payload["multisession_authorization"]
    assert payload["metadata"]["auth_mode"] == "multisession-wallet"  # type: ignore[index]
