#!/usr/bin/env python3
"""Testnet-only, bounded XLag reserve -> Captain -> Hub escrow replenishment.

The Worker acceptance harness is the only automatic caller. The existing
Captain bridge cumulative cap is *not* changed. A guarded reserve payout pays
for a separately completed, normally verified bridge deposit. If an operation
has an uncertain outcome, it remains locked for deposit-specific reconciliation.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from main_computer.captain_cli import (
    CaptainCliError, _apply_captain_network_defaults, _captain_pending_bridge_path,
    _complete_captain_bridge_deposit, _fetch_hub_credit_balance,
    _require_hub_bridge_completion_ready, _require_no_other_captain_pending_deposits,
    _save_captain_pending_bridge, build_captain_options_parser,
    build_captain_runtime, normalize_smoke_id, resolve_captain_wallet,
    plan_captain_bounded_bridge_top_up,
    send_captain_bridge_deposit, send_captain_contract_transaction,
)
from main_computer.config import MainComputerConfig
from main_computer.hub_credit_bridge_completion import JsonRpcClient
from main_computer.hub_credit_models import normalize_address
from tools.worker_captain_funding_preflight import (
    FundingEvidenceError, TESTNET_HUB, UNIT, assess_funding,
)

CHAIN = 42424241
MAX_TOP_UP = UNIT // 5                  # 0.2 credit per Work-cycle attempt
DAILY_TOP_UP = UNIT // 2                # 0.5 credit per UTC day
BUFFER = UNIT // 40                     # 0.025 credit reserve for price drift
GAS_BUFFER = UNIT // 200                # 0.005 native units for gas
STATE_DIR = Path('runtime/state/captain/reserve-siphon')
SELECTORS = {
    'next': '2ab09d14', 'delay': '5a8d8e42', 'max': 'e21a90a6',
    'office': 'a653a364', 'state': 'd26331d4',
}


class SiphonRefused(RuntimeError):
    pass


def plan_siphon(balance: dict, *, wallet: str, required_wei: int) -> dict:
    """Exact wei, no implicit funding for preflight failures or sufficient funds."""
    if isinstance(required_wei, bool) or not isinstance(required_wei, int) or not 0 < required_wei <= 2*UNIT:
        raise SiphonRefused('invalid_worker_price')
    try:
        account = assess_funding(balance, wallet_address=wallet,
                                 required_credit_wei=required_wei,
                                 ceiling_credits=1 if required_wei <= UNIT else 2)
    except FundingEvidenceError as exc:
        raise SiphonRefused('invalid_hub_credit_evidence') from exc
    available = int(account['availableCreditWei'])
    if available >= required_wei:
        return {'needed': False, 'top_up_wei': 0, 'payout_wei': 0}
    normal = plan_captain_bounded_bridge_top_up(balance, wallet_address=wallet)
    if available + normal['deposit_credit_wei'] >= required_wei:
        # Even with a one-credit *Worker* offer, prefer the established capped
        # bridge path when it still has room. Do not siphon treasury first.
        raise SiphonRefused('ordinary_bridge_capacity_remains')
    if account['verdict'] != 'insufficient_credit_under_cumulative_cap':
        raise SiphonRefused('unexpected_funding_state')
    amount = required_wei - available + BUFFER
    if amount > MAX_TOP_UP:
        raise SiphonRefused('deficit_exceeds_small_siphon_limit')
    return {'needed': True, 'top_up_wei': amount, 'payout_wei': amount+GAS_BUFFER}


def _read_uint(rpc: JsonRpcClient, contract: str, selector: str, arg: int | None = None) -> int:
    data = '0x' + selector + (int(arg).to_bytes(32, 'big').hex() if arg is not None else '')
    result = rpc.eth_call(to=contract, data=data)
    if not isinstance(result, str) or len(result) < 66:
        raise SiphonRefused('reserve_contract_call_unavailable')
    return int(result[2:66], 16)


def _read_office(rpc: JsonRpcClient, contract: str, index: int) -> str:
    value = _read_uint(rpc, contract, SELECTORS['office'], index)
    if value >> 160:
        raise SiphonRefused('invalid_reserve_officer_identity')
    return normalize_address('0x' + f'{value:040x}')


def _verify_proposal_receipt(receipt: dict, *, reserve: str, proposal: int,
                             captain: str, amount: int) -> None:
    """Bind the predicted proposal ID to *our* actual on-chain payout event.

    Another Captain operation could consume nextProposalId between eth_call
    and submission. Never ask O2 to second an unrelated proposal.
    """
    from eth_utils import keccak
    signature = 'PayoutProposed(uint256,address,address,uint256,string,uint64)'
    event_topic = '0x' + keccak(text=signature).hex()
    entries = receipt.get('logs') if isinstance(receipt, dict) else None
    if not isinstance(entries, list):
        raise SiphonRefused('reserve_proposal_event_unavailable')
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('topics'), list):
            continue
        topics = entry['topics']
        if len(topics) != 4 or str(topics[0]).lower() != event_topic.lower():
            continue
        try:
            source = normalize_address(entry.get('address', ''))
            captain_event = normalize_address('0x' + str(topics[2])[-40:])
            recipient_event = normalize_address('0x' + str(topics[3])[-40:])
            event_proposal = int(topics[1], 16)
            event_amount = int(entry['data'][2:66], 16)
        except (ValueError, KeyError, TypeError):
            continue
        if (source == normalize_address(reserve) and
                captain_event == recipient_event == normalize_address(captain) and
                event_proposal == proposal and event_amount == amount):
            return
    raise SiphonRefused('reserve_proposal_event_mismatch')


def _write_event(folder: Path, event: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / (datetime.now(timezone.utc).date().isoformat() + '.jsonl')).open('a', encoding='utf8') as f:
        f.write(json.dumps(event, sort_keys=True) + '\n')
        f.flush()
        os.fsync(f.fileno())


def _charged_today(folder: Path) -> int:
    file = folder / (datetime.now(timezone.utc).date().isoformat() + '.jsonl')
    if not file.exists():
        return 0
    total = 0
    for line in file.read_text(encoding='utf8').splitlines():
        entry = json.loads(line)
        if entry.get('status') == 'initiated':
            total += int(entry['top_up_wei'])
    return total


def _save_lock(lock: Path, data: dict, *, exclusive: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    fd = os.open(lock, flags, 0o600)
    with os.fdopen(fd, 'w', encoding='utf8') as f:
        json.dump(data, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())


def _runtime(root: Path):
    options = build_captain_options_parser().parse_args(['--network', 'testnet'])
    config = MainComputerConfig.from_env()
    _apply_captain_network_defaults(options, base_config=config, cwd=root)
    runtime = build_captain_runtime(config, options=options, selector='captain', cwd=root)
    if (runtime.network != 'testnet' or runtime.chain_id != CHAIN or
            runtime.config.hub_url.rstrip('/') != TESTNET_HUB):
        raise SiphonRefused('testnet_identity_mismatch')
    return runtime


def run(*, root: Path, required_wei: int, execute: bool = False,
        timeout_s: float = 180) -> dict:
    runtime = _runtime(root)
    balance = _fetch_hub_credit_balance(runtime.config.hub_url,
                                        wallet_address=runtime.wallet.address, timeout_s=15)
    plan = plan_siphon(balance, wallet=runtime.wallet.address, required_wei=required_wei)
    if not plan['needed']:
        return {'ok': True, 'status': 'already_funded', 'topUpCreditWei': '0'}
    if not execute:
        return {'ok': True, 'status': 'dry_run',
                'topUpCreditWei': str(plan['top_up_wei']),
                'reservePayoutWei': str(plan['payout_wei'])}

    # Verify all authority *before* we make the first irreversible write.
    beta = resolve_captain_wallet('o2', deployment=runtime.deployment,
                                  deployment_path=runtime.deployment_path, network='testnet')
    if not runtime.wallet.private_key or not beta.private_key or normalize_address(beta.address) == normalize_address(runtime.wallet.address):
        raise SiphonRefused('reserve_signing_authority_missing')
    rpc = JsonRpcClient(runtime.rpc_url, timeout_s=15)
    if rpc.chain_id() != CHAIN:
        raise SiphonRefused('testnet_rpc_chain_mismatch')
    if _read_office(rpc, runtime.xlag_address, 0) != normalize_address(runtime.wallet.address):
        raise SiphonRefused('reserve_captain_office_mismatch')
    if _read_office(rpc, runtime.xlag_address, 2) != normalize_address(beta.address):
        raise SiphonRefused('reserve_beta_office_mismatch')
    if _read_uint(rpc, runtime.xlag_address, SELECTORS['max']) < plan['payout_wei']:
        raise SiphonRefused('reserve_contract_payout_limit')
    if int(rpc.rpc('eth_getBalance', [runtime.xlag_address, 'latest']), 16) < plan['payout_wei']:
        raise SiphonRefused('reserve_balance_unavailable')
    _require_hub_bridge_completion_ready(runtime.config.hub_url, chain_id=CHAIN,
                                         contract_address=runtime.bridge_escrow_address,
                                         controller_address=runtime.bridge_controller_address,
                                         timeout_s=15)
    operation_id = normalize_smoke_id('worker-test-reserve:' + runtime.wallet.address + ':' + str(time.time_ns()))
    pending = _captain_pending_bridge_path(runtime, smoke_id=operation_id, cwd=root)
    _require_no_other_captain_pending_deposits(pending, wallet_address=runtime.wallet.address)
    folder = root / STATE_DIR
    lock = folder / 'active.json'
    folder.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        raise SiphonRefused('reserve_siphon_pending_reconciliation')
    if _charged_today(folder) + plan['top_up_wei'] > DAILY_TOP_UP:
        raise SiphonRefused('reserve_daily_siphon_limit')
    # One permanent journal allocation plus a lock before broadcasting.
    # Interrupted/uncertain operations must be reconciled, not retried with a
    # fresh proposal or duplicate escrow deposit.
    try:
        _save_lock(lock, {'operation': operation_id, 'status': 'initiated',
                          'top_up_wei': str(plan['top_up_wei'])}, exclusive=True)
    except FileExistsError as exc:
        raise SiphonRefused('reserve_siphon_pending_reconciliation') from exc
    try:
        _write_event(folder, {'operation': operation_id, 'status': 'initiated',
                              'top_up_wei': str(plan['top_up_wei'])})
        # Re-fetch the Hub ledger at the point of no return. Fail closed if a
        # competing request has changed the deficit; lock remains if uncertain.
        live = _fetch_hub_credit_balance(runtime.config.hub_url,
                                         wallet_address=runtime.wallet.address, timeout_s=15)
        if plan_siphon(live, wallet=runtime.wallet.address, required_wei=required_wei) != plan:
            raise SiphonRefused('balance_changed_before_reserve_payout')
        delay = _read_uint(rpc, runtime.xlag_address, SELECTORS['delay'])
        proposal = _read_uint(rpc, runtime.xlag_address, SELECTORS['next'])
        current_block = int(rpc.rpc('eth_blockNumber'), 16)
        expiry = current_block + max(256, delay + 128)
        memo = 'worker functional test reserve siphon ' + operation_id
        _save_lock(lock, {'operation': operation_id, 'status': 'proposing', 'proposal': proposal})
        proposed = send_captain_contract_transaction(
            runtime, contract_address=runtime.xlag_address, private_key=runtime.wallet.private_key,
            function_signature='proposePayout(address,uint256,string,uint64)',
            function_args=[runtime.wallet.address, str(plan['payout_wei']), memo, str(expiry)],
            timeout_s=30)
        _save_lock(lock, {'operation': operation_id, 'status': 'proposed',
                          'proposal': proposal, 'tx_hash': proposed['transaction_hash']})
        _verify_proposal_receipt(proposed.get('receipt'), reserve=runtime.xlag_address,
                                 proposal=proposal, captain=runtime.wallet.address,
                                 amount=plan['payout_wei'])
        if _read_uint(rpc, runtime.xlag_address, SELECTORS['state'], proposal) != 1:
            raise SiphonRefused('reserve_proposal_not_pending')
        beta_runtime = replace(runtime, wallet=beta)
        seconded = send_captain_contract_transaction(
            beta_runtime, contract_address=runtime.xlag_address, private_key=beta.private_key,
            function_signature='secondPayout(uint256)', function_args=[str(proposal)], timeout_s=30)
        _save_lock(lock, {'operation': operation_id, 'status': 'seconded',
                          'proposal': proposal, 'tx_hash': seconded['transaction_hash']})
        cutoff = time.monotonic() + max(30.0, timeout_s)
        creation_block = int(proposed['block_number'])
        while int(rpc.rpc('eth_blockNumber'), 16) < creation_block + delay:
            if time.monotonic() >= cutoff:
                raise SiphonRefused('reserve_payout_delay_pending')
            time.sleep(2)
        if _read_uint(rpc, runtime.xlag_address, SELECTORS['state'], proposal) != 1:
            raise SiphonRefused('reserve_payout_belayed_contested_or_expired')
        _save_lock(lock, {'operation': operation_id, 'status': 'executing', 'proposal': proposal})
        executed = send_captain_contract_transaction(
            runtime, contract_address=runtime.xlag_address, private_key=runtime.wallet.private_key,
            function_signature='executePayout(uint256)', function_args=[str(proposal)], timeout_s=30)
        _save_lock(lock, {'operation': operation_id, 'status': 'reserve_paid',
                          'proposal': proposal, 'tx_hash': executed['transaction_hash']})
        if _read_uint(rpc, runtime.xlag_address, SELECTORS['state'], proposal) != 4:
            raise SiphonRefused('reserve_payout_not_executed')
        # No historical two-credit cap applies to this explicitly reserve-funded
        # deposit. The bridge still proves its on-chain value and credits once.
        deposit = send_captain_bridge_deposit(runtime,
                                              deposit_credit_wei=plan['top_up_wei'],
                                              smoke_id=operation_id, timeout_s=30)
        deposit = {**deposit, 'wallet_address': runtime.wallet.address,
                   'amount_credit_wei': str(plan['top_up_wei'])}
        _save_captain_pending_bridge(pending, deposit)
        _save_lock(lock, {'operation': operation_id, 'status': 'bridge_pending',
                          'proposal': proposal, 'deposit_id': deposit['deposit_id'],
                          'deposit_tx_hash': deposit['transaction_hash']})
        completion = _complete_captain_bridge_deposit(runtime, deposit, timeout_s=30)
        if completion.get('ok') is not True:
            raise SiphonRefused('hub_bridge_completion_not_confirmed')
        final = _fetch_hub_credit_balance(runtime.config.hub_url,
                                          wallet_address=runtime.wallet.address, timeout_s=15)
        final_plan = plan_siphon(final, wallet=runtime.wallet.address, required_wei=required_wei)
        if final_plan['needed']:
            raise SiphonRefused('hub_still_underfunded_after_completion')
        _write_event(folder, {'operation': operation_id, 'status': 'completed',
                              'proposal': proposal, 'deposit_id': deposit['deposit_id']})
        pending.unlink(missing_ok=True)
        lock.unlink()
        return {'ok': True, 'status': 'funded', 'topUpCreditWei': str(plan['top_up_wei']),
                'reservePayoutWei': str(plan['payout_wei']), 'proposalId': proposal,
                'depositId': deposit['deposit_id']}
    except Exception:
        # Do not remove the lock: it prevents a blind second payout if the
        # first call was included on-chain but the HTTP receipt was lost.
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description='Bounded, testnet-only reserve-funded Captain credit replenishment')
    ap.add_argument('--required-credit-wei', type=int, required=True)
    ap.add_argument('--execute', action='store_true', help='Authorize one capped governance payout and escrow deposit')
    args = ap.parse_args()
    try:
        result = run(root=Path(__file__).resolve().parents[1],
                     required_wei=args.required_credit_wei, execute=args.execute)
    except (SiphonRefused, CaptainCliError, FundingEvidenceError) as exc:
        # Error codes only: never expose Mother private-state paths or keys.
        reason = str(exc) if isinstance(exc, SiphonRefused) else 'reserve_funding_unavailable'
        result = {'ok': False, 'reason': reason}
    except Exception:
        result = {'ok': False, 'reason': 'reserve_funding_execution_uncertain_check_state'}
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get('ok') else 2


if __name__ == '__main__':
    raise SystemExit(main())
