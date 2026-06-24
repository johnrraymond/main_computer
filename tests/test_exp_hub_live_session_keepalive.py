from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.exp_fdb_credit_ledger import ExperimentalFoundationDbConfig
from main_computer.exp_fdb_hub import (
    ExperimentalFoundationDbHubServerHandler,
    _exp_fdb_stable_hub_id,
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
                "worker_id": "worker-exp-live-1",
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
        assert accepted["worker_id"] == "worker-exp-live-1"
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
        assert ping["connection_id"] == accepted["connection_id"]

        _ws_send_json(
            sock,
            {
                "type": "worker.pong",
                "connection_id": accepted["connection_id"],
                "ping_id": ping["ping_id"],
            },
        )
        pong = _ws_recv_json(sock)
        assert pong["type"] == "hub.pong.accepted"
        assert pong["ok"] is True
        assert pong["owner"]["owner_hub_id"] == hub.stable_hub_node.hub_id
        assert pong["owner"]["owner_hub_url"] == hub.stable_hub_node.hub_url
        assert pong["owner"]["last_pong_at"]

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
                "worker_id": "worker-exp-exec-1",
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
                "connection_id": accepted_auth["connection_id"],
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
                },
                timeout=10,
            )
            work_response_holder["status"] = status
            work_response_holder["payload"] = payload

        submit_thread = threading.Thread(target=submit_work, daemon=True)
        submit_thread.start()
        offer = _ws_recv_json(worker_sock)
        assert offer["type"] == "hub.work.offer"
        assert offer["service"] == "main_computer.exp_fdb_hub"
        assert offer["execution_hub"]["hub_id"] == hub.stable_hub_node.hub_id
        assert offer["execution_hub"]["hub_url"] == hub.stable_hub_node.hub_url
        assert offer["execution_hub"]["local_owner"] is True
        assert offer["execution_hub"]["handoff"] is False
        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.accepted",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
                "worker_id": "worker-exp-exec-1",
            },
        )
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

        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.result",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
                "worker_id": "worker-exp-exec-1",
                "lease_id": offer["lease_id"],
                "result": {
                    "status": "success",
                    "response": {
                        "content": "exp live-session result",
                        "provider": "exp-live-worker",
                        "model": "live-session-worker",
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

        stream_status, stream = _get_json_status(str(accepted_work["continuation_url"]))
        assert stream_status == 200
        assert stream["ok"] is True
        assert stream["session_id"] == offer["session_id"]
        assert stream["status"] == "succeeded"
        assert stream["execution_hub"]["hub_id"] == hub.stable_hub_node.hub_id
        assert stream["execution_hub"]["handoff"] is False
        assert stream["bounce"]["required"] is True
        assert stream["payout"]["charge_id"] == terminal_ack["payout"]["charge_id"]
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


def test_exp_hub_forwards_remote_live_session_work_to_owner_hub(tmp_path: Path) -> None:
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
        cluster_id="pytest-exp-handoff-cluster",
        network={"network_key": "dev", "kind": "local-dev-chain", "chain_id": 42424242},
        storage={"backend": "memory"},
        entry_urls=(entry_url, owner_url),
        hubs=(
            StableHubNode(
                hub_id=entry_id,
                hub_url=entry_url,
                public_url=entry_url,
                roles=("entry", "requester", "execution"),
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

    requester_account = "requester-exp-handoff"
    owner_hub.credit_ledger.issue(
        account_id=requester_account,
        credits=25,
        memo="fund exp handoff requester on execution hub",
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
                "worker_id": "worker-exp-remote-1",
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
                "connection_id": accepted_auth["connection_id"],
                "ping_id": ping["ping_id"],
            },
        )
        assert _ws_recv_json(worker_sock)["type"] == "hub.pong.accepted"

        entry_identity = json.loads(
            urlopen(f"{entry_url}/api/hub/v1/hub-identity", timeout=5).read().decode("utf-8")
        )
        assert entry_identity["contract"]["hub_to_hub_handoff"] == "owner-hub-forwarding-v1"
        assert entry_identity["peer_hubs"][0]["hub_id"] == owner_id

        work_response_holder: dict[str, object] = {}

        def submit_work_to_entry() -> None:
            status, payload = _post_json_status(
                entry_url + "/api/hub/v1/work/requests",
                {
                    "request_id": "exp-remote-req-1",
                    "client_node_id": "requester-exp-handoff",
                    "account_id": requester_account,
                    "max_credits": 3,
                    "ring": "ring-3",
                    "capabilities": ["text"],
                    "input": {"prompt": "hello through exp remote handoff"},
                    "messages": [{"role": "user", "content": "hello through exp remote handoff"}],
                },
                timeout=10,
            )
            work_response_holder["status"] = status
            work_response_holder["payload"] = payload

        submit_thread = threading.Thread(target=submit_work_to_entry, daemon=True)
        submit_thread.start()
        offer = _ws_recv_json(worker_sock)
        assert offer["type"] == "hub.work.offer"
        assert offer["service"] == "main_computer.exp_fdb_hub"
        assert offer["execution_hub"]["hub_id"] == owner_id
        assert offer["execution_hub"]["hub_url"] == owner_url
        assert offer["execution_hub"]["local_owner"] is True
        assert offer["execution_hub"]["handoff"] is False

        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.accepted",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
                "worker_id": "worker-exp-remote-1",
            },
        )
        submit_thread.join(timeout=10)
        assert not submit_thread.is_alive()
        assert work_response_holder["status"] == 200
        accepted_work = work_response_holder["payload"]
        assert accepted_work["ok"] is True
        assert accepted_work["accepted"] is True
        assert accepted_work["hub_id"] == entry_id
        assert accepted_work["entry_hub_id"] == entry_id
        assert accepted_work["accepted_by_hub_id"] == owner_id
        assert accepted_work["hub_to_hub_handoff"] is True
        assert accepted_work["execution_hub"] == {
            "hub_id": owner_id,
            "hub_url": owner_url,
            "local_owner": False,
            "handoff": True,
        }
        assert accepted_work["worker_hub"] == accepted_work["execution_hub"]
        assert accepted_work["bounce"]["required"] is True
        assert accepted_work["bounce"]["same_hub"] is False
        assert accepted_work["continuation"]["hub_id"] == owner_id
        assert accepted_work["continuation"]["hub_url"] == owner_url
        assert accepted_work["continuation_url"].startswith(owner_url + "/api/hub/v1/work/sessions/")

        _ws_send_json(
            worker_sock,
            {
                "type": "worker.work.result",
                "session_id": offer["session_id"],
                "run_id": offer["run_id"],
                "request_id": offer["request_id"],
                "worker_id": "worker-exp-remote-1",
                "lease_id": offer["lease_id"],
                "result": {
                    "status": "success",
                    "response": {
                        "content": "exp remote handoff result",
                        "provider": "exp-live-worker",
                        "model": "live-session-worker",
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

        stream_status, stream = _get_json_status(str(accepted_work["continuation_url"]))
        assert stream_status == 200
        assert stream["ok"] is True
        assert stream["session_id"] == offer["session_id"]
        assert stream["status"] == "succeeded"
        assert stream["execution_hub"]["hub_id"] == owner_id
        assert stream["execution_hub"]["hub_url"] == owner_url
        assert stream["execution_hub"]["local_owner"] is True
        assert stream["execution_hub"]["handoff"] is False
        assert stream["payout"]["charge_id"] == terminal_ack["payout"]["charge_id"]
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
