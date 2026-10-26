"""Terminal, hash-verified archives for orphaned Captain bridge recovery hints.

A terminal record is historical evidence, never a payment authorization or
Hub-ledger reconciliation. The active pending directory is the only operational
recovery queue; this separate archive is excluded from all wallet pending scans.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any


_HEX32 = re.compile(r'0x[0-9a-fA-F]{64}\Z')
_ADDR = re.compile(r'0x[0-9a-fA-F]{40}\Z')
_PENDING_NAME = re.compile(r'[0-9a-f]{64}\.json\Z')
_CLASS = 'orphaned_by_chain_history_replacement'


class CaptainArchiveError(ValueError):
    """A proof, record, or archival publication was not verified."""


def archive_path_for_pending(pending: Path) -> Path:
    return pending.parent.parent / 'archived-bridge-deposits' / pending.name


def _read_pending(path: Path) -> tuple[bytes, dict[str, Any]]:
    if not _PENDING_NAME.fullmatch(path.name) or path.is_symlink():
        raise CaptainArchiveError('unsafe_pending_record_path')
    raw = path.read_bytes()
    try:
        obj = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptainArchiveError('unreadable_pending_record') from exc
    if not isinstance(obj, dict):
        raise CaptainArchiveError('invalid_pending_record')
    if not (_HEX32.fullmatch(str(obj.get('transaction_hash') or ''))
            and _HEX32.fullmatch(str(obj.get('deposit_id') or ''))
            and _ADDR.fullmatch(str(obj.get('wallet_address') or ''))
            and str(obj.get('amount_credit_wei') or '').isdigit()
            and int(obj['amount_credit_wei']) > 0):
        raise CaptainArchiveError('invalid_pending_transaction_identity')
    receipt = obj.get('receipt')
    if not isinstance(receipt, dict) or str(receipt.get('status') or '').lower() != '0x1':
        raise CaptainArchiveError('no_successful_historical_receipt_recorded')
    return raw, obj


def classify_terminal_orphan(*, record: dict[str, Any], audit_row: dict[str, Any],
                             audit: dict[str, Any], network: str) -> None:
    """Fail closed unless independent chain and exact-Hub evidence agree.

    This proves a historical chain orphan, NOT that Hub's aggregate balance is
    reconciled or that another transfer is safe without separate authorization.
    """
    if network != 'testnet' or audit.get('network') != 'testnet' or audit.get('readOnly') is not True:
        raise CaptainArchiveError('terminal_archive_requires_read_only_testnet_proof')
    if not isinstance(audit.get('chainId'), int) or audit['chainId'] <= 0:
        raise CaptainArchiveError('unverified_chain_identity')
    if not _ADDR.fullmatch(str(audit.get('escrow') or '')):
        raise CaptainArchiveError('unverified_escrow_identity')
    if not isinstance(audit_row, dict):
        raise CaptainArchiveError('missing_deposit_specific_evidence')
    if any(str(audit_row.get(a) or '').lower() != str(record.get(b) or '').lower()
           for a, b in (('depositId','deposit_id'), ('transactionHash','transaction_hash'),
                        ('walletAddress','wallet_address'), ('creditWei','amount_credit_wei'))):
        raise CaptainArchiveError('audit_and_pending_record_identity_conflict')
    if (audit_row.get('chainProof') != 'not-verified'
            or audit_row.get('reason') != 'transaction_receipt_absent_from_live_chain'
            or audit_row.get('hubDepositAttribution') != 'not_recorded_in_hub_fdb'):
        raise CaptainArchiveError('insufficient_chain_or_hub_orphan_evidence')
    evidence = audit_row.get('missingReceiptEvidence')
    if not isinstance(evidence, dict):
        raise CaptainArchiveError('missing_chain_history_evidence')
    old_hash = str(evidence.get('recordedReceiptBlockHash') or '')
    canonical_hash = str(evidence.get('currentBlockHashAtReceiptHeight') or '')
    if not (_HEX32.fullmatch(old_hash) and _HEX32.fullmatch(canonical_hash)
            and old_hash.lower() != canonical_hash.lower()
            and evidence.get('recordedBlockCanonical') == 'different_canonical_block'):
        raise CaptainArchiveError('canonical_block_replacement_not_verified')
    try:
        recorded_height = int(evidence['recordedReceiptBlockNumber'])
        current_head = int(evidence['currentHeadBlockNumber'])
        receipt_height = int(str(record['receipt']['blockNumber']), 16)
    except (ValueError, TypeError, KeyError) as exc:
        raise CaptainArchiveError('invalid_block_height_evidence') from exc
    if recorded_height < 0 or current_head < recorded_height or receipt_height != recorded_height:
        raise CaptainArchiveError('recorded_receipt_height_mismatch')
    # A forged mismatch between saved receipt block and proof must not retire an active deposit.
    saved_hash = str(record['receipt'].get('blockHash') or '')
    if saved_hash and saved_hash.lower() != old_hash.lower():
        raise CaptainArchiveError('receipt_block_hash_mismatch')
    if evidence.get('transactionLookup') != 'absent_from_current_rpc':
        raise CaptainArchiveError('transaction_still_known_or_rpc_unavailable')
    escrow = evidence.get('escrowDepositState')
    if not isinstance(escrow, dict) or escrow.get('exists') is not False or escrow.get('completed') is not False:
        raise CaptainArchiveError('escrow_deposit_not_proven_absent')


def archive_terminal_orphan(*, pending: Path, audit_row: dict[str, Any],
                            audit: dict[str, Any], network: str,
                            execute: bool = False) -> dict[str, Any]:
    """Validate, exclusive-create a durable certificate, verify, then dequeue.

    On a crash after publishing the archive, a retry verifies identical saved
    evidence and finishes dequeuing. On uncertainty, the pending hint remains.
    """
    raw, record = _read_pending(pending)
    classify_terminal_orphan(record=record, audit_row=audit_row, audit=audit, network=network)
    dest = archive_path_for_pending(pending)
    evidence = {
        'schema': 'main-computer.captain-terminal-bridge-archive.v1',
        'classification': _CLASS,
        'operationalStatus': 'terminal-archived',
        'financialAccounting': 'not_reconciled_by_this_archive',
        'authorizesNewDeposit': False,
        'network': network,
        'chainId': audit['chainId'],
        'escrow': audit['escrow'],
        'identity': {key: record[key] for key in
                     ('deposit_id','transaction_hash','wallet_address','amount_credit_wei')},
        'pendingFilename': pending.name,
        'pendingSha256': hashlib.sha256(raw).hexdigest(),
        'originalPendingRecord': record,
        'chainAndHubProof': audit_row,
        'hubAccountAggregatesAtArchive': audit.get('hubAccountAggregates', {}),
    }
    payload = (json.dumps(evidence, sort_keys=True, indent=2) + '\n').encode('utf-8')
    summary = {
        'classification': _CLASS, 'pending': pending.name,
        'archive': str(dest), 'transactionHash': record['transaction_hash'],
        'depositId': record['deposit_id'], 'certificateSha256': hashlib.sha256(payload).hexdigest(),
        'mutated': False, 'financialAccounting': 'separate_unresolved_history',
    }
    if not execute:
        return summary
    if dest.is_symlink():
        raise CaptainArchiveError('archive_symlink_refused')
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        # Crash recovery: a previously published certificate can have a lower
        # observed chain head. Do not rewrite it, but validate its saved proof
        # and identity before finishing the pending-file dequeue.
        saved = dest.read_bytes()
        try:
            previous = json.loads(saved)
            if (not isinstance(previous, dict)
                    or previous.get('schema') != evidence['schema']
                    or previous.get('classification') != _CLASS
                    or previous.get('pendingSha256') != evidence['pendingSha256']
                    or previous.get('pendingFilename') != pending.name
                    or previous.get('identity') != evidence['identity']
                    or previous.get('originalPendingRecord') != record
                    or previous.get('network') != network
                    or previous.get('chainId') != audit['chainId']
                    or str(previous.get('escrow') or '').lower() != str(audit['escrow']).lower()):
                raise CaptainArchiveError('existing_archive_evidence_conflicts_with_current_audit')
            old_audit = {
                'readOnly': True, 'network': previous['network'],
                'chainId': previous['chainId'], 'escrow': previous['escrow'],
            }
            classify_terminal_orphan(
                record=record, audit_row=previous['chainAndHubProof'],
                audit=old_audit, network=network,
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise CaptainArchiveError('existing_archive_evidence_conflicts_with_current_audit') from exc
        summary['certificateSha256'] = hashlib.sha256(saved).hexdigest()
    else:
        try:
            with dest.open('xb') as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            raise CaptainArchiveError('concurrent_archive_conflict')
    if hashlib.sha256(dest.read_bytes()).hexdigest() != summary['certificateSha256']:
        raise CaptainArchiveError('archive_verification_failed_pending_preserved')
    if pending.is_symlink() or pending.read_bytes() != raw:
        raise CaptainArchiveError('pending_changed_during_archival_preserved')
    pending.unlink()
    summary['mutated'] = True
    return summary
