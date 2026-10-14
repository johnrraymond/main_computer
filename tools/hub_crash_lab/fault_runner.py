"""Lab-only fault hooks. Never imported by normal Hub entry points.

Supports killing the Hub inside an uncommitted FDB transaction or after the
outermost successful commit before the ledger result reaches the HTTP handler.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
from pathlib import Path
import runpy
import threading
import time
from typing import Any

TARGETS = {
    ('stable', 'reserve'): ('main_computer.stable_hub_worker_sessions', 'StableHubPayoutLedgerDirectory', 'create_hold'),
    ('stable', 'charge'): ('main_computer.stable_hub_worker_sessions', 'StableHubPayoutLedgerDirectory', 'spend_request_credit'),
    ('stable', 'claim'): ('main_computer.stable_hub_worker_sessions', 'StableHubPayoutLedgerDirectory', 'record_worker_claim'),
    ('stable', 'batch'): ('main_computer.stable_hub_worker_sessions', 'StableHubPayoutLedgerDirectory', 'create_worker_settlement_batch'),
    ('exp', 'reserve'): ('main_computer.exp_fdb_credit_ledger', 'ExperimentalFoundationDbCreditLedger', 'create_hold_credit_wei'),
    ('exp', 'charge'): ('main_computer.exp_fdb_credit_ledger', 'ExperimentalFoundationDbCreditLedger', 'charge_hold_credit_wei'),
    ('exp', 'claim'): ('main_computer.exp_fdb_credit_ledger', 'ExperimentalFoundationDbCreditLedger', 'record_worker_claim'),
    ('exp', 'batch'): ('main_computer.exp_fdb_credit_ledger', 'ExperimentalFoundationDbCreditLedger', 'create_worker_settlement_batch'),
}
STABLE_STORE_TARGET = ('main_computer.stable_hub_worker_sessions', 'FoundationDbStableWorkerSessionStore')
KIND = {'reserve': ('hold', 'hold_id'), 'charge': ('charge', 'charge_id'),
        'claim': ('claim', 'claim_id'), 'batch': ('batch', 'batch_id')}


def _evidence(lane: str, fault: str, result: dict[str, Any], kwargs: dict[str, Any], *, point: str) -> dict[str, Any]:
    kind, field = KIND[fault]
    # Stable reservation/claim/batch operations return their record directly,
    # whereas spend_request_credit returns an envelope containing ``charge``
    # and ``worker_earning``.  Experimental returns envelopes for all four.
    # Do not mistake a successful Stable charge for a missing record and send
    # a spurious hub.error before the external controller can kill the Hub.
    if lane == 'stable' and fault == 'charge':
        record = result.get('charge')
    elif lane == 'exp':
        record = result.get(kind)
    else:
        record = result
    if not isinstance(record, dict):
        raise RuntimeError(f'Lab fault hook expected {lane} {fault} ledger record')
    record_id = str(record.get(field) or '')
    request_id = str(record.get('request_id') or kwargs.get('request_id') or
                     record.get('worker_node_id') or record.get('worker_id') or
                     kwargs.get('worker_node_id') or kwargs.get('worker_id') or '')
    if not record_id or not request_id:
        raise RuntimeError('Lab fault hook cannot identify the target record')
    return {'lane': lane, 'fault': fault, 'point': point,
            'pid': os.getpid(), 'record_kind': kind, 'record_id': record_id,
            'request_id': request_id,
            'worker_earning_id': str((result.get('worker_earning') or {}).get('earning_id') or record.get('worker_earning_id') or ''),
            'credit_wei': str(record.get('credit_wei') or record.get('charged_credit_wei') or ''),
            'earning_ids': list(record.get('earning_ids') or []),
            'claim_ids': list(record.get('claim_ids') or [])}


def _trace_hook(marker: Path, phase: str, **fields: Any) -> None:
    """Lab-only charge-hook trace, intentionally separate from crash marker."""
    if not marker.parent.exists():
        return
    trace = marker.with_name('fault-hook-trace.jsonl')
    row = {'phase': phase, 'pid': os.getpid(), 'thread_id': threading.get_ident(),
           **fields}
    with trace.open('a', encoding='utf-8') as f:
        f.write(json.dumps(row, sort_keys=True, default=str) + '\n')
        f.flush()
        os.fsync(f.fileno())


def _barrier(marker: Path, evidence: dict[str, Any]) -> None:
    try:
        fd = os.open(str(marker), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        json.dump(evidence, output, sort_keys=True)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
    while True:
        time.sleep(60)


def _commit_barrier(marker: Path, evidence: dict[str, Any]) -> None:
    # Preserve hook seam used by the existing deterministic regression tests.
    _barrier(marker, evidence)


def _install_stable_outer_commit_hook(state: threading.local, marker: Path, *, point: str = 'after_commit') -> None:
    """Wrap outer atomic transaction; never report nested method return as commit."""
    from importlib import import_module
    module_name, class_name = STABLE_STORE_TARGET
    store_class = getattr(import_module(module_name), class_name)
    original_atomic = store_class.atomic

    @functools.wraps(original_atomic)
    def observed_atomic(store: Any, action: Any) -> Any:
        active = getattr(getattr(store, '_transaction_local', None), 'transaction', None)
        if active is not None:
            return original_atomic(store, action)
        previous = getattr(state, 'context', None)
        context: dict[str, Any] = {'pending': None}
        state.context = context

        def tracked_attempt() -> Any:
            context['pending'] = None  # FDB may retry an aborted callback.
            result = action()
            if point == 'before_commit' and context['pending'] is not None:
                _barrier(marker, context['pending'])  # Still inside outer FDB callback.
            return result

        try:
            result = original_atomic(store, tracked_attempt)
            pending = context['pending']
        finally:
            state.context = previous
        if point == 'after_commit' and pending is not None:
            _trace_hook(marker, 'outer_fdb_commit_returned',
                        record_id=pending['record_id'],
                        request_id=pending['request_id'])
            _commit_barrier(marker, pending)
        return result

    setattr(store_class, 'atomic', observed_atomic)


def _install_stable_before_commit_save_hook(marker: Path, fault: str) -> None:
    """Trap the actual document write while the Stable outer FDB tx is open."""
    from importlib import import_module
    cls = getattr(import_module(STABLE_STORE_TARGET[0]), STABLE_STORE_TARGET[1])
    original = cls.save
    collection = 'payout_holds' if fault == 'reserve' else 'payout_charges'
    kind, field = KIND[fault]

    @functools.wraps(original)
    def observed_save(self: Any, data: dict[str, Any]) -> None:
        active = getattr(getattr(self, '_transaction_local', None), 'transaction', None)
        original(self, data)
        if active is None:
            return  # Never claim precommit semantics outside the FDB transaction.
        rows = data.get(collection) or {}
        for item_id, row in rows.items():
            if not isinstance(row, dict) or not row.get('request_id'):
                continue
            _barrier(marker, {'lane': 'stable', 'fault': fault,
                'point': 'before_fdb_commit', 'pid': os.getpid(),
                'record_kind': kind, 'record_id': str(row.get(field) or item_id),
                'request_id': str(row['request_id']),
                'worker_earning_id': str(row.get('worker_earning_id') or '')})
    cls.save = observed_save


def _install_exp_before_commit_write_hook(marker: Path, fault: str) -> None:
    """Pause inside the actual FDB transaction after staging a target write."""
    from importlib import import_module
    module = import_module('main_computer.exp_fdb_credit_ledger')
    cls = module.ExperimentalFoundationDbCreditLedger
    original = cls._write_dict
    kind = 'hold' if fault == 'reserve' else 'charge'
    field = 'hold_id' if fault == 'reserve' else 'charge_id'

    @functools.wraps(original)
    def observed_write(self: Any, tr: Any, written_kind: str, item_id: str,
                       payload: dict[str, Any]) -> None:
        original(self, tr, written_kind, item_id, payload)
        if written_kind != kind or not payload.get('request_id'):
            return
        if fault == 'reserve' and payload.get('status') not in ('held', 'active', 'reserved'):
            return
        if fault == 'charge' and not payload.get('charge_id'):
            return
        _barrier(marker, {
            'lane': 'exp', 'fault': fault, 'point': 'before_fdb_commit',
            'pid': os.getpid(), 'record_kind': kind,
            'record_id': str(payload.get(field) or item_id),
            'request_id': str(payload['request_id']),
            'worker_earning_id': str(payload.get('worker_earning_id') or ''),
        })
    cls._write_dict = observed_write


def install(lane: str, fault: str, marker: Path, *, point: str = 'after_commit') -> None:
    from importlib import import_module
    if (lane, fault) not in TARGETS:
        raise ValueError(f'Unsupported crash-lab injection: {lane}/{fault}')
    if point not in ('before_commit', 'after_commit'):
        raise ValueError('Unknown FDB crash boundary')
    if point == 'before_commit' and fault not in ('reserve', 'charge'):
        raise ValueError('Precommit currently supports reservation/charge only')
    marker = marker.resolve()
    state = threading.local()
    if lane == 'stable' and point == 'before_commit':
        _install_stable_before_commit_save_hook(marker, fault)
        return
    if lane == 'stable':
        _install_stable_outer_commit_hook(state, marker, point=point)
    elif point == 'before_commit':
        _install_exp_before_commit_write_hook(marker, fault)
        return

    module_name, class_name, method_name = TARGETS[(lane, fault)]
    target = getattr(import_module(module_name), class_name)
    original = getattr(target, method_name)

    @functools.wraps(original)
    def instrumented(self: Any, *args: Any, **kwargs: Any) -> Any:
        tracing = lane == 'stable' and fault == 'charge'
        if tracing:
            _trace_hook(marker, 'stable_charge_method_entered')
        try:
            result = original(self, *args, **kwargs)
        except BaseException as exc:
            if tracing:
                _trace_hook(marker, 'stable_charge_method_raised',
                            exception_type=type(exc).__name__, error=str(exc))
            raise
        if tracing:
            _trace_hook(marker, 'stable_charge_method_returned',
                        idempotent=isinstance(result, dict) and result.get('idempotent') is True)
        if not isinstance(result, dict) or result.get('idempotent') is True:
            return result
        evidence = _evidence(lane, fault, result, kwargs,
                             point='before_fdb_commit' if point == 'before_commit'
                             else 'after_fdb_commit_before_call_return')
        context = getattr(state, 'context', None)
        if lane == 'stable' and context is not None:
            pending = context.get('pending')
            if pending is not None and pending['record_id'] != evidence['record_id']:
                raise RuntimeError('Multiple target ledger records in one FDB transaction')
            context['pending'] = evidence
            return result
        if point == 'before_commit':
            # No enclosing FDB transaction: cannot claim pre-commit crash semantics.
            raise RuntimeError('Precommit injection must occur within Stable outer store.atomic()')
        _commit_barrier(marker, evidence)
        return result
    setattr(target, method_name, instrumented)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lane', required=True, choices=['stable', 'exp'])
    p.add_argument('--fault', required=True, choices=list(KIND))
    p.add_argument('--point', default='after_commit', choices=['before_commit', 'after_commit'])
    p.add_argument('--marker', required=True, type=Path)
    args, hub_argv = p.parse_known_args(argv)
    if not hub_argv or hub_argv[0] != '--':
        p.error('Hub arguments must follow --')
    install(args.lane, args.fault, args.marker, point=args.point)
    import sys
    sys.argv = ['main_computer.' + ('stable_hub' if args.lane == 'stable' else 'exp_fdb_hub'), *hub_argv[1:]]
    runpy.run_module(sys.argv[0], run_name='__main__', alter_sys=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
