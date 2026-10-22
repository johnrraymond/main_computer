"""Command-network isolation for Captain, Data, worker-pull, and bridge funding."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer import cli
from main_computer.captain_cli import (
    CaptainCliError, _apply_captain_live_defaults, build_captain_options_parser,
)
from main_computer.config import MainComputerConfig
from main_computer.hub_credit_indexer import wallet_account_id


def _manifest(root: Path, network: str, address: str) -> None:
    path = root / "runtime" / "deployments" / network / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"offices": [{"office": "O3", "address": address}]}), encoding="utf-8")


def test_captain_testnet_profile_selects_hub_rpc_chain_and_manifest(tmp_path) -> None:
    opts = build_captain_options_parser().parse_args(["--network", "testnet", "--prepare-only"])
    _apply_captain_live_defaults(opts, base_config=MainComputerConfig(workspace=tmp_path), cwd=tmp_path)
    assert opts.network == "testnet"
    assert opts.hub_url == "https://testnet-hub.greatlibrary.io"
    assert opts.rpc_url == "https://testnet-rpc.greatlibrary.io"
    assert opts.chain_id == 42424241
    assert opts.state == tmp_path / "runtime/deployments/testnet/latest.json"
    assert opts.execute is False


def test_captain_testnet_missing_profile_fails_closed(monkeypatch, tmp_path) -> None:
    import main_computer.captain_cli as captain_cli
    monkeypatch.setattr(captain_cli, "load_hub_network_registry", lambda: {})
    opts = build_captain_options_parser().parse_args(["--network", "testnet"])
    with pytest.raises(CaptainCliError, match="Testnet network profile is unavailable"):
        _apply_captain_live_defaults(opts, base_config=MainComputerConfig(workspace=tmp_path), cwd=tmp_path)


@pytest.mark.parametrize("command", [
    ["captain", "--network", "testnet", "Make", "it", "so"],
    ["captain", "Make", "it", "so", "--network", "testnet"],
])
def test_captain_cli_forwards_testnet_before_or_after_prompt(command, monkeypatch) -> None:
    captured = []
    monkeypatch.setattr(cli, "run_captain", lambda argv, **kwargs: captured.append(argv) or 0)
    args = cli.build_parser().parse_args(command)
    assert args.func(args) == 0
    assert captured[0][-2:] == ["--network", "testnet"]


@pytest.mark.parametrize("command", [
    ["data", "--network", "testnet", "Make", "it", "so"],
    ["data", "Make", "it", "so", "--network", "testnet"],
])
def test_data_shortcut_forwards_testnet_to_captain_o3(command, monkeypatch) -> None:
    captured = []
    monkeypatch.setattr(cli, "run_captain", lambda argv, **kwargs: captured.append(argv) or 0)
    args = cli.build_parser().parse_args(command)
    assert args.func(args) == 0
    assert captured[0][:2] == ["smoke", "o3"]
    assert captured[0][-2:] == ["--network", "testnet"]


def test_data_testnet_agent_uses_its_hub_and_o3_and_ignores_mainnet_env(tmp_path, monkeypatch) -> None:
    mainnet = "0x" + "1" * 40
    testnet = "0x" + "2" * 40
    _manifest(tmp_path, "mainnet", mainnet)
    _manifest(tmp_path, "testnet", testnet)
    monkeypatch.setenv("MAIN_COMPUTER_DATA_O3_WALLET_ADDRESS", mainnet)
    monkeypatch.setenv("MAIN_COMPUTER_DATA_O3_HUB_ACCOUNT_ID", wallet_account_id(mainnet))
    monkeypatch.chdir(tmp_path)
    args = cli.build_parser().parse_args([
        "data", "--network", "testnet", "--agent", "--god-mode", "What", "is", "five",
    ])
    argv = cli._data_god_mode_smoke_argv(args, [*args.data_args, *cli._data_prefix_option_tokens(args)])
    assert argv[argv.index("--ai-hub-url") + 1] == "https://testnet-hub.greatlibrary.io"
    assert argv[argv.index("--ai-hub-account-id") + 1] == wallet_account_id(testnet)
    assert argv[argv.index("--ai-provider") + 1] == "hub"
    assert argv[argv.index("--ai-hub-transport") + 1] == "worker-pull"


def test_data_testnet_agent_network_flag_after_prompt(tmp_path, monkeypatch) -> None:
    address = "0x" + "3" * 40
    _manifest(tmp_path, "testnet", address)
    monkeypatch.chdir(tmp_path)
    args = cli.build_parser().parse_args([
        "data", "What", "is", "five", "--god-mode", "--agent", "--network", "testnet",
    ])
    argv = cli._data_god_mode_smoke_argv(args, [*args.data_args, *cli._data_prefix_option_tokens(args)])
    assert argv[argv.index("--ai-hub-url") + 1] == "https://testnet-hub.greatlibrary.io"
    assert argv[argv.index("--ai-hub-account-id") + 1] == wallet_account_id(address)


@pytest.mark.parametrize("network,expected", [
    ("mainnet", "https://mainnet-hub.greatlibrary.io"),
    ("testnet", "https://testnet-hub.greatlibrary.io"),
])
def test_captain_engage_worker_routes_selected_hub(monkeypatch, network, expected) -> None:
    seen = []
    fake_comp = SimpleNamespace(provider=SimpleNamespace(chat=lambda messages: None))
    monkeypatch.setattr(cli.MainComputer, "build", lambda cfg: fake_comp)
    monkeypatch.setattr(cli, "serve_hub_worker_pull", lambda config, chat, **kwargs: seen.append((config, kwargs)))
    args = cli.build_parser().parse_args([
        "captain", "smoke", "john", "luc", "picard", "engage", "computer",
        "--network", network, "--noverbose",
    ])
    assert args.func(args) == 0
    assert seen[0][0].hub_network == network
    assert seen[0][1]["hub_url"] == expected


@pytest.mark.parametrize("network,expected", [
    ("mainnet", "https://mainnet-hub.greatlibrary.io"),
    ("testnet", "https://testnet-hub.greatlibrary.io"),
])
def test_data_engage_worker_routes_selected_hub(monkeypatch, network, expected) -> None:
    seen = []
    fake_comp = SimpleNamespace(provider=SimpleNamespace(chat=lambda messages: None))
    monkeypatch.setattr(cli.MainComputer, "build", lambda cfg: fake_comp)
    monkeypatch.setattr(cli, "serve_hub_worker_pull", lambda config, chat, **kwargs: seen.append((config, kwargs)))
    args = cli.build_parser().parse_args([
        "data", "engage", "computer", "--agent", "--network", network, "--noverbose",
    ])
    assert args.func(args) == 0
    assert seen[0][0].hub_network == network
    assert seen[0][1]["hub_url"] == expected


def test_testnet_prefund_uses_testnet_authority_and_wallet(tmp_path, monkeypatch) -> None:
    import main_computer.captain_cli as captain_cli
    address = "0x" + "4" * 40
    _manifest(tmp_path, "testnet", address)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_data_god_mode_is_live_agent_worker_pull", lambda argv: True)
    observed = []
    def fake_runtime(config, *, options, selector, cwd):
        observed.append((options.network, selector, options.hub_url))
        return SimpleNamespace(wallet=SimpleNamespace(address=address),
                               config=SimpleNamespace(hub_url=options.hub_url))
    monkeypatch.setattr(captain_cli, "build_captain_runtime", fake_runtime)
    monkeypatch.setattr(captain_cli, "_fetch_hub_credit_balance", lambda *a, **kw: {"available_credit_wei": 10**25})
    # Compare via actual account to ensure no network cross-contamination.
    args = cli.build_parser().parse_args(["data", "--network", "testnet", "--agent", "--god-mode", "Question"])
    data_args = [*args.data_args, *cli._data_prefix_option_tokens(args)]
    argv = cli._data_god_mode_smoke_argv(args, data_args)
    # Prefunding must resolve its wallet and Hub from the same selected network.
    summary = cli._data_god_mode_bridge_prefund(args, data_args, argv)
    assert observed == [("testnet", address, "https://testnet-hub.greatlibrary.io")]
    assert summary["hub_url"] == "https://testnet-hub.greatlibrary.io"
    assert summary["account_id"] == wallet_account_id(address)


def test_testnet_missing_o3_never_uses_mainnet_wallet(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAIN_COMPUTER_DATA_O3_WALLET_ADDRESS", "0x" + "f" * 40)
    assert cli._data_o3_wallet_address(network="testnet") == ""
    assert cli._data_o3_hub_account_id(network="testnet") == ""


def test_testnet_missing_profile_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_hub_network_registry", lambda: {})
    with pytest.raises(RuntimeError, match="No Hub URL configured for testnet"):
        cli._data_network_hub_url(network="testnet")


@pytest.mark.parametrize("failure", ["unavailable", "wrong-chain", "wrong-contract", "wrong-controller"])
def test_bridge_completion_preflight_refuses_unverified_testnet_deposit(monkeypatch, failure) -> None:
    import main_computer.captain_cli as captain_cli
    status = {
        "ok": True,
        "deployment": {
            "chain_id": 42424241,
            "contract_address": "0x" + "a" * 40,
            "bridge_controller_address": "0x" + "b" * 40,
        },
    }
    if failure == "unavailable":
        status.update({"ok": False, "error": "bridge signer missing"})
    elif failure == "wrong-chain":
        status["deployment"]["chain_id"] = 42424240
    elif failure == "wrong-contract":
        status["deployment"]["contract_address"] = "0x" + "c" * 40
    elif failure == "wrong-controller":
        status["deployment"]["bridge_controller_address"] = "0x" + "c" * 40
    monkeypatch.setattr(captain_cli, "_get_hub_json", lambda *args, **kwargs: status)
    with pytest.raises(CaptainCliError, match="refusing.*deposit|refusing deposit"):
        captain_cli._require_hub_bridge_completion_ready(
            "https://testnet-hub.greatlibrary.io", chain_id=42424241,
            contract_address="0x" + "a" * 40,
            controller_address="0x" + "b" * 40,
            timeout_s=10,
        )


def test_bridge_completion_preflight_allows_matching_testnet_deployment(monkeypatch) -> None:
    import main_computer.captain_cli as captain_cli
    seen = []
    def get(hub_url, path, *, timeout_s):
        seen.append((hub_url, path))
        return {"ok": True, "deployment": {"chain_id": 42424241,
                "contract_address": "0x" + "a" * 40,
                "bridge_controller_address": "0x" + "b" * 40}}
    monkeypatch.setattr(captain_cli, "_get_hub_json", get)
    captain_cli._require_hub_bridge_completion_ready(
        "https://testnet-hub.greatlibrary.io", chain_id=42424241,
        contract_address="0x" + "a" * 40,
        controller_address="0x" + "b" * 40,
        timeout_s=10,
    )
    assert seen == [("https://testnet-hub.greatlibrary.io", "/api/hub/v1/credits/wallet-funding/completion")]
