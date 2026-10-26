#!/usr/bin/env python3
"""Archive conclusively orphaned Testnet Captain deposits out of active recovery.

Preview (default) and --execute both fetch fresh, read-only Hub and chain proof.
No signing, deposit, refund, Hub ledger mutation, or automatic replacement funding.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main_computer.captain_bridge_archive import archive_terminal_orphan, CaptainArchiveError
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from captain_bridge_audit import audit_pending


def archive_from_fresh_audit(*, root: Path = ROOT, network: str = 'testnet',
                             execute: bool = False, audit=None) -> dict:
    if network != 'testnet':
        raise CaptainArchiveError('automatic_terminal_archival_is_testnet_only')
    report = audit if audit is not None else audit_pending(root=root, network=network)
    if report.get('readOnly') is not True or report.get('network') != network:
        raise CaptainArchiveError('invalid_read_only_proof')
    folder = root / 'runtime/state/captain/pending-bridge-deposits'
    responses = []
    for row in report.get('records', []):
        filename = str(row.get('file') or '')
        try:
            response = archive_terminal_orphan(
                pending=folder / filename, audit_row=row, audit=report,
                network=network, execute=execute,
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            response = {'pending': filename, 'mutated': False, 'status': 'kept-active',
                        'reason': str(exc) if isinstance(exc, CaptainArchiveError)
                        else type(exc).__name__}
        responses.append(response)
    remaining = len(list(folder.glob('*.json'))) if folder.exists() else 0
    return {
        'schema': 'main-computer.captain-terminal-archive-run.v1',
        'network': network, 'execute': execute,
        'classification': 'orphaned_by_chain_history_replacement',
        'financialAccountingUnchanged': True,
        'newDepositAuthorized': False,
        'records': responses,
        'archivedCount': sum(x.get('mutated') is True for x in responses),
        'activePendingCount': remaining,
        'allTerminalRecordsArchived': execute and remaining == 0 and bool(responses),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--network', choices=('testnet',), default='testnet')
    p.add_argument('--execute', action='store_true',
                   help='Explicitly archive records with fresh complete proof; default is read-only preview')
    args = p.parse_args(argv)
    try:
        result = archive_from_fresh_audit(network=args.network, execute=args.execute)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({'ok': False, 'error': 'archive_proof_unavailable',
                          'errorType': type(exc).__name__}, indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if (result['activePendingCount'] == 0 or
                 (not args.execute and all('archive' in x for x in result['records']))) else 3


if __name__ == '__main__':
    raise SystemExit(main())
