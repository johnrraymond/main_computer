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


DEV_TOPOLOGY = Path("deploy/stable-hub-lab/dev-topology.json")
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


def _signed_msk_request(*, request_id: str = "stable-worker-live-session-msk") -> dict:
    now = datetime.now(timezone.utc)
    wallet_address = private_key_to_address(DEV_PRIVATE_KEY)
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
        private_key=DEV_PRIVATE_KEY,
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


def test_worker_live_session_uses_open_websocket_and_fdb_style_owner_directory() -> None:
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
                "worker_id": "worker-live-session-1",
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
        assert accepted["worker_id"] == "worker-live-session-1"
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
        assert session.worker_id == "worker-live-session-1"
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
            f"{hub1_url}/api/hub/v1/workers/worker-live-session-1/owner"
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

        market_record = hub1.worker_market_directory.get_worker("worker-live-session-1")
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
        assert selected["worker_id"] == "worker-live-session-1"
        assert selected["owner_hub_id"] == "dev-hub3"
        assert selected["partition"] == "ring-2"

        _ws_send_json(sock, {"type": "worker.close"})
        sock.close()
        sock = None
        deadline = time.monotonic() + 2.0
        closed = None
        while time.monotonic() < deadline:
            closed = hub1.worker_session_directory.get_owner("worker-live-session-1")
            if closed and closed.get("status") == "closed":
                break
            time.sleep(0.05)
        assert closed is not None
        assert closed["status"] == "closed"
        assert closed["owner_hub_id"] == "dev-hub3"
        closed_market = hub1.worker_market_directory.get_worker("worker-live-session-1")
        assert closed_market is not None
        assert closed_market["status"] == "closed"
    finally:
        if sock is not None:
            sock.close()
        hub1.shutdown()
        hub3.shutdown()
        hub1.server_close()
        hub3.server_close()
        thread1.join(timeout=2)
        thread3.join(timeout=2)


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
