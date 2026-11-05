"""Fail-closed, secret-free reconciliation for repeated emulated earn/spend cycles.

The buyer is funded synthetically in the smoke namespace. Execution uses the
real SLH Worker transport with an emulated computation callback; all debits,
earnings, organization obligations and Reserve payments are authoritative Hub
and local obligation-ledger observations, not fabricated balances.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from main_computer.credit_units import CREDIT_WEI_PER_CREDIT
from main_computer.slh.pinky_promise import ASSESSMENT_WEI as ONE_CREDIT_LOCAL_ASSESSMENT_WEI

PINKY_PER_REQUEST_WEI = ONE_CREDIT_LOCAL_ASSESSMENT_WEI
EXPECTED_PRIVATE_REQUESTS = 2
SCHEMA = 'main-computer.slh-pinky-economic-reconciliation.v1'


def reconcile_cycle(paid_process: dict, *, cycle: int) -> dict:
    """Check both sides of every transfer before recording a completed cycle."""
    market = paid_process.get('paid_marketplace') or {}
    private = paid_process.get('pinky_integrated') or {}
    buyer = market.get('accounting') or {}
    pinky = private.get('accounting') or {}

    def number(mapping, name):
        value = mapping.get(name)
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return -1
        try:
            return int(value)
        except ValueError:
            return -1

    charge = number(market, 'charged_credit_wei')
    earning = number(market, 'earned_credit_wei')
    paid_amount = number(private, 'swept_credit_wei')
    assessed = number(private, 'assessed_credit_wei')
    buyer_delta = number(buyer, 'buyer_spent_delta_wei')
    buyer_available_before = number(buyer, 'buyer_available_before_wei')
    buyer_available_after = number(buyer, 'buyer_available_after_wei')
    org_before = number(buyer, 'worker_available_before_wei')
    org_earned = number(buyer, 'worker_available_after_settlement_wei')
    org_sweep_before = number(pinky, 'wallet_after_earning_wei')
    org_sweep_after = number(pinky, 'wallet_after_sweep_wei')
    reserve_before = number(pinky, 'reserve_before_wei')
    reserve_after = number(pinky, 'reserve_after_wei')
    private_ids = pinky.get('private_request_ids')
    private_ids = private_ids if isinstance(private_ids, list) else []
    buyer_id = buyer.get('buyer_account_id')
    org_account = pinky.get('organization_account_id')
    reserve_account = pinky.get('reserve_account_id')
    request_id = market.get('request_id')
    session_id = market.get('session_id')
    sweep_id = pinky.get('sweep_id')
    receipt_id = pinky.get('hub_receipt_id')
    gates = {
        'real_paid_process_passed': paid_process.get('pass') is True and paid_process.get('status') == 'PASS',
        'marketplace_gates_passed': market.get('pass') is True and all(v is True for v in (market.get('gates') or {}).values()) and bool(market.get('gates')),
        'cycle_market_worker_isolated': (bool(market.get('market_isolation_capability'))
            and (market.get('gates') or {}).get('market_isolation_capability_verified') is True),
        'private_and_sweep_gates_passed': private.get('pass') is True and all(v is True for v in (private.get('gates') or {}).values()) and bool(private.get('gates')),
        'paid_job_one_credit': charge == earning == CREDIT_WEI_PER_CREDIT,
        'market_earning_matches_pinky_earning': number(private, 'earned_credit_wei') == earning and earning > 0,
        'buyer_spent_exactly_charge': buyer_delta == charge and charge > 0,
        'buyer_available_exactly_decreased': buyer_available_before == 2 * CREDIT_WEI_PER_CREDIT and buyer_available_after == CREDIT_WEI_PER_CREDIT and buyer_available_before - buyer_available_after == charge and charge > 0,
        'buyer_has_no_unsettled_hold': number(buyer, 'buyer_held_after_wei') == 0,
        'earned_credit_deposited_to_org': org_before == 0 and org_earned - org_before == earning and earning > 0 and number(buyer,'worker_earned_after_settlement_wei') == earning,
        'same_org_wallet_used_for_pinky': buyer.get('worker_account_id') == org_account == private.get('worker_account_id') and org_account != buyer_id and bool(org_account),
        'two_private_requests_distinct': (len(private_ids) == EXPECTED_PRIVATE_REQUESTS and len(set(private_ids)) == EXPECTED_PRIVATE_REQUESTS and all(isinstance(x,str) and x for x in private_ids)
                                         and number(pinky,'private_succeeded_count') == EXPECTED_PRIVATE_REQUESTS),
        'obligations_match_completed_private_work': (number(pinky,'obligation_count_before_sweep') == EXPECTED_PRIVATE_REQUESTS
             and number(pinky,'obligation_paid_count_after_sweep') == EXPECTED_PRIVATE_REQUESTS
             and number(pinky,'obligation_outstanding_before_wei') == PINKY_PER_REQUEST_WEI * EXPECTED_PRIVATE_REQUESTS
             and number(pinky,'obligation_outstanding_after_wei') == 0
             and assessed == PINKY_PER_REQUEST_WEI * EXPECTED_PRIVATE_REQUESTS),
        'sweep_exactly_assessed_obligations': paid_amount == assessed and paid_amount > 0,
        'org_wallet_debited_exactly_once': org_earned == org_sweep_before and org_sweep_before - org_sweep_after == paid_amount and paid_amount > 0,
        'reserve_credited_exactly_once': reserve_after - reserve_before == paid_amount and paid_amount > 0,
        'payment_receipt_present': bool(sweep_id and receipt_id and request_id and session_id and buyer.get('charge_id') and buyer.get('earning_id')),
        'buyer_org_reserve_distinct': bool(buyer_id and org_account and reserve_account and len({buyer_id,org_account,reserve_account}) == 3),
        'same_hub_in_marketplace_and_pinky': (bool(private.get('hub_id')) and bool(private.get('cluster_id')) and private.get('namespace') == 'main-computer-hub-smoke'),
        'emulated_worker_only': market.get('funding') == 'SYNTHETIC_LAB_ONLY' and private.get('limitations',{}).get('source_funding') == 'LAB_REQUESTER_SYNTHETIC',
    }
    return {
        'schema': 'main-computer.slh-pinky-economic-cycle.v1',
        'cycle': cycle, 'status': 'PASS' if all(gates.values()) else 'FAIL',
        'pass': all(gates.values()), 'gates': gates,
        'hub_id': private.get('hub_id'), 'cluster_id': private.get('cluster_id'),
        'market_isolation_capability': market.get('market_isolation_capability'),
        'buyer_account_id': buyer_id, 'organization_account_id': org_account,
        'reserve_account_id': reserve_account, 'paid_request_id': request_id,
        'paid_session_id': session_id, 'charge_id': buyer.get('charge_id'),
        'earning_id': buyer.get('earning_id'), 'private_request_ids': private_ids,
        'sweep_id': sweep_id, 'receipt_id': receipt_id,
        'buyer_spent_wei': str(buyer_delta), 'earned_wei': str(earning),
        'assessed_wei': str(assessed), 'swept_wei': str(paid_amount),
        'org_wallet_before_paid_wei': str(org_before),
        'org_wallet_after_paid_wei': str(org_earned),
        'org_wallet_after_sweep_wei': str(org_sweep_after),
        'reserve_before_wei': str(reserve_before),
        'reserve_after_wei': str(reserve_after),
        'execution': 'EMULATED_WORKER_NO_AI_CALLS',
    }


def summarize_cycles(cycles: list[dict], *, requested: int) -> dict:
    """Prove each economic cycle is independent while Reserve payments accumulate."""
    gates = {
        'all_requested_cycles_completed': len(cycles) == requested,
        'all_individual_cycles_passed': bool(cycles) and all(x.get('pass') is True for x in cycles),
        'unique_wallet_accounts': len({x.get('organization_account_id') for x in cycles if x.get('organization_account_id')}) == len(cycles),
        'unique_buyers': len({x.get('buyer_account_id') for x in cycles if x.get('buyer_account_id')}) == len(cycles),
        'unique_org_wallets': len({x.get('organization_account_id') for x in cycles if x.get('organization_account_id')}) == len(cycles),
        'unique_paid_requests': len({x.get('paid_request_id') for x in cycles if x.get('paid_request_id')}) == len(cycles),
        'unique_market_isolation_capabilities': len({x.get('market_isolation_capability') for x in cycles if x.get('market_isolation_capability')}) == len(cycles),
        'unique_sweep_ids': len({x.get('sweep_id') for x in cycles if x.get('sweep_id')}) == len(cycles),
        'unique_receipts': len({x.get('receipt_id') for x in cycles if x.get('receipt_id')}) == len(cycles),
        'unique_paid_sessions': len({x.get('paid_session_id') for x in cycles if x.get('paid_session_id')}) == len(cycles),
        'unique_charge_ids': len({x.get('charge_id') for x in cycles if x.get('charge_id')}) == len(cycles),
        'unique_earning_ids': len({x.get('earning_id') for x in cycles if x.get('earning_id')}) == len(cycles),
        'buyers_never_reused_as_orgs': not ({x.get('buyer_account_id') for x in cycles} & {x.get('organization_account_id') for x in cycles}),
        'same_reserve': bool(cycles) and len({x.get('reserve_account_id') for x in cycles}) == 1,
        'same_verified_hub_cluster': bool(cycles) and len({(x.get('hub_id'), x.get('cluster_id')) for x in cycles}) == 1,
        'reserve_continuity': all(cycles[i]['reserve_before_wei'] == cycles[i-1]['reserve_after_wei'] for i in range(1,len(cycles))),
        'no_cross_cycle_private_request_reuse': sum(len(x.get('private_request_ids') or []) for x in cycles) == len({rid for x in cycles for rid in x.get('private_request_ids') or []}),
    }
    total_earned = sum(int(x.get('earned_wei') or 0) for x in cycles)
    total_spent = sum(int(x.get('swept_wei') or 0) for x in cycles)
    total_buyer_debits = sum(int(x.get('buyer_spent_wei') or 0) for x in cycles)
    gates['total_buyer_debits_equal_earnings'] = total_buyer_debits == total_earned == len(cycles) * CREDIT_WEI_PER_CREDIT
    gates['total_reserve_credit_equals_sweeps'] = total_spent == len(cycles) * 2 * PINKY_PER_REQUEST_WEI
    success = all(gates.values())
    return {
        'schema': SCHEMA,
        'status': 'PASS' if success else ('INCOMPLETE' if len(cycles) < requested and all(x.get('pass') for x in cycles) else 'FAIL'),
        'pass': success, 'cycles_requested': requested, 'cycles_completed': len(cycles),
        'gates': gates, 'cycles': list(cycles),
        'total_buyer_debit_wei': str(total_buyer_debits),
        'total_earned_credit_wei': str(total_earned),
        'total_reserve_swept_wei': str(total_spent),
        'execution': 'EMULATED_WORKER_NO_AI_CALLS',
        'funding': 'SYNTHETIC_BUYER_ONLY_IN_SMOKE_NAMESPACE',
    }


def write_atomic_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(temporary, path)
