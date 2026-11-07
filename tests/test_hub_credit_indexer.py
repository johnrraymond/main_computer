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






if __name__ == "__main__":
    unittest.main()
