"""Former receipt-ingestion URLs must never mint credits or be advertised."""
from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
from main_computer.hub_admin_site import build_admin_bootstrap_payload


REMOVED_PATHS = (
    "/api/hub/v1/credits/deposits/import",
    "/api/hub/v1/credits/purchases/import",
    "/api/hub/v1/credits/wallet-funding/import",
)


@pytest.mark.parametrize("endpoint", REMOVED_PATHS)
def test_former_import_route_is_404_and_does_not_modify_ledger(endpoint):
    with tempfile.TemporaryDirectory() as tmp:
        config = MainComputerConfig(workspace=Path(tmp), model="fake-model",
                                    hub_root=Path(tmp) / "hub-runtime", hub_bridge_backend="mock-chain")
        server = HubHttpServer(("127.0.0.1", 0), config, verbose=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            original = server.credit_ledger.status()
            payload = {
                "chain_id": 42424242,
                "contract_address": "0x" + "1" * 40,
                "tx_hash": "0x" + "2" * 64,
                "log_index": 0,
                "block_number": 12,
                "account_id": "fake-deposit-victim",
                "wallet_address": "0x" + "3" * 40,
                "payer_address": "0x" + "3" * 40,
                "payment_asset": "native",
                "payment_amount_base_units": 10**18,
                "credits_granted_wei": str(1_000_000 * 10**18),
            }
            request = Request(f"http://127.0.0.1:{server.server_port}{endpoint}",
                              data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json"}, method="POST")
            with pytest.raises(HTTPError) as failure:
                urlopen(request, timeout=5)
            assert failure.value.code == 404
            assert server.credit_ledger.status() == original
            assert server.credit_ledger.get_account("fake-deposit-victim").available_credits == 0
            admin = build_admin_bootstrap_payload(
                config=config, registry=server.registry, dispatcher=server.dispatcher,
                energy_ledger=server.energy_ledger,
                credit_ledger=server.credit_ledger, credit_indexer=server.credit_indexer)
            assert endpoint not in admin["endpoints"].values()
            assert endpoint not in server.credit_indexer.status()["endpoints"].values()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


def test_receipt_importer_not_exposed_from_legacy_hub_handler():
    source = (Path(__file__).resolve().parents[1] / "main_computer" / "hub.py").read_text()
    assert "credit_indexer.import_deposit(body)" not in source
    assert "credit_indexer.import_wallet_funding(body)" not in source


@pytest.mark.parametrize("endpoint", REMOVED_PATHS)
def test_stable_hub_does_not_accept_deprecated_funding_endpoint(endpoint):
    from main_computer.stable_hub import create_stable_hub_server
    from main_computer.stable_hub_topology import load_stable_hub_topology
    import http.client

    topology = load_stable_hub_topology(Path(__file__).resolve().parents[1] /
                                        'deploy/hub-topology/dev-topology.json')
    server = create_stable_hub_server(topology=topology, hub_id='dev-hub1',
                                       bind_host='127.0.0.1', bind_port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            conn.request('POST', endpoint, body=b'{"credits_granted_wei":"99999999999999999999"}',
                         headers={'Content-Type':'application/json'})
            response = conn.getresponse()
            assert response.status == 404
            response.read()
            assert response.will_close or conn.sock is None
        finally:
            conn.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
