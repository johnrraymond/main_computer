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
            'type': 'worker.auth', 'execution_protocol': 'durable-authorization-v1', 'worker_id': 'non_authoritative_label',
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


def test_integrated_stress_reconnect_cannot_select_retiring_same_run_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduce the intermittent 409 seen on stress iteration 18/25.

    The old (first-generation) socket's closure is intentionally prevented from
    committing before the new (reconnected) socket is asked to work. Make that
    old offer cheaper to ensure a same-capability request would select it first.
    The reconnect must target only the newly authenticated connection, while
    the previous generation is still a valid *directory* entry.
    """
    import main_computer.stable_hub as hub_module
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
    first_owner_hub = servers[2][0]
    close_entered = threading.Event()
    permit_close = threading.Event()
    saw_retiring_market_candidate = threading.Event()
    original_close = hub_module.close_worker_connection_and_settle
    original_select = first_owner_hub.worker_market_directory.select_worker_for_work
    old_connection = []

    def stalled_old_close(server, *, worker_id, connection_id):
        if server is first_owner_hub and not close_entered.is_set():
            owner = server.worker_session_directory.get_owner(worker_id)
            old_market = server.worker_market_directory.get_worker(worker_id)
            assert owner and old_market and old_market['status'] == 'live'
            # Deterministic negative control: the retiring lease is cheaper
            # than the reconnect offer, so both sharing one capability would
            # cause the Hub to select this unusable former socket.
            server.worker_market_directory.record_worker_live(
                worker_id=worker_id, owner=owner,
                market_profile={
                    'rings': old_market['rings'],
                    'price': {'amount': '0.001', 'unit': 'credit'},
                    'capabilities': old_market['capabilities'],
                    'max_concurrency': old_market['max_concurrency'],
                },
                worker_msk_id=old_market['worker_msk_id'],
                worker_wallet_address=old_market['worker_wallet_address'],
                worker_account_id=old_market['worker_account_id'],
            )
            old_connection.append(connection_id)
            close_entered.set()
            permit_close.wait(20)
        return original_close(server, worker_id=worker_id, connection_id=connection_id)

    def check_reconnect_selection(work, *, excluded_connections=None):
        caps = set(work.get('capabilities') or [])
        if any(str(cap).endswith('_reconnect') for cap in caps):
            old_candidates = [record for record in first_owner_hub.worker_market_directory.list_workers()
                              if record.get('connection_id') in old_connection
                              and record.get('status') == 'live'
                              and record.get('price', {}).get('amount') == '0.001']
            if close_entered.is_set() and old_candidates:
                saw_retiring_market_candidate.set()
        return original_select(work, excluded_connections=excluded_connections)

    monkeypatch.setattr(hub_module, 'close_worker_connection_and_settle', stalled_old_close)
    monkeypatch.setattr(first_owner_hub.worker_market_directory, 'select_worker_for_work', check_reconnect_selection)
    try:
        result = build_stable_hub_integrated_market_stress_result(
            topology_path=path, requester_wallet_path=tmp_path / 'captain.json',
            worker_wallet_path=tmp_path / 'worker.json', timeout=5,
        )
        assert close_entered.is_set(), 'old-generation close was not exercised'
        assert saw_retiring_market_candidate.is_set(), 'reconnect did not encounter a still-live older lease'
        assert result['ok'], result
        assert result['proof']['reconnect_generation_market_isolation']
        assert all(result['proof'].values())
        assert result['traffic']['reconnect_success']['offer']['worker_id'] == result['actors']['worker']['reconnect_connection_id']
        assert result['actors']['worker']['first_connection_id'] in old_connection
        assert result['actors']['worker']['initial_generation_capability'] != result['actors']['worker']['reconnect_generation_capability']
    finally:
        permit_close.set()
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_cluster_protocol_gate_rejects_stale_or_mixed_smoke_hubs_before_spend(monkeypatch):
    """Patch-1 harness must not silently drive a pre-Patch-1 long-running Hub."""
    from tools.stable_hub_lab import run_lab

    expected = "durable-authorization-v1"
    checks = [
        {"ok": True, "hub_id": "smoke-hub1", "hub_url": "http://127.0.0.1:8870",
         "execution_authorization": expected},
        {"ok": True, "hub_id": "smoke-hub2", "hub_url": "http://127.0.0.1:8871",
         "execution_authorization": None},
        {"ok": True, "hub_id": "smoke-hub3", "hub_url": "http://127.0.0.1:8872",
         "execution_authorization": expected},
    ]
    monkeypatch.setattr(run_lab, "check_cluster", lambda **kw: {"ok": True, "checks": checks})
    with pytest.raises(StableHubTopologyError, match=r"smoke cluster protocol mismatch.*smoke-hub2"):
        run_lab.require_durable_execution_cluster(topology_path=DEV, timeout=1.0)
    checks[1]["execution_authorization"] = expected
    assert run_lab.require_durable_execution_cluster(topology_path=DEV, timeout=1.0)["ok"]


def test_stable_hub_identity_advertises_durable_execution_protocol():
    """The live-version gate reads Hub identity, not the WebSocket handshake."""
    from main_computer.stable_hub import build_hub_identity, stable_hub_contract

    expected = "durable-authorization-v1"
    assert stable_hub_contract()["execution_authorization"] == expected
    topology = load_stable_hub_topology(DEV)
    for hub in topology.hubs:
        identity = build_hub_identity(topology, hub.hub_id)
        assert identity["contract"]["execution_authorization"] == expected
