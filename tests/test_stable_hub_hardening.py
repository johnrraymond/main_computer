from __future__ import annotations

import base64
import json
import os
import queue
import socket
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main_computer.multisession_key_signing import build_personal_sign_blob, private_key_to_address
from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import (
    InMemoryStableWorkerSessionStore,
    StableHubWorkerMarketDirectory,
    StableHubWorkerSessionDirectory,
)


DEV_TOPOLOGY = Path("deploy/stable-hub-lab/dev-topology.json")
WORKER_PRIVATE_KEY = "0x" + "66" * 32
REQUESTER_PRIVATE_KEY = "0x" + "77" * 32
WORKER_USER_SLUG = "user_slug_" + "h" * 40
REQUESTER_USER_SLUG = "user_slug_" + "q" * 40


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_test_topology(path: Path, *, hub1_url: str, hub3_url: str) -> None:
    document = json.loads(DEV_TOPOLOGY.read_text(encoding="utf-8"))
    for hub in document["hubs"]:
        if hub["hub_id"] == "dev-hub1":
            hub["hub_url"] = hub["public_url"] = hub1_url
        if hub["hub_id"] == "dev-hub3":
            hub["hub_url"] = hub["public_url"] = hub3_url
    document["entry_urls"] = [hub["hub_url"] for hub in document["hubs"]]
    path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")


def _post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:  # noqa: S310 - local test server
        return json.loads(response.read().decode("utf-8"))


def _post_json_status(url: str, payload: dict) -> tuple[int, dict]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 - local test server
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _signed_msk_request(
    *,
    private_key: str,
    user_slug: str,
    request_id: str,
    origin: str = "stable-hardening-unit-test",
) -> dict:
    now = datetime.now(timezone.utc)
    wallet_address = private_key_to_address(private_key)
    message = {
        "purpose": "request_multi_session_key",
        "wallet_address": wallet_address,
        "chain_id": "42424242",
        "request_id": request_id,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
        "origin": origin,
        "user_slug": user_slug,
    }
    return build_personal_sign_blob(
        message=message,
        private_key=private_key,
        wallet_address=wallet_address,
        chain_id="42424242",
    )


def _issue_msk(base_url: str, *, private_key: str, user_slug: str, request_id: str) -> dict:
    return _post_json(
        f"{base_url}/api/hub/v1/credits/multisession-keys/request",
        {
            "signed_request": _signed_msk_request(
                private_key=private_key,
                user_slug=user_slug,
                request_id=request_id,
            )
        },
    )


def _start_server(
    *,
    topology_path: Path,
    hub_id: str,
    bind_port: int,
    msk_store: InMemoryStableMultiSessionKeyStore,
    worker_store: InMemoryStableWorkerSessionStore,
    work_offer_timeout_seconds: float = 10.0,
):
    topology = load_stable_hub_topology(topology_path)
    server = create_stable_hub_server(
        topology=topology,
        hub_id=hub_id,
        bind_host="127.0.0.1",
        bind_port=bind_port,
        multisession_key_store=msk_store,
        worker_session_store=worker_store,
        work_offer_timeout_seconds=work_offer_timeout_seconds,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


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


def _valid_request_body(requester_msk_id: str, *, request_id: str = "req_hardening") -> dict:
    return {
        "request_id": request_id,
        "multisession_authorization": {
            "kind": "multisession_key",
            "multisession_key_id": requester_msk_id,
        },
        "work": {
            "ring": "ring-2",
            "max_price": {"amount": "0.10", "unit": "credit"},
            "capabilities": ["python"],
            "input": {"kind": "echo", "value": "hardening"},
        },
    }


def test_work_request_rejects_invalid_requester_msk_before_market_selection(tmp_path: Path) -> None:
    hub3_port = _free_port()
    hub3_url = f"http://127.0.0.1:{hub3_port}"
    topology_path = tmp_path / "stable-hub-invalid-msk-topology.json"
    _write_test_topology(topology_path, hub1_url=f"http://127.0.0.1:{_free_port()}", hub3_url=hub3_url)

    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    hub3, thread3 = _start_server(
        topology_path=topology_path,
        hub_id="dev-hub3",
        bind_port=hub3_port,
        msk_store=msk_store,
        worker_store=worker_store,
    )
    try:
        status, body = _post_json_status(
            f"{hub3_url}/api/hub/v1/work/requests",
            _valid_request_body("msk_missing_requester", request_id="req_invalid_requester_msk"),
        )
    finally:
        hub3.shutdown()
        hub3.server_close()
        thread3.join(timeout=2)

    assert status == 401
    assert body["ok"] is False
    assert body["error"] == "requester_msk_invalid"
    assert body["reason_code"]


def test_owner_hub_detects_selected_worker_owner_record_changed_before_offer(tmp_path: Path) -> None:
    hub3_port = _free_port()
    hub3_url = f"http://127.0.0.1:{hub3_port}"
    topology_path = tmp_path / "stable-hub-owner-changed-topology.json"
    _write_test_topology(topology_path, hub1_url=f"http://127.0.0.1:{_free_port()}", hub3_url=hub3_url)

    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    hub3, thread3 = _start_server(
        topology_path=topology_path,
        hub_id="dev-hub3",
        bind_port=hub3_port,
        msk_store=msk_store,
        worker_store=worker_store,
    )
    try:
        requester_msk = _issue_msk(
            hub3_url,
            private_key=REQUESTER_PRIVATE_KEY,
            user_slug=REQUESTER_USER_SLUG,
            request_id="owner-changed-requester-msk",
        )
        first_owner = hub3.worker_session_directory.record_connected(
            worker_id="worker-owner-changed",
            connection_id="conn_owner_changed_1",
            multisession_key_id="msk_worker_owner_changed",
            wallet_address=private_key_to_address(WORKER_PRIVATE_KEY),
            account_id="acct_worker_owner_changed",
        )
        hub3.worker_market_directory.record_worker_live(
            worker_id="worker-owner-changed",
            owner=first_owner,
            market_profile={
                "rings": ["ring-2"],
                "price": {"amount": "0.01", "unit": "credit"},
                "capabilities": ["python"],
                "max_concurrency": 1,
            },
            worker_msk_id="msk_worker_owner_changed",
            worker_wallet_address=private_key_to_address(WORKER_PRIVATE_KEY),
            worker_account_id="acct_worker_owner_changed",
        )
        second_owner = hub3.worker_session_directory.record_connected(
            worker_id="worker-owner-changed",
            connection_id="conn_owner_changed_2",
            multisession_key_id="msk_worker_owner_changed",
            wallet_address=private_key_to_address(WORKER_PRIVATE_KEY),
            account_id="acct_worker_owner_changed",
        )

        status, body = _post_json_status(
            f"{hub3_url}/api/hub/v1/work/requests",
            _valid_request_body(requester_msk["key"]["id"], request_id="req_owner_changed"),
        )
    finally:
        hub3.shutdown()
        hub3.server_close()
        thread3.join(timeout=2)

    assert second_owner["lease_epoch"] == first_owner["lease_epoch"] + 1
    assert status == 409
    assert body["ok"] is False
    assert body["error"] == "worker_owner_changed"
    assert body["selected_worker"]["connection_id"] == "conn_owner_changed_1"
    assert body["owner"]["connection_id"] == "conn_owner_changed_2"


def test_entry_hub_reports_remote_owner_handoff_failure_when_owner_hub_is_unavailable(tmp_path: Path) -> None:
    hub1_port = _free_port()
    hub3_port = _free_port()
    hub1_url = f"http://127.0.0.1:{hub1_port}"
    hub3_url = f"http://127.0.0.1:{hub3_port}"
    topology_path = tmp_path / "stable-hub-remote-owner-down-topology.json"
    _write_test_topology(topology_path, hub1_url=hub1_url, hub3_url=hub3_url)

    topology = load_stable_hub_topology(topology_path)
    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    owner_directory = StableHubWorkerSessionDirectory(topology=topology, hub_id="dev-hub3", store=worker_store)
    market_directory = StableHubWorkerMarketDirectory(topology=topology, hub_id="dev-hub3", store=worker_store)
    owner = owner_directory.record_connected(
        worker_id="worker-remote-owner-down",
        connection_id="conn_remote_owner_down",
        multisession_key_id="msk_worker_remote_owner_down",
        wallet_address=private_key_to_address(WORKER_PRIVATE_KEY),
        account_id="acct_worker_remote_owner_down",
    )
    market_directory.record_worker_live(
        worker_id="worker-remote-owner-down",
        owner=owner,
        market_profile={
            "rings": ["ring-2"],
            "price": {"amount": "0.01", "unit": "credit"},
            "capabilities": ["python"],
            "max_concurrency": 1,
        },
        worker_msk_id="msk_worker_remote_owner_down",
        worker_wallet_address=private_key_to_address(WORKER_PRIVATE_KEY),
        worker_account_id="acct_worker_remote_owner_down",
    )

    hub1, thread1 = _start_server(
        topology_path=topology_path,
        hub_id="dev-hub1",
        bind_port=hub1_port,
        msk_store=msk_store,
        worker_store=worker_store,
    )
    try:
        requester_msk = _issue_msk(
            hub1_url,
            private_key=REQUESTER_PRIVATE_KEY,
            user_slug=REQUESTER_USER_SLUG,
            request_id="remote-owner-down-requester-msk",
        )
        status, body = _post_json_status(
            f"{hub1_url}/api/hub/v1/work/requests",
            _valid_request_body(requester_msk["key"]["id"], request_id="req_remote_owner_down"),
        )
    finally:
        hub1.shutdown()
        hub1.server_close()
        thread1.join(timeout=2)

    assert status == 502
    assert body["ok"] is False
    assert body["error"] == "owner_hub_handoff_failed"
    assert body["owner_hub"] == {"hub_id": "dev-hub3", "hub_url": hub3_url}
    assert body["selected_worker"]["worker_id"] == "worker-remote-owner-down"


def test_owner_hub_times_out_when_worker_does_not_accept_offer(tmp_path: Path) -> None:
    hub3_port = _free_port()
    hub3_url = f"http://127.0.0.1:{hub3_port}"
    topology_path = tmp_path / "stable-hub-offer-timeout-topology.json"
    _write_test_topology(topology_path, hub1_url=f"http://127.0.0.1:{_free_port()}", hub3_url=hub3_url)

    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    hub3, thread3 = _start_server(
        topology_path=topology_path,
        hub_id="dev-hub3",
        bind_port=hub3_port,
        msk_store=msk_store,
        worker_store=worker_store,
        work_offer_timeout_seconds=0.15,
    )
    sock: socket.socket | None = None
    post_thread: threading.Thread | None = None
    result_queue: queue.Queue[tuple[int, dict] | BaseException] = queue.Queue()
    try:
        worker_msk = _issue_msk(
            hub3_url,
            private_key=WORKER_PRIVATE_KEY,
            user_slug=WORKER_USER_SLUG,
            request_id="offer-timeout-worker-msk",
        )
        requester_msk = _issue_msk(
            hub3_url,
            private_key=REQUESTER_PRIVATE_KEY,
            user_slug=REQUESTER_USER_SLUG,
            request_id="offer-timeout-requester-msk",
        )

        sock = _ws_connect("127.0.0.1", hub3_port, "/api/hub/v1/workers/live-session")
        _ws_send_json(
            sock,
            {
                "type": "worker.auth",
                "worker_id": "worker-offer-timeout",
                "multisession_authorization": {
                    "kind": "multisession_key",
                    "multisession_key_id": worker_msk["key"]["id"],
                },
                "market": {
                    "rings": ["ring-2"],
                    "price": {"amount": "0.01", "unit": "credit"},
                    "capabilities": ["python"],
                    "max_concurrency": 1,
                },
            },
        )
        accepted = _ws_recv_json(sock)
        ping = _ws_recv_json(sock)
        _ws_send_json(
            sock,
            {
                "type": "worker.pong",
                "connection_id": accepted["connection_id"],
                "ping_id": ping["ping_id"],
            },
        )
        assert _ws_recv_json(sock)["type"] == "hub.pong.accepted"

        def _submit_work() -> None:
            try:
                result_queue.put(
                    _post_json_status(
                        f"{hub3_url}/api/hub/v1/work/requests",
                        _valid_request_body(requester_msk["key"]["id"], request_id="req_offer_timeout"),
                    )
                )
            except BaseException as exc:  # pragma: no cover - surfaced by assertion below
                result_queue.put(exc)

        post_thread = threading.Thread(target=_submit_work, daemon=True)
        post_thread.start()
        offer = _ws_recv_json(sock)
        assert offer["type"] == "hub.work.offer"
        assert offer["request_id"] == "req_offer_timeout"

        response = result_queue.get(timeout=3)
        assert not isinstance(response, BaseException), response
        status, body = response
        post_thread.join(timeout=2)

        assert status == 504
        assert body["ok"] is False
        assert body["error"] == "worker_offer_timeout"
        assert body["worker_id"] == "worker-offer-timeout"
        assert hub3.accepted_work_session_directory.get_session(offer["session_id"]) is None
        market_record = hub3.worker_market_directory.get_worker("worker-offer-timeout")
        assert market_record is not None
        assert market_record["active_sessions"] == 0
    finally:
        if sock is not None:
            try:
                _ws_send_json(sock, {"type": "worker.close"})
            except OSError:
                pass
            sock.close()
        if post_thread is not None:
            post_thread.join(timeout=2)
        hub3.shutdown()
        hub3.server_close()
        thread3.join(timeout=2)
