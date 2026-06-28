from __future__ import annotations

import concurrent.futures
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
            self.assertEqual(result["account"]["available_credit_wei"], "125000000000000000000")
            self.assertEqual(result["account"]["available_credits_display"], "125")
            self.assertEqual(result["transaction"]["transaction_type"], "admin_adjustment")
            self.assertEqual(result["transaction"]["credit_wei"], "125000000000000000000")

            reloaded = HubCreditLedger(Path(tmp))
            account = reloaded.get_account("User One")
            self.assertEqual(account.available_credits, 125)
            self.assertEqual(account.available_credit_wei, 125000000000000000000)
            self.assertEqual(reloaded.status()["totals"]["available_credits"], 125)
            self.assertEqual(reloaded.status()["totals"]["available_credit_wei"], "125000000000000000000")
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
            memo="contract escrow deposit",
        )

        with tempfile.TemporaryDirectory() as tmp:
            ledger = HubCreditLedger(Path(tmp))
            first = ledger.record_deposit(deposit)
            second = ledger.record_deposit(deposit)

            self.assertFalse(first["idempotent"])
            self.assertTrue(second["idempotent"])
            self.assertEqual(ledger.get_account("buyer").available_credits, 25)
            self.assertEqual(ledger.get_account("buyer").available_credit_wei, 25000000000000000000)
            self.assertEqual(ledger.status()["deposit_count"], 1)
            self.assertEqual(len(ledger.list_transactions(account_id="buyer")), 1)

    def test_concurrent_same_account_direct_spends_are_atomic_and_do_not_overspend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = HubCreditLedger(Path(tmp))
            ledger.issue(account_id="buyer", credits=10, memo="fund buyer")

            def try_spend(index: int) -> tuple[str, dict[str, object] | str]:
                try:
                    result = ledger.spend_request_credit(
                        account_id="buyer",
                        request_id=f"req-concurrent-{index}",
                        credits=1,
                        worker_node_id="worker-1",
                        memo="same wallet concurrent request spend",
                    )
                    return "ok", result
                except ValueError as exc:
                    return "insufficient", str(exc)

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(try_spend, range(25)))

            charged = [payload for status, payload in results if status == "ok"]
            rejected = [payload for status, payload in results if status == "insufficient"]

            self.assertEqual(len(charged), 10)
            self.assertEqual(len(rejected), 15)
            self.assertTrue(all("Insufficient Compute Credits" in str(item) for item in rejected))

            account = ledger.get_account("buyer")
            self.assertEqual(account.available_credit_wei, 0)
            self.assertEqual(account.held_credit_wei, 0)
            self.assertEqual(account.spent_credit_wei, 10 * 10**18)

            status = ledger.status()
            self.assertEqual(status["totals"]["available_credit_wei"], "0")
            self.assertEqual(status["totals"]["held_credit_wei"], "0")
            self.assertEqual(status["active_hold_count"], 0)


    def test_credit_wei_direct_spend_preserves_fractional_amounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = HubCreditLedger(Path(tmp))
            ledger.issue(account_id="buyer", credits=3, memo="fund buyer")

            charge = ledger.spend_request_credit_wei(
                account_id="buyer",
                request_id="req-fractional",
                credit_wei="1024000000000000000",
                worker_node_id="worker-1",
                memo="fractional direct spend",
            )
            self.assertEqual(charge["charge"]["charged_credit_wei"], "1024000000000000000")
            self.assertEqual(charge["charge"]["charged_credits_display"], "1.024")
            self.assertEqual(charge["charge"]["hold_id"], "")
            self.assertEqual(charge["account"]["available_credit_wei"], "1976000000000000000")
            self.assertEqual(charge["account"]["held_credit_wei"], "0")
            self.assertEqual(charge["account"]["spent_credit_wei"], "1024000000000000000")

            duplicate = ledger.spend_request_credit_wei(
                account_id="buyer",
                request_id="req-fractional",
                credit_wei="1024000000000000000",
                worker_node_id="worker-1",
                memo="fractional duplicate",
            )
            self.assertTrue(duplicate["idempotent"])
            self.assertEqual(duplicate["account"]["spent_credit_wei"], "1024000000000000000")

            account = HubCreditLedger(Path(tmp)).get_account("buyer")
            self.assertEqual(account.available_credit_wei, 1976000000000000000)
            self.assertEqual(account.held_credit_wei, 0)
            self.assertEqual(account.spent_credit_wei, 1024000000000000000)


    def test_legacy_held_balance_is_treated_as_spendable_after_holds_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = HubCreditLedger(Path(tmp))
            ledger.issue(account_id="buyer", credits=2, memo="fund buyer")
            data = json.loads((Path(tmp) / "ledger.json").read_text(encoding="utf-8"))
            account = data["accounts"]["buyer"]
            account["available_credit_wei"] = "0"
            account["available_credits"] = 0
            account["held_credit_wei"] = "2000000000000000000"
            account["held_credits"] = 2
            data["holds"]["legacy-hold"] = {
                "hold_id": "legacy-hold",
                "account_id": "buyer",
                "request_id": "legacy-request",
                "credits": 2,
                "credit_wei": "2000000000000000000",
                "status": "held",
                "created_at": "2026-01-01T00:00:00+00:00",
                "expires_at": "",
                "released_at": "",
                "charged_at": "",
            }
            (Path(tmp) / "ledger.json").write_text(json.dumps(data), encoding="utf-8")

            reloaded = HubCreditLedger(Path(tmp))
            account = reloaded.get_account("buyer")
            self.assertEqual(account.available_credit_wei, 2000000000000000000)
            self.assertEqual(account.held_credit_wei, 0)
            self.assertEqual(reloaded.status()["active_hold_count"], 0)

            spend = reloaded.spend_request_credit(
                account_id="buyer",
                request_id="spend-after-legacy-hold",
                credits=2,
                worker_node_id="worker-1",
            )
            self.assertEqual(spend["account"]["available_credit_wei"], "0")
            self.assertEqual(spend["account"]["held_credit_wei"], "0")
            self.assertEqual(spend["account"]["spent_credit_wei"], "2000000000000000000")


    def test_hub_credit_api_exposes_balances_transactions_deposits_and_bootstrap(self) -> None:
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

                with urlopen(f"{hub_base}/api/hub/v1/credits/deposits", timeout=5) as response:
                    deposits = json.loads(response.read().decode("utf-8"))
                self.assertEqual(deposits["deposit_count"], 0)

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
