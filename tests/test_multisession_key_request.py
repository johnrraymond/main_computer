from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
from main_computer.multisession_key_signing import verify_personal_sign_blob
from main_computer.viewport_server import ViewportServer


_TEST_WALLET_ADDRESS = "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf"
_TEST_SIGNATURE = (
    "0x05a1b57b422dbc066283e928fd489a9040bd364b6431ba4a8ea5cf8837e808ed"
    "40fc4f43e64bb3a4851939b54ae729153b963d38e34bf8b96979942cc5fbf6321c"
)


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


def _signed_msk_blob(
    *,
    account_id: str = "bridge_local_test_wallet",
    request_id: str = "msk_req_unit_valid",
    tamper_wallet: bool = False,
) -> dict[str, object]:
    message = {
        "purpose": "request_multi_session_key",
        "request_id": request_id,
        "wallet_address": "0x1111111111111111111111111111111111111111" if tamper_wallet else _TEST_WALLET_ADDRESS,
        "chain_id": "0x28757b2",
        "bridge_account_id": account_id,
        "bridge_account_status": "prepared",
        "origin": "pytest",
        "version": "main-computer-multisession-key-request-v1",
    }
    message_text = json.dumps(message, separators=(",", ":"))
    return {
        "kind": "main_computer_multisession_key_request",
        "signing_method": "personal_sign",
        "wallet_address": _TEST_WALLET_ADDRESS,
        "chain_id": "0x28757b2",
        "message": message,
        "message_text": message_text,
        "message_hex": "0x" + message_text.encode("utf-8").hex(),
        "signature": _TEST_SIGNATURE,
    }


def test_shared_multisession_signing_verifies_personal_sign_blob_and_rejects_mismatch() -> None:
    blob = _signed_msk_blob()
    result = verify_personal_sign_blob(blob, expected_chain_id="0x28757b2", max_age_minutes=15)

    assert result["ok"] is True
    assert result["matched"] is True
    assert result["wallet_address"] == _TEST_WALLET_ADDRESS
    assert result["request_id"] == "msk_req_unit_valid"

    tampered = _signed_msk_blob(tamper_wallet=True)

    try:
        verify_personal_sign_blob(tampered, expected_chain_id="0x28757b2", max_age_minutes=15)
    except ValueError as exc:
        assert "message wallet mismatch" in str(exc) or "signature recovered" in str(exc)
    else:
        raise AssertionError("tampered blob unexpectedly verified")


def test_hub_multisession_key_endpoint_verifies_signature_and_persists_key() -> None:
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

        try:
            hub_url = f"http://127.0.0.1:{hub.server_port}"
            account_id = "bridge_local_test_wallet"
            blob = _signed_msk_blob(account_id=account_id)
            payload = {
                "signed_request": blob,
                "bridge_context": {
                    "account_id": account_id,
                    "status": "prepared",
                    "wallet_address": blob["wallet_address"],
                },
            }

            data = _post_json(f"{hub_url}/api/hub/v1/credits/multisession-keys/request", payload)

            assert data["ok"] is True
            assert data["verification"]["matched"] is True  # type: ignore[index]
            assert data["account"]["spendable"] is True  # type: ignore[index]
            assert data["key"]["status"] == "active"  # type: ignore[index]
            assert data["key"]["account_id"] == account_id  # type: ignore[index]
            assert (root / "hub" / "compute_credits" / "multisession_keys.json").exists()

            bad_blob = _signed_msk_blob(account_id=account_id, tamper_wallet=True)
            status, error = _post_json_error(
                f"{hub_url}/api/hub/v1/credits/multisession-keys/request",
                {
                    "signed_request": bad_blob,
                    "bridge_context": {
                        "account_id": account_id,
                        "status": "prepared",
                        "wallet_address": blob["wallet_address"],
                    },
                },
            )
            assert status == 400
            assert error["ok"] is False
            assert "message wallet mismatch" in str(error["error"]) or "signature recovered" in str(error["error"])
        finally:
            hub.shutdown()
            hub.server_close()


def test_worker_multisession_key_local_proxy_forwards_to_hub() -> None:
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
            account_id = "bridge_local_test_wallet"
            blob = _signed_msk_blob(account_id=account_id)
            viewport_url = f"http://127.0.0.1:{viewport.server_port}"
            data = _post_json(
                f"{viewport_url}/api/applications/worker/multisession-key/request",
                {
                    "hub_url": hub_url,
                    "signed_request": blob,
                    "bridge_context": {
                        "account_id": account_id,
                        "status": "prepared",
                        "wallet_address": blob["wallet_address"],
                    },
                },
            )

            assert data["ok"] is True
            assert data["hub_url"] == hub_url
            assert data["verification"]["recovered_address"] == _TEST_WALLET_ADDRESS  # type: ignore[index]
            assert data["key"]["id"].startswith("msk_")  # type: ignore[index]
        finally:
            viewport.shutdown()
            hub.shutdown()
            viewport.server_close()
            hub.server_close()
