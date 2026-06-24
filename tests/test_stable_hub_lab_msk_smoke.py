from __future__ import annotations

import json
import threading
from pathlib import Path

from main_computer.multisession_key_signing import private_key_to_address
from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
from main_computer.stable_hub_topology import load_stable_hub_topology
from tools.stable_hub_lab.run_lab import build_stable_msk_smoke_result


DEV_TOPOLOGY = Path("deploy/hub-topology/dev-topology.json")
DEV_PRIVATE_KEY = "0x" + "22" * 32
TEST_USER_SLUG = "user_slug_" + "s" * 40


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


def _write_test_topology(path: Path, *, hub1_url: str, hub3_url: str) -> None:
    document = json.loads(DEV_TOPOLOGY.read_text(encoding="utf-8"))
    for hub in document["hubs"]:
        if hub["hub_id"] == "dev-hub1":
            hub["hub_url"] = hub["public_url"] = hub1_url
        if hub["hub_id"] == "dev-hub3":
            hub["hub_url"] = hub["public_url"] = hub3_url
    document["entry_urls"] = [
        hub["hub_url"]
        for hub in document["hubs"]
    ]
    path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")


def test_stable_hub_lab_smoke_msk_creates_wallet_issues_on_one_hub_and_validates_on_another(tmp_path: Path) -> None:
    store = InMemoryStableMultiSessionKeyStore()
    hub1, thread1 = _start_server("dev-hub1", store)
    hub3, thread3 = _start_server("dev-hub3", store)
    wallet_path = tmp_path / "generated" / "wallet.json"
    topology_path = tmp_path / "dev-topology.json"
    _write_test_topology(
        topology_path,
        hub1_url=f"http://127.0.0.1:{hub1.server_port}",
        hub3_url=f"http://127.0.0.1:{hub3.server_port}",
    )

    assert not wallet_path.exists()

    try:
        result = build_stable_msk_smoke_result(
            topology_path=topology_path,
            wallet_path=wallet_path,
            request_hub_id="dev-hub1",
            validate_hub_id="dev-hub3",
            user_slug=TEST_USER_SLUG,
            request_id="stable-lab-msk-smoke-unit",
        )
    finally:
        hub1.shutdown()
        hub3.shutdown()
        hub1.server_close()
        hub3.server_close()
        thread1.join(timeout=2)
        thread3.join(timeout=2)

    wallet_payload = json.loads(wallet_path.read_text(encoding="utf-8"))
    generated_private_key = wallet_payload["private_key"]
    generated_address = private_key_to_address(generated_private_key)

    assert result["ok"] is True
    assert result["wallet_created"] is True
    assert Path(result["wallet_path"]) == wallet_path
    assert result["request_hub"]["hub_id"] == "dev-hub1"
    assert result["validate_hub"]["hub_id"] == "dev-hub3"
    assert result["wallet_address"] == generated_address
    assert result["user_slug"] == TEST_USER_SLUG
    assert result["multisession_key_id"].startswith(f"msk_{TEST_USER_SLUG}_")
    assert result["proof"] == {
        "signed_user_slug": True,
        "hub_slug_added": True,
        "msk_combines_user_and_hub_slugs": True,
        "stored_full_signed_request": True,
        "validation_used_key_id_only": True,
        "wallet_derived_from_stored_request": True,
        "cross_hub_validation": True,
    }

    stored = store.load()["keys"][result["multisession_key_id"]]
    assert stored["signed_request"]["message"]["user_slug"] == TEST_USER_SLUG
    assert stored["signed_message"]["user_slug"] == TEST_USER_SLUG
    assert stored["issued_by_hub_id"] == "dev-hub1"
    assert result["validated_key"]["issued_by_hub_id"] == "dev-hub1"


def test_stable_hub_lab_smoke_msk_reuses_existing_wallet(tmp_path: Path) -> None:
    store = InMemoryStableMultiSessionKeyStore()
    hub1, thread1 = _start_server("dev-hub1", store)
    hub3, thread3 = _start_server("dev-hub3", store)
    wallet_path = tmp_path / "wallet.json"
    wallet_path.write_text(
        json.dumps(
            {
                "address": private_key_to_address(DEV_PRIVATE_KEY),
                "private_key": DEV_PRIVATE_KEY,
            }
        ),
        encoding="utf-8",
    )
    topology_path = tmp_path / "dev-topology.json"
    _write_test_topology(
        topology_path,
        hub1_url=f"http://127.0.0.1:{hub1.server_port}",
        hub3_url=f"http://127.0.0.1:{hub3.server_port}",
    )

    try:
        result = build_stable_msk_smoke_result(
            topology_path=topology_path,
            wallet_path=wallet_path,
            request_hub_id="dev-hub1",
            validate_hub_id="dev-hub3",
            user_slug=TEST_USER_SLUG,
            request_id="stable-lab-msk-smoke-existing-wallet",
        )
    finally:
        hub1.shutdown()
        hub3.shutdown()
        hub1.server_close()
        hub3.server_close()
        thread1.join(timeout=2)
        thread3.join(timeout=2)

    assert result["ok"] is True
    assert result["wallet_created"] is False
    assert result["wallet_address"] == private_key_to_address(DEV_PRIVATE_KEY)


def test_stable_hub_lab_smoke_msk_rejects_wallet_address_mismatch(tmp_path: Path) -> None:
    wallet_path = tmp_path / "bad-wallet.json"
    wallet_path.write_text(
        json.dumps(
            {
                "address": "0x0000000000000000000000000000000000000001",
                "private_key": DEV_PRIVATE_KEY,
            }
        ),
        encoding="utf-8",
    )

    try:
        build_stable_msk_smoke_result(
            topology_path=DEV_TOPOLOGY,
            wallet_path=wallet_path,
            user_slug=TEST_USER_SLUG,
            request_id="should-not-post",
        )
    except Exception as exc:
        assert "wallet address mismatch" in str(exc)
    else:
        raise AssertionError("expected wallet address mismatch")
