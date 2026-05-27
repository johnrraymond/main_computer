from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_script(path: str):
    spec = importlib.util.spec_from_file_location(Path(path).stem, REPO_ROOT / path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def post_json(url: str, payload: dict, *, timeout: float = 5.0) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class BridgeEscrowPaidMockSpendSmokeTests(unittest.TestCase):
    def _start_server(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return thread

    def _manifest(self, path: Path, *, hub_url: str) -> dict:
        scale = 1_000_000
        addresses = [
            "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
            "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
            "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
            "0x90f79bf6eb2c4f870365e785982e1f101e93b906",
        ]
        requesters = [
            {
                "id": f"requester_{index}",
                "role": "requester",
                "account_id": f"bridge-escrow-requester-{index}",
                "address": address,
                "deposit_credits": 100,
                "deposit_units": 100 * scale,
                "log_index": index,
                "normalized_receipt_tx_hash": "0x" + f"{index + 1:064x}",
            }
            for index, address in enumerate(addresses)
        ]
        manifest = {
            "schema_version": "bridge-escrow-dev-manifest-v0",
            "hub": {"url": hub_url},
            "chain": {
                "rpc_url": "http://127.0.0.1:18545",
                "chain_id": 42424242,
                "contract_address": "0x1111111111111111111111111111111111111111",
            },
            "credit_units": {"name": "compute_credit", "scale": scale},
            "actors": {
                "requesters": requesters,
                "worker": {"worker_id": "paid-mock-worker-01"},
            },
            "mock_ai": {
                "provider": "mock",
                "worker_id": "paid-mock-worker-01",
                "models": ["mock-fast-chat"],
                "response_template": "mock {prompt}",
            },
        }
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    def _import_atom_deposits(self, *, hub_url: str, manifest: dict) -> None:
        for requester in manifest["actors"]["requesters"]:
            imported = post_json(
                f"{hub_url}/api/hub/v1/credits/deposits/import",
                {
                    "chain_id": manifest["chain"]["chain_id"],
                    "contract_address": manifest["chain"]["contract_address"],
                    "tx_hash": requester["normalized_receipt_tx_hash"],
                    "log_index": requester["log_index"],
                    "block_number": 1,
                    "account_id": requester["account_id"],
                    "payer_address": requester["address"],
                    "payment_asset": "native",
                    "payment_amount_base_units": requester["deposit_units"],
                    "credits_granted": requester["deposit_units"],
                    "memo": "atom-unit test deposit",
                },
            )
            self.assertEqual(imported["account"]["available_credits"], 100_000_000)

    def test_paid_mock_spend_script_bootstraps_repo_root_for_path_invocation(self) -> None:
        source = (REPO_ROOT / "scripts/run_bridge_escrow_paid_mock_spend_smoke.py").read_text(encoding="utf-8")

        self.assertIn("REPO_ROOT = Path(__file__).resolve().parents[1]", source)
        self.assertIn("sys.path.insert(0, str(REPO_ROOT))", source)
        self.assertLess(source.index("sys.path.insert(0, str(REPO_ROOT))"), source.index("from main_computer.config"))

    def test_phase1_smoke_spends_four_atom_funded_requesters_and_rejects_unfunded(self) -> None:
        smoke = load_script("scripts/run_bridge_escrow_paid_mock_spend_smoke.py")

        with tempfile.TemporaryDirectory() as hub_tmp:
            hub_config = MainComputerConfig(
                workspace=Path(hub_tmp),
                model="mock-fast-chat",
                hub_root=Path(hub_tmp) / "hub-runtime",
                hub_credits_per_request=1,
            )
            hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
            thread = self._start_server(hub)
            try:
                hub_url = f"http://127.0.0.1:{hub.server_port}"
                manifest_path = Path(hub_tmp) / "bridge_escrow_dev_manifest.json"
                manifest = self._manifest(manifest_path, hub_url=hub_url)
                self._import_atom_deposits(hub_url=hub_url, manifest=manifest)

                report = smoke.run_smoke(
                    argparse.Namespace(
                        manifest=manifest_path,
                        report=Path(hub_tmp) / "report.json",
                        hub_url="",
                        worker_id="",
                        model="",
                        response_template="",
                        timeout=10.0,
                        credit_unit_scale=0,
                        spend_credits=[],
                        hold_slack_credits="0.5",
                        worker_share_bps=10_000,
                        negative_account_id="bridge-escrow-unfunded-negative",
                        skip_negative_case=False,
                        idempotency_prefix="test-bridge-escrow-paid-mock-spend",
                        json=True,
                    )
                )

                self.assertTrue(report["ok"])
                self.assertEqual(len(report["spend_plan"]), 4)
                self.assertEqual([row["charge_units"] for row in report["spend_plan"]], [5_500_000, 2_250_000, 10_000_000, 750_000])
                self.assertEqual([row["released_units"] for row in report["spend_plan"]], [500_000, 500_000, 500_000, 500_000])
                self.assertEqual([row["worker_earning_units"] for row in report["spend_plan"]], [5_500_000, 2_250_000, 10_000_000, 750_000])
                self.assertTrue(report["negative_case"]["ok"])
                self.assertEqual(report["negative_case"]["mock_worker_calls_before"], 4)
                self.assertEqual(report["negative_case"]["mock_worker_calls_after"], 4)
            finally:
                hub.shutdown()
                hub.server_close()
                thread.join(timeout=2)

    def test_multi_wallet_import_payload_grants_atom_units(self) -> None:
        multi_wallet = load_script("scripts/run_bridge_escrow_multi_wallet_smoke.py")
        requester = {
            "index": 0,
            "account_id": "bridge-escrow-requester-0",
            "address": "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
            "deposit_credits": 100,
            "deposit_units": 100_000_000,
            "log_index": 0,
        }
        payload = multi_wallet.build_import_payload(
            requester=requester,
            chain={
                "chain_id": 42424242,
                "contract_address": "0x1111111111111111111111111111111111111111",
            },
            tx_hash="0x" + "2" * 64,
            block_number=12,
        )

        self.assertEqual(payload["payment_amount_base_units"], 100_000_000)
        self.assertEqual(payload["credits_granted"], 100_000_000)


if __name__ == "__main__":
    unittest.main()
