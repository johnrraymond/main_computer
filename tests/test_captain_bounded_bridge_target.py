"""The browser acceptance top-up refuses cumulative funding above two credits."""
import pytest

from main_computer.captain_cli import (
    CaptainCliError, build_captain_options_parser, plan_captain_bounded_bridge_top_up,
)

WALLET = '0x'+'ab'*20
U = 10**18


def balance(available, completed, *, address=WALLET):
    return {'ok': True, 'wallet_address': address,
            'account': {'wallet_address': address, 'owner_address': address,
                        'available_credit_wei': str(available),
                        'bridge_completed_credit_wei': str(completed)}}


def plan(a, c):
    return plan_captain_bounded_bridge_top_up(balance(a,c), wallet_address=WALLET)


def test_existing_two_or_more_available_credits_means_no_new_bridge():
    assert plan(2*U, 3*U)['deposit_credit_wei'] == 0
    assert plan(4*U, 4*U)['deposit_credit_wei'] == 0
    assert plan(2*U, 0)['deposit_credit_wei'] == 0


def test_empty_wallet_may_fund_at_most_two():
    assert plan(0, 0)['deposit_credit_wei'] == 2*U
    assert plan(U, U)['deposit_credit_wei'] == U
    assert plan(U + U//2, U + U//2)['deposit_credit_wei'] == U//2


def test_historical_bridge_cap_never_replenishes_but_allows_request():
    # Accepted jobs can hold or spend credit, reducing available below the
    # price ceiling. Never treat that as permission to bridge more credits.
    for available, completed in ((0, 2*U), (U, 3*U), (U//2, 3*U),
                                 (0, 3*U), (U + U//2, 3*U)):
        result = plan(available, completed)
        assert result['deposit_credit_wei'] == 0
        assert result['available_credit_wei'] == available
        assert result['bridge_completed_credit_wei'] == completed


def test_partial_funding_respects_cumulative_bridge_cap():
    # If earlier spending means the shortfall is larger than the remaining
    # lifetime allowance, cap funding at that allowance instead of erroring.
    assert plan(0, U)['deposit_credit_wei'] == U
    assert plan(U//2, U + U//2)['deposit_credit_wei'] == U//2
    assert plan(U//2, U)['deposit_credit_wei'] == U
    assert plan(0, 2*U - 1)['deposit_credit_wei'] == 1


def test_two_credit_ceiling_not_misread_as_required_available_balance():
    result = plan(U + U//2, 3*U)
    assert result['deposit_credit_wei'] == 0
    assert result['available_credit_wei'] < 2*U


@pytest.mark.parametrize('corrupt', [
    {}, {'ok': False}, {'ok': True},
    balance(2*U,3*U,address='0x'+'ee'*20),
    {'ok': True, 'wallet_address': WALLET, 'account': {'wallet_address': WALLET,
       'owner_address': WALLET, 'available_credit_wei': '1.5', 'bridge_completed_credit_wei': '0'}},
    {'ok': True, 'wallet_address': WALLET, 'account': {'wallet_address': WALLET,
       'owner_address': WALLET, 'available_credit_wei': 'NaN', 'bridge_completed_credit_wei': '0'}},
])
def test_unverified_or_malformed_balance_never_authorizes_bridge(corrupt):
    with pytest.raises(CaptainCliError):
        plan_captain_bounded_bridge_top_up(corrupt,wallet_address=WALLET)


def test_target_must_be_exactly_two_credits():
    with pytest.raises(CaptainCliError, match='2 credits'):
        plan_captain_bounded_bridge_top_up(balance(U,U),wallet_address=WALLET,target_wei=3*U)
    options=build_captain_options_parser().parse_args(['--bridge-balance-target','2','--max-credits','2'])
    assert options.bridge_balance_target == '2' and options.max_credits == 2
