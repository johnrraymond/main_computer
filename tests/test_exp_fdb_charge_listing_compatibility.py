"""Experimental ledger must implement the inherited Hub charge-history API.

Regression: owner-Hub failover queried /api/hub/v1/credits/charges and the
inherited handler passed account_id to a ledger method that rejected it.
This module exercises the real handler and FDB-shaped in-memory ledger.
"""
from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
from urllib.request import urlopen

from main_computer.hub import HubServerHandler
from test_exp_fdb_hold_lifecycle_p0 import ledger


def _charged_store():
    store = ledger()
    for account, request in (("account-one", "req-one"),
                             ("account-one", "req-two"),
                             ("account-two", "req-three")):
        store.issue(account_id=account, credits=2)
        hold = store.create_hold(account_id=account, request_id=request, credits=1)
        store.charge_hold(hold_id=hold["hold"]["hold_id"], charged_credits=1,
                          worker_node_id="worker-one")
    return store


def test_charge_listing_supports_stable_account_and_request_filters():
    store = _charged_store()
    assert len(store.list_charges()) == 3
    assert {c.request_id for c in store.list_charges(account_id="account-one")} == {"req-one", "req-two"}
    assert [c.request_id for c in store.list_charges(account_id="account-two")] == ["req-three"]
    assert [c.request_id for c in store.list_charges(request_id="req-one")] == ["req-one"]
    assert [c.request_id for c in store.list_charges(account_id="account-one", request_id="req-two")] == ["req-two"]
    assert store.list_charges(account_id="account-two", request_id="req-two") == []
    assert len(store.list_charges(account_id="account-one", limit=1)) == 1
    assert len(store.list_charges(account_id="account-one", limit=0)) == 2  # Stable default
    assert len(store.list_charges(account_id="account-one", limit=-1)) == 1


def test_inherited_hub_http_charge_listing_returns_json_instead_of_disconnect():
    store = _charged_store()
    server = ThreadingHTTPServer(("127.0.0.1", 0), HubServerHandler)
    server.credit_ledger = store
    server.verbose = False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/api/hub/v1/credits/charges"
        for filters, expected in [
            ({"request_id": "req-one"}, {"req-one"}),
            ({"account_id": "account-one"}, {"req-one", "req-two"}),
            ({"account_id": "account-two", "request_id": "req-three"}, {"req-three"}),
            ({"account_id": "account-two", "request_id": "req-one"}, set()),
        ]:
            with urlopen(base + "?" + urlencode(filters), timeout=3) as response:
                assert response.status == 200
                result = json.load(response)
            assert result["ok"] is True
            assert result["charge_count"] == len(expected)
            assert {charge["request_id"] for charge in result["charges"]} == expected
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
