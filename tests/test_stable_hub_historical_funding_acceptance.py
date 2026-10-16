"""Deposit identity, not the escrow's cumulative history, governs crediting."""
from main_computer.hub_credit_indexer import wallet_account_id
from test_stable_hub_credit_bridge_patch2 import FakeEscrow, service, complete, WALLET, DEPOSIT, UNIT
from test_stable_hub_fdb_atomic_accounting import _two_hubs


def test_historical_chain_totals_do_not_block_new_verified_deposit():
    _, stores, a, c = _two_hubs()
    chain = FakeEscrow(historical_units=7 * UNIT)
    result = complete(service(a, chain))
    assert result["delta_credit_wei"] == str(UNIT)
    assert result["chain_completed_credit_wei"] == str(8 * UNIT)
    assert a.get_account(wallet_account_id(WALLET))["available_credit_wei"] == str(UNIT)
    assert complete(service(c, chain))["idempotent"] is True
    assert len(stores[1].load()["payout_deposits"]) == 1


def test_preexisting_account_spend_is_not_recredited_by_watermark():
    _, stores, a, c = _two_hubs()
    account_id = wallet_account_id(WALLET)
    def seed():
        data = stores[0].load()
        data.setdefault("payout_accounts", {})[account_id] = a._account_payload(
            account_id=account_id, wallet_address=WALLET,
            available_credit_wei=0, spent_credit_wei=7 * UNIT,
            bridge_completed_credit_wei=7 * UNIT,
        )
        stores[0].save(data)
    stores[0].atomic(seed)
    chain = FakeEscrow(historical_units=7 * UNIT)
    first = complete(service(a, chain))
    replay = complete(service(c, chain))
    assert first["delta_credit_wei"] == str(UNIT)
    assert replay["idempotent"] is True and replay["delta_credit_wei"] == "0"
    for ledger in (a, c):
        account = ledger.get_account(account_id)
        assert account["available_credit_wei"] == str(UNIT)
        assert account["spent_credit_wei"] == str(7 * UNIT)
        assert account["bridge_completed_credit_wei"] == str(8 * UNIT)
    assert len(stores[1].load()["payout_deposits"]) == 1
