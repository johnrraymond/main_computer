"""Terminal archives are separate from the active Captain recovery state machine."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer.captain_bridge_archive import (
    CaptainArchiveError, archive_path_for_pending, archive_terminal_orphan,
)
from main_computer.captain_cli import (
    CaptainCliError, _read_captain_pending_bridge,
    _require_no_other_captain_pending_deposits, _save_captain_pending_bridge,
)

TX = '0x' + 'a1' * 32
ID = '0x' + 'b2' * 32
WALLET = '0x' + 'c3' * 20
ESCROW = '0x' + 'd4' * 20
BLOCK_HASH = '0x' + '1a' * 32
CURRENT_HASH = '0x' + '2b' * 32
AMOUNT = '1000000000000000000'
NAME = 'd' * 64 + '.json'


def fixture(tmp_path):
    parent = tmp_path / 'runtime/state/captain/pending-bridge-deposits'
    parent.mkdir(parents=True)
    path = parent / NAME
    record = {
        'deposit_id': ID, 'transaction_hash': TX, 'wallet_address': WALLET,
        'amount_credit_wei': AMOUNT,
        'receipt': {'status': '0x1', 'blockNumber': '0x5a9', 'blockHash': BLOCK_HASH},
    }
    path.write_text(json.dumps(record), encoding='utf-8')
    row = {
        'file': NAME, 'depositId': ID, 'transactionHash': TX,
        'walletAddress': WALLET, 'creditWei': AMOUNT,
        'chainProof': 'not-verified',
        'hubDepositAttribution': 'not_recorded_in_hub_fdb',
        'reason': 'transaction_receipt_absent_from_live_chain',
        'missingReceiptEvidence': {
            'recordedBlockCanonical': 'different_canonical_block',
            'recordedReceiptBlockNumber': 1449, 'currentHeadBlockNumber': 82463,
            'recordedReceiptBlockHash': BLOCK_HASH,
            'currentBlockHashAtReceiptHeight': CURRENT_HASH,
            'transactionLookup': 'absent_from_current_rpc',
            'escrowDepositState': {'exists': False, 'completed': False,
                                   'walletMatches': False, 'amountMatches': False},
            'permitsNewDeposit': False,
        },
    }
    audit = {'readOnly': True, 'network': 'testnet', 'chainId': 42424241,
             'escrow': ESCROW, 'records': [row],
             'hubAccountAggregates': {WALLET: {'spent_credit_wei': AMOUNT}}}
    return path, record, row, audit


def test_preview_preserves_pending_and_does_not_make_archive(tmp_path):
    path, _, row, audit = fixture(tmp_path)
    prior = path.read_bytes()
    result = archive_terminal_orphan(pending=path, audit_row=row, audit=audit,
                                     network='testnet', execute=False)
    assert result['mutated'] is False
    assert path.read_bytes() == prior
    assert not archive_path_for_pending(path).exists()


def test_execute_moves_out_of_operational_recovery_and_writes_certificate(tmp_path):
    path, record, row, audit = fixture(tmp_path)
    original = path.read_bytes()
    result = archive_terminal_orphan(pending=path, audit_row=row, audit=audit,
                                     network='testnet', execute=True)
    archived = archive_path_for_pending(path)
    assert not path.exists()
    assert archived.exists()
    cert = json.loads(archived.read_text(encoding='utf-8'))
    assert cert['classification'] == 'orphaned_by_chain_history_replacement'
    assert cert['pendingSha256'] == hashlib.sha256(original).hexdigest()
    assert cert['originalPendingRecord'] == record
    assert cert['authorizesNewDeposit'] is False
    assert cert['financialAccounting'] == 'not_reconciled_by_this_archive'
    assert cert['chainAndHubProof'] == row
    assert result['certificateSha256'] == hashlib.sha256(archived.read_bytes()).hexdigest()
    _require_no_other_captain_pending_deposits(path.parent / ('f'*64+'.json'),wallet_address=WALLET)


def test_archived_smoke_id_cannot_resurrect_or_replay(tmp_path):
    path, record, row, audit = fixture(tmp_path)
    archive_terminal_orphan(pending=path, audit_row=row, audit=audit,
                            network='testnet', execute=True)
    runtime = SimpleNamespace(wallet=SimpleNamespace(address=WALLET))
    with pytest.raises(CaptainCliError, match='terminal archived'):
        _read_captain_pending_bridge(path, runtime=runtime, amount_wei=int(AMOUNT))
    with pytest.raises(CaptainCliError, match='archived terminal'):
        _save_captain_pending_bridge(path, record)
    assert not path.exists()


@pytest.mark.parametrize('field,value', [
    ('recordedBlockCanonical', 'matches'),
    ('transactionLookup','known_on_current_rpc'),
    ('escrowDepositState',{'exists': True, 'completed': False}),
    ('recordedReceiptBlockHash',CURRENT_HASH),
    ('currentHeadBlockNumber',100),
])
def test_weak_chain_evidence_does_not_archive(tmp_path, field, value):
    path, _, row, audit = fixture(tmp_path)
    row['missingReceiptEvidence'][field] = value
    with pytest.raises(CaptainArchiveError):
        archive_terminal_orphan(pending=path, audit_row=row, audit=audit,
                                network='testnet', execute=True)
    assert path.exists()
    assert not archive_path_for_pending(path).exists()


@pytest.mark.parametrize('field,value', [
    ('hubDepositAttribution','historical_fdb_credit_verified'),
    ('hubDepositAttribution','exact_lookup_unavailable'),
    ('reason','wrong_chain'),
    ('transactionHash','0x'+'ff'*32),
])
def test_weak_hub_or_identity_evidence_does_not_archive(tmp_path, field, value):
    path, _, row, audit = fixture(tmp_path)
    row[field] = value
    with pytest.raises(CaptainArchiveError):
        archive_terminal_orphan(pending=path, audit_row=row, audit=audit,
                                network='testnet', execute=True)
    assert path.exists()


def test_mainnet_cannot_auto_archive(tmp_path):
    path, _, row, audit = fixture(tmp_path)
    with pytest.raises(CaptainArchiveError, match='testnet'):
        archive_terminal_orphan(pending=path, audit_row=row, audit=audit,
                                network='mainnet', execute=True)
    assert path.exists()


def test_changed_pending_record_before_archive_is_preserved(tmp_path):
    path, _, row, audit = fixture(tmp_path)
    data = json.loads(path.read_text())
    data['transaction_hash'] = '0x'+'ff'*32
    path.write_text(json.dumps(data))
    with pytest.raises(CaptainArchiveError):
        archive_terminal_orphan(pending=path, audit_row=row, audit=audit,
                                network='testnet', execute=True)
    assert path.exists()


def test_conflicting_existing_archive_fails_closed(tmp_path):
    path, _, row, audit = fixture(tmp_path)
    archive = archive_path_for_pending(path)
    archive.parent.mkdir(parents=True)
    archive.write_text('{"evidence":"other"}')
    with pytest.raises(CaptainArchiveError, match='conflicts'):
        archive_terminal_orphan(pending=path, audit_row=row, audit=audit,
                                network='testnet', execute=True)
    assert path.exists()
    assert archive.read_text() == '{"evidence":"other"}'


def test_script_preview_then_execute(tmp_path):
    path, _, row, audit = fixture(tmp_path)
    filename = Path(__file__).resolve().parents[1] / 'tools/worker_browser_acceptance/captain_archive_orphaned.py'
    spec = importlib.util.spec_from_file_location('captain_archive_entrypoint_test', filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    preview = module.archive_from_fresh_audit(root=tmp_path, audit=audit)
    assert preview['archivedCount'] == 0 and preview['activePendingCount'] == 1
    actual = module.archive_from_fresh_audit(root=tmp_path, audit=audit, execute=True)
    assert actual['archivedCount'] == 1 and actual['activePendingCount'] == 0
    assert actual['financialAccountingUnchanged'] is True
    assert path.exists() is False


def test_other_pending_wallet_still_blocks(tmp_path):
    path, _, row, audit = fixture(tmp_path)
    archive_terminal_orphan(pending=path, audit_row=row, audit=audit, network='testnet', execute=True)
    other = path.parent / ('1'*64 + '.json')
    other.write_text(json.dumps({'deposit_id': ID, 'transaction_hash': TX,
                                 'wallet_address': WALLET}))
    with pytest.raises(CaptainCliError, match='unresolved bridge deposit'):
        _require_no_other_captain_pending_deposits(path.parent / ('2'*64 + '.json'),wallet_address=WALLET)


def test_retry_after_certificate_written_but_before_pending_removed(tmp_path):
    path, _, row, audit = fixture(tmp_path)
    original = path.read_bytes()
    result = archive_terminal_orphan(pending=path, audit_row=row, audit=audit,
                                     network='testnet', execute=True)
    cert = archive_path_for_pending(path)
    saved = cert.read_bytes()
    # Model interruption after certificate publication: reintroduce only the
    # exact previously verified pending bytes.
    path.write_bytes(original)
    audit['records'][0]['missingReceiptEvidence']['currentHeadBlockNumber'] += 100
    retry = archive_terminal_orphan(pending=path, audit_row=audit['records'][0],
                                    audit=audit, network='testnet', execute=True)
    assert retry['mutated'] is True
    assert not path.exists()
    assert cert.read_bytes() == saved
    assert retry['certificateSha256'] == result['certificateSha256']


def test_unverified_existing_archive_cannot_clear_pending(tmp_path):
    path, _, row, audit = fixture(tmp_path)
    old = archive_path_for_pending(path)
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps({'schema': 'main-computer.captain-terminal-bridge-archive.v1',
                               'classification': 'orphaned_by_chain_history_replacement',
                               'pendingSha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                               'pendingFilename': path.name,
                               'identity': {key: json.loads(path.read_text())[key]
                                            for key in ('deposit_id','transaction_hash','wallet_address','amount_credit_wei')},
                               'originalPendingRecord': json.loads(path.read_text()),
                               'network': 'testnet','chainId': 42424241,'escrow': ESCROW,
                               'chainAndHubProof': {'hubDepositAttribution': 'exact_lookup_unavailable'}}))
    with pytest.raises(CaptainArchiveError, match='conflicts'):
        archive_terminal_orphan(pending=path, audit_row=row, audit=audit,
                                network='testnet', execute=True)
    assert path.exists()
