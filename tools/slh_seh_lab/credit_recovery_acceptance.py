"""Real Organization Service credit-recovery acceptance, using disposable Hub credits.

Run after the same SEH-connected Worker earned its first credit on the real
loopback Stable Hub. No scheduler monkeypatch, synthetic organization top-up,
Hub socket imitation, production wallet, or injected withdrawal ACK.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
from contextlib import closing
import secrets
import time

from main_computer.credit_units import CREDIT_WEI_PER_CREDIT

from .pinky_integrated import _enroll, _requester_login
from .service_lifecycle import _http, _ok

REQUIRED_GATES = frozenset({
    'real_market_earning_funds_org_wallet', 'real_hub_wallet_bound',
    'one_worker_below_reserve_public', 'one_worker_funded_local',
    'one_worker_recovery_reopened', 'single_worker_affordable_private_executes',
    'single_worker_hub_ack_before_local', 'single_worker_returns_to_market',
    'two_workers_independently_public', 'two_worker_private_reclaim_succeeded',
    'two_worker_only_selected_slot_retired', 'two_worker_other_slot_undisturbed',
    'five_local_assessments_exactly_one_credit',
    'unaffordable_local_refused_without_withdrawal',
    'unaffordable_rejection_creates_no_obligation',
})


def accepted(report):
    """Fail closed if any mandatory witness or exact economic quantity is absent."""
    if not isinstance(report, dict):
        return False
    gates = report.get('gates')
    return (report.get('status') == 'PASS' and report.get('pass') is True
            and isinstance(gates, dict)
            and REQUIRED_GATES <= gates.keys()
            and all(value is True for value in gates.values())
            and report.get('worker_count') == 2
            and report.get('local_requests_succeeded') == 5
            and report.get('local_requests_refused') == 1
            and report.get('source_earning_credit_wei') == str(CREDIT_WEI_PER_CREDIT)
            and report.get('obligations_credit_wei') == str(CREDIT_WEI_PER_CREDIT))



def _settle_disposable_recovery_earning(*, ledger, paid, org_wallet_address):
    """Settle one verified *in-memory dev-Hub* earning for the recovery lab.

    The public Hub API deliberately leaves a Worker claim in a pending batch;
    local credit recovery cannot use it until the smoke operator settles it.
    Never permit a persistent store, remote Hub, or unverified earning here.
    """
    from main_computer.hub_credit_indexer import wallet_account_id
    from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore

    if (not isinstance(ledger.store, InMemoryStableWorkerSessionStore)
            or ledger.topology.network.get('network_kind') != 'dev'
            or ledger.cluster_id != 'main-computer-dev-stable-hub'
            or ledger.hub.hub_id != 'dev-hub3'):
        raise RuntimeError('Credit-recovery operator settlement requires disposable in-memory dev Hub')
    org_account = wallet_account_id(org_wallet_address)
    evidence = paid.get('accounting') or {}
    gates = paid.get('gates') or {}
    if (paid.get('pass') is not True or not gates
            or not all(value is True for value in gates.values())
            or paid.get('terminal_status') != 'succeeded'
            or paid.get('settlement') != 'CLAIM_BATCHED_NOT_SETTLED_OR_BRIDGED'
            or paid.get('worker_account_id') != org_account
            or evidence.get('worker_account_id') != org_account):
        raise RuntimeError('Recovery settlement requires successful wallet-owned paid job')
    request_id = paid.get('request_id')
    session_id = paid.get('session_id')
    earning_id = evidence.get('earning_id')
    amount = int(paid.get('earned_credit_wei') or 0)
    if not request_id or not session_id or not earning_id or amount != CREDIT_WEI_PER_CREDIT:
        raise RuntimeError('Recovery settlement requires exactly one credit of paid earnings')
    state = ledger.store.load()
    earnings = [row for row in state.get('worker_earnings', {}).values()
                if isinstance(row, dict) and row.get('earning_id') == earning_id
                and row.get('worker_id') == org_account
                and row.get('request_id') == request_id
                and row.get('session_id') == session_id]
    charges = [row for row in state.get('payout_charges', {}).values()
               if isinstance(row, dict) and row.get('request_id') == request_id
               and row.get('session_id') == session_id]
    batches = [row for row in state.get('payout_settlements', {}).values()
               if isinstance(row, dict) and row.get('worker_id') == org_account
               and row.get('idempotency_key') == 'smoke-batch-' + request_id]
    if len(earnings) != 1 or len(charges) != 1 or len(batches) != 1:
        raise RuntimeError('Recovery settlement paid charge/earning/batch lineage is incomplete')
    batch = batches[0]
    claims = [state.get('worker_claims', {}).get(cid) for cid in batch.get('claim_ids') or []]
    if (batch.get('status') != 'batched' or len(claims) != 1
            or not isinstance(claims[0], dict)
            or claims[0].get('worker_id') != org_account
            or claims[0].get('earning_ids') != [earning_id]
            or int(batch.get('total_credit_wei_published') or 0) != amount
            or int(earnings[0].get('earned_credit_wei') or 0) != amount
            or int(charges[0].get('charged_credit_wei') or 0) != amount):
        raise RuntimeError('Recovery settlement pending batch is not settleable')
    before = ledger.get_account(org_account)
    if int((before or {}).get('available_credit_wei') or 0) != 0:
        raise RuntimeError('Recovery organization wallet must start with zero spendable credits')
    reference = 'recovery-inmemory-' + request_id
    committed = ledger.settle_worker_settlement_batch(
        batch_id=batch['batch_id'], settlement_reference=reference,
        idempotency_key=reference)
    replay = ledger.settle_worker_settlement_batch(
        batch_id=batch['batch_id'], settlement_reference=reference,
        idempotency_key=reference)
    after = ledger.get_account(org_account)
    if (committed.get('status') != 'settled'
            or committed.get('settlement_reference') != reference
            or replay.get('batch_id') != batch['batch_id']
            or int((after or {}).get('available_credit_wei') or 0) != amount
            or int((after or {}).get('earned_credit_wei') or 0) != amount):
        raise RuntimeError('Recovery in-memory settlement failed balance/idempotency proof')
    return {'status': 'IN_MEMORY_LAB_LEDGER_SETTLED_NOT_BRIDGED',
            'earning_id': earning_id, 'batch_id': batch['batch_id'],
            'worker_account_id': org_account,
            'verified_available_wei': str(after['available_credit_wei']),
            'verified_earned_wei': str(after['earned_credit_wei'])}



def _verified_disposable_private_assignment(root, *, requester, request_id, public_result):
    # Only the disposable lab may read its private Worker assignment ledger.
    # The public API intentionally omits Worker device identity.
    root = Path(root)
    if not root.name.startswith('mc-slh-two-service-lab-paid-'):
        raise RuntimeError('Private Worker assignment audit requires disposable lab root')
    db_path = root / 'state' / 'private_requests.sqlite3'
    if not db_path.is_file():
        raise RuntimeError('Disposable private-request ledger is unavailable')
    with closing(sqlite3.connect(db_path, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute('''SELECT requester_device_id,request_id,status,
                                      worker_device_id,run_id,result_hash
                               FROM private_requests
                               WHERE requester_device_id=? AND request_id=?''',
                            (requester, request_id)).fetchall()
    if (len(rows) != 1 or public_result.get('request_id') != request_id
            or public_result.get('status') != 'SUCCEEDED'
            or not public_result.get('run_id')
            or not public_result.get('result_hash')):
        return None
    row = dict(rows[0])
    if (row['status'] != 'SUCCEEDED'
            or row['run_id'] != public_result['run_id']
            or row['result_hash'] != public_result['result_hash']
            or not row['worker_device_id']):
        return None
    return row['worker_device_id']


def verify_credit_recovery(*, root, api_port, worker_port, local, org_token,
                           proxy, wallet, hub_identity, ledger, hub_url,
                           original_worker, pin, workers, paid, hub_session_directory):
    """One- then two-Worker public earning -> private withdrawal, real service.

    Every result is grounded in the spawned Organization Service, real managed
    WebSocket clients, Hub-owned connection records and actual local liabilities.
    """
    gates = {}
    unit = CREDIT_WEI_PER_CREDIT
    device = original_worker.device_id
    org_account = paid['accounting']['worker_account_id']
    # A paid Worker earning is only a pending claim until the disposable lab
    # operator settles its verified batch. The organization cannot spend it
    # merely because the public Hub returned a successful paid-job receipt.
    settlement = _settle_disposable_recovery_earning(
        ledger=ledger, paid=paid, org_wallet_address=wallet.address)
    account = ledger.get_account(org_account)
    gates['real_market_earning_funds_org_wallet'] = (
        account is not None and int(account['available_credit_wei']) == unit and
        int(account['earned_credit_wei']) == unit and
        settlement['verified_available_wei'] == str(unit) and
        paid['earned_credit_wei'] == str(unit) and
        org_account == next(iter({slot.accepted.get('account_id') for slot in proxy.sessions.values()})))

    # The Hub itself verifies the binding and balance; never fund the org
    # wallet synthetically or alter its SQLite observation from the lab.
    binding = _ok(_http(api_port, 'POST', '/v1/org/admin/marketplace/wallet/bind', {
        'expected_revision': 0,
        'expected_hub_id': hub_identity['hub_id'],
        'expected_cluster_id': hub_identity['cluster_id'],
        'expected_chain_id': str(ledger.topology.network['chain_id']),
    }, bearer=org_token, tls=True))
    gates['real_hub_wallet_bound'] = (
        binding['binding']['wallet_address'] == wallet.address and
        int(binding['balance']['available_credit_wei']) == unit)

    def policy(**changes):
        current = _ok(_http(api_port, 'GET', '/v1/admin/scheduler/policy',
                            bearer=org_token, tls=True))
        return _ok(_http(api_port, 'PATCH', '/v1/admin/scheduler/policy', {
            'expected_revision':current['revision'], 'changes':changes},
            bearer=org_token, tls=True))

    def slots():
        return proxy.sessions

    def wait_slots(expected, label):
        # Do not accept a transient slot snapshot during a policy transition.
        # Confirm a stable view and Hub-side ownership of each live slot.
        deadline = time.monotonic() + 25
        last = {}
        stable = 0
        while time.monotonic() < deadline:
            last = {did: slot.accepted.get('connection_id') for did, slot in slots().items()}
            good = (set(last) == set(expected) and all(
                cid and hub_state(cid) == 'live' for cid in last.values()))
            stable = stable + 1 if good else 0
            if stable >= 3:
                return last
            time.sleep(.1)
        raise RuntimeError('Hub capacity did not stabilize: '+label+' (slots='+
                           str(sorted(last))+', expected='+str(sorted(expected))+')')

    def hub_state(connection):
        row = hub_session_directory.get_owner(connection)
        return (row or {}).get('status')

    # Normal 80/20 policy but below the credit reserve: even a single Worker
    # is advertised. The operator's 20% reserve is NOT a permanent minimum.
    policy(max_public_percent=80, min_organization_percent=20,
           min_private_idle_percent=0, local_credit_reserve_credits='2')
    first = wait_slots({device}, 'credit-shortage single Worker advertised')
    gates['one_worker_below_reserve_public'] = (hub_state(first[device]) == 'live')

    # The same spendable 1-credit balance is funded relative to a 0.5-credit
    # reserve. It must now keep the solitary Worker local, with a real Hub ACK.
    policy(local_credit_reserve_credits='0.5')
    wait_slots(set(), 'funded single Worker retained locally')
    gates['one_worker_funded_local'] = hub_state(first[device]) == 'closed'

    # Recovery mode again: no private request has yet been made.
    policy(local_credit_reserve_credits='2')
    recovered = wait_slots({device}, 'single Worker readvertised when short')
    gates['one_worker_recovery_reopened'] = (
        recovered[device] != first[device] and hub_state(recovered[device]) == 'live')

    keys = Path(root) / 'credit-recovery-keys'
    requester = 'lab-credit-recovery-requester'
    authority = local.organization_controller.authority
    req_key = _enroll(api_port, org_token, keys, requester, 'requester',
                      audience='slh://' + authority.service_fingerprint())
    token = _requester_login(api_port, requester, req_key)
    ids = []

    def submit():
        request_id = 'credit-recovery-' + secrets.token_hex(8)
        ids.append(request_id)
        return _ok(_http(api_port, 'POST', '/v1/work', {
            'request_id': request_id, 'model':'mock-model',
            'work':{'model':'mock-model', 'input':{'kind':'chat',
                   'prompt':'credit recovery acceptance ' + request_id}},
        }, bearer=token, tls=True, timeout=25))

    # The private request itself drives the live Hub withdrawal; no policy
    # change to max_public=0 and no manual proxy.reconcile shortcut.
    original = recovered[device]
    completed = submit()
    first_assignment = _verified_disposable_private_assignment(
        root, requester=requester, request_id=ids[-1], public_result=completed)
    gates['single_worker_affordable_private_executes'] = (
        completed.get('status') == 'SUCCEEDED' and
        first_assignment == device and bool(completed.get('result_hash')))
    gates['single_worker_hub_ack_before_local'] = hub_state(original) == 'closed'
    gates['single_worker_returns_to_market'] = (
        wait_slots({device}, 're-advertise after local request')[device] != original)

    # A second actual managed Worker connects directly, whereas the original
    # stays on the real local SEH relay. Both use the same SLH bank.
    other = 'lab-credit-recovery-direct-worker'
    _enroll(api_port, org_token, keys, other, 'worker',
            audience='slh://' + authority.service_fingerprint())
    def compute(offer):
        return {'status':'success', 'response':{'model':'mock-model','content':'computed '+
            str(len(str((offer.get('work') or {}).get('input') or '')))}}
    from main_computer.slh.managed_client import ManagedSLHWorkerClient
    direct = ManagedSLHWorkerClient(
        device_id=other, identity_dir=keys, tls_fingerprint=pin,
        hub_url=f'https://127.0.0.1:{worker_port}', worker_id=other,
        auth_message={'type':'worker.auth','device_id':other,'market':{
            'rings':['ring-2'], 'capabilities':['chat.completions'],
            'models':['mock-model'], 'price':{'amount':'1','unit':'compute_credit'},
            'max_concurrency':1}},work_executor=compute)
    direct.start()
    workers.append(direct)  # caller owns cleanup on all exit paths
    two = wait_slots({device, other}, 'two workers advertised below reserve')
    gates['two_workers_independently_public'] = (
        two[device] != two[other] and all(hub_state(c)=='live' for c in two.values()))

    result = submit()
    selected = _verified_disposable_private_assignment(
        root, requester=requester, request_id=ids[-1], public_result=result)
    untouched = (set(two) - {selected}).pop() if selected in two else None
    gates['two_worker_private_reclaim_succeeded'] = (
        result.get('status') == 'SUCCEEDED' and selected in two)
    gates['two_worker_only_selected_slot_retired'] = (
        selected in two and hub_state(two[selected]) == 'closed' and
        untouched is not None and hub_state(two[untouched]) == 'live')
    # The retained public slot must keep exactly its former connection ID.
    current = slots()
    gates['two_worker_other_slot_undisturbed'] = (
        untouched in current and
        current[untouched].accepted.get('connection_id') == two[untouched])
    wait_slots({device, other}, 're-advertise reclaimed worker independently')

    # Five completed 0.2-credit organization jobs encumber the one earned
    # credit. A sixth must be refused *before* any public withdrawal.
    for _ in range(3):
        result = submit()
        if result.get('status') != 'SUCCEEDED':
            raise RuntimeError('Credit recovery local job did not complete: '+str(result.get('failure_code')))
        wait_slots({device,other}, 'restore both slots after local completion')
    obligations = _ok(_http(api_port, 'GET', '/v1/accounting/obligations',
                            bearer=org_token, tls=True))
    gates['five_local_assessments_exactly_one_credit'] = (
        obligations['count'] == 5 and
        int(obligations['outstanding_credit_wei']) == unit and
        len(ids) == 5)
    before_reject = wait_slots({device,other}, 'both slots before unaffordable request')
    rejected = submit()
    after_reject = wait_slots({device,other}, 'both slots after unaffordable request')
    gates['unaffordable_local_refused_without_withdrawal'] = (
        rejected.get('status') == 'FAILED' and
        rejected.get('failure_code') == 'INSUFFICIENT_LOCAL_CREDITS' and
        before_reject == after_reject and
        all(hub_state(c) == 'live' for c in after_reject.values()))
    final_obligations = _ok(_http(api_port, 'GET', '/v1/accounting/obligations',
                                 bearer=org_token, tls=True))
    gates['unaffordable_rejection_creates_no_obligation'] = (
        final_obligations['count'] == 5 and
        int(final_obligations['outstanding_credit_wei']) == unit)

    return {'schema':'main-computer.slh-credit-recovery-real-process.v1',
            'status':'PASS' if all(gates.values()) else 'FAIL',
            'pass':bool(gates) and all(gates.values()), 'gates':gates,
            'worker_count':2, 'local_requests_succeeded':5,
            'local_requests_refused':1, 'source_earning_credit_wei':str(unit),
            'obligations_credit_wei':str(final_obligations['outstanding_credit_wei']),
            'worker_transports':{device:'REAL_SEH_LOOPBACK', other:'DIRECT_TLS'},
            'requester_transport':'DIRECT_ORGANIZATION_API_TLS',
            'hub_session_evidence':{'single_public_original':first[device],
                                    'single_public_reopened':recovered[device],
                                    'two_worker_before_private':two,
                                    'two_worker_after_rejection':after_reject},
            'limitations':{'public_wss':'NOT_TESTED',
                           'external_bridge':'NOT_TESTED',
                           'requester_via_seh':'NOT_SUPPORTED_BY_THIS_RELAY_LANE',
                           'source_funding':'SYNTHETIC_BUYER_REAL_HUB_EARNING'}}
