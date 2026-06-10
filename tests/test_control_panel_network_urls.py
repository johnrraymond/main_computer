from __future__ import annotations

import unittest
from unittest.mock import patch

from main_computer.hub_networks import load_hub_network_registry
from main_computer.viewport_route_dispatch import _control_panel_network_status_cards


class ControlPanelNetworkUrlTests(unittest.TestCase):
    def test_remote_cards_use_public_hub_urls_not_bind_addresses(self) -> None:
        registry = load_hub_network_registry()
        connect_calls: list[tuple[str, int]] = []

        def fake_connect(host: str, port: int, *, timeout_s: float) -> dict[str, object]:
            connect_calls.append((host, port))
            return {"ok": False, "host": host, "port": port}

        with patch("main_computer.viewport_route_dispatch.load_hub_network_registry", return_value=registry), patch(
            "main_computer.viewport_route_dispatch._control_panel_connect", side_effect=fake_connect
        ), patch("main_computer.viewport_route_dispatch._control_panel_rpc_probe", return_value={"ok": True}):
            payload = _control_panel_network_status_cards()

        testnet = next(network for network in payload["networks"] if network["network_key"] == "testnet")
        mainnet = next(network for network in payload["networks"] if network["network_key"] == "mainnet")

        self.assertEqual(testnet["hub_url"], "https://testnet-hub.greatlibrary.io")
        self.assertEqual(testnet["hub_endpoint"], "https://testnet-hub.greatlibrary.io")
        self.assertIn("https://testnet-hub.greatlibrary.io", testnet["summary"])
        self.assertNotIn("0.0.0.0", testnet["summary"])

        self.assertEqual(mainnet["hub_url"], "https://mainnet-hub.greatlibrary.io")
        self.assertEqual(mainnet["hub_endpoint"], "https://mainnet-hub.greatlibrary.io")
        self.assertIn(("testnet-hub.greatlibrary.io", 443), connect_calls)
        self.assertIn(("mainnet-hub.greatlibrary.io", 443), connect_calls)


if __name__ == "__main__":
    unittest.main()
