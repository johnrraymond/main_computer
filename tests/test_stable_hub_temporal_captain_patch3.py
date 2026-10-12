"""Patch 3: Stable Hub Captain protocol and optional Temporal safety gates.

No real wallet deposits, worker payments, or live Temporal services are used.
"""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from main_computer import captain_cli as captain
from main_computer import hub_cli_live_session as live
from main_computer.config import MainComputerConfig
from main_computer.multisession_key_signing import private_key_to_address, verify_personal_sign_blob
from main_computer.stable_hub_temporal import StableHubTemporalRuntime, TemporalLifecycleUnavailable
from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore, StableHubAcceptedWorkSessionDirectory


KEY = "0x" + "19" * 32
WALLET = private_key_to_address(KEY)
STABLE_IDENTITY = {
    "service": "main_computer.stable_hub", "cluster_id": "mainnet-cluster",
    "network": {"chain_id": 42424240},
}


def test_stable_hub_authorization_uses_advertised_chain_and_signed_user_entropy(monkeypatch):
    captured = []

    def fake_post(_url, _path, payload, *, timeout_s):
        proof = verify_personal_sign_blob(
            payload["signed_request"], expected_chain_id="42424240", max_age_minutes=15,
        )
        message = proof["message"]
        assert len(message["user_slug"]) >= 32
        assert message["wallet_address"] == WALLET
        captured.append(message)
        return {
            "ok": True,
            "key": {"id": "msk-stable", "status": "active", "wallet_address": WALLET},
        }

    monkeypatch.setattr(live, "_hub_post", fake_post)
    authorization = live.issue_cli_multisession_authorization(
        "https://hub.example", wallet=SimpleNamespace(address=WALLET, private_key=KEY),
        stable_identity=STABLE_IDENTITY,
    )
    assert authorization["chain_id"] == "42424240"
    assert authorization["cluster_id"] == "mainnet-cluster"
    assert authorization["key_id"] == "msk-stable"
    assert len(captured) == 1


def test_stable_hub_authorization_fails_closed_without_signing_domain():
    with pytest.raises(RuntimeError, match="signing chain ID"):
        live.issue_cli_multisession_authorization(
            "https://hub.example", wallet=SimpleNamespace(address=WALLET, private_key=KEY),
            stable_identity={"service": "main_computer.stable_hub", "network": {}},
        )


def _response(*, url: str = "https://hub-c.example", session_id: str = "sess_abc") -> dict:
    return {
        "request_id": "req_abc", "session_id": session_id,
        "execution_hub": {"hub_id": "mainnetc-hub1", "hub_url": url},
        "continuation_url": f"{url}/api/hub/v1/work/sessions/{session_id}/stream",
    }


def test_captain_continuation_uses_owner_hub_and_recovers_terminal_result(monkeypatch):
    calls = []
    def get(base, path, *, timeout_s):
        calls.append((base, path))
        return {
            "ok": True, "session_id": "sess_abc", "status": "succeeded",
            "accepted_session": {
                "status": "succeeded", "worker_result": {
                    "result": {"response": {"content": "Hub C reports ready"}}
                },
            },
        }
    monkeypatch.setattr(captain, "_get_hub_json", get)
    result = captain._poll_stable_work_session(_response(), timeout_s=3, poll_seconds=1)
    assert captain._hub_request_state(result) == "completed"
    assert captain._extract_ai_response_text(result) == "Hub C reports ready"
    assert calls == [("https://hub-c.example", "/api/hub/v1/work/sessions/sess_abc/stream")]


@pytest.mark.parametrize("bad_url", [
    "https://attacker.example/api/hub/v1/work/sessions/sess_abc/stream",
    "https://hub-c.example/api/hub/v1/work/sessions/sess_wrong/stream",
    "http://hub-c.example/api/hub/v1/work/sessions/sess_abc/stream",
    "https://hub-c.example/api/hub/v1/work/sessions/sess_abc/stream?redirect=1",
])
def test_continuation_rejects_untrusted_location_before_network(monkeypatch, bad_url):
    monkeypatch.setattr(captain, "_get_hub_json", lambda *args, **kwargs: pytest.fail("unsafe network access"))
    accepted = _response()
    accepted["continuation_url"] = bad_url
    with pytest.raises(captain.CaptainCliError, match="continuation URL"):
        captain._poll_stable_work_session(accepted, timeout_s=2, poll_seconds=1)


def test_stable_request_payload_matches_hub_schema():
    legacy = {
        "messages": [{"role": "user", "content": "Report ready"}],
        "ring": "ring-3", "model": "gemma4:26b", "metadata": {"smoke": True},
        "multisession_authorization": {"key_id": "msk-a", "chain_id": "42424240"},
    }
    body = captain.build_stable_hub_request_payload(legacy, request_id="req-one", max_credits=4)
    assert body["request_id"] == "req-one"
    assert body["work"]["ring"] == "ring-3"
    assert body["work"]["max_price"] == {"amount": "4", "unit": "compute_credit"}
    assert body["work"]["messages"][0]["content"] == "Report ready"
    assert body["multisession_authorization"]["chain_id"] == "42424240"


def _run_mock_captain(monkeypatch, tmp_path, *, bridge):
    runtime = captain.CaptainRuntime(
        config=MainComputerConfig(workspace=tmp_path),
        deployment_path=tmp_path / "latest.json", deployment={},
        wallet=captain.CaptainWallet("captain", "O0", "Captain", WALLET, KEY),
        rpc_url="https://rpc.example", chain_id=42424240,
        xlag_address="0x" + "1" * 40, network="mainnet",
        bridge_escrow_address="0x" + "2" * 40, bridge_controller_address="0x" + "3" * 40,
    )
    monkeypatch.setattr(captain, "build_captain_runtime", lambda *a, **k: runtime)
    monkeypatch.setattr(live, "hub_uses_live_sessions", lambda _url: True)
    monkeypatch.setattr(live, "identify_hub_protocol", lambda _url, *, timeout_s: STABLE_IDENTITY)
    monkeypatch.setattr(live, "issue_cli_multisession_authorization", lambda _url, *, wallet, timeout_s, stable_identity: {
        "key_id": "msk-stable", "chain_id": "42424240", "wallet_address": WALLET,
    })
    requests = []
    def post(_url, path, payload, *, timeout_s):
        requests.append((path, payload))
        if path == "/api/hub/v1/credits/wallet-funding/complete":
            return {"ok": True, "deposit_id": "deposit-one"}
        if path == "/api/hub/v1/work/requests":
            return {"ok": True, **_response()}
        pytest.fail(f"unexpected endpoint: {path}")
    monkeypatch.setattr(captain, "_post_hub_json", post)
    monkeypatch.setattr(captain, "_poll_stable_work_session", lambda *_a, **_k: {
        "session_id": "sess_abc", "status": "succeeded",
        "accepted_session": {"worker_result": {"result": {"response": {"content": "ready"}}}},
    })
    monkeypatch.setattr(captain, "_fetch_hub_credit_balance", lambda *_a, **_k: {
        "ok": True, "account": {"available_credit_wei": "1000000000000000000"},
    })
    monkeypatch.setattr(captain, "send_captain_bridge_deposit", lambda *_a, **_k: {
        "deposit_id": "deposit-one", "transaction_hash": "0x" + "4" * 64,
    })
    monkeypatch.setattr(captain, "send_captain_bridge_refund", lambda *_a, **_k: pytest.fail("unsafe chain refund"))
    args = ["smoke", "captain", "Report", "ready", "--no-stipend", "--no-chain"]
    if not bridge:
        args += ["--no-bridge"]
    assert captain.run_captain(args, config=runtime.config, cwd=tmp_path) == 0
    body = [b for p, b in requests if p == "/api/hub/v1/work/requests"][0]
    assert body["request_id"]
    assert body["work"]["ring"] == "ring-3"
    assert body["multisession_authorization"]["key_id"] == "msk-stable"
    return requests


def test_captain_stable_work_request(monkeypatch, tmp_path):
    requests = _run_mock_captain(monkeypatch, tmp_path, bridge=False)
    assert [p for p, _ in requests] == ["/api/hub/v1/work/requests"]


def test_captain_stable_bridge_never_directly_refunds(monkeypatch, tmp_path):
    requests = _run_mock_captain(monkeypatch, tmp_path, bridge=True)
    assert [p for p, _ in requests] == [
        "/api/hub/v1/credits/wallet-funding/complete", "/api/hub/v1/work/requests",
    ]


def test_configured_temporal_fails_closed_without_worker():
    runtime = object.__new__(StableHubTemporalRuntime)
    runtime._ready = threading.Event()
    runtime._last_error = "temporalio_sdk_missing"
    with pytest.raises(TemporalLifecycleUnavailable, match="no new paid work admitted"):
        runtime.require_ready()


def test_temporal_intent_is_durable_and_opt_in():
    topology = load_stable_hub_topology("deploy/hub-topology/dev-topology.json")
    store = InMemoryStableWorkerSessionStore()
    directory = StableHubAcceptedWorkSessionDirectory(topology=topology, hub_id="dev-hub3", store=store)
    def record(session, intent):
        return directory.record_accepted(
            session_id=session, run_id="run_" + session, request_id="req_" + session,
            requester_msk_id="msk", requester_account_id="acct", requester_wallet_address=WALLET,
            worker_id="worker-a", worker_connection_id="conn-a", owner_hub_id="dev-hub3",
            owner_hub_url="http://localhost:8873", partition="ring-3", task_queue="main-computer-work-ring-3",
            work={"messages": [{"role": "user", "content": "hello"}]}, worker_acceptance={"ok": True},
            temporal_intent=intent,
        )
    enabled = record("sess_temporal", True)
    ordinary = record("sess_ordinary", False)
    assert enabled["execution"]["temporal_mode"] == "enabled"
    assert "temporal_mode" not in ordinary["execution"]
    assert store.load()["accepted_sessions"]["sess_temporal"]["execution"]["temporal_mode"] == "enabled"


def test_temporal_reconciliation_is_idempotent_and_read_only(monkeypatch):
    import asyncio
    import copy
    import sys
    from types import ModuleType
    from main_computer import stable_hub_temporal as temporal

    fake_common = ModuleType("temporalio.common")
    fake_common.WorkflowIDReusePolicy = SimpleNamespace(REJECT_DUPLICATE="reject_duplicate")
    fake_exceptions = ModuleType("temporalio.exceptions")
    class AlreadyStarted(Exception):
        pass
    fake_exceptions.WorkflowAlreadyStartedError = AlreadyStarted
    monkeypatch.setitem(sys.modules, "temporalio.common", fake_common)
    monkeypatch.setitem(sys.modules, "temporalio.exceptions", fake_exceptions)
    monkeypatch.setattr(temporal, "WorkSessionWorkflow", SimpleNamespace(run="work.run", finished="work.finished"))

    document = {
        "payout_accounts": {"acct": {"available_credit_wei": "500"}},
        "accepted_sessions": {
            "sess-real": {
                "session_id": "sess-real", "request_id": "req-real", "owner_hub_id": "mainnetc-hub1",
                "status": "succeeded", "execution": {"temporal_mode": "enabled"},
            },
            "sess-old": {
                "session_id": "sess-old", "request_id": "req-old", "owner_hub_id": "mainnetc-hub1",
                "status": "accepted", "execution": {"backend": "temporal"},
            },
        },
    }
    original = copy.deepcopy(document)
    events = []
    class FakeHandle:
        async def signal(self, name, payload):
            events.append(("signal", name, dict(payload)))
    class FakeClient:
        async def start_workflow(self, *args, **kwargs):
            events.append(("start", args, kwargs))
            if sum(item[0] == "start" for item in events) > 1:
                raise AlreadyStarted()
        def get_workflow_handle(self, session_id):
            assert session_id == "sess-real"
            return FakeHandle()
    class FakeStore:
        def load(self):
            return copy.deepcopy(document)

    runtime = object.__new__(StableHubTemporalRuntime)
    runtime.store = FakeStore()
    runtime._ready = threading.Event()
    runtime._last_error = ""
    asyncio.run(runtime._reconcile_once(FakeClient()))
    asyncio.run(runtime._reconcile_once(FakeClient()))
    assert [item[0] for item in events] == ["start", "signal", "start", "signal"]
    assert events[0][2]["id_reuse_policy"] == "reject_duplicate"
    assert events[0][2]["id"] == "sess-real"
    assert events[1][2]["status"] == "succeeded"
    assert document == original
