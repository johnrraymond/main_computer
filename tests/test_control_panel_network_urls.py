from __future__ import annotations

import unittest
from unittest.mock import patch

from main_computer.hub_networks import load_hub_network_registry
from main_computer.viewport_route_dispatch import _control_panel_energy_credits_service, _control_panel_network_status_cards


class ControlPanelEnergyCreditsServiceTests(unittest.TestCase):
    def _service_for(self, *networks: dict[str, object]) -> dict[str, object]:
        return _control_panel_energy_credits_service({"ok": True, "networks": list(networks)})

    def _network(self, key: str, reachable: bool, *, severity: str | None = None) -> dict[str, object]:
        if severity is None:
            severity = "green" if reachable else "red"
        return {
            "network_key": key,
            "hub_reachable": reachable,
            "state": "healthy" if reachable else "down",
            "severity": severity,
            "status_text": "reachable" if reachable else "unreachable",
        }

    def test_energy_credits_main_card_is_green_only_when_mainnet_reachable(self) -> None:
        service = self._service_for(
            self._network("mainnet", True),
            self._network("testnet", True),
        )

        self.assertEqual(service["state"], "healthy")
        self.assertEqual(service["severity"], "green")
        self.assertIn("mainnet is reachable", service["summary"])

    def test_energy_credits_main_card_is_yellow_when_only_testnet_reachable(self) -> None:
        service = self._service_for(
            self._network("mainnet", False),
            self._network("testnet", True),
        )

        self.assertEqual(service["state"], "degraded")
        self.assertEqual(service["severity"], "yellow")
        self.assertIn("mainnet is unreachable", service["summary"])
        self.assertIn("testnet", service["summary"])

    def test_energy_credits_main_card_is_yellow_when_only_local_network_reachable(self) -> None:
        service = self._service_for(
            self._network("mainnet", False),
            self._network("testnet", False, severity="gray"),
            self._network("test", True),
            self._network("dev", False, severity="gray"),
        )

        self.assertEqual(service["state"], "degraded")
        self.assertEqual(service["severity"], "yellow")
        self.assertIn("test", service["summary"])

    def test_energy_credits_main_card_is_red_when_nothing_reachable(self) -> None:
        service = self._service_for(
            self._network("mainnet", False),
            self._network("testnet", False, severity="yellow"),
            self._network("test", False, severity="gray"),
            self._network("dev", False, severity="gray"),
        )

        self.assertEqual(service["state"], "down")
        self.assertEqual(service["severity"], "red")
        self.assertEqual(service["summary"], "No Energy Credits networks are reachable")


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

    def test_remote_contract_status_uses_deployment_manifests_as_ground_truth(self) -> None:
        registry = load_hub_network_registry()

        with patch("main_computer.viewport_route_dispatch.load_hub_network_registry", return_value=registry), patch(
            "main_computer.viewport_route_dispatch._control_panel_connect", return_value={"ok": False}
        ), patch("main_computer.viewport_route_dispatch._control_panel_rpc_probe", return_value={"ok": True}):
            payload = _control_panel_network_status_cards()

        mainnet = next(network for network in payload["networks"] if network["network_key"] == "mainnet")
        testnet = next(network for network in payload["networks"] if network["network_key"] == "testnet")

        self.assertEqual(mainnet["contracts_status"], "known")
        self.assertEqual(mainnet["contracts_count"], 3)
        self.assertEqual(mainnet["contracts_source"], "deployment-manifest")
        self.assertTrue(str(mainnet["contracts_manifest_path"]).endswith("runtime/deployments/mainnet/latest.json"))
        self.assertEqual(
            mainnet["contracts"]["hub_credit_bridge_escrow"],
            "0xDc64a140Aa3E981100a9becA4E685f962f0cF6C9",
        )
        self.assertEqual(mainnet["contracts_manifest"]["environment"], "mainnet")

        self.assertEqual(testnet["contracts_status"], "known")
        self.assertEqual(testnet["contracts_count"], 3)
        self.assertEqual(testnet["contracts_source"], "deployment-manifest")
        self.assertTrue(str(testnet["contracts_manifest_path"]).endswith("runtime/deployments/testnet/latest.json"))
        self.assertEqual(
            testnet["contracts"]["hub_credit_bridge_escrow"],
            "0xDc64a140Aa3E981100a9becA4E685f962f0cF6C9",
        )
        self.assertEqual(testnet["contracts_manifest"]["environment"], "testnet")



if __name__ == "__main__":
    unittest.main()
