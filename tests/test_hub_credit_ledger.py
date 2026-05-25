from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
from main_computer.hub_credit_ledger import HubCreditLedger
from main_computer.hub_credit_models import ChainEventRef, CreditDeposit


class HubCreditLedgerTests(unittest.TestCase):
    def _start_server(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return thread

    def test_issue_updates_balance_and_persists_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = HubCreditLedger(Path(tmp))
            result = ledger.issue(account_id="User One", credits=125, memo="test credit")

            self.assertTrue(result["ok"])
            self.assertEqual(result["account"]["account_id"], "user-one")
            self.assertEqual(result["account"]["available_credits"], 125)
            self.assertEqual(result["transaction"]["transaction_type"], "admin_adjustment")

            reloaded = HubCreditLedger(Path(tmp))
            account = reloaded.get_account("User One")
            self.assertEqual(account.available_credits, 125)
            self.assertEqual(reloaded.status()["totals"]["available_credits"], 125)
            self.assertEqual(len(reloaded.list_transactions(account_id="user-one")), 1)

    def test_deposit_import_is_idempotent_by_chain_event(self) -> None:
        event = ChainEventRef(
            chain_id=42424242,
            contract_address="0x1111111111111111111111111111111111111111",
            tx_hash="0x2222222222222222222222222222222222222222222222222222222222222222",
            log_index=7,
            block_number=99,
        )
        deposit = CreditDeposit(
            deposit_id="",
            account_id="buyer",
            payer_address="0x3333333333333333333333333333333333333333",
            payment_asset="native",
            payment_amount_base_units=1000,
            credits_granted=25,
            chain_event=event,
            memo="contract purchase",
        )

        with tempfile.TemporaryDirectory() as tmp:
            ledger = HubCreditLedger(Path(tmp))
            first = ledger.record_deposit(deposit)
            second = ledger.record_deposit(deposit)

            self.assertFalse(first["idempotent"])
            self.assertTrue(second["idempotent"])
            self.assertEqual(ledger.get_account("buyer").available_credits, 25)
            self.assertEqual(ledger.status()["purchase_count"], 1)
            self.assertEqual(len(ledger.list_transactions(account_id="buyer")), 1)

    def test_hub_credit_api_exposes_balances_transactions_purchases_and_bootstrap(self) -> None:
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

                with urlopen(f"{hub_base}/api/hub/v1/credits", timeout=5) as response:
                    empty_status = json.loads(response.read().decode("utf-8"))
                self.assertEqual(empty_status["unit"]["key"], "compute_credit")
                self.assertEqual(empty_status["account_count"], 0)

                issue_request = Request(
                    f"{hub_base}/api/hub/v1/credits/admin/issue",
                    data=json.dumps(
                        {
                            "account_id": "User One",
                            "credits": 42,
                            "memo": "manual test issue",
                            "owner_address": "0xABCDEF0000000000000000000000000000000000",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(issue_request, timeout=5) as response:
                    issued = json.loads(response.read().decode("utf-8"))
                self.assertTrue(issued["ok"])
                self.assertEqual(issued["account"]["available_credits"], 42)

                with urlopen(f"{hub_base}/api/hub/v1/credits/balance?account_id=user-one", timeout=5) as response:
                    balance = json.loads(response.read().decode("utf-8"))
                self.assertEqual(balance["account"]["account_id"], "user-one")
                self.assertEqual(balance["account"]["owner_address"], "0xabcdef0000000000000000000000000000000000")
                self.assertEqual(balance["account"]["available_credits"], 42)

                with urlopen(f"{hub_base}/api/hub/v1/credits/accounts", timeout=5) as response:
                    accounts = json.loads(response.read().decode("utf-8"))
                self.assertEqual(accounts["account_count"], 1)

                with urlopen(f"{hub_base}/api/hub/v1/credits/transactions?account_id=user-one", timeout=5) as response:
                    transactions = json.loads(response.read().decode("utf-8"))
                self.assertEqual(transactions["transaction_count"], 1)
                self.assertEqual(transactions["transactions"][0]["transaction_type"], "admin_adjustment")

                with urlopen(f"{hub_base}/api/hub/v1/credits/purchases", timeout=5) as response:
                    purchases = json.loads(response.read().decode("utf-8"))
                self.assertEqual(purchases["purchase_count"], 0)

                with urlopen(f"{hub_base}/api/hub/v1/admin/bootstrap", timeout=5) as response:
                    bootstrap = json.loads(response.read().decode("utf-8"))
                self.assertEqual(bootstrap["credits"]["account_count"], 1)
                self.assertEqual(bootstrap["credits"]["totals"]["available_credits"], 42)
                self.assertEqual(bootstrap["endpoints"]["credit_balance"], "/api/hub/v1/credits/balance")
            finally:
                hub.shutdown()
                thread.join(timeout=5)
                hub.server_close()


if __name__ == "__main__":
    unittest.main()
