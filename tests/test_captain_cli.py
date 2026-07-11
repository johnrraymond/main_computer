from __future__ import annotations

from pathlib import Path

from main_computer.config import MainComputerConfig

from main_computer.captain_cli import (
    _apply_captain_smoke_defaults,
    _extract_ai_response_text,
    _hub_http_headers,
    _sum_charge_credit_wei,
    build_captain_options_parser,
    build_hub_request_payload,
    normalize_smoke_id,
    parse_captain_invocation,
    resolve_captain_wallet,
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


def test_ai_response_text_extracts_common_result_shapes() -> None:
    assert _extract_ai_response_text({"response": {"choices": [{"message": {"content": "Make it so."}}]}}) == "Make it so."
