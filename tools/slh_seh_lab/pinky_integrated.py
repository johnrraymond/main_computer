"""P5: one disposable SLH organization, real SEH paid earnings, then private
fulfillment and an aggregate Reserve sweep on the verified smoke/FDB Hub.

The public work leg is performed by real_paid_service before this function.
Private SEH execution is intentionally NOT claimed: the same organization is
restarted in supported direct-Worker mode for its private-work leg.
"""
from __future__ import annotations

from contextlib import closing

import os
from pathlib import Path
import secrets
import sqlite3
from unittest.mock import patch

from main_computer.credit_units import CREDIT_WEI_PER_CREDIT
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.multisession_key_signing import private_key_to_address, sign_personal_message
from main_computer.pinky_reserve_transfer import SWEEP_SCHEMA, canonical_sweep_intent
from main_computer.slh.authenticator import sign_object
from main_computer.slh.pinky_sweep import RESERVE_ENV, SIGNER_ENV
from tools.slh_authenticator_cli import save_identity, load_identity

from .service_lifecycle import ServiceLabFailure, _http, _ok, _await, _login, _fingerprint


def _lab_http(port, method, route, body=None, *, bearer='', tls=False):
    """Allow a real FDB-backed child time to respond; attribute any timeout.

    This timeout applies only to the integrated laboratory, not the local
    service's general request behavior or the production transport.
    """
    try:
        return _http(port, method, route, body, bearer=bearer, tls=tls, timeout=15)
    except (OSError, TimeoutError) as exc:
        raise ServiceLabFailure(
            f'Pinky integrated HTTP {method} {route} failed: '
            f'{type(exc).__name__}: {exc}') from exc




def _read_sweep_row(state, sweep_id):
    """Read the committed batch without leaking an SQLite handle on Windows.

    sqlite3.Connection.__exit__ does not close a connection. When a later
    Hub request raises, its traceback can retain that connection until
    after TemporaryDirectory tries to delete the database.
    """
    with closing(sqlite3.connect(Path(state) / 'private_requests.sqlite3')) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT * FROM pinky_sweeps WHERE sweep_id=?',
                         (sweep_id,)).fetchone()
        if row is None:
            raise ServiceLabFailure('Confirmed Pinky sweep has no local batch record')
        return dict(row)


def _enroll(api_port, admin_token, directory, device_id, role, *, audience):
    save_identity(directory, device_id)
    key, public = load_identity(directory, device_id)
    req = {'selected_admin': 'lab-owner', 'device_id': device_id,
           'public_key': public, 'kind': role, 'device_name': device_id,
           'claimed_user': 'Lab Owner', 'requested_role': role,
           'client_nonce': secrets.token_urlsafe(24)}
    proof = sign_object(key, {**req, 'version': 1,
                         'action': 'slh.device.enrollment.request',
                         'audience': audience})
    pending = _ok(_lab_http(api_port, 'POST', '/v1/enrollment/requests',
                        {**req, 'proof': proof}, tls=True), 201)
    _ok(_lab_http(api_port, 'POST', '/v1/enrollment/approvals',
              {'request_id': pending['request_id']}, bearer=admin_token, tls=True), 201)
    return key


def _requester_login(api_port, device_id, key):
    challenge = _ok(_lab_http(api_port, 'POST', '/v1/auth/challenge',
                          {'device_id': device_id}, tls=True))
    login = _ok(_lab_http(api_port, 'POST', '/v1/auth/verify',
                      {'device_id': device_id, 'challenge_id': challenge['challenge_id'],
                       'signature': sign_object(key, challenge)}, tls=True))
    if login.get('role') != 'requester':
        raise ServiceLabFailure('Private requester did not authenticate as requester')
    return login['access_token']



def _settle_verified_smoke_earning(*, ledger, paid: dict, org_wallet_address: str) -> dict:
    """Lab-controlled settlement of one proven earning, never a Worker HTTP settlement.

    This is *only* called after the real paid job has succeeded in the isolated
    smoke/FDB namespace, and after the parent verified FDB store, cluster and
    Hub identity. It is not evidence of an external blockchain settlement.
    """
    from main_computer.stable_hub_worker_sessions import FoundationDbStableWorkerSessionStore
    if (not isinstance(ledger.store, FoundationDbStableWorkerSessionStore)
            or ledger.topology.storage.get('namespace') != 'main-computer-hub-smoke'
            or 'smoke' not in ledger.cluster_id):
        raise ServiceLabFailure('Pinky smoke internal settlement requires real smoke/FDB authority')
    org_account = wallet_account_id(org_wallet_address)
    evidence = paid.get('accounting') or {}
    gates = paid.get('gates') or {}
    if (not gates or not all(value is True for value in gates.values())
            or paid.get('pass') is not True or paid.get('terminal_status') != 'succeeded'
            or paid.get('settlement') != 'CLAIM_BATCHED_NOT_SETTLED_OR_BRIDGED'
            or paid.get('worker_account_id') != org_account
            or evidence.get('worker_account_id') != org_account):
        raise ServiceLabFailure('Pinky smoke settlement requires verified wallet-owned paid job')
    request_id = paid.get('request_id')
    session_id = paid.get('session_id')
    earning_id = evidence.get('earning_id')
    amount = int(paid.get('earned_credit_wei') or 0)
    if (not request_id or not session_id or not earning_id
            or amount != CREDIT_WEI_PER_CREDIT):
        raise ServiceLabFailure('Pinky smoke settlement requires exactly one credit of earned funds')

    snapshot = ledger.store.load()  # authoritative FDB snapshot; no synthesized account.
    earnings = [row for row in snapshot.get('worker_earnings', {}).values()
                if isinstance(row, dict) and row.get('earning_id') == earning_id
                and row.get('worker_id') == org_account
                and row.get('request_id') == request_id
                and row.get('session_id') == session_id]
    charges = [row for row in snapshot.get('payout_charges', {}).values()
               if isinstance(row, dict) and row.get('request_id') == request_id
               and row.get('session_id') == session_id]
    batches = [row for row in snapshot.get('payout_settlements', {}).values()
               if isinstance(row, dict) and row.get('worker_id') == org_account
               and row.get('idempotency_key') == 'smoke-batch-'+request_id]
    if len(earnings) != 1 or len(charges) != 1 or len(batches) != 1:
        raise ServiceLabFailure('Pinky smoke earning/charge/batch lineage is incomplete')
    batch = batches[0]
    claims = [snapshot.get('worker_claims', {}).get(cid) for cid in batch.get('claim_ids') or []]
    if (batch.get('status') != 'batched' or len(claims) != 1
            or not isinstance(claims[0], dict)
            or claims[0].get('worker_id') != org_account
            or claims[0].get('earning_ids') != [earning_id]
            or int(batch.get('total_credit_wei_published') or 0) != amount
            or int(earnings[0].get('earned_credit_wei') or 0) != amount
            or int(charges[0].get('charged_credit_wei') or 0) != amount):
        raise ServiceLabFailure('Pinky smoke paid accounting is not settleable')
    before = ledger.get_account(org_account)
    before_available = int((before or {}).get('available_credit_wei') or 0)
    if before_available != 0:
        raise ServiceLabFailure('Pinky smoke organization wallet already contains spendable credits')

    # Explicit internal smoke-operator settlement. A worker cannot reach this
    # operation via the public Hub API, and no external bridge is represented.
    reference = 'pinky-smoke-'+request_id
    committed = ledger.settle_worker_settlement_batch(
        batch_id=batch['batch_id'], settlement_reference=reference,
        idempotency_key=reference)
    if (committed.get('status') != 'settled'
            or committed.get('settlement_reference') != reference):
        raise ServiceLabFailure('Pinky smoke internal ledger settlement was not confirmed')
    committed_replay = ledger.settle_worker_settlement_batch(
        batch_id=batch['batch_id'], settlement_reference=reference,
        idempotency_key=reference)
    after = ledger.get_account(org_account)
    if (committed_replay.get('batch_id') != batch['batch_id']
            or int((after or {}).get('available_credit_wei') or 0)
                != before_available + amount):
        raise ServiceLabFailure('Pinky smoke settlement was not idempotent')
    # Both fields below are the post-settlement authoritative Hub account,
    # not values reconstructed from the earlier pending payout receipt.
    return {'status': 'LOCAL_LEDGER_SETTLED_NOT_BRIDGED',
            'batch_id': batch['batch_id'], 'earning_id': earning_id,
            'credit_wei': str(amount), 'worker_account_id': org_account,
            'verified_available_wei': str(int(after['available_credit_wei'])),
            'verified_earned_wei': str(int(after['earned_credit_wei']))}

def verify_pinky_shared_hub(*, root, state, local, local_port, local_token,
                            api_port, worker_port, hub_url, hub_identity,
                            ledger, org_wallet_private, org_wallet_address,
                            worker, reserve_wallet, market_controller, paid_marketplace=None):
    """Complete a verified smoke/FDB payout after the real SEH paid job.

    The supplied Hub ledger must be the real persistent three-Hub smoke/FDB
    ledger. The routine never seeds the organization account with synthetic
    funds: its balance must already contain the just-earned public job payout.
    """
    from main_computer.stable_hub_worker_sessions import FoundationDbStableWorkerSessionStore
    if not isinstance(ledger.store, FoundationDbStableWorkerSessionStore):
        raise ServiceLabFailure('Pinky shared-Hub lab requires the actual FoundationDB credit store')
    if (ledger.topology.storage.get('namespace') != 'main-computer-hub-smoke'
            or 'smoke' not in ledger.cluster_id):
        raise ServiceLabFailure('Pinky shared-Hub lab requires verified smoke/FDB identity')
    if hub_identity.get('hub_id') != ledger.hub.hub_id or hub_identity.get('cluster_id') != ledger.cluster_id:
        raise ServiceLabFailure('Pinky Hub identity mismatch')
    if reserve_wallet == org_wallet_address:
        raise ServiceLabFailure('Reserve destination cannot be organization wallet')
    org_account = wallet_account_id(org_wallet_address)
    reserve_account = wallet_account_id(reserve_wallet)
    if not isinstance(paid_marketplace, dict):
        raise ServiceLabFailure('Pinky smoke settlement requires paid-job evidence')
    lab_settlement = _settle_verified_smoke_earning(
        ledger=ledger, paid=paid_marketplace, org_wallet_address=org_wallet_address)
    starting = ledger.get_account(org_account)
    if not starting or int(starting['available_credit_wei']) < CREDIT_WEI_PER_CREDIT:
        raise ServiceLabFailure('No settled paid-job credit in the organization wallet')
    reserve_before = ledger.get_account(reserve_account)
    reserve_before_wei = int(reserve_before['available_credit_wei']) if reserve_before else 0
    initial_available = int(starting['available_credit_wei'])
    if initial_available != CREDIT_WEI_PER_CREDIT:
        # A disposable wallet should have exactly the single public payout.
        raise ServiceLabFailure('Organization wallet has credits of unknown provenance')

    # Private requests are not enabled when SEH routing is active. Shut down
    # the paid Worker and the Organization child, then restart the SAME saved
    # organization in direct Worker mode. The public earning remains at Hub.
    # Disable automatic public advertising before restarting the SAME
    # organization, so direct private Worker capacity cannot be sold again.
    market_controller.configure(hub_url=hub_url, enabled=False)
    worker.close(reason='pinky_integrated_switch_to_private')
    _ok(_lab_http(local_port, 'POST', '/v1/local/organization/disable', {},
              bearer=local_token))
    _await(lambda: not local.organization_controller.status()['listening'],
           'paid Organization Service stopped', timeout=12)
    _ok(_lab_http(local_port, 'PATCH', '/v1/local/organization/config',
              {'host':'127.0.0.1', 'api_port':api_port, 'worker_port':worker_port,
               'workers_enabled':True, 'seh_enabled':False}, bearer=local_token))
    signer_path = Path(root) / 'pinky-lab-signer.private'
    signer_path.write_text(org_wallet_private, encoding='ascii')
    if os.name == 'posix':
        signer_path.chmod(0o600)
    gates = {}
    with patch.dict(os.environ, {RESERVE_ENV:reserve_wallet, SIGNER_ENV:str(signer_path)}):
        try:
            _ok(_lab_http(local_port, 'POST', '/v1/local/organization/enable', {},
                      bearer=local_token))
            _await(lambda:local.organization_controller.status()['listening'],
                   'private Organization Service restarted', timeout=15)
            admin_token = _login(api_port, local_sign_port=local_port, tls=True)
            pin = _fingerprint(api_port)
            identity = _ok(_lab_http(api_port, 'GET', '/v1/org/identity', tls=True))
            gates['same_org_after_seh_to_private_restart'] = bool(identity.get('tls_sha256'))
            gates['private_transport_without_seh'] = (
                _ok(_lab_http(api_port, 'GET', '/v1/org/status',tls=True)).get('seh_enabled') is False)
            bound = _ok(_lab_http(api_port, 'POST', '/v1/org/admin/marketplace/wallet/bind', {
                'expected_revision':0, 'expected_hub_id':hub_identity['hub_id'],
                'expected_cluster_id':hub_identity['cluster_id'],
                'expected_chain_id':str(ledger.topology.network['chain_id'])},
                bearer=admin_token,tls=True))
            gates['authoritative_wallet_bound'] = (
                bound['binding']['wallet_address'] == org_wallet_address and
                int(bound['balance']['available_credit_wei']) == initial_available)
            devices = Path(root)/'pinky-private-keys'
            req_id = 'pinky-requester-'+secrets.token_hex(4)
            worker_id = 'pinky-private-worker-'+secrets.token_hex(4)
            audience = 'slh://' + local.organization_controller.authority.service_fingerprint()
            req_key = _enroll(api_port,admin_token,devices,req_id,'requester', audience=audience)
            _enroll(api_port,admin_token,devices,worker_id,'worker', audience=audience)
            requester_token = _requester_login(api_port,req_id,req_key)
            def compute(offer):
                data = (offer.get('work') or {}).get('input') or {}
                return {'status':'success','response':{'model':'mock-model','content':'completed '+str(len(str(data.get('prompt',''))))}}
            from main_computer.slh.managed_client import ManagedSLHWorkerClient
            direct_worker = ManagedSLHWorkerClient(
                device_id=worker_id,identity_dir=devices,tls_fingerprint=pin,
                hub_url=f'https://127.0.0.1:{worker_port}',worker_id=worker_id,
                auth_message={'type':'worker.auth','device_id':worker_id,'market':{
                    'rings':['ring-2'],'capabilities':['chat.completions'],
                    'models':['mock-model'],'price':{'amount':'1','unit':'compute_credit'},
                    'max_concurrency':1}},work_executor=compute)
            direct_worker.start()
            try:
                _await(lambda: any(x.get('device_id') == worker_id for x in
                       _ok(_lab_http(api_port,'GET','/v1/org/admin/marketplace/status',
                                 bearer=admin_token,tls=True))['workers']),
                       'private worker registered',timeout=12)
                request_ids = ['pinky-'+secrets.token_hex(8) for _ in range(2)]
                for index, rid in enumerate(request_ids):
                    payload={'request_id':rid,'model':'mock-model',
                             'work':{'model':'mock-model','input':{
                                 'kind':'chat','prompt':'private lab computation '+str(index)}}}
                    answer=_ok(_lab_http(api_port,'POST','/v1/work',payload,
                                     bearer=requester_token,tls=True))
                    if answer.get('status')!='SUCCEEDED' or not answer.get('result_hash'):
                        raise ServiceLabFailure('Private Worker did not fulfill request')
                    replay=_ok(_lab_http(api_port,'POST','/v1/work',payload,
                                     bearer=requester_token,tls=True))
                    if replay.get('status')!='SUCCEEDED' or 'result' in replay:
                        raise ServiceLabFailure('Private request replay reexecuted')
                before=_ok(_lab_http(api_port,'GET','/v1/accounting/obligations',
                                 bearer=admin_token,tls=True))
                gates['two_successes_two_obligations'] = (
                    before['count']==2 and before['paid_count']==0 and
                    int(before['outstanding_credit_wei'])==4*10**17)
                sweep=_ok(_lab_http(api_port,'POST','/v1/accounting/sweep',{},
                                bearer=admin_token,tls=True))
                if sweep.get('status')!='CONFIRMED':
                    raise ServiceLabFailure('Pinky sweep not confirmed: '+str(sweep)[:240])
                status=_ok(_lab_http(api_port,'GET','/v1/accounting/sweeps',
                                 bearer=admin_token,tls=True))
                after=_ok(_lab_http(api_port,'GET','/v1/accounting/obligations',
                                bearer=admin_token,tls=True))
                gates['aggregate_sweep_exactly_once'] = (
                    status['paid_count']==2 and status['pending_sweeps']==0 and
                    after['paid_count']==2 and int(after['outstanding_credit_wei'])==0 and
                    sweep['credit_wei']==str(4*10**17))
                settled = ledger.get_account(org_account)
                reserve_after = ledger.get_account(reserve_account)
                gates['actual_fdb_wallet_debit'] = (
                    int(settled['available_credit_wei'])==initial_available-4*10**17)
                gates['actual_fdb_reserve_credit'] = (
                    int(reserve_after['available_credit_wei'])==reserve_before_wei+4*10**17)
                # The persisted batch can be recovered and replayed at Hub3;
                # the same sweep ID must return the same receipt, no extra debit.
                from main_computer.slh.organization_wallet import HubCreditReader
                row = _read_sweep_row(state, sweep['sweep_id'])
                intent={'schema':SWEEP_SCHEMA,
                        'sweep_id':row['sweep_id'],'cluster_id':row['cluster_id'],
                        'chain_id':row['chain_id'],'wallet_address':row['wallet_address'],
                        'reserve_wallet_address':row['reserve_wallet_address'],
                        'credit_wei':row['credit_wei']}
                repeat=HubCreditReader(hub_url).request('POST',
                    '/api/hub/v1/credits/pinky-reserve/sweep',
                    {'intent':intent,'signature':sign_personal_message(
                        canonical_sweep_intent(intent),org_wallet_private)})
                gates['hub_replay_no_second_transfer'] = (
                    repeat.get('receipt_id')==sweep['receipt_id'] and
                    int(ledger.get_account(org_account)['available_credit_wei'])==initial_available-4*10**17 and
                    int(ledger.get_account(reserve_account)['available_credit_wei'])==reserve_before_wei+4*10**17)
                nothing=_ok(_lab_http(api_port,'POST','/v1/accounting/sweep',{},
                                  bearer=admin_token,tls=True))
                gates['next_sweep_has_no_duplicate_payment'] = nothing['status']=='NOTHING_DUE'
                gates['requester_cannot_view_payment_ledger'] = (
                    _lab_http(api_port,'GET','/v1/accounting/sweeps',bearer=requester_token,tls=True)[0]==403)
                accounting = {
                    'organization_account_id': org_account,
                    'wallet_after_earning_wei': str(initial_available),
                    'wallet_after_sweep_wei': str(int(settled['available_credit_wei'])),
                    'reserve_account_id': reserve_account,
                    'reserve_before_wei': str(reserve_before_wei),
                    'reserve_after_wei': str(int(reserve_after['available_credit_wei'])),
                    'private_request_ids': request_ids,
                    'private_succeeded_count': 2,
                    'obligation_count_before_sweep': int(before['count']),
                    'obligation_paid_count_after_sweep': int(after['paid_count']),
                    'obligation_outstanding_before_wei': str(before['outstanding_credit_wei']),
                    'obligation_outstanding_after_wei': str(after['outstanding_credit_wei']),
                    'sweep_id': sweep['sweep_id'],
                    'hub_receipt_id': sweep['receipt_id'],
                }
                return {'schema':'main-computer.slh-pinky-shared-hub.v1',
                        'accounting': accounting, 'local_lab_settlement': lab_settlement,
                        'status':'PASS' if all(gates.values()) else 'FAIL','pass':all(gates.values()),
                        'gates':gates,'hub_id':hub_identity['hub_id'],
                        'cluster_id':hub_identity['cluster_id'],
                        'namespace':'main-computer-hub-smoke',
                        'private_transport':'DIRECT_ONLY',
                        'public_transport':'REAL_SEH',
'worker_account_id':org_account,
                        'reserve_account_id':reserve_account,
                        'earned_credit_wei':str(initial_available),
                        'assessed_credit_wei':str(4*10**17),
                        'swept_credit_wei':str(4*10**17),
                        'receipt_id':sweep['receipt_id'],
                        'limitations':{'external_chain_bridge':'NOT_TESTED',
                                       'private_over_seh':'NOT_IMPLEMENTED',
                                       'source_funding':'LAB_REQUESTER_SYNTHETIC'}}
            finally:
                direct_worker.close(reason='pinky_integrated_done')
        finally:
            _ok(_lab_http(local_port,'POST','/v1/local/organization/disable',{},bearer=local_token))
            signer_path.unlink(missing_ok=True)
