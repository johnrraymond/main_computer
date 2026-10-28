"""Patch 2 isolated process-death verification for Stable Hub credit recovery.

Runs against a NEW disposable local FoundationDB namespace. Never touches
running smoke Hubs, deployed networks, or on-chain credits.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from main_computer.stable_hub_topology import load_stable_hub_topology
from tools.stable_hub_lab.run_lab import (
    _new_user_slug, _post_json_url, _read_json_url, _read_json_url_status, _ws_connect,
    _ws_recv_json, _ws_send_json, build_stable_msk_smoke_result,
    check_cluster,
)

ROOT = Path(__file__).resolve().parents[2]


def _stop_child(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                       capture_output=True, timeout=15, check=False)
    else:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def _is_loopback(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')


def _pick_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def _payout(url: str) -> dict[str, Any]:
    body = _read_json_url(url + '/api/hub/v1/payout/status', timeout=5)
    if body.get('ok') is not True or not isinstance(body.get('payout'), dict):
        raise RuntimeError(f'payout unavailable: {body}')
    return body['payout']


def _balance(url: str, wallet: str) -> dict[str, Any]:
    body = _read_json_url(url + '/api/hub/v1/credits/balance?wallet_address=' + quote(wallet), timeout=5)
    if body.get('ok') is not True:
        raise RuntimeError(f'balance unavailable: {body}')
    return body['account']


def run_isolated_process_death(*, topology_path: Path, out: Path, timeout: float = 110) -> dict[str, Any]:
    base = json.loads(topology_path.read_text(encoding='utf-8'))
    if (base.get('network', {}).get('network_key') != 'dev'
            or base.get('network', {}).get('network_kind') != 'smoke'
            or base.get('storage', {}).get('backend') != 'foundationdb'
            or base.get('contract', {}).get('topology_scope') != 'local-smoke-lab'
            or len(base.get('hubs', [])) != 3
            or not all(_is_loopback(node.get('hub_url', '')) for node in base['hubs'])):
        raise ValueError('refusing nonlocal, non-smoke, non-FoundationDB topology')
    cluster_file = (ROOT / str(base['storage']['cluster_file'])).resolve()
    if not cluster_file.is_file():
        raise FileNotFoundError(f'local FDB cluster file missing: {cluster_file}')
    run_id = secrets.token_hex(8)
    out = out / run_id
    out.mkdir(parents=True, exist_ok=False)
    base['cluster_id'] = 'smoke-crash-recovery-' + run_id
    base['storage']['cluster_file'] = str(cluster_file)
    base['storage']['namespace'] = 'main-computer-hub-smoke-crash-recovery-' + run_id
    ports = [_pick_port() for _ in base['hubs']]
    if len(set(ports)) != len(ports):
        raise RuntimeError('port allocator returned duplicate ports')
    for node, port in zip(base['hubs'], ports):
        node['hub_url'] = node['public_url'] = f'http://127.0.0.1:{port}'
    base['entry_urls'] = [node['hub_url'] for node in base['hubs']]
    temporary_topology = out / 'isolated-topology.json'
    temporary_topology.write_text(json.dumps(base, indent=2) + '\n', encoding='utf-8')
    topology = load_stable_hub_topology(temporary_topology)
    entry, _, victim = topology.hubs
    processes: list[subprocess.Popen[Any]] = []
    logs = []
    worker_sock = None
    evidence: dict[str, Any] = {'ok': False, 'cluster_id': topology.cluster_id,
                               'namespace': topology.storage['namespace'],
                               'victim': victim.hub_id, 'survivor': entry.hub_id}
    try:
        env = dict(os.environ)
        env['MAIN_COMPUTER_STABLE_HUB_RECOVERY_LEASE_SECONDS'] = '30'
        env['MAIN_COMPUTER_STABLE_HUB_RECOVERY_INTERVAL_SECONDS'] = '5'
        for node in topology.hubs:
            log = (out / f'{node.hub_id}.log').open('w', encoding='utf-8')
            logs.append(log)
            processes.append(subprocess.Popen(
                [sys.executable, '-u', '-m', 'main_computer.stable_hub',
                 '--topology', str(temporary_topology), '--hub-id', node.hub_id],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            ))
        checked = check_cluster(topology_path=temporary_topology, timeout=35)
        if not checked['ok']:
            raise RuntimeError(f'isolated cluster did not start: {checked}')
        worker_wallet = out / 'worker-private.json'
        captain_wallet = out / 'captain-private.json'
        worker_msk = build_stable_msk_smoke_result(
            topology_path=temporary_topology, wallet_path=worker_wallet,
            request_hub_id=entry.hub_id, validate_hub_id=victim.hub_id,
            user_slug=_new_user_slug(), request_id='worker-' + run_id,
            origin='isolated-crash-recovery-worker', timeout=15,
        )
        captain_msk = build_stable_msk_smoke_result(
            topology_path=temporary_topology, wallet_path=captain_wallet,
            request_hub_id=entry.hub_id, validate_hub_id=victim.hub_id,
            user_slug=_new_user_slug(), request_id='captain-' + run_id,
            origin='isolated-crash-recovery-captain', timeout=15,
        )
        pre = _balance(entry.hub_url, captain_msk['wallet_address'])
        capability = 'crash_' + run_id
        worker_sock = _ws_connect(victim.hub_url, '/api/hub/v1/workers/live-session', timeout=15)
        worker_sock.settimeout(20)
        _ws_send_json(worker_sock, {
            'type': 'worker.auth', 'execution_protocol': 'durable-authorization-v1',
            'multisession_authorization': {'kind': 'multisession_key',
                                           'multisession_key_id': worker_msk['multisession_key_id']},
            'market': {'rings': ['ring-2'], 'capabilities': ['python', capability],
                       'max_concurrency': 1, 'price': {'amount': '0.512', 'unit': 'credit'}},
        })
        auth, ping = _ws_recv_json(worker_sock), _ws_recv_json(worker_sock)
        if auth.get('type') != 'hub.auth.accepted' or auth.get('execution_protocol') != 'durable-authorization-v1':
            raise RuntimeError(f'authenticated socket rejected: {auth}')
        connection_id = auth['connection_id']
        _ws_send_json(worker_sock, {'type': 'worker.pong', 'connection_id': connection_id,
                                    'ping_id': ping['ping_id']})
        if _ws_recv_json(worker_sock).get('type') != 'hub.pong.accepted':
            raise RuntimeError('initial owner heartbeat was not acknowledged')
        request_id = 'req_crash_' + run_id
        work = {'ring': 'ring-2', 'capabilities': [capability],
                'max_price': {'amount': '0.512', 'unit': 'credit'},
                'input': {'kind': 'echo', 'value': 'crash'}}
        submission: queue.Queue[Any] = queue.Queue()
        def send_captain():
            try:
                submission.put(_post_json_url(entry.hub_url + '/api/hub/v1/work/requests', {
                    'request_id': request_id,
                    'multisession_authorization': {'kind': 'multisession_key',
                                                   'multisession_key_id': captain_msk['multisession_key_id']},
                    'work': work,
                }, timeout=20))
            except BaseException as exc:
                submission.put(exc)
        thread = threading.Thread(target=send_captain, daemon=True)
        thread.start()
        offer = _ws_recv_json(worker_sock)
        if (offer.get('type') != 'hub.work.offer'
                or offer.get('request_id') != request_id
                or offer.get('execution_authorization_required') is not True):
            raise RuntimeError(f'unexpected preparation offer: {offer}')
        _ws_send_json(worker_sock, {'type': 'worker.work.accepted',
                                    'session_id': offer['session_id'], 'request_id': request_id})
        authorized = _ws_recv_json(worker_sock)
        if (authorized.get('type') != 'hub.work.authorized'
                or authorized.get('session_id') != offer['session_id']
                or authorized.get('work') != work):
            raise RuntimeError(f'execution authorization missing: {authorized}')
        submitted = submission.get(timeout=20)
        if isinstance(submitted, BaseException) or submitted.get('accepted') is not True:
            raise RuntimeError(f'Captain request not accepted: {submitted}')
        session_id = offer['session_id']
        holds_before = [h for h in _payout(entry.hub_url)['holds']
                        if h.get('session_id') == session_id and h.get('status') == 'held']
        if len(holds_before) != 1:
            raise RuntimeError(f'no durable reserved hold before process kill: {holds_before}')
        held_account = _balance(entry.hub_url, captain_msk['wallet_address'])
        evidence.update({'request_id': request_id, 'session_id': session_id,
                         'connection_id': connection_id,
                         'hold_id': holds_before[0]['hold_id'],
                         'held_balance': {k: held_account.get(k) for k in (
                             'available_credit_wei', 'held_credit_wei', 'spent_credit_wei')},
                         'pre_balance': {
                            k: pre.get(k) for k in ('available_credit_wei', 'held_credit_wei', 'spent_credit_wei')}})
        victim_proc = processes[2]
        evidence['victim_pid'] = victim_proc.pid
        _stop_child(victim_proc)
        if victim_proc.poll() is None:
            raise RuntimeError('victim termination not confirmed')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(proc.poll() is not None for proc in processes[:2]):
                raise RuntimeError('surviving Hub exited during recovery')
            try:
                payout = _payout(entry.hub_url)
                related_holds = [h for h in payout['holds'] if h.get('session_id') == session_id]
                charges = [c for c in payout['charges'] if c.get('session_id') == session_id]
                earnings = [e for e in payout['worker_earnings'] if e.get('session_id') == session_id]
                if len(related_holds) == 1 and related_holds[0]['status'] == 'released':
                    after = _balance(entry.hub_url, captain_msk['wallet_address'])
                    hold_wei = int(holds_before[0]['credit_wei'])
                    if (charges or earnings
                            or int(after['held_credit_wei']) != int(held_account['held_credit_wei']) - hold_wei
                            or int(after['available_credit_wei']) != int(held_account['available_credit_wei']) + hold_wei
                            or int(after['spent_credit_wei']) != int(held_account['spent_credit_wei'])):
                        raise RuntimeError(f'financial invariants violated after recovery: {after}')
                    status_code, session = _read_json_url_status(
                        entry.hub_url + '/api/hub/v1/work/sessions/' + session_id + '/stream', timeout=5)
                    if status_code != 409 or session.get('status') != 'failed':
                        raise RuntimeError(f'hold released without session failing: {session}')
                    evidence['ok'] = True
                    evidence['proof'] = {
                        'owner_process_killed': True, 'survivor_released_exactly_one_hold': True,
                        'accepted_session_failed': True, 'no_charge_or_worker_earning': True,
                        'captain_balance_restored': True,
                    }
                    return evidence
            except (OSError, TimeoutError):
                pass
            time.sleep(2)
        raise TimeoutError('survivor did not recover expired owner and reserved hold before deadline')
    finally:
        if worker_sock is not None:
            worker_sock.close()
        for proc in processes:
            _stop_child(proc)
        for log in logs:
            log.close()
        # Disposable wallet private keys remain local; clean them, even if
        # interrupted by a caught error. Never add this directory to a patch.
        for name in ('worker-private.json', 'captain-private.json'):
            (out / name).unlink(missing_ok=True)
        (out / 'report.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description='Isolated real owner-process death and credit recovery campaign')
    parser.add_argument('--topology', type=Path, default=Path('deploy/hub-topology/smoke-topology.json'))
    parser.add_argument('--output', type=Path, default=Path('runtime/reports/stable-hub-crash-recovery'))
    parser.add_argument('--timeout', type=float, default=110)
    args = parser.parse_args()
    try:
        result = run_isolated_process_death(topology_path=args.topology, out=args.output, timeout=args.timeout)
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f'STABLE_HUB_CRASH_RECOVERY_FAIL: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2
    print('STABLE_HUB_CRASH_RECOVERY_PASS')
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
