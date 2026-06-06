from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
from main_computer.hub_credit_indexer import HubCreditIndexer, wallet_account_id
from main_computer.hub_credit_ledger import HubCreditLedger


def normalized_deposit_payload(**overrides):
    payload = {
        "chain_id": 42424242,
        "contract_address": "0x1111111111111111111111111111111111111111",
        "tx_hash": "0x2222222222222222222222222222222222222222222222222222222222222222",
        "log_index": 0,
        "block_number": 123,
        "account_id": "User One",
        "payer_address": "0x3333333333333333333333333333333333333333",
        "payment_asset": "native",
        "payment_amount_base_units": 1_000_000_000_000_000_000,
        "credits_granted_wei": "100000000000000000000",
        "memo": "dev-chain funding receipt",
    }
    payload.update(overrides)
    return payload


class HubCreditIndexerTests(unittest.TestCase):
    def _start_server(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return thread

    def test_manual_import_credits_account_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = HubCreditLedger(Path(tmp))
            indexer = HubCreditIndexer(ledger)

            result = indexer.import_deposit(normalized_deposit_payload())

            self.assertTrue(result["ok"])
            self.assertFalse(result["idempotent"])
            self.assertEqual(result["deposit"]["account_id"], "user-one")
            self.assertEqual(result["deposit"]["credits_granted_wei"], "100000000000000000000")
            self.assertEqual(result["account"]["available_credits"], 100)
            self.assertEqual(result["transaction"]["transaction_type"], "deposit_indexed")
            self.assertEqual(ledger.status()["totals"]["deposited_credits"], 100)

    def test_same_event_imported_twice_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = HubCreditLedger(Path(tmp))
            indexer = HubCreditIndexer(ledger)
            payload = normalized_deposit_payload()

            first = indexer.import_deposit(payload)
            second = indexer.import_deposit(payload)

            self.assertFalse(first["idempotent"])
            self.assertTrue(second["idempotent"])
            self.assertEqual(first["deposit"]["deposit_id"], second["deposit"]["deposit_id"])
            self.assertEqual(second["transaction"]["transaction_type"], "deposit_indexed")
            self.assertEqual(ledger.get_account("user-one").available_credits, 100)
            self.assertEqual(ledger.status()["deposit_count"], 1)
            self.assertEqual(len(ledger.list_transactions(account_id="user-one")), 1)

    def test_wallet_funding_import_uses_wallet_address_as_account_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wallet = "0xABCDEFabcdefABCDEFabcdefABCDEFabcdefABCD"
            normalized_wallet = wallet.lower()
            ledger = HubCreditLedger(Path(tmp))
            indexer = HubCreditIndexer(ledger)

            payload = normalized_deposit_payload(
                account_id="browser-invented-account-must-not-win",
                wallet_address=wallet,
                payer_address=wallet,
                tx_hash="0x4444444444444444444444444444444444444444444444444444444444444444",
                credits_granted_wei="250000000000000000000",
            )

            result = indexer.import_wallet_funding(payload)

            self.assertTrue(result["ok"])
            self.assertEqual(wallet_account_id(wallet), normalized_wallet)
            self.assertEqual(result["wallet_address"], normalized_wallet)
            self.assertEqual(result["account_id"], normalized_wallet)
            self.assertEqual(result["deposit"]["account_id"], normalized_wallet)
            self.assertEqual(result["account"]["available_credits"], 250)
            self.assertEqual(ledger.get_account(normalized_wallet).available_credits, 250)
            self.assertEqual(ledger.get_account("browser-invented-account-must-not-win").available_credits, 0)

    def test_malformed_event_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            indexer = HubCreditIndexer(HubCreditLedger(Path(tmp)))

            bad_hash = normalized_deposit_payload(tx_hash="0x1234")
            with self.assertRaisesRegex(ValueError, "tx_hash"):
                indexer.import_deposit(bad_hash)

            bad_credits = normalized_deposit_payload(credits_granted_wei=0)
            with self.assertRaisesRegex(ValueError, "credits_granted_wei"):
                indexer.import_deposit(bad_credits)

            missing_account = normalized_deposit_payload(account_id="")
            with self.assertRaisesRegex(ValueError, "account_id"):
                indexer.import_deposit(missing_account)

    def test_hub_api_import_exposes_deposits_transactions_and_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as hub_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="fake-model",
                hub_root=Path(hub_tmp) / "hub-runtime",
            )
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            thread = self._start_server(hub)
            try:
                hub_base = f"http://127.0.0.1:{hub.server_port}"

                with urlopen(f"{hub_base}/api/hub/v1/credits/indexer", timeout=5) as response:
                    indexer_status = json.loads(response.read().decode("utf-8"))
                self.assertTrue(indexer_status["ok"])
                self.assertEqual(indexer_status["phase"], "R2A")
                self.assertFalse(indexer_status["credit_card_supported"])

                import_request = Request(
                    f"{hub_base}/api/hub/v1/credits/deposits/import",
                    data=json.dumps(normalized_deposit_payload()).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(import_request, timeout=5) as response:
                    imported = json.loads(response.read().decode("utf-8"))
                self.assertTrue(imported["ok"])
                self.assertFalse(imported["idempotent"])
                self.assertEqual(imported["account"]["available_credits"], 100)

                duplicate_request = Request(
                    f"{hub_base}/api/hub/v1/credits/deposits/import",
                    data=json.dumps(normalized_deposit_payload()).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(duplicate_request, timeout=5) as response:
                    duplicate = json.loads(response.read().decode("utf-8"))
                self.assertTrue(duplicate["idempotent"])
                self.assertEqual(duplicate["account"]["available_credits"], 100)

                with urlopen(f"{hub_base}/api/hub/v1/credits/deposits?account_id=user-one", timeout=5) as response:
                    deposits = json.loads(response.read().decode("utf-8"))
                self.assertEqual(deposits["deposit_count"], 1)
                self.assertEqual(deposits["deposits"][0]["credits_granted_wei"], "100000000000000000000")

                with urlopen(f"{hub_base}/api/hub/v1/credits/transactions?account_id=user-one", timeout=5) as response:
                    transactions = json.loads(response.read().decode("utf-8"))
                self.assertEqual(transactions["transaction_count"], 1)
                self.assertEqual(transactions["transactions"][0]["transaction_type"], "deposit_indexed")

                with urlopen(f"{hub_base}/api/hub/v1/admin/bootstrap", timeout=5) as response:
                    bootstrap = json.loads(response.read().decode("utf-8"))
                self.assertEqual(bootstrap["credits"]["totals"]["deposited_credits"], 100)
                self.assertEqual(bootstrap["credit_indexer"]["phase"], "R2A")
            finally:
                hub.shutdown()
                hub.server_close()
                thread.join(timeout=2)


    def test_hub_api_imports_wallet_funding_and_reports_wallet_balance(self) -> None:
        with tempfile.TemporaryDirectory() as hub_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="fake-model",
                hub_root=Path(hub_tmp) / "hub-runtime",
            )
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            thread = self._start_server(hub)
            try:
                hub_base = f"http://127.0.0.1:{hub.server_port}"
                wallet = "0x5555555555555555555555555555555555555555"

                import_request = Request(
                    f"{hub_base}/api/hub/v1/credits/wallet-funding/import",
                    data=json.dumps(
                        normalized_deposit_payload(
                            account_id="frontend-label-ignored",
                            wallet_address=wallet,
                            payer_address=wallet,
                            tx_hash="0x6666666666666666666666666666666666666666666666666666666666666666",
                            credits_granted_wei="777000000000000000000",
                        )
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(import_request, timeout=5) as response:
                    imported = json.loads(response.read().decode("utf-8"))

                self.assertTrue(imported["ok"])
                self.assertFalse(imported["idempotent"])
                self.assertEqual(imported["wallet_address"], wallet)
                self.assertEqual(imported["account_id"], wallet)
                self.assertEqual(imported["account"]["available_credits"], 777)

                with urlopen(f"{hub_base}/api/hub/v1/credits/balance?wallet_address={wallet}", timeout=5) as response:
                    balance = json.loads(response.read().decode("utf-8"))
                self.assertTrue(balance["ok"])
                self.assertEqual(balance["wallet_address"], wallet)
                self.assertEqual(balance["account_id"], wallet)
                self.assertEqual(balance["account"]["available_credits"], 777)
                self.assertEqual(balance["funding_model"], "hub_credit_bridge_escrow_wallet_v1")
            finally:
                hub.shutdown()
                hub.server_close()
                thread.join(timeout=2)

    def test_hub_api_rejects_malformed_import(self) -> None:
        with tempfile.TemporaryDirectory() as hub_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="fake-model",
                hub_root=Path(hub_tmp) / "hub-runtime",
            )
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            thread = self._start_server(hub)
            try:
                hub_base = f"http://127.0.0.1:{hub.server_port}"
                bad_request = Request(
                    f"{hub_base}/api/hub/v1/credits/deposits/import",
                    data=json.dumps(normalized_deposit_payload(credits_granted_wei=0)).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(bad_request, timeout=5)
                self.assertEqual(raised.exception.code, 400)
                body = json.loads(raised.exception.read().decode("utf-8"))
                self.assertIn("credits_granted_wei", body["error"])
            finally:
                hub.shutdown()
                hub.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
