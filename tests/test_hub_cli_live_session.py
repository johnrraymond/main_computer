"""Captain/Data CLI live-session protocol regression (no remote credits spent)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from main_computer import hub_cli_live_session as live
from main_computer.models import ChatMessage, ChatResponse
from main_computer.multisession_key_signing import (
    private_key_to_address, verify_personal_sign_blob,
)

KEY = "0x" + "1".zfill(64)
WALLET = private_key_to_address(KEY)


def test_wallet_signs_expected_hub_key_domain(monkeypatch) -> None:
    captured = []

    def fake_post(_url, path, body, *, timeout_s):
        assert path.endswith("multisession-keys/request")
        verified = verify_personal_sign_blob(
            body["signed_request"], expected_chain_id=live.HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID,
            max_age_minutes=15,
        )
        assert verified["wallet_address"] == WALLET
        captured.append(body)
        return {"ok": True, "key": {"id": "msk-test-key", "status": "active", "wallet_address": WALLET}}

    monkeypatch.setattr(live, "_hub_post", fake_post)
    authorization = live.issue_cli_multisession_authorization(
        "https://mainneta-hub1.greatlibrary.io", wallet=SimpleNamespace(address=WALLET, private_key=KEY)
    )
    assert authorization == {
        "kind": "multisession_key", "wallet_address": WALLET,
        "key_id": "msk-test-key", "multisession_key_id": "msk-test-key",
        "chain_id": live.HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID,
    }
    assert len(captured) == 1


def test_live_offer_executes_chat_and_preserves_ring_proof() -> None:
    captured = []

    def fake_chat(messages):
        captured.extend(messages)
        return ChatResponse(content="13", provider="ollama", model="gemma4:26b", metadata={"ring3_answer_verified": True})

    result = live._chat_result(fake_chat, {
        "work": {"messages": [{"role": "system", "content": "Short"}, {"role": "user", "content": "4+9"}]}
    })
    assert [(m.role, m.content) for m in captured] == [("system", "Short"), ("user", "4+9")]
    assert result["response"]["metadata"]["ring3_answer_verified"] is True
    assert result["response"]["content"] == "13"
    assert result["transport"] == "websocket-live-session"


def test_live_worker_requires_same_wallet_not_anonymous(monkeypatch) -> None:
    monkeypatch.setattr(live, "_hub_post", lambda *a, **kw: {"ok": True, "key": {"id": "bad", "status": "active", "wallet_address": "0x" + "2" * 40}})
    with pytest.raises(RuntimeError, match="wrong wallet"):
        live.issue_cli_multisession_authorization(
            "https://mainneta-hub1.greatlibrary.io", wallet=SimpleNamespace(address=WALLET, private_key=KEY)
        )


def test_data_submits_to_live_route_with_o3_key(monkeypatch) -> None:
    from main_computer import rag_code_edit_agent_guidance_smoke as rag
    from main_computer.hub_credit_indexer import wallet_account_id

    submitted = {}
    monkeypatch.setattr(live, "hub_uses_live_sessions", lambda _url: True)
    monkeypatch.setattr(live, "cli_request_authorization", lambda _url, *, officer: {"wallet_address": WALLET, "key_id": "msk-data", "chain_id": "42424242"})

    def fake_open(_url, path, *, method="GET", payload=None, **kwargs):
        submitted.update(path=path, payload=payload, method=method)
        raise RuntimeError("STOP_AFTER_POST")

    monkeypatch.setattr(rag, "_open_hub_ai_json", fake_open)
    with pytest.raises(RuntimeError, match="STOP_AFTER_POST"):
        rag.call_hub_worker_pull_ai_json(
            stage="test", system_prompt="Short", user_prompt="4+9", model="gemma4:26b",
            hub_url="https://mainnetc-hub1.greatlibrary.io", client_node_id="data-test",
            account_id=wallet_account_id(WALLET), timeout_seconds=10,
        )
    assert submitted["path"] == "/api/hub/v1/work/requests"
    assert submitted["method"] == "POST"
    assert submitted["payload"]["ring"] == "ring-3"
    assert submitted["payload"]["multisession_authorization"]["key_id"] == "msk-data"


def test_data_rejects_cross_wallet_credit_spend(monkeypatch) -> None:
    from main_computer import rag_code_edit_agent_guidance_smoke as rag
    monkeypatch.setattr(live, "hub_uses_live_sessions", lambda _url: True)
    monkeypatch.setattr(live, "cli_request_authorization", lambda _url, *, officer: {"wallet_address": WALLET, "key_id": "msk-data"})
    with pytest.raises(RuntimeError, match="differs from its signed wallet"):
        rag.call_hub_worker_pull_ai_json(
            stage="test", system_prompt="Short", user_prompt="4+9", model="gemma4:26b",
            hub_url="https://mainnetc-hub1.greatlibrary.io", client_node_id="data-test",
            account_id="a-different-account", timeout_seconds=10,
        )


def test_worker_pull_entrypoint_selects_live_session_instead_of_rest(monkeypatch) -> None:
    from main_computer.config import MainComputerConfig
    from main_computer.hub import serve_hub_worker_pull

    calls = []
    monkeypatch.setattr(live, "hub_uses_live_sessions", lambda url: True)
    monkeypatch.setattr(live, "serve_cli_live_worker", lambda **kwargs: calls.append(kwargs))
    from dataclasses import replace
    config = replace(MainComputerConfig.from_env(), hub_url="https://mainneta-hub1.greatlibrary.io",
                     hub_worker_node_id="captain-smoke-a", model="gemma4:26b")
    serve_hub_worker_pull(
        config, lambda messages: ChatResponse(content="ok", provider="ollama", model=config.model),
        max_requests=1, officer="o3", verbose=False,
    )
    assert len(calls) == 1
    assert calls[0]["officer"] == "o3"
    assert calls[0]["worker_node_id"] == "captain-smoke-a"
    assert calls[0]["assigned_ring"] == 3


def test_captain_live_request_uses_new_route_and_auth_before_spend(monkeypatch, tmp_path) -> None:
    from main_computer import captain_cli as captain
    from main_computer.config import MainComputerConfig

    wallet = captain.CaptainWallet(
        selector="captain", office="O0", title="Captain", address=WALLET, private_key=KEY
    )
    runtime = captain.CaptainRuntime(
        config=MainComputerConfig(workspace=tmp_path), deployment_path=tmp_path / "latest.json",
        deployment={}, wallet=wallet, rpc_url="https://rpc.example", chain_id=42424240,
        xlag_address="0x" + "1" * 40, network="mainnet",
        bridge_escrow_address="0x" + "2" * 40, bridge_controller_address="0x" + "3" * 40,
    )
    calls = []
    monkeypatch.setattr(captain, "build_captain_runtime", lambda *a, **k: runtime)
    monkeypatch.setattr(live, "hub_uses_live_sessions", lambda _url: True)
    monkeypatch.setattr(live, "issue_cli_multisession_authorization", lambda _url, *, wallet, timeout_s: (
        calls.append(("authorization", wallet.address)) or {"kind": "multisession_key", "wallet_address": WALLET, "key_id": "key-1"}
    ))

    def fake_post(_url, path, payload, *, timeout_s):
        calls.append((path, payload))
        assert path == "/api/hub/v1/work/requests"
        return {"ok": True, "accepted": True, "request_id": "req-one"}

    monkeypatch.setattr(captain, "_post_hub_json", fake_post)
    monkeypatch.setattr(captain, "_poll_hub_request", lambda *a, **k: {"request": {"state": "completed"}})
    monkeypatch.setattr(captain, "_pickup_hub_request_result", lambda *a, **k: {"response": {"content": "ready"}})
    result = captain.run_captain(
        ["smoke", "captain", "report", "ready", "--no-bridge", "--no-stipend", "--no-chain", "--poll-seconds", "0"],
        config=runtime.config, cwd=tmp_path,
    )
    assert result == 0
    assert calls[0] == ("authorization", WALLET)
    path, payload = calls[1]
    assert path == "/api/hub/v1/work/requests"
    assert payload["ring"] == "ring-3"
    assert payload["multisession_authorization"]["wallet_address"] == WALLET
    assert payload["messages"][0]["content"] == "report ready"
    assert len(calls) == 2
