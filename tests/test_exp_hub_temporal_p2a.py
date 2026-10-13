"""P2-A: Experimental Hub shares Stable's FDB-authoritative Temporal contract.

Real local HTTP/WebSocket tests use the existing memory-backed Hub fixture.
Temporal's client is simulated; live Temporal/FDB/process-crash acceptance is
necessarily separate and must not be inferred from these tests.
"""
from __future__ import annotations

import asyncio
import copy
import json
import socket
import sys
import threading
from types import ModuleType, SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from main_computer.config import MainComputerConfig
from main_computer.stable_hub_temporal import StableHubTemporalRuntime, TemporalLifecycleUnavailable
from main_computer.stable_hub_worker_sessions import (
    stable_temporal_namespace_for_topology,
)
from test_exp_hub_live_session_keepalive import (
    _TestExpLiveSessionHub, _post_json_status, _ws_connect, _ws_recv_json, _ws_send_json,
)


def _config(tmp_path):
    return MainComputerConfig(
        workspace=tmp_path, hub_root=tmp_path / "hub-runtime",
        hub_bridge_backend="mock", hub_allow_insecure_dev_network=True,
        hub_network="dev", hub_network_kind="local-dev-chain",
        chain_id=42424242, hub_credits_per_request=1,
    )


def _health(url):
    try:
        with urlopen(url + "/health", timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


class _MockTemporal:
    namespace = "main-computer-dev"

    def __init__(self, *, ready):
        self.is_ready = ready

    def require_ready(self):
        if not self.is_ready:
            raise TemporalLifecycleUnavailable("Temporal unavailable; no new paid work admitted")


def _open_worker(hub):
    sock = _ws_connect("127.0.0.1", hub.server_port, "/api/hub/v1/workers/live-session")
    _ws_send_json(sock, {"type": "worker.auth", "market": {
        "rings": ["ring-3"], "price": {"amount": "1", "unit": "compute_credit"},
        "capabilities": ["chat.completions"], "max_concurrency": 1,
    }})
    assert _ws_recv_json(sock)["type"] == "hub.auth.accepted"
    ping = _ws_recv_json(sock)
    _ws_send_json(sock, {"type": "worker.pong", "ping_id": ping["ping_id"]})
    assert _ws_recv_json(sock)["type"] == "hub.pong.accepted"
    return sock


def _work(account):
    return {
        "request_id": "p2a-req", "client_node_id": account,
        "account_id": account, "max_credits": 3,
        "ring": "ring-3", "capabilities": ["chat.completions"],
        "input": {"prompt": "test Temporal"},
        "messages": [{"role": "user", "content": "test Temporal"}],
        "model": "micro-agent-local",
    }


def test_temporal_unavailable_rejects_before_claim_offer_and_credit_hold(tmp_path):
    hub = _TestExpLiveSessionHub(("127.0.0.1", 0), _config(tmp_path))
    hub.temporal_runtime = _MockTemporal(ready=False)
    account = "p2a-requester"
    hub.credit_ledger.issue(account_id=account, credits=8)
    thread = threading.Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    sock = None
    try:
        url = f"http://127.0.0.1:{hub.server_port}"
        status, health = _health(url)
        assert status == 503 and health["temporal"]["configured"] is True
        assert health["temporal"]["ready"] is False
        sock = _open_worker(hub)
        status, rejected = _post_json_status(url + "/api/hub/v1/work/requests", _work(account))
        assert status == 503 and rejected["error"] == "temporal_unavailable"
        assert hub.credit_ledger.get_account(account).held_credits == 0
        assert hub.credit_ledger.list_charges() == []
        assert hub.exp_accepted_work_session_directory.store.load()["accepted_sessions"] == {}
        sock.settimeout(0.3)
        with pytest.raises(socket.timeout):
            _ws_recv_json(sock)  # no offer was emitted
    finally:
        if sock is not None:
            sock.close()
        hub.shutdown(); hub.server_close(); thread.join(timeout=2)


def test_temporal_disabled_keeps_existing_live_hub_health(tmp_path):
    hub = _TestExpLiveSessionHub(("127.0.0.1", 0), _config(tmp_path))
    thread = threading.Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    try:
        status, health = _health(f"http://127.0.0.1:{hub.server_port}")
        assert status == 200 and health["ok"] is True
        assert health["temporal"]["configured"] is False
    finally:
        hub.shutdown(); hub.server_close(); thread.join(timeout=2)


def test_acceptance_intent_survives_temporal_reconciliation_restarts_without_paid_replay(tmp_path, monkeypatch):
    hub = _TestExpLiveSessionHub(("127.0.0.1", 0), _config(tmp_path))
    hub.temporal_runtime = _MockTemporal(ready=True)
    account = "p2a-requester"
    hub.credit_ledger.issue(account_id=account, credits=8)
    thread = threading.Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    sock = None
    try:
        url = f"http://127.0.0.1:{hub.server_port}"
        assert _health(url)[0] == 200
        sock = _open_worker(hub)
        response_holder = {}

        def submit():
            response_holder["status"], response_holder["body"] = _post_json_status(
                url + "/api/hub/v1/work/requests", _work(account), timeout=10,
            )
        submit_thread = threading.Thread(target=submit, daemon=True)
        submit_thread.start()
        offer = _ws_recv_json(sock)
        assert offer["type"] == "hub.work.offer"
        _ws_send_json(sock, {"type": "worker.work.accepted", "session_id": offer["session_id"],
                             "run_id": offer["run_id"], "request_id": offer["request_id"]})
        _ws_send_json(sock, {"type": "worker.work.result", "session_id": offer["session_id"],
                             "run_id": offer["run_id"], "request_id": offer["request_id"],
                             "result": {"status": "success", "response": {"content": "done"}}})
        assert _ws_recv_json(sock)["type"] == "hub.work.result.accepted"
        submit_thread.join(timeout=10)
        assert not submit_thread.is_alive() and response_holder["status"] == 200

        store = hub.exp_accepted_work_session_directory.store
        document = store.load()
        accepted = document["accepted_sessions"][offer["session_id"]]
        assert accepted["execution"]["temporal_mode"] == "enabled"
        assert accepted["execution"]["workflow_id"] == offer["session_id"]
        assert accepted["execution"]["namespace"] == stable_temporal_namespace_for_topology(hub.stable_topology)
        assert accepted["status"] == "succeeded"
        before = copy.deepcopy(document)
        account_before = hub.credit_ledger.get_account(account).as_dict()
        charges_before = hub.credit_ledger.list_charges()
        earnings_before = hub.credit_ledger.list_worker_earnings()
        assert len(charges_before) == 1 and len(earnings_before) == 1

        # A restarted supervisor sees the same FDB accepted-session outbox.
        # Its only actions are workflow start and terminal signal; no money or
        # worker offer appears in the Temporal code path.
        from main_computer import stable_hub_temporal as temporal
        common = ModuleType("temporalio.common")
        common.WorkflowIDReusePolicy = SimpleNamespace(REJECT_DUPLICATE="reject_duplicate")
        errors = ModuleType("temporalio.exceptions")
        class AlreadyStarted(Exception):
            pass
        errors.WorkflowAlreadyStartedError = AlreadyStarted
        monkeypatch.setitem(sys.modules, "temporalio.common", common)
        monkeypatch.setitem(sys.modules, "temporalio.exceptions", errors)
        monkeypatch.setattr(temporal, "WorkSessionWorkflow", SimpleNamespace(run="run", finished="finished"))
        seen = set()
        events = []
        class Handle:
            async def signal(self, method, terminal):
                events.append(("signal", method, terminal["status"]))
        class Client:
            async def start_workflow(self, method, *, args, id, **kwargs):
                if id in seen:
                    raise AlreadyStarted()
                seen.add(id)
                events.append(("start", id))
            def get_workflow_handle(self, workflow_id):
                assert workflow_id in seen
                return Handle()
        for _ in range(2):  # restart of the observer (same FDB state)
            runtime = object.__new__(StableHubTemporalRuntime)
            runtime.store = store
            runtime._ready = threading.Event()
            runtime._last_error = ""
            asyncio.run(runtime._reconcile_once(Client()))
            assert runtime.is_ready
        assert events == [("start", offer["session_id"]),
                          ("signal", "finished", "succeeded"),
                          ("signal", "finished", "succeeded")]
        assert store.load() == before
        assert hub.credit_ledger.get_account(account).as_dict() == account_before
        assert hub.credit_ledger.list_charges() == charges_before
        assert hub.credit_ledger.list_worker_earnings() == earnings_before
    finally:
        if sock is not None:
            sock.close()
        hub.shutdown(); hub.server_close(); thread.join(timeout=2)


@pytest.mark.parametrize("workflow_status", [
    "WorkflowExecutionStatus.COMPLETED",
    2,  # Python Temporal SDK 1.34.0 can stringify COMPLETED as "2".
    SimpleNamespace(name="COMPLETED"),
    SimpleNamespace(name="WORKFLOW_EXECUTION_STATUS_COMPLETED"),
])
def test_live_temporal_checkpoint_is_read_only_and_rejects_changed_fdb(monkeypatch, tmp_path, workflow_status):
    from tools.exp_hub_lab import experimental_temporal
    from main_computer import stable_hub_worker_sessions as sessions_module

    terminal = {"session_id": "sess-p2a", "request_id": "req-p2a",
                "owner_hub_id": "exp-fdb-hub-8970", "status": "succeeded",
                "execution": {"temporal_mode": "enabled", "workflow_id": "sess-p2a",
                              "namespace": "main-computer-exp-handoff-parity"},
                "payout": {"hold_id": "hold-1", "charge_id": "charge-1"}}
    values = {"accepted_sessions": {"sess-p2a": terminal}}
    class FakeStore:
        def __init__(self, **kwargs):
            assert kwargs["namespace"] == "isolation-stable-live-sessions"
        def load(self):
            return copy.deepcopy(values)
    monkeypatch.setattr(sessions_module, "FoundationDbStableWorkerSessionStore", FakeStore)
    temporalio = ModuleType("temporalio")
    temporal_client = ModuleType("temporalio.client")
    class FakeHandle:
        async def describe(self):
            return SimpleNamespace(status=workflow_status)
    class FakeClient:
        @classmethod
        async def connect(cls, address, namespace):
            assert address == "127.0.0.1:7233"
            assert namespace == "main-computer-exp-handoff-parity"
            return cls()
        def get_workflow_handle(self, session_id):
            assert session_id == "sess-p2a"
            return FakeHandle()
    temporal_client.Client = FakeClient
    monkeypatch.setitem(sys.modules, "temporalio", temporalio)
    monkeypatch.setitem(sys.modules, "temporalio.client", temporal_client)
    cluster = tmp_path / "docker.cluster"
    cluster.write_text("fake", encoding="utf-8")
    checkpoint = tmp_path / "checkpoint.json"
    kwargs = dict(session_id="sess-p2a", cluster_file=cluster, exp_namespace="isolation",
                  temporal_address="127.0.0.1:7233", checkpoint=checkpoint)
    first = experimental_temporal.verify(**kwargs)
    assert first["ok"] and first["phase"] == "baseline-created"
    assert first["temporal_status"] == "COMPLETED"
    second = experimental_temporal.verify(**kwargs)
    assert second["ok"] and second["phase"] == "post-restart-compared"
    values["accepted_sessions"]["sess-p2a"]["payout"]["charge_id"] = "charge-2"
    third = experimental_temporal.verify(**kwargs)
    assert third["ok"] is False and "checkpoint mismatch" in third["error"]
    assert json.loads(checkpoint.read_text())["fdb_record_sha256"] == first["snapshot"]["fdb_record_sha256"]


def test_p2a_cli_routes_to_exp_not_stable(monkeypatch, capsys):
    from tools.exp_hub_lab import run_lab, experimental_temporal
    calls = []
    def fake_verify(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "phase": "post-restart-compared", "checkpoint": "evidence.json"}
    monkeypatch.setattr(experimental_temporal, "verify", fake_verify)
    assert run_lab.main(["--verify-exp-temporal", "--temporal-session-id", "sess-p2a",
                         "--temporal-address", "127.0.0.1:7233", "--json"]) == 0
    assert calls[0]["session_id"] == "sess-p2a"
    assert calls[0]["exp_namespace"] == "main-computer-exp-handoff-parity"
    assert json.loads(capsys.readouterr().out)["phase"] == "post-restart-compared"


def test_shared_supervisor_reconciliation_failure_clears_readiness(monkeypatch):
    """A known failed Temporal RPC must not be reported as ready to the Hub."""
    from main_computer import stable_hub_temporal as temporal
    import copy
    old = {"accepted_sessions": {"s": {
        "session_id": "s", "request_id": "r", "owner_hub_id": "exp-1",
        "status": "accepted", "execution": {"temporal_mode": "enabled"},
    }}}
    runtime = object.__new__(StableHubTemporalRuntime)
    runtime.store = SimpleNamespace(load=lambda: copy.deepcopy(old))
    runtime._ready = threading.Event()
    runtime._ready.set()
    runtime._last_error = ""

    async def failed(client, session):
        raise ConnectionError("Temporal unreachable")
    monkeypatch.setattr(runtime, "_ensure_workflow", failed)
    asyncio.run(runtime._reconcile_once(object()))
    assert not runtime.is_ready
    assert runtime._last_error == "workflow_reconcile_ConnectionError"
    with pytest.raises(TemporalLifecycleUnavailable, match="no new paid work admitted"):
        runtime.require_ready()

    async def restored(client, session):
        return None
    monkeypatch.setattr(runtime, "_ensure_workflow", restored)
    asyncio.run(runtime._reconcile_once(object()))
    assert runtime.is_ready and runtime._last_error == ""


def test_completed_workflow_duplicate_signal_is_ignored_but_rpc_failure_is_not(monkeypatch):
    from main_computer import stable_hub_temporal as temporal
    common = ModuleType("temporalio.common")
    common.WorkflowIDReusePolicy = SimpleNamespace(REJECT_DUPLICATE="reject_duplicate")
    errors = ModuleType("temporalio.exceptions")
    class AlreadyStarted(Exception):
        pass
    errors.WorkflowAlreadyStartedError = AlreadyStarted
    monkeypatch.setitem(sys.modules, "temporalio.common", common)
    monkeypatch.setitem(sys.modules, "temporalio.exceptions", errors)
    monkeypatch.setattr(temporal, "WorkSessionWorkflow", SimpleNamespace(run="run", finished="finished"))
    record = {"session_id": "s", "request_id": "r", "owner_hub_id": "exp-1", "status": "succeeded"}
    class Handle:
        def __init__(self, status):
            self.status = status
        async def signal(self, *args):
            raise RuntimeError("Cannot signal")
        async def describe(self):
            if self.status == "unavailable":
                raise ConnectionError("Temporal RPC down")
            return SimpleNamespace(status=SimpleNamespace(name=self.status))
    class Client:
        def __init__(self, status):
            self.status = status
        async def start_workflow(self, *args, **kwargs):
            raise AlreadyStarted()
        def get_workflow_handle(self, session_id):
            return Handle(self.status)
    runtime = object.__new__(StableHubTemporalRuntime)
    asyncio.run(runtime._ensure_workflow(Client("COMPLETED"), record))
    with pytest.raises(ConnectionError):
        asyncio.run(runtime._ensure_workflow(Client("unavailable"), record))


@pytest.mark.parametrize(("status", "completed"), [
    (0, False), (1, False), (2, True), (3, False), (4, False),
    (5, False), (6, False), (7, False),
    ("2", False),  # Do not accept arbitrary untyped strings as enum values.
    ("COMPLETED", True), ("WorkflowExecutionStatus.COMPLETED", True),
    ("WORKFLOW_EXECUTION_STATUS_COMPLETED", True),
    (SimpleNamespace(name="COMPLETED"), True),
    (SimpleNamespace(name="FAILED"), False),
    (SimpleNamespace(name="COMPLETED", value=3), True),
    (True, False),
])
def test_temporal_workflow_status_classifier_is_fail_closed(status, completed):
    from main_computer.stable_hub_temporal import temporal_workflow_is_completed
    assert temporal_workflow_is_completed(status) is completed


def test_completed_numeric_temporal_status_allows_idempotent_terminal_reconciliation(monkeypatch):
    """An already-completed workflow must not disable admission on signal replay."""
    from main_computer import stable_hub_temporal as temporal
    common = ModuleType("temporalio.common")
    common.WorkflowIDReusePolicy = SimpleNamespace(REJECT_DUPLICATE="reject_duplicate")
    errors = ModuleType("temporalio.exceptions")
    class AlreadyStarted(Exception):
        pass
    errors.WorkflowAlreadyStartedError = AlreadyStarted
    monkeypatch.setitem(sys.modules, "temporalio.common", common)
    monkeypatch.setitem(sys.modules, "temporalio.exceptions", errors)
    monkeypatch.setattr(temporal, "WorkSessionWorkflow", SimpleNamespace(run="run", finished="finished"))
    session = {"session_id": "s", "request_id": "r", "owner_hub_id": "exp-1", "status": "succeeded"}
    class Handle:
        def __init__(self, status):
            self.status = status
        async def signal(self, *args):
            raise RuntimeError("already completed or failed RPC")
        async def describe(self):
            return SimpleNamespace(status=self.status)
    class Client:
        def __init__(self, status):
            self.status = status
        async def start_workflow(self, *args, **kwargs):
            raise AlreadyStarted()
        def get_workflow_handle(self, session_id):
            return Handle(self.status)
    asyncio.run(temporal.StableHubTemporalRuntime._ensure_workflow(object(), Client(2), session))
    with pytest.raises(RuntimeError, match="already completed"):
        asyncio.run(temporal.StableHubTemporalRuntime._ensure_workflow(object(), Client(3), session))
