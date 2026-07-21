from __future__ import annotations

import json
from pathlib import Path

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




def test_mainnet_captain_wallet_reads_private_state_key_without_manifest_secret(tmp_path) -> None:
    deployment_path = tmp_path / "runtime" / "deployments" / "mainnet" / "latest.json"
    deployment_path.parent.mkdir(parents=True)
    deployment = {
        "chain": {"chain_id": 42424240},
        "offices": [
            {
                "office": "O0",
                "title": "Captain",
                "address": "0xE81Bc6ef15b991F1083db9c1a0899Ef9AE00516F",
            }
        ],
    }
    private_key = "0x" + "1" * 64
    private_state = tmp_path / "runtime" / "state" / "main_computer.private.yaml"
    private_state.parent.mkdir(parents=True)
    private_state.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "networks:",
                "  mainnet:",
                "    wallets:",
                "      captain:",
                "        address: \"0xE81Bc6ef15b991F1083db9c1a0899Ef9AE00516F\"",
                f"        private_key: \"{private_key}\"",
                "",
            ]
        ),
        encoding="utf-8",
    )

    wallet = resolve_captain_wallet(
        "captain",
        deployment=deployment,
        deployment_path=deployment_path,
        private_state_path=private_state,
    )

    assert wallet.address == "0xE81Bc6ef15b991F1083db9c1a0899Ef9AE00516F"
    assert wallet.private_key == private_key


def test_mainnet_captain_wallet_prefers_private_state_when_manifest_has_dev_defaults(tmp_path) -> None:
    deployment_path = tmp_path / "runtime" / "deployments" / "mainnet" / "latest.json"
    deployment_path.parent.mkdir(parents=True)
    deployment = {
        "chain": {"chain_id": 42424240},
        "offices": [
            {
                "office": "O0",
                "title": "Captain",
                "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
            }
        ],
    }
    private_key = "0x" + "2" * 64
    private_state = tmp_path / "runtime" / "state" / "main_computer.private.yaml"
    private_state.parent.mkdir(parents=True)
    private_state.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "networks:",
                "  mainnet:",
                "    wallets:",
                "      captain:",
                "        address: \"0x0e2d466Bd9252CF122F0100a862090bd4B3aCE75\"",
                f"        private_key: \"{private_key}\"",
                "",
            ]
        ),
        encoding="utf-8",
    )

    wallet = resolve_captain_wallet(
        "captain",
        deployment=deployment,
        deployment_path=deployment_path,
        private_state_path=private_state,
    )

    assert wallet.address == "0x0e2d466Bd9252CF122F0100a862090bd4B3aCE75"
    assert wallet.private_key == private_key


def test_private_state_key_must_match_selected_office_address(tmp_path) -> None:
    deployment_path = tmp_path / "runtime" / "deployments" / "mainnet" / "latest.json"
    deployment_path.parent.mkdir(parents=True)
    deployment = {
        "chain": {"chain_id": 42424240},
        "offices": [
            {
                "office": "O0",
                "title": "Captain",
                "address": "0xE81Bc6ef15b991F1083db9c1a0899Ef9AE00516F",
            }
        ],
    }
    private_state = tmp_path / "runtime" / "state" / "main_computer.private.yaml"
    private_state.parent.mkdir(parents=True)
    private_state.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "networks:",
                "  mainnet:",
                "    wallets:",
                "      captain:",
                "        address: \"0x0000000000000000000000000000000000000001\"",
                "        private_key: \"0x1111111111111111111111111111111111111111111111111111111111111111\"",
                "",
            ]
        ),
        encoding="utf-8",
    )

    wallet = resolve_captain_wallet(
        "captain",
        deployment=deployment,
        deployment_path=deployment_path,
        private_state_path=private_state,
    )

    assert wallet.private_key == ""


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
                        "private_key": "0x" + "1" * 64,
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
    assert submit_payload["account_id"] == "0xe81bc6ef15b991f1083db9c1a0899ef9ae00516f"


def test_captain_smoke_falls_back_to_wallet_funding_import_when_hub_lacks_bridge_metadata(tmp_path, monkeypatch) -> None:
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
                        "private_key": "0x" + "1" * 64,
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
    assert import_payload["wallet_address"] == "0xE81Bc6ef15b991F1083db9c1a0899Ef9AE00516F"
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
