from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from tools.mother.common.ethereum_identity import private_key_to_address
from pathlib import Path
from types import SimpleNamespace

from main_computer.config import MainComputerConfig

from main_computer.captain_cli import (
    CaptainCliError,
    CaptainRuntime,
    CaptainWallet,
    _apply_captain_live_defaults,
    _apply_captain_smoke_defaults,
    _extract_ai_response_text,
    _hub_http_headers,
    _is_missing_bridge_completion_metadata_error,
    _pickup_hub_request_result,
    _print_captain_result,
    _sum_charge_credit_wei,
    build_bridge_wallet_funding_import_payload,
    build_captain_options_parser,
    build_hub_request_payload,
    normalize_smoke_id,
    parse_captain_invocation,
    resolve_captain_wallet,
    run_captain,
    send_captain_bridge_refund,
)


def test_captain_smoke_consumes_free_prompt_until_first_option() -> None:
    parsed = parse_captain_invocation(["smoke", "john", "luc", "picard", "--ring", "3", "--yes"])

    assert parsed.smoke is True
    assert parsed.selector == "captain"
    assert parsed.prompt == "john luc picard"
    assert parsed.option_tokens == ("--ring", "3", "--yes")


def test_captain_smoke_can_select_officer_then_continue_prompt() -> None:
    parsed = parse_captain_invocation(["smoke", "officer", "o2", "make", "it", "so", "--prompt", "override"])

    assert parsed.smoke is True
    assert parsed.selector == "o2"
    assert parsed.prompt == "make it so"
    assert parsed.option_tokens == ("--prompt", "override")


def test_dev_captain_wallet_defaults_to_local_office_key_without_printing_it() -> None:
    deployment = {
        "chain": {"chain_id": 42424242},
        "offices": [
            {
                "office": "O0",
                "title": "Captain",
                "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
            }
        ],
    }

    wallet = resolve_captain_wallet("captain", deployment=deployment, deployment_path=Path("runtime/deployments/dev/latest.json"))

    assert wallet.office == "O0"
    assert wallet.title == "Captain"
    assert wallet.address.lower() == "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"
    assert wallet.private_key.startswith("0x")
    assert len(wallet.private_key) == 66




MOTHER_O0_KEY = "0x" + "1" * 64
MOTHER_O3_KEY = "0x" + "4" * 64
MOTHER_O0_ADDRESS = private_key_to_address(MOTHER_O0_KEY)
MOTHER_O3_ADDRESS = private_key_to_address(MOTHER_O3_KEY)


def _stub_verified_mother(monkeypatch, *, network="mainnet", captain=None, o3=None, fail=None):
    """Stub only Mother's verified reader, never a YAML or manifest key source."""
    from tools.mother.common import private_state as mother_private_state

    document = {"networks": {network: {"wallets": {
        "captain": captain if captain is not None else {
            "address": MOTHER_O0_ADDRESS, "private_key": MOTHER_O0_KEY,
        },
        "o3": o3 if o3 is not None else {
            "address": MOTHER_O3_ADDRESS, "private_key": MOTHER_O3_KEY,
        },
    }}}}
    calls = []

    def fake_read(paths, *, operation):
        assert paths.identity_file.name == "identity.private.yaml"
        assert paths.identity_file.parent.name == "mother"
        assert operation.network == network
        calls.append((paths, operation))
        if fail is not None:
            raise fail
        return SimpleNamespace(canonical_object_bytes=json.dumps(document).encode("utf-8"))

    monkeypatch.setattr(mother_private_state, "read_private_state", fake_read)
    return calls


def test_mainnet_captain_wallet_uses_verified_mother_and_ignores_manifest(tmp_path, monkeypatch) -> None:
    calls = _stub_verified_mother(monkeypatch)
    deployment_path = tmp_path / "runtime" / "deployments" / "mainnet" / "latest.json"
    deployment = {
        "chain": {"chain_id": 42424240, "network_name": "dev"},
        "offices": [{"office": "O0", "title": "Captain", "address": "0x" + "f" * 40,
                     "private_key": "0x" + "2" * 64}],
    }
    monkeypatch.setenv("MAIN_COMPUTER_CAPTAIN_PRIVATE_KEY", "0x" + "3" * 64)
    wallet = resolve_captain_wallet(
        "captain", deployment=deployment, deployment_path=deployment_path,
    )
    assert wallet.office == "O0"
    assert wallet.address == MOTHER_O0_ADDRESS
    assert wallet.private_key == MOTHER_O0_KEY
    assert calls and calls[0][0].root == tmp_path / "runtime" / "state" / "mother"


def test_mainnet_o3_and_address_selector_use_mother(tmp_path, monkeypatch) -> None:
    _stub_verified_mother(monkeypatch)
    deployment_path = tmp_path / "runtime/deployments/mainnet/latest.json"
    wallet = resolve_captain_wallet("o3", deployment={}, deployment_path=deployment_path)
    assert wallet.office == "O3"
    assert wallet.address == MOTHER_O3_ADDRESS
    assert wallet.private_key == MOTHER_O3_KEY
    same = resolve_captain_wallet(MOTHER_O3_ADDRESS, deployment={}, deployment_path=deployment_path)
    assert same.office == "O3" and same.private_key == MOTHER_O3_KEY
    with pytest.raises(CaptainCliError, match="No Mother mainnet officer matches"):
        resolve_captain_wallet("0x" + "f" * 40, deployment={}, deployment_path=deployment_path)


def test_mainnet_invalid_key_address_fails_closed(tmp_path, monkeypatch) -> None:
    _stub_verified_mother(monkeypatch, captain={
        "address": MOTHER_O0_ADDRESS, "private_key": "0x" + "2" * 64,
    })
    with pytest.raises(CaptainCliError, match="signing key does not match"):
        resolve_captain_wallet("o0", deployment={}, deployment_path=tmp_path / "runtime/deployments/mainnet/latest.json")


def test_mainnet_missing_key_fails_closed(tmp_path, monkeypatch) -> None:
    _stub_verified_mother(monkeypatch, captain={"address": MOTHER_O0_ADDRESS})
    with pytest.raises(CaptainCliError, match="no private key"):
        resolve_captain_wallet("captain", deployment={}, deployment_path=tmp_path / "runtime/deployments/mainnet/latest.json")


def test_mainnet_corrupt_mother_fails_even_with_manifest_key(tmp_path, monkeypatch) -> None:
    _stub_verified_mother(monkeypatch, fail=RuntimeError("VERIFICATION_MISMATCH"))
    deployment = {"offices": [{"address": MOTHER_O0_ADDRESS, "private_key": MOTHER_O0_KEY}]}
    with pytest.raises(CaptainCliError, match="Cannot verify Mother private state"):
        resolve_captain_wallet("o0", deployment=deployment, deployment_path=tmp_path / "runtime/deployments/mainnet/latest.json")


@pytest.mark.parametrize("override", ["private_key_override", "private_state_path"])
def test_mainnet_rejects_key_override(tmp_path, monkeypatch, override) -> None:
    calls = _stub_verified_mother(monkeypatch)
    kwargs = {override: MOTHER_O0_KEY if override == "private_key_override" else tmp_path / "untrusted.yaml"}
    with pytest.raises(CaptainCliError, match="overrides are not supported"):
        resolve_captain_wallet("o0", deployment={}, deployment_path=tmp_path / "runtime/deployments/mainnet/latest.json", **kwargs)
    assert not calls


def test_testnet_reads_its_own_mother_network(tmp_path, monkeypatch) -> None:
    _stub_verified_mother(monkeypatch, network="testnet")
    wallet = resolve_captain_wallet("o0", deployment={}, deployment_path=tmp_path / "runtime/deployments/testnet/latest.json")
    assert wallet.private_key == MOTHER_O0_KEY


def test_hub_payload_defaults_to_ring3_worker_pull_market_quote() -> None:
    smoke_id = normalize_smoke_id("captain smoke test")
    payload = build_hub_request_payload(
        prompt="john luc picard",
        model="agent-model",
        client_node_id="captain-cli",
        wallet_address="0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        max_credits=1,
        requested_ring=3,
        idempotency_key=smoke_id,
        smoke_id=smoke_id,
    )

    assert payload["account_id"] == "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"
    assert payload["max_credits"] == 1
    assert payload["metadata"]["requested_ring"] == 3
    assert payload["metadata"]["worker_pull_v0"] is True
    assert payload["metadata"]["captain_smoke"] is True
    assert payload["messages"] == [{"role": "user", "content": "john luc picard"}]



def test_captain_smoke_defaults_to_mainnet_execute_bridge() -> None:
    options = build_captain_options_parser().parse_args([])

    _apply_captain_smoke_defaults(options, base_config=MainComputerConfig(workspace=Path.cwd()), cwd=Path.cwd())

    assert options.network == "mainnet"
    assert options.execute is True
    assert options.no_chain is True
    assert options.poll_seconds == 90.0
    assert options.hub_url == "https://mainnet-hub.greatlibrary.io"
    assert str(options.state).replace("\\", "/").endswith("runtime/deployments/mainnet/latest.json")


def test_captain_smoke_prepare_only_does_not_execute() -> None:
    options = build_captain_options_parser().parse_args(["--prepare-only"])

    _apply_captain_smoke_defaults(options, base_config=MainComputerConfig(workspace=Path.cwd()), cwd=Path.cwd())

    assert options.network == "mainnet"
    assert options.execute is False


def test_bare_captain_prompt_defaults_to_mainnet_live_bridge() -> None:
    options = build_captain_options_parser().parse_args([])

    _apply_captain_live_defaults(options, base_config=MainComputerConfig(workspace=Path.cwd()), cwd=Path.cwd())

    assert options.network == "mainnet"
    assert options.execute is True
    assert options.no_chain is True
    assert options.poll_seconds == 90.0
    assert options.hub_url == "https://mainnet-hub.greatlibrary.io"
    assert str(options.state).replace("\\", "/").endswith("runtime/deployments/mainnet/latest.json")




def test_hub_http_headers_identify_captain_cli_client(monkeypatch) -> None:
    monkeypatch.delenv("MAIN_COMPUTER_HUB_USER_AGENT", raising=False)

    headers = _hub_http_headers(json_body=True)

    assert headers["Accept"] == "application/json"
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Main-Computer-Client"] == "captain-cli"
    assert headers["User-Agent"].startswith("main-computer-captain-cli/")
    assert "Python-urllib" not in headers["User-Agent"]


def test_hub_http_headers_allow_operator_user_agent_override(monkeypatch) -> None:
    monkeypatch.setenv("MAIN_COMPUTER_HUB_USER_AGENT", "allowed-mainnet-client/2026")

    headers = _hub_http_headers()

    assert headers["User-Agent"] == "allowed-mainnet-client/2026"
    assert "Content-Type" not in headers

def test_charge_wei_sum_reads_atomic_charge_rows() -> None:
    assert _sum_charge_credit_wei(
        {
            "charges": [
                {"charged_credit_wei": "125"},
                {"charged_credit_wei": 75},
                {"charged_credit_wei": "bad"},
            ]
        }
    ) == 200


def test_hub_result_pickup_tries_pickup_when_result_endpoint_is_missing(monkeypatch) -> None:
    calls: list[str] = []

    def fake_get_hub_json(hub_url: str, path: str, *, timeout_s: float) -> dict[str, object]:
        calls.append(path)
        if "/result?" in path:
            raise CaptainCliError(
                "Hub request failed for https://mainnet-hub.greatlibrary.io/api/hub/v1/requests/hub_123/result "
                "with HTTP 404: Not Found"
            )
        if "/pickup?" in path:
            return {"ok": True, "response": {"content": "Make it so."}}
        raise AssertionError(f"unexpected hub path: {path}")

    monkeypatch.setattr("main_computer.captain_cli._get_hub_json", fake_get_hub_json)

    result = _pickup_hub_request_result(
        "https://mainnet-hub.greatlibrary.io",
        "hub_123",
        account_id="0xe81bc6ef15b991f1083db9c1a0899ef9ae00516f",
        client_node_id="main-computer-captain-cli",
        timeout_s=30.0,
    )

    assert result["ok"] is True
    assert result["result_pickup_fallback"] is True
    assert result["result_pickup_endpoint"] == "pickup"
    assert calls == [
        "/api/hub/v1/requests/hub_123/result?account_id=0xe81bc6ef15b991f1083db9c1a0899ef9ae00516f&client_node_id=main-computer-captain-cli",
        "/api/hub/v1/requests/hub_123/pickup?account_id=0xe81bc6ef15b991f1083db9c1a0899ef9ae00516f&client_node_id=main-computer-captain-cli",
    ]


def test_hub_result_pickup_falls_back_to_status_when_pickup_is_missing(monkeypatch) -> None:
    calls: list[str] = []

    def fake_get_hub_json(hub_url: str, path: str, *, timeout_s: float) -> dict[str, object]:
        calls.append(path)
        if "/result?" in path or "/pickup?" in path:
            raise CaptainCliError(
                "Hub request failed for https://mainnet-hub.greatlibrary.io/api/hub/v1/requests/hub_123/result "
                "with HTTP 404: Not Found"
            )
        if path == "/api/hub/v1/requests/hub_123":
            return {"ok": True, "request": {"state": "completed", "response": {"content": "Engage."}}}
        raise AssertionError(f"unexpected hub path: {path}")

    monkeypatch.setattr("main_computer.captain_cli._get_hub_json", fake_get_hub_json)

    result = _pickup_hub_request_result(
        "https://mainnet-hub.greatlibrary.io",
        "hub_123",
        account_id="0xe81bc6ef15b991f1083db9c1a0899ef9ae00516f",
        client_node_id="main-computer-captain-cli",
        timeout_s=30.0,
    )

    assert result["ok"] is True
    assert result["result_pickup_fallback"] is True
    assert result["request"]["state"] == "completed"
    assert calls[-1] == "/api/hub/v1/requests/hub_123"


def test_ai_response_text_extracts_common_result_shapes() -> None:
    assert _extract_ai_response_text({"response": {"choices": [{"message": {"content": "Make it so."}}]}}) == "Make it so."
    assert (
        _extract_ai_response_text(
            {
                "ok": True,
                "request": {
                    "state": "completed",
                    "result": {
                        "status": "success",
                        "response": {
                            "content": "Engage.",
                            "provider": "local",
                            "model": "gemma4:26b",
                        },
                    },
                },
            }
        )
        == "Engage."
    )
    assert (
        _extract_ai_response_text(
            {
                "request": {
                    "state": "completed",
                    "messages": [
                        {"role": "user", "content": "john luc picard make it so"},
                        {"role": "assistant", "content": "Aye, Captain."},
                    ],
                }
            }
        )
        == "Aye, Captain."
    )


def test_print_captain_result_shows_nested_worker_response(capsys) -> None:
    _print_captain_result(
        {
            "prompt": "john luc picard make it so",
            "wallet": {"title": "Captain", "selector": "captain", "address": "0x1"},
            "hub": {
                "enabled": True,
                "network": "mainnet",
                "url": "https://hub.example",
                "ring": 3,
                "poll": {"request": {"state": "completed"}},
                "result": {
                    "request": {
                        "state": "completed",
                        "result": {"status": "success", "response": {"content": "Make it so."}},
                    }
                },
            },
            "chain": {},
        },
        json_output=False,
    )

    output = capsys.readouterr().out
    assert "Hub request state: completed" in output
    assert "AI result:" in output
    assert "Make it so." in output



def test_bridge_wallet_funding_import_payload_uses_deposit_receipt() -> None:
    runtime = CaptainRuntime(
        config=MainComputerConfig(workspace=Path.cwd(), hub_url="https://hub.example"),
        deployment_path=Path("runtime/deployments/mainnet/latest.json"),
        deployment={},
        wallet=CaptainWallet(
            selector="captain",
            office="O0",
            title="Captain",
            address="0xE81Bc6ef15b991F1083db9c1a0899Ef9AE00516F",
            private_key="0x" + "1" * 64,
        ),
        rpc_url="https://rpc.example",
        chain_id=42424240,
        xlag_address="0x1111111111111111111111111111111111111111",
        network="mainnet",
        bridge_escrow_address="0x2222222222222222222222222222222222222222",
        bridge_controller_address="0x3333333333333333333333333333333333333333",
    )
    payload = build_bridge_wallet_funding_import_payload(
        runtime,
        {
            "deposit_id": "0x" + "a" * 64,
            "transaction_hash": "0x" + "b" * 64,
            "amount_credit_wei": "1000000000000000000",
            "receipt": {
                "blockNumber": "0x2a",
                "logs": [
                    {"address": "0x9999999999999999999999999999999999999999", "logIndex": "0x1"},
                    {"address": "0x2222222222222222222222222222222222222222", "logIndex": "0x7"},
                ],
            },
        },
    )

    assert payload["wallet_address"] == "0xE81Bc6ef15b991F1083db9c1a0899Ef9AE00516F"
    assert payload["chain_id"] == 42424240
    assert payload["contract_address"] == "0x2222222222222222222222222222222222222222"
    assert payload["tx_hash"] == "0x" + "b" * 64
    assert payload["log_index"] == 7
    assert payload["block_number"] == 42
    assert payload["payment_amount_base_units"] == "1000000000000000000"
    assert payload["credits_granted_wei"] == "1000000000000000000"
    assert payload["metadata"]["completion_fallback"] is True


def test_bare_captain_prompt_uses_mainnet_hub_request_path(tmp_path, monkeypatch) -> None:
    _stub_verified_mother(monkeypatch)
    monkeypatch.setattr("main_computer.hub_cli_live_session.hub_uses_live_sessions", lambda *_a, **_k: False)
    escrow = "0x2222222222222222222222222222222222222222"
    deployment_path = tmp_path / "runtime" / "deployments" / "mainnet" / "latest.json"
    deployment_path.parent.mkdir(parents=True)
    deployment_path.write_text(
        json.dumps(
            {
                "chain": {
                    "network": "mainnet",
                    "chain_id": 42424240,
                    "rpc_url": "https://rpc.example",
                },
                "contracts": {
                    "xlag-bridge-reserve": {"address": "0x1111111111111111111111111111111111111111"},
                    "hub_credit_bridge_escrow": {
                        "address": escrow,
                        "bridge_controller_address": "0x3333333333333333333333333333333333333333",
                    },
                },
                "offices": [
                    {
                        "office": "O0",
                        "title": "Captain",
                        "address": "0xE81Bc6ef15b991F1083db9c1a0899Ef9AE00516F",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_post_hub_json(hub_url: str, path: str, payload: dict[str, object], *, timeout_s: float) -> dict[str, object]:
        calls.append((hub_url, path, payload))
        if path == "/api/hub/v1/requests/quote":
            return {"ok": True, "quote_id": "quote-1"}
        if path == "/api/hub/v1/credits/wallet-funding/complete":
            return {"ok": True, "funding_model": "hub_credit_bridge_escrow_wallet_v1"}
        if path == "/api/hub/v1/requests":
            return {"ok": True}
        raise AssertionError(f"unexpected hub path: {path}")

    monkeypatch.setattr("main_computer.captain_cli._post_hub_json", fake_post_hub_json)
    monkeypatch.setattr(
        "main_computer.captain_cli._fetch_hub_credit_balance",
        lambda *args, **kwargs: {"ok": True, "account": {"available_credit_wei": "0"}},
    )
    monkeypatch.setattr(
        "main_computer.captain_cli.send_captain_bridge_deposit",
        lambda *args, **kwargs: {
            "deposit_id": "0x" + "a" * 64,
            "transaction_hash": "0x" + "b" * 64,
            "amount_credit_wei": "1000000000000000000",
            "receipt": {"blockNumber": "0x2a", "logs": [{"address": escrow, "logIndex": "0x0"}]},
        },
    )

    exit_code = run_captain(
        ["is", "this", "thing", "on?", "--poll-seconds", "0", "--no-bridge-refund"],
        config=MainComputerConfig(workspace=tmp_path),
        cwd=tmp_path,
    )

    assert exit_code == 0
    assert [path for _hub_url, path, _payload in calls] == [
        "/api/hub/v1/requests/quote",
        "/api/hub/v1/credits/wallet-funding/complete",
        "/api/hub/v1/requests",
    ]
    assert all(hub_url == "https://mainnet-hub.greatlibrary.io" for hub_url, _path, _payload in calls)
    submit_payload = calls[-1][2]
    assert submit_payload["messages"] == [{"role": "user", "content": "is this thing on?"}]
    assert submit_payload["account_id"] == MOTHER_O0_ADDRESS.lower()


def test_captain_smoke_falls_back_to_wallet_funding_import_when_hub_lacks_bridge_metadata(tmp_path, monkeypatch) -> None:
    _stub_verified_mother(monkeypatch)
    monkeypatch.setattr("main_computer.hub_cli_live_session.hub_uses_live_sessions", lambda *_a, **_k: False)
    escrow = "0x2222222222222222222222222222222222222222"
    deployment_path = tmp_path / "runtime" / "deployments" / "mainnet" / "latest.json"
    deployment_path.parent.mkdir(parents=True)
    deployment_path.write_text(
        json.dumps(
            {
                "chain": {
                    "network": "bridge-net",
                    "chain_id": 42424240,
                    "rpc_url": "https://rpc.example",
                },
                "contracts": {
                    "xlag-bridge-reserve": {"address": "0x1111111111111111111111111111111111111111"},
                    "hub_credit_bridge_escrow": {
                        "address": escrow,
                        "bridge_controller_address": "0x3333333333333333333333333333333333333333",
                    },
                },
                "offices": [
                    {
                        "office": "O0",
                        "title": "Captain",
                        "address": "0xE81Bc6ef15b991F1083db9c1a0899Ef9AE00516F",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_post_hub_json(hub_url: str, path: str, payload: dict[str, object], *, timeout_s: float) -> dict[str, object]:
        calls.append((path, payload))
        if path == "/api/hub/v1/requests/quote":
            return {"ok": True, "quote_id": "quote-1"}
        if path == "/api/hub/v1/credits/wallet-funding/complete":
            raise CaptainCliError(
                "Hub request failed for https://mainnet-hub.greatlibrary.io/api/hub/v1/credits/wallet-funding/complete "
                "with HTTP 400: {\"error\": \"Could not find a usable runtime/deployments/current.json "
                "with hub_credit_bridge_escrow metadata.\"}"
            )
        if path == "/api/hub/v1/credits/wallet-funding/import":
            return {"ok": True, "funding_model": "hub_credit_bridge_escrow_wallet_v1"}
        if path == "/api/hub/v1/requests":
            return {"ok": True}
        raise AssertionError(f"unexpected hub path: {path}")

    monkeypatch.setattr("main_computer.captain_cli._post_hub_json", fake_post_hub_json)
    monkeypatch.setattr(
        "main_computer.captain_cli._fetch_hub_credit_balance",
        lambda *args, **kwargs: {"ok": True, "account": {"available_credit_wei": "0"}},
    )
    monkeypatch.setattr(
        "main_computer.captain_cli.send_captain_bridge_deposit",
        lambda *args, **kwargs: {
            "deposit_id": "0x" + "a" * 64,
            "transaction_hash": "0x" + "b" * 64,
            "amount_credit_wei": "1000000000000000000",
            "receipt": {"blockNumber": "0x2a", "logs": [{"address": escrow, "logIndex": "0x0"}]},
        },
    )

    exit_code = run_captain(
        [
            "smoke",
            "john",
            "luc",
            "picard",
            "make",
            "it",
            "so",
            "--state",
            str(deployment_path),
            "--hub-url",
            "https://mainnet-hub.greatlibrary.io",
            "--poll-seconds",
            "0",
            "--no-bridge-refund",
        ],
        config=MainComputerConfig(workspace=tmp_path),
        cwd=tmp_path,
    )

    assert exit_code == 0
    paths = [path for path, _payload in calls]
    assert paths == [
        "/api/hub/v1/requests/quote",
        "/api/hub/v1/credits/wallet-funding/complete",
        "/api/hub/v1/credits/wallet-funding/import",
        "/api/hub/v1/requests",
    ]
    import_payload = calls[2][1]
    assert import_payload["wallet_address"] == MOTHER_O0_ADDRESS
    assert import_payload["contract_address"] == escrow
    assert import_payload["credits_granted_wei"] == "1000000000000000000"
    assert import_payload["metadata"]["completion_fallback"] is True


def test_missing_bridge_completion_metadata_detector_is_narrow() -> None:
    assert _is_missing_bridge_completion_metadata_error(
        CaptainCliError(
            "Hub request failed for /api/hub/v1/credits/wallet-funding/complete with HTTP 400: "
            "Could not find runtime/deployments/current.json with hub_credit_bridge_escrow metadata."
        )
    )
    assert not _is_missing_bridge_completion_metadata_error(
        CaptainCliError("Hub request failed for /api/hub/v1/credits/wallet-funding/complete with HTTP 500: database down")
    )
    assert not _is_missing_bridge_completion_metadata_error(
        CaptainCliError("Hub request failed for /api/hub/v1/requests/quote with HTTP 400: hub_credit_bridge_escrow metadata")
    )


def test_captain_bridge_refund_is_signed_by_hub_not_local_controller(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_post(hub_url: str, path: str, payload: dict[str, object], *, timeout_s: float) -> dict[str, object]:
        assert hub_url == "https://mainnet-hub.greatlibrary.io"
        assert timeout_s == 30.0
        calls.append((path, payload))
        return {"ok": True, "signing_mode": "hub-bridge-controller"}

    monkeypatch.setattr("main_computer.captain_cli._post_hub_json", fake_post)
    runtime = SimpleNamespace(
        wallet=SimpleNamespace(address="0x1111111111111111111111111111111111111111"),
        config=SimpleNamespace(hub_url="https://mainnet-hub.greatlibrary.io"),
    )

    result = send_captain_bridge_refund(
        runtime,
        bridge_credit_wei=2 * 10**18,
        charged_credit_wei=10**18,
        smoke_id="0x" + "ab" * 32,
        request_id="request-1",
        deposit_id="0x" + "cd" * 32,
        controller_private_key="0x" + "99" * 32,
        timeout_s=30.0,
    )

    assert result["ok"] is True
    assert result["signing_mode"] == "hub-bridge-controller"
    assert len(calls) == 1
    path, payload = calls[0]
    assert path == "/api/hub/v1/credits/bridge-reconciliation/execute"
    assert payload["request_id"] == "request-1"
    assert payload["deposit_id"] == "0x" + "cd" * 32
    assert payload["wallet_address"] == runtime.wallet.address
    assert payload["expected_bridge_credit_wei"] == str(2 * 10**18)
    assert payload["expected_charged_credit_wei"] == str(10**18)
    assert "rectified_credit_wei" not in payload
    assert "withdrawn_credit_wei" not in payload
    assert "recipient_address" not in payload
    assert "rectification_id" not in payload
    assert "withdrawal_id" not in payload
    assert "private_key" not in payload
    assert "99" * 32 not in json.dumps(payload)


def test_mainnet_real_verified_mother_private_state(tmp_path) -> None:
    """Exercise canonical Mother bootstrap, verification and Captain resolution together."""
    from tools.mother.common.models import OperationIdentity
    from tools.mother.common.paths import MotherPaths
    from tools.mother.common.private_state import (
        install_verified_private_state, prepare_private_state_bootstrap,
    )

    paths = MotherPaths(runtime_state_root=tmp_path / "runtime/state").resolve_private_state_paths()
    operation = OperationIdentity(
        operation_id="captain-verified-wallet-test", request_id="captain-test",
        network="mainnet", operation_kind="MOTHER-OP-DIAGNOSE",
    )
    document = {
        "kind": "main_computer.mother.private_state.v1", "schema_version": 1,
        "networks": {"mainnet": {"chain_id": 42424240, "wallets": {
            "captain": {"address": MOTHER_O0_ADDRESS, "private_key": MOTHER_O0_KEY},
        }}},
    }
    closure = prepare_private_state_bootstrap(
        paths, document, updated_at="2026-10-10T00:00:00Z",
        updated_by_action_id=operation.operation_id, operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)

    deployment_path = tmp_path / "runtime/deployments/mainnet/latest.json"
    # Neither public deployment state nor local environment keys exist.
    wallet = resolve_captain_wallet("captain", deployment={}, deployment_path=deployment_path)
    assert wallet.address == MOTHER_O0_ADDRESS
    assert wallet.private_key == MOTHER_O0_KEY

    # A modified Mother document without a fresh closure must not be trusted.
    original = paths.identity_file.read_bytes()
    paths.identity_file.write_bytes(original + b"\n# unsigned mutation\n")
    with pytest.raises(CaptainCliError, match="Cannot verify Mother private state"):
        resolve_captain_wallet("captain", deployment={}, deployment_path=deployment_path)
