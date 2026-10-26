#!/usr/bin/env python3
"""Read-only inspection of Captain's pending testnet bridge-deposit records.

No signing, sends, funding completion POSTs, deletion, or recovery mutation.
Chain verification is distinct from exact Hub-ledger attribution: Stable Hub's
current public GET endpoints expose only account aggregates, not deposit IDs.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main_computer.hub_networks import load_hub_network_registry
from main_computer.hub_credit_bridge_completion import (
    JsonRpcClient, _DEPOSIT_RECORD_SELECTOR, _split_words,
)
from main_computer.captain_cli import _get_hub_json

_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
_HASH = re.compile(r"0x[0-9a-fA-F]{64}\Z")


def _record_data(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError('invalid_local_record')
    values = {k: str(raw.get(k) or '') for k in
              ('deposit_id', 'transaction_hash', 'wallet_address', 'amount_credit_wei')}
    if not _HASH.fullmatch(values['deposit_id']) or not _HASH.fullmatch(values['transaction_hash']):
        raise ValueError('invalid_local_deposit_or_transaction_hash')
    if not _ADDRESS.fullmatch(values['wallet_address']):
        raise ValueError('invalid_local_wallet')
    if not values['amount_credit_wei'].isdigit() or int(values['amount_credit_wei']) <= 0:
        raise ValueError('invalid_local_amount')
    receipt = raw.get('receipt')
    if isinstance(receipt, dict):
        values['receipt'] = {key: receipt[key] for key in ('blockNumber', 'blockHash') if key in receipt}
    return values


def _address_word(address: str) -> str:
    return '0x' + address[2:].lower().rjust(64, '0')


def _verify_receipt_event(receipt: dict[str, Any], record: dict[str, str], contract: str) -> bool:
    # Match the same ABI event signature verified by the Hub's completion logic.
    from eth_utils import keccak
    topic = '0x' + keccak(text='CreditDeposited(bytes32,address,address,uint256,string)').hex()
    expected_topics = [topic.lower(), record['deposit_id'].lower(),
                       _address_word(record['wallet_address']), _address_word(record['wallet_address'])]
    for log in receipt.get('logs') or []:
        if not isinstance(log, dict) or str(log.get('address') or '').lower() != contract.lower():
            continue
        topics = [str(t).lower() for t in log.get('topics') or []]
        data = str(log.get('data') or '')
        if (topics == expected_topics and data.startswith('0x') and len(data) >= 66
                and int(data[2:66], 16) == int(record['amount_credit_wei'])):
            return True
    return False


def _read_only_missing_receipt_evidence(
    record: dict[str, str], *, rpc: JsonRpcClient, contract: str,
) -> dict[str, Any]:
    """Independently test historical block, transaction, and escrow state.

    Every call is read-only. A historical mismatch is evidence of history drift,
    not permission to delete a saved receipt or deposit again. If one RPC
    query fails, preserve the failure category without leaking RPC content.
    """
    evidence: dict[str, Any] = {
        'transactionLookup': 'unverified',
        'recordedBlockCanonical': 'unverified',
        'escrowDepositState': 'unverified',
        'permitsNewDeposit': False,
    }
    saved_receipt = record.get('receipt') if isinstance(record.get('receipt'), dict) else {}
    recorded_number = saved_receipt.get('blockNumber')
    recorded_hash = str(saved_receipt.get('blockHash') or '').lower()
    try:
        height = int(str(recorded_number), 16) if isinstance(recorded_number, str) and recorded_number.startswith('0x') else int(recorded_number)
        if height < 0: raise ValueError('negative block height')
        evidence['recordedReceiptBlockNumber'] = height
    except (ValueError, TypeError):
        height = None
    if _HASH.fullmatch(recorded_hash):
        evidence['recordedReceiptBlockHash'] = recorded_hash
    try:
        tip = rpc.rpc('eth_blockNumber', [])
        current_height = int(str(tip), 16)
        if current_height < 0: raise ValueError('negative height')
        evidence['currentHeadBlockNumber'] = current_height
        if height is not None:
            if current_height < height:
                evidence['recordedBlockCanonical'] = 'current_head_below_recorded_receipt'
            else:
                block = rpc.rpc('eth_getBlockByNumber', [hex(height), False])
                if not isinstance(block, dict):
                    evidence['recordedBlockCanonical'] = 'block_unavailable_from_rpc'
                elif _HASH.fullmatch(recorded_hash):
                    current_hash = str(block.get('hash') or '').lower()
                    if _HASH.fullmatch(current_hash):
                        evidence['currentBlockHashAtReceiptHeight'] = current_hash
                        evidence['recordedBlockCanonical'] = (
                            'matches' if current_hash == recorded_hash else 'different_canonical_block'
                        )
                    else:
                        evidence['recordedBlockCanonical'] = 'block_hash_unavailable'
                else:
                    evidence['recordedBlockCanonical'] = 'saved_receipt_missing_block_hash'
    except (ValueError, TypeError, RuntimeError, KeyError):
        evidence['recordedBlockCanonical'] = 'read_only_block_lookup_unavailable'
    try:
        tx = rpc.rpc('eth_getTransactionByHash', [record['transaction_hash']])
        evidence['transactionLookup'] = (
            'known_on_current_rpc' if isinstance(tx, dict) else
            'absent_from_current_rpc' if tx is None else 'unexpected_rpc_response'
        )
    except (ValueError, TypeError, RuntimeError, KeyError):
        evidence['transactionLookup'] = 'read_only_transaction_lookup_unavailable'
    try:
        raw = rpc.eth_call(to=contract, data='0x' + _DEPOSIT_RECORD_SELECTOR + record['deposit_id'][2:])
        words = _split_words(raw, min_words=5)
        exists, completed = bool(int(words[0], 16)), bool(int(words[1], 16))
        evidence['escrowDepositState'] = {
            'exists': exists, 'completed': completed,
            'walletMatches': ('0x' + words[2][-40:]).lower() == record['wallet_address'].lower()
                and ('0x' + words[3][-40:]).lower() == record['wallet_address'].lower(),
            'amountMatches': int(words[4], 16) == int(record['amount_credit_wei']),
        }
    except (ValueError, TypeError, RuntimeError, KeyError):
        evidence['escrowDepositState'] = 'read_only_escrow_lookup_unavailable'
    return evidence


def audit_record(record: dict[str, str], *, rpc: JsonRpcClient,
                 contract: str, chain_id: int) -> dict[str, Any]:
    # No write methods. Any missing/contradictory proof leaves the guard intact.
    result: dict[str, Any] = {
        'depositId': record['deposit_id'], 'transactionHash': record['transaction_hash'],
        'walletAddress': record['wallet_address'], 'creditWei': record['amount_credit_wei'],
        'chainProof': 'not-verified', 'hubDepositAttribution': 'unverified',
    }
    if rpc.chain_id() != chain_id:
        result['reason'] = 'live_rpc_chain_mismatch'; return result
    code = rpc.rpc('eth_getCode', [contract, 'latest'])
    if not isinstance(code, str) or code in {'0x', '0x0', ''}:
        result['reason'] = 'escrow_contract_code_missing'; return result
    receipt = rpc.transaction_receipt(record['transaction_hash'])
    if not isinstance(receipt, dict):
        result['reason'] = 'transaction_receipt_absent_from_live_chain'
        result['missingReceiptEvidence'] = _read_only_missing_receipt_evidence(
            record, rpc=rpc, contract=contract
        )
        return result
    result['liveReceiptBlock'] = str(receipt.get('blockNumber') or '')
    if (str(receipt.get('status') or '').lower() != '0x1'
            or str(receipt.get('transactionHash') or '').lower() != record['transaction_hash'].lower()
            or str(receipt.get('to') or '').lower() != contract.lower()):
        result['reason'] = 'receipt_status_hash_or_escrow_mismatch'; return result
    tx = rpc.rpc('eth_getTransactionByHash', [record['transaction_hash']])
    if (not isinstance(tx, dict)
            or str(tx.get('from') or '').lower() != record['wallet_address'].lower()
            or str(tx.get('to') or '').lower() != contract.lower()
            or int(str(tx.get('value') or '0x0'), 16) != int(record['amount_credit_wei'])):
        result['reason'] = 'funding_transaction_identity_or_amount_mismatch'; return result
    words = _split_words(rpc.eth_call(to=contract, data='0x' + _DEPOSIT_RECORD_SELECTOR + record['deposit_id'][2:]), min_words=5)
    account = '0x' + words[2][-40:]
    payer = '0x' + words[3][-40:]
    amount = int(words[4], 16)
    result['escrowRecord'] = {
        'exists': bool(int(words[0], 16)), 'completed': bool(int(words[1], 16)),
        'walletMatches': account.lower() == record['wallet_address'].lower()
                         and payer.lower() == record['wallet_address'].lower(),
        'amountMatches': amount == int(record['amount_credit_wei']),
    }
    if not all((result['escrowRecord']['exists'], result['escrowRecord']['walletMatches'],
                result['escrowRecord']['amountMatches'])):
        result['reason'] = 'escrow_deposit_record_mismatch'; return result
    if not _verify_receipt_event(receipt, record, contract):
        result['reason'] = 'escrow_deposit_event_not_proven'; return result
    result['chainProof'] = 'verified-on-current-chain'
    result['reason'] = 'hub_exact_deposit_ledger_record_not_exposed_by_read_only_API'
    return result


def inspect_hub_historical_deposit(
    record: dict[str, Any], *, hub_url: str, chain_id: int, contract: str,
    hub_get, timeout_s: int,
) -> dict[str, Any]:
    """Correlate one saved funding transaction with an FDB deposit record.

    A matching historical record proves Hub-ledger attribution, NOT present-day
    chain validity or permission to retire/redeposit. Keep every query read-only.
    """
    from urllib.parse import urlencode
    route = '/api/hub/v1/credits/bridge-deposit?' + urlencode({
        'chain_id': str(chain_id), 'contract_address': contract,
        'wallet_address': record['wallet_address'], 'deposit_id': record['deposit_id'],
    })
    try:
        response = hub_get(hub_url, route, timeout_s=timeout_s)
    except (ValueError, RuntimeError, OSError, KeyError):
        return {'hubDepositAttribution': 'exact_lookup_unavailable'}
    if not isinstance(response, dict) or response.get('ok') is not True or response.get('readOnly') is not True:
        return {'hubDepositAttribution': 'exact_lookup_unverified'}
    expected = {
        'deposit_id': record['deposit_id'].lower(),
        'wallet_address': record['wallet_address'].lower(),
        'contract_address': contract.lower(),
        'chain_id': str(chain_id),
    }
    if any(str(response.get(field) or '').lower() != value for field, value in expected.items()):
        return {'hubDepositAttribution': 'exact_lookup_identity_conflict'}
    if response.get('found') is False:
        return {'hubDepositAttribution': 'not_recorded_in_hub_fdb'}
    if response.get('found') is not True:
        return {'hubDepositAttribution': 'exact_lookup_unverified'}
    credit = response.get('credit')
    if credit is None and response.get('completed') is False:
        return {'hubDepositAttribution': 'prepared_only_in_hub_fdb',
                'hubDepositIntentStatus': str((response.get('intent') or {}).get('status') or '')}
    if not isinstance(credit, dict) or response.get('completed') is not True:
        return {'hubDepositAttribution': 'exact_lookup_unverified'}
    matched = (str(credit.get('status') or '') == 'bridge_completed'
               and str(credit.get('amount_credit_wei') or '') == record['amount_credit_wei']
               and str(credit.get('credits_granted_wei') or '') == record['amount_credit_wei']
               and str(credit.get('funding_tx_hash') or '').lower() == record['transaction_hash'].lower())
    return {
        'hubDepositAttribution': 'historical_fdb_credit_verified' if matched else 'hub_deposit_credit_conflict',
        'hubDepositRecord': {
            'status': str(credit.get('status') or ''),
            'creditedAmountMatches': str(credit.get('credits_granted_wei') or '') == record['amount_credit_wei'],
            'fundingTransactionMatches': str(credit.get('funding_tx_hash') or '').lower() == record['transaction_hash'].lower(),
            'creditedAt': str(credit.get('created_at') or ''),
            'completionTransactionHash': str(credit.get('completion_tx_hash') or ''),
        },
    }


def audit_pending(*, root: Path = ROOT, network: str = 'testnet', timeout_s: int = 15,
                  rpc: JsonRpcClient | None = None, hub_get=None) -> dict[str, Any]:
    # Never auto-select mainnet. It must be requested explicitly.
    profile = load_hub_network_registry().get(network)
    if not profile.chain_rpc_url or not profile.chain_id:
        raise ValueError('selected_network_has_no_rpc_or_chain_id')
    get = hub_get or _get_hub_json
    hub_url = profile.hub_url
    info = get(hub_url, '/api/hub/v1/credits/wallet-funding/completion', timeout_s=timeout_s)
    deployment = info.get('deployment') if isinstance(info, dict) else None
    if not isinstance(deployment, dict) or info.get('ok') is not True:
        raise ValueError('hub_bridge_completion_identity_unavailable')
    contract = str(deployment.get('contract_address') or '')
    if (not _ADDRESS.fullmatch(contract) or str(deployment.get('chain_id')) != str(profile.chain_id)):
        raise ValueError('hub_bridge_contract_or_chain_identity_mismatch')
    if profile.contracts.get('hub_credit_bridge_escrow') and profile.contracts['hub_credit_bridge_escrow'].lower() != contract.lower():
        raise ValueError('hub_bridge_escrow_differs_from_local_registry')
    client = rpc or JsonRpcClient(profile.chain_rpc_url, timeout_s=timeout_s)
    folder = root / 'runtime/state/captain/pending-bridge-deposits'
    files = sorted(folder.glob('*.json')) if folder.is_dir() else []
    rows: list[dict[str, Any]] = []
    balances: dict[str, Any] = {}
    for path in files:
        try:
            data = _record_data(json.loads(path.read_text(encoding='utf-8')))
            row = {'file': path.name, **audit_record(data, rpc=client, contract=contract, chain_id=profile.chain_id)}
            row.update(inspect_hub_historical_deposit(
                data, hub_url=hub_url, chain_id=profile.chain_id,
                contract=contract, hub_get=get, timeout_s=timeout_s,
            ))
            wallet = data['wallet_address'].lower()
            if wallet not in balances:
                balance = get(hub_url, '/api/hub/v1/credits/balance?' + urlencode({'wallet_address': wallet}), timeout_s=timeout_s)
                account = balance.get('account') if isinstance(balance, dict) else None
                if not isinstance(account, dict) or balance.get('ok') is not True or str(account.get('wallet_address') or '').lower() != wallet:
                    raise ValueError('hub_account_balance_unverified')
                balances[wallet] = {k: str(account.get(k) or '0') for k in
                                    ('available_credit_wei', 'spent_credit_wei', 'bridge_completed_credit_wei')}
        except (ValueError, TypeError, OSError, RuntimeError, KeyError) as exc:
            row = {'file': path.name, 'chainProof': 'not-verified',
                   'hubDepositAttribution': 'not-verifiable-via-current-read-only-hub-api',
                   'reason': type(exc).__name__ + ':read_only_evidence_unavailable'}
        rows.append(row)
    return {
        'schema': 'main-computer.captain-bridge-read-only-audit.v1',
        'readOnly': True, 'network': profile.network_key, 'chainId': profile.chain_id,
        'escrow': contract, 'pendingCount': len(files), 'records': rows,
        'hubAccountAggregates': balances,
        'authorizesRetireOrNewDeposit': False,
        'nextAction': 'retain_pending_records_until_deposit_specific_hub_ledger_proof_and_chain_are_reconciled',
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--network', choices=('testnet', 'mainnet'), default='testnet')
    args = parser.parse_args(argv)
    try:
        report = audit_pending(network=args.network)
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': 'read_only_audit_unavailable',
                          'errorType': type(exc).__name__, 'authorizesRetireOrNewDeposit': False}, indent=2))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report['pendingCount'] == 0 else 3  # Records remain pending regardless of findings.


if __name__ == '__main__':
    raise SystemExit(main())
