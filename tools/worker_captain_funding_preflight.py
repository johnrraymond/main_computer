#!/usr/bin/env python3
"""Read-only Captain credit preflight for the browser Work-cycle test.

Uses the same verified Mother officer identity, Testnet Hub profile, and
Hub ledger contract as Captain itself. Never signs, creates a deposit,
submits work, refunds, or modifies a Hub account.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from main_computer.captain_cli import (
    CaptainCliError,
    _apply_captain_network_defaults,
    _fetch_hub_credit_balance,
    build_captain_options_parser,
    build_captain_runtime,
    plan_captain_bounded_bridge_top_up,
)
from main_computer.config import MainComputerConfig

UNIT = 10**18
TESTNET_HUB = "https://testnet-hub.greatlibrary.io"


class FundingEvidenceError(ValueError):
    """A failed funding prerequisite, never raw Hub or Mother internals."""


def assess_funding(balance: dict, *, wallet_address: str,
                   required_credit_wei: int, ceiling_credits: int) -> dict:
    """Calculate available funding using the existing validated bridge-cap rule.

    A two-credit ceiling is not permission to bridge unnecessarily. New deposit
    capacity may help only if the configured Work-cycle command actually allows
    bounded bridge funding; one-credit mode never bridges.
    """
    if ceiling_credits not in (1, 2) or isinstance(ceiling_credits, bool):
        raise FundingEvidenceError('invalid_price_ceiling')
    if (isinstance(required_credit_wei, bool) or not isinstance(required_credit_wei, int)
            or not 0 < required_credit_wei <= ceiling_credits * UNIT):
        raise FundingEvidenceError('invalid_worker_price')
    try:
        plan = plan_captain_bounded_bridge_top_up(balance, wallet_address=wallet_address)
    except (CaptainCliError, ValueError, TypeError, KeyError) as exc:
        raise FundingEvidenceError('invalid_hub_credit_evidence') from exc
    available = plan['available_credit_wei']
    completed = plan['bridge_completed_credit_wei']
    possible_deposit = plan['deposit_credit_wei'] if ceiling_credits == 2 else 0
    needs_bridge = available < required_credit_wei
    # Account can already pay: no extra credits should be bridged. Account
    # still deficient with a fully consumed cap: fail before a paid request.
    fundable = available >= required_credit_wei or (
        ceiling_credits == 2 and available + possible_deposit >= required_credit_wei
    )
    verdict = ('existing_credits_sufficient' if not needs_bridge else
               'bounded_top_up_possible' if fundable else
               'insufficient_credit_under_cumulative_cap')
    return {
        'ok': True,
        'fundable': fundable,
        'reason': 'none' if fundable else 'insufficient_credit_under_cumulative_cap',
        'verdict': verdict,
        'bridgeFundingRequired': needs_bridge and fundable,
        'requiredCreditWei': str(required_credit_wei),
        'availableCreditWei': str(available),
        'bridgeCompletedCreditWei': str(completed),
        'possibleTopUpCreditWei': str(possible_deposit),
    }


def probe_live(*, required_credit_wei: int, ceiling_credits: int, repo_root: Path) -> dict:
    options = build_captain_options_parser().parse_args(['--network', 'testnet'])
    config = MainComputerConfig.from_env()
    _apply_captain_network_defaults(options, base_config=config, cwd=repo_root)
    runtime = build_captain_runtime(config, options=options,
                                    selector='captain', cwd=repo_root)
    # Explicit fail-closed authority boundary: never query another network.
    if (runtime.network != 'testnet' or runtime.config.hub_url.rstrip('/') != TESTNET_HUB
            or runtime.chain_id != 42424241):
        raise FundingEvidenceError('testnet_identity_mismatch')
    try:
        balance = _fetch_hub_credit_balance(runtime.config.hub_url,
                                            wallet_address=runtime.wallet.address, timeout_s=15)
    except Exception as exc:
        raise FundingEvidenceError('testnet_ledger_unavailable') from exc
    return assess_funding(balance, wallet_address=runtime.wallet.address,
                          required_credit_wei=required_credit_wei,
                          ceiling_credits=ceiling_credits)


def main() -> int:
    parser = argparse.ArgumentParser(description='Read-only Testnet Captain funding gate')
    parser.add_argument('--required-credit-wei', type=int, default=1024000000000000000)
    parser.add_argument('--ceiling-credits', type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    if not 0 < args.required_credit_wei <= args.ceiling_credits * UNIT:
        print(json.dumps({'ok': False, 'fundable': False, 'reason': 'invalid_worker_price'}))
        return 2
    try:
        result = probe_live(required_credit_wei=args.required_credit_wei,
                            ceiling_credits=args.ceiling_credits,
                            repo_root=Path(__file__).resolve().parents[1])
    except FundingEvidenceError as exc:
        result = {'ok': False, 'fundable': False, 'reason': str(exc)}
    except Exception:
        # No exception text: Mother/configuration exceptions may contain paths
        # or deployment details, and this subprocess output is operator-facing.
        result = {'ok': False, 'fundable': False, 'reason': 'captain_configuration_unavailable'}
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get('fundable') and result.get('ok') else 2


if __name__ == '__main__':
    raise SystemExit(main())
