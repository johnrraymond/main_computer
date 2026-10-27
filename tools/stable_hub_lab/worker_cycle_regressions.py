"""Local Stable Hub regression campaign drawn from the October 2026 Worker work cycle.

Uses actual Hub WebSocket/HTTP routes, never a deployed Hub or bridge. No
browser/MetaMask is simulated: that end-to-end responsibility stays in the
separate worker_browser_acceptance harness.
"""
from __future__ import annotations

import queue
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, quote

from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.stable_hub_topology import StableHubTopologyError, build_lab_plan, load_stable_hub_topology
from tools.stable_hub_lab.run_lab import (
    DEFAULT_MSK_ORIGIN, DEFAULT_REQUESTER_WALLET, DEFAULT_WORKER_WALLET,
    _new_user_slug, _post_json_url, _post_json_url_status, _read_json_url,
    _ws_connect, _ws_recv_json, _ws_send_json, build_stable_msk_smoke_result, check_cluster,
)

PRICE_OLD_WEI = 1024000000000000000
PRICE_NEW_WEI = 512000000000000000


def _assert_loopback_lab(topology: Any) -> None:
    # This test opens sockets, creates accepted sessions, and spends local dev
    # credits. Explicitly refuse live networks, even with a custom topology.
    namespace = str(topology.storage.get("namespace", "")).lower()
    cluster = str(topology.cluster_id).lower()
    if (str(topology.network.get("network_key") or "").lower() != "dev"
            or not any(term in namespace for term in ("smoke", "dev", "lab")) or any(
        term in namespace for term in ('mainnet', 'testnet', 'production')
    ) or any(term in cluster for term in ('mainnet', 'testnet', 'production'))):
        raise StableHubTopologyError('worker-cycle regression requires a disposable local lab namespace')
    for hub in topology.hubs:
        for url in (hub.hub_url, hub.public_url):
            parsed = urlparse(str(url))
            if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
                raise StableHubTopologyError('worker-cycle regression refuses non-loopback Hub URL')


def _account(url: str, wallet_address: str, *, timeout: float) -> dict[str, Any]:
    body = _read_json_url(
        f'{url.rstrip("/")}/api/hub/v1/credits/balance?wallet_address={quote(wallet_address)}',
        timeout=timeout,
    )
    if body.get('ok') is not True or not isinstance(body.get('account'), dict):
        raise StableHubTopologyError(f'account balance unavailable: {body}')
    return body['account']


def _payout_records(url: str, request_ids: set[str], *, timeout: float) -> dict[str, list[dict[str, Any]]]:
    body = _read_json_url(f'{url.rstrip("/")}/api/hub/v1/payout/status', timeout=timeout)
    if body.get('ok') is not True or not isinstance(body.get('payout'), dict):
        raise StableHubTopologyError(f'payout status unavailable: {body}')
    payout = body['payout']
    return {
        kind: [r for r in payout.get(kind, []) if isinstance(r, dict)
               and str(r.get('request_id') or '') in request_ids]
        for kind in ('holds', 'charges', 'worker_earnings')
    }


def build_worker_cycle_regressions_result(
    *, topology_path: str | Path,
    requester_wallet_path: str | Path = DEFAULT_REQUESTER_WALLET,
    worker_wallet_path: str | Path = DEFAULT_WORKER_WALLET,
    worker_entry_index: int = 2,
    requester_entry_index: int = 0,
    origin: str = DEFAULT_MSK_ORIGIN,
    timeout: float = 10.0,
) -> dict[str, Any]:
    topology = load_stable_hub_topology(topology_path)
    _assert_loopback_lab(topology)
    identities = check_cluster(topology_path=topology_path, timeout=timeout)
    if not identities.get('ok') or any(
        check.get('storage_namespace') != topology.storage.get('namespace')
        for check in identities.get('checks', [])
    ):
        raise StableHubTopologyError(f'Worker-cycle regression refused incorrect Hub identity/namespace: {identities}')
    plan = build_lab_plan(topology, worker_entry_index=worker_entry_index,
                          requester_entry_index=requester_entry_index)
    worker_hub = topology.hub_by_id(str(plan['worker_initial_entry']['hub_id']))
    requester_hub = topology.hub_by_id(str(plan['requester_initial_entry']['hub_id']))
    # Unique capability prevents other concurrent smoke campaigns or leftovers
    # from satisfying this test's work requests.
    scenario = 'cycle_' + secrets.token_hex(8)
    capability = 'smoke_' + secrets.token_hex(8)
    worker_label = 'worker_' + secrets.token_hex(8)
    worker_msk = build_stable_msk_smoke_result(
        topology_path=topology_path, wallet_path=worker_wallet_path,
        request_hub_id=requester_hub.hub_id, validate_hub_id=worker_hub.hub_id,
        origin=f'{origin}-worker-cycle-worker', user_slug=_new_user_slug(),
        request_id=f'{scenario}-worker-msk', timeout=timeout,
    )
    captain_msk = build_stable_msk_smoke_result(
        topology_path=topology_path, wallet_path=requester_wallet_path,
        request_hub_id=requester_hub.hub_id, validate_hub_id=worker_hub.hub_id,
        origin=f'{origin}-worker-cycle-captain', user_slug=_new_user_slug(),
        request_id=f'{scenario}-captain-msk', timeout=timeout,
    )
    account_id = wallet_account_id(worker_msk.get('wallet_address'))
    captain_account_id = wallet_account_id(captain_msk.get('wallet_address'))
    old_socket: socket.socket | None = None
    socket_new: socket.socket | None = None
    before = _account(requester_hub.hub_url, str(captain_msk['wallet_address']), timeout=timeout)

    def connect(price: str) -> tuple[socket.socket, dict[str, Any]]:
        sock = _ws_connect(worker_hub.hub_url, '/api/hub/v1/workers/live-session', timeout=timeout)
        sock.settimeout(timeout)
        try:
            _ws_send_json(sock, {
                'type': 'worker.auth', 'worker_id': worker_label,
                'multisession_authorization': {'kind': 'multisession_key',
                                                'multisession_key_id': worker_msk['multisession_key_id']},
                'market': {'rings': ['ring-3'], 'capabilities': ['python', capability],
                           'price': {'amount': price, 'unit': 'credit'}, 'max_concurrency': 1},
            })
            auth, ping = _ws_recv_json(sock), _ws_recv_json(sock)
            record = auth.get('market') if isinstance(auth.get('market'), dict) else {}
            owner = auth.get('owner') if isinstance(auth.get('owner'), dict) else {}
            connection_id = str(auth.get('connection_id') or '')
            if not (auth.get('type') == 'hub.auth.accepted' and auth.get('ok') is True
                    and auth.get('account_id') == account_id and connection_id.startswith('conn_')
                    and connection_id != worker_label and owner.get('connection_id') == connection_id
                    and owner.get('account_id') == account_id
                    and record.get('connection_id') == connection_id
                    and record.get('worker_account_id') == account_id
                    and record.get('status') == 'live' and 'ring-3' in record.get('rings', [])
                    and capability in record.get('capabilities', [])
                    and record.get('price') == {'amount': price, 'unit': 'credit'}
                    and record.get('max_concurrency') == 1
                    and ping.get('type') == 'hub.ping' and ping.get('connection_id') == connection_id):
                raise StableHubTopologyError(f'Hub did not acknowledge exact authenticated market offer: {auth}')
            _ws_send_json(sock, {'type': 'worker.pong', 'connection_id': connection_id,
                                 'ping_id': ping.get('ping_id')})
            pong = _ws_recv_json(sock)
            if pong.get('type') != 'hub.pong.accepted' or pong.get('ok') is not True:
                raise StableHubTopologyError(f'Hub live pong not accepted: {pong}')
            return sock, auth
        except BaseException:
            sock.close()
            raise

    def request_payload(label: str, max_price: str) -> dict[str, Any]:
        return {
            'request_id': f'req_{scenario}_{label}',
            'multisession_authorization': {'kind': 'multisession_key',
                                            'multisession_key_id': captain_msk['multisession_key_id']},
            'work': {'ring': 'ring-3', 'max_price': {'amount': max_price, 'unit': 'credit'},
                     'capabilities': [capability], 'input': {'kind': 'echo', 'value': label}},
        }

    def submit_accepted(sock: socket.socket, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        q: queue.Queue[Any] = queue.Queue()
        def post() -> None:
            try:
                q.put(_post_json_url(f'{requester_hub.hub_url.rstrip("/")}/api/hub/v1/work/requests',
                                     payload, timeout=timeout))
            except BaseException as exc:
                q.put(exc)
        thread = threading.Thread(target=post, daemon=True)
        thread.start()
        offer = _ws_recv_json(sock)
        if offer.get('type') != 'hub.work.offer' or offer.get('request_id') != payload['request_id']:
            raise StableHubTopologyError(f'expected matching browser-style work offer: {offer}')
        _ws_send_json(sock, {'type': 'worker.work.accepted', 'session_id': offer['session_id'],
                             'request_id': offer['request_id']})
        result = q.get(timeout=timeout)
        thread.join(timeout=1.0)
        if isinstance(result, BaseException):
            raise StableHubTopologyError(f'Captain request failed: {result}') from result
        if not (result.get('ok') is True and result.get('accepted') is True
                and result.get('request_id') == offer['request_id']
                and result.get('session_id') == offer['session_id']
                and result.get('run_id') == offer['run_id']):
            raise StableHubTopologyError(f'Captain/Worker session linkage failed: {result} versus {offer}')
        return offer, result

    def continuation(response: dict[str, Any], target: str) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            report = _read_json_url(str(response['continuation_url']), timeout=timeout)
            if report.get('status') == target:
                return report
            if str(report.get('status') or '') in ('succeeded', 'failed'):
                raise StableHubTopologyError(f'unexpected terminal session status: {report}')
            time.sleep(0.05)
        raise StableHubTopologyError(f'session never became {target}')

    try:
        old_socket, old_auth = connect('1.024')
        old_price_body = request_payload('old-price-reject', '1')
        old_status, old_response = _post_json_url_status(
            f'{requester_hub.hub_url.rstrip("/")}/api/hub/v1/work/requests',
            old_price_body, timeout=timeout,
        )
        if old_status != 409 or old_response.get('error') != 'worker_not_live':
            raise StableHubTopologyError(f'old 1.024 offer should not fit 1-credit cap: {old_status} {old_response}')
        # Close the original connection before authenticating the same wallet
        # with its new price. This matches the browser test's refresh path.
        _ws_send_json(old_socket, {'type': 'worker.close'})
        old_socket.close()
        old_socket = None
        socket_new, new_auth = connect('0.512')
        if new_auth['connection_id'] == old_auth['connection_id']:
            raise StableHubTopologyError('changed signed offer reused the old Hub connection identity')
        under_status, under_response = _post_json_url_status(
            f'{requester_hub.hub_url.rstrip("/")}/api/hub/v1/work/requests',
            request_payload('new-price-reject', '0.5'), timeout=timeout,
        )
        if under_status != 409 or under_response.get('error') != 'worker_not_live':
            raise StableHubTopologyError(f'new .512 offer incorrectly eligible under .5: {under_status} {under_response}')
        after_rejections = _account(requester_hub.hub_url, str(captain_msk['wallet_address']), timeout=timeout)
        unchanged = all(int(after_rejections.get(field) or 0) == int(before.get(field) or 0)
                        for field in ('available_credit_wei', 'held_credit_wei', 'spent_credit_wei'))
        if not unchanged:
            raise StableHubTopologyError('price-rejected requests changed Captain balance')
        success_payload = request_payload('success', '0.512')
        success_offer, success = submit_accepted(socket_new, success_payload)
        _ws_send_json(socket_new, {
            'type': 'worker.work.result', 'session_id': success_offer['session_id'],
            'run_id': success_offer['run_id'], 'request_id': success_offer['request_id'],
            'worker_id': success_offer['worker_id'], 'result': {'echo': 'success'},
        })
        success_ack = _ws_recv_json(socket_new)
        success_continuation = continuation(success, 'succeeded')
        after_success = _account(requester_hub.hub_url, str(captain_msk['wallet_address']), timeout=timeout)
        abandon_payload = request_payload('disconnect', '0.512')
        abandon_offer, abandon = submit_accepted(socket_new, abandon_payload)
        held_account = _account(requester_hub.hub_url, str(captain_msk['wallet_address']), timeout=timeout)
        if int(held_account.get('held_credit_wei') or 0) < PRICE_NEW_WEI:
            raise StableHubTopologyError('accepted unfinished work did not reserve .512 credits')
        _ws_send_json(socket_new, {'type': 'worker.close'})
        socket_new.close()
        socket_new = None
        failed_continuation = continuation(abandon, 'failed')
        after = _account(requester_hub.hub_url, str(captain_msk['wallet_address']), timeout=timeout)
        records = _payout_records(requester_hub.hub_url,
                                  {success_payload['request_id'], abandon_payload['request_id']},
                                  timeout=timeout)
        holds = {str(r['request_id']): r for r in records['holds']}
        charges = records['charges']
        earnings = records['worker_earnings']
        proof = {
            'old_offer_acknowledged_at_1_024': old_auth['market']['price']['amount'] == '1.024',
            'old_price_rejected_below_ceiling': old_status == 409 and old_response.get('error') == 'worker_not_live',
            'same_wallet_new_connection': new_auth.get('account_id') == old_auth.get('account_id')
                                          and new_auth.get('connection_id') != old_auth.get('connection_id'),
            'new_offer_acknowledged_at_0_512': new_auth['market']['price']['amount'] == '0.512',
            'new_price_rejected_below_ceiling': under_status == 409 and under_response.get('error') == 'worker_not_live',
            'market_rejections_have_no_credit_effect': unchanged,
            'exact_ceiling_captain_worker_linked': success['request_id'] == success_offer['request_id']
                                                   and success['session_id'] == success_offer['session_id'],
            'successful_result_terminal': success_continuation.get('status') == 'succeeded'
                                          and success_ack.get('type') == 'hub.work.result.accepted',
            'disconnect_terminal_failure': failed_continuation.get('status') == 'failed',
            'successful_hold_charged_exactly_once': holds.get(success_payload['request_id'], {}).get('status') == 'charged'
                                                    and len(charges) == 1
                                                    and charges[0].get('request_id') == success['request_id'],
            'unfinished_hold_released_no_charge': holds.get(abandon_payload['request_id'], {}).get('status') == 'released'
                                                   and len([c for c in charges if c.get('request_id') == abandon['request_id']]) == 0,
            'worker_earning_only_for_success': len(earnings) == 1 and earnings[0].get('request_id') == success['request_id'],
            'no_stranded_credit_after_disconnect': (int(after.get('held_credit_wei') or 0)
                                                  == int(after_success.get('held_credit_wei') or 0)),
            'captain_debited_only_completed_work': (int(after.get('available_credit_wei') or 0)
                                                     == int(after_success.get('available_credit_wei') or 0)
                                                     and int(after.get('spent_credit_wei') or 0)
                                                     == int(after_success.get('spent_credit_wei') or 0)
                                                     and int(after_success.get('spent_credit_wei') or 0)
                                                     == int(before.get('spent_credit_wei') or 0) + PRICE_NEW_WEI),
        }
        return {
            'ok': all(proof.values()), 'mode': 'verify-worker-cycle',
            'cluster_id': topology.cluster_id, 'topology_path': str(topology_path),
            'requester_hub_id': requester_hub.hub_id, 'worker_hub_id': worker_hub.hub_id,
            'worker_account_id': account_id, 'captain_account_id': captain_account_id,
            'checked_hubs': identities.get('checks', []),
            'old_connection_id': old_auth['connection_id'], 'new_connection_id': new_auth['connection_id'],
            'success_request_id': success['request_id'], 'success_session_id': success['session_id'],
            'abandoned_request_id': abandon['request_id'], 'abandoned_session_id': abandon['session_id'],
            'account_before': {k: before.get(k) for k in ('available_credit_wei', 'held_credit_wei', 'spent_credit_wei')},
            'account_after_success': {k: after_success.get(k) for k in ('available_credit_wei', 'held_credit_wei', 'spent_credit_wei')},
            'account_after': {k: after.get(k) for k in ('available_credit_wei', 'held_credit_wei', 'spent_credit_wei')},
            'payout': {'holds': records['holds'], 'charges': charges, 'worker_earnings': earnings},
            'proof': proof,
        }
    finally:
        for sock in (old_socket, socket_new):
            if sock is not None:
                try:
                    _ws_send_json(sock, {'type': 'worker.close'})
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass


def render_worker_cycle_regressions_result(result: dict[str, Any]) -> str:
    lines = [
        f"Stable Hub Worker cycle regressions: {'ok' if result['ok'] else 'failed'}",
        f"Cluster: {result['cluster_id']}",
        f"Hub pair: {result['requester_hub_id']} -> {result['worker_hub_id']}",
        f"Successful session: {result['success_session_id']}",
        f"Disconnected session: {result['abandoned_session_id']}",
        'Proof:',
    ]
    lines.extend(f"  {name}: {'yes' if passed else 'NO'}" for name, passed in result['proof'].items())
    return '\n'.join(lines)
