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
from main_computer.multisession_key_signing import normalize_chain_id, verify_personal_sign_blob
from main_computer.viewport_server import ViewportServer


_TEST_WALLET_ADDRESS = "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf"
_TEST_SIGNATURE = (
    "0x08f4f37e2d8f74e18c1b8fde2374d5f28402fb8ab7fd1cc5b786aa40851a70cb"
    "fbda0244b7266d2b2651d5a0c6d00a4f9843db5b222e067666c0859b0c8ad25d1b"
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
    request_id: str = "msk_req_unit_valid",
    tamper_wallet: bool = False,
) -> dict[str, object]:
    message = {
        "purpose": "request_multi_session_key",
        "request_id": request_id,
        "wallet_address": "0x1111111111111111111111111111111111111111" if tamper_wallet else _TEST_WALLET_ADDRESS,
        "chain_id": "0x28757b2",
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



def test_multisession_chain_id_normalization_accepts_decimal_and_hex_text() -> None:
    assert normalize_chain_id("42424242") == "42424242"
    assert normalize_chain_id(42424242) == "42424242"
    assert normalize_chain_id("0x28757b2") == "42424242"
    assert normalize_chain_id("0X028757B2") == "42424242"
    assert normalize_chain_id("") == ""


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
            hub_bridge_backend="mock-chain",
            hub_allow_insecure_dev_network=False,
        )
        hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
        hub_thread = threading.Thread(target=hub.serve_forever, daemon=True)
        hub_thread.start()

        try:
            hub_url = f"http://127.0.0.1:{hub.server_port}"
            blob = _signed_msk_blob()
            payload = {"signed_request": blob}

            data = _post_json(f"{hub_url}/api/hub/v1/credits/multisession-keys/request", payload)

            assert data["ok"] is True
            assert data["verification"]["matched"] is True  # type: ignore[index]
            assert data["key"]["status"] == "active"  # type: ignore[index]
            assert data["key"]["wallet_address"] == _TEST_WALLET_ADDRESS  # type: ignore[index]
            assert "account" not in data
            assert "account_id" not in data["key"]  # type: ignore[index]
            assert (root / "hub" / "compute_credits" / "multisession_keys.json").exists()

            bad_blob = _signed_msk_blob(tamper_wallet=True)
            status, error = _post_json_error(
                f"{hub_url}/api/hub/v1/credits/multisession-keys/request",
                {"signed_request": bad_blob},
            )
            assert status == 400
            assert error["ok"] is False
            assert "message wallet mismatch" in str(error["error"]) or "signature recovered" in str(error["error"])
        finally:
            hub.shutdown()
            hub.server_close()


def test_worker_multisession_key_local_proxy_forwards_to_hub_and_caches_key_by_wallet() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old_cwd = Path.cwd()
        os.chdir(root)
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
            blob = _signed_msk_blob()
            viewport_url = f"http://127.0.0.1:{viewport.server_port}"
            data = _post_json(
                f"{viewport_url}/api/applications/worker/multisession-key/request",
                {
                    "hub_url": hub_url,
                    "signed_request": blob,
                },
            )

            assert data["ok"] is True
            assert data["hub_url"] == hub_url
            assert data["verification"]["recovered_address"] == _TEST_WALLET_ADDRESS  # type: ignore[index]
            assert data["key"]["id"].startswith("msk_")  # type: ignore[index]
            assert data["key"]["wallet_address"] == _TEST_WALLET_ADDRESS  # type: ignore[index]
            assert data["local_cache"]["stored"] is True  # type: ignore[index]
            assert "id" not in data["local_cache"]["key"]  # type: ignore[index]
            cache_path = root / ".main_computer" / "worker_multisession_keys.json"
            assert cache_path.exists()
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            assert cache["keys"][data["key"]["id"]]["wallet_address"] == _TEST_WALLET_ADDRESS  # type: ignore[index]

            duplicate_status, duplicate_error = _post_json_error(
                f"{viewport_url}/api/applications/worker/multisession-key/request",
                {
                    "hub_url": hub_url,
                    "signed_request": blob,
                },
            )
            assert duplicate_status == 400
            assert "already exists" in str(duplicate_error.get("error", ""))

            loaded = _post_json(
                f"{viewport_url}/api/applications/worker/multisession-keys/load",
                {
                    "hub_url": hub_url,
                    "wallet_address": _TEST_WALLET_ADDRESS,
                },
            )

            assert loaded["ok"] is True
            assert loaded["wallet_address"] == _TEST_WALLET_ADDRESS
            assert loaded["key_ids_redacted"] is True
            assert loaded["active_key"]["status"] == "active"  # type: ignore[index]
            assert "id" not in loaded["active_key"]  # type: ignore[operator]
            assert loaded["keys"][0]["wallet_address"] == _TEST_WALLET_ADDRESS  # type: ignore[index]
            assert "id" not in loaded["keys"][0]  # type: ignore[operator]

            revoked = _post_json(
                f"{viewport_url}/api/applications/worker/multisession-key/revoke",
                {
                    "hub_url": hub_url,
                    "wallet_address": _TEST_WALLET_ADDRESS,
                },
            )
            assert revoked["ok"] is True
            assert revoked["revoked"] is True
            assert revoked["key_ids_redacted"] is True
            assert "id" not in revoked["key"]  # type: ignore[operator]

            loaded_after_revoke = _post_json(
                f"{viewport_url}/api/applications/worker/multisession-keys/load",
                {
                    "hub_url": hub_url,
                    "wallet_address": _TEST_WALLET_ADDRESS,
                },
            )
            assert loaded_after_revoke["active_key"] is None
            cache_after_revoke = json.loads(cache_path.read_text(encoding="utf-8"))
            assert cache_after_revoke["keys"][data["key"]["id"]]["status"] == "revoked"  # type: ignore[index]
        finally:
            viewport.shutdown()
            hub.shutdown()
            viewport.server_close()
            hub.server_close()
            os.chdir(old_cwd)
