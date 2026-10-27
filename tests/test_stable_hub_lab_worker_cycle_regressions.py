from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
from main_computer.stable_hub_topology import StableHubTopologyError, load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
from tools.stable_hub_lab.worker_cycle_regressions import (
    _assert_loopback_lab, build_worker_cycle_regressions_result,
    render_worker_cycle_regressions_result,
)

DEV = Path('deploy/hub-topology/dev-topology.json')


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def _topology(tmp_path: Path, *, network_key: str = 'dev') -> Path:
    data = json.loads(DEV.read_text(encoding='utf-8'))
    data['network']['network_key'] = network_key
    for hub in data['hubs']:
        url = f'http://127.0.0.1:{_free_port()}'
        hub['hub_url'] = hub['public_url'] = url
    data['entry_urls'] = [hub['hub_url'] for hub in data['hubs']]
    path = tmp_path / 'topology.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


def test_worker_cycle_lab_refuses_testnet_and_external_hosts(tmp_path: Path) -> None:
    path = _topology(tmp_path, network_key='testnet')
    with pytest.raises(StableHubTopologyError, match='disposable local lab'):
        _assert_loopback_lab(load_stable_hub_topology(path))
    data = json.loads(path.read_text())
    data['network']['network_key'] = 'dev'
    data['hubs'][0]['hub_url'] = 'http://example.org:8871'
    path.write_text(json.dumps(data))
    with pytest.raises(StableHubTopologyError, match='non-loopback'):
        _assert_loopback_lab(load_stable_hub_topology(path))


def test_worker_cycle_market_price_refresh_dispatch_and_disconnect_accounting(tmp_path: Path) -> None:
    path = _topology(tmp_path)
    topology = load_stable_hub_topology(path)
    stores = InMemoryStableWorkerSessionStore()
    keys = InMemoryStableMultiSessionKeyStore()
    servers = []
    for hub in topology.hubs:
        port = int(hub.hub_url.rsplit(':', 1)[-1])
        server = create_stable_hub_server(
            topology=topology, hub_id=hub.hub_id, bind_host='127.0.0.1',
            bind_port=port, multisession_key_store=keys, worker_session_store=stores,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
    try:
        result = build_worker_cycle_regressions_result(
            topology_path=path, requester_wallet_path=tmp_path / 'captain.json',
            worker_wallet_path=tmp_path / 'worker.json', timeout=5.0,
        )
    finally:
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    assert result['ok'], result
    assert all(result['proof'].values())
    assert result['old_connection_id'] != result['new_connection_id']
    assert len(result['payout']['holds']) == 2
    assert len(result['payout']['charges']) == 1
    assert len(result['payout']['worker_earnings']) == 1
    assert 'Stable Hub Worker cycle regressions: ok' in render_worker_cycle_regressions_result(result)


def test_affordable_market_selection_can_still_fail_credit_reservation_without_charging(tmp_path: Path) -> None:
    """Reproduce the historical HTTP 402 separately from the 409 market filter."""
    from main_computer.hub_credit_indexer import wallet_account_id
    from tools.stable_hub_lab.run_lab import (
        _new_user_slug, _post_json_url_status, _ws_connect, _ws_recv_json, _ws_send_json,
        build_stable_msk_smoke_result,
    )
    import queue

    path = _topology(tmp_path)
    topology = load_stable_hub_topology(path)
    store = InMemoryStableWorkerSessionStore()
    keys = InMemoryStableMultiSessionKeyStore()
    servers = []
    for hub in topology.hubs:
        port = int(hub.hub_url.rsplit(':', 1)[-1])
        server = create_stable_hub_server(
            topology=topology, hub_id=hub.hub_id, bind_host='127.0.0.1', bind_port=port,
            multisession_key_store=keys, worker_session_store=store,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
    worker_socket = None
    try:
        entry = topology.hubs[0]
        owner = topology.hubs[2]
        worker = build_stable_msk_smoke_result(
            topology_path=path, wallet_path=tmp_path / 'worker402.json',
            request_hub_id=entry.hub_id, validate_hub_id=owner.hub_id,
            origin='worker-cycle-402-worker', user_slug=_new_user_slug(),
            request_id='req_worker402', timeout=5,
        )
        captain = build_stable_msk_smoke_result(
            topology_path=path, wallet_path=tmp_path / 'captain402.json',
            request_hub_id=entry.hub_id, validate_hub_id=owner.hub_id,
            origin='worker-cycle-402-captain', user_slug=_new_user_slug(),
            request_id='req_captain402', timeout=5,
        )
        wallet = captain['wallet_address']
        account_id = wallet_account_id(wallet)
        # Lab-owned in-memory state only. Do not expose a public funding route.
        ledger = servers[0][0].payout_ledger_directory
        ledger.fund_account(account_id=account_id, wallet_address=wallet, credits='0.976', replace=True)
        balance_before = ledger.get_account(account_id)
        worker_socket = _ws_connect(owner.hub_url, '/api/hub/v1/workers/live-session', timeout=5)
        worker_socket.settimeout(5)
        _ws_send_json(worker_socket, {
            'type': 'worker.auth', 'worker_id': 'non_authoritative_label',
            'multisession_authorization': {'kind': 'multisession_key',
                                            'multisession_key_id': worker['multisession_key_id']},
            'market': {'rings': ['ring-3'], 'price': {'amount': '1.024', 'unit': 'credit'},
                       'capabilities': ['insufficient_funds_smoke'], 'max_concurrency': 1},
        })
        auth, ping = _ws_recv_json(worker_socket), _ws_recv_json(worker_socket)
        assert auth['type'] == 'hub.auth.accepted'
        _ws_send_json(worker_socket, {'type': 'worker.pong', 'connection_id': auth['connection_id'],
                                      'ping_id': ping['ping_id']})
        assert _ws_recv_json(worker_socket)['type'] == 'hub.pong.accepted'
        payload = {
            'request_id': 'req_insufficient_smoke_402',
            'multisession_authorization': {'kind': 'multisession_key',
                                            'multisession_key_id': captain['multisession_key_id']},
            'work': {'ring': 'ring-3', 'capabilities': ['insufficient_funds_smoke'],
                     'max_price': {'amount': '1.024', 'unit': 'credit'},
                     'input': {'kind': 'echo', 'value': 'unaffordable'}},
        }
        responses: queue.Queue[object] = queue.Queue()
        def post() -> None:
            try:
                responses.put(_post_json_url_status(
                    f'{entry.hub_url}/api/hub/v1/work/requests', payload, timeout=5))
            except BaseException as exc:
                responses.put(exc)
        requester_thread = threading.Thread(target=post, daemon=True)
        requester_thread.start()
        offer = _ws_recv_json(worker_socket)
        assert offer['type'] == 'hub.work.offer'
        assert offer['request_id'] == payload['request_id']
        _ws_send_json(worker_socket, {'type': 'worker.work.accepted',
                                      'session_id': offer['session_id'], 'request_id': offer['request_id']})
        response = responses.get(timeout=5)
        requester_thread.join(timeout=1)
        if isinstance(response, BaseException):
            raise response
        status, body = response
        assert status == 402
        assert body['error'] == 'requester_funds_unavailable'
        balance_after = ledger.get_account(account_id)
        assert balance_before['available_credit_wei'] == balance_after['available_credit_wei'] == str(976 * 10**15)
        assert balance_before['held_credit_wei'] == balance_after['held_credit_wei'] == '0'
        assert balance_after['spent_credit_wei'] == '0'
        report = ledger.status()
        assert not [r for r in report['holds'] if r['request_id'] == payload['request_id']]
        assert not [r for r in report['charges'] if r['request_id'] == payload['request_id']]
        assert not [r for r in report['worker_earnings'] if r['request_id'] == payload['request_id']]
    finally:
        if worker_socket is not None:
            try:
                _ws_send_json(worker_socket, {'type': 'worker.close'})
            except OSError:
                pass
            worker_socket.close()
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_integrated_stress_isolated_from_stale_cheaper_market_worker(tmp_path: Path) -> None:
    """Do not dispatch a new campaign to an older live-directory ghost.

    The ghost is deliberately lower priced and has no local WebSocket. Without
    the campaign-specific capability the market deterministically picks it,
    generating worker_owner_not_local instead of testing the current socket.
    """
    from main_computer.hub_credit_indexer import wallet_account_id
    from tools.stable_hub_lab.run_lab import build_stable_hub_integrated_market_stress_result

    path = _topology(tmp_path)
    topology = load_stable_hub_topology(path)
    store = InMemoryStableWorkerSessionStore()
    keys = InMemoryStableMultiSessionKeyStore()
    servers = []
    for hub in topology.hubs:
        port = int(hub.hub_url.rsplit(':', 1)[-1])
        server = create_stable_hub_server(
            topology=topology, hub_id=hub.hub_id, bind_host='127.0.0.1',
            bind_port=port, multisession_key_store=keys, worker_session_store=store,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
    try:
        worker_hub = servers[2][0]
        wallet = '0x1111111111111111111111111111111111111111'
        account = wallet_account_id(wallet)
        stale_id = 'conn_old_but_still_in_shared_directory'
        owner = worker_hub.worker_session_directory.record_connected(
            worker_id=stale_id, connection_id=stale_id,
            multisession_key_id='msk_stale_lab', wallet_address=wallet,
            account_id=account,
        )
        worker_hub.worker_market_directory.record_worker_live(
            worker_id=stale_id, owner=owner,
            market_profile={'rings': ['ring-2'], 'price': {'amount': '0.001', 'unit': 'credit'},
                            'capabilities': ['python', 'echo'], 'max_concurrency': 2},
            worker_msk_id='msk_stale_lab', worker_wallet_address=wallet,
            worker_account_id=account,
        )
        result = build_stable_hub_integrated_market_stress_result(
            topology_path=path, requester_wallet_path=tmp_path / 'stress_captain.json',
            worker_wallet_path=tmp_path / 'stress_worker.json', timeout=5,
        )
        assert result['ok'], result
        assert all(result['proof'].values())
        capability = result['actors']['worker']['scenario_capability']
        assert capability.startswith('stable_stress_')
        assert result['traffic']['success']['offer']['worker_id'] == result['actors']['worker']['first_connection_id']
        assert result['traffic']['reconnect_success']['offer']['worker_id'] == result['actors']['worker']['reconnect_connection_id']
        assert all(capability in entry['request']['work']['capabilities']
                   for entry in (result['traffic']['success'], result['traffic']['failure'],
                                 result['traffic']['reconnect_success']))
        assert worker_hub.worker_session_directory.get_owner(stale_id)['status'] == 'live'
    finally:
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_integrated_stress_keeps_socket_open_during_slow_local_fdb_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old stress client timed out and closed its own socket before dispatch.

    Delay the owner-Hub's first market selection *past the client's startup
    timeout*, but not past the Hub's offer-acceptance timeout. The legitimate
    Worker must still get the offer and complete all accounting invariants.
    """
    import time
    from tools.stable_hub_lab.run_lab import build_stable_hub_integrated_market_stress_result

    path = _topology(tmp_path)
    topology = load_stable_hub_topology(path)
    shared_store = InMemoryStableWorkerSessionStore()
    shared_keys = InMemoryStableMultiSessionKeyStore()
    servers = []
    for hub in topology.hubs:
        port = int(hub.hub_url.rsplit(':', 1)[-1])
        server = create_stable_hub_server(
            topology=topology, hub_id=hub.hub_id, bind_host='127.0.0.1',
            bind_port=port, multisession_key_store=shared_keys,
            worker_session_store=shared_store,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
    try:
        owner = servers[2][0]
        original = owner.worker_market_directory.select_worker_for_work
        delayed = [False]

        def delayed_selection(*args, **kwargs):
            if not delayed[0]:
                delayed[0] = True
                time.sleep(2.5)
            return original(*args, **kwargs)

        monkeypatch.setattr(owner.worker_market_directory, 'select_worker_for_work', delayed_selection)
        result = build_stable_hub_integrated_market_stress_result(
            topology_path=path,
            requester_wallet_path=tmp_path / 'captain_slow.json',
            worker_wallet_path=tmp_path / 'worker_slow.json',
            timeout=2.0,
        )
        assert delayed[0]
        assert result['ok'], result
        waits = [event['offer_wait_seconds'] for event in result['events']
                 if event.get('type') == 'work.offer.received']
        assert len(waits) == 3
        assert waits[0] >= 2.4
        assert result['metrics']['longest_offer_wait_seconds'] >= 2.4
        assert result['metrics']['invariant_violations'] == 0
    finally:
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
