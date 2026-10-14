"""Dedicated crash-lab HTTP workload child (killed separately from the controller)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import queue
import secrets
import socket
import threading
import time


def stable_first_charge_workload(*, topology_path: Path, output: Path, timeout: float) -> int:
    """Drive exactly one real paid Stable worker result, then await the fault.

    A failover charge test must *not* reuse the integrated market stress
    scenario: that scenario sends duplicate terminal results, reconnects, and
    settles payments. Those later phases can close a WebSocket without ever
    exercising the intended charge crash boundary. This driver stops after
    the first result and leaves the acknowledgment deliberately unread.
    """
    from main_computer.stable_hub_topology import build_lab_plan, load_stable_hub_topology
    from tools.stable_hub_lab import run_lab as stable

    topology = load_stable_hub_topology(topology_path)
    plan = build_lab_plan(topology, worker_entry_index=2, requester_entry_index=0)
    worker_hub = topology.hub_by_id(str(plan['worker_initial_entry']['hub_id']))
    requester_hub = topology.hub_by_id(str(plan['requester_initial_entry']['hub_id']))
    def progress(phase: str, **evidence: object) -> None:
        # A process killed by the controller cannot write a final report.
        # Persist its last completed client step before attempting the next.
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_name(output.name + '.tmp')
        tmp.write_text(json.dumps({'phase': phase, **evidence}, indent=2), encoding='utf-8')
        tmp.replace(output)

    progress('starting')
    scenario = 'crash_one_charge_' + secrets.token_urlsafe(10).rstrip('=')
    worker_id = 'worker_' + secrets.token_urlsafe(12).rstrip('=')
    worker = stable.build_stable_msk_smoke_result(
        topology_path=topology_path, wallet_path=output.parent / 'worker-wallet.json',
        request_hub_id=requester_hub.hub_id, validate_hub_id=worker_hub.hub_id,
        origin='crash-lab-one-charge-worker', user_slug=stable._new_user_slug(),
        request_id=scenario + '-worker-msk', timeout=timeout)
    requester = stable.build_stable_msk_smoke_result(
        topology_path=topology_path, wallet_path=output.parent / 'requester-wallet.json',
        request_hub_id=requester_hub.hub_id, validate_hub_id=worker_hub.hub_id,
        origin='crash-lab-one-charge-requester', user_slug=stable._new_user_slug(),
        request_id=scenario + '-requester-msk', timeout=timeout)
    worker_key = str(worker['multisession_key_id'])
    requester_key = str(requester['multisession_key_id'])
    sock = stable._ws_connect(worker_hub.hub_url, '/api/hub/v1/workers/live-session', timeout=timeout)
    try:
        stable._ws_send_json(sock, {
            'type': 'worker.auth', 'worker_id': worker_id,
            'multisession_authorization': {
                'kind': 'multisession_key', 'multisession_key_id': worker_key},
            'market': {'rings': ['ring-2'], 'price': {'amount': '0.03', 'unit': 'credit'},
                       'capabilities': ['python', 'echo'], 'max_concurrency': 2},
        })
        auth = stable._ws_recv_json(sock)
        ping = stable._ws_recv_json(sock)
        if auth.get('type') != 'hub.auth.accepted' or ping.get('type') != 'hub.ping':
            raise RuntimeError(f'Stable worker authentication failed: {auth!r} / {ping!r}')
        progress('worker_authenticated', worker_hub_url=worker_hub.hub_url)
        stable._ws_send_json(sock, {'type': 'worker.pong', 'connection_id': auth.get('connection_id'),
                                    'ping_id': ping.get('ping_id')})
        pong = stable._ws_recv_json(sock)
        if pong.get('type') != 'hub.pong.accepted' or pong.get('ok') is not True:
            raise RuntimeError(f'Stable worker heartbeat failed: {pong!r}')

        request_id = 'req_' + secrets.token_urlsafe(12).rstrip('=')
        payload = {
            'request_id': request_id,
            'multisession_authorization': {
                'kind': 'multisession_key', 'multisession_key_id': requester_key},
            'work': {'ring': 'ring-2', 'max_price': {'amount': '0.10', 'unit': 'credit'},
                     'capabilities': ['python'], 'input': {'kind': 'echo', 'value': 'one-charge'}},
        }
        responses: queue.Queue = queue.Queue(maxsize=1)
        def submit() -> None:
            try:
                responses.put(stable._post_json_url(
                    requester_hub.hub_url.rstrip('/') + '/api/hub/v1/work/requests',
                    payload, timeout=timeout))
            except BaseException as exc:
                responses.put(exc)
        sender = threading.Thread(target=submit, daemon=True)
        sender.start()
        offer = stable._ws_recv_json(sock)
        if offer.get('type') != 'hub.work.offer':
            raise RuntimeError(f'Stable work not offered: {offer!r}')
        progress('worker_offer_received', session_id=offer.get('session_id'),
                 request_id=offer.get('request_id'))
        stable._ws_send_json(sock, {'type': 'worker.work.accepted',
                                    'session_id': offer.get('session_id'),
                                    'request_id': offer.get('request_id')})
        response = responses.get(timeout=timeout)
        if isinstance(response, BaseException):
            raise RuntimeError(f'Stable requester acceptance failed: {response}') from response
        if response.get('ok') is not True or response.get('accepted') is not True:
            raise RuntimeError(f'Stable work not accepted: {response!r}')
        sender.join(timeout=1)
        accepted = stable._read_json_url(str(response.get('continuation_url') or ''), timeout=timeout)
        payout = dict((accepted.get('accepted_session') or {}).get('payout') or
                      accepted.get('payout') or {})
        hold_id = str(payout.get('hold_id') or '')
        if not hold_id or payout.get('hold_status') != 'held':
            raise RuntimeError(f'Stable charge test requires a committed hold: {payout!r}')
        if str(response.get('session_id')) != str(offer.get('session_id')):
            raise RuntimeError('Stable requester/worker session identity mismatch')

        progress('hold_verified', session_id=offer.get('session_id'),
                 request_id=offer.get('request_id'), hold_id=hold_id)
        result_message = {
            'type': 'worker.work.result', 'session_id': offer.get('session_id'),
            'run_id': offer.get('run_id'), 'request_id': offer.get('request_id'),
            'worker_id': offer.get('worker_id'), 'result': {'echo': 'one-charge'},
        }
        stable._ws_send_json(sock, result_message)
        evidence = {
            'request_id': offer.get('request_id'), 'session_id': offer.get('session_id'),
            'hold_id': hold_id, 'worker_hub_url': worker_hub.hub_url,
            'requester_hub_url': requester_hub.hub_url,
        }
        progress('first_result_sent_awaiting_charge_crash', **evidence)
        # The after-commit fault hook should hold the handler before any reply.
        # Read the WebSocket only to expose an *unexpected* early reply or
        # disconnection. The controller terminates us after observing the marker.
        # In particular, an unhandled Stable server error previously went
        # completely unnoticed while this client slept for > the lab timeout.
        try:
            ack = stable._ws_recv_json(sock)
        except Exception as exc:
            progress('terminal_websocket_failed_before_marker', **evidence,
                     exception_type=type(exc).__name__, error=str(exc))
            raise RuntimeError('Stable terminal WebSocket failed before the '
                               f'after-commit crash marker: {type(exc).__name__}: {exc}') from exc
        progress('unexpected_terminal_ack_before_marker', **evidence,
                 terminal_ack=ack)
        raise RuntimeError('Stable terminal WebSocket replied before the charge '
                           f'fault hook blocked it: {ack!r}')
    finally:
        sock.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lane', required=True, choices=['stable', 'exp'])
    p.add_argument('--topology', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--timeout', type=float, default=25)
    p.add_argument('--fault', choices=('reserve', 'charge', 'claim', 'batch'), default='charge')
    p.add_argument('--failover-first-charge', action='store_true',
                   help='Stable failover: send one paid worker result and await injected charge crash')
    args = p.parse_args(argv)
    if args.failover_first_charge:
        if args.lane != 'stable' or args.fault != 'charge':
            p.error('--failover-first-charge only applies to stable/charge')
        return stable_first_charge_workload(topology_path=args.topology,
                                            output=args.output, timeout=args.timeout)
    topo = json.loads(args.topology.read_text(encoding='utf-8'))
    if args.lane == 'stable':
        from tools.stable_hub_lab.run_lab import build_stable_hub_integrated_market_stress_result
        result = build_stable_hub_integrated_market_stress_result(
            topology_path=args.topology,
            requester_wallet_path=args.output.parent / 'requester-wallet.json',
            worker_wallet_path=args.output.parent / 'worker-wallet.json',
            timeout=args.timeout,
        )
    else:
        from tools.exp_hub_lab import run_lab as exp_lab
        result = exp_lab.verify_handoff(hub_urls=topo['entry_urls'], stress_requests=3,
                                        failure_indices=(2,), timeout=args.timeout,
                                        require_multisession_auth=True)
        if result.get('ok') and args.fault in ('claim', 'batch'):
            worker, earning_ids = exp_lab._resolve_settlement_worker_from_handoff(
                result, owner_url=topo['entry_urls'][0], timeout=args.timeout)
            if not earning_ids:
                raise RuntimeError('No earned credits available for settlement crash test')
            claim = exp_lab._post_json_url(
                topo['entry_urls'][0] + '/api/hub/v1/workers/claims',
                {'worker_node_id': worker, 'earning_ids': earning_ids,
                 'idempotency_key': 'crash-lab-' + str(result['scenario_id']) + '-claim'},
                timeout=args.timeout)
            if args.fault == 'batch':
                claim_id = str((claim.get('claim') or {}).get('claim_id') or '')
                if not claim_id:
                    raise RuntimeError('Unable to seed settlement claim')
                exp_lab._post_json_url(
                    topo['entry_urls'][0] + '/api/hub/v1/workers/settlements/batches',
                    {'worker_node_id': worker, 'claim_ids': [claim_id],
                     'idempotency_key': 'crash-lab-' + str(result['scenario_id']) + '-batch'},
                    timeout=args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
