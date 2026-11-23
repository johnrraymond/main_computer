#!/usr/bin/env python3
"""Non-spending Captain credit preflight for the browser Work-cycle test.

Uses the verified Mother officer identity and Captain's existing Stable Hub
MSK issuance to authenticate a read-only ledger request. An explicitly
authorized run may sign and issue/reuse an MSK; it never creates a deposit,
submits work, refunds, or modifies a Hub credit balance.
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


def probe_live(*, required_credit_wei: int, ceiling_credits: int, repo_root: Path,
               authorize_captain_wallet_signing: bool = False) -> dict:
    options = build_captain_options_parser().parse_args(['--network', 'testnet'])
    config = MainComputerConfig.from_env()
    _apply_captain_network_defaults(options, base_config=config, cwd=repo_root)
    runtime = build_captain_runtime(config, options=options,
                                    selector='captain', cwd=repo_root)
    # Explicit fail-closed authority boundary: never query another network.
    if (runtime.network != 'testnet' or runtime.config.hub_url.rstrip('/') != TESTNET_HUB
            or runtime.chain_id != 42424241):
        raise FundingEvidenceError('testnet_identity_mismatch')
    # The Stable Hub refuses an anonymous balance read (HTTP 403). Reuse
    # Captain's signed MSK authorization, but only when the operator's live
    # work-cycle invocation explicitly authorizes Captain wallet signing.
    if not authorize_captain_wallet_signing:
        raise FundingEvidenceError('captain_wallet_signing_not_authorized')
    from main_computer.hub_cli_live_session import (
        identify_hub_protocol, issue_cli_multisession_authorization,
    )
    try:
        identity = identify_hub_protocol(runtime.config.hub_url, timeout_s=15)
    except Exception as exc:
        raise FundingEvidenceError('testnet_hub_identity_unavailable') from exc
    network = identity.get('network') if isinstance(identity, dict) else None
    if (not isinstance(identity, dict)
            or identity.get('service') != 'main_computer.stable_hub'
            or not isinstance(network, dict)
            or str(network.get('chain_id') or '') != '42424241'):
        raise FundingEvidenceError('testnet_hub_identity_mismatch')
    try:
        authorization = issue_cli_multisession_authorization(
            runtime.config.hub_url, wallet=runtime.wallet, timeout_s=15,
            stable_identity=identity,
        )
    except Exception as exc:
        raise FundingEvidenceError('captain_msk_authorization_unavailable') from exc
    try:
        balance = _fetch_hub_credit_balance(
            runtime.config.hub_url, wallet_address=runtime.wallet.address,
            timeout_s=15, authorization=authorization,
        )
    except Exception as exc:
        raise FundingEvidenceError('testnet_ledger_unavailable') from exc
    return assess_funding(balance, wallet_address=runtime.wallet.address,
                          required_credit_wei=required_credit_wei,
                          ceiling_credits=ceiling_credits)


def main() -> int:
    parser = argparse.ArgumentParser(description='Non-spending Testnet Captain funding gate (wallet-signed MSK authorization required)')
    parser.add_argument('--required-credit-wei', type=int, default=1024000000000000000)
    parser.add_argument('--ceiling-credits', type=int, choices=(1, 2), default=2)
    parser.add_argument('--authorize-captain-wallet-signing', action='store_true',
                        help='Permit Captain to sign and issue/reuse an MSK for authenticated read-only ledger access.')
    args = parser.parse_args()
    if not 0 < args.required_credit_wei <= args.ceiling_credits * UNIT:
        print(json.dumps({'ok': False, 'fundable': False, 'reason': 'invalid_worker_price'}))
        return 2
    try:
        result = probe_live(required_credit_wei=args.required_credit_wei,
                            ceiling_credits=args.ceiling_credits,
                            repo_root=Path(__file__).resolve().parents[1],
                            authorize_captain_wallet_signing=args.authorize_captain_wallet_signing)
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
