"""Read-only Stable Hub inspection must bind runtime proof to resolved Coolify UUID.

Prior live redeployment passed, but subsequent inspect omitted the Coolify
application UUID and accepted Hub admin address needed by runtime attestation.
Tests use no production endpoints or private keys.
"""
from __future__ import annotations

from types import SimpleNamespace

from tools.hub_control import inspect as inspector
from tools.hub_control.common.models import HubContext


def _ctx(tmp_path):
    return HubContext.from_repo(tmp_path)


def _accepted():
    return {
        'generation': 19, 'hubs': [
            {'hub_id':'mainneta-hub1','controller_id':'coolify-a',
             'host_id':'host-a','public_url':'https://mainneta-hub1.example.invalid',
             'hub_admin_address':'0x'+'ab'*20,
             'fdb_contract':{'sha256':'f'},'chain_contract':{'sha256':'c'}},
        ],
    }


def _seed(monkeypatch):
    monkeypatch.setattr(inspector, 'read_accepted', lambda *a:_accepted())
    monkeypatch.setattr(inspector, 'load_current_fdb_contract', lambda *a:SimpleNamespace(
        payload={'namespace':'mainnet'},sha256='f',reference=lambda:{'sha256':'f'}))
    monkeypatch.setattr(inspector, 'load_current_chain_contract', lambda *a:SimpleNamespace(
        payload={'chain_id':42424240,'rpc_url':'https://rpc.example.invalid'},sha256='c',
        reference=lambda:{'sha256':'c'}))
    monkeypatch.setattr(inspector, 'load_private',lambda *a:{'networks':{'mainnet':{}}})
    monkeypatch.setattr(inspector, 'controller_coordinates', lambda *a, **kw:{
        'url':'https://coolify.example.invalid','api_token':'SECRET-not-to-leak',
        'project_uuid':'project-a','server_uuid':'server-a'})
    monkeypatch.setattr(inspector, '_client',lambda t: object())
    monkeypatch.setattr(inspector, '_inspect_frozen_application',lambda *a:{
        'present':True,'dockerfile_location':'/Dockerfile.hub.stable'})


def test_live_inspect_passes_frozen_uuid_coolify_and_admin_to_stable_attestation(monkeypatch, tmp_path):
    _seed(monkeypatch)
    monkeypatch.setattr(inspector, 'inspect_deployment',lambda *a:{
        'application_uuid':'uuid-existing-a','present':True,'placement_mismatch':False,
        'migration_candidate':False})
    seen=[]
    def proof(target, *, wait_timeout_s):
        seen.append(target)
        assert target['hub_runtime']=='stable'
        assert target['application_uuid']=='uuid-existing-a'
        assert target['coolify']['api_token']=='SECRET-not-to-leak'
        assert target['hub_admin_address']=='0x'+'ab'*20
        assert target['fdb_contract']['namespace']=='mainnet'
        assert target['chain_contract']['chain_id']==42424240
        assert target['public_url']=='https://mainneta-hub1.example.invalid'
        assert wait_timeout_s==0
        return {'verified':True,'reason':'runtime-attestation-verified',
                'hub_running':True,'fdb_adoption_verified':True,'chain_adoption_verified':True}
    result=inspector.inspect_network(_ctx(tmp_path),'mainnet',observer=proof)
    assert result['topology_verification']['verified'] is True
    assert result['hubs'][0]['verification']['verified'] is True
    assert len(seen)==1
    assert 'SECRET-not-to-leak' not in str(result)


def test_live_inspect_missing_uuid_fails_closed_without_observing(monkeypatch,tmp_path):
    _seed(monkeypatch)
    monkeypatch.setattr(inspector, 'inspect_deployment',lambda *a:{
        'application_uuid':None,'present':False,'placement_mismatch':False})
    def should_not_observe(*a,**k):
        raise AssertionError('invalid Coolify identity reached runtime observer')
    result=inspector.inspect_network(_ctx(tmp_path),'mainnet',observer=should_not_observe)
    assert result['topology_verification']['verified'] is False
    assert result['hubs'][0]['verification']['reason']=='hub-runtime-unverified'
    assert 'verified Coolify application' in result['hubs'][0]['verification']['error']


def test_live_inspect_missing_admin_cannot_pass_bridge_gate(monkeypatch,tmp_path):
    _seed(monkeypatch)
    accepted=_accepted()
    del accepted['hubs'][0]['hub_admin_address']
    monkeypatch.setattr(inspector,'read_accepted',lambda *a:accepted)
    monkeypatch.setattr(inspector,'inspect_deployment',lambda *a:{
        'application_uuid':'uuid-existing-a','present':True,'placement_mismatch':False,
        'migration_candidate':False})
    seen=[]
    def should_not_observe(*a,**k):
        seen.append(True)
        return {'verified':True}
    result=inspector.inspect_network(_ctx(tmp_path),'mainnet',observer=should_not_observe)
    assert not seen
    assert result['topology_verification']['verified'] is False
    assert 'lacks verified attestation or admin identity' in result['hubs'][0]['verification']['error']


def test_inspect_does_not_override_contracts_with_runtime_resolver(monkeypatch,tmp_path):
    _seed(monkeypatch)
    def malicious_resolver(*a):
        return {'hub_runtime':'stable','application_uuid':'uuid','coolify':{'url':'u','api_token':'secret'},
                'hub_admin_address':'0x'+'ab'*20,
                'fdb_contract':{'namespace':'wrong'},'chain_contract':{'chain_id':0},
                'public_url':'https://forged.invalid'}
    def observer(target,**kwargs):
        assert target['fdb_contract']['namespace']=='mainnet'
        assert target['chain_contract']['chain_id']==42424240
        assert target['public_url']=='https://mainneta-hub1.example.invalid'
        return {'verified':True,'hub_running':True,
                'fdb_adoption_verified':True,'chain_adoption_verified':True}
    result=inspector.inspect_network(_ctx(tmp_path),'mainnet',observer=observer,
                                     runtime_resolver=malicious_resolver)
    assert result['topology_verification']['verified'] is True


def test_inspect_preserves_real_runtime_verifier_failures(monkeypatch,tmp_path):
    _seed(monkeypatch)
    monkeypatch.setattr(inspector, 'inspect_deployment',lambda *a:{
        'application_uuid':'uuid-existing-a','present':True,'placement_mismatch':False,
        'migration_candidate':False})
    def negative(target, **kwargs):
        return {'verified':False,'reason':'stable-runtime-attestation-key-unavailable',
                'endpoint_errors':{'attestation_key':'Hub attestation key unavailable'},
                'checks':{'healthy':True,'chain_rpc':False}}
    result=inspector.inspect_network(_ctx(tmp_path),'mainnet',observer=negative)
    assert result['topology_verification']['verified'] is False
    assert result['hubs'][0]['verification']['reason']=='stable-runtime-attestation-key-unavailable'
    assert result['hubs'][0]['verification']['checks']['chain_rpc'] is False


def _generation_only_contract_seed(monkeypatch, *, old_generation=20, current_generation=33,
                                   changed_chain_fields=None, fdb_matches=True):
    from tools.hub_control.common.canonical import sha256_json
    from types import SimpleNamespace
    from tools.hub_control import inspect as inspector

    current_core = {'schema': 'main-computer.chain.consumer-contract.v1',
                    'network': 'mainnet', 'generation': current_generation,
                    'chain_id': 42424240, 'rpc_url': 'https://rpc.example.invalid',
                    'contracts': {'escrow': '0x' + 'cd' * 20},
                    'compatibility': {'json_rpc': 'ethereum'}}
    current_sha = sha256_json(current_core)
    current = SimpleNamespace(generation=current_generation, sha256=current_sha,
                              payload={**current_core, 'sha256': current_sha},
                              reference=lambda: {'generation': current_generation,
                                                 'sha256': current_sha})
    historical_core = {**current_core, 'generation': old_generation,
                       **(changed_chain_fields or {})}
    accepted = _accepted()
    accepted['hubs'][0]['chain_contract'] = {
        'generation': old_generation,
        'sha256': sha256_json(historical_core),
    }
    if not fdb_matches:
        accepted['hubs'][0]['fdb_contract']['sha256'] = 'wrong'
    monkeypatch.setattr(inspector, 'read_accepted', lambda *args: accepted)
    monkeypatch.setattr(inspector, 'load_current_chain_contract', lambda *args: current)
    return current, accepted


def test_generation_only_chain_drift_still_requires_live_hub_proof(monkeypatch, tmp_path):
    _seed(monkeypatch)
    current, accepted = _generation_only_contract_seed(monkeypatch)
    seen=[]
    def verified_runtime(target, *, wait_timeout_s):
        assert target['chain_contract'] == current.payload
        assert target['fdb_contract']['namespace'] == 'mainnet'
        seen.append(target['hub_id'])
        return {'verified': True, 'hub_running': True,
                'fdb_adoption_verified': True, 'chain_adoption_verified': True,
                'checks': {key: True for key in (
                    'healthy', 'stable_identity', 'stable_topology',
                    'fdb_backend', 'fdb_cluster_file', 'fdb_namespace',
                    'chain_id', 'chain_rpc')}}
    result = inspector.inspect_network(_ctx(tmp_path), 'mainnet',
                                       observer=verified_runtime,
                                       runtime_resolver=lambda *a: 'stable')
    assert seen == ['mainneta-hub1']
    assert result['topology_verification']['verified'] is True
    v = result['hubs'][0]['verification']
    assert v['chain_reference_current'] is False
    assert v['chain_generation_only_compatible'] is True
    assert v['accepted_chain_generation'] == 20
    assert v['current_chain_generation'] == 33
    assert result['hubs'][0]['chain_contract'] == accepted['hubs'][0]['chain_contract']
    assert result['accepted_generation'] == 19


def test_generation_only_compatibility_does_not_mask_failed_runtime_attestation(monkeypatch, tmp_path):
    _seed(monkeypatch)
    _generation_only_contract_seed(monkeypatch)
    result = inspector.inspect_network(
        _ctx(tmp_path), 'mainnet',
        observer=lambda *a, **k: {'verified': False, 'reason': 'runtime-attestation-failed',
                                  'chain_adoption_verified': False},
        runtime_resolver=lambda *a: 'stable')
    assert result['topology_verification']['verified'] is False
    v = result['hubs'][0]['verification']
    assert v['chain_generation_only_compatible'] is True
    assert v['reason'] == 'runtime-attestation-failed'
    assert result['hubs'][0]['chain_status'] == 'stale-or-unverified'


def test_true_chain_contract_change_stays_stale_before_network_observation(monkeypatch, tmp_path):
    _seed(monkeypatch)
    _generation_only_contract_seed(monkeypatch,
                                  changed_chain_fields={'rpc_url': 'https://old-rpc.example.invalid'})
    seen=[]
    result = inspector.inspect_network(_ctx(tmp_path), 'mainnet',
                                       observer=lambda *a, **k: seen.append('called'),
                                       runtime_resolver=lambda *a: 'stable')
    assert not seen
    assert result['topology_verification']['verified'] is False
    assert result['hubs'][0]['verification']['reason'] == 'hub-dependency-contract-stale'
    assert result['hubs'][0]['verification']['chain_generation_only_compatible'] is False


def test_fdb_drift_still_blocks_even_when_chain_generation_only(monkeypatch, tmp_path):
    _seed(monkeypatch)
    _generation_only_contract_seed(monkeypatch, fdb_matches=False)
    seen=[]
    result = inspector.inspect_network(_ctx(tmp_path), 'mainnet',
                                       observer=lambda *a, **k: seen.append('called'),
                                       runtime_resolver=lambda *a: 'stable')
    assert not seen
    assert result['topology_verification']['verified'] is False
    assert result['hubs'][0]['verification']['reason'] == 'hub-dependency-contract-stale'


def test_generation_only_digest_requires_valid_historical_generation_and_current_digest(monkeypatch, tmp_path):
    _seed(monkeypatch)
    for invalid_generation in [True, 0, 34, '20']:
        current, accepted = _generation_only_contract_seed(
            monkeypatch, old_generation=invalid_generation)
        assert inspector._chain_generation_only_equivalent(
            current, accepted['hubs'][0]['chain_contract']) is False
    current, accepted = _generation_only_contract_seed(monkeypatch)
    current.payload['rpc_url'] = 'https://forged-rpc.example.invalid'
    assert inspector._chain_generation_only_equivalent(
        current, accepted['hubs'][0]['chain_contract']) is False


def test_generation_only_observer_verified_bit_is_not_sufficient(monkeypatch, tmp_path):
    _seed(monkeypatch)
    _generation_only_contract_seed(monkeypatch)
    result = inspector.inspect_network(_ctx(tmp_path), 'mainnet',
                                       observer=lambda *a, **k: {
                                           'verified': True, 'hub_running': True,
                                           'fdb_adoption_verified': True, 'chain_adoption_verified': True,
                                           'checks': {'healthy': True, 'chain_rpc': True}},
                                       runtime_resolver=lambda *a: 'stable')
    assert result['topology_verification']['verified'] is False
    assert result['hubs'][0]['verification']['reason'] == 'generation-only-runtime-proof-incomplete'


def test_generation_only_legacy_observer_cannot_bypass_stable_attestation(monkeypatch, tmp_path):
    _seed(monkeypatch)
    _generation_only_contract_seed(monkeypatch)
    result = inspector.inspect_network(_ctx(tmp_path), 'mainnet',
                                       observer=lambda *a, **k: {
                                           'verified': True, 'hub_running': True,
                                           'fdb_adoption_verified': True, 'chain_adoption_verified': True,
                                           'checks': {key: True for key in (
                                               'healthy', 'stable_identity', 'stable_topology',
                                               'fdb_backend', 'fdb_cluster_file', 'fdb_namespace',
                                               'chain_id', 'chain_rpc')}},
                                       runtime_resolver=lambda *a: 'experimental')
    assert result['topology_verification']['verified'] is False
    assert result['hubs'][0]['verification']['reason'] == 'generation-only-runtime-proof-incomplete'
