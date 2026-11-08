"""Permanent MSK lifetime/revocation boundary; never uses production services."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from main_computer.multisession_key_signing import build_personal_sign_blob, private_key_to_address
from main_computer.stable_hub_msk import (
    InMemoryStableMultiSessionKeyStore, StableHubMultiSessionKeyService,
    STABLE_MSK_KEY_LIFETIME,
)
from main_computer.stable_hub_topology import load_stable_hub_topology
from pathlib import Path

PRIVATE = '0x' + '11'*32
WALLET = private_key_to_address(PRIVATE)


def _signed(private, *, action=None, key_id=None, cluster_id=None):
    now = datetime.now(timezone.utc)
    addr = private_key_to_address(private)
    msg = {
        'purpose':'request_multi_session_key', 'wallet_address':addr,
        'chain_id':'42424242','request_id':'lifecycle-test-' + (key_id or 'new'),
        'issued_at':now.isoformat(), 'expires_at':(now+timedelta(minutes=5)).isoformat(),
        'user_slug':'life_' + 'x'*40,
    }
    if action:
        msg.update(action=action,key_id=key_id,cluster_id=cluster_id)
    return build_personal_sign_blob(message=msg, private_key=private,
                                    wallet_address=addr, chain_id='42424242')


def _service(store=None):
    topo = load_stable_hub_topology(Path('deploy/hub-topology/dev-topology.json'))
    return StableHubMultiSessionKeyService(
        topology=topo, hub_id='dev-hub1', store=store or InMemoryStableMultiSessionKeyStore())


def _validate(svc, key):
    return svc.validate_key({'multisession_authorization':{
        'kind':'multisession_key','multisession_key_id':key}})


def test_new_msk_expires_within_fixed_maximum_lifetime():
    svc = _service()
    key = svc.request_key({'signed_request':_signed(PRIVATE)})['key']
    assert datetime.fromisoformat(key['expires_at']) - datetime.fromisoformat(key['created_at']) <= STABLE_MSK_KEY_LIFETIME + timedelta(seconds=1)
    assert _validate(svc,key['id'])['valid'] is True


def test_expired_legacy_msk_cannot_authorize_even_if_status_is_active():
    now = datetime.now(timezone.utc)
    record={'id':'msk_expired', 'status':'active','wallet_address':WALLET,
            'chain_id':'42424242','created_at':(now-timedelta(days=9)).isoformat()}
    svc = _service(InMemoryStableMultiSessionKeyStore({'keys':{'msk_expired':record}}))
    assert _validate(svc,'msk_expired')['reason_code'] == 'key_expired'


def test_missing_or_future_issuance_timestamp_fails_closed():
    now = datetime.now(timezone.utc)
    records={'a':{'id':'a','status':'active','wallet_address':WALLET,'chain_id':'42424242'},
             'b':{'id':'b','status':'active','wallet_address':WALLET,'chain_id':'42424242',
                  'created_at':(now+timedelta(days=1)).isoformat()}}
    svc = _service(InMemoryStableMultiSessionKeyStore({'keys':records}))
    assert _validate(svc,'a')['reason_code'] == 'key_lifetime_invalid'
    assert _validate(svc,'b')['reason_code'] == 'key_lifetime_invalid'


def test_wallet_signed_revoke_is_atomic_and_rejects_cross_wallet():
    store=InMemoryStableMultiSessionKeyStore()
    svc=_service(store)
    key=svc.request_key({'signed_request':_signed(PRIVATE)})['key']['id']
    other='0x'+'22'*32
    bad=svc.revoke_key if False else None
    try:
        svc.revoke_key({'signed_request':_signed(other,action='revoke_multisession_key',
                                                key_id=key,cluster_id=svc.cluster_id)})
    except ValueError:
        pass
    else:
        raise AssertionError('cross-wallet key revocation accepted')
    assert _validate(svc,key)['valid'] is True
    success=svc.revoke_key({'signed_request':_signed(PRIVATE,action='revoke_multisession_key',
                                                     key_id=key,cluster_id=svc.cluster_id)})
    assert success['revoked'] is True
    assert _validate(svc,key)['reason_code'] == 'key_not_active'
    assert store.load()['keys'][key]['status']=='revoked'


def test_signature_must_bind_action_key_and_cluster():
    svc=_service()
    key=svc.request_key({'signed_request':_signed(PRIVATE)})['key']['id']
    for action,cluster in [('request_key',svc.cluster_id), ('revoke_multisession_key','foreign')]:
        try:
            svc.revoke_key({'signed_request':_signed(PRIVATE,action=action,key_id=key,cluster_id=cluster)})
        except ValueError:
            pass
        else:
            raise AssertionError('unsigned/mismatched intent accepted')
    assert _validate(svc,key)['valid'] is True


def test_revocation_signature_cannot_be_reused_to_issue_a_new_key():
    svc = _service()
    key = svc.request_key({'signed_request': _signed(PRIVATE)})['key']['id']
    revoke = _signed(PRIVATE, action='revoke_multisession_key',
                     key_id=key, cluster_id=svc.cluster_id)
    try:
        svc.request_key({'signed_request': revoke})
    except ValueError as exc:
        assert 'not a key-issuance intent' in str(exc)
    else:
        raise AssertionError('revocation signature accepted as a key issuance')
    assert _validate(svc, key)['valid'] is True
