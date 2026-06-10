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
        self.assertEqual(test.hub_runtime_dir, Path("runtime/hub/test"))

        self.assertEqual(testnet.display_name, "Main Computer Testnet")
        self.assertEqual(testnet.kind, "testnet")
        self.assertEqual(testnet.chain_id, 42424241)
        self.assertEqual(testnet.chain_rpc_url, "http://testnet-rpc.greatlibrary.io:30010")
        self.assertEqual(testnet.hub_bind_host, "0.0.0.0")
        self.assertEqual(testnet.hub_bind_port, 8785)
        self.assertEqual(testnet.hub_public_url, "https://testnet.greatlibrary.io")
        self.assertEqual(testnet.hub_runtime_dir, Path("runtime/hub/testnet"))

        self.assertEqual(mainnet.kind, "mainnet")
        self.assertEqual(mainnet.chain_id, 42424240)
        self.assertEqual(mainnet.chain_rpc_url, "http://mainnet-rpc.greatlibrary.io:31010")
        self.assertEqual(mainnet.hub_bind_host, "0.0.0.0")
        self.assertEqual(mainnet.hub_bind_port, 8790)
        self.assertEqual(mainnet.hub_public_url, "https://mainnet.greatlibrary.io")
        self.assertEqual(mainnet.hub_runtime_dir, Path("runtime/hub/mainnet"))

        self.assertEqual(mainnet.hub_host, mainnet.hub_bind_host)
        self.assertEqual(mainnet.hub_port, mainnet.hub_bind_port)
        self.assertEqual(mainnet.hub_url, "https://mainnet.greatlibrary.io")

    def test_hub_without_network_argument_uses_registry_default_mainnet_network(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = _config_from_args(_hub_args())

        self.assertEqual(config.hub_network, "mainnet")
        self.assertEqual(config.hub_network_kind, "mainnet")
        self.assertEqual(config.hub_bind_host, "0.0.0.0")
        self.assertEqual(config.hub_bind_port, 8790)
        self.assertEqual(config.hub_url, "https://mainnet.greatlibrary.io")
        self.assertEqual(config.hub_root, Path("runtime/hub/mainnet"))
        self.assertEqual(config.chain_rpc_url, "http://mainnet-rpc.greatlibrary.io:31010")
        self.assertEqual(config.chain_id, 42424240)
        self.assertEqual(config.energy_chain_rpc_url, config.chain_rpc_url)
        self.assertEqual(config.energy_chain_id, config.chain_id)

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
        self.assertEqual(config.chain_rpc_url_source, "hub-network:test")
        self.assertEqual(config.chain_id_source, "hub-network:test")
        self.assertEqual(config.energy_chain_rpc_url, "http://127.0.0.1:30010")
        self.assertEqual(config.energy_chain_id, 42424241)

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
        self.assertEqual(config.hub_url, "https://testnet.greatlibrary.io")
        self.assertEqual(config.hub_root, Path("runtime/hub/testnet"))
        self.assertEqual(config.chain_rpc_url, "http://testnet-rpc.greatlibrary.io:30010")
        self.assertEqual(config.chain_id, 42424241)
        self.assertEqual(config.chain_rpc_url_source, "hub-network:testnet")
        self.assertEqual(config.chain_id_source, "hub-network:testnet")

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
                )
            )

        self.assertEqual(config.hub_network, "test")
        self.assertEqual(config.hub_bind_host, "127.0.0.2")
        self.assertEqual(config.hub_bind_port, 8888)
        self.assertEqual(config.hub_url, "http://127.0.0.2:8888")
        self.assertEqual(config.hub_root, Path("runtime/hub/test-alt"))
        self.assertEqual(config.chain_rpc_url, "http://127.0.0.1:39999")
        self.assertEqual(config.chain_id, 42)
        self.assertEqual(config.energy_chain_rpc_url, "http://127.0.0.1:39999")
        self.assertEqual(config.energy_chain_id, 42)

    def test_hub_url_override_sets_public_url_without_changing_bind_address(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = _config_from_args(
                _hub_args(
                    "--network",
                    "testnet",
                    "--host",
                    "0.0.0.0",
                    "--hub-url",
                    "https://hub.example.test",
                )
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

    def test_mainnet_profile_is_runnable_from_committed_topology(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = _config_from_args(_hub_args("--network", "mainnet"))

        self.assertEqual(config.hub_network, "mainnet")
        self.assertEqual(config.hub_network_kind, "mainnet")
        self.assertEqual(config.hub_bind_host, "0.0.0.0")
        self.assertEqual(config.hub_bind_port, 8790)
        self.assertEqual(config.hub_url, "https://mainnet.greatlibrary.io")
        self.assertEqual(config.hub_root, Path("runtime/hub/mainnet"))
        self.assertEqual(config.chain_rpc_url, "http://mainnet-rpc.greatlibrary.io:31010")
        self.assertEqual(config.chain_id, 42424240)

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
                                "contracts": {"HubCreditBridgeEscrow": "0x0000000000000000000000000000000000000001"},
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
        self.assertEqual(config.hub_network_config_path, registry_path)

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
                with self.assertRaises(HubNetworkConfigError) as raised:
                    _config_from_args(_hub_args("--network-config", str(registry_path)))

        self.assertIn("not runnable", str(raised.exception))
        self.assertIn("chain_id", str(raised.exception))
        self.assertIn("chain_rpc_url", str(raised.exception))

    def test_hub_status_reports_selected_network_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with patch.dict(os.environ, {}, clear=True):
                config = _config_from_args(
                    _hub_args(
                        "--network",
                        "test",
                        "--hub-runtime-dir",
                        str(Path(tempdir) / "hub-test"),
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
        self.assertEqual(status["network"]["chain_id_hex"], "0x28757b1")
        self.assertEqual(status["network"]["chain_rpc_url"], "http://127.0.0.1:30010")
        self.assertEqual(status["network"]["hub_public_url"], "http://127.0.0.1:8780")
        self.assertEqual(status["network"]["hub_bind_port"], 8780)
        self.assertEqual(status["network"]["hub_port"], 8780)
        self.assertTrue(status["network"]["hub_runtime_dir"].endswith("hub-test"))


if __name__ == "__main__":
    unittest.main()
