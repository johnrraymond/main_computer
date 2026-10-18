"""CLEF integration with the existing Stable Hub; mocks never spend credits."""
from __future__ import annotations

from dataclasses import replace
from types import ModuleType

import pytest

from main_computer.hub_cli_live_session import _clef_result, serve_cli_live_worker
from main_computer.models import ChatResponse
from main_computer.providers.nanojev import CLEF_EVALUATE, CLEF_PAIRWISE
from tools import nanojev_hub_request as requester


def _solution(capability):
    return {"schema": "main-computer.nanojev-solution.v1", "provider": "nanojev",
            "capability": capability, "model": {"family": "nanojev-clef"},
            "result": {"states": []}}


def _offer(capability=CLEF_EVALUATE):
    return {"work": {"model": "nanojev-clef", "capabilities": [capability],
                     "input": {"kind": capability, "payload": {"states": [{"id": "a"}]}}}}


@pytest.mark.parametrize("capability", [CLEF_EVALUATE, CLEF_PAIRWISE])
def test_existing_worker_executes_native_solutions_without_chat(capability):
    got = []
    def solve(*, capability, payload):
        got.append((capability, payload))
        return _solution(capability)
    result = _clef_result(solve, _offer(capability), model="nanojev-clef")
    assert got == [(capability, {"states": [{"id": "a"}]})]
    assert result["status"] == "success"
    assert result["solution"]["capability"] == capability
    assert result["response"]["content"] == ""
    assert result["transport"] == "websocket-live-session"


@pytest.mark.parametrize("change", [
    {"capabilities": ["chat.completions"]},
    {"capabilities": [CLEF_EVALUATE, CLEF_PAIRWISE]},
    {"input": {"kind": "chat.completions", "payload": {"states": []}}},
    {"input": {"kind": CLEF_EVALUATE, "payload": {}}},
    {"model": "another-model"},
])
def test_clef_rejects_incompatible_hub_offers_before_execution(change):
    offer = _offer()
    offer["work"].update(change)
    with pytest.raises(ValueError):
        _clef_result(lambda **kw: pytest.fail("CLEF must never be called"), offer, model="nanojev-clef")


def test_bad_native_solution_fails_closed():
    with pytest.raises(ValueError, match="invalid solution envelope"):
        _clef_result(lambda **kw: {"response": {"content": "fake"}}, _offer(), model="nanojev-clef")


def test_existing_live_session_advertises_clef_not_chat(monkeypatch):
    from main_computer import hub_cli_live_session as live
    import sys
    fake_viewport = ModuleType("main_computer.viewport_routes_energy")
    monkeypatch.setitem(sys.modules, "main_computer.viewport_routes_energy", fake_viewport)
    captured = {}
    monkeypatch.setattr(live, "identify_hub_protocol", lambda *a, **kw: {"service": "main_computer.stable_hub", "network": {"chain_id": "42424240"}})
    monkeypatch.setattr(live, "_officer_wallet", lambda *a, **kw: object())
    monkeypatch.setattr(live, "issue_cli_multisession_authorization", lambda *a, **kw: {"chain_id": "42424240"})

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        def start(self):
            raise RuntimeError("STOP_BEFORE_CONNECTION")
        def close(self, *, reason):
            pass

    fake_viewport._WorkerHubLiveSessionClient = FakeClient
    with pytest.raises(RuntimeError, match="STOP_BEFORE_CONNECTION"):
        serve_cli_live_worker(hub_url="https://example.org", worker_node_id="test-clef",
                              model="nanojev-clef", chat_fn=lambda _: pytest.fail("no chat"),
                              provider_name="nanojev", solve_fn=lambda **kw: _solution(kw["capability"]),
                              assigned_ring=3, credits_per_request="1", officer="o3", verbose=False)
    auth = captured["auth_message"]
    assert auth["market"]["capabilities"] == [CLEF_EVALUATE, CLEF_PAIRWISE]
    assert auth["capabilities"]["provider"] == "nanojev"
    assert "chat.completions" not in auth["market"]["capabilities"]
    assert captured["work_executor"](_offer())["solution"]["capability"] == CLEF_EVALUATE


def test_existing_hub_worker_pull_forwards_native_solver(monkeypatch):
    from main_computer import hub_cli_live_session as live
    from main_computer.hub import serve_hub_worker_pull
    from main_computer.config import MainComputerConfig
    captured = {}
    monkeypatch.setattr(live, "hub_uses_live_sessions", lambda *a, **kw: True)
    monkeypatch.setattr(live, "serve_cli_live_worker", lambda **kw: captured.update(kw))
    config = replace(MainComputerConfig.from_env(), provider="nanojev", model="nanojev-clef",
                     hub_url="https://example.org")
    solver = lambda **kw: _solution(kw["capability"])
    serve_hub_worker_pull(config, lambda _: pytest.fail("no chat"), solve_fn=solver, verbose=False)
    assert captured["provider_name"] == "nanojev"
    assert captured["solve_fn"] is solver
    assert captured["model"] == "nanojev-clef"


def test_clef_refuses_legacy_hub_transport(monkeypatch):
    from main_computer import hub_cli_live_session as live
    from main_computer.hub import serve_hub_worker_pull
    from main_computer.config import MainComputerConfig
    monkeypatch.setattr(live, "hub_uses_live_sessions", lambda *a, **kw: False)
    config = replace(MainComputerConfig.from_env(), provider="nanojev", model="nanojev-clef")
    with pytest.raises(RuntimeError, match="authenticated live-session"):
        serve_hub_worker_pull(config, lambda _: None, verbose=False)


def test_requester_uses_native_market_envelope_and_credit_cap():
    work = requester.build_work(capability=CLEF_EVALUATE, payload={"states": [{"id": "a"}]},
                                timeout_s=1200, max_price="1")
    assert work["capabilities"] == [CLEF_EVALUATE]
    assert work["input"]["kind"] == CLEF_EVALUATE
    assert work["max_price"] == {"amount": "1", "unit": "compute_credit"}
    with pytest.raises(ValueError):
        requester.build_work(capability=CLEF_EVALUATE, payload={"states": []}, timeout_s=1200, max_price="-1")


def test_requester_reads_typed_solution_from_existing_accepted_session(monkeypatch):
    calls = []
    accepted = {"session_id": "sess-test", "continuation_url":
                "https://example.org/api/hub/v1/work/sessions/sess-test/stream"}
    def fake_json(url, *, timeout_s):
        calls.append(url)
        return {"ok": True, "accepted_session": {"status": "succeeded", "session_id": "sess-test",
                "worker_result": {"result": {"solution": _solution(CLEF_EVALUATE)}}, "payout": {"charged_credits": "1"}}}
    monkeypatch.setattr(requester, "_hub_json", fake_json)
    result = requester.retrieve_solution(accepted, hub_url="https://example.org", timeout_s=10)
    assert result["solution"]["schema"] == "main-computer.nanojev-solution.v1"
    assert result["payout"]["charged_credits"] == "1"
    assert len(calls) == 1


def test_requester_preview_never_calls_hub(tmp_path, monkeypatch, capsys):
    import json
    path = tmp_path / "request.json"
    path.write_text(json.dumps({"states": [{"id": "test"}]}), encoding="utf-8-sig")
    monkeypatch.setattr(requester, "_hub_json", lambda *a, **kw: pytest.fail("dry-run must not call Hub"))
    assert requester.main(["--payload-file", str(path)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["dry_run"] is True
    assert printed["request"]["work"]["capabilities"] == [CLEF_EVALUATE]


def test_requester_submits_normal_signed_work_request(tmp_path, monkeypatch, capsys):
    import json
    from main_computer import hub_cli_live_session as live
    path = tmp_path / "request.json"
    path.write_text(json.dumps({"states": [{"id": "test"}]}), encoding="utf-8")
    captured = {}
    monkeypatch.setattr(live, "identify_hub_protocol", lambda *a, **kw: {"service": "main_computer.stable_hub"})
    monkeypatch.setattr(live, "cli_request_authorization", lambda *a, **kw: {"key_id": "signed-stub"})
    def fake_json(url, *, body=None, timeout_s=15):
        captured.update(url=url, body=body)
        return {"ok": True, "accepted": True, "session_id": "sess-mock"}
    monkeypatch.setattr(requester, "_hub_json", fake_json)
    assert requester.main(["--payload-file", str(path), "--submit", "--no-wait"]) == 0
    assert captured["url"].endswith("/api/hub/v1/work/requests")
    assert captured["body"]["multisession_authorization"]["key_id"] == "signed-stub"
    assert captured["body"]["work"]["input"]["kind"] == CLEF_EVALUATE
    assert captured["body"]["work"]["max_price"]["amount"] == "1"
    assert "sess-mock" in capsys.readouterr().out
