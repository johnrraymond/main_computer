from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from main_computer.cli import _config_from_args
from main_computer.config import DEFAULT_ENERGY_CHAIN_ID, DEFAULT_ENERGY_CHAIN_RPC_URL, MainComputerConfig
from main_computer.energy import EnergyCreditLedger
from main_computer.energy_chain import EnergyChainClient
from main_computer.dev_faucet import DevFaucetError, xlag_dev_faucet, xlag_dev_faucet_status
from main_computer.governance import bridge_governance_status
from main_computer.models import ChatMessage, ChatResponse
from main_computer.revision import DebugAssetRevisionControl, RevisionControl
from main_computer.viewport import APPLICATIONS_INDEX_HTML, DEBUG_GRAPHICAL_INDEX_HTML, DEBUG_TEXT_INDEX_HTML, ENERGY_INDEX_HTML, GRAPHICAL_INDEX_HTML, REVISION_INDEX_HTML, TEXT_INDEX_HTML, ViewportHandler, ViewportServer, _application_route_target, serve
from main_computer.viewport_routes_energy import ViewportEnergyRoutesMixin
import main_computer.viewport_routes_energy as viewport_routes_energy
from main_computer.xlag_contract import xlag_contract_status


class _EnergyComponentMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.components: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(attrs)

    def _record(self, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name: value for name, value in attrs if name}
        if str(attr_map.get("data-mc-component-id", "")).startswith("energy."):
            self.components.append(attr_map)


def _write_dev_chain_latest(root: Path, *, host_rpc_url: str = "http://127.0.0.1:18545", chain_id: int = 42424242) -> dict:
    payload = {
        "run_id": "unit-runtime",
        "dry_run": False,
        "chain": {
            "chain_id": chain_id,
            "host_rpc_url": host_rpc_url,
            "container_rpc_url": "http://main-computer-dev-chain-unit-runtime:8545",
        },
        "offices": [
            {
                "office": "O0",
                "title": "Captain",
                "address": "0x1111111111111111111111111111111111111111",
                "private_key": "0x" + "1" * 64,
            },
            {
                "office": "O1",
                "title": "First Officer",
                "address": "0x2222222222222222222222222222222222222222",
                "private_key": "0x" + "2" * 64,
            },
            {
                "office": "O2",
                "title": "Second Officer",
                "address": "0x3333333333333333333333333333333333333333",
                "private_key": "0x" + "3" * 64,
            },
            {
                "office": "O3",
                "title": "Third Officer",
                "address": "0x4444444444444444444444444444444444444444",
                "private_key": "0x" + "4" * 64,
            },
        ],
        "deployments": {
            "alpha-beta-lockout": {
                "target": "AlphaBetaLockout.sol:AlphaBetaLockout",
                "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "transaction_hash": "0x" + "a" * 64,
            },
            "xlag-bridge-reserve": {
                "target": "src/XLagBridgeReserve.sol:XLagBridgeReserve",
                "address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "transaction_hash": "0x" + "b" * 64,
            },
        },
    }
    latest = root / "runtime" / "dev-chain" / "latest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _write_energy_network_latest(root: Path, network: str, *, chain_id: int, offices: list[str] | None = None) -> dict:
    office_addresses = offices or [
        "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
    ]
    payload = {
        "schema": "main-computer.deployment.v1",
        "environment": network,
        "run_id": f"{network}-unit-run",
        "created_at": "2026-06-16T00:00:00Z",
        "source": {"kind": "unit-test"},
        "chain": {
            "chain_id": chain_id,
            "host_rpc_url": f"http://127.0.0.1:{18000 + chain_id % 1000}",
            "rpc_url": f"http://127.0.0.1:{18000 + chain_id % 1000}",
        },
        "offices": [
            {"office": f"O{index}", "title": title, "address": address}
            for index, (title, address) in enumerate(
                zip(["Captain", "First Officer", "Second Officer", "Third Officer"], office_addresses)
            )
        ],
        "contracts": {
            "alpha-beta-lockout": {
                "target": "AlphaBetaLockout.sol:AlphaBetaLockout",
                "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "transaction_hash": "0x" + "a" * 64,
            },
            "xlag-bridge-reserve": {
                "target": "src/XLagBridgeReserve.sol:XLagBridgeReserve",
                "address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "transaction_hash": "0x" + "b" * 64,
            },
            "hub_credit_bridge_escrow": {
                "target": "src/HubCreditBridgeEscrow.sol:HubCreditBridgeEscrow",
                "address": "0xcccccccccccccccccccccccccccccccccccccccc",
                "transaction_hash": "0x" + "c" * 64,
            },
        },
    }
    path = root / "runtime" / "deployments" / network / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


class ViewportEnergyRouteTests(unittest.TestCase):
    def test_energy_index_contains_control_hooks(self) -> None:
        self.assertIn("Main Computer Energy Credits", ENERGY_INDEX_HTML)
        self.assertIn("/api/energy/status", ENERGY_INDEX_HTML)
        self.assertIn("Native Energy Chain", ENERGY_INDEX_HTML)
        self.assertIn("/api/energy/chain/status", ENERGY_INDEX_HTML)
        self.assertIn("energy-chain-connected", ENERGY_INDEX_HTML)
        self.assertIn("energy-chain-block", ENERGY_INDEX_HTML)
        self.assertIn("energy-chain-peers", ENERGY_INDEX_HTML)
        self.assertIn("energy-chain-defaults", ENERGY_INDEX_HTML)
        self.assertIn("energy-chain-rpc-source", ENERGY_INDEX_HTML)
        self.assertIn("energy-chain-id-source", ENERGY_INDEX_HTML)
        self.assertIn("Main Computer Governance", ENERGY_INDEX_HTML)
        self.assertIn("/api/bridge/governance", ENERGY_INDEX_HTML)
        self.assertIn("bridge-governance-status", ENERGY_INDEX_HTML)
        self.assertIn("Bridge Order Flow", ENERGY_INDEX_HTML)
        self.assertIn("bridge-order-flow", ENERGY_INDEX_HTML)
        self.assertIn("bridge-order-belay", ENERGY_INDEX_HTML)
        self.assertIn("bridge-order-helm", ENERGY_INDEX_HTML)
        self.assertIn("X-LAG Contract Reserve", ENERGY_INDEX_HTML)
        self.assertIn("/api/xlag/contract/status", ENERGY_INDEX_HTML)
        self.assertIn("xlag-payout-propose", ENERGY_INDEX_HTML)
        self.assertIn("xlag-payout-second", ENERGY_INDEX_HTML)
        self.assertIn("xlag-payout-belay", ENERGY_INDEX_HTML)
        self.assertIn("xlag-payout-contest", ENERGY_INDEX_HTML)
        self.assertIn("xlag-reset-propose", ENERGY_INDEX_HTML)
        self.assertIn("xlag-reset-approve", ENERGY_INDEX_HTML)
        self.assertIn("xlag-reset-contest", ENERGY_INDEX_HTML)
        self.assertIn("xlag-reset-execute", ENERGY_INDEX_HTML)
        self.assertIn("xlag-wallet-smoke-finalize", ENERGY_INDEX_HTML)
        self.assertIn("finalizeWalletSmokeTest(bytes32,string)", ENERGY_INDEX_HTML)
        self.assertIn("dev-chain-wallet-smoke-guide.py", ENERGY_INDEX_HTML)
        self.assertIn("Browser Wallet Diagnostics", ENERGY_INDEX_HTML)
        self.assertIn("xlag-wallet-diagnostics", ENERGY_INDEX_HTML)
        self.assertIn("[energy:xlag-wallet]", ENERGY_INDEX_HTML)
        self.assertIn("wallet_addEthereumChain", ENERGY_INDEX_HTML)
        self.assertIn("xlagHandleWalletError", ENERGY_INDEX_HTML)
        self.assertIn("xlagPendingActions", ENERGY_INDEX_HTML)
        self.assertIn("xlagRunExclusive", ENERGY_INDEX_HTML)
        self.assertIn("duplicate click ignored", ENERGY_INDEX_HTML)
        self.assertIn("aria-busy", ENERGY_INDEX_HTML)
        self.assertIn("This frob id was already submitted", ENERGY_INDEX_HTML)
        self.assertIn("Any User Frobber", ENERGY_INDEX_HTML)
        self.assertIn("xlag-any-user-frob-submit", ENERGY_INDEX_HTML)
        self.assertIn("frobByAnyUser(bytes32,string)", ENERGY_INDEX_HTML)
        self.assertIn("Dev Wallet Faucet", ENERGY_INDEX_HTML)
        self.assertIn("/api/xlag/dev/faucet", ENERGY_INDEX_HTML)
        self.assertIn("xlag-dev-faucet-fund", ENERGY_INDEX_HTML)
        self.assertIn("xlag-any-user-frob-balance", ENERGY_INDEX_HTML)
        self.assertIn("/api/energy/nodes/register", ENERGY_INDEX_HTML)
        self.assertIn("/api/energy/credits/issue", ENERGY_INDEX_HTML)
        self.assertIn("/api/energy/credits/spend", ENERGY_INDEX_HTML)
        self.assertIn("Hub Configuration", ENERGY_INDEX_HTML)
        self.assertIn("/api/hub/config", ENERGY_INDEX_HTML)
        self.assertIn("hub-provider-state", ENERGY_INDEX_HTML)
        self.assertIn("hub-connect-upstream", ENERGY_INDEX_HTML)



    def test_energy_index_contains_mcel_network_monitor(self) -> None:
        self.assertIn("Mainnet Energy Credits Command Center", ENERGY_INDEX_HTML)
        self.assertIn("/api/energy/networks/status?live=1", ENERGY_INDEX_HTML)
        self.assertIn('data-mc-kind="read-only-command-center"', ENERGY_INDEX_HTML)
        self.assertIn("energy-network-ribbon", ENERGY_INDEX_HTML)
        self.assertIn("Contract Inventory", ENERGY_INDEX_HTML)
        self.assertIn("Captain to Third Officer", ENERGY_INDEX_HTML)

    def test_energy_index_collapses_raw_config_blobs_by_default(self) -> None:
        self.assertIn('data-mc-kind="collapsed-config-blob"', ENERGY_INDEX_HTML)
        self.assertIn("Raw hub configuration response", ENERGY_INDEX_HTML)
        self.assertIn("Raw X-LAG contract status payload", ENERGY_INDEX_HTML)
        self.assertIn("Raw energy chain RPC/config payload", ENERGY_INDEX_HTML)
        self.assertIn("Raw bridge governance policy payload", ENERGY_INDEX_HTML)
        self.assertIn("Raw local ledger balance payload", ENERGY_INDEX_HTML)
        collapsed = re.findall(r'<details[^>]*data-mc-kind="collapsed-config-blob"[^>]*>', ENERGY_INDEX_HTML)
        self.assertEqual(5, len(collapsed))
        self.assertTrue(all(" open" not in tag for tag in collapsed))

    def test_energy_networks_status_api_reports_four_network_monitor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_energy_network_latest(root, "mainnet", chain_id=42424240)
            _write_energy_network_latest(root, "testnet", chain_id=42424241)
            _write_energy_network_latest(root, "test", chain_id=42424241)
            _write_energy_network_latest(root, "dev", chain_id=42424242)
            old_cwd = os.getcwd()
            server = None
            thread = None
            os.chdir(root)
            try:
                server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=root), verbose=False)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()

                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(f"{base}/api/energy/networks/status?live=0", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertTrue(payload["ok"])
                self.assertEqual("read-only-monitor", payload["mode"])
                self.assertEqual("mainnet", payload["default_network"])
                self.assertEqual(["mainnet", "testnet", "test", "dev"], [network["network"] for network in payload["networks"]])
                mainnet = payload["networks"][0]
                self.assertEqual("mainnet", mainnet["network"])
                self.assertEqual("unsafe", mainnet["overall_status"])
                self.assertTrue(mainnet["read_only"])
                self.assertEqual("monitor-only", mainnet["mutation_policy"])
                self.assertEqual(
                    ["alpha-beta-lockout", "xlag-bridge-reserve", "hub_credit_bridge_escrow"],
                    [contract["key"] for contract in mainnet["contracts"]],
                )
                self.assertTrue(any("default Anvil" in warning for warning in mainnet["warnings"]))
            finally:
                if server is not None:
                    server.shutdown()
                    server.server_close()
                if thread is not None:
                    thread.join(timeout=5)
                os.chdir(old_cwd)


    def test_energy_rpc_call_uses_main_computer_json_rpc_headers(self) -> None:
        captured: dict[str, object] = {}

        class _Response:
            def __enter__(self) -> "_Response":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def read(self) -> bytes:
                return b'{"jsonrpc":"2.0","id":1,"result":"0x28757b1"}'

        def fake_urlopen(request: Request, timeout: float = 0.0) -> _Response:
            captured["request"] = request
            captured["timeout"] = timeout
            return _Response()

        with patch.object(viewport_routes_energy, "urlopen", fake_urlopen):
            result = ViewportEnergyRoutesMixin()._energy_rpc_call(
                "https://testnet-rpc.greatlibrary.io",
                "eth_chainId",
                timeout_s=3.5,
            )

        self.assertEqual("0x28757b1", result)
        request = captured["request"]
        self.assertIsInstance(request, Request)
        self.assertEqual("POST", request.get_method())
        self.assertEqual("https://testnet-rpc.greatlibrary.io", request.full_url)
        self.assertEqual("application/json", request.get_header("Content-type"))
        self.assertEqual("application/json", request.get_header("Accept"))
        self.assertEqual("MainComputerEnergy/1.0", request.get_header("User-agent"))
        self.assertEqual(3.5, captured["timeout"])

    def test_energy_components_have_widget_metadata_layer(self) -> None:
        parser = _EnergyComponentMetadataParser()
        parser.feed(ENERGY_INDEX_HTML)
        parser.close()

        self.assertEqual(104, len(parser.components))
        for attrs in parser.components:
            component_id = str(attrs["data-mc-component-id"])
            dom_id = str(attrs.get("id", ""))
            component_kind = attrs.get("data-mc-component-kind")
            component_label = attrs.get("data-mc-component-label")
            component_suffix = dom_id.removeprefix("energy-").replace("-", ".")
            expected_component_id = f"energy.{component_suffix}"
            expected_widget_id = "energy." + component_suffix.replace(".", "-")

            with self.subTest(component_id=component_id):
                self.assertEqual(expected_component_id, component_id)
                self.assertEqual(expected_widget_id, attrs.get("data-mc-widget-id"))
                self.assertEqual(component_kind, attrs.get("data-mc-widget-kind"))
                self.assertEqual(component_kind, attrs.get("data-mc-widget-class"))
                self.assertEqual(component_label, attrs.get("data-mc-widget-label"))
                self.assertTrue(str(attrs.get("data-mc-feature-id", "")).startswith("energy.feature."))

    def test_hub_config_api_saves_runtime_settings_without_switching_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as runtime_dir:
            old_cwd = os.getcwd()
            os.chdir(runtime_dir)
            server = None
            thread = None
            try:
                config = MainComputerConfig(workspace=Path(tmpdir), provider="ollama")
                server = ViewportServer(("127.0.0.1", 0), config, verbose=False)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()

                base = f"http://127.0.0.1:{server.server_port}"
                local_unreachable_hub_url = base
                request = Request(
                    f"{base}/api/hub/config",
                    data=json.dumps(
                        {
                            "provider": "hub",
                            "hub_url": local_unreachable_hub_url,
                            "hub_client_node_id": "Local Browser",
                            "hub_timeout_s": 42,
                            "upstream_hub_url": "http://10.0.0.10:8770",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(payload["provider"], "ollama")
                self.assertEqual(payload["active_provider"], "ollama")
                self.assertEqual(server.config.provider, "ollama")
                self.assertEqual(server.computer.provider.name, "ollama")

                saved = json.loads((Path(runtime_dir) / "hub_configuration.json").read_text(encoding="utf-8"))
                self.assertNotIn("provider", saved)
                self.assertEqual(saved["upstream_hub_url"], "http://10.0.0.10:8770")
            finally:
                if server is not None:
                    server.shutdown()
                    server.server_close()
                if thread is not None:
                    thread.join(timeout=5)
                os.chdir(old_cwd)

    def test_config_uses_default_energy_chain_when_env_is_missing(self) -> None:
        with patch.dict(os.environ, {"MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL": "", "MAIN_COMPUTER_ENERGY_CHAIN_ID": ""}, clear=False):
            config = MainComputerConfig.from_env()
        self.assertEqual(DEFAULT_ENERGY_CHAIN_RPC_URL, "http://127.0.0.1:18545")
        self.assertEqual(config.energy_chain_rpc_url, DEFAULT_ENERGY_CHAIN_RPC_URL)
        self.assertEqual(config.energy_chain_id, DEFAULT_ENERGY_CHAIN_ID)
        self.assertEqual(config.energy_chain_rpc_url_source, "default")
        self.assertEqual(config.energy_chain_id_source, "default")

    def test_config_parses_decimal_energy_chain_id_from_env(self) -> None:
        with patch.dict(os.environ, {"MAIN_COMPUTER_ENERGY_CHAIN_ID": "42424242"}, clear=False):
            config = MainComputerConfig.from_env()
        self.assertEqual(config.energy_chain_id, 42424242)
        self.assertEqual(config.energy_chain_id_source, "env")

    def test_config_parses_hex_energy_chain_id_from_env(self) -> None:
        with patch.dict(os.environ, {"MAIN_COMPUTER_ENERGY_CHAIN_ID": "0x28757b2"}, clear=False):
            config = MainComputerConfig.from_env()
        self.assertEqual(config.energy_chain_id, 42424242)
        self.assertEqual(config.energy_chain_id_source, "env")

    def test_config_invalid_energy_chain_id_falls_back_to_default(self) -> None:
        with patch.dict(os.environ, {"MAIN_COMPUTER_ENERGY_CHAIN_ID": "not-a-chain"}, clear=False):
            config = MainComputerConfig.from_env()
        self.assertEqual(config.energy_chain_id, DEFAULT_ENERGY_CHAIN_ID)
        self.assertEqual(config.energy_chain_id_source, "default-invalid-env")

    def test_bridge_governance_status_models_xlag_safety(self) -> None:
        status = bridge_governance_status()
        self.assertEqual(status["model"], "xlag-byzantine-bridge-governance")
        self.assertEqual(status["compartments"]["Alpha"], [0, 1])
        self.assertEqual(status["compartments"]["Beta"], [2, 3])
        self.assertTrue(status["adversary"]["any_pair_can_collude"])
        self.assertTrue(status["safety"]["any_office_can_contest"])
        self.assertTrue(status["safety"]["no_pair_can_execute"])
        self.assertFalse(status["execution"]["reserve_execution_enabled"])
        self.assertFalse(status["execution"]["native_transfer_enabled"])
        self.assertFalse(status["execution"]["wallet_mapping_enabled"])
        order_flow = status["bridge_order_flow"]
        self.assertFalse(order_flow["captain_touches_computer_required"])
        self.assertFalse(order_flow["first_officer_touches_computer_required"])
        self.assertEqual(order_flow["console_operator_role"], "conn")
        self.assertEqual(order_flow["seconding_station"], "helm")
        self.assertIn("BELAY_WINDOW", order_flow["states"])
        self.assertIn("HELM_SECOND", order_flow["states"])
        self.assertTrue(order_flow["execution"]["serious_outcomes_require_governance"])

    def test_cli_config_preserves_energy_chain_settings(self) -> None:
        args = __import__("argparse").Namespace(
            workspace=None,
            provider=None,
            model=None,
            ollama_base_url=None,
            ollama_timeout_s=None,
            openai_base_url=None,
        )
        with patch.dict(
            os.environ,
            {
                "MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL": "http://127.0.0.1:9545",
                "MAIN_COMPUTER_ENERGY_CHAIN_ID": "0x28757b2",
                "MAIN_COMPUTER_XLAG_CONTRACT_ADDRESS": "0x2222222222222222222222222222222222222222",
                "MAIN_COMPUTER_XLAG_CHAIN_ID": "42424242",
            },
            clear=False,
        ):
            config = _config_from_args(args)
        self.assertEqual(config.energy_chain_rpc_url, "http://127.0.0.1:9545")
        self.assertEqual(config.energy_chain_id, 42424242)
        self.assertEqual(config.energy_chain_rpc_url_source, "env")
        self.assertEqual(config.energy_chain_id_source, "env")
        self.assertEqual(config.xlag_contract_address, "0x2222222222222222222222222222222222222222")
        self.assertEqual(config.xlag_chain_id, 42424242)

    def test_config_preserves_xlag_contract_address(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MAIN_COMPUTER_XLAG_CONTRACT_ADDRESS": "0x1111111111111111111111111111111111111111",
                "MAIN_COMPUTER_XLAG_CHAIN_ID": "0x28757b2",
            },
            clear=False,
        ):
            config = MainComputerConfig.from_env()
        self.assertEqual(config.xlag_contract_address, "0x1111111111111111111111111111111111111111")
        self.assertEqual(config.xlag_contract_address_source, "env")
        self.assertEqual(config.xlag_chain_id, 42424242)
        self.assertEqual(config.xlag_chain_id_source, "env")

    def test_viewport_server_loads_dev_chain_latest_runtime_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            payload = _write_dev_chain_latest(root, host_rpc_url="http://127.0.0.1:18545", chain_id=42424243)
            old_cwd = os.getcwd()
            server = None
            thread = None
            os.chdir(root)
            try:
                config = MainComputerConfig(workspace=root)
                server = ViewportServer(("127.0.0.1", 0), config, verbose=False)
                self.assertEqual(server.config.energy_chain_rpc_url, "http://127.0.0.1:18545")
                self.assertEqual(server.config.energy_chain_rpc_url_source, "runtime-dev-chain")
                self.assertEqual(server.config.energy_chain_id, 42424243)
                self.assertEqual(server.config.energy_chain_id_source, "runtime-dev-chain")
                self.assertEqual(server.config.xlag_chain_id, 42424243)
                self.assertEqual(server.config.xlag_chain_id_source, "runtime-dev-chain")
                self.assertEqual(
                    server.config.xlag_contract_address,
                    payload["deployments"]["xlag-bridge-reserve"]["address"],
                )
                self.assertEqual(server.config.xlag_contract_address_source, "runtime-dev-chain")
                self.assertEqual(
                    server.config.alpha_beta_lockout_contract_address,
                    payload["deployments"]["alpha-beta-lockout"]["address"],
                )
                self.assertEqual(server.config.alpha_beta_lockout_contract_address_source, "runtime-dev-chain")
                self.assertEqual(server.config.dev_chain_run_id, "unit-runtime")
                self.assertEqual(len(server.config.dev_chain_offices), 4)
                self.assertNotIn("private_key", server.config.dev_chain_offices[0])

                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(f"{base}/api/xlag/contract/status", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    status = json.loads(response.read().decode("utf-8"))

                self.assertTrue(status["configured"])
                self.assertEqual(status["contract_address"], payload["deployments"]["xlag-bridge-reserve"]["address"])
                self.assertEqual(status["alpha_beta_lockout_contract_address"], payload["deployments"]["alpha-beta-lockout"]["address"])
                self.assertEqual(status["chain_id"], 42424243)
                self.assertEqual(status["config_source"]["contract_address"], "runtime-dev-chain")
                self.assertEqual(status["config_source"]["chain_id"], "runtime-dev-chain")
                self.assertEqual(status["config_source"]["alpha_beta_lockout_contract_address"], "runtime-dev-chain")
                self.assertEqual(status["dev_chain"]["run_id"], "unit-runtime")
                self.assertEqual(len(status["dev_chain"]["offices"]), 4)
                self.assertNotIn("private_key", status["dev_chain"]["offices"][0])
            finally:
                if server is not None:
                    if thread is not None:
                        server.shutdown()
                    server.server_close()
                if thread is not None:
                    thread.join(timeout=5)
                os.chdir(old_cwd)

    def test_dev_chain_runtime_does_not_override_env_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dev_chain_latest(root, host_rpc_url="http://127.0.0.1:18545", chain_id=42424243)
            old_cwd = os.getcwd()
            server = None
            os.chdir(root)
            try:
                with patch.dict(
                    os.environ,
                    {
                        "MAIN_COMPUTER_WORKSPACE": str(root),
                        "MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL": "http://127.0.0.1:9545",
                        "MAIN_COMPUTER_ENERGY_CHAIN_ID": "0x28757b2",
                        "MAIN_COMPUTER_XLAG_CONTRACT_ADDRESS": "0xcccccccccccccccccccccccccccccccccccccccc",
                        "MAIN_COMPUTER_XLAG_CHAIN_ID": "0x28757b2",
                        "MAIN_COMPUTER_ALPHA_BETA_LOCKOUT_CONTRACT_ADDRESS": "0xdddddddddddddddddddddddddddddddddddddddd",
                    },
                    clear=True,
                ):
                    config = MainComputerConfig.from_env()
                    server = ViewportServer(("127.0.0.1", 0), config, verbose=False)

                self.assertEqual(server.config.energy_chain_rpc_url, "http://127.0.0.1:9545")
                self.assertEqual(server.config.energy_chain_rpc_url_source, "env")
                self.assertEqual(server.config.energy_chain_id, 42424242)
                self.assertEqual(server.config.energy_chain_id_source, "env")
                self.assertEqual(server.config.xlag_contract_address, "0xcccccccccccccccccccccccccccccccccccccccc")
                self.assertEqual(server.config.xlag_contract_address_source, "env")
                self.assertEqual(server.config.xlag_chain_id, 42424242)
                self.assertEqual(server.config.xlag_chain_id_source, "env")
                self.assertEqual(server.config.alpha_beta_lockout_contract_address, "0xdddddddddddddddddddddddddddddddddddddddd")
                self.assertEqual(server.config.alpha_beta_lockout_contract_address_source, "env")
                self.assertEqual(server.config.dev_chain_run_id, "unit-runtime")
                self.assertEqual(server.config.dev_chain_runtime_source, "runtime-dev-chain")
            finally:
                if server is not None:
                    server.server_close()
                os.chdir(old_cwd)

    def test_energy_chain_status_uses_defaults_without_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            server = None
            thread = None
            try:
                config = MainComputerConfig(workspace=Path(tmpdir))
                server = ViewportServer(("127.0.0.1", 0), config, verbose=False)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(f"{base}/api/energy/chain/status", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    chain = json.loads(response.read().decode("utf-8"))

                self.assertTrue(chain["enabled"])
                self.assertEqual(chain["rpc_url"], DEFAULT_ENERGY_CHAIN_RPC_URL)
                self.assertEqual(chain["expected_chain_id"], DEFAULT_ENERGY_CHAIN_ID)
                self.assertTrue(chain["using_defaults"])
                self.assertIn("rpc_url", chain["defaults_used"])
                self.assertIn("expected_chain_id", chain["defaults_used"])
                self.assertEqual(chain["config_source"]["rpc_url"], "default")
                self.assertEqual(chain["config_source"]["expected_chain_id"], "default")
                if chain["connected"]:
                    self.assertEqual(chain["chain_id"], DEFAULT_ENERGY_CHAIN_ID)
                    self.assertTrue(chain["chain_id_ok"])
                else:
                    self.assertIsNone(chain["chain_id"])
                    self.assertIsNone(chain["block_number"])
                    self.assertIsNone(chain["peer_count"])
                    self.assertFalse(chain["chain_id_ok"])
                    self.assertTrue(chain["error"])

                with urlopen(f"{base}/api/energy/status", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    energy = json.loads(response.read().decode("utf-8"))
                self.assertEqual(energy["head"]["node_id"], "main-computer-head")
            finally:
                if server is not None:
                    server.shutdown()
                    server.server_close()
                if thread is not None:
                    thread.join(timeout=5)
                os.chdir(old_cwd)

    def test_energy_chain_status_treats_missing_peer_count_as_optional(self) -> None:
        client = EnergyChainClient("http://unit.test:18545", expected_chain_id=42424242)

        def fake_rpc(method: str) -> str:
            if method == "eth_chainId":
                return "0x28757b2"
            if method == "eth_blockNumber":
                return "0x2a"
            if method == "net_peerCount":
                raise RuntimeError({"code": -32601, "message": "Method not found"})
            raise AssertionError(f"unexpected RPC method: {method}")

        with patch.object(client, "_rpc", side_effect=fake_rpc):
            status = client.status()

        self.assertTrue(status["connected"])
        self.assertEqual(status["chain_id"], 42424242)
        self.assertEqual(status["block_number"], 42)
        self.assertTrue(status["chain_id_ok"])
        self.assertIsNone(status["peer_count"])
        self.assertIn("Method not found", status["peer_count_error"])
        self.assertIsNone(status["error"])

    def test_bridge_governance_api_returns_xlag_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MainComputerConfig(workspace=Path(tmpdir))
            server = ViewportServer(("127.0.0.1", 0), config, verbose=False)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(f"{base}/api/bridge/governance", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    status = json.loads(response.read().decode("utf-8"))

                self.assertEqual(status["model"], "xlag-byzantine-bridge-governance")
                self.assertEqual(status["compartments"]["Alpha"], [0, 1])
                self.assertEqual(status["compartments"]["Beta"], [2, 3])
                self.assertTrue(status["adversary"]["any_pair_can_collude"])
                self.assertTrue(status["safety"]["any_office_can_contest"])
                self.assertTrue(status["safety"]["no_pair_can_execute"])
                self.assertFalse(status["execution"]["reserve_execution_enabled"])
                self.assertFalse(status["execution"]["native_transfer_enabled"])
                self.assertFalse(status["execution"]["wallet_mapping_enabled"])
                order_flow = status["bridge_order_flow"]
                self.assertFalse(order_flow["captain_touches_computer_required"])
                self.assertFalse(order_flow["first_officer_touches_computer_required"])
                self.assertEqual(order_flow["console_operator_role"], "conn")
                self.assertEqual(order_flow["seconding_station"], "helm")
                self.assertIn("BELAY_WINDOW", order_flow["states"])
                self.assertIn("HELM_SECOND", order_flow["states"])
                self.assertTrue(order_flow["execution"]["serious_outcomes_require_governance"])
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()

    def test_xlag_contract_status_without_contract_address(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            server = None
            thread = None
            try:
                config = MainComputerConfig(workspace=Path(tmpdir), xlag_contract_address=None)
                server = ViewportServer(("127.0.0.1", 0), config, verbose=False)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(f"{base}/api/xlag/contract/status", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    status = json.loads(response.read().decode("utf-8"))

                self.assertEqual(status["model"], "xlag-contract-enforced-bridge-reserve-v0")
                self.assertIsNone(status["contract_address"])
                self.assertFalse(status["configured"])
                self.assertEqual(status["chain_id"], 42424242)
                self.assertFalse(status["backend_signing_enabled"])
                self.assertFalse(status["native_transfers_backend_enabled"])
                self.assertEqual(status["enforcement"], "smart-contract")
                self.assertFalse(status["live"]["enabled"])
                self.assertFalse(status["live"]["connected"])
                self.assertFalse(status["live"]["has_code"])
                self.assertEqual(status["live"]["error"], "xlag contract address is not configured")
                self.assertTrue(status["policies"]["payout"]["captain_intent_required"])
                self.assertEqual(status["policies"]["reset"]["approvals_required"], 3)
            finally:
                if server is not None:
                    server.shutdown()
                    server.server_close()
                if thread is not None:
                    thread.join(timeout=5)
                os.chdir(old_cwd)

    def test_xlag_contract_status_includes_live_read_only_contract_values(self) -> None:
        offices = (
            {"office": "O0", "title": "Captain", "address": "0x1111111111111111111111111111111111111111"},
            {"office": "O1", "title": "First Officer", "address": "0x2222222222222222222222222222222222222222"},
            {"office": "O2", "title": "Second Officer", "address": "0x3333333333333333333333333333333333333333"},
            {"office": "O3", "title": "Third Officer", "address": "0x4444444444444444444444444444444444444444"},
        )

        def encode_uint(value: int) -> str:
            return "0x" + hex(value)[2:].rjust(64, "0")

        def encode_address(address: str) -> str:
            return "0x" + address.lower().removeprefix("0x").rjust(64, "0")

        def encode_bytes32(value: str) -> str:
            return "0x" + value.lower().removeprefix("0x").rjust(64, "0")

        def encode_string(value: str) -> str:
            raw = value.encode("utf-8").hex()
            padded = raw.ljust(((len(raw) + 63) // 64) * 64, "0")
            return "0x" + hex(32)[2:].rjust(64, "0") + hex(len(value.encode("utf-8")))[2:].rjust(64, "0") + padded

        class FakeEnergyChainClient:
            def __init__(self, **kwargs):
                self.rpc_url = kwargs["rpc_url"]

            def rpc(self, method, params=None):
                if method == "eth_chainId":
                    return hex(42424242)
                if method == "eth_blockNumber":
                    return hex(123)
                if method == "eth_getCode":
                    return "0x" + "ab" * 12
                if method == "eth_getBalance":
                    return hex(5_250_000_000_000_000_000)
                raise AssertionError(f"unexpected rpc method: {method}")

            def get_code(self, address):
                return self.rpc("eth_getCode", [address, "latest"])

            def get_balance(self, address):
                return int(self.rpc("eth_getBalance", [address, "latest"]), 16)

            def eth_call(self, to, data):
                selector = str(data)[2:10]
                if selector == "05cdb182":
                    return encode_uint(4)
                if selector == "e21a90a6":
                    return encode_uint(1_000_000_000_000_000_000)
                if selector == "5a8d8e42":
                    return encode_uint(1)
                if selector == "68b44c2d":
                    return encode_uint(1)
                if selector == "2ab09d14":
                    return encode_uint(7)
                if selector == "a653a364":
                    index = int(str(data)[10:74], 16)
                    return encode_address(offices[index]["address"])
                if selector == "1f18795c":
                    return encode_uint(1)
                if selector == "50ac700f":
                    raw_address = "0x" + str(data)[-40:].lower()
                    index = [office["address"].lower() for office in offices].index(raw_address)
                    return encode_uint(index + 1)
                if selector == "1a0cbd5d":
                    return encode_uint(2)
                if selector == "b22be79d":
                    return encode_address(offices[0]["address"])
                if selector == "1c13b45c":
                    return encode_uint(0)
                if selector == "3c7cfff2":
                    return encode_bytes32("0x" + "ab" * 32)
                if selector == "b15a70c3":
                    return encode_string("browser wallet smoke")
                if selector == "7fce9e09":
                    return encode_uint(122)
                if selector == "216a323b":
                    return encode_uint(3)
                if selector == "40a5be83":
                    return encode_address("0x5555555555555555555555555555555555555555")
                if selector == "bf7b5956":
                    return encode_bytes32("0x" + "cd" * 32)
                if selector == "6f6491d0":
                    return encode_string("any wallet frob")
                if selector == "6fd8d6db":
                    return encode_uint(124)
                raise AssertionError(f"unexpected eth_call selector: {selector}")

        config = MainComputerConfig(
            workspace=Path.cwd(),
            energy_chain_rpc_url="http://127.0.0.1:18545",
            xlag_contract_address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            alpha_beta_lockout_contract_address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            xlag_chain_id=42424242,
            dev_chain_offices=offices,
        )

        with patch("main_computer.xlag_contract.EnergyChainClient", FakeEnergyChainClient):
            status = xlag_contract_status(config)

        live = status["live"]
        self.assertTrue(status["configured"])
        self.assertTrue(live["enabled"])
        self.assertTrue(live["connected"])
        self.assertTrue(live["chain_id_ok"])
        self.assertTrue(live["has_code"])
        self.assertTrue(live["alpha_beta_lockout_has_code"])
        self.assertEqual(live["chain_id"], 42424242)
        self.assertEqual(live["block_number"], 123)
        self.assertEqual(live["reserve_balance_wei"], "5250000000000000000")
        self.assertEqual(live["reserve_balance_credits"], "5.25")
        self.assertEqual(live["max_payout_wei"], "1000000000000000000")
        self.assertEqual(live["max_payout_credits"], "1")
        self.assertEqual(live["payout_delay_blocks"], 1)
        self.assertEqual(live["reset_delay_blocks"], 1)
        self.assertEqual(live["next_proposal_id"], 7)
        self.assertEqual(live["office_count"], 4)
        self.assertTrue(live["wallet_smoke"]["available"])
        self.assertEqual(live["wallet_smoke"]["function"], "finalizeWalletSmokeTest(bytes32,string)")
        self.assertEqual(live["wallet_smoke"]["selector"], "0xaa46bd04")
        self.assertEqual(live["wallet_smoke"]["nonce"], 2)
        self.assertEqual(live["wallet_smoke"]["last_finalizer"], offices[0]["address"].lower())
        self.assertEqual(live["wallet_smoke"]["last_office"], 0)
        self.assertEqual(live["wallet_smoke"]["last_smoke_id"], "0x" + "ab" * 32)
        self.assertEqual(live["wallet_smoke"]["last_memo"], "browser wallet smoke")
        self.assertEqual(live["wallet_smoke"]["last_block"], 122)
        self.assertTrue(live["any_user_frobber"]["available"])
        self.assertEqual(live["any_user_frobber"]["function"], "frobByAnyUser(bytes32,string)")
        self.assertEqual(live["any_user_frobber"]["selector"], "0xbcd0a3bf")
        self.assertEqual(live["any_user_frobber"]["requires"], "any connected browser wallet on the configured dev chain")
        self.assertEqual(live["any_user_frobber"]["nonce"], 3)
        self.assertEqual(live["any_user_frobber"]["last_frobber"], "0x5555555555555555555555555555555555555555")
        self.assertEqual(live["any_user_frobber"]["last_frob_id"], "0x" + "cd" * 32)
        self.assertEqual(live["any_user_frobber"]["last_frob_memo"], "any wallet frob")
        self.assertEqual(live["any_user_frobber"]["last_block"], 124)
        self.assertEqual(len(live["offices"]), 4)
        self.assertEqual(live["offices"][2]["office"], "O2")
        self.assertEqual(live["offices"][2]["office_index_plus_one"], 3)
        self.assertTrue(live["offices"][2]["is_office"])
        self.assertTrue(live["offices"][2]["matches_expected"])
        self.assertIsNone(live["error"])

    def test_xlag_dev_faucet_sends_native_dev_value_from_local_faucet_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runtime_path = root / "runtime" / "deployments" / "dev" / "latest.json"
            runtime_path.parent.mkdir(parents=True)
            runtime_path.write_text(json.dumps({"environment": "dev"}), encoding="utf-8")

            class FakeChain:
                def __init__(self) -> None:
                    self.sent: dict | None = None

                def rpc(self, method: str, params=None):
                    if method == "eth_chainId":
                        return hex(42424242)
                    if method == "eth_sendTransaction":
                        self.sent = params[0]
                        return "0x" + "f" * 64
                    raise AssertionError(f"unexpected rpc method: {method}")

                def get_balance(self, address: str) -> int:
                    return 0 if address.endswith("7777") else 10_000 * 10**18

            chain = FakeChain()
            config = MainComputerConfig(
                workspace=root,
                energy_chain_rpc_url="http://127.0.0.1:18547",
                xlag_chain_id=42424242,
                dev_chain_runtime_source="deployment-runtime",
                dev_chain_runtime_path=runtime_path,
                dev_chain_offices=(
                    {"office": "O0", "title": "Captain", "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"},
                ),
            )

            result = xlag_dev_faucet(
                config,
                chain,
                {"address": "0x7777777777777777777777777777777777777777", "amount_credits": "0.25"},
                remote_addr="127.0.0.1",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "local-dev-faucet")
            self.assertEqual(result["amount_wei"], "250000000000000000")
            self.assertEqual(result["amount_credits"], "0.25")
            self.assertEqual(result["to"], "0x7777777777777777777777777777777777777777")
            self.assertEqual(result["from"], "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266")
            self.assertEqual(chain.sent["from"], result["from"])
            self.assertEqual(chain.sent["to"], result["to"])
            self.assertEqual(chain.sent["value"], hex(250_000_000_000_000_000))
            self.assertNotIn("private", json.dumps(result).lower())

    def test_xlag_dev_faucet_status_reports_runtime_chain_and_account_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runtime_path = root / "runtime" / "deployments" / "dev" / "latest.json"
            runtime_path.parent.mkdir(parents=True)
            runtime_path.write_text(json.dumps({"environment": "dev"}), encoding="utf-8")

            class FakeChain:
                def rpc(self, method: str, params=None):
                    if method == "eth_chainId":
                        return hex(42424242)
                    raise AssertionError(f"unexpected rpc method: {method}")

            config = MainComputerConfig(
                workspace=root,
                energy_chain_rpc_url="http://127.0.0.1:18547",
                xlag_chain_id=42424242,
                dev_chain_runtime_source="deployment-runtime",
                dev_chain_runtime_path=runtime_path,
                dev_chain_offices=(
                    {"office": "O0", "title": "Captain", "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"},
                ),
            )

            result = xlag_dev_faucet_status(config, FakeChain())

            self.assertTrue(result["ok"])
            self.assertTrue(result["ready"])
            self.assertEqual(result["endpoint"], "/api/xlag/dev/faucet")
            self.assertEqual(result["method"], "POST")
            self.assertEqual(result["runtime_source"], "deployment-runtime")
            self.assertTrue(result["has_faucet_account"])
            self.assertEqual(result["faucet_from"], "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266")
            self.assertEqual(result["chain_id"], 42424242)
            self.assertEqual(result["expected_chain_id_hex"], "0x28757b2")

    def test_xlag_dev_faucet_refuses_non_loopback_rpc(self) -> None:
        config = MainComputerConfig(
            workspace=Path.cwd(),
            energy_chain_rpc_url="https://rpc.example.invalid",
            xlag_chain_id=42424242,
            dev_chain_runtime_source="deployment-runtime",
            dev_chain_offices=(
                {"office": "O0", "title": "Captain", "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"},
            ),
        )

        with self.assertRaises(DevFaucetError) as ctx:
            xlag_dev_faucet(config, object(), {"address": "0x7777777777777777777777777777777777777777"}, remote_addr="127.0.0.1")

        self.assertEqual(ctx.exception.status, 403)
        self.assertIn("non-loopback", str(ctx.exception))

    def test_xlag_dev_faucet_api_funds_connected_wallet_on_local_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            current = root / "runtime" / "deployments" / "dev" / "latest.json"
            current.parent.mkdir(parents=True)
            current.write_text(
                json.dumps(
                    {
                        "schema": "main-computer.deployment.v1",
                        "environment": "dev",
                        "run_id": "faucet-unit",
                        "chain": {
                            "chain_id": 42424242,
                            "rpc_url": "http://127.0.0.1:18547",
                            "host_rpc_url": "http://127.0.0.1:18547",
                        },
                        "contracts": {
                            "xlag-bridge-reserve": {"address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
                            "alpha-beta-lockout": {"address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
                        },
                        "offices": [
                            {"office": "O0", "title": "Captain", "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            class FakeChain:
                def __init__(self) -> None:
                    self.sent: dict | None = None

                def rpc(self, method: str, params=None):
                    if method == "eth_chainId":
                        return hex(42424242)
                    if method == "eth_sendTransaction":
                        self.sent = params[0]
                        return "0x" + "e" * 64
                    raise AssertionError(f"unexpected rpc method: {method}")

                def get_balance(self, address: str) -> int:
                    return 0

            old_cwd = os.getcwd()
            server = None
            thread = None
            os.chdir(root)
            try:
                server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=root), verbose=False)
                fake_chain = FakeChain()
                server.energy_chain = fake_chain
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()

                status_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/xlag/dev/faucet",
                    headers={"Accept": "application/json"},
                    method="GET",
                )
                with urlopen(status_request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    status_payload = json.loads(response.read().decode("utf-8"))

                self.assertTrue(status_payload["ready"])
                self.assertEqual(status_payload["endpoint"], "/api/xlag/dev/faucet")
                self.assertEqual(status_payload["runtime_source"], "deployment-runtime")
                self.assertEqual(status_payload["chain_id"], 42424242)

                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/xlag/dev/faucet",
                    data=json.dumps(
                        {
                            "address": "0x7777777777777777777777777777777777777777",
                            "amount_credits": "1",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertTrue(payload["ok"])
                self.assertEqual(payload["runtime_source"], "deployment-runtime")
                self.assertEqual(payload["tx_hash"], "0x" + "e" * 64)
                self.assertEqual(fake_chain.sent["to"], "0x7777777777777777777777777777777777777777")
            finally:
                if server is not None:
                    server.shutdown()
                    server.server_close()
                if thread is not None:
                    thread.join(timeout=5)
                os.chdir(old_cwd)

    def test_worker_wallet_balance_api_reports_connected_wallet_balance_from_local_rpc(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wallet = "0x7777777777777777777777777777777777777777"

            class FakeChain:
                def rpc(self, method: str, params=None):
                    if method == "eth_chainId":
                        return hex(42424242)
                    raise AssertionError(f"unexpected rpc method: {method}")

                def get_balance(self, address: str) -> int:
                    self.address = address
                    return 2 * 10**18 + 123

            server = None
            thread = None
            try:
                server = ViewportServer(
                    ("127.0.0.1", 0),
                    MainComputerConfig(workspace=root, energy_chain_rpc_url="http://127.0.0.1:18547", xlag_chain_id=42424242),
                    verbose=False,
                )
                fake_chain = FakeChain()
                server.energy_chain = fake_chain
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()

                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/applications/worker/wallet-balance",
                    data=json.dumps({"wallet_address": wallet}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertTrue(payload["ok"])
                self.assertEqual(payload["wallet_address"], wallet)
                self.assertEqual(payload["chain_id"], 42424242)
                self.assertEqual(payload["chain_id_hex"], "0x28757b2")
                self.assertEqual(payload["balance_base_units"], str(2 * 10**18 + 123))
                self.assertEqual(payload["available_credits"], "2.000000000000000123")
                self.assertEqual(payload["source"], "local-rpc")
                self.assertEqual(fake_chain.address, wallet)
            finally:
                if server is not None:
                    server.shutdown()
                    server.server_close()
                if thread is not None:
                    thread.join(timeout=5)

    def test_local_energy_ledger_registers_nodes_and_tracks_credits(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        try:
            ledger = EnergyCreditLedger(Path(tempdir.name))
            status = ledger.status()
            self.assertEqual(status["head"]["node_id"], "main-computer-head")

            status = ledger.register_node("meter one", "meter", "local://meter-one")
            self.assertEqual(status["nodes"][0]["node_id"], "meter-one")

            status = ledger.issue("meter-one", 25, "startup")
            self.assertEqual(status["balances"]["meter-one"], 25)

            status = ledger.spend("meter-one", 5, "work")
            self.assertEqual(status["balances"]["meter-one"], 20)
            self.assertTrue(status["transactions"][0]["tx_id"].startswith("0x"))
        finally:
            tempdir.cleanup()

    def test_ollama_debug_chat_receives_workspace_context(self) -> None:
        config = MainComputerConfig(workspace=Path.cwd().parent)
        server = ViewportServer(("127.0.0.1", 0), config)
        captured = {}

        class FakeOllamaProvider:
            def __init__(self, **kwargs):
                self.model = kwargs["model"]

            def chat(self, messages):
                captured["messages"] = messages
                return ChatResponse(content="debug ok", provider="fake", model=self.model)

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            enable = Request(
                f"{base}/api/ollama-debug/session",
                data=json.dumps({"action": "enable"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(enable, timeout=5):
                pass

            debug_chat = Request(
                f"{base}/api/ollama-debug/chat",
                data=json.dumps({"prompt": "Can you see main_computer?"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with patch("main_computer.viewport.OllamaProvider", FakeOllamaProvider):
                with urlopen(debug_chat, timeout=30) as response:
                    data = json.loads(response.read().decode("utf-8"))

            self.assertEqual(data["content"], "debug ok")
            context = "\n".join(message.content for message in captured["messages"] if message.role == "system")
            self.assertIn("Current deterministic workspace context available to debug mode", context)
            self.assertIn("main_computer", context)
            self.assertIn("main_computer_test", context)
            self.assertIn("main_copmputer_production", context)
            self.assertIn("Main computer file manifest:", context)
            self.assertIn("TODO.md", context)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
