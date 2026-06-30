from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.exp_fdb_credit_ledger import ExperimentalFoundationDbConfig
from main_computer.exp_fdb_hub import (
    ExperimentalFoundationDbHubServerHandler,
    _exp_fdb_stable_hub_id,
    _exp_public_redacted,
    build_experimental_hub_identity,
    build_experimental_stable_topology,
    build_parser,
)
from main_computer.hub import HubHttpServer
from main_computer.stable_hub import LiveWorkerSession
from main_computer.stable_hub_topology import StableHubNode, StableHubTopology
from main_computer.stable_hub_worker_sessions import (
    InMemoryStableWorkerSessionStore,
    StableHubAcceptedWorkSessionDirectory,
    StableHubWorkerMarketDirectory,
    StableHubWorkerSessionDirectory,
)


def _recv_until_headers(sock: socket.socket) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def _ws_connect(host: str, port: int, path: str) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=5)
    sock.settimeout(5)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = _recv_until_headers(sock).decode("iso-8859-1", errors="replace")
    assert response.startswith("HTTP/1.1 101"), response
    return sock


def _ws_send_json(sock: socket.socket, payload: dict[str, object]) -> None:
    data = json.dumps(payload).encode("utf-8")
    first = 0x81
    if len(data) < 126:
        header = struct.pack("!BB", first, 0x80 | len(data))
    elif len(data) <= 0xFFFF:
        header = struct.pack("!BBH", first, 0x80 | 126, len(data))
    else:
        header = struct.pack("!BBQ", first, 0x80 | 127, len(data))
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
    sock.sendall(header + mask + masked)


def _ws_recv_json(sock: socket.socket) -> dict[str, object]:
    while True:
        header = sock.recv(2)
        assert len(header) == 2
        first, second = header[0], header[1]
        opcode = first & 0x0F
        length = second & 0x7F
        masked = bool(second & 0x80)
        if length == 126:
            length = struct.unpack("!H", sock.recv(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", sock.recv(8))[0]
        mask = sock.recv(4) if masked else b""
        payload = b""
        while len(payload) < length:
            payload += sock.recv(length - len(payload))
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        if opcode == 0x9:
            # Server ping; respond with pong and keep reading app messages.
            sock.sendall(bytes([0x8A, len(payload)]) + payload)
            continue
        if opcode == 0xA:
            continue
        assert opcode == 0x1
        decoded = json.loads(payload.decode("utf-8"))
        assert isinstance(decoded, dict)
        return decoded


def _get_json_status(url: str) -> tuple[int, dict[str, object]]:
    try:
        with urlopen(url, timeout=5) as response:  # noqa: S310 - local test server
            body = response.read().decode("utf-8")
            return int(response.status), json.loads(body)
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError:
            decoded = {"ok": False, "error": "not_found", "body": body}
        return int(exc.code), decoded


def _post_json_status(url: str, payload: dict[str, object], *, timeout: float = 5.0) -> tuple[int, dict[str, object]]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - local test server
            body = response.read().decode("utf-8")
            return int(response.status), json.loads(body)
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError:
            decoded = {"ok": False, "error": "http_error", "body": body}
        return int(exc.code), decoded


def _read_sse_events_until(url: str, stop_event_type: str, holder: dict[str, object]) -> None:
    request = Request(url, headers={"Accept": "text/event-stream"}, method="GET")
    events: list[dict[str, object]] = []
    holder["events"] = events
    with urlopen(request, timeout=10) as response:  # noqa: S310 - local test server
        current_event = ""
        data_lines: list[str] = []
        for raw_line in response:
            line = raw_line.decode("utf-8").rstrip("\n").rstrip("\r")
            if not line:
                if current_event:
                    payload = json.loads("\n".join(data_lines) or "{}")
                    payload["__event"] = current_event
                    events.append(payload)
                    if current_event == stop_event_type:
                        return
                current_event = ""
                data_lines = []
                continue
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())



_PRIVATE_WORKER_FIELDS = {
    "worker_id",
    "worker_node_id",
    "requested_worker_node_id",
    "selected_worker_node_id",
    "worker_instance_id",
    "selected_worker_instance_id",
    "connection_id",
    "worker_connection_id",
    "worker_wallet_address",
    "worker_account_id",
    "worker_msk_id",
    "account_id",
    "wallet_address",
    "requester_account_id",
    "requester_wallet_address",
    "multisession_key_id",
    "msk_id",
    "selected_worker",
    "accepted_session",
    "credit_wallet",
    "payout_wallet_address",
    "log_file",
    "raw_stream_events",
}


def _assert_no_worker_private_fields(payload: object) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert str(key) not in _PRIVATE_WORKER_FIELDS
            _assert_no_worker_private_fields(value)
    elif isinstance(payload, list):
        for value in payload:
            _assert_no_worker_private_fields(value)


def test_exp_public_redaction_removes_wallet_and_socket_fields() -> None:
    payload = {
        "account_id": "0xrequester",
        "wallet_address": "0xrequester",
        "requester_wallet_address": "0xrequester",
        "multisession_key_id": "msk_secret",
        "receipt": {
            "charge_id": "chg_public",
            "worker_wallet_address": "0xworker",
            "connection_id": "conn_secret",
            "worker_earning_id": "earn_public",
        },
        "request": {
            "request_payload": {
                "metadata": {
                    "wallet_address": "0xrequester",
                    "selected_offer": {
                        "unit": "compute_credit",
                        "worker_instance_id": "conn_secret",
                    },
                }
            }
        },
    }

    clean = _exp_public_redacted(payload)

    _assert_no_worker_private_fields(clean)
    assert clean["receipt"]["charge_id"] == "chg_public"  # type: ignore[index]
    assert clean["receipt"]["worker_earning_id"] == "earn_public"  # type: ignore[index]
    assert clean["request"]["request_payload"]["metadata"]["selected_offer"]["unit"] == "compute_credit"  # type: ignore[index]


class _TestExpLiveSessionHub(HubHttpServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        config: MainComputerConfig,
        *,
        stable_topology: StableHubTopology | None = None,
        stable_hub_id: str = "",
        stable_store: InMemoryStableWorkerSessionStore | None = None,
    ) -> None:
        super().__init__(server_address, config, verbose=False)
        self.RequestHandlerClass = ExperimentalFoundationDbHubServerHandler
        hub_url = f"http://127.0.0.1:{self.server_port}"
        hub_node = StableHubNode(
            hub_id=_exp_fdb_stable_hub_id(self.server_port),
            hub_url=hub_url,
            public_url=hub_url,
            roles=("entry", "worker-owner", "requester", "execution"),
        )
        self.stable_topology = stable_topology or StableHubTopology(
            kind="main_computer.stable_hub_topology.v1",
            cluster_id="pytest-exp-live-single-cluster",
            network={"network_key": "dev", "kind": "local-dev-chain", "chain_id": 42424242},
            storage={"backend": "memory"},
            entry_urls=(hub_url,),
            hubs=(hub_node,),
        )
        self.stable_hub_node = self.stable_topology.hub_by_id(stable_hub_id) if stable_hub_id else hub_node
        self.identity = build_experimental_hub_identity(self.stable_topology, self.stable_hub_node.hub_id)
        store = stable_store or InMemoryStableWorkerSessionStore()
        self.stable_worker_session_directory = StableHubWorkerSessionDirectory(
            topology=self.stable_topology,
            hub_id=self.stable_hub_node.hub_id,
            store=store,
        )
        self.stable_worker_market_directory = StableHubWorkerMarketDirectory(
            topology=self.stable_topology,
            hub_id=self.stable_hub_node.hub_id,
            store=store,
        )
        self.exp_accepted_work_session_directory = StableHubAcceptedWorkSessionDirectory(
            topology=self.stable_topology,
            hub_id=self.stable_hub_node.hub_id,
            store=store,
        )
        self.live_worker_sessions: dict[str, LiveWorkerSession] = {}
        self.live_worker_sessions_lock = threading.Lock()

    def register_live_worker_session(self, session: LiveWorkerSession) -> None:
        with self.live_worker_sessions_lock:
            self.live_worker_sessions[session.connection_id] = session

    def get_live_worker_session(self, connection_id: str) -> LiveWorkerSession | None:
        with self.live_worker_sessions_lock:
            return self.live_worker_sessions.get(connection_id)

    def remove_live_worker_session(self, connection_id: str) -> LiveWorkerSession | None:
        with self.live_worker_sessions_lock:
            return self.live_worker_sessions.pop(connection_id, None)


def test_exp_hub_live_session_keepalive_returns_itself_as_worker_hub(tmp_path: Path) -> None:
    config = MainComputerConfig(
        workspace=tmp_path,
        hub_root=tmp_path / "hub-runtime",
        hub_bridge_backend="mock",
        hub_allow_insecure_dev_network=True,
        hub_network="dev",
        hub_network_kind="local-dev-chain",
        chain_id=42424242,
    )
    hub = _TestExpLiveSessionHub(("127.0.0.1", 0), config)
    thread = threading.Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    sock: socket.socket | None = None
    try:
        sock = _ws_connect("127.0.0.1", hub.server_port, "/api/hub/v1/workers/live-session")
        _ws_send_json(
            sock,
            {
                "type": "worker.auth",
                "market": {
                    "rings": ["ring-3"],
                    "price": {"amount": "0.05", "unit": "compute_credit"},
                    "capabilities": ["text"],
                    "max_concurrency": 1,
                },
            },
        )
        accepted = _ws_recv_json(sock)
        ping = _ws_recv_json(sock)

        assert accepted["type"] == "hub.auth.accepted"
        assert accepted["service"] == "main_computer.exp_fdb_hub"
        assert "worker_id" not in accepted
        assert "connection_id" not in accepted
        assert accepted["worker_hub"]["hub_id"] == hub.stable_hub_node.hub_id
        assert accepted["worker_hub"]["hub_url"] == hub.stable_hub_node.hub_url
        assert accepted["worker_hub"]["local_owner"] is True
        assert accepted["worker_hub"]["handoff"] is False
        assert accepted["execution_hub"] == {
            "hub_id": hub.stable_hub_node.hub_id,
            "hub_url": hub.stable_hub_node.hub_url,
        }
        assert accepted["heartbeat"]["transport"] == "websocket"
        assert accepted["contract"]["hub_to_hub_handoff"] == "owner-hub-forwarding-v1"
        assert ping["type"] == "hub.ping"
        assert "connection_id" not in ping

        _ws_send_json(
            sock,
            {
                "type": "worker.pong",
                "ping_id": ping["ping_id"],
            },
        )
        pong = _ws_recv_json(sock)
        assert pong["type"] == "hub.pong.accepted"
        assert pong["ok"] is True
        assert pong["worker_session"]["connected"] is True
        assert "worker_id" not in pong
        assert "connection_id" not in pong
        assert "owner" not in pong

        identity = json.loads(
            urlopen(f"http://127.0.0.1:{hub.server_port}/api/hub/v1/hub-identity", timeout=5)
            .read()
            .decode("utf-8")
        )
        assert identity["hub_id"] == hub.stable_hub_node.hub_id
        assert identity["hub_url"] == hub.stable_hub_node.hub_url
        assert identity["entry_urls"] == [hub.stable_hub_node.hub_url]
        assert identity["peer_hubs"] == []

        owner_status, owner_body = _get_json_status(
            f"http://127.0.0.1:{hub.server_port}/api/hub/v1/workers/worker-exp-live-1/owner"
        )
        assert owner_status == 404
        assert owner_body["error"] == "not_found"
    finally:
        if sock is not None:
            try:
                _ws_send_json(sock, {"type": "worker.close"})
            except OSError:
                pass
            sock.close()
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=2)


def test_exp_hub_stable_topology_is_single_local_owner_for_default_startup(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--network-key",
            "dev",
            "--network-kind",
            "local-dev-chain",
            "--bridge-backend",
            "dev-chain",
            "--allow-missing-bridge-signer",
            "--namespace",
            "pytest-exp",
        ]
    )
    config = MainComputerConfig(
        workspace=tmp_path,
        hub_root=tmp_path / "hub-runtime",
        hub_bridge_backend="dev-chain",
        hub_allow_missing_bridge_signer=True,
        hub_allow_insecure_dev_network=True,
        hub_network="dev",
        hub_network_kind="local-dev-chain",
        chain_id=42424242,
    )
    fdb_config = ExperimentalFoundationDbConfig(
        cluster_file=tmp_path / "docker.cluster",
        namespace="pytest-exp",
        repo_root=tmp_path,
        activate_native_client=False,
    )

    topology = build_experimental_stable_topology(args, config=config, fdb_config=fdb_config, port=8870)

    assert topology.cluster_id == "pytest-exp-stable-cluster"
    assert topology.hub_ids() == ("exp-fdb-hub-8870",)
    assert topology.concrete_hub_urls() == ("http://127.0.0.1:8870",)
    assert tuple(topology.entry_urls) == ("http://127.0.0.1:8870",)
    assert len(topology.hubs) == 1


def test_exp_hub_stable_topology_uses_all_manual_ports_for_owner_handoff(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--network-kind",
            "local-dev-chain",
            "--bridge-backend",
            "dev-chain",
            "--allow-missing-bridge-signer",
            "--namespace",
            "pytest-exp-handoff",
            "-ports",
            "8870,8871,8872",
        ]
    )
    config = MainComputerConfig(
        workspace=tmp_path,
        hub_root=tmp_path / "hub-runtime",
        hub_bridge_backend="dev-chain",
        hub_allow_missing_bridge_signer=True,
        hub_allow_insecure_dev_network=True,
        hub_network="dev",
        hub_network_kind="local-dev-chain",
        chain_id=42424242,
    )
    fdb_config = ExperimentalFoundationDbConfig(
        cluster_file=tmp_path / "docker.cluster",
        namespace="pytest-exp-handoff",
        repo_root=tmp_path,
        activate_native_client=False,
    )

    topology = build_experimental_stable_topology(args, config=config, fdb_config=fdb_config, port=8871)
    identity = build_experimental_hub_identity(topology, "exp-fdb-hub-8871")

    assert topology.cluster_id == "pytest-exp-handoff-stable-cluster"
    assert topology.hub_ids() == ("exp-fdb-hub-8870", "exp-fdb-hub-8871", "exp-fdb-hub-8872")
    assert topology.concrete_hub_urls() == (
        "http://127.0.0.1:8870",
        "http://127.0.0.1:8871",
        "http://127.0.0.1:8872",
    )
    assert tuple(topology.entry_urls) == topology.concrete_hub_urls()
    assert identity["hub_id"] == "exp-fdb-hub-8871"
    assert [hub["hub_id"] for hub in identity["peer_hubs"]] == ["exp-fdb-hub-8870", "exp-fdb-hub-8872"]




def test_exp_hub_live_session_selection_repairs_missing_market_row_without_worker_page(tmp_path: Path) -> None:
    config = MainComputerConfig(
        workspace=tmp_path,
        hub_root=tmp_path / "hub-runtime",
        hub_bridge_backend="mock",
        hub_allow_insecure_dev_network=True,
        hub_network="dev",
        hub_network_kind="local-dev-chain",
        chain_id=42424242,
        hub_credits_per_request=1,
    )
    hub = _TestExpLiveSessionHub(("127.0.0.1", 0), config)
    requester_account = "requester-exp-live-repair-missing"
    hub.credit_ledger.issue(account_id=requester_account, credits=25, memo="fund repair missing market row")
    thread = threading.Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    worker_sock: socket.socket | None = None
    try:
        base_url = f"http://127.0.0.1:{hub.server_port}"
        worker_sock = _ws_connect("127.0.0.1", hub.server_port, "/api/hub/v1/workers/live-session")
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.auth",
                "market": {
                    "rings": ["ring-3"],
                    "price": {"amount": "1", "unit": "compute_credit"},
                    "capabilities": ["chat.completions"],
                    "max_concurrency": 1,
                },
            },
        )
        assert _ws_recv_json(worker_sock)["type"] == "hub.auth.accepted"
        ping = _ws_recv_json(worker_sock)
        _ws_send_json(worker_sock, {"type": "worker.pong", "ping_id": ping["ping_id"]})
        assert _ws_recv_json(worker_sock)["type"] == "hub.pong.accepted"

        data = hub.stable_worker_market_directory.store.load()
        assert data.get("market_workers", {}) == {}

        response_holder: dict[str, object] = {}

        def submit_work() -> None:
            status, payload = _post_json_status(
                base_url + "/api/hub/v1/work/requests",
                {
                    "request_id": "exp-live-repair-missing-req-1",
                    "client_node_id": requester_account,
                    "account_id": requester_account,
                    "max_credits": 3,
                    "ring": "ring-3",
                    "capabilities": ["chat.completions"],
                    "input": {"prompt": "hello without opening worker page"},
                    "messages": [{"role": "user", "content": "hello without opening worker page"}],
                    "model": "micro-agent-local",
                },
                timeout=10,
            )
            response_holder["status"] = status
            response_holder["payload"] = payload

        submit_thread = threading.Thread(target=submit_work, daemon=True)
        submit_thread.start()
        offer = _ws_recv_json(worker_sock)
        assert offer["type"] == "hub.work.offer"
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.accepted",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
            },
        )
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.result",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
                "result": {
                    "status": "success",
                    "response": {
                        "content": "no page route works",
                        "provider": "exp-live-worker",
                        "model": "micro-agent-local",
                        "metadata": {"from_live_session": True},
                    },
                },
            },
        )
        assert _ws_recv_json(worker_sock)["type"] == "hub.work.result.accepted"
        submit_thread.join(timeout=10)
        assert not submit_thread.is_alive()
        assert response_holder["status"] == 200
        payload = response_holder["payload"]
        assert payload["ok"] is True  # type: ignore[index]
        assert payload["accepted"] is True  # type: ignore[index]
    finally:
        if worker_sock is not None:
            worker_sock.close()
        hub.shutdown()
        hub.server_close()


def test_exp_hub_live_session_selection_repairs_stale_capacity_without_worker_page(tmp_path: Path) -> None:
    config = MainComputerConfig(
        workspace=tmp_path,
        hub_root=tmp_path / "hub-runtime",
        hub_bridge_backend="mock",
        hub_allow_insecure_dev_network=True,
        hub_network="dev",
        hub_network_kind="local-dev-chain",
        chain_id=42424242,
        hub_credits_per_request=1,
    )
    hub = _TestExpLiveSessionHub(("127.0.0.1", 0), config)
    requester_account = "requester-exp-live-repair-capacity"
    hub.credit_ledger.issue(account_id=requester_account, credits=25, memo="fund repair stale capacity")
    thread = threading.Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    worker_sock: socket.socket | None = None
    try:
        base_url = f"http://127.0.0.1:{hub.server_port}"
        worker_sock = _ws_connect("127.0.0.1", hub.server_port, "/api/hub/v1/workers/live-session")
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.auth",
                "market": {
                    "rings": ["ring-3"],
                    "price": {"amount": "1", "unit": "compute_credit"},
                    "capabilities": ["chat.completions"],
                    "max_concurrency": 1,
                },
            },
        )
        assert _ws_recv_json(worker_sock)["type"] == "hub.auth.accepted"
        ping = _ws_recv_json(worker_sock)
        _ws_send_json(worker_sock, {"type": "worker.pong", "ping_id": ping["ping_id"]})
        assert _ws_recv_json(worker_sock)["type"] == "hub.pong.accepted"

        data = hub.stable_worker_market_directory.store.load()
        data.setdefault("market_workers", {})["local-worker-001"] = {
            "worker_id": "local-worker-001",
            "status": "live",
            "connection_id": "conn_legacy_stale",
            "owner_hub_id": hub.stable_hub_node.hub_id,
            "rings": ["ring-3"],
            "capabilities": ["chat.completions"],
            "price": {"amount": "1", "unit": "compute_credit"},
            "max_concurrency": 1,
            "active_sessions": 1,
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        hub.stable_worker_market_directory.store.save(data)

        response_holder: dict[str, object] = {}

        def submit_work() -> None:
            status, payload = _post_json_status(
                base_url + "/api/hub/v1/work/requests",
                {
                    "request_id": "exp-live-repair-capacity-req-1",
                    "client_node_id": requester_account,
                    "account_id": requester_account,
                    "max_credits": 3,
                    "ring": "ring-3",
                    "capabilities": ["chat.completions"],
                    "input": {"prompt": "hello after stale capacity"},
                    "messages": [{"role": "user", "content": "hello after stale capacity"}],
                    "model": "micro-agent-local",
                },
                timeout=10,
            )
            response_holder["status"] = status
            response_holder["payload"] = payload

        submit_thread = threading.Thread(target=submit_work, daemon=True)
        submit_thread.start()
        offer = _ws_recv_json(worker_sock)
        assert offer["type"] == "hub.work.offer"
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.accepted",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
            },
        )
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.result",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
                "result": {
                    "status": "success",
                    "response": {
                        "content": "stale capacity route works",
                        "provider": "exp-live-worker",
                        "model": "micro-agent-local",
                        "metadata": {"from_live_session": True},
                    },
                },
            },
        )
        assert _ws_recv_json(worker_sock)["type"] == "hub.work.result.accepted"
        submit_thread.join(timeout=10)
        assert not submit_thread.is_alive()
        assert response_holder["status"] == 200
        payload = response_holder["payload"]
        assert payload["ok"] is True  # type: ignore[index]
        assert payload["accepted"] is True  # type: ignore[index]
    finally:
        if worker_sock is not None:
            worker_sock.close()
        hub.shutdown()
        hub.server_close()


def test_exp_hub_single_local_live_session_executes_work_and_requires_bounce(tmp_path: Path) -> None:
    config = MainComputerConfig(
        workspace=tmp_path,
        hub_root=tmp_path / "hub-runtime",
        hub_bridge_backend="mock",
        hub_allow_insecure_dev_network=True,
        hub_network="dev",
        hub_network_kind="local-dev-chain",
        chain_id=42424242,
        hub_credits_per_request=1,
    )
    hub = _TestExpLiveSessionHub(("127.0.0.1", 0), config)
    requester_account = "requester-exp-live"
    hub.credit_ledger.issue(
        account_id=requester_account,
        credits=25,
        memo="fund exp live-session requester",
    )
    thread = threading.Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    worker_sock: socket.socket | None = None
    try:
        base_url = f"http://127.0.0.1:{hub.server_port}"
        worker_sock = _ws_connect("127.0.0.1", hub.server_port, "/api/hub/v1/workers/live-session")
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.auth",
                "market": {
                    "rings": ["ring-3"],
                    "price": {"amount": "1", "unit": "compute_credit"},
                    "capabilities": ["text", "mock-ai"],
                    "max_concurrency": 1,
                },
            },
        )
        accepted_auth = _ws_recv_json(worker_sock)
        ping = _ws_recv_json(worker_sock)
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.pong",
                "ping_id": ping["ping_id"],
            },
        )
        assert _ws_recv_json(worker_sock)["type"] == "hub.pong.accepted"

        for legacy_path in (
            "/api/hub/v1/workers/register",
            "/api/hub/v1/workers/heartbeat",
            "/api/hub/v1/workers/poll",
            "/api/hub/v1/workers/results",
            "/api/hub/workers/register",
            "/api/hub/workers/heartbeat",
            "/api/hub/workers/poll",
            "/api/hub/workers/results",
        ):
            status, legacy = _post_json_status(base_url + legacy_path, {"node_id": "guess"}, timeout=5)
            assert status == 404
            assert legacy["error"] == "not_found"

        work_response_holder: dict[str, object] = {}

        def submit_work() -> None:
            status, payload = _post_json_status(
                base_url + "/api/hub/v1/work/requests",
                {
                    "request_id": "exp-live-req-1",
                    "client_node_id": "requester-exp-live",
                    "account_id": requester_account,
                    "max_credits": 3,
                    "ring": "ring-3",
                    "capabilities": ["text"],
                    "input": {"prompt": "hello over exp live session"},
                    "messages": [{"role": "user", "content": "hello over exp live session"}],
                    "model": "micro-agent-local",
                },
                timeout=10,
            )
            work_response_holder["status"] = status
            work_response_holder["payload"] = payload

        submit_thread = threading.Thread(target=submit_work, daemon=True)
        submit_thread.start()
        offer = _ws_recv_json(worker_sock)
        assert offer["type"] == "hub.work.offer"
        _assert_no_worker_private_fields(offer)
        assert offer["service"] == "main_computer.exp_fdb_hub"
        assert offer["execution_hub"]["hub_id"] == hub.stable_hub_node.hub_id
        assert offer["execution_hub"]["hub_url"] == hub.stable_hub_node.hub_url
        assert offer["execution_hub"]["local_owner"] is True
        assert offer["execution_hub"]["handoff"] is False
        assert offer["work"]["model"] == "micro-agent-local"
        assert "lease_id" not in offer
        assert offer["pricing"]["legacy_worker_pull_lease"] is False
        assert offer["selected_offer"]["legacy_worker_pull_lease"] is False
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.accepted",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
            },
        )
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.result",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
                "result": {
                    "status": "success",
                    "response": {
                        "content": "exp live-session result",
                        "provider": "exp-live-worker",
                        "model": "micro-agent-local",
                        "metadata": {"from_live_session": True},
                    },
                },
            },
        )
        terminal_ack = _ws_recv_json(worker_sock)
        assert terminal_ack["type"] == "hub.work.result.accepted"
        assert terminal_ack["ok"] is True
        assert terminal_ack["status"] == "succeeded"
        assert terminal_ack["payout"]["status"] == "charged"
        assert terminal_ack["payout"]["charge_id"]
        assert terminal_ack["payout"]["worker_earning_id"]

        submit_thread.join(timeout=10)
        assert not submit_thread.is_alive()
        assert work_response_holder["status"] == 200
        accepted_work = work_response_holder["payload"]
        assert accepted_work["ok"] is True
        assert accepted_work["accepted"] is True
        assert accepted_work["execution_hub"]["hub_id"] == hub.stable_hub_node.hub_id
        assert accepted_work["execution_hub"]["hub_url"] == hub.stable_hub_node.hub_url
        assert accepted_work["execution_hub"]["handoff"] is False
        assert accepted_work["bounce"]["required"] is True
        assert accepted_work["bounce"]["same_hub"] is True
        assert accepted_work["continuation"]["bounce_required"] is True
        assert accepted_work["continuation_url"].startswith(base_url + "/api/hub/v1/work/sessions/")

        stream_status, stream = _get_json_status(str(accepted_work["continuation_url"]))
        assert stream_status == 200
        assert stream["ok"] is True
        assert stream["session_id"] == offer["session_id"]
        assert stream["status"] == "succeeded"
        assert stream["execution_hub"]["hub_id"] == hub.stable_hub_node.hub_id
        assert stream["execution_hub"]["handoff"] is False
        assert stream["bounce"]["required"] is True
        assert stream["payout"]["charge_id"] == terminal_ack["payout"]["charge_id"]

        assert hub.stable_worker_market_directory.list_workers() == []

        second_response_holder: dict[str, object] = {}

        def submit_second_work() -> None:
            status, payload = _post_json_status(
                base_url + "/api/hub/v1/work/requests",
                {
                    "request_id": "exp-live-req-2",
                    "client_node_id": "requester-exp-live",
                    "account_id": requester_account,
                    "max_credits": 3,
                    "ring": "ring-3",
                    "capabilities": ["text"],
                    "input": {"prompt": "hello again over exp live session"},
                    "messages": [{"role": "user", "content": "hello again over exp live session"}],
                    "model": "micro-agent-local",
                },
                timeout=10,
            )
            second_response_holder["status"] = status
            second_response_holder["payload"] = payload

        second_submit_thread = threading.Thread(target=submit_second_work, daemon=True)
        second_submit_thread.start()
        second_offer = _ws_recv_json(worker_sock)
        assert second_offer["type"] == "hub.work.offer"
        assert second_offer["request_id"]
        assert second_offer["request_id"] != offer["request_id"]
        assert second_offer["work"]["model"] == "micro-agent-local"
        assert "lease_id" not in second_offer
        assert second_offer["pricing"]["legacy_worker_pull_lease"] is False

        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.accepted",
                "session_id": second_offer["session_id"],
                "run_id": second_offer["run_id"],
                "request_id": second_offer["request_id"],
            },
        )
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.result",
                "session_id": second_offer["session_id"],
                "run_id": second_offer["run_id"],
                "request_id": second_offer["request_id"],
                "result": {
                    "status": "success",
                    "response": {
                        "content": "second exp live-session result",
                        "provider": "exp-live-worker",
                        "model": "micro-agent-local",
                        "metadata": {"from_live_session": True, "repeat": True},
                    },
                },
            },
        )
        terminal_ack_2 = _ws_recv_json(worker_sock)
        assert terminal_ack_2["type"] == "hub.work.result.accepted"
        assert terminal_ack_2["ok"] is True
        assert terminal_ack_2["status"] == "succeeded"

        second_submit_thread.join(timeout=10)
        assert not second_submit_thread.is_alive()
        assert second_response_holder["status"] == 200
        accepted_second = second_response_holder["payload"]
        assert accepted_second["ok"] is True
        assert accepted_second["accepted"] is True
        second_stream_status, second_stream = _get_json_status(str(accepted_second["continuation_url"]))
        assert second_stream_status == 200
        assert second_stream["ok"] is True
        assert second_stream["session_id"] == second_offer["session_id"]
        assert second_stream["status"] == "succeeded"
        assert second_stream["payout"]["charge_id"] == terminal_ack_2["payout"]["charge_id"]

        assert hub.stable_worker_market_directory.list_workers() == []
    finally:
        if worker_sock is not None:
            try:
                _ws_send_json(worker_sock, {"type": "worker.close"})
            except OSError:
                pass
            worker_sock.close()
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=2)



def test_exp_hub_sse_stream_receives_worker_delta_before_terminal_result(tmp_path: Path) -> None:
    config = MainComputerConfig(
        workspace=tmp_path,
        hub_root=tmp_path / "hub-runtime",
        hub_bridge_backend="mock",
        hub_allow_insecure_dev_network=True,
        hub_network="dev",
        hub_network_kind="local-dev-chain",
        chain_id=42424242,
        hub_credits_per_request=1,
    )
    hub = _TestExpLiveSessionHub(("127.0.0.1", 0), config)
    requester_account = "requester-exp-live-stream"
    hub.credit_ledger.issue(
        account_id=requester_account,
        credits=25,
        memo="fund exp live-session requester streaming",
    )
    thread = threading.Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    worker_sock: socket.socket | None = None
    try:
        base_url = f"http://127.0.0.1:{hub.server_port}"
        worker_sock = _ws_connect("127.0.0.1", hub.server_port, "/api/hub/v1/workers/live-session")
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.auth",
                "market": {
                    "rings": ["ring-3"],
                    "price": {"amount": "1", "unit": "compute_credit"},
                    "capabilities": ["text", "mock-ai"],
                    "max_concurrency": 1,
                },
            },
        )
        assert _ws_recv_json(worker_sock)["type"] == "hub.auth.accepted"
        ping = _ws_recv_json(worker_sock)
        _ws_send_json(worker_sock, {"type": "worker.pong", "ping_id": ping["ping_id"]})
        assert _ws_recv_json(worker_sock)["type"] == "hub.pong.accepted"

        response_holder: dict[str, object] = {}

        def submit_work() -> None:
            status, payload = _post_json_status(
                base_url + "/api/hub/v1/work/requests",
                {
                    "request_id": "exp-live-stream-req-1",
                    "client_node_id": "requester-exp-live-stream",
                    "account_id": requester_account,
                    "max_credits": 3,
                    "ring": "ring-3",
                    "capabilities": ["text"],
                    "input": {"prompt": "stream hello"},
                    "messages": [{"role": "user", "content": "stream hello"}],
                    "model": "micro-agent-local",
                },
                timeout=10,
            )
            response_holder["status"] = status
            response_holder["payload"] = payload

        submit_thread = threading.Thread(target=submit_work, daemon=True)
        submit_thread.start()
        offer = _ws_recv_json(worker_sock)
        assert offer["type"] == "hub.work.offer"
        _assert_no_worker_private_fields(offer)
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.accepted",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
            },
        )

        submit_thread.join(timeout=10)
        assert not submit_thread.is_alive()
        assert response_holder["status"] == 200
        accepted_work = response_holder["payload"]
        stream_url = str(accepted_work["continuation_url"]) + "?format=sse&timeout=10"  # type: ignore[index]

        sse_holder: dict[str, object] = {}
        sse_thread = threading.Thread(
            target=_read_sse_events_until,
            args=(stream_url, "result", sse_holder),
            daemon=True,
        )
        sse_thread.start()
        time.sleep(0.2)

        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.delta",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
                "seq": 1,
                "delta": "Violet",
                "content_so_far": "Violet",
                "created_at": "worker-created-before-terminal",
            },
        )

        deadline = time.monotonic() + 5.0
        events: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            events = list(sse_holder.get("events") or [])  # type: ignore[arg-type]
            if any(event.get("__event") == "delta" for event in events):
                break
            time.sleep(0.05)
        delta_events = [event for event in events if event.get("__event") == "delta"]
        assert delta_events, events
        first_delta = delta_events[0]
        assert first_delta["type"] == "delta"
        assert first_delta["delta"] == "Violet"
        assert first_delta["content_so_far"] == "Violet"
        assert first_delta["worker_seq"] == 1
        assert first_delta["hub_received_at"]
        _assert_no_worker_private_fields(first_delta)

        delta_ack = _ws_recv_json(worker_sock)
        assert delta_ack["type"] == "hub.work.delta.accepted"
        assert delta_ack["seq"] >= 1

        # The requester observed a real-time delta before the worker sent the
        # terminal result frame.
        assert not any(event.get("__event") == "result" for event in events)

        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.result",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
                "result": {
                    "status": "success",
                    "response": {
                        "content": "Violet beacon",
                        "provider": "exp-live-worker",
                        "model": "micro-agent-local",
                        "metadata": {"from_live_session": True},
                    },
                },
            },
        )
        terminal_ack = _ws_recv_json(worker_sock)
        assert terminal_ack["type"] == "hub.work.result.accepted"
        sse_thread.join(timeout=10)
        assert not sse_thread.is_alive()
        events = list(sse_holder.get("events") or [])  # type: ignore[arg-type]
        result_events = [event for event in events if event.get("__event") == "result"]
        assert result_events
        assert result_events[-1]["content"] == "Violet beacon"
        assert [event.get("__event") for event in events].index("delta") < [event.get("__event") for event in events].index("result")
        for event in events:
            _assert_no_worker_private_fields(event)
    finally:
        if worker_sock is not None:
            try:
                _ws_send_json(worker_sock, {"type": "worker.close"})
            except OSError:
                pass
            worker_sock.close()
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=2)



def test_exp_hub_marks_direct_live_session_failed_when_worker_socket_closes_after_acceptance(tmp_path: Path) -> None:
    config = MainComputerConfig(
        workspace=tmp_path,
        hub_root=tmp_path / "hub-runtime",
        hub_bridge_backend="mock",
        hub_allow_insecure_dev_network=True,
        hub_network="dev",
        hub_network_kind="local-dev-chain",
        chain_id=42424242,
        hub_credits_per_request=1,
    )
    hub = _TestExpLiveSessionHub(("127.0.0.1", 0), config)
    requester_account = "requester-exp-live-close"
    hub.credit_ledger.issue(
        account_id=requester_account,
        credits=25,
        memo="fund exp live-session close requester",
    )
    thread = threading.Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    worker_sock: socket.socket | None = None
    try:
        base_url = f"http://127.0.0.1:{hub.server_port}"
        worker_sock = _ws_connect("127.0.0.1", hub.server_port, "/api/hub/v1/workers/live-session")
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.auth",
                "market": {
                    "rings": ["ring-3"],
                    "price": {"amount": "1", "unit": "compute_credit"},
                    "capabilities": ["text"],
                    "max_concurrency": 1,
                },
            },
        )
        accepted_auth = _ws_recv_json(worker_sock)
        ping = _ws_recv_json(worker_sock)
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.pong",
                "ping_id": ping["ping_id"],
            },
        )
        assert _ws_recv_json(worker_sock)["type"] == "hub.pong.accepted"

        work_response_holder: dict[str, object] = {}

        def submit_work() -> None:
            status, payload = _post_json_status(
                base_url + "/api/hub/v1/work/requests",
                {
                    "request_id": "exp-live-close-req-1",
                    "client_node_id": "requester-exp-live-close",
                    "account_id": requester_account,
                    "max_credits": 3,
                    "ring": "ring-3",
                    "capabilities": ["text"],
                    "input": {"prompt": "close before result"},
                    "messages": [{"role": "user", "content": "close before result"}],
                    "model": "micro-agent-local",
                },
                timeout=10,
            )
            work_response_holder["status"] = status
            work_response_holder["payload"] = payload

        submit_thread = threading.Thread(target=submit_work, daemon=True)
        submit_thread.start()
        offer = _ws_recv_json(worker_sock)
        assert offer["type"] == "hub.work.offer"
        _assert_no_worker_private_fields(offer)
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.accepted",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
            },
        )

        submit_thread.join(timeout=10)
        assert not submit_thread.is_alive()
        assert work_response_holder["status"] == 200
        accepted_work = work_response_holder["payload"]
        assert accepted_work["ok"] is True
        assert accepted_work["accepted"] is True

        worker_sock.close()
        worker_sock = None

        stream: dict[str, object] | None = None
        for _ in range(20):
            stream_status, stream_payload = _get_json_status(str(accepted_work["continuation_url"]))
            assert stream_status == 200
            stream = stream_payload
            if stream.get("status") == "failed":
                break
            threading.Event().wait(0.1)

        assert stream is not None
        assert stream["status"] == "failed"
        assert stream["request"]["state"] == "failed"
        assert stream["request"]["terminal_reason"] in {"worker_connection_closed", "worker_connection_lost"}
        assert "accepted_session" not in stream
        assert "worker_id" not in json.dumps(stream)
        assert "connection_id" not in json.dumps(stream)
        assert stream["payout"]["legacy_worker_pull_lease"] is False

        assert hub.stable_worker_market_directory.list_workers() == []
    finally:
        if worker_sock is not None:
            try:
                _ws_send_json(worker_sock, {"type": "worker.close"})
            except OSError:
                pass
            worker_sock.close()
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=2)


def test_exp_hub_dev_socket_reconnect_does_not_expose_or_reuse_worker_identity(tmp_path: Path) -> None:
    config = MainComputerConfig(
        workspace=tmp_path,
        hub_root=tmp_path / "hub-runtime",
        hub_bridge_backend="mock",
        hub_allow_insecure_dev_network=True,
        hub_network="dev",
        hub_network_kind="local-dev-chain",
        chain_id=42424242,
        hub_credits_per_request=1,
    )
    hub = _TestExpLiveSessionHub(("127.0.0.1", 0), config)
    requester_account = "requester-exp-live-reconnect"
    hub.credit_ledger.issue(
        account_id=requester_account,
        credits=25,
        memo="fund exp live-session reconnect requester",
    )
    thread = threading.Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    old_sock: socket.socket | None = None
    new_sock: socket.socket | None = None
    try:
        base_url = f"http://127.0.0.1:{hub.server_port}"
        old_sock = _ws_connect("127.0.0.1", hub.server_port, "/api/hub/v1/workers/live-session")
        _ws_send_json(
            old_sock,
            {
                "type": "worker.auth",
                "market": {
                    "rings": ["ring-3"],
                    "price": {"amount": "1", "unit": "compute_credit"},
                    "capabilities": ["text"],
                    "max_concurrency": 1,
                },
            },
        )
        old_auth = _ws_recv_json(old_sock)
        old_ping = _ws_recv_json(old_sock)
        _ws_send_json(old_sock, {"type": "worker.pong", "ping_id": old_ping["ping_id"]})
        assert _ws_recv_json(old_sock)["type"] == "hub.pong.accepted"

        work_response_holder: dict[str, object] = {}

        def submit_work() -> None:
            status, payload = _post_json_status(
                base_url + "/api/hub/v1/work/requests",
                {
                    "request_id": "exp-live-reconnect-req-1",
                    "client_node_id": "requester-exp-live-reconnect",
                    "account_id": requester_account,
                    "max_credits": 3,
                    "ring": "ring-3",
                    "capabilities": ["text"],
                    "input": {"prompt": "reconnect before result"},
                    "messages": [{"role": "user", "content": "reconnect before result"}],
                    "model": "micro-agent-local",
                },
                timeout=10,
            )
            work_response_holder["status"] = status
            work_response_holder["payload"] = payload

        submit_thread = threading.Thread(target=submit_work, daemon=True)
        submit_thread.start()
        old_offer = _ws_recv_json(old_sock)
        assert old_offer["type"] == "hub.work.offer"
        _assert_no_worker_private_fields(old_offer)
        _ws_send_json(
            old_sock,
            {
                "type": "worker.work.accepted",
                "session_id": old_offer["session_id"],
                "run_id": old_offer["run_id"],
                "request_id": old_offer["request_id"],
            },
        )
        submit_thread.join(timeout=10)
        assert not submit_thread.is_alive()
        assert work_response_holder["status"] == 200
        accepted_work = work_response_holder["payload"]

        new_sock = _ws_connect("127.0.0.1", hub.server_port, "/api/hub/v1/workers/live-session")
        _ws_send_json(
            new_sock,
            {
                "type": "worker.auth",
                "market": {
                    "rings": ["ring-3"],
                    "price": {"amount": "1", "unit": "compute_credit"},
                    "capabilities": ["text"],
                    "max_concurrency": 1,
                },
            },
        )
        new_auth = _ws_recv_json(new_sock)
        new_ping = _ws_recv_json(new_sock)
        assert "connection_id" not in new_auth
        assert "connection_id" not in old_auth
        _ws_send_json(new_sock, {"type": "worker.pong", "ping_id": new_ping["ping_id"]})
        assert _ws_recv_json(new_sock)["type"] == "hub.pong.accepted"

        stream_status, stream = _get_json_status(str(accepted_work["continuation_url"]))
        assert stream_status == 200
        assert stream["status"] == "accepted"
        assert "accepted_session" not in stream
        assert "worker_id" not in json.dumps(stream)
        assert "connection_id" not in json.dumps(stream)

        # In insecure dev-mode tests without wallet authorization, the Hub falls
        # back to a private connection-scoped internal key.  A second socket is not
        # the same worker and must not reveal or reuse a fake worker id.  The first
        # accepted session fails only when its own socket closes.
        old_sock.close()
        old_sock = None

        closed_stream: dict[str, object] | None = None
        for _ in range(20):
            stream_status, stream_payload = _get_json_status(str(accepted_work["continuation_url"]))
            assert stream_status == 200
            closed_stream = stream_payload
            if closed_stream.get("status") == "failed":
                break
            threading.Event().wait(0.1)

        assert closed_stream is not None
        assert closed_stream["status"] == "failed"
        assert closed_stream["request"]["terminal_reason"] in {"worker_connection_closed", "worker_connection_lost"}

        assert hub.stable_worker_market_directory.list_workers() == []
    finally:
        for sock in (old_sock, new_sock):
            if sock is not None:
                try:
                    _ws_send_json(sock, {"type": "worker.close"})
                except OSError:
                    pass
                sock.close()
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=2)


def _configure_exp_test_hub_topology(
    hub: _TestExpLiveSessionHub,
    *,
    topology: StableHubTopology,
    hub_id: str,
    store: InMemoryStableWorkerSessionStore,
) -> None:
    hub.stable_topology = topology
    hub.stable_hub_node = topology.hub_by_id(hub_id)
    hub.identity = build_experimental_hub_identity(topology, hub_id)
    hub.stable_worker_session_directory = StableHubWorkerSessionDirectory(
        topology=topology,
        hub_id=hub_id,
        store=store,
    )
    hub.stable_worker_market_directory = StableHubWorkerMarketDirectory(
        topology=topology,
        hub_id=hub_id,
        store=store,
    )
    hub.exp_accepted_work_session_directory = StableHubAcceptedWorkSessionDirectory(
        topology=topology,
        hub_id=hub_id,
        store=store,
    )


def test_exp_hub_entry_hub_does_not_route_by_legacy_remote_worker_id_rows(tmp_path: Path) -> None:
    config = MainComputerConfig(
        workspace=tmp_path,
        hub_root=tmp_path / "hub-runtime",
        hub_bridge_backend="mock",
        hub_allow_insecure_dev_network=True,
        hub_network="dev",
        hub_network_kind="local-dev-chain",
        chain_id=42424242,
        hub_credits_per_request=1,
    )
    entry_hub = _TestExpLiveSessionHub(("127.0.0.1", 0), config)
    owner_hub = _TestExpLiveSessionHub(("127.0.0.1", 0), config)
    entry_url = f"http://127.0.0.1:{entry_hub.server_port}"
    owner_url = f"http://127.0.0.1:{owner_hub.server_port}"
    entry_id = _exp_fdb_stable_hub_id(entry_hub.server_port)
    owner_id = _exp_fdb_stable_hub_id(owner_hub.server_port)
    topology = StableHubTopology(
        kind="main_computer.stable_hub_topology.v1",
        cluster_id="pytest-exp-live-handoff-cluster",
        network={"network_key": "dev", "kind": "local-dev-chain", "chain_id": 42424242},
        storage={"backend": "memory"},
        entry_urls=(entry_url, owner_url),
        hubs=(
            StableHubNode(
                hub_id=entry_id,
                hub_url=entry_url,
                public_url=entry_url,
                roles=("entry", "requester"),
            ),
            StableHubNode(
                hub_id=owner_id,
                hub_url=owner_url,
                public_url=owner_url,
                roles=("entry", "worker-owner", "requester", "execution"),
            ),
        ),
    )
    shared_store = InMemoryStableWorkerSessionStore()
    _configure_exp_test_hub_topology(entry_hub, topology=topology, hub_id=entry_id, store=shared_store)
    _configure_exp_test_hub_topology(owner_hub, topology=topology, hub_id=owner_id, store=shared_store)

    requester_account = "requester-exp-no-legacy-handoff"
    owner_hub.credit_ledger.issue(
        account_id=requester_account,
        credits=25,
        memo="fund direct owner requester",
    )

    entry_thread = threading.Thread(target=entry_hub.serve_forever, daemon=True)
    owner_thread = threading.Thread(target=owner_hub.serve_forever, daemon=True)
    entry_thread.start()
    owner_thread.start()
    worker_sock: socket.socket | None = None
    try:
        worker_sock = _ws_connect("127.0.0.1", owner_hub.server_port, "/api/hub/v1/workers/live-session")
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.auth",
                "worker_id": "local-worker-001",
                "worker_node_id": "local-worker-001",
                "market": {
                    "rings": ["ring-3"],
                    "price": {"amount": "1", "unit": "compute_credit"},
                    "capabilities": ["text", "mock-ai"],
                    "max_concurrency": 1,
                },
            },
        )
        accepted_auth = _ws_recv_json(worker_sock)
        _assert_no_worker_private_fields(accepted_auth)
        ping = _ws_recv_json(worker_sock)
        _ws_send_json(worker_sock, {"type": "worker.pong", "ping_id": ping["ping_id"]})
        assert _ws_recv_json(worker_sock)["type"] == "hub.pong.accepted"

        data = shared_store.load()
        data.setdefault("market_workers", {})["local-worker-001"] = {
            "worker_id": "local-worker-001",
            "status": "live",
            "owner_hub_id": owner_id,
            "owner_hub_url": owner_url,
            "connection_id": "conn_legacy_remote",
            "rings": ["ring-3"],
            "capabilities": ["text"],
            "price": {"amount": "1", "unit": "compute_credit"},
            "max_concurrency": 1,
            "active_sessions": 0,
        }
        shared_store.save(data)

        entry_status, entry_payload = _post_json_status(
            entry_url + "/api/hub/v1/work/requests",
            {
                "request_id": "exp-remote-legacy-row-req-1",
                "client_node_id": "requester-exp-no-legacy-handoff",
                "account_id": requester_account,
                "max_credits": 3,
                "ring": "ring-3",
                "capabilities": ["text"],
                "input": {"prompt": "entry must not use legacy durable worker id rows"},
                "messages": [{"role": "user", "content": "entry must not use legacy durable worker id rows"}],
            },
            timeout=10,
        )
        assert entry_status == 409
        assert entry_payload["error"] == "no_live_worker_available"
        _assert_no_worker_private_fields(entry_payload)

        response_holder: dict[str, object] = {}

        def submit_work_to_owner() -> None:
            status, payload = _post_json_status(
                owner_url + "/api/hub/v1/work/requests",
                {
                    "request_id": "exp-owner-direct-no-legacy-req-1",
                    "client_node_id": "requester-exp-no-legacy-handoff",
                    "account_id": requester_account,
                    "max_credits": 3,
                    "ring": "ring-3",
                    "capabilities": ["text"],
                    "input": {"prompt": "owner direct live socket works"},
                    "messages": [{"role": "user", "content": "owner direct live socket works"}],
                },
                timeout=10,
            )
            response_holder["status"] = status
            response_holder["payload"] = payload

        submit_thread = threading.Thread(target=submit_work_to_owner, daemon=True)
        submit_thread.start()
        offer = _ws_recv_json(worker_sock)
        assert offer["type"] == "hub.work.offer"
        _assert_no_worker_private_fields(offer)
        assert "local-worker-001" not in json.dumps(offer)

        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.accepted",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
            },
        )
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.result",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
                "result": {
                    "status": "success",
                    "response": {
                        "content": "owner direct result",
                        "provider": "exp-live-worker",
                        "model": "micro-agent-local",
                    },
                },
            },
        )
        terminal_ack = _ws_recv_json(worker_sock)
        assert terminal_ack["type"] == "hub.work.result.accepted"
        _assert_no_worker_private_fields(terminal_ack)
        submit_thread.join(timeout=10)
        assert not submit_thread.is_alive()
        assert response_holder["status"] == 200
        accepted_work = response_holder["payload"]
        _assert_no_worker_private_fields(accepted_work)
        assert "local-worker-001" not in json.dumps(accepted_work)
    finally:
        if worker_sock is not None:
            try:
                _ws_send_json(worker_sock, {"type": "worker.close"})
            except OSError:
                pass
            worker_sock.close()
        entry_hub.shutdown()
        owner_hub.shutdown()
        entry_hub.server_close()
        owner_hub.server_close()
        entry_thread.join(timeout=2)
        owner_thread.join(timeout=2)
