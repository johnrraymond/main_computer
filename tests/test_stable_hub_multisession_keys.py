from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main_computer.multisession_key_signing import build_personal_sign_blob, private_key_to_address
from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
from main_computer.stable_hub_topology import load_stable_hub_topology


DEV_TOPOLOGY = Path("deploy/hub-topology/dev-topology.json")
DEV_PRIVATE_KEY = "0x" + "11" * 32
DEFAULT_USER_SLUG = "user_slug_" + "a" * 40


def _post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=3) as response:  # noqa: S310 - local test server
        return json.loads(response.read().decode("utf-8"))


def _post_json_error(url: str, payload: dict) -> tuple[int, dict]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urlopen(request, timeout=3)  # noqa: S310
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
    return 200, {}


def _signed_msk_request(
    *,
    request_id: str = "stable-msk-test-1",
    chain_id: str = "42424242",
    user_slug: str | None = DEFAULT_USER_SLUG,
) -> dict:
    now = datetime.now(timezone.utc)
    wallet_address = private_key_to_address(DEV_PRIVATE_KEY)
    message = {
        "purpose": "request_multi_session_key",
        "wallet_address": wallet_address,
        "chain_id": chain_id,
        "request_id": request_id,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
        "origin": "stable-hub-unit-test",
    }
    if user_slug is not None:
        message["user_slug"] = user_slug
    return build_personal_sign_blob(
        message=message,
        private_key=DEV_PRIVATE_KEY,
        wallet_address=wallet_address,
        chain_id=chain_id,
    )


def _start_server(hub_id: str, store: InMemoryStableMultiSessionKeyStore):
    topology = load_stable_hub_topology(DEV_TOPOLOGY)
    server = create_stable_hub_server(
        topology=topology,
        hub_id=hub_id,
        bind_host="127.0.0.1",
        bind_port=0,
        multisession_key_store=store,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_stable_hub_issues_and_validates_multisession_key_by_id_only() -> None:
    store = InMemoryStableMultiSessionKeyStore()
    server, thread = _start_server("dev-hub1", store)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        signed_request = _signed_msk_request()
        issued = _post_json(
            f"{base_url}/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": signed_request},
        )
        # A client only needs the MSK id for later validation/auth. Wallet/account
        # are derived from the stored signed request, not trusted from input.
        validated = _post_json(
            f"{base_url}/api/hub/v1/credits/multisession-keys/validate",
            {
                "multisession_authorization": {
                    "kind": "multisession_key",
                    "multisession_key_id": issued["key"]["id"],
                }
            },
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert issued["ok"] is True
    assert issued["hub_id"] == "dev-hub1"
    assert issued["cluster_id"] == "main-computer-dev-stable-hub"
    assert issued["key"]["status"] == "active"
    assert issued["key"]["chain_id"] == "42424242"
    assert issued["key"]["user_slug"] == DEFAULT_USER_SLUG
    assert issued["key"]["hub_slug"]
    assert issued["key"]["id"].startswith(f"msk_{DEFAULT_USER_SLUG}_")
    assert issued["key"]["has_signed_request"] is True
    assert issued["multisession_authorization"] == {
        "kind": "multisession_key",
        "multisession_key_id": issued["key"]["id"],
        "key_id": issued["key"]["id"],
        "chain_id": "42424242",
        "cluster_id": "main-computer-dev-stable-hub",
    }

    assert validated["ok"] is True
    assert validated["valid"] is True
    assert validated["ready"] is True
    assert validated["reason_code"] == "active"
    assert validated["wallet_address"] == issued["key"]["wallet_address"]
    assert validated["account_id"] == issued["key"]["account_id"]
    assert validated["multisession_key_id"] == issued["key"]["id"]


def test_stable_hub_stores_full_signed_request_and_both_entropy_slugs() -> None:
    store = InMemoryStableMultiSessionKeyStore()
    server, thread = _start_server("dev-hub1", store)
    signed_request = _signed_msk_request(request_id="stored-full-signed-request")
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        issued = _post_json(
            f"{base_url}/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": signed_request},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    stored = store.load()["keys"][issued["key"]["id"]]
    assert stored["signed_request"] == signed_request
    assert stored["signed_message"]["user_slug"] == DEFAULT_USER_SLUG
    assert stored["user_slug"] == DEFAULT_USER_SLUG
    assert stored["hub_slug"]
    assert stored["id"] == f"msk_{stored['user_slug']}_{stored['hub_slug']}"
    assert stored["verified"]["wallet_address"] == private_key_to_address(DEV_PRIVATE_KEY)
    assert stored["verified"]["account_id"] == issued["key"]["account_id"]


def test_stable_hub_reuses_same_key_for_idempotent_signed_request_retry() -> None:
    store = InMemoryStableMultiSessionKeyStore()
    server, thread = _start_server("dev-hub1", store)
    signed_request = _signed_msk_request(request_id="idempotent-retry")
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        first = _post_json(
            f"{base_url}/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": signed_request},
        )
        second = _post_json(
            f"{base_url}/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": signed_request},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert second["key"]["id"] == first["key"]["id"]
    assert len(store.load()["keys"]) == 1


def test_stable_hub_creates_new_msk_for_new_signed_user_slug() -> None:
    store = InMemoryStableMultiSessionKeyStore()
    server, thread = _start_server("dev-hub1", store)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        first = _post_json(
            f"{base_url}/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": _signed_msk_request(request_id="new-key-1", user_slug="user_slug_" + "b" * 40)},
        )
        second = _post_json(
            f"{base_url}/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": _signed_msk_request(request_id="new-key-2", user_slug="user_slug_" + "c" * 40)},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert first["key"]["id"] != second["key"]["id"]
    assert first["key"]["user_slug"] != second["key"]["user_slug"]
    assert len(store.load()["keys"]) == 2


def test_stable_hub_msk_store_is_shared_across_concrete_hubs() -> None:
    store = InMemoryStableMultiSessionKeyStore()
    hub1, thread1 = _start_server("dev-hub1", store)
    hub3, thread3 = _start_server("dev-hub3", store)
    try:
        hub1_url = f"http://127.0.0.1:{hub1.server_port}"
        hub3_url = f"http://127.0.0.1:{hub3.server_port}"
        issued = _post_json(
            f"{hub1_url}/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": _signed_msk_request(request_id="shared-store-msk")},
        )
        validated = _post_json(
            f"{hub3_url}/api/hub/v1/credits/multisession-keys/validate",
            {"multisession_authorization": {"kind": "multisession_key", "multisession_key_id": issued["key"]["id"]}},
        )
    finally:
        hub1.shutdown()
        hub3.shutdown()
        hub1.server_close()
        hub3.server_close()
        thread1.join(timeout=2)
        thread3.join(timeout=2)

    assert issued["hub_id"] == "dev-hub1"
    assert validated["hub_id"] == "dev-hub3"
    assert validated["valid"] is True
    assert validated["cluster_id"] == "main-computer-dev-stable-hub"
    assert validated["key"]["issued_by_hub_id"] == "dev-hub1"


def test_stable_hub_validate_reports_stale_or_missing_msk_clearly() -> None:
    store = InMemoryStableMultiSessionKeyStore()
    server, thread = _start_server("dev-hub2", store)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        validated = _post_json(
            f"{base_url}/api/hub/v1/credits/multisession-keys/validate",
            {
                "multisession_authorization": {
                    "kind": "multisession_key",
                    "multisession_key_id": "msk_missing",
                    "chain_id": "42424242",
                }
            },
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert validated["ok"] is True
    assert validated["valid"] is False
    assert validated["ready"] is False
    assert validated["reason_code"] == "key_not_active"
    assert "Request a new multi-session key" in validated["user_message"]


def test_stable_hub_rejects_wrong_chain_signed_msk_request() -> None:
    store = InMemoryStableMultiSessionKeyStore()
    server, thread = _start_server("dev-hub1", store)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        status, body = _post_json_error(
            f"{base_url}/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": _signed_msk_request(chain_id="999")},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 400
    assert body["ok"] is False
    assert "wrong blob chain_id" in body["error"]


def test_stable_hub_rejects_msk_request_without_signed_user_slug() -> None:
    store = InMemoryStableMultiSessionKeyStore()
    server, thread = _start_server("dev-hub1", store)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        status, body = _post_json_error(
            f"{base_url}/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": _signed_msk_request(request_id="missing-user-slug", user_slug=None)},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 400
    assert body["ok"] is False
    assert "user_slug is required" in body["error"]


def test_stable_hub_rejects_low_entropy_user_slug() -> None:
    store = InMemoryStableMultiSessionKeyStore()
    server, thread = _start_server("dev-hub1", store)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        status, body = _post_json_error(
            f"{base_url}/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": _signed_msk_request(request_id="short-user-slug", user_slug="too-short")},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 400
    assert body["ok"] is False
    assert "user_slug must be at least" in body["error"]


class _DirectOnlyMskStore:
    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        self.hash_index: dict[str, str] = {}

    def load(self) -> dict:
        raise AssertionError("direct MSK hot path must not load the whole store")

    def save(self, data: dict) -> None:
        raise AssertionError("direct MSK hot path must not save the whole store")

    def create_key_record_if_absent(self, *, signed_request_hash: str, record: dict) -> dict:
        existing_id = self.hash_index.get(signed_request_hash)
        if existing_id:
            return {"record": dict(self.records[existing_id]), "idempotent": True}
        key_id = str(record["id"])
        self.records[key_id] = dict(record)
        self.hash_index[signed_request_hash] = key_id
        return {"record": dict(record), "idempotent": False}

    def get_key_record(self, key_id: str) -> dict | None:
        record = self.records.get(key_id)
        return dict(record) if isinstance(record, dict) else None


def test_stable_hub_msk_fdb_style_store_does_not_rewrite_whole_history() -> None:
    store = _DirectOnlyMskStore()
    server, thread = _start_server("dev-hub1", store)  # type: ignore[arg-type]
    signed_request = _signed_msk_request(request_id="direct-record-store")
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        first = _post_json(
            f"{base_url}/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": signed_request},
        )
        second = _post_json(
            f"{base_url}/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": signed_request},
        )
        validated = _post_json(
            f"{base_url}/api/hub/v1/credits/multisession-keys/validate",
            {"multisession_authorization": {"multisession_key_id": first["key"]["id"]}},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert second["key"]["id"] == first["key"]["id"]
    assert validated["valid"] is True
    assert validated["multisession_key_id"] == first["key"]["id"]
    assert list(store.records) == [first["key"]["id"]]
