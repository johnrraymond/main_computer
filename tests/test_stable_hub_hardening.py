from __future__ import annotations

import base64
import json
import os
import queue
import socket
import threading

import pytest
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
    StableHubPayoutLedgerDirectory,
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


def _get_json_status(url: str) -> tuple[int, dict]:
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 - local test server
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _connect_live_worker(
    *,
    hub_port: int,
    worker_msk_id: str,
    worker_id: str,
    price: str = "0.01",
    max_concurrency: int = 1,
) -> tuple[socket.socket, dict]:
    sock = _ws_connect("127.0.0.1", hub_port, "/api/hub/v1/workers/live-session")
    _ws_send_json(
        sock,
        {
            "type": "worker.auth",
            "worker_id": worker_id,
            "multisession_authorization": {
                "kind": "multisession_key",
                "multisession_key_id": worker_msk_id,
            },
            "market": {
                "rings": ["ring-2"],
                "price": {"amount": price, "unit": "credit"},
                "capabilities": ["python"],
                "max_concurrency": max_concurrency,
            },
        },
    )
    accepted = _ws_recv_json(sock)
    assert accepted["type"] == "hub.auth.accepted"
    ping = _ws_recv_json(sock)
    assert ping["type"] == "hub.ping"
    _ws_send_json(
        sock,
        {
            "type": "worker.pong",
            "connection_id": accepted["connection_id"],
            "ping_id": ping["ping_id"],
        },
    )
    assert _ws_recv_json(sock)["type"] == "hub.pong.accepted"
    return sock, accepted


def _submit_work_async(
    *,
    hub_url: str,
    requester_msk_id: str,
    request_id: str,
    max_price: str = "0.10",
    value: str = "hardening",
) -> tuple[threading.Thread, queue.Queue[tuple[int, dict] | BaseException]]:
    result_queue: queue.Queue[tuple[int, dict] | BaseException] = queue.Queue()

    def _submit() -> None:
        try:
            body = _valid_request_body(requester_msk_id, request_id=request_id)
            body["work"]["max_price"] = {"amount": max_price, "unit": "credit"}
            body["work"]["input"] = {"kind": "echo", "value": value}
            result_queue.put(_post_json_status(f"{hub_url}/api/hub/v1/work/requests", body))
        except BaseException as exc:  # pragma: no cover - surfaced by test assertion
            result_queue.put(exc)

    thread = threading.Thread(target=_submit, daemon=True)
    thread.start()
    return thread, result_queue


def _accept_current_offer(sock: socket.socket, *, worker_id: str) -> dict:
    offer = _ws_recv_json(sock)
    assert offer["type"] == "hub.work.offer"
    _ws_send_json(
        sock,
        {
            "type": "worker.work.accepted",
            "worker_id": worker_id,
            "session_id": offer["session_id"],
            "run_id": offer["run_id"],
            "request_id": offer["request_id"],
        },
    )
    return offer


def _finish_offer_with_result(sock: socket.socket, offer: dict, *, worker_id: str, value: str = "ok") -> dict:
    _ws_send_json(
        sock,
        {
            "type": "worker.work.result",
            "worker_id": worker_id,
            "session_id": offer["session_id"],
            "run_id": offer["run_id"],
            "request_id": offer["request_id"],
            "result": {"value": value},
        },
    )
    ack = _ws_recv_json(sock)
    assert ack["type"] == "hub.work.result.accepted"
    assert ack["ok"] is True
    return ack


def _finish_offer_with_failure(sock: socket.socket, offer: dict, *, worker_id: str, message: str = "boom") -> dict:
    _ws_send_json(
        sock,
        {
            "type": "worker.work.failed",
            "worker_id": worker_id,
            "session_id": offer["session_id"],
            "run_id": offer["run_id"],
            "request_id": offer["request_id"],
            "error": {"message": message},
        },
    )
    ack = _ws_recv_json(sock)
    assert ack["type"] == "hub.work.failed.accepted"
    assert ack["ok"] is True
    return ack


def _new_local_hub_with_worker(tmp_path: Path, *, test_name: str, worker_id: str, worker_price: str = "0.01", max_concurrency: int = 1):
    hub3_port = _free_port()
    hub3_url = f"http://127.0.0.1:{hub3_port}"
    topology_path = tmp_path / f"{test_name}-topology.json"
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
    worker_msk = _issue_msk(
        hub3_url,
        private_key=WORKER_PRIVATE_KEY,
        user_slug=WORKER_USER_SLUG,
        request_id=f"{test_name}-worker-msk",
    )
    requester_msk = _issue_msk(
        hub3_url,
        private_key=REQUESTER_PRIVATE_KEY,
        user_slug=REQUESTER_USER_SLUG,
        request_id=f"{test_name}-requester-msk",
    )
    sock, auth = _connect_live_worker(
        hub_port=hub3_port,
        worker_msk_id=worker_msk["key"]["id"],
        worker_id=worker_id,
        price=worker_price,
        max_concurrency=max_concurrency,
    )
    return hub3, thread3, hub3_url, sock, auth, requester_msk


def test_malformed_continuation_stream_id_returns_400_instead_of_handler_crash(tmp_path: Path) -> None:
    hub3_port = _free_port()
    hub3_url = f"http://127.0.0.1:{hub3_port}"
    topology_path = tmp_path / "stable-hub-malformed-stream-topology.json"
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
        status, body = _get_json_status(f"{hub3_url}/api/hub/v1/work/sessions/bad!/stream")
    finally:
        hub3.shutdown()
        hub3.server_close()
        thread3.join(timeout=2)

    assert status == 400
    assert body["ok"] is False
    assert "session_id" in body["error"]


def test_duplicate_request_id_returns_existing_session_without_second_worker_offer(tmp_path: Path) -> None:
    worker_id = "worker-duplicate-request"
    hub3, thread3, hub3_url, sock, _auth, requester_msk = _new_local_hub_with_worker(
        tmp_path,
        test_name="duplicate-request",
        worker_id=worker_id,
    )
    try:
        post_thread, result_queue = _submit_work_async(
            hub_url=hub3_url,
            requester_msk_id=requester_msk["key"]["id"],
            request_id="req_duplicate_stable",
        )
        offer = _accept_current_offer(sock, worker_id=worker_id)
        response = result_queue.get(timeout=3)
        assert not isinstance(response, BaseException), response
        status, body = response
        post_thread.join(timeout=2)
        assert status == 200
        assert body["session_id"] == offer["session_id"]

        _finish_offer_with_result(sock, offer, worker_id=worker_id)

        duplicate_status, duplicate_body = _post_json_status(
            f"{hub3_url}/api/hub/v1/work/requests",
            _valid_request_body(requester_msk["key"]["id"], request_id="req_duplicate_stable"),
        )
        assert duplicate_status == 200
        assert duplicate_body["idempotent"] is True
        assert duplicate_body["duplicate_request_id"] is True
        assert duplicate_body["session_id"] == offer["session_id"]

        sock.settimeout(0.2)
        with pytest.raises((TimeoutError, socket.timeout)):
            _ws_recv_json(sock)
        sock.settimeout(None)

        mismatch_body = _valid_request_body(requester_msk["key"]["id"], request_id="req_duplicate_stable")
        mismatch_body["work"]["input"] = {"kind": "echo", "value": "different"}
        mismatch_status, mismatch_response = _post_json_status(
            f"{hub3_url}/api/hub/v1/work/requests",
            mismatch_body,
        )
        assert mismatch_status == 409
        assert mismatch_response["error"] == "duplicate_request_id_work_mismatch"
    finally:
        try:
            _ws_send_json(sock, {"type": "worker.close"})
        except OSError:
            pass
        sock.close()
        hub3.shutdown()
        hub3.server_close()
        thread3.join(timeout=2)


def test_duplicate_request_id_after_timeout_is_rejected_before_new_offer(tmp_path: Path) -> None:
    hub3_port = _free_port()
    hub3_url = f"http://127.0.0.1:{hub3_port}"
    topology_path = tmp_path / "stable-hub-duplicate-after-timeout-topology.json"
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
    try:
        worker_msk = _issue_msk(
            hub3_url,
            private_key=WORKER_PRIVATE_KEY,
            user_slug=WORKER_USER_SLUG,
            request_id="duplicate-timeout-worker-msk",
        )
        requester_msk = _issue_msk(
            hub3_url,
            private_key=REQUESTER_PRIVATE_KEY,
            user_slug=REQUESTER_USER_SLUG,
            request_id="duplicate-timeout-requester-msk",
        )
        sock, _auth = _connect_live_worker(
            hub_port=hub3_port,
            worker_msk_id=worker_msk["key"]["id"],
            worker_id="worker-duplicate-timeout",
        )
        post_thread, result_queue = _submit_work_async(
            hub_url=hub3_url,
            requester_msk_id=requester_msk["key"]["id"],
            request_id="req_duplicate_timeout",
        )
        offer = _ws_recv_json(sock)
        assert offer["type"] == "hub.work.offer"
        response = result_queue.get(timeout=3)
        assert not isinstance(response, BaseException), response
        status, body = response
        post_thread.join(timeout=2)
        assert status == 504
        assert body["error"] == "worker_offer_timeout"

        duplicate_status, duplicate_body = _post_json_status(
            f"{hub3_url}/api/hub/v1/work/requests",
            _valid_request_body(requester_msk["key"]["id"], request_id="req_duplicate_timeout"),
        )
        assert duplicate_status == 409
        assert duplicate_body["error"] == "duplicate_request_id_already_reserved"
        assert duplicate_body["hold_status"] == "released"

        sock.settimeout(0.2)
        with pytest.raises((TimeoutError, socket.timeout)):
            _ws_recv_json(sock)
        sock.settimeout(None)
    finally:
        if sock is not None:
            try:
                _ws_send_json(sock, {"type": "worker.close"})
            except OSError:
                pass
            sock.close()
        hub3.shutdown()
        hub3.server_close()
        thread3.join(timeout=2)


def test_worker_terminal_messages_are_bound_to_session_run_request_and_worker(tmp_path: Path) -> None:
    worker_id = "worker-terminal-hardening"
    hub3, thread3, hub3_url, sock, _auth, requester_msk = _new_local_hub_with_worker(
        tmp_path,
        test_name="terminal-hardening",
        worker_id=worker_id,
    )
    try:
        post_thread, result_queue = _submit_work_async(
            hub_url=hub3_url,
            requester_msk_id=requester_msk["key"]["id"],
            request_id="req_terminal_bindings",
        )
        offer = _accept_current_offer(sock, worker_id=worker_id)
        response = result_queue.get(timeout=3)
        assert not isinstance(response, BaseException), response
        assert response[0] == 200
        status_before, continuation_before = _get_json_status(response[1]["continuation_url"])
        assert status_before == 200
        assert continuation_before["status"] == "accepted"

        _ws_send_json(
            sock,
            {
                "type": "worker.work.result",
                "worker_id": worker_id,
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": "req_wrong",
                "result": {"value": "bad"},
            },
        )
        wrong_request_ack = _ws_recv_json(sock)
        assert wrong_request_ack["type"] == "hub.error"
        assert "request_id mismatch" in wrong_request_ack["error"]

        _ws_send_json(
            sock,
            {
                "type": "worker.work.result",
                "worker_id": "other-worker",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
                "result": {"value": "bad"},
            },
        )
        wrong_worker_ack = _ws_recv_json(sock)
        assert wrong_worker_ack["type"] == "hub.error"
        assert "worker_id mismatch" in wrong_worker_ack["error"]

        _ws_send_json(
            sock,
            {
                "type": "worker.work.result",
                "worker_id": worker_id,
                "session_id": offer["session_id"],
                "run_id": "run_wrong",
                "request_id": offer["request_id"],
                "result": {"value": "bad"},
            },
        )
        wrong_run_ack = _ws_recv_json(sock)
        assert wrong_run_ack["type"] == "hub.error"
        assert "run_id mismatch" in wrong_run_ack["error"]

        _finish_offer_with_result(sock, offer, worker_id=worker_id)
        status_after, continuation_after = _get_json_status(response[1]["continuation_url"])
        assert status_after == 200
        assert continuation_after["status"] == "succeeded"
        payout_status = _get_json_status(f"{hub3_url}/api/hub/v1/payout/status")[1]["payout"]
        assert len(payout_status["charges"]) == 1
    finally:
        try:
            _ws_send_json(sock, {"type": "worker.close"})
        except OSError:
            pass
        sock.close()
        hub3.shutdown()
        hub3.server_close()
        thread3.join(timeout=2)


def test_worker_terminal_messages_are_idempotent_after_success_or_failure(tmp_path: Path) -> None:
    worker_id = "worker-terminal-idempotent"
    hub3, thread3, hub3_url, sock, _auth, requester_msk = _new_local_hub_with_worker(
        tmp_path,
        test_name="terminal-idempotent",
        worker_id=worker_id,
    )
    try:
        first_thread, first_queue = _submit_work_async(
            hub_url=hub3_url,
            requester_msk_id=requester_msk["key"]["id"],
            request_id="req_terminal_success",
        )
        first_offer = _accept_current_offer(sock, worker_id=worker_id)
        first_response = first_queue.get(timeout=3)
        assert not isinstance(first_response, BaseException), first_response
        assert first_response[0] == 200
        _finish_offer_with_result(sock, first_offer, worker_id=worker_id)
        _ws_send_json(
            sock,
            {
                "type": "worker.work.failed",
                "worker_id": worker_id,
                "session_id": first_offer["session_id"],
                "run_id": first_offer["run_id"],
                "request_id": first_offer["request_id"],
                "error": {"message": "late failure"},
            },
        )
        late_failure = _ws_recv_json(sock)
        assert late_failure["type"] == "hub.work.terminal.accepted"
        assert late_failure["idempotent"] is True
        assert late_failure["status"] == "succeeded"

        second_thread, second_queue = _submit_work_async(
            hub_url=hub3_url,
            requester_msk_id=requester_msk["key"]["id"],
            request_id="req_terminal_failure",
        )
        second_offer = _accept_current_offer(sock, worker_id=worker_id)
        second_response = second_queue.get(timeout=3)
        assert not isinstance(second_response, BaseException), second_response
        assert second_response[0] == 200
        _finish_offer_with_failure(sock, second_offer, worker_id=worker_id)
        _ws_send_json(
            sock,
            {
                "type": "worker.work.result",
                "worker_id": worker_id,
                "session_id": second_offer["session_id"],
                "run_id": second_offer["run_id"],
                "request_id": second_offer["request_id"],
                "result": {"value": "late success"},
            },
        )
        late_result = _ws_recv_json(sock)
        assert late_result["type"] == "hub.work.terminal.accepted"
        assert late_result["idempotent"] is True
        assert late_result["status"] == "failed"

        first_thread.join(timeout=2)
        second_thread.join(timeout=2)
        payout_status = _get_json_status(f"{hub3_url}/api/hub/v1/payout/status")[1]["payout"]
        assert len(payout_status["charges"]) == 1
        assert len([hold for hold in payout_status["holds"] if hold["status"] == "charged"]) == 1
        assert len([hold for hold in payout_status["holds"] if hold["status"] == "released"]) == 1
    finally:
        try:
            _ws_send_json(sock, {"type": "worker.close"})
        except OSError:
            pass
        sock.close()
        hub3.shutdown()
        hub3.server_close()
        thread3.join(timeout=2)


def test_worker_capacity_and_requester_funds_reject_before_offer(tmp_path: Path) -> None:
    worker_id = "worker-capacity-and-funds"
    hub3, thread3, hub3_url, sock, _auth, requester_msk = _new_local_hub_with_worker(
        tmp_path,
        test_name="capacity-funds",
        worker_id=worker_id,
        worker_price="0.01",
        max_concurrency=1,
    )
    try:
        first_thread, first_queue = _submit_work_async(
            hub_url=hub3_url,
            requester_msk_id=requester_msk["key"]["id"],
            request_id="req_capacity_held",
        )
        first_offer = _accept_current_offer(sock, worker_id=worker_id)
        first_response = first_queue.get(timeout=3)
        assert not isinstance(first_response, BaseException), first_response
        assert first_response[0] == 200

        second_status, second_body = _post_json_status(
            f"{hub3_url}/api/hub/v1/work/requests",
            _valid_request_body(requester_msk["key"]["id"], request_id="req_capacity_rejected"),
        )
        assert second_status == 409
        assert second_body["error"] == "worker_not_live"
        sock.settimeout(0.2)
        with pytest.raises((TimeoutError, socket.timeout)):
            _ws_recv_json(sock)
        sock.settimeout(None)

        _finish_offer_with_result(sock, first_offer, worker_id=worker_id)

        account_id = requester_msk["key"]["account_id"]
        hub3.payout_ledger_directory.fund_account(
            account_id=account_id,
            wallet_address=requester_msk["key"]["wallet_address"],
            credits="0.005",
            replace=True,
        )
        poor_status, poor_body = _post_json_status(
            f"{hub3_url}/api/hub/v1/work/requests",
            _valid_request_body(requester_msk["key"]["id"], request_id="req_insufficient_funds"),
        )
        assert poor_status == 402
        assert poor_body["error"] == "requester_funds_unavailable"
        sock.settimeout(0.2)
        with pytest.raises((TimeoutError, socket.timeout)):
            _ws_recv_json(sock)
        sock.settimeout(None)
    finally:
        try:
            _ws_send_json(sock, {"type": "worker.close"})
        except OSError:
            pass
        sock.close()
        hub3.shutdown()
        hub3.server_close()
        thread3.join(timeout=2)


def test_payout_claim_settlement_and_bridge_boundaries_are_worker_scoped() -> None:
    topology = load_stable_hub_topology(DEV_TOPOLOGY)
    store = InMemoryStableWorkerSessionStore()
    ledger = StableHubPayoutLedgerDirectory(topology=topology, hub_id="dev-hub3", store=store)

    empty_claim = ledger.record_worker_claim(worker_id="worker-empty", idempotency_key="empty-claim")
    assert empty_claim["status"] == "empty"
    assert empty_claim["earning_ids"] == []
    assert empty_claim["amount"] == "0"

    ledger.fund_account(
        account_id="acct-payout-hardening",
        wallet_address="0x" + "12" * 20,
        credits="1",
        replace=True,
    )
    hold = ledger.create_hold(
        account_id="acct-payout-hardening",
        wallet_address="0x" + "12" * 20,
        request_id="req_payout_scope",
        session_id="sess_payout_scope",
        run_id="run_payout_scope",
        worker_id="worker-payout-a",
        selected_price={"amount": "0.25", "unit": "credit"},
        requester_max_price={"amount": "1", "unit": "credit"},
        partition="ring-2",
    )
    charged = ledger.charge_hold(
        hold_id=hold["hold_id"],
        session_id="sess_payout_scope",
        request_id="req_payout_scope",
        worker_id="worker-payout-a",
        result={"value": "ok"},
    )
    earning_id = charged["worker_earning"]["earning_id"]
    with pytest.raises(Exception, match="not claimable"):
        ledger.record_worker_claim(worker_id="worker-payout-b", earning_ids=[earning_id])

    claim = ledger.record_worker_claim(
        worker_id="worker-payout-a",
        earning_ids=[earning_id],
        idempotency_key="claim-a",
    )
    with pytest.raises(Exception, match="not settleable"):
        ledger.create_worker_settlement_batch(
            worker_id="worker-payout-b",
            claim_ids=[claim["claim_id"]],
        )

    batch = ledger.create_worker_settlement_batch(
        worker_id="worker-payout-a",
        claim_ids=[claim["claim_id"]],
        idempotency_key="settle-a",
    )
    with pytest.raises(Exception, match="must be settled"):
        ledger.request_bridge_payout(
            worker_id="worker-payout-a",
            batch_id=batch["batch_id"],
            idempotency_key="bridge-before-settle",
        )

    settled = ledger.settle_worker_settlement_batch(
        batch_id=batch["batch_id"],
        settlement_reference="settled-a",
    )
    assert settled["status"] == "settled"
    with pytest.raises(Exception, match="not bridgeable"):
        ledger.request_bridge_payout(
            worker_id="worker-payout-b",
            batch_id=batch["batch_id"],
            idempotency_key="bridge-wrong-worker",
        )

    bridge = ledger.request_bridge_payout(
        worker_id="worker-payout-a",
        batch_id=batch["batch_id"],
        idempotency_key="bridge-a",
    )
    assert bridge["status"] == "requested"
    failed = ledger.fail_bridge_payout(bridge_payout_id=bridge["bridge_payout_id"], reason="temporary")
    assert failed["status"] == "failed"
    recovered = ledger.confirm_bridge_payout(
        bridge_payout_id=bridge["bridge_payout_id"],
        settlement_reference="bridge-recovered",
    )
    assert recovered["status"] == "confirmed"
    assert recovered["previous_status"] == "failed"
    still_confirmed = ledger.fail_bridge_payout(bridge_payout_id=bridge["bridge_payout_id"], reason="late")
    assert still_confirmed["status"] == "confirmed"

    status = ledger.status()
    assert status["ledger_version"] == "stable-hub-exp-compatible-credit-ledger-v1"
    assert status["exp_compatible_golden_path"] is True
    assert any(tx["transaction_type"] == "hold_created" for tx in status["transactions"])
    assert any(tx["transaction_type"] == "request_charged" for tx in status["transactions"])
    assert any(tx["transaction_type"] == "worker_claimed" for tx in status["transactions"])
    assert any(tx["transaction_type"] == "batch_settled" for tx in status["transactions"])
    assert any(tx["transaction_type"] == "withdrawal_released" for tx in status["transactions"])
    assert hold["credit_wei"] == "250000000000000000"
    assert charged["charge"]["charged_credit_wei"] == hold["credit_wei"]
    assert charged["worker_earning"]["earned_credit_wei"] == hold["credit_wei"]
    assert claim["claimed_credit_wei"] == hold["credit_wei"]
    assert settled["total_credit_wei_published"] == hold["credit_wei"]
    assert recovered["credit_wei"] == hold["credit_wei"]
    assert any(event["event_type"] == "hub.hold.created" for event in status["bridge_audit"])
    assert any(event["event_type"] == "hub.hold.charged" for event in status["bridge_audit"])
    assert any(event["event_type"] == "hub.worker.earning.recorded" for event in status["bridge_audit"])
