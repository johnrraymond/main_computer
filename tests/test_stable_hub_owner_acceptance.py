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
from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore


DEV_TOPOLOGY = Path("deploy/hub-topology/dev-topology.json")
WORKER_PRIVATE_KEY = "0x" + "44" * 32
REQUESTER_PRIVATE_KEY = "0x" + "55" * 32
WORKER_USER_SLUG = "user_slug_" + "l" * 40
REQUESTER_USER_SLUG = "user_slug_" + "r" * 40


def _post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:  # noqa: S310 - local test server
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str) -> dict:
    with urlopen(url, timeout=5) as response:  # noqa: S310 - local test server
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
    origin: str = "stable-owner-acceptance-unit-test",
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


def test_owner_hub_accepts_local_work_over_live_worker_socket_and_saves_session() -> None:
    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    hub3, thread3 = _start_server("dev-hub3", msk_store, worker_store)
    sock: socket.socket | None = None
    post_thread: threading.Thread | None = None
    result_queue: queue.Queue[dict | BaseException] = queue.Queue()
    try:
        hub3_url = f"http://127.0.0.1:{hub3.server_port}"
        worker_msk = _issue_msk(
            hub3_url,
            private_key=WORKER_PRIVATE_KEY,
            user_slug=WORKER_USER_SLUG,
            request_id="owner-acceptance-worker-msk",
        )
        requester_msk = _issue_msk(
            hub3_url,
            private_key=REQUESTER_PRIVATE_KEY,
            user_slug=REQUESTER_USER_SLUG,
            request_id="owner-acceptance-requester-msk",
        )

        sock = _ws_connect("127.0.0.1", hub3.server_port, "/api/hub/v1/workers/live-session")
        _ws_send_json(
            sock,
            {
                "type": "worker.auth", "execution_protocol": "durable-authorization-v1",
                "worker_id": "worker-owner-local-acceptance",
                "multisession_authorization": {
                    "kind": "multisession_key",
                    "multisession_key_id": worker_msk["key"]["id"],
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

        request_body = {
            "request_id": "req_owner_local_acceptance",
            "multisession_authorization": {
                "kind": "multisession_key",
                "multisession_key_id": requester_msk["key"]["id"],
            },
            "work": {
                "ring": "ring-2",
                "max_price": {"amount": "0.10", "unit": "credit"},
                "capabilities": ["python"],
                "input": {"kind": "echo", "value": "hello"},
            },
        }

        def _submit_work() -> None:
            try:
                result_queue.put(_post_json(f"{hub3_url}/api/hub/v1/work/requests", request_body))
            except BaseException as exc:  # pragma: no cover - failures are asserted below
                result_queue.put(exc)

        post_thread = threading.Thread(target=_submit_work, daemon=True)
        post_thread.start()

        offer = _ws_recv_json(sock)
        assert offer["type"] == "hub.work.offer"
        assert offer["request_id"] == request_body["request_id"]
        assert offer["worker_id"] == accepted["connection_id"]
        assert offer["partition"] == "ring-2"
        assert offer["task_queue"] == "main-computer-work-ring-2"
        assert offer["execution_authorization_required"] is True
        assert offer["work"] == {"execution_authorization_pending": True}
        assert offer["session_id"].startswith("sess_")
        assert offer["run_id"].startswith("run_")

        _ws_send_json(
            sock,
            {
                "type": "worker.work.accepted",
                "session_id": offer["session_id"],
                "request_id": offer["request_id"],
            },
        )

        response = result_queue.get(timeout=3)
        assert not isinstance(response, BaseException), response
        post_thread.join(timeout=2)

        assert response["ok"] is True
        assert response["accepted"] is True
        assert response["session_id"] == offer["session_id"]
        assert response["run_id"] == offer["run_id"]
        assert response["request_id"] == request_body["request_id"]
        assert response["worker_id"] == accepted["connection_id"]
        assert response["owner_hub_id"] == "dev-hub3"
        assert response["partition"] == "ring-2"
        assert response["task_queue"] == "main-computer-work-ring-2"
        assert response["execution"] == {
            "backend": "temporal",
            "namespace": "main-computer-dev",
            "workflow_type": "WorkSessionWorkflow",
            "workflow_id": offer["session_id"],
            "task_queue": "main-computer-work-ring-2",
            "status": "accepted",
        }
        expected_owner_hub_url = accepted["owner"]["owner_hub_url"]
        assert response["execution_hub"] == {"hub_id": "dev-hub3", "hub_url": expected_owner_hub_url}
        assert response["continuation_url"] == (
            f"{expected_owner_hub_url}/api/hub/v1/work/sessions/{offer['session_id']}/stream"
        )
        assert response["continuation"] == {
            "direct": True,
            "stream_path": f"/api/hub/v1/work/sessions/{offer['session_id']}/stream",
            "hub_id": "dev-hub3",
            "hub_url": expected_owner_hub_url,
        }

        continuation = _get_json(f"{hub3_url}/api/hub/v1/work/sessions/{offer['session_id']}/stream")
        assert continuation["ok"] is True
        assert continuation["session_id"] == offer["session_id"]
        assert continuation["run_id"] == offer["run_id"]
        assert continuation["status"] == "accepted"
        assert continuation["execution_hub"] == {"hub_id": "dev-hub3", "hub_url": expected_owner_hub_url}
        assert continuation["continuation_url"] == response["continuation_url"]
        assert continuation["stream"] == {
            "transport": "stable-hub-session-stream",
            "mode": "accepted-session-state",
            "status": "accepted",
            "source": "accepted-session-record",
        }

        saved = hub3.accepted_work_session_directory.get_session(offer["session_id"])
        assert saved is not None
        assert saved["status"] == "accepted"
        assert saved["session_id"] == offer["session_id"]
        assert saved["run_id"] == offer["run_id"]
        assert saved["requester_msk_id"] == requester_msk["key"]["id"]
        assert saved["requester_account_id"] == requester_msk["key"]["account_id"]
        assert saved["requester_wallet_address"] == requester_msk["key"]["wallet_address"]
        assert saved["worker_id"] == accepted["connection_id"]
        assert saved["worker_connection_id"] == accepted["connection_id"]
        assert saved["partition"] == "ring-2"
        assert saved["task_queue"] == "main-computer-work-ring-2"
        assert saved["execution"] == {
            "backend": "temporal",
            "namespace": "main-computer-dev",
            "workflow_type": "WorkSessionWorkflow",
            "workflow_id": offer["session_id"],
            "task_queue": "main-computer-work-ring-2",
            "status": "accepted",
        }
        assert saved["worker_acceptance"]["type"] == "worker.work.accepted"

        market_record = hub3.worker_market_directory.get_worker(accepted["connection_id"])
        assert market_record is not None
        assert market_record["active_sessions"] == 1
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


def test_owner_hub_reports_local_owner_record_without_socket_as_not_local() -> None:
    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    hub3, thread3 = _start_server("dev-hub3", msk_store, worker_store)
    try:
        hub3_url = f"http://127.0.0.1:{hub3.server_port}"
        requester_msk = _issue_msk(
            hub3_url,
            private_key=REQUESTER_PRIVATE_KEY,
            user_slug=REQUESTER_USER_SLUG,
            request_id="owner-not-local-requester-msk",
        )
        owner = hub3.worker_session_directory.record_connected(
            worker_id="worker-owner-record-no-socket",
            connection_id="conn_owner_record_without_socket",
            multisession_key_id="msk_worker_without_socket",
            wallet_address=private_key_to_address(WORKER_PRIVATE_KEY),
            account_id="acct_worker_without_socket",
        )
        hub3.worker_market_directory.record_worker_live(
            worker_id="worker-owner-record-no-socket",
            owner=owner,
            market_profile={
                "rings": ["ring-2"],
                "price": {"amount": "0.01", "unit": "credit"},
                "capabilities": ["python"],
                "max_concurrency": 1,
            },
            worker_msk_id="msk_worker_without_socket",
            worker_wallet_address=private_key_to_address(WORKER_PRIVATE_KEY),
            worker_account_id="acct_worker_without_socket",
        )

        status, body = _post_json_status(
            f"{hub3_url}/api/hub/v1/work/requests",
            {
                "request_id": "req_owner_record_without_socket",
                "multisession_authorization": {
                    "kind": "multisession_key",
                    "multisession_key_id": requester_msk["key"]["id"],
                },
                "work": {
                    "ring": "ring-2",
                    "max_price": {"amount": "0.10", "unit": "credit"},
                    "capabilities": ["python"],
                    "input": {},
                },
            },
        )
    finally:
        hub3.shutdown()
        hub3.server_close()
        thread3.join(timeout=2)

    assert status == 409
    assert body["ok"] is False
    assert body["error"] == "worker_owner_not_local"
    assert body["worker_id"] == "worker-owner-record-no-socket"
    assert body["owner"]["connection_id"] == "conn_owner_record_without_socket"


def test_owner_hub_returns_worker_not_live_when_no_market_candidate_matches() -> None:
    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    hub3, thread3 = _start_server("dev-hub3", msk_store, worker_store)
    try:
        hub3_url = f"http://127.0.0.1:{hub3.server_port}"
        requester_msk = _issue_msk(
            hub3_url,
            private_key=REQUESTER_PRIVATE_KEY,
            user_slug=REQUESTER_USER_SLUG,
            request_id="no-market-candidate-requester-msk",
        )

        status, body = _post_json_status(
            f"{hub3_url}/api/hub/v1/work/requests",
            {
                "request_id": "req_no_market_candidate",
                "multisession_authorization": {
                    "kind": "multisession_key",
                    "multisession_key_id": requester_msk["key"]["id"],
                },
                "work": {
                    "ring": "ring-2",
                    "max_price": {"amount": "0.10", "unit": "credit"},
                    "capabilities": ["python"],
                    "input": {},
                },
            },
        )
    finally:
        hub3.shutdown()
        hub3.server_close()
        thread3.join(timeout=2)

    assert status == 409
    assert body["ok"] is False
    assert body["error"] == "worker_not_live"
    assert body["partition"] == "ring-2"


def test_missing_owner_socket_is_retired_and_fresh_connection_is_selected(monkeypatch) -> None:
    """A stale cheaper slot must not keep a reconnected wallet off the market."""
    from types import SimpleNamespace

    from main_computer.stable_hub_worker_sessions import StableHubWorkerSessionError

    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    hub, thread = _start_server("dev-hub3", msk_store, worker_store)
    try:
        base = f"http://127.0.0.1:{hub.server_port}"
        auth = _issue_msk(
            base, private_key=REQUESTER_PRIVATE_KEY,
            user_slug=REQUESTER_USER_SLUG,
            request_id="ghost-owner-recovery-requester-msk",
        )
        wallet = private_key_to_address(WORKER_PRIVATE_KEY)
        # Both records are on the owner Hub; only the newer socket exists.
        for worker_id, price in (("conn_000_ghost", "0.01"), ("conn_zzz_fresh", "0.02")):
            owner = hub.worker_session_directory.record_connected(
                worker_id=worker_id, connection_id=worker_id,
                multisession_key_id="msk_test_worker", wallet_address=wallet,
                account_id="acct_worker",
            )
            hub.worker_market_directory.record_worker_live(
                worker_id=worker_id, owner=owner,
                market_profile={
                    "rings": ["ring-2"], "capabilities": ["python"],
                    "price": {"amount": price, "unit": "credit"},
                    "max_concurrency": 1,
                },
                worker_msk_id="msk_test_worker", worker_wallet_address=wallet,
                worker_account_id="acct_worker",
            )
        hub.register_live_worker_session(
            SimpleNamespace(connection_id="conn_zzz_fresh", is_live=True)
        )
        selected_at_reservation: list[str] = []

        def _deny_reservation(*, worker_id: str, connection_id: str, lease_epoch: int) -> None:
            selected_at_reservation.append(worker_id)
            raise StableHubWorkerSessionError("worker_capacity_unavailable")

        monkeypatch.setattr(hub.worker_market_directory, "reserve_worker_capacity", _deny_reservation)
        status, result = _post_json_status(base + "/api/hub/v1/work/requests", {
            "request_id": "req_ghost_owner_recovery",
            "multisession_authorization": {
                "kind": "multisession_key", "multisession_key_id": auth["key"]["id"],
            },
            "work": {
                "ring": "ring-2", "capabilities": ["python"],
                "max_price": {"amount": "0.10", "unit": "credit"}, "input": {},
            },
        })
        assert status == 409
        assert result["error"] == "worker_capacity_unavailable"
        assert selected_at_reservation == ["conn_zzz_fresh"]
        assert hub.worker_session_directory.get_owner("conn_000_ghost")["status"] == "closed"
        assert hub.worker_market_directory.get_worker("conn_000_ghost")["status"] == "closed"
        assert hub.worker_session_directory.get_owner("conn_zzz_fresh")["status"] == "live"
        assert hub.worker_market_directory.get_worker("conn_zzz_fresh")["status"] == "live"
    finally:
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=2)


def test_closed_owner_in_live_market_is_skipped_before_reservation(monkeypatch) -> None:
    """The observed 409 race: owner closed, stale market row still live."""
    from types import SimpleNamespace
    from main_computer.stable_hub_worker_sessions import StableHubWorkerSessionError

    hub, thread = _start_server("dev-hub3", InMemoryStableMultiSessionKeyStore(), InMemoryStableWorkerSessionStore())
    try:
        base = f"http://127.0.0.1:{hub.server_port}"
        auth = _issue_msk(base, private_key=REQUESTER_PRIVATE_KEY,
                          user_slug=REQUESTER_USER_SLUG, request_id="requester-stale-owner-msk")
        wallet = private_key_to_address(WORKER_PRIVATE_KEY)
        for worker_id, price in (("conn_a_stale", "0.01"), ("conn_b_live", "0.02")):
            owner = hub.worker_session_directory.record_connected(
                worker_id=worker_id, connection_id=worker_id,
                multisession_key_id="msk_worker", wallet_address=wallet,
                account_id="acct_worker",
            )
            hub.worker_market_directory.record_worker_live(
                worker_id=worker_id, owner=owner,
                market_profile={"ring": "ring-2", "price": {"amount": price, "unit": "credit"}},
                worker_msk_id="msk_worker", worker_wallet_address=wallet,
                worker_account_id="acct_worker",
            )
        hub.worker_session_directory.record_closed(worker_id="conn_a_stale", connection_id="conn_a_stale")
        assert hub.worker_market_directory.get_worker("conn_a_stale")["status"] == "live"
        hub.register_live_worker_session(SimpleNamespace(connection_id="conn_b_live", is_live=True))
        reserved = []

        def refuse_reservation(*, worker_id: str, connection_id: str, lease_epoch: int) -> None:
            reserved.append((worker_id, connection_id, lease_epoch))
            raise StableHubWorkerSessionError("worker_capacity_unavailable")

        monkeypatch.setattr(hub.worker_market_directory, "reserve_worker_capacity", refuse_reservation)
        status, response = _post_json_status(base + "/api/hub/v1/work/requests", {
            "request_id": "req_skip_stale_owner",
            "multisession_authorization": {
                "kind": "multisession_key", "multisession_key_id": auth["key"]["id"],
            },
            "work": {"ring": "ring-2", "input": {}},
        })
        assert status == 409
        assert response["error"] == "worker_capacity_unavailable"
        assert len(reserved) == 1 and reserved[0][0] == "conn_b_live"
        assert hub.worker_session_directory.get_owner("conn_a_stale")["status"] == "closed"
    finally:
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=2)


def test_owner_closes_after_selection_retries_replacement(monkeypatch) -> None:
    """Closing after a market read must reselect, not dispatch a stale offer."""
    from types import SimpleNamespace
    from main_computer.stable_hub_worker_sessions import StableHubWorkerSessionError

    hub, thread = _start_server("dev-hub3", InMemoryStableMultiSessionKeyStore(), InMemoryStableWorkerSessionStore())
    try:
        base = f"http://127.0.0.1:{hub.server_port}"
        auth = _issue_msk(base, private_key=REQUESTER_PRIVATE_KEY,
                          user_slug=REQUESTER_USER_SLUG, request_id="requester-selection-race-msk")
        wallet = private_key_to_address(WORKER_PRIVATE_KEY)
        for worker_id, price in (("conn_a_race", "0.01"), ("conn_b_replacement", "0.02")):
            owner = hub.worker_session_directory.record_connected(
                worker_id=worker_id, connection_id=worker_id,
                multisession_key_id="msk_worker", wallet_address=wallet, account_id="acct_worker",
            )
            hub.worker_market_directory.record_worker_live(
                worker_id=worker_id, owner=owner,
                market_profile={"ring": "ring-2", "price": {"amount": price, "unit": "credit"}},
                worker_msk_id="msk_worker", worker_wallet_address=wallet, worker_account_id="acct_worker",
            )
        hub.register_live_worker_session(SimpleNamespace(connection_id="conn_b_replacement", is_live=True))
        select = hub.worker_market_directory.select_worker_for_work
        observed = []

        def close_after_selection(work, *, excluded_connections=None):
            selected = select(work, excluded_connections=excluded_connections)
            if selected and selected["worker_id"] == "conn_a_race":
                hub.worker_session_directory.record_closed(worker_id="conn_a_race", connection_id="conn_a_race")
            return selected

        def refuse_reservation(*, worker_id: str, connection_id: str, lease_epoch: int) -> None:
            observed.append(worker_id)
            raise StableHubWorkerSessionError("worker_capacity_unavailable")

        monkeypatch.setattr(hub.worker_market_directory, "select_worker_for_work", close_after_selection)
        monkeypatch.setattr(hub.worker_market_directory, "reserve_worker_capacity", refuse_reservation)
        status, response = _post_json_status(base + "/api/hub/v1/work/requests", {
            "request_id": "req_selection_close_race",
            "multisession_authorization": {
                "kind": "multisession_key", "multisession_key_id": auth["key"]["id"],
            },
            "work": {"ring": "ring-2", "input": {}},
        })
        assert status == 409
        assert response["error"] == "worker_capacity_unavailable"
        assert observed == ["conn_b_replacement"]
    finally:
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=2)


def test_reselection_accepts_once_on_live_socket_after_closed_owner() -> None:
    """HTTP request routes past a stale cheap owner and reaches the real WebSocket."""
    hub, thread = _start_server("dev-hub3", InMemoryStableMultiSessionKeyStore(), InMemoryStableWorkerSessionStore())
    sock = None
    submit = None
    result_queue = queue.Queue()
    try:
        base = f"http://127.0.0.1:{hub.server_port}"
        worker_key = _issue_msk(base, private_key=WORKER_PRIVATE_KEY,
                                user_slug=WORKER_USER_SLUG, request_id="reselect-worker-msk")
        requester_key = _issue_msk(base, private_key=REQUESTER_PRIVATE_KEY,
                                   user_slug=REQUESTER_USER_SLUG, request_id="reselect-requester-msk")
        sock = _ws_connect("127.0.0.1", hub.server_port, "/api/hub/v1/workers/live-session")
        _ws_send_json(sock, {
            "type": "worker.auth", "execution_protocol": "durable-authorization-v1",
            "multisession_authorization": {
                "kind": "multisession_key", "multisession_key_id": worker_key["key"]["id"],
            },
            "market": {"ring": "ring-2", "price": {"amount": "0.02", "unit": "credit"},
                       "capabilities": ["python"], "max_concurrency": 1},
        })
        worker_auth = _ws_recv_json(sock)
        ping = _ws_recv_json(sock)
        assert worker_auth["type"] == "hub.auth.accepted"
        assert ping["type"] == "hub.ping"
        _ws_send_json(sock, {"type": "worker.pong", "ping_id": ping["ping_id"],
                             "connection_id": worker_auth["connection_id"]})
        assert _ws_recv_json(sock)["type"] == "hub.pong.accepted"
        ghost_id = "conn_a_dead_cheaper"
        owner = hub.worker_session_directory.record_connected(
            worker_id=ghost_id, connection_id=ghost_id,
            multisession_key_id="msk_worker", wallet_address=private_key_to_address(WORKER_PRIVATE_KEY),
            account_id="acct_worker",
        )
        hub.worker_market_directory.record_worker_live(
            worker_id=ghost_id, owner=owner,
            market_profile={"ring": "ring-2", "price": {"amount": "0.01", "unit": "credit"},
                            "capabilities": ["python"]},
            worker_msk_id="msk_worker", worker_wallet_address=private_key_to_address(WORKER_PRIVATE_KEY),
            worker_account_id="acct_worker",
        )
        hub.worker_session_directory.record_closed(worker_id=ghost_id, connection_id=ghost_id)
        body = {
            "request_id": "req_reselection_single_accept",
            "multisession_authorization": {
                "kind": "multisession_key", "multisession_key_id": requester_key["key"]["id"],
            },
            "work": {"ring": "ring-2", "capabilities": ["python"],
                     "max_price": {"amount": "0.10", "unit": "credit"}, "input": {}},
        }

        def post():
            try:
                result_queue.put(_post_json_status(base + "/api/hub/v1/work/requests", body))
            except BaseException as exc:
                result_queue.put(exc)

        submit = threading.Thread(target=post, daemon=True)
        submit.start()
        offer = _ws_recv_json(sock)
        assert offer["type"] == "hub.work.offer"
        assert offer["worker_id"] == worker_auth["connection_id"]
        _ws_send_json(sock, {
            "type": "worker.work.accepted", "session_id": offer["session_id"],
            "request_id": offer["request_id"],
        })
        response = result_queue.get(timeout=5)
        assert not isinstance(response, BaseException), response
        status, result = response
        assert status == 200, result
        assert result["accepted"] is True
        assert result["worker_id"] == worker_auth["connection_id"]
        assert len(hub.worker_session_directory.store.load()["accepted_sessions"]) == 1
    finally:
        if sock is not None:
            try:
                _ws_send_json(sock, {"type": "worker.close"})
            except OSError:
                pass
            sock.close()
        if submit is not None:
            submit.join(timeout=2)
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=2)
