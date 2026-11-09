"""P3 contract: Dev-Test Hub cannot silently use production networks or routes."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from main_computer.dev_test_hub import (
    DevTestHubConfigurationError,
    LOCAL_CHAIN_IDS,
    main,
    validate_dev_test_config,
    validate_dev_test_hub,
    validate_dev_test_route,
)

ROOT = Path(__file__).resolve().parents[1]


def _cli():
    # The uploaded repository snapshot omits game_projects; CLI imports the
    # Viewport which renders that unrelated game bundle at import time.
    from main_computer import game_web_loader
    with patch.object(game_web_loader, "read_game_bundle", return_value=""):
        import main_computer.cli as cli
    return cli


def valid(**overrides):
    params = dict(
        network="dev", network_kind="dev", chain_id="42424242",
        chain_rpc_url="http://127.0.0.1:18545",
        hub_urls=["http://127.0.0.1:8871"], bind_host="127.0.0.1",
    )
    params.update(overrides)
    return validate_dev_test_hub(**params)


def test_dev_and_local_test_allowed():
    assert valid()["role"] == "dev-test-hub"
    assert valid(network="test", network_kind="test", chain_id=42424241,
                 chain_rpc_url="http://127.0.0.1:30010",
                 hub_urls=["http://127.0.0.1:8780"])["network"] == "test"


@pytest.mark.parametrize("changes", [
    {"network": "mainnet", "network_kind": "mainnet", "chain_id": 42424240},
    {"network": "testnet", "network_kind": "testnet", "chain_id": 42424241},
    {"network_kind": "mainnet"},
    {"requested_network": "mainnet"},
    {"chain_id": 42424240},
    {"chain_id": 42424241},
    {"hub_urls": ["https://mainnet-hub.greatlibrary.io"]},
    {"hub_urls": ["http://mainnet-hub.greatlibrary.io:8790"]},
    {"hub_urls": ["http://0.0.0.0:8871"]},
    {"hub_urls": ["http://127.0.0.1:8871", "http://evil.example:8872"]},
    {"hub_urls": ["http://operator:secret@127.0.0.1:8871"]},
    {"chain_rpc_url": "https://mainnet-rpc.greatlibrary.io"},
    {"chain_rpc_url": "https://testnet-rpc.greatlibrary.io"},
    {"chain_rpc_url": "http://10.0.0.1:18545"},
    {"bind_host": "0.0.0.0"},
    {"bind_host": "mainnet-hub.greatlibrary.io"},
    {"hub_urls": []},
])
def test_public_or_ambiguous_dev_configuration_rejected(changes):
    with pytest.raises(DevTestHubConfigurationError):
        valid(**changes)


def test_real_dev_topology_accepted():
    path = ROOT / "deploy/hub-topology/dev-topology.json"
    info = json.loads(path.read_text())
    actual = validate_dev_test_hub(
        network=info["network"]["network_key"],
        network_kind=info["network"]["network_kind"],
        chain_id=info["network"]["chain_id"],
        chain_rpc_url=info["network"]["chain_rpc_url"],
        bind_host="127.0.0.1", hub_urls=info["entry_urls"],
        topology=path,
    )
    assert len(actual["hub_urls"]) == 3


def test_topology_mismatch_rejected_before_start(tmp_path):
    source = json.loads((ROOT / "deploy/hub-topology/dev-topology.json").read_text())
    topology = tmp_path / "dev.json"
    source["network"]["network_key"] = "mainnet"
    topology.write_text(json.dumps(source))
    with pytest.raises(DevTestHubConfigurationError):
        valid(hub_urls=["http://127.0.0.1:8871", "http://127.0.0.1:8872", "http://127.0.0.1:8873"], topology=topology)


def test_topology_unexpected_external_hub_rejected(tmp_path):
    source = json.loads((ROOT / "deploy/hub-topology/dev-topology.json").read_text())
    source["hubs"][0]["public_url"] = "https://mainnet-hub.greatlibrary.io"
    topology = tmp_path / "dev.json"
    topology.write_text(json.dumps(source))
    with pytest.raises(DevTestHubConfigurationError):
        valid(hub_urls=source["entry_urls"], topology=topology)


def test_topology_chain_endpoint_mismatch_rejected(tmp_path):
    source = json.loads((ROOT / "deploy/hub-topology/dev-topology.json").read_text())
    source["network"]["chain_rpc_url"] = "http://127.0.0.1:30010"
    topology = tmp_path / "dev.json"
    topology.write_text(json.dumps(source))
    with pytest.raises(DevTestHubConfigurationError):
        valid(hub_urls=source["entry_urls"], topology=topology)


def test_preflight_does_not_start_services(capsys):
    assert main(["preflight", "--network", "dev", "--network-kind", "dev",
                 "--chain-id", "42424242", "--chain-rpc-url", "http://127.0.0.1:18545",
                 "--host", "127.0.0.1", "--hub-url", "http://127.0.0.1:8871"]) == 0
    assert "DEV_TEST_HUB_PREFLIGHT_PASS" in capsys.readouterr().out


def test_preflight_refuses_mainnet(capsys):
    with pytest.raises(SystemExit) as info:
        main(["preflight", "--network", "mainnet", "--network-kind", "mainnet",
              "--chain-id", "42424240", "--chain-rpc-url", "https://mainnet-rpc.greatlibrary.io",
              "--host", "0.0.0.0", "--hub-url", "https://mainnet-hub.greatlibrary.io"])
    assert info.value.code == 2
    assert "REFUSED" in capsys.readouterr().err


def test_cli_dev_test_hub_default_is_dev_even_if_registry_defaults_mainnet():
    cli = _cli()
    args = cli.build_parser().parse_args(["dev-test-hub"])
    assert args.func is cli.cmd_dev_test_hub
    with patch.dict(os.environ, {"MAIN_COMPUTER_WORKSPACE": str(ROOT)}, clear=True), patch("main_computer.cli.serve_hub") as mock_serve:
        assert cli.cmd_dev_test_hub(args) == 0
    assert mock_serve.call_args.args[0].hub_network == "dev"
    assert mock_serve.call_args.args[0].hub_url == "http://127.0.0.1:8871"


def test_cli_dev_test_hub_and_legacy_hub_refuse_mainnet_before_start():
    cli = _cli()
    with patch.dict(os.environ, {"MAIN_COMPUTER_WORKSPACE": str(ROOT)}, clear=True), patch("main_computer.cli.serve_hub") as mock_serve:
        for argv in (["dev-test-hub", "--network", "mainnet"],
                     ["dev-test-hub", "--network", "testnet"],
                     ["hub", "--network", "mainnet"],
                     ["hub", "--network", "testnet"],
                     ["hub"]):
            args = cli.build_parser().parse_args(argv)
            with pytest.raises(DevTestHubConfigurationError):
                args.func(args)
    mock_serve.assert_not_called()


def test_cli_dev_test_hub_refuses_env_mainnet_override():
    cli = _cli()
    with patch.dict(os.environ, {"MAIN_COMPUTER_HUB_NETWORK": "mainnet"}, clear=True), patch("main_computer.cli.serve_hub") as mock_serve:
        args = cli.build_parser().parse_args(["dev-test-hub"])
        with pytest.raises(DevTestHubConfigurationError):
            args.func(args)
        mock_serve.assert_not_called()


def test_cli_dev_test_hub_rejects_public_rpc_and_bind_override():
    cli = _cli()
    with patch.dict(os.environ, {"MAIN_COMPUTER_WORKSPACE": str(ROOT)}, clear=True), patch("main_computer.cli.serve_hub") as mock_serve:
        for argv in (["dev-test-hub", "--chain-rpc-url", "https://mainnet-rpc.greatlibrary.io"],
                     ["dev-test-hub", "--host", "0.0.0.0"],
                     ["dev-test-hub", "--chain-id", "42424240"]):
            args = cli.build_parser().parse_args(argv)
            with pytest.raises(DevTestHubConfigurationError):
                args.func(args)
        mock_serve.assert_not_called()


def test_startup_preflight_happens_before_old_hub_stop():
    script = (ROOT / "scripts/main-computer-start-stop.ps1").read_text(encoding="utf-8-sig")
    start = script[script.index("function Start-MainComputerDevHubFresh"):script.index("function Start-MainComputerDevHubOnly")]
    assert start.index("-m\", \"main_computer.dev_test_hub\"") < start.index("Stop-MainComputerDevHubForRestart")
    assert '"dev-test-hub"' in start
    assert '"--network-kind", [string]$endpoint.network' in start
    assert '"--topology", [string]$preflightTopologyPath' in start
    assert "if ($LASTEXITCODE -ne 0)" in start
    dev_only = start[:start.index("function Start-MainComputer(")]
    assert "$NoSLHHubRequested" not in dev_only


def test_supervisor_reports_dev_test_actual_implementation():
    script = (ROOT / "scripts/main-computer-start-stop.ps1").read_text(encoding="utf-8-sig")
    assert 'display_name = "Dev-Test Hub"' in script
    assert '"exp-fdb-hub.py --network-key "' in script
    assert '"main_computer.cli dev-test-hub --network "' in script
    assert 'implementation = $hubKind' in script
    assert 'role = "dev-test-hub"' in script
    assert LOCAL_CHAIN_IDS == {"dev": 42424242, "test": 42424241}


@pytest.mark.parametrize("endpoint", [
    "http://127.0.0.1:8871", "http://localhost:8871",
    "http://host.docker.internal:8871", "http://hub:8871",
    "http://dev-test-hub:8871",
])
def test_local_dev_application_route_allowed(endpoint):
    assert validate_dev_test_route(endpoint) == endpoint


@pytest.mark.parametrize("endpoint", [
    "https://mainnet-hub.greatlibrary.io",
    "https://testnet-hub.greatlibrary.io",
    "http://mainnet-hub.greatlibrary.io:8790",
    "http://10.1.1.5:8871",
    "http://evil-host:8871",
    "http://127.0.0.1:8871/api/hub/chat",
    "http://user:secret@127.0.0.1:8871",
])
def test_local_dev_application_route_refuses_public_or_ambiguous_endpoint(endpoint):
    with pytest.raises(DevTestHubConfigurationError):
        validate_dev_test_route(endpoint)


def test_main_computer_router_local_hub_protection_while_preserving_stable_hub_paths():
    cli = _cli()
    from main_computer.config import MainComputerConfig
    from main_computer.router import MainComputer
    from pathlib import Path
    with patch("main_computer.router.HubProvider") as mock_provider:
        config = MainComputerConfig(workspace=ROOT, provider="hub", hub_network="dev", hub_url="https://mainnet-hub.greatlibrary.io")
        with pytest.raises(DevTestHubConfigurationError):
            MainComputer.build(config)
        mock_provider.assert_not_called()
        config = MainComputerConfig(workspace=ROOT, provider="hub", hub_network="dev", hub_url="http://127.0.0.1:8871")
        MainComputer.build(config)
        mock_provider.assert_called_once()
        with pytest.raises(DevTestHubConfigurationError):
            MainComputer.build(MainComputerConfig(workspace=ROOT, provider="hub", hub_network="mainnet", hub_url="http://127.0.0.1:8871"))


def test_remote_overflow_old_dev_test_path_refuses_public_endpoints():
    _cli()
    from main_computer.config import MainComputerConfig
    from main_computer.remote_overflow import RemoteHubExecutionGateway
    with patch("main_computer.remote_overflow.HubProvider") as provider:
        gw = RemoteHubExecutionGateway(config=MainComputerConfig(workspace=ROOT, hub_network="dev"), provider_factory=provider)
        with pytest.raises(DevTestHubConfigurationError):
            gw._provider_for_request({"hub_url": "https://mainnet-hub.greatlibrary.io"})
        provider.assert_not_called()
        gw._provider_for_request({"hub_url": "http://host.docker.internal:8871"})
        provider.assert_called_once()
        with pytest.raises(DevTestHubConfigurationError):
            RemoteHubExecutionGateway(config=MainComputerConfig(workspace=ROOT, hub_network="mainnet"), provider_factory=provider)._provider_for_request({"hub_url": "http://127.0.0.1:8871"})
