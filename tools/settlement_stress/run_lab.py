"""Campaign A (P3): four concurrent financial experiments against real FDB.

This is a ledger boundary test. It launches three independent **ledger client**
processes per experiment, not three network Hub servers. HTTP/auth parity is a
separate gate. No on-chain payment is issued.

Usage: python -m tools.settlement_stress.run_lab --lane both --experiments all
    --clients 32 --rounds 20 --cluster-file .foundationdb/docker.cluster
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WORKER = 'campaign-a-worker'
WALLET = '0x' + 'ac' * 20
EXPERIMENTS = ('claim', 'batch', 'payout', 'confirm')
LANES = ('stable', 'exp')


class StressError(RuntimeError):
    pass


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + '.tmp')
    staged.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    os.replace(staged, path)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise StressError(f'Expected JSON object: {path}')
    return value


def configure_topology(out: Path, namespace: str, cluster_file: Path) -> Path:
    base = read_json(ROOT / 'deploy/hub-topology/smoke-topology.json')
    if (base.get('network') or {}).get('network_kind') != 'smoke':
        raise StressError('Only local smoke topology is permitted')
    base['cluster_id'] = namespace
    base['storage']['namespace'] = namespace
    base['storage']['backend'] = 'foundationdb'
    base['storage']['cluster_file'] = str(cluster_file)
    target = out / 'topology.json'
    write_json(target, base)
    return target


def connect(lane: str, *, namespace: str, cluster_file: Path, topology_path: Path) -> Any:
    if lane == 'stable':
        from main_computer.stable_hub_topology import load_stable_hub_topology
        from main_computer.stable_hub_worker_sessions import (
            FoundationDbStableWorkerSessionStore, StableHubPayoutLedgerDirectory,
        )
        topology = load_stable_hub_topology(topology_path)
        store = FoundationDbStableWorkerSessionStore(
            cluster_file=cluster_file, namespace=namespace, repo_root=ROOT,
        )
        return StableHubPayoutLedgerDirectory(topology=topology,
                                               hub_id=topology.hubs[0].hub_id, store=store)
    if lane == 'exp':
        from main_computer.exp_fdb_credit_ledger import (
            ExperimentalFoundationDbConfig, ExperimentalFoundationDbCreditLedger,
        )
        return ExperimentalFoundationDbCreditLedger(ExperimentalFoundationDbConfig(
            cluster_file=cluster_file, namespace=namespace, repo_root=ROOT,
        ))
    raise StressError(f'Unknown ledger lane: {lane}')


def snapshot(lane: str, ledger: Any) -> dict[str, list[dict[str, Any]]]:
    if lane == 'exp':
        return ledger._snapshot()
    d = ledger.store.load()
    return {
        'accounts': list(d.get('payout_accounts', {}).values()),
        'worker_earnings': list(d.get('worker_earnings', {}).values()),
        'worker_claims': list(d.get('worker_claims', {}).values()),
        'settlement_batches': list(d.get('payout_settlements', {}).values()),
        'bridge_payouts': list(d.get('bridge_payouts', {}).values()),
        'mock_chain_wallets': list(d.get('mock_chain_wallets', {}).values()),
    }


def field(row: dict[str, Any], name: str, default: str = '') -> str:
    return str(row.get(name) or default)


def make_earning(lane: str, ledger: Any, suffix: str) -> str:
    acct, req, sess = 'campaign-a-requester', f'campaign-a-{suffix}', f'sess_campaign_a_{suffix}'
    if lane == 'stable':
        hold = ledger.create_hold(
            account_id=acct, wallet_address=WALLET, request_id=req,
            session_id=sess, run_id=f'run_{suffix}', worker_id=WORKER,
            selected_price={'amount': '1', 'unit': 'credit'},
        )
        charged = ledger.charge_hold(
            hold_id=hold['hold_id'], session_id=sess,
            request_id=req, worker_id=WORKER,
        )
    else:
        hold = ledger.create_hold(account_id=acct, request_id=req, credits=1)
        charged = ledger.charge_hold(hold_id=hold['hold']['hold_id'],
                                     charged_credits=1, worker_node_id=WORKER)
    return charged['worker_earning']['earning_id']


def new_claim(lane: str, ledger: Any, earning_ids: list[str], key: str) -> dict[str, Any]:
    if lane == 'stable':
        return ledger.record_worker_claim(worker_id=WORKER, earning_ids=earning_ids,
                                          idempotency_key=key)
    return (ledger.record_worker_claim(worker_node_id=WORKER, earning_ids=earning_ids,
                                       idempotency_key=key).get('claim') or {})


def new_batch(lane: str, ledger: Any, claim_ids: list[str], key: str) -> dict[str, Any]:
    if lane == 'stable':
        return ledger.create_worker_settlement_batch(worker_id=WORKER, claim_ids=claim_ids,
                                                     idempotency_key=key)
    return ledger.create_worker_settlement_batch(
        worker_node_id=WORKER, claim_ids=claim_ids, idempotency_key=key,
    ).get('batch') or {}


def settle(lane: str, ledger: Any, batch_id: str, key: str) -> dict[str, Any]:
    if lane == 'stable':
        return ledger.settle_worker_settlement_batch(batch_id=batch_id,
                            settlement_reference=f'prepared-{key}', idempotency_key=key)
    return ledger.settle_worker_settlement_batch(
        batch_id=batch_id, settlement_reference=f'prepared-{key}',
        idempotency_key=key, payout_rail='hub-internal-bridge-preparation',
    )['batch']


def new_payout(lane: str, ledger: Any, batch_id: str, key: str) -> dict[str, Any]:
    if lane == 'stable':
        return ledger.request_bridge_payout(worker_id=WORKER, batch_id=batch_id,
                                            idempotency_key=key)
    return ledger.request_bridge_payout(wallet_address=WALLET, worker_node_id=WORKER,
                         batch_id=batch_id, idempotency_key=key)['payout']


def confirm(lane: str, ledger: Any, payout_id: str, reference: str) -> dict[str, Any]:
    if lane == 'stable':
        return ledger.confirm_bridge_payout(bridge_payout_id=payout_id,
                                             settlement_reference=reference)
    return ledger.confirm_bridge_payout(payout_id=payout_id,
                   metadata={'settlement_reference': reference})['payout']


def seed(lane: str, exp: str, ledger: Any) -> dict[str, Any]:
    if lane == 'stable':
        ledger.fund_account(account_id='campaign-a-requester', wallet_address=WALLET,
                            credits='20', replace=True)
    else:
        ledger.issue(account_id='campaign-a-requester', credits=20)
    num_earnings = 3 if exp == 'batch' else 1
    earnings = [make_earning(lane, ledger, str(i)) for i in range(num_earnings)]
    initial = {'earning_ids': earnings}
    if exp in ('batch', 'payout', 'confirm'):
        claims = [new_claim(lane, ledger, [eid], f'seed-claim-{idx}')
                  for idx, eid in enumerate(earnings)]
        if any(not c.get('claim_id') for c in claims):
            raise StressError('Failed to seed claim')
        initial['claim_ids'] = [c['claim_id'] for c in claims]
    if exp in ('payout', 'confirm'):
        batch = new_batch(lane, ledger, initial['claim_ids'], 'seed-batch')
        if not batch.get('batch_id'):
            raise StressError('Failed to seed batch')
        initial['batch_id'] = batch['batch_id']
        settle(lane, ledger, batch['batch_id'], 'seed-settlement')
    if exp == 'confirm':
        payout = new_payout(lane, ledger, initial['batch_id'], 'seed-payout')
        initial['payout_id'] = payout.get('payout_id') or payout.get('bridge_payout_id')
        if not initial['payout_id']:
            raise StressError('Failed to seed bridge payout')
    return initial


def intentions(exp: str, total: int, seed_value: int, initial: dict[str, Any]) -> list[dict[str, Any]]:
    """Fixed deterministic contention mix, with actual conflict and idempotent replay."""
    rng = random.Random(seed_value)
    result = []
    for i in range(total):
        if exp == 'claim':
            result.append({'key': f'claim-{i // 4}' if i % 4 in (0, 1) else f'claim-{i}',
                           'ids': initial['earning_ids']})
        elif exp == 'batch':
            ids = initial['claim_ids']
            selected = [ids[0], ids[1]] if i % 3 == 0 else [ids[1], ids[2]] if i % 3 == 1 else ids
            result.append({'key': f'batch-{i // 4}' if i % 4 in (0, 1) else f'batch-{i}',
                           'ids': selected})
        elif exp == 'payout':
            result.append({'key': 'payout-shared' if i % 4 in (0, 1) else f'payout-{i}',
                           'batch_id': initial['batch_id']})
        else:
            result.append({'reference': 'reference-authoritative' if i % 2 == 0
                            else f'reference-conflict-{i}', 'payout_id': initial['payout_id']})
    rng.shuffle(result)
    return result


def attempt(lane: str, exp: str, ledger: Any, action: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    common = {'start_ns': time.perf_counter_ns()}
    try:
        if exp == 'claim':
            record = new_claim(lane, ledger, action['ids'], action['key'])
            ident = field(record, 'claim_id') if record.get('earning_ids') else ''
        elif exp == 'batch':
            record = new_batch(lane, ledger, action['ids'], action['key'])
            ident = field(record, 'batch_id') if record.get('claim_ids') else ''
        elif exp == 'payout':
            record = new_payout(lane, ledger, action['batch_id'], action['key'])
            ident = field(record, 'payout_id', field(record, 'bridge_payout_id'))
        else:
            record = confirm(lane, ledger, action['payout_id'], action['reference'])
            ident = field(record, 'payout_id', field(record, 'bridge_payout_id'))
        return {**common, 'end_ns': time.perf_counter_ns(),
                'record_earning_ids': record.get('earning_ids', []) if exp == 'claim' else [],
                'record_claim_ids': record.get('claim_ids', []) if exp == 'batch' else [],
                'ok': True, 'record_id': ident, 'empty': not bool(ident),
                'latency_ms': round(1000 * (time.perf_counter() - started), 3), 'intent': action}
    except (ValueError, KeyError) as exc:
        return {**common, 'end_ns': time.perf_counter_ns(),
                'ok': False, 'rejected': True, 'error': str(exc), 'kind': type(exc).__name__,
                'latency_ms': round(1000 * (time.perf_counter() - started), 3), 'intent': action}
    except Exception as exc:
        return {**common, 'end_ns': time.perf_counter_ns(),
                'ok': False, 'rejected': False, 'error': str(exc), 'kind': type(exc).__name__,
                'latency_ms': round(1000 * (time.perf_counter() - started), 3), 'intent': action}


def worker(job_path: Path, ready: Path, release: Path, output: Path) -> int:
    job = read_json(job_path)
    ledger = connect(job['lane'], namespace=job['namespace'], cluster_file=Path(job['cluster_file']),
                     topology_path=Path(job['topology_path']))
    actions = job['actions']
    if not actions:
        raise StressError('No actions assigned to worker')
    # Every thread prepares its own call and awaits a common file-release barrier.
    # Each process has its own FDB connection, which exercises cross-process conflicts.
    start = threading.Event()
    lock = threading.Lock()
    count = [0]
    ready_all = threading.Event()

    def run(action: dict[str, Any]) -> dict[str, Any]:
        with lock:
            count[0] += 1
            if count[0] == len(actions):
                ready_all.set()
        if not start.wait(timeout=120):
            raise StressError('Worker release barrier timed out')
        return attempt(job['lane'], job['experiment'], ledger, action)

    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        futures = [pool.submit(run, action) for action in actions]
        if not ready_all.wait(timeout=45):
            raise StressError('Worker threads did not reach barrier')
        write_json(ready, {'ready': True, 'thread_count': len(actions), 'pid': os.getpid()})
        limit = time.monotonic() + 120
        while not release.is_file() and time.monotonic() < limit:
            time.sleep(.01)
        if not release.is_file():
            raise StressError('Parent never released barrier')
        start.set()
        results = [future.result() for future in futures]
    write_json(output, {'pid': os.getpid(), 'results': results})
    return 0


def audit(exp: str, initial: dict[str, Any], state: dict[str, list[dict[str, Any]]],
          results: list[dict[str, Any]]) -> dict[str, Any]:
    """Audit durable obligations independently of operation return values."""
    failures = []
    unexpected = [r for r in results if not r['ok'] and not r.get('rejected')]
    if unexpected:
        failures.append(f'{len(unexpected)} unexpected runtime errors')
    for item in results:
        if not item.get('ok') or not item.get('record_id'):
            continue
        if exp == 'claim' and item.get('record_earning_ids') != item['intent']['ids']:
            failures.append('claim returned a different earning set than was requested')
            break
        if exp == 'batch' and item.get('record_claim_ids') != item['intent']['ids']:
            failures.append('batch returned a different claim set than was requested')
            break
    claims = [c for c in state['worker_claims'] if c.get('earning_ids')]
    batches = [b for b in state['settlement_batches'] if b.get('claim_ids')]
    payouts = [p for p in state['bridge_payouts'] if p.get('batch_id')]
    earning_owners, claim_owners, batch_owners = defaultdict(set), defaultdict(set), defaultdict(set)
    for claim in claims:
        for eid in claim.get('earning_ids', []):
            earning_owners[eid].add(field(claim, 'claim_id'))
    for batch in batches:
        if field(batch, 'status') == 'cancelled':
            continue
        for cid in batch.get('claim_ids', []):
            claim_owners[cid].add(field(batch, 'batch_id'))
        exact = int(batch.get('total_credit_wei_exact') or 0)
        published = int(batch.get('total_credit_wei_published') or 0)
        dust = int(batch.get('dust_credit_wei') or 0)
        if exact != published + dust:
            failures.append(f'batch {batch.get("batch_id")}: amount conservation failed')
    for payout in payouts:
        batch_owners[field(payout, 'batch_id')].add(field(payout, 'payout_id', field(payout, 'bridge_payout_id')))
    if any(len(s) > 1 for s in earning_owners.values()):
        failures.append('duplicate earning ownership by distinct claims')
    if any(len(s) > 1 for s in claim_owners.values()):
        failures.append('duplicate claim membership in distinct batches')
    if any(len(s) > 1 for s in batch_owners.values()):
        failures.append('duplicate payout obligation for a settled batch')
    # Each round seeds 20 credits and charges one credit for each known earning.
    # The settlement lifecycle must not mutate the requester's pre-existing balance.
    for account in state.get('accounts', []):
        if field(account, 'account_id') == 'campaign-a-requester':
            n = len(initial['earning_ids'])
            unit = 10**18
            available = int(account.get('available_credit_wei') or 0)
            spent = int(account.get('spent_credit_wei') or 0)
            held = int(account.get('held_credit_wei') or 0)
            if available != (20 - n)*unit or spent != n*unit or held != 0:
                failures.append('requester account conservation failed')
            break
    else:
        if 'accounts' in state:
            failures.append('requester funding account missing')
    batch_by_id = {field(b, 'batch_id'): b for b in batches}
    for payout in payouts:
        b = batch_by_id.get(field(payout, 'batch_id'))
        if b and int(payout.get('credit_wei') or 0) != int(b.get('total_credit_wei_published') or 0):
            failures.append(f'payout {payout.get("payout_id")}: incorrect published amount')
    if exp == 'claim' and len(earning_owners.get(initial['earning_ids'][0], set())) != 1:
        failures.append('claim target not acquired exactly once')
    if exp == 'batch' and not claim_owners:
        failures.append('batch stage failed to acquire a claim')
    if exp in ('payout', 'confirm') and len(batch_owners.get(initial['batch_id'], set())) != 1:
        failures.append('payout for seeded batch missing or duplicated')
    if exp == 'confirm':
        original = [p for p in payouts if field(p, 'batch_id') == initial['batch_id']]
        if len(original) == 1:
            if original[0].get('status') != 'confirmed':
                failures.append('confirmation target is not confirmed')
            expected = int(original[0].get('credit_wei') or 0)
            wallets = [w for w in state['mock_chain_wallets'] if field(w, 'wallet_address') == WALLET]
            if wallets and int(wallets[0].get('available_credit_wei') or 0) != expected:
                failures.append('confirmation credited a wallet more or less than once')
            if not wallets and field(original[0], 'wallet_address'):
                failures.append('confirmed payout has no wallet credit record')
    success = [r for r in results if r.get('ok') and r.get('record_id')]
    # Same payout id must never be reported under two different batch ids; batch audit covers durability.
    latencies = sorted(float(r.get('latency_ms') or 0) for r in results)
    interval_events = sorted([event for r in results
                              if 'start_ns' in r and 'end_ns' in r
                              for event in ((int(r['start_ns']), 1), (int(r['end_ns']), -1))])
    active = peak = 0
    for _, delta in interval_events:
        active += delta
        peak = max(peak, active)
    def percentile(p: float) -> float:
        return latencies[min(len(latencies) - 1, int((len(latencies) - 1) * p))] if latencies else 0.0
    return {'ok': not failures, 'errors': failures, 'attempts': len(results),
            'accepted': len(success), 'rejected': sum(bool(r.get('rejected')) for r in results),
            'empty': sum(bool(r.get('empty')) for r in results), 'unexpected_errors': len(unexpected),
            'unique_claims': len(claims), 'unique_batches': len(batches), 'unique_batch_payouts': len(payouts),
            'max_observed_concurrent_calls': peak,
            'p50_ms': round(percentile(.5), 3), 'p95_ms': round(percentile(.95), 3),
            'p99_ms': round(percentile(.99), 3)}


def run_round(*, lane: str, exp: str, round_id: int, clients: int, seed_value: int,
              cluster_file: Path, out: Path, timeout: float = 180) -> dict[str, Any]:
    namespace = f'mc-p3-stress-{uuid.uuid4().hex[:18]}'
    out.mkdir(parents=True, exist_ok=True)
    topology_path = configure_topology(out, namespace, cluster_file)
    control = connect(lane, namespace=namespace, cluster_file=cluster_file, topology_path=topology_path)
    initial = seed(lane, exp, control)
    write_json(out / 'initial.json', initial)
    actions = intentions(exp, clients, seed_value, initial)
    write_json(out / 'intents.json', actions)
    release = out / 'release.signal'
    children = []
    start = time.monotonic()
    try:
        for index in range(3):
            mine = actions[index::3]
            job_path = out / f'job-{index}.json'
            write_json(job_path, {'lane': lane, 'experiment': exp, 'namespace': namespace,
                                  'cluster_file': str(cluster_file), 'topology_path': str(topology_path),
                                  'actions': mine})
            stdout = (out / f'client-{index}.log').open('w', encoding='utf-8')
            command = [sys.executable, '-m', 'tools.settlement_stress.run_lab', '--_worker',
                       '--_job', str(job_path), '--_ready', str(out / f'ready-{index}.json'),
                       '--_release', str(release), '--_output', str(out / f'output-{index}.json')]
            child = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=subprocess.STDOUT)
            children.append((child, stdout))
        while time.monotonic() - start < timeout:
            if all((out / f'ready-{i}.json').is_file() for i in range(3)):
                break
            exited = [p.returncode for p, _ in children if p.poll() is not None]
            if exited:
                raise StressError(f'Ledger client process exited before barrier: {exited}')
            time.sleep(.05)
        else:
            raise StressError('Contention start barrier timed out')
        write_json(release, {'released': True, 'at': time.time()})
        for process, _ in children:
            process.wait(timeout=timeout)
            if process.returncode:
                raise StressError(f'Ledger client process failed, exit={process.returncode}')
        data = [read_json(out / f'output-{i}.json') for i in range(3)]
        results = [x for process in data for x in process['results']]
        state = snapshot(lane, control)
        write_json(out / 'state.json', state)
        write_json(out / 'history.json', results)
        assessment = audit(exp, initial, state, results)
        assessment.update({'lane': lane, 'experiment': exp, 'round': round_id,
                           'seed': seed_value, 'namespace': namespace,
                           'client_processes': [d['pid'] for d in data],
                           'elapsed_s': round(time.monotonic() - start, 3),
                           'evidence_dir': str(out)})
        write_json(out / 'audit.json', assessment)
        return assessment
    finally:
        for process, _ in children:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                pass
        for _, stream in children:
            stream.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lane', choices=['stable', 'exp', 'both'], default='both')
    parser.add_argument('--experiments', default='all', help='all or comma-separated claim,batch,payout,confirm')
    parser.add_argument('--clients', type=int, default=32)
    parser.add_argument('--rounds', type=int, default=20)
    parser.add_argument('--seed', type=int, default=424242)
    parser.add_argument('--cluster-file', type=Path, default=ROOT / '.foundationdb/docker.cluster')
    parser.add_argument('--report-dir', type=Path, default=ROOT / 'runtime/settlement-stress')
    parser.add_argument('--_worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--_job', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--_ready', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--_release', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--_output', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._worker:
        if not all((args._job, args._ready, args._release, args._output)):
            parser.error('worker requires job, ready, release, output')
        return worker(args._job, args._ready, args._release, args._output)
    if args.clients < 3 or args.clients > 128 or args.rounds < 1 or args.rounds > 1000:
        parser.error('Require 3 <= clients <= 128 and 1 <= rounds <= 1000')
    selected = list(EXPERIMENTS) if args.experiments == 'all' else args.experiments.split(',')
    if not selected or any(x not in EXPERIMENTS for x in selected) or len(set(selected)) != len(selected):
        parser.error('Invalid or duplicate experiments')
    cluster_file = args.cluster_file.resolve()
    if not cluster_file.is_file():
        parser.error(f'FoundationDB cluster file not found: {cluster_file}')
    # No implicit fallback to fake/mock FDB: live runs must establish connectivity.
    lanes = LANES if args.lane == 'both' else (args.lane,)
    run_id = uuid.uuid4().hex[:16]
    out = args.report_dir.resolve() / run_id
    records = []
    summary = {'run_id': run_id, 'status': 'RUNNING', 'clients': args.clients, 'rounds': args.rounds,
               'lanes': list(lanes), 'experiments': selected, 'seed': args.seed,
               'scope': 'independent-ledger-processes; real-foundationdb; no-hub-http; no-chain',
               'cluster_file': str(cluster_file), 'results': records}
    write_json(args.report_dir.resolve() / 'last-run.json', summary)
    try:
        for exp in selected:
            for lane in lanes:
                for rnd in range(args.rounds):
                    path = out / exp / lane / f'round-{rnd:03d}'
                    try:
                        result = run_round(lane=lane, exp=exp, round_id=rnd, clients=args.clients,
                                           seed_value=args.seed + rnd, cluster_file=cluster_file, out=path)
                    except Exception as exc:
                        result = {'ok': False, 'lane': lane, 'experiment': exp, 'round': rnd,
                                  'errors': [f'{type(exc).__name__}: {exc}'], 'evidence_dir': str(path)}
                    records.append(result)
                    summary['status'] = 'PASS' if all(r['ok'] for r in records) else 'FAIL'
                    write_json(args.report_dir.resolve() / 'last-run.json', summary)
                    print(f'  {lane}/{exp} round={rnd + 1}: {"PASS" if result["ok"] else "FAIL"}', flush=True)
                    # Preserve the complete Stable/Experimental parity matrix. One
                    # failed lane must not hide later experiments or the other lane.
                    # The final exit code still fails closed.
    except Exception as exc:
        summary['status'] = 'FAIL'
        summary['error'] = f'{type(exc).__name__}: {exc}'
        write_json(args.report_dir.resolve() / 'last-run.json', summary)
        print(f'Campaign A settlement stress: FAIL; report: {args.report_dir.resolve() / "last-run.json"}')
        print(f'  {summary["error"]}')
        return 1
    summary['status'] = 'PASS' if all(r['ok'] for r in records) else 'FAIL'
    write_json(args.report_dir.resolve() / 'last-run.json', summary)
    print(f'Campaign A settlement stress: {summary["status"]}; report: {args.report_dir.resolve() / "last-run.json"}')
    print(f'  {len(records)} lane/experiment rounds; {args.clients} competing clients each; 3 ledger processes')
    print(f'  passed={sum(bool(r["ok"]) for r in records)} failed={sum(not r["ok"] for r in records)}')
    return 0 if summary['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
