from __future__ import annotations

import json
import queue
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
from tools.stable_hub_lab.run_lab import (
    _post_json_url,
    _post_json_url_status,
    _read_json_url,
    _read_json_url_status,
    _ws_connect,
    _ws_recv_json,
    _ws_send_json,
    build_stable_msk_smoke_result,
)


DEV_TOPOLOGY = Path("deploy/stable-hub-lab/dev-topology.json")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_full_dev_topology(path: Path) -> dict[str, str]:
    """Write the real three-Hub dev topology with ephemeral local ports."""

    document = json.loads(DEV_TOPOLOGY.read_text(encoding="utf-8"))
    hub_urls: dict[str, str] = {}
    for hub in document["hubs"]:
        url = f"http://127.0.0.1:{_free_port()}"
        hub["hub_url"] = hub["public_url"] = url
        hub_urls[str(hub["hub_id"])] = url
    document["entry_urls"] = [str(hub["hub_url"]) for hub in document["hubs"]]
    path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    return hub_urls


def _start_server(
    *,
    topology_path: Path,
    hub_id: str,
    msk_store: InMemoryStableMultiSessionKeyStore,
    worker_store: InMemoryStableWorkerSessionStore,
):
    topology = load_stable_hub_topology(topology_path)
    hub = topology.hub_by_id(hub_id)
    parsed_port = int(hub.hub_url.rsplit(":", 1)[1])
    server = create_stable_hub_server(
        topology=topology,
        hub_id=hub_id,
        bind_host="127.0.0.1",
        bind_port=parsed_port,
        multisession_key_store=msk_store,
        worker_session_store=worker_store,
        work_offer_timeout_seconds=5.0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _wait_for_continuation(url: str, *, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    current_url = url
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, last = _read_json_url_status(current_url, timeout=min(1.0, timeout))
        if status == 409 and last.get("error") == "session_continuation_not_on_this_hub":
            next_url = str(last.get("continuation_url") or "")
            if next_url and next_url != current_url:
                current_url = next_url
                continue
        if status == 200 and str(last.get("status") or "") in {"succeeded", "failed", "cancelled"}:
            return last
        time.sleep(0.02)
    raise AssertionError(f"continuation did not become terminal: {last}")


class _DevTopologyWorker:
    def __init__(
        self,
        *,
        hub_id: str,
        hub_url: str,
        worker_id: str,
        worker_key_id: str,
        max_concurrency: int,
        timeout: float,
        terminal_delay: float = 0.05,
    ) -> None:
        self.hub_id = hub_id
        self.hub_url = hub_url
        self.worker_id = worker_id
        self.worker_key_id = worker_key_id
        self.max_concurrency = max_concurrency
        self.timeout = timeout
        self.terminal_delay = terminal_delay
        self.connection_id = ""
        self.lease_epoch = 0
        self.sock: socket.socket | None = None
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.offers: list[dict[str, Any]] = []
        self.terminal_acks: list[dict[str, Any]] = []
        self.errors: "queue.Queue[BaseException]" = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        sock = _ws_connect(self.hub_url, "/api/hub/v1/workers/live-session", timeout=self.timeout)
        sock.settimeout(self.timeout)
        _ws_send_json(
            sock,
            {
                "type": "worker.auth",
                "worker_id": self.worker_id,
                "multisession_authorization": {
                    "kind": "multisession_key",
                    "multisession_key_id": self.worker_key_id,
                },
                "market": {
                    "rings": ["ring-dev-topology-stress"],
                    "price": {"amount": "0.03", "unit": "credit"},
                    "capabilities": ["python", "echo", "stress"],
                    "max_concurrency": self.max_concurrency,
                },
            },
        )
        auth = _ws_recv_json(sock)
        ping = _ws_recv_json(sock)
        assert auth.get("type") == "hub.auth.accepted"
        assert ping.get("type") == "hub.ping"
        self.connection_id = str(auth.get("connection_id") or "")
        self.lease_epoch = int(auth.get("lease_epoch") or 0)
        _ws_send_json(
            sock,
            {
                "type": "worker.pong",
                "connection_id": self.connection_id,
                "ping_id": ping.get("ping_id"),
            },
        )
        pong = _ws_recv_json(sock)
        assert pong.get("type") == "hub.pong.accepted"
        assert pong.get("ok") is True
        sock.settimeout(0.2)
        self.sock = sock
        self.thread.start()
        self.ready.set()

    def close(self) -> None:
        self.stop.set()
        sock = self.sock
        if sock is not None:
            try:
                _ws_send_json(sock, {"type": "worker.close"})
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self.thread.join(timeout=2.0)

    def _send_pong(self, message: dict[str, Any]) -> None:
        if self.sock is None:
            return
        _ws_send_json(
            self.sock,
            {
                "type": "worker.pong",
                "connection_id": self.connection_id,
                "ping_id": message.get("ping_id"),
            },
        )

    def _recv_terminal_ack(self) -> dict[str, Any]:
        assert self.sock is not None
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            message = _ws_recv_json(self.sock)
            if message.get("type") == "hub.ping":
                self._send_pong(message)
                continue
            if message.get("type") in {
                "hub.work.result.accepted",
                "hub.work.failed.accepted",
                "hub.work.terminal.accepted",
            }:
                return message
        raise AssertionError(f"{self.worker_id} did not receive a terminal ack")

    def _run(self) -> None:
        assert self.sock is not None
        try:
            while not self.stop.is_set():
                try:
                    message = _ws_recv_json(self.sock)
                except TimeoutError:
                    continue
                except OSError:
                    if self.stop.is_set():
                        return
                    raise
                if message.get("type") == "hub.ping":
                    self._send_pong(message)
                    continue
                if message.get("type") in {
                    "hub.work.result.accepted",
                    "hub.work.failed.accepted",
                    "hub.work.terminal.accepted",
                }:
                    self.terminal_acks.append(message)
                    continue
                if message.get("type") != "hub.work.offer":
                    continue
                self.offers.append(message)
                _ws_send_json(
                    self.sock,
                    {
                        "type": "worker.work.accepted",
                        "session_id": message.get("session_id"),
                        "request_id": message.get("request_id"),
                    },
                )
                time.sleep(self.terminal_delay)
                work = message.get("work") if isinstance(message.get("work"), dict) else {}
                work_input = work.get("input") if isinstance(work.get("input"), dict) else {}
                value = str(work_input.get("value") or "")
                if value.startswith("fail:"):
                    _ws_send_json(
                        self.sock,
                        {
                            "type": "worker.work.failed",
                            "session_id": message.get("session_id"),
                            "run_id": message.get("run_id"),
                            "request_id": message.get("request_id"),
                            "worker_id": message.get("worker_id"),
                            "error": {
                                "code": "injected_dev_topology_failure",
                                "message": value,
                            },
                        },
                    )
                else:
                    _ws_send_json(
                        self.sock,
                        {
                            "type": "worker.work.result",
                            "session_id": message.get("session_id"),
                            "run_id": message.get("run_id"),
                            "request_id": message.get("request_id"),
                            "worker_id": message.get("worker_id"),
                            "result": {
                                "echo": value,
                                "worker_id": self.worker_id,
                                "owner_hub_id": self.hub_id,
                            },
                        },
                    )
                # Continuation polling proves terminal durability; keep reading the socket for more offers.
        except BaseException as exc:  # pragma: no cover - surfaced by the test
            self.errors.put(exc)


def test_stable_hub_full_dev_topology_concurrent_handoff_payout_settlement_stress(tmp_path: Path) -> None:
    """Stress the real three-Hub dev topology, not only the single-Hub golden path.

    The proof target is a realistic Stable flow: live workers on different owner
    Hubs, concurrent requester submissions through different entry Hubs, remote
    handoff to worker owners, terminal continuation state, hold charge/release,
    worker earnings, claim idempotency, settlement idempotency/conflict handling,
    and bridge confirmation idempotency/conflict handling.
    """

    topology_path = tmp_path / "dev-topology.json"
    hub_urls = _write_full_dev_topology(topology_path)
    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    servers = [
        _start_server(
            topology_path=topology_path,
            hub_id=hub_id,
            msk_store=msk_store,
            worker_store=worker_store,
        )
        for hub_id in ("dev-hub1", "dev-hub2", "dev-hub3")
    ]

    timeout = 8.0
    workers: list[_DevTopologyWorker] = []
    try:
        requester_msk = build_stable_msk_smoke_result(
            topology_path=topology_path,
            wallet_path=tmp_path / "requester-wallet.json",
            request_hub_id="dev-hub1",
            validate_hub_id="dev-hub3",
            origin="pytest-dev-topology-stress-requester",
            user_slug="requester-dev-topology-stress-user-000001",
            request_id="requester-dev-topology-stress-msk",
            timeout=timeout,
        )
        worker_msk = build_stable_msk_smoke_result(
            topology_path=topology_path,
            wallet_path=tmp_path / "worker-wallet.json",
            request_hub_id="dev-hub2",
            validate_hub_id="dev-hub1",
            origin="pytest-dev-topology-stress-worker",
            user_slug="worker-dev-topology-stress-user-000001",
            request_id="worker-dev-topology-stress-msk",
            timeout=timeout,
        )
        requester_key_id = str(requester_msk["multisession_key_id"])
        worker_key_id = str(worker_msk["multisession_key_id"])

        for index, hub_id in enumerate(("dev-hub1", "dev-hub2", "dev-hub3"), start=1):
            worker = _DevTopologyWorker(
                hub_id=hub_id,
                hub_url=hub_urls[hub_id],
                worker_id=f"worker-dev-topology-stress-{index}",
                worker_key_id=worker_key_id,
                max_concurrency=2,
                timeout=timeout,
                terminal_delay=0.25,
            )
            worker.start()
            workers.append(worker)

        request_count = 6
        entries = ["dev-hub1", "dev-hub2", "dev-hub3", "dev-hub1", "dev-hub2", "dev-hub3"]

        def _submit(index: int) -> dict[str, Any]:
            entry_hub_id = entries[index]
            terminal_value = f"fail:{index}" if index in {1, 4} else f"ok:{index}"
            request_id = f"req-dev-topology-stress-{index}"
            response = _post_json_url(
                f"{hub_urls[entry_hub_id].rstrip('/')}/api/hub/v1/work/requests",
                {
                    "request_id": request_id,
                    "multisession_authorization": {
                        "kind": "multisession_key",
                        "multisession_key_id": requester_key_id,
                    },
                    "work": {
                        "ring": "ring-dev-topology-stress",
                        "max_price": {"amount": "0.10", "unit": "credit"},
                        "capabilities": ["python", "echo", "stress"],
                        "input": {
                            "kind": "echo",
                            "value": terminal_value,
                        },
                    },
                },
                timeout=timeout,
            )
            response["entry_hub_id"] = entry_hub_id
            response["terminal_value"] = terminal_value
            return response

        with ThreadPoolExecutor(max_workers=request_count) as executor:
            futures = [executor.submit(_submit, index) for index in range(request_count)]
            responses = [future.result(timeout=timeout + 2.0) for future in as_completed(futures)]

        for worker in workers:
            if not worker.errors.empty():
                raise worker.errors.get()

        continuations = [
            _wait_for_continuation(str(response["continuation_url"]), timeout=timeout)
            for response in responses
        ]

        assert len(responses) == request_count
        assert all(response.get("ok") is True and response.get("accepted") is True for response in responses)
        assert {continuation.get("status") for continuation in continuations} == {"succeeded", "failed"}
        assert sum(1 for continuation in continuations if continuation.get("status") == "succeeded") == 4
        assert sum(1 for continuation in continuations if continuation.get("status") == "failed") == 2
        assert any((response.get("handoff") or {}).get("routed") is True for response in responses)
        assert len({response.get("session_id") for response in responses}) == request_count
        assert len({response.get("continuation_url") for response in responses}) == request_count

        all_offers = [offer for worker in workers for offer in worker.offers]
        assert len(all_offers) == request_count
        assert all(len(worker.offers) <= worker.max_concurrency for worker in workers)

        payout_status = _read_json_url(f"{hub_urls['dev-hub1'].rstrip('/')}/api/hub/v1/payout/status", timeout=timeout)
        payout = payout_status.get("payout") if isinstance(payout_status.get("payout"), dict) else {}
        session_ids = {str(response.get("session_id") or "") for response in responses}
        holds = [
            hold
            for hold in payout.get("holds", [])
            if isinstance(hold, dict) and str(hold.get("session_id") or "") in session_ids
        ]
        hold_ids = {str(hold.get("hold_id") or "") for hold in holds}
        charges = [
            charge
            for charge in payout.get("charges", [])
            if isinstance(charge, dict) and str(charge.get("hold_id") or "") in hold_ids
        ]
        charge_ids = {str(charge.get("charge_id") or "") for charge in charges}
        earnings = [
            earning
            for earning in payout.get("worker_earnings", [])
            if isinstance(earning, dict) and str(earning.get("charge_id") or "") in charge_ids
        ]

        assert len(holds) == request_count
        assert len(hold_ids) == request_count
        assert len(charges) == 4
        assert len(charge_ids) == 4
        assert len(earnings) == 4
        assert len({str(earning.get("earning_id") or "") for earning in earnings}) == 4
        assert sum(1 for hold in holds if hold.get("status") == "charged") == 4
        assert sum(1 for hold in holds if hold.get("status") == "released") == 2

        worker_auth = {
            "multisession_authorization": {
                "kind": "multisession_key",
                "multisession_key_id": worker_key_id,
            }
        }
        workers_with_earnings = sorted({str(earning.get("worker_id") or "") for earning in earnings})
        assert workers_with_earnings

        for worker_id in workers_with_earnings:
            claim = _post_json_url(
                f"{hub_urls['dev-hub2'].rstrip('/')}/api/hub/v1/workers/{worker_id}/payout/claim",
                {**worker_auth, "idempotency_key": f"claim-{worker_id}"},
                timeout=timeout,
            )
            duplicate_claim = _post_json_url(
                f"{hub_urls['dev-hub3'].rstrip('/')}/api/hub/v1/workers/{worker_id}/payout/claim",
                {**worker_auth, "idempotency_key": f"claim-{worker_id}"},
                timeout=timeout,
            )
            assert claim["claim"]["claim_id"] == duplicate_claim["claim"]["claim_id"]
            if claim["claim"].get("empty"):
                continue

            settlement = _post_json_url(
                f"{hub_urls['dev-hub1'].rstrip('/')}/api/hub/v1/workers/{worker_id}/payout/settlements",
                {
                    **worker_auth,
                    "claim_ids": [claim["claim"]["claim_id"]],
                    "idempotency_key": f"settle-{worker_id}",
                    "settle": True,
                    "settlement_reference": f"settlement-ref-{worker_id}",
                },
                timeout=timeout,
            )
            duplicate_settlement = _post_json_url(
                f"{hub_urls['dev-hub2'].rstrip('/')}/api/hub/v1/workers/{worker_id}/payout/settlements",
                {
                    **worker_auth,
                    "claim_ids": [claim["claim"]["claim_id"]],
                    "idempotency_key": f"settle-{worker_id}",
                    "settle": True,
                    "settlement_reference": f"settlement-ref-{worker_id}",
                },
                timeout=timeout,
            )
            assert settlement["settlement"]["batch_id"] == duplicate_settlement["settlement"]["batch_id"]
            conflict_status, conflict_body = _post_json_url_status(
                f"{hub_urls['dev-hub3'].rstrip('/')}/api/hub/v1/workers/{worker_id}/payout/settlements",
                {
                    **worker_auth,
                    "claim_ids": [claim["claim"]["claim_id"]],
                    "idempotency_key": f"settle-{worker_id}",
                    "settle": True,
                    "settlement_reference": f"settlement-ref-conflict-{worker_id}",
                },
                timeout=timeout,
            )
            assert conflict_status == 400
            assert conflict_body.get("error") in {
                "settlement_reference_conflict",
                "settlement_idempotency_key_conflict",
            }

            bridge = _post_json_url(
                f"{hub_urls['dev-hub3'].rstrip('/')}/api/hub/v1/workers/{worker_id}/payout/bridge",
                {
                    **worker_auth,
                    "batch_id": settlement["settlement"]["batch_id"],
                    "idempotency_key": f"bridge-{worker_id}",
                },
                timeout=timeout,
            )
            bridge_id = str(bridge["bridge_payout"]["bridge_payout_id"])
            confirmed = _post_json_url(
                f"{hub_urls['dev-hub1'].rstrip('/')}/api/hub/v1/payout/bridge/{bridge_id}/confirm",
                {"settlement_reference": f"bridge-ref-{worker_id}"},
                timeout=timeout,
            )
            duplicate_confirmed = _post_json_url(
                f"{hub_urls['dev-hub2'].rstrip('/')}/api/hub/v1/payout/bridge/{bridge_id}/confirm",
                {"settlement_reference": f"bridge-ref-{worker_id}"},
                timeout=timeout,
            )
            assert confirmed["bridge_payout"]["status"] == "confirmed"
            assert duplicate_confirmed["bridge_payout"]["status"] == "confirmed"
            bridge_conflict_status, bridge_conflict_body = _post_json_url_status(
                f"{hub_urls['dev-hub3'].rstrip('/')}/api/hub/v1/payout/bridge/{bridge_id}/confirm",
                {"settlement_reference": f"bridge-ref-conflict-{worker_id}"},
                timeout=timeout,
            )
            assert bridge_conflict_status == 400
            assert bridge_conflict_body.get("error") == "bridge_confirmation_reference_conflict"
    finally:
        for worker in workers:
            worker.close()
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)
