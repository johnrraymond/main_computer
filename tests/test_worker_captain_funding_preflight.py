"""No network access, private state, or deposits in these funding-unit tests."""
import pytest

from tools.worker_captain_funding_preflight import assess_funding, FundingEvidenceError

U = 10**18
WALLET = '0x' + 'a' * 40


def ledger(available, completed, *, wallet=WALLET):
    return {'ok': True, 'wallet_address': wallet, 'account': {
        'wallet_address': wallet, 'owner_address': wallet,
        'available_credit_wei': str(available),
        'bridge_completed_credit_wei': str(completed),
    }}


def test_historical_held_credit_blocks_further_spending_after_cap():
    state = assess_funding(ledger(976 * U // 1000, 2 * U),
                           wallet_address=WALLET, required_credit_wei=1024 * U // 1000,
                           ceiling_credits=2)
    assert state['fundable'] is False
    assert state['bridgeFundingRequired'] is False
    assert state['reason'] == 'insufficient_credit_under_cumulative_cap'
    assert state['possibleTopUpCreditWei'] == '0'


def test_existing_funds_prevent_any_new_bridge():
    state = assess_funding(ledger(1500 * U // 1000, 1600 * U // 1000),
                           wallet_address=WALLET, required_credit_wei=1024 * U // 1000,
                           ceiling_credits=2)
    assert state['fundable'] is True
    assert state['verdict'] == 'existing_credits_sufficient'
    assert state['bridgeFundingRequired'] is False


def test_bounded_top_up_possible_but_not_guaranteed():
    state = assess_funding(ledger(1000 * U // 1000, 1500 * U // 1000),
                           wallet_address=WALLET, required_credit_wei=1024 * U // 1000,
                           ceiling_credits=2)
    assert state['fundable'] is True
    assert state['bridgeFundingRequired'] is True
    assert state['possibleTopUpCreditWei'] == str(500 * U // 1000)


def test_one_credit_mode_does_not_bridge():
    state = assess_funding(ledger(100 * U // 1000, U), wallet_address=WALLET,
                           required_credit_wei=900 * U // 1000, ceiling_credits=1)
    assert not state['fundable']
    assert state['possibleTopUpCreditWei'] == '0'


@pytest.mark.parametrize('untrusted', [
    ledger(1, 2, wallet='0x' + 'b'*40),
    ledger('not-a-number', 0),
    {'ok': True, 'wallet_address': WALLET},
    {'ok': False, 'wallet_address': WALLET},
    ledger(-1, 0),
])
def test_malformed_or_cross_wallet_hub_ledger_fails_closed(untrusted):
    with pytest.raises(FundingEvidenceError, match='invalid_hub_credit_evidence'):
        assess_funding(untrusted, wallet_address=WALLET,
                       required_credit_wei=U, ceiling_credits=1)


def test_invalid_prices_fail_closed():
    for required, ceiling in [(0, 1), (U + 1, 1), (2 * U + 1, 2), (1, 3)]:
        with pytest.raises(FundingEvidenceError):
            assess_funding(ledger(U, U), wallet_address=WALLET,
                           required_credit_wei=required, ceiling_credits=ceiling)
