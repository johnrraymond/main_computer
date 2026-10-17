from __future__ import annotations

import base64
import json
import os
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main_computer.multisession_key_signing import build_personal_sign_blob, private_key_to_address
from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
from tools.stable_hub_lab.run_lab import build_worker_live_session_smoke_result


DEV_TOPOLOGY = Path("deploy/hub-topology/dev-topology.json")
DEV_PRIVATE_KEY = "0x" + "33" * 32
TEST_USER_SLUG = "user_slug_" + "w" * 40


def _post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=3) as response:  # noqa: S310 - local test server
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str) -> dict:
    with urlopen(url, timeout=3) as response:  # noqa: S310 - local test server
        return json.loads(response.read().decode("utf-8"))


def _get_json_status(url: str) -> tuple[int, dict]:
    try:
        with urlopen(url, timeout=3) as response:  # noqa: S310 - local test server
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return int(exc.code), json.loads(exc.read().decode("utf-8"))


def _signed_msk_request(*, request_id: str = "stable-worker-live-session-msk", private_key: str = DEV_PRIVATE_KEY) -> dict:
    now = datetime.now(timezone.utc)
    wallet_address = private_key_to_address(private_key)
    message = {
        "purpose": "request_multi_session_key",
        "wallet_address": wallet_address,
        "chain_id": "42424242",
        "request_id": request_id,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
        "origin": "stable-worker-live-session-unit-test",
        "user_slug": TEST_USER_SLUG,
    }
    return build_personal_sign_blob(
        message=message,
        private_key=private_key,
        wallet_address=wallet_address,
        chain_id="42424242",
    )


def _start_server(
    hub_id: str,
    msk_store: InMemoryStableMultiSessionKeyStore,
    worker_store: InMemoryStableWorkerSessionStore,
):
    topology = load_stable_hub_topology(DEV_TOPOLOGY)
    server = create_stable_hub_server(
        topology=topology,
        hub_id=hub_id,
        bind_host="127.0.0.1",
        bind_port=0,
        multisession_key_store=msk_store,
        worker_session_store=worker_store,
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
    document["entry_urls"] = [hub["hub_url"] for hub in document["hubs"]]
    path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")


def _ws_connect(host: str, port: int, path: str) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=3)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise AssertionError(f"websocket handshake closed early: {response!r}")
        response += chunk
    assert b" 101 " in response.split(b"\r\n", 1)[0], response.decode("latin1", errors="replace")
    return sock


def _ws_send_json(sock: socket.socket, payload: dict) -> None:
    data = json.dumps(payload, sort_keys=True).encode("utf-8")
    header = bytearray([0x81])
    if len(data) < 126:
        header.append(0x80 | len(data))
    elif len(data) < 65536:
        header.append(0x80 | 126)
        header.extend(len(data).to_bytes(2, "big"))
    else:
        header.append(0x80 | 127)
        header.extend(len(data).to_bytes(8, "big"))
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
    sock.sendall(bytes(header) + mask + masked)


def _read_exact(sock: socket.socket, length: int) -> bytes:
    chunks = []
    remaining = length
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise AssertionError("socket closed while reading websocket frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _ws_recv_json(sock: socket.socket) -> dict:
    while True:
        header = _read_exact(sock, 2)
        opcode = header[0] & 0x0F
        length = header[1] & 0x7F
        if length == 126:
            length = int.from_bytes(_read_exact(sock, 2), "big")
        elif length == 127:
            length = int.from_bytes(_read_exact(sock, 8), "big")
        payload = _read_exact(sock, length)
        if opcode == 0x8:
            raise AssertionError("websocket closed before JSON message")
        if opcode != 0x1:
            continue
        decoded = json.loads(payload.decode("utf-8"))
        assert isinstance(decoded, dict)
        return decoded


def test_worker_live_session_uses_open_websocket_and_fdb_style_owner_directory(capsys) -> None:
    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    hub1, thread1 = _start_server("dev-hub1", msk_store, worker_store)
    hub3, thread3 = _start_server("dev-hub3", msk_store, worker_store)
    sock: socket.socket | None = None
    try:
        hub1_url = f"http://127.0.0.1:{hub1.server_port}"
        issued = _post_json(
            f"{hub1_url}/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": _signed_msk_request()},
        )
        msk_id = issued["key"]["id"]

        sock = _ws_connect("127.0.0.1", hub3.server_port, "/api/hub/v1/workers/live-session")
        _ws_send_json(
            sock,
            {
                "type": "worker.auth",
                "diagnostic_attempt_id": "diag-normal-close",
                "multisession_authorization": {
                    "kind": "multisession_key",
                    "multisession_key_id": msk_id,
                },
                "market": {
                    "rings": ["ring-2"],
                    "price": {"amount": "0.05", "unit": "credit"},
                    "capabilities": ["python"],
                    "max_concurrency": 2,
                },
            },
        )
        accepted = _ws_recv_json(sock)
        ping = _ws_recv_json(sock)

        assert accepted["type"] == "hub.auth.accepted"
        assert accepted["hub_id"] == "dev-hub3"
        assert "worker_id" not in accepted
        assert accepted["account_id"] == issued["key"]["account_id"]
        assert accepted["owner"]["account_id"] == issued["key"]["account_id"]
        assert accepted["market"]["rings"] == ["ring-2"]
        assert accepted["market"]["partitions"] == ["ring-2"]
        assert accepted["market"]["price"] == {"amount": "0.05", "unit": "credit"}
        assert accepted["market"]["capabilities"] == ["python"]
        assert accepted["market"]["max_concurrency"] == 2
        assert accepted["heartbeat"]["transport"] == "websocket"
        assert ping["type"] == "hub.ping"
        assert ping["connection_id"] == accepted["connection_id"]

        _ws_send_json(
            sock,
            {
                "type": "worker.pong",
                "connection_id": accepted["connection_id"],
                "ping_id": ping["ping_id"],
            },
        )
        pong_accepted = _ws_recv_json(sock)
        assert pong_accepted["type"] == "hub.pong.accepted"
        assert pong_accepted["ok"] is True

        session = hub3.get_live_worker_session(accepted["connection_id"])
        assert session is not None
        assert session.worker_id == accepted["connection_id"]
        assert session.connection_id == accepted["connection_id"]
        assert session.snapshot()["status"] == "live"
        assert session.snapshot()["last_pong_at"] == pong_accepted["owner"]["last_pong_at"]

        server_thread_ping_id = "ping_from_server_thread"
        sender = threading.Thread(
            target=session.send_json,
            args=(
                {
                    "type": "hub.ping",
                    "ping_id": server_thread_ping_id,
                    "connection_id": accepted["connection_id"],
                },
            ),
        )
        sender.start()
        pushed_ping = _ws_recv_json(sock)
        sender.join(timeout=2)

        assert not sender.is_alive()
        assert pushed_ping == {
            "type": "hub.ping",
            "ping_id": server_thread_ping_id,
            "connection_id": accepted["connection_id"],
        }

        _ws_send_json(
            sock,
            {
                "type": "worker.pong",
                "connection_id": accepted["connection_id"],
                "ping_id": server_thread_ping_id,
            },
        )
        pushed_ping_pong_accepted = _ws_recv_json(sock)
        assert pushed_ping_pong_accepted["type"] == "hub.pong.accepted"
        assert (
            session.snapshot()["last_pong_at"]
            == pushed_ping_pong_accepted["owner"]["last_pong_at"]
        )

        public_owner_status, public_owner_body = _get_json_status(
            f"{hub1_url}/api/hub/v1/workers/{accepted['connection_id']}/owner"
        )
        owner = accepted["owner"]

        assert public_owner_status == 404
        assert public_owner_body["error"] == "not_found"
        assert accepted["worker_hub"]["hub_id"] == "dev-hub3"
        assert accepted["worker_hub"]["hub_url"] == "http://127.0.0.1:8873"
        assert accepted["worker_hub"]["local_owner"] is True
        assert accepted["worker_hub"]["handoff"] is False
        assert owner["status"] == "live"
        assert owner["owner_hub_id"] == "dev-hub3"
        assert owner["owner_hub_url"] == "http://127.0.0.1:8873"
        assert owner["connection_id"] == accepted["connection_id"]
        assert owner["multisession_key_id"] == msk_id
        assert owner["wallet_address"] == issued["key"]["wallet_address"]
        assert owner["account_id"] == issued["key"]["account_id"]

        market_record = hub1.worker_market_directory.get_worker(accepted["connection_id"])
        assert market_record is not None
        assert market_record["status"] == "live"
        assert market_record["owner_hub_id"] == "dev-hub3"
        assert market_record["connection_id"] == accepted["connection_id"]
        assert market_record["worker_msk_id"] == msk_id
        assert market_record["worker_wallet_address"] == issued["key"]["wallet_address"]
        assert market_record["worker_account_id"] == issued["key"]["account_id"]

        selected = hub1.worker_market_directory.select_worker_for_work(
            {
                "ring": "ring-2",
                "max_price": {"amount": "0.10", "unit": "credit"},
                "capabilities": ["python"],
            }
        )
        assert selected is not None
        assert selected["worker_id"] == accepted["connection_id"]
        assert selected["owner_hub_id"] == "dev-hub3"
        assert selected["partition"] == "ring-2"

        _ws_send_json(sock, {"type": "worker.close"})
        sock.close()
        sock = None
        deadline = time.monotonic() + 2.0
        closed = None
        while time.monotonic() < deadline:
            closed = hub1.worker_session_directory.get_owner(accepted["connection_id"])
            if closed and closed.get("status") == "closed":
                break
            time.sleep(0.05)
        assert closed is not None
        assert closed["status"] == "closed"
        assert closed["owner_hub_id"] == "dev-hub3"
        closed_market = hub1.worker_market_directory.get_worker(accepted["connection_id"])
        assert closed_market is not None
        assert closed_market["status"] == "closed"
        log = capsys.readouterr().err
        assert "attempt_id=diag-normal-close" in log
        assert f"connection_id={accepted['connection_id']}" in log
        assert "session_registered=True" in log
        assert "reason='worker.close'" in log
    finally:
        if sock is not None:
            sock.close()
        hub1.shutdown()
        hub3.shutdown()
        hub1.server_close()
        hub3.server_close()
        thread1.join(timeout=2)
        thread3.join(timeout=2)



def test_worker_live_session_logs_correlated_abrupt_tcp_eof(capsys) -> None:
    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    hub, thread = _start_server("dev-hub3", msk_store, worker_store)
    sock: socket.socket | None = None
    try:
        hub_url = f"http://127.0.0.1:{hub.server_port}"
        issued = _post_json(
            f"{hub_url}/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": _signed_msk_request(request_id="stable-worker-eof-diagnostic")},
        )
        sock = _ws_connect("127.0.0.1", hub.server_port, "/api/hub/v1/workers/live-session")
        _ws_send_json(sock, {
            "type": "worker.auth",
            "diagnostic_attempt_id": "diag-abrupt-eof",
            "multisession_authorization": {
                "kind": "multisession_key",
                "multisession_key_id": issued["key"]["id"],
            },
            "market": {"rings": ["ring-2"], "capabilities": ["python"]},
        })
        accepted = _ws_recv_json(sock)
        ping = _ws_recv_json(sock)
        assert accepted["type"] == "hub.auth.accepted"
        _ws_send_json(sock, {
            "type": "worker.pong",
            "connection_id": accepted["connection_id"],
            "ping_id": ping["ping_id"],
        })
        assert _ws_recv_json(sock)["type"] == "hub.pong.accepted"

        sock.shutdown(socket.SHUT_RDWR)
        sock.close()
        sock = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            owner = hub.worker_session_directory.get_owner(accepted["connection_id"])
            if owner and owner.get("status") == "closed":
                break
            time.sleep(0.05)
        log = capsys.readouterr().err
        assert "attempt_id=diag-abrupt-eof" in log
        assert f"connection_id={accepted['connection_id']}" in log
        assert "session_registered=True" in log
        assert "last_message_type='worker.pong'" in log
        assert "reason='ConnectionError: websocket closed'" in log
    finally:
        if sock is not None:
            sock.close()
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=2)

def test_worker_rest_polling_heartbeat_is_not_exposed() -> None:
    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    hub1, thread1 = _start_server("dev-hub1", msk_store, worker_store)
    try:
        request = Request(
            f"http://127.0.0.1:{hub1.server_port}/api/hub/v1/workers/worker-1/ping",
            data=json.dumps({"connection_id": "conn_wrong"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(request, timeout=3)  # noqa: S310
            status = 200
            body = {}
        except HTTPError as exc:
            status = exc.code
            body = json.loads(exc.read().decode("utf-8"))
    finally:
        hub1.shutdown()
        hub1.server_close()
        thread1.join(timeout=2)

    assert status == 404
    assert body["error"] == "not_found"


def test_lab_worker_live_session_smoke_helper_uses_authenticated_owner_claim(tmp_path: Path) -> None:
    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    hub1, thread1 = _start_server("dev-hub1", msk_store, worker_store)
    hub3, thread3 = _start_server("dev-hub3", msk_store, worker_store)
    topology_path = tmp_path / "dev-topology.json"
    wallet_path = tmp_path / "worker-live-wallet.json"
    _write_test_topology(
        topology_path,
        hub1_url=f"http://127.0.0.1:{hub1.server_port}",
        hub3_url=f"http://127.0.0.1:{hub3.server_port}",
    )
    try:
        result = build_worker_live_session_smoke_result(
            topology_path=topology_path,
            wallet_path=wallet_path,
            request_hub_id="dev-hub1",
            validate_hub_id="dev-hub3",
            worker_hub_id="dev-hub3",
            owner_check_hub_id="dev-hub1",
            worker_id="worker-live-session-smoke-helper",
            user_slug=TEST_USER_SLUG,
            request_id="worker-live-session-smoke-helper",
        )
    finally:
        hub1.shutdown()
        hub3.shutdown()
        hub1.server_close()
        hub3.server_close()
        thread1.join(timeout=2)
        thread3.join(timeout=2)

    assert result["ok"] is True
    assert result["worker_hub"]["hub_id"] == "dev-hub3"
    assert result["owner"]["owner_hub_id"] == "dev-hub3"
    assert result["owner"]["status"] == "live"
    assert result["proof"]["websocket_auth_accepted"] is True
    assert result["proof"]["hub_ping_over_open_connection"] is True
    assert result["proof"]["worker_pong_over_same_connection"] is True
    assert result["proof"]["authenticated_owner_claim_returned"] is True
    assert result["proof"]["public_worker_owner_lookup_absent"] is True


def test_same_wallet_concurrent_connections_have_independent_hub_slots() -> None:
    """A client label may collide, but it cannot replace another wallet socket."""
    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    hub, thread = _start_server("dev-hub3", msk_store, worker_store)
    sockets: list[socket.socket] = []
    try:
        url = f"http://127.0.0.1:{hub.server_port}"
        issued = _post_json(
            f"{url}/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": _signed_msk_request(request_id="multi-wallet-concurrent")},
        )
        account = issued["key"]["account_id"]
        key_id = issued["key"]["id"]
        accepted: list[dict] = []
        for _ in range(2):
            sock = _ws_connect("127.0.0.1", hub.server_port, "/api/hub/v1/workers/live-session")
            sockets.append(sock)
            _ws_send_json(sock, {
                "type": "worker.auth",
                "worker_id": "same-client-label-must-be-ignored",
                "multisession_authorization": {
                    "kind": "multisession_key", "multisession_key_id": key_id,
                },
                "market": {"rings": ["ring-2"], "models": ["gemma4"],
                           "price": {"amount": "0.01", "unit": "credit"}},
            })
            reply = _ws_recv_json(sock)
            ping = _ws_recv_json(sock)
            assert reply["type"] == "hub.auth.accepted"
            assert "worker_id" not in reply
            assert reply["account_id"] == account
            assert reply["owner"]["account_id"] == account
            assert ping["type"] == "hub.ping"
            _ws_send_json(sock, {"type": "worker.pong", "ping_id": ping["ping_id"]})
            assert _ws_recv_json(sock)["type"] == "hub.pong.accepted"
            accepted.append(reply)
        first, second = accepted
        assert first["connection_id"] != second["connection_id"]
        live = [x for x in hub.worker_market_directory.list_workers() if x["status"] == "live"]
        assert {x["worker_id"] for x in live} == {first["connection_id"], second["connection_id"]}
        assert {x["worker_account_id"] for x in live} == {account}
        _ws_send_json(sockets[0], {"type": "worker.close"})
        sockets[0].close()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            first_market = hub.worker_market_directory.get_worker(first["connection_id"])
            if first_market is not None and first_market["status"] == "closed":
                break
            time.sleep(0.02)
        assert first_market["status"] == "closed"
        second_market = hub.worker_market_directory.get_worker(second["connection_id"])
        assert second_market["status"] == "live"
        assert hub.worker_session_directory.get_owner(second["connection_id"])["status"] == "live"
        selected = hub.worker_market_directory.select_worker_for_work({"ring": "ring-2"})
        assert selected["worker_id"] == second["connection_id"]
    finally:
        for sock in sockets:
            try:
                sock.close()
            except OSError:
                pass
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=2)


def test_wallet_is_economic_identity_across_connection_slots() -> None:
    from main_computer.hub_credit_indexer import wallet_account_id
    from main_computer.stable_hub_worker_sessions import StableHubPayoutLedgerDirectory

    wallet = "0x" + "ce" * 20
    account = wallet_account_id(wallet)
    requester_wallet = "0x" + "bd" * 20
    requester = wallet_account_id(requester_wallet)
    store = InMemoryStableWorkerSessionStore()
    ledger = StableHubPayoutLedgerDirectory(
        topology=load_stable_hub_topology(DEV_TOPOLOGY), hub_id="dev-hub3", store=store,
    )
    ledger.fund_account(account_id=requester, wallet_address=requester_wallet, credits="2", replace=True)
    slots = ["conn_first", "conn_second"]
    for i, slot in enumerate(slots):
        ledger.spend_request_credit(
            account_id=requester, wallet_address=requester_wallet,
            request_id=f"request-wallet-{i}", session_id=f"sess_wallet_{i}",
            run_id=f"run_wallet_{i}", worker_id=slot,
            worker_account_id=wallet,
            selected_price={"amount": "0.25", "unit": "credit"},
            requester_max_price={"amount": "1", "unit": "credit"},
            partition="ring-2", result={"ok": True},
        )
    earnings = list(store.load()["worker_earnings"].values())
    assert len(earnings) == 2
    assert {entry["worker_id"] for entry in earnings} == {account}
    assert {entry["worker_node_id"] for entry in earnings} == set(slots)
    claim = ledger.record_worker_claim(worker_id=account)
    assert len(claim["earning_ids"]) == 2
    assert claim["claimed_credit_wei"] == "500000000000000000"
    assert ledger.record_worker_claim(worker_id="conn_first")["status"] == "empty"
    batch = ledger.create_worker_settlement_batch(worker_id=account)
    assert batch["total_credit_wei_exact"] == "500000000000000000"


def test_payout_claim_authority_is_wallet_not_connection_or_client_label() -> None:
    from main_computer.hub_credit_indexer import wallet_account_id

    msk_store = InMemoryStableMultiSessionKeyStore()
    store = InMemoryStableWorkerSessionStore()
    hub, thread = _start_server("dev-hub3", msk_store, store)
    try:
        url = f"http://127.0.0.1:{hub.server_port}"
        own = _post_json(f"{url}/api/hub/v1/credits/multisession-keys/request", {
            "signed_request": _signed_msk_request(request_id="owner-payout")})
        stranger = _post_json(f"{url}/api/hub/v1/credits/multisession-keys/request", {
            "signed_request": _signed_msk_request(
                request_id="stranger-payout", private_key="0x" + "44" * 32)})
        wallet_account = own["key"]["account_id"]
        assert wallet_account != stranger["key"]["account_id"]

        requester_wallet = "0x" + "cc" * 20
        requester = wallet_account_id(requester_wallet)
        ledger = hub.payout_ledger_directory
        ledger.fund_account(account_id=requester, wallet_address=requester_wallet, credits="1", replace=True)
        paid = ledger.spend_request_credit(
            account_id=requester, wallet_address=requester_wallet,
            request_id="req-payout-wallet", session_id="sess_payout_wallet",
            run_id="run_payout_wallet", worker_id="conn_synthetic_slot",
            worker_account_id=own["key"]["wallet_address"],
            selected_price={"amount": "0.1", "unit": "credit"},
            requester_max_price=None, partition="ring-3", result={"ok": True},
        )
        assert paid["worker_earning"]["worker_id"] == wallet_account
        path = f"{url}/api/hub/v1/workers/{wallet_account}/payout/claim"

        def attempt_claim(msk: str) -> tuple[int, dict]:
            request = Request(
                path,
                data=json.dumps({"multisession_authorization": {
                    "kind": "multisession_key", "multisession_key_id": msk},
                    "earning_ids": [paid["worker_earning"]["earning_id"]]}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urlopen(request, timeout=3) as response:
                    return response.status, json.loads(response.read())
            except HTTPError as exc:
                return exc.code, json.loads(exc.read())

        denied_status, denied = attempt_claim(stranger["key"]["id"])
        assert denied_status == 403
        assert denied["error"] == "worker_payout_account_mismatch"
        ok_status, ok = attempt_claim(own["key"]["id"])
        assert ok_status == 200
        assert ok["claim"]["worker_id"] == wallet_account
        assert len(ok["claim"]["earning_ids"]) == 1
    finally:
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=2)


def test_websocket_upgrade_is_http11_and_invalid_first_frame_round_trips(capsys) -> None:
    """A 101 alone is insufficient: we must see the Hub's first application frame."""
    from tools.captain_websocket_transport_smoke import probe

    hub, thread = _start_server(
        "dev-hub1", InMemoryStableMultiSessionKeyStore(), InMemoryStableWorkerSessionStore()
    )
    try:
        assert probe(f"http://127.0.0.1:{hub.server_port}", timeout_s=3.0)
        output = capsys.readouterr().out
        assert "HANDSHAKE HTTP/1.1 101 Switching Protocols" in output
        assert "FRAME hub.error" in output
        assert "PASS: upgrade and bidirectional WebSocket frames verified" in output
    finally:
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=3)


def test_captain_websocket_client_handshake_and_live_auth_using_real_server() -> None:
    """No mock 101: Captain must complete the wallet-derived live handshake."""
    # This snapshot omits game_projects/webgl-demo/game.json, which the viewport
    # import requires.  Load the exact client class from its source, not a
    # reimplementation; exercise its real sockets and methods below.
    import ast
    import hashlib
    import ssl
    from urllib.parse import urlparse

    source = (Path(__file__).resolve().parents[1] / "main_computer" / "viewport_routes_energy.py")
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    client_class = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                        and node.name == "_WorkerHubLiveSessionClient")
    module = ast.Module(body=[client_class], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Any": object, "json": json, "os": os, "socket": socket,
        "ssl": ssl, "threading": threading, "time": time,
        "hashlib": hashlib, "base64": base64, "urlparse": urlparse,
        "datetime": datetime, "timezone": timezone,
        "_worker_runtime_public_redacted": lambda obj: obj,
    }
    exec(compile(module, str(source), "exec"), namespace)
    _WorkerHubLiveSessionClient = namespace["_WorkerHubLiveSessionClient"]

    msk_store = InMemoryStableMultiSessionKeyStore()
    hub, thread = _start_server(
        "dev-hub1", msk_store, InMemoryStableWorkerSessionStore()
    )
    client = None
    try:
        url = f"http://127.0.0.1:{hub.server_port}"
        issued = _post_json(
            url + "/api/hub/v1/credits/multisession-keys/request",
            {"signed_request": _signed_msk_request(request_id="captain-upgrade-http11")},
        )
        client = _WorkerHubLiveSessionClient(
            hub_url=url,
            worker_id="display-only",
            timeout_s=5.0,
            auth_message={
                "type": "worker.auth",
                "multisession_authorization": {
                    "kind": "multisession_key",
                    "multisession_key_id": issued["key"]["id"],
                },
                "market": {
                    "rings": ["ring-3"],
                    "price": {"amount": "1", "unit": "compute_credit"},
                    "capabilities": ["chat.completions"],
                    "max_concurrency": 1,
                },
            },
        )
        snapshot = client.start()
        assert client.websocket_upgrade_status == "HTTP/1.1 101 Switching Protocols"
        assert snapshot["accepted"]["type"] == "hub.auth.accepted"
        assert snapshot["accepted"]["ok"] is True
        assert snapshot["last_pong"]["type"] == "hub.pong.accepted"
    finally:
        if client is not None:
            client.close(reason="test_finished")
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=3)


def test_transport_smoke_detects_the_pre_patch_http10_upgrade(monkeypatch, capsys) -> None:
    """A 101 with HTTP/1.0 must FAIL the smoke even if direct frames work."""
    from main_computer.stable_hub import StableHubRequestHandler
    from tools.captain_websocket_transport_smoke import probe

    monkeypatch.setattr(StableHubRequestHandler, "protocol_version", "HTTP/1.0")
    hub, thread = _start_server(
        "dev-hub1", InMemoryStableMultiSessionKeyStore(), InMemoryStableWorkerSessionStore()
    )
    try:
        assert probe(f"http://127.0.0.1:{hub.server_port}", timeout_s=3.0) is False
        output = capsys.readouterr().out
        assert "HANDSHAKE HTTP/1.0 101 Switching Protocols" in output
        assert "FAIL_UPGRADE_PROTOCOL" in output
    finally:
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=3)
