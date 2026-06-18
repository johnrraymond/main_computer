from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from main_computer.cli import _config_from_args, build_parser
from main_computer.hub import HubHttpServer
from main_computer.hub_networks import HubNetworkConfigError, load_hub_network_registry


def _hub_args(*items: str):
    return build_parser().parse_args(["hub", *items])


class HubNetworkRegistryTests(unittest.TestCase):
    def test_default_registry_defines_full_v2_topology(self) -> None:
        registry = load_hub_network_registry()

        self.assertEqual(registry.version, 2)
        self.assertEqual(registry.default_network, "mainnet")
        self.assertEqual(set(registry.networks), {"dev", "test", "testnet", "mainnet"})

        dev = registry.get("dev")
        test = registry.get("test")
        testnet = registry.get("testnet")
        mainnet = registry.get("mainnet")

        self.assertEqual(dev.display_name, "Main Computer Local Dev")
        self.assertEqual(dev.kind, "dev")
        self.assertEqual(dev.chain_id, 42424242)
        self.assertEqual(dev.chain_rpc_url, "http://127.0.0.1:18545")
        self.assertEqual(dev.hub_bind_host, "127.0.0.1")
        self.assertEqual(dev.hub_bind_port, 8770)
        self.assertEqual(dev.hub_public_url, "http://127.0.0.1:8770")
        self.assertEqual(dev.hub_runtime_dir, Path("runtime/hub/dev"))
        self.assertEqual(dev.deployment_manifest_path, Path("runtime/deployments/dev/latest.json"))

        self.assertEqual(test.display_name, "Main Computer Local QBFT Test")
        self.assertEqual(test.kind, "test")
        self.assertEqual(test.chain_id, 42424241)
        self.assertEqual(test.chain_rpc_url, "http://127.0.0.1:30010")
        self.assertEqual(test.hub_bind_host, "127.0.0.1")
        self.assertEqual(test.hub_bind_port, 8780)
        self.assertEqual(test.hub_public_url, "http://127.0.0.1:8780")

        self.assertEqual(testnet.kind, "testnet")
        self.assertEqual(testnet.chain_id, 42424241)
        self.assertEqual(testnet.chain_rpc_url, "https://testnet-rpc.greatlibrary.io")
        self.assertEqual(testnet.hub_bind_host, "0.0.0.0")
        self.assertEqual(testnet.hub_bind_port, 8785)
        self.assertEqual(testnet.hub_public_url, "https://testnet-hub.greatlibrary.io")

        self.assertEqual(mainnet.kind, "mainnet")
        self.assertEqual(mainnet.chain_id, 42424240)
        self.assertEqual(mainnet.chain_rpc_url, "https://mainnet-rpc.greatlibrary.io")
        self.assertEqual(mainnet.hub_bind_host, "0.0.0.0")
        self.assertEqual(mainnet.hub_bind_port, 8790)
        self.assertEqual(mainnet.hub_public_url, "https://mainnet-hub.greatlibrary.io")
        self.assertEqual(mainnet.hub_host, mainnet.hub_bind_host)
        self.assertEqual(mainnet.hub_port, mainnet.hub_bind_port)
        self.assertEqual(mainnet.hub_url, "https://mainnet-hub.greatlibrary.io")

    def test_hub_without_network_argument_uses_registry_default_mainnet_network(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = _config_from_args(_hub_args())

        self.assertEqual(config.hub_network, "mainnet")
        self.assertEqual(config.hub_network_kind, "mainnet")
        self.assertEqual(config.hub_bind_host, "0.0.0.0")
        self.assertEqual(config.hub_bind_port, 8790)
        self.assertEqual(config.hub_url, "https://mainnet-hub.greatlibrary.io")
        self.assertEqual(config.hub_root, Path("runtime/hub/mainnet"))
        self.assertEqual(config.chain_rpc_url, "https://mainnet-rpc.greatlibrary.io")
        self.assertEqual(config.chain_id, 42424240)
        self.assertEqual(config.hub_bridge_backend, "dev-chain")
        self.assertEqual(config.hub_dev_chain_deployment_path, Path("runtime/deployments/mainnet/latest.json"))

    def test_test_network_selects_local_qbft_rpc_node_and_separate_hub_port(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = _config_from_args(_hub_args("--network", "test"))

        self.assertEqual(config.hub_network, "test")
        self.assertEqual(config.hub_network_display_name, "Main Computer Local QBFT Test")
        self.assertEqual(config.hub_network_kind, "test")
        self.assertEqual(config.hub_bind_host, "127.0.0.1")
        self.assertEqual(config.hub_bind_port, 8780)
        self.assertEqual(config.hub_url, "http://127.0.0.1:8780")
        self.assertEqual(config.hub_root, Path("runtime/hub/test"))
        self.assertEqual(config.chain_rpc_url, "http://127.0.0.1:30010")
        self.assertEqual(config.chain_id, 42424241)
        self.assertEqual(config.hub_bridge_backend, "dev-chain")
        self.assertEqual(config.hub_dev_chain_deployment_path, Path("runtime/deployments/test/latest.json"))

    def test_testnet_network_uses_committed_remote_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            old_cwd = os.getcwd()
            try:
                os.chdir(tempdir)
                with patch.dict(os.environ, {}, clear=True):
                    config = _config_from_args(_hub_args("--network", "testnet"))
            finally:
                os.chdir(old_cwd)

        self.assertEqual(config.hub_network, "testnet")
        self.assertEqual(config.hub_network_display_name, "Main Computer Testnet")
        self.assertEqual(config.hub_network_kind, "testnet")
        self.assertEqual(config.hub_bind_host, "0.0.0.0")
        self.assertEqual(config.hub_bind_port, 8785)
        self.assertEqual(config.hub_url, "https://testnet-hub.greatlibrary.io")
        self.assertEqual(config.chain_rpc_url, "https://testnet-rpc.greatlibrary.io")
        self.assertEqual(config.chain_id, 42424241)

    def test_command_line_overrides_every_runtime_network_field(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = _config_from_args(
                _hub_args(
                    "--network",
                    "test",
                    "--host",
                    "127.0.0.2",
                    "--port",
                    "8888",
                    "--hub-runtime-dir",
                    "runtime/hub/test-alt",
                    "--chain-rpc-url",
                    "http://127.0.0.1:39999",
                    "--chain-id",
                    "0x2a",
                    "--bridge-backend",
                    "mock-chain",
                    "--dev-chain-deployment-path",
                    "runtime/deployments/custom/latest.json",
                )
            )

        self.assertEqual(config.hub_network, "test")
        self.assertEqual(config.hub_bind_host, "127.0.0.2")
        self.assertEqual(config.hub_bind_port, 8888)
        self.assertEqual(config.hub_url, "http://127.0.0.2:8888")
        self.assertEqual(config.hub_root, Path("runtime/hub/test-alt"))
        self.assertEqual(config.chain_rpc_url, "http://127.0.0.1:39999")
        self.assertEqual(config.chain_id, 42)
        self.assertEqual(config.hub_bridge_backend, "mock-chain")
        self.assertEqual(config.hub_dev_chain_deployment_path, Path("runtime/deployments/custom/latest.json"))

    def test_hub_url_override_sets_public_url_without_changing_bind_address(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = _config_from_args(
                _hub_args("--network", "testnet", "--host", "0.0.0.0", "--hub-url", "https://hub.example.test")
            )

        self.assertEqual(config.hub_bind_host, "0.0.0.0")
        self.assertEqual(config.hub_bind_port, 8785)
        self.assertEqual(config.hub_url, "https://hub.example.test")

    def test_environment_overrides_profile_and_cli_overrides_environment(self) -> None:
        env = {
            "MAIN_COMPUTER_HUB_NETWORK": "test",
            "MAIN_COMPUTER_HUB_BIND_HOST": "127.0.0.3",
            "MAIN_COMPUTER_HUB_BIND_PORT": "8890",
            "MAIN_COMPUTER_HUB_RUNTIME_DIR": "runtime/hub/from-env",
            "MAIN_COMPUTER_CHAIN_RPC_URL": "http://127.0.0.1:30011",
            "MAIN_COMPUTER_CHAIN_ID": "0x28757b1",
        }
        with patch.dict(os.environ, env, clear=True):
            config = _config_from_args(
                _hub_args(
                    "--host",
                    "127.0.0.4",
                    "--port",
                    "8891",
                    "--hub-runtime-dir",
                    "runtime/hub/from-cli",
                    "--chain-rpc-url",
                    "http://127.0.0.1:30012",
                    "--chain-id",
                    "42424299",
                )
            )

        self.assertEqual(config.hub_network, "test")
        self.assertEqual(config.hub_bind_host, "127.0.0.4")
        self.assertEqual(config.hub_bind_port, 8891)
        self.assertEqual(config.hub_url, "http://127.0.0.4:8891")
        self.assertEqual(config.hub_root, Path("runtime/hub/from-cli"))
        self.assertEqual(config.chain_rpc_url, "http://127.0.0.1:30012")
        self.assertEqual(config.chain_id, 42424299)

    def test_custom_network_registry_file_can_define_alternate_v1_topology(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            registry_path = Path(tempdir) / "hub_networks.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "default_network": "test",
                        "networks": {
                            "test": {
                                "network_key": "test",
                                "display_name": "Remote Testnet",
                                "kind": "testnet",
                                "chain_id": 9001,
                                "chain_rpc_url": "https://testnet-rpc.example",
                                "hub_host": "127.0.0.1",
                                "hub_port": 18880,
                                "hub_runtime_dir": "runtime/hub/remote-test",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                config = _config_from_args(_hub_args("--network-config", str(registry_path)))

        self.assertEqual(config.hub_network, "test")
        self.assertEqual(config.hub_network_display_name, "Remote Testnet")
        self.assertEqual(config.chain_rpc_url, "https://testnet-rpc.example")
        self.assertEqual(config.chain_id, 9001)
        self.assertEqual(config.hub_bind_host, "127.0.0.1")
        self.assertEqual(config.hub_bind_port, 18880)
        self.assertEqual(config.hub_url, "http://127.0.0.1:18880")
        self.assertEqual(config.hub_root, Path("runtime/hub/remote-test"))

    def test_missing_runnable_fields_still_raise_for_custom_dynamic_network(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            registry_path = Path(tempdir) / "hub_networks.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "default_network": "dynamic",
                        "networks": {
                            "dynamic": {
                                "display_name": "Dynamic",
                                "kind": "testnet",
                                "chain_id": None,
                                "chain_rpc_url": None,
                                "hub_bind_host": "0.0.0.0",
                                "hub_bind_port": 19000,
                                "hub_public_url": "https://dynamic.example",
                                "hub_runtime_dir": "runtime/hub/dynamic",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(HubNetworkConfigError):
                    _config_from_args(_hub_args("--network-config", str(registry_path)))

    def test_hub_status_reports_selected_network_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with patch.dict(os.environ, {}, clear=True):
                config = _config_from_args(
                    _hub_args(
                        "--network",
                        "test",
                        "--hub-runtime-dir",
                        str(Path(tempdir) / "hub-test"),
                        "--bridge-backend",
                        "mock-chain",
                    )
                )

            server = HubHttpServer(("127.0.0.1", 0), config, verbose=False)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                with urlopen(f"{base_url}/api/hub/status", timeout=5) as response:
                    status = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertEqual(status["network"]["network_key"], "test")
        self.assertEqual(status["network"]["kind"], "test")
        self.assertEqual(status["network"]["chain_id"], 42424241)
        self.assertEqual(status["network"]["hub_public_url"], "http://127.0.0.1:8780")
        self.assertEqual(status["network"]["hub_bind_port"], 8780)
        self.assertEqual(status["network"]["hub_port"], 8780)
        self.assertTrue(status["network"]["hub_runtime_dir"].endswith("hub-test"))

    def test_registry_loads_public_contract_config_next_to_network_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "main_computer" / "config"
            config_dir.mkdir(parents=True)
            networks = {
                "version": 2,
                "default_network": "testnet",
                "networks": {
                    "testnet": {
                        "display_name": "Unit Testnet",
                        "kind": "testnet",
                        "chain_id": 42424241,
                        "chain_rpc_url": "https://rpc.example.test",
                        "hub_bind_host": "0.0.0.0",
                        "hub_bind_port": 8785,
                        "hub_public_url": "https://hub.example.test",
                        "hub_runtime_dir": "runtime/hub/testnet",
                    }
                },
            }
            (config_dir / "hub_networks.json").write_text(json.dumps(networks), encoding="utf-8")
            (config_dir / "testnet_contracts.json").write_text(
                json.dumps(
                    {
                        "hub_credit_bridge_escrow": "0x3333333333333333333333333333333333333333",
                    }
                ),
                encoding="utf-8",
            )

            registry = load_hub_network_registry(config_dir / "hub_networks.json")

            self.assertEqual(
                registry.get("testnet").contracts["hub_credit_bridge_escrow"],
                "0x3333333333333333333333333333333333333333",
            )

if __name__ == "__main__":
    unittest.main()
