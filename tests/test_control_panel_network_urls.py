from __future__ import annotations

import unittest
from pathlib import Path
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

    def test_energy_credits_main_card_is_red_when_reachable_network_authority_is_unsafe(self) -> None:
        service = self._service_for(
            {
                **self._network("mainnet", True),
                "state": "unsafe",
                "severity": "red",
                "status_text": "reachable but unsafe",
                "authority_status": "unsafe",
            },
            self._network("testnet", True),
        )

        self.assertEqual(service["state"], "unsafe")
        self.assertEqual(service["severity"], "red")
        self.assertIn("authority is unsafe", service["summary"])
        self.assertIn("mainnet", service["summary"])


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


    def test_reachable_non_mainnet_cards_are_capped_at_yellow(self) -> None:
        registry = load_hub_network_registry()

        def fake_status(url: str, *, timeout_s: float = 1.0) -> dict[str, object]:
            network_key = "unknown"
            if "testnet-hub" in url:
                network_key = "testnet"
            elif "127.0.0.1:8780" in url:
                network_key = "test"
            elif "127.0.0.1:8770" in url:
                network_key = "dev"
            elif "mainnet-hub" in url:
                network_key = "mainnet"
            profile = registry.networks.get(network_key)
            chain_id = profile.chain_id if profile is not None else None
            return {"ok": True, "data": {"network": {"network_key": network_key, "chain_id": chain_id}}}

        with patch("main_computer.viewport_route_dispatch.load_hub_network_registry", return_value=registry), patch(
            "main_computer.viewport_route_dispatch._control_panel_connect", return_value={"ok": True}
        ), patch("main_computer.viewport_route_dispatch._control_panel_http_json", side_effect=fake_status), patch(
            "main_computer.viewport_route_dispatch._control_panel_rpc_probe", return_value={"ok": True}
        ), patch(
            "main_computer.viewport_route_dispatch._control_panel_deployment_contracts",
            return_value={"ok": False, "contract_addresses": {}, "count": 0, "source": "deployment-manifest", "path": "", "error": "", "candidates": []},
        ):
            payload = _control_panel_network_status_cards(Path.cwd())

        cards = {network["network_key"]: network for network in payload["networks"]}
        self.assertEqual(cards["mainnet"]["state"], "healthy")
        self.assertEqual(cards["mainnet"]["severity"], "green")
        for key in ("testnet", "test", "dev"):
            self.assertEqual(cards[key]["state"], "degraded")
            self.assertEqual(cards[key]["severity"], "yellow")
            self.assertEqual(cards[key]["status_text"], "reachable")

        service = _control_panel_energy_credits_service(payload)
        badges = {badge["key"]: badge for badge in service["network_badges"]}
        self.assertEqual(badges["mainnet"]["severity"], "green")
        for key in ("testnet", "test", "dev"):
            self.assertEqual(badges[key]["severity"], "yellow")


    def test_remote_cards_use_deployment_manifests_for_contract_truth(self) -> None:
        registry = load_hub_network_registry()

        with patch("main_computer.viewport_route_dispatch.load_hub_network_registry", return_value=registry), patch(
            "main_computer.viewport_route_dispatch._control_panel_connect", return_value={"ok": False}
        ), patch("main_computer.viewport_route_dispatch._control_panel_rpc_probe", return_value={"ok": True}):
            payload = _control_panel_network_status_cards(Path.cwd())

        testnet = next(network for network in payload["networks"] if network["network_key"] == "testnet")
        mainnet = next(network for network in payload["networks"] if network["network_key"] == "mainnet")

        for network in (testnet, mainnet):
            self.assertEqual(network["contracts_status"], "known")
            self.assertEqual(network["contracts_source"], "deployment-manifest")
            self.assertEqual(network["contracts_count"], 3)
            self.assertEqual(network["contracts_manifest_error"], "")
            self.assertTrue(str(network["contracts_manifest_path"]).endswith(f"runtime/deployments/{network['network_key']}/latest.json"))
            self.assertEqual(network["authority_status"], "unsafe")
            self.assertEqual(network["state"], "unsafe")
            self.assertEqual(network["severity"], "red")
            self.assertIn("default Anvil", network["authority_warning"])
            self.assertIn("default Anvil", network["summary"])
            self.assertEqual(
                set(network["contracts"]),
                {"alpha-beta-lockout", "hub_credit_bridge_escrow", "xlag-bridge-reserve"},
            )


if __name__ == "__main__":
    unittest.main()
