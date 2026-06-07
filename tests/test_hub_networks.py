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
    def test_default_registry_defines_dev_test_and_reserved_main(self) -> None:
        registry = load_hub_network_registry()

        self.assertEqual(registry.default_network, "dev")
        self.assertEqual(set(registry.networks), {"dev", "test", "main"})

        dev = registry.get("dev")
        test = registry.get("test")
        main = registry.get("main")

        self.assertEqual(dev.chain_id, 42424242)
        self.assertEqual(dev.chain_rpc_url, "http://127.0.0.1:18545")
        self.assertEqual(dev.hub_port, 8770)
        self.assertEqual(dev.hub_runtime_dir, Path("runtime/hub/dev"))

        self.assertEqual(test.chain_id, 42424241)
        self.assertEqual(test.chain_rpc_url, "http://127.0.0.1:30010")
        self.assertEqual(test.hub_port, 8780)
        self.assertEqual(test.hub_runtime_dir, Path("runtime/hub/test"))

        self.assertEqual(main.kind, "mainnet")
        self.assertIsNone(main.chain_id)
        self.assertIsNone(main.chain_rpc_url)
        self.assertEqual(main.hub_port, 8790)

        self.assertNotEqual(dev.hub_port, test.hub_port)
        self.assertNotEqual(dev.hub_runtime_dir, test.hub_runtime_dir)
        self.assertNotEqual(dev.chain_rpc_url, test.chain_rpc_url)

    def test_hub_without_network_argument_uses_registry_default_dev_network(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = _config_from_args(_hub_args())

        self.assertEqual(config.hub_network, "dev")
        self.assertEqual(config.hub_network_kind, "dev")
        self.assertEqual(config.hub_bind_host, "127.0.0.1")
        self.assertEqual(config.hub_bind_port, 8770)
        self.assertEqual(config.hub_root, Path("runtime/hub/dev"))
        self.assertEqual(config.chain_rpc_url, "http://127.0.0.1:18545")
        self.assertEqual(config.chain_id, 42424242)
        self.assertEqual(config.energy_chain_rpc_url, config.chain_rpc_url)
        self.assertEqual(config.energy_chain_id, config.chain_id)

    def test_test_network_selects_local_qbft_rpc_node_and_separate_hub_port(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = _config_from_args(_hub_args("--network", "test"))

        self.assertEqual(config.hub_network, "test")
        self.assertEqual(config.hub_network_display_name, "Main Computer Local QBFT Testnet")
        self.assertEqual(config.hub_network_kind, "testnet")
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

    def test_environment_overrides_profile_and_cli_overrides_environment(self) -> None:
        env = {
            "MAIN_COMPUTER_HUB_NETWORK": "test",
            "MAIN_COMPUTER_HUB_HOST": "127.0.0.3",
            "MAIN_COMPUTER_HUB_PORT": "8890",
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
        self.assertEqual(config.hub_root, Path("runtime/hub/from-cli"))
        self.assertEqual(config.chain_rpc_url, "http://127.0.0.1:30012")
        self.assertEqual(config.chain_id, 42424299)

    def test_mainnet_profile_is_present_but_not_runnable_without_deploy_values(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HubNetworkConfigError) as raised:
                _config_from_args(_hub_args("--network", "main"))

        self.assertIn("not runnable", str(raised.exception))
        self.assertIn("chain_id", str(raised.exception))
        self.assertIn("chain_rpc_url", str(raised.exception))

    def test_mainnet_can_be_used_when_required_values_are_overridden(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = _config_from_args(
                _hub_args(
                    "--network",
                    "main",
                    "--chain-rpc-url",
                    "https://rpc.main-computer.example",
                    "--chain-id",
                    "0x1234",
                )
            )

        self.assertEqual(config.hub_network, "main")
        self.assertEqual(config.hub_network_kind, "mainnet")
        self.assertEqual(config.hub_bind_port, 8790)
        self.assertEqual(config.hub_root, Path("runtime/hub/main"))
        self.assertEqual(config.chain_rpc_url, "https://rpc.main-computer.example")
        self.assertEqual(config.chain_id, 0x1234)

    def test_custom_network_registry_file_can_define_alternate_topology(self) -> None:
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
        self.assertEqual(config.hub_bind_port, 18880)
        self.assertEqual(config.hub_root, Path("runtime/hub/remote-test"))
        self.assertEqual(config.hub_network_config_path, registry_path)

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
        self.assertEqual(status["network"]["kind"], "testnet")
        self.assertEqual(status["network"]["chain_id"], 42424241)
        self.assertEqual(status["network"]["chain_id_hex"], "0x28757b1")
        self.assertEqual(status["network"]["chain_rpc_url"], "http://127.0.0.1:30010")
        self.assertEqual(status["network"]["hub_port"], 8780)
        self.assertTrue(status["network"]["hub_runtime_dir"].endswith("hub-test"))


if __name__ == "__main__":
    unittest.main()
