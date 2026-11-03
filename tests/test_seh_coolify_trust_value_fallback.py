"""Verified Coolify env readback when real_value is unusable, without SQL or key exposure."""
import json

import pytest

from tools.seh_control import git_deployment as git
from tests.test_seh_operator_trust_binding import configured, public_b64


def complicated_rows(key):
    return [
        {'key': 'SEH_SLH_ID', 'real_value': '[REDACTED]', 'value': 'slh'},
        {'key': 'SEH_SLH_PUBLIC_KEY_B64', 'real_value': '********', 'value': key},
    ]


def test_diagnostic_finds_valid_identity_and_old_key_from_value(tmp_path, monkeypatch):
    root, host, _ = configured(tmp_path, monkeypatch)
    old, new = public_b64(), public_b64()
    host.envs['app-one'] = complicated_rows(old)
    output = git.diagnostic_slh_trust(root, 'mainnet', 'company', slh_id='slh', public_key_b64=new, client=host)
    assert 'SEH_ORGANIZATION_ID' not in output['fields']
    assert output['fields']['SEH_SLH_ID']['status'] == 'MATCH'
    assert output['fields']['SEH_SLH_PUBLIC_KEY_B64']['status'] == 'MISMATCH'
    assert output['issues'] == ['SEH_SLH_PUBLIC_KEY_B64: MISMATCH']
    assert all(method == 'GET' for method, path in host.calls)
    assert old not in json.dumps(output) and new not in json.dumps(output)


def test_bind_rotates_key_only_when_existing_ids_match(tmp_path, monkeypatch):
    root, host, _ = configured(tmp_path, monkeypatch)
    old, new = public_b64(), public_b64()
    host.envs['app-one'] = complicated_rows(old)
    recorded = []
    original = host.request

    def inspect_mutation(method, path, body=None):
        if method == 'PATCH' and path.endswith('/envs/bulk'):
            recorded.append(body)
        # mimic Coolify: real_value may remain masked while value is updated
        return original(method, path, body)

    host.request = inspect_mutation
    with pytest.raises(git.SEHGitError, match='replace-existing-trust'):
        git.bind_slh(root, 'mainnet', 'company', slh_id='slh',
                     public_key_b64=new, client=host, execute=True)
    assert recorded == []
    result = git.bind_slh(root, 'mainnet', 'company', slh_id='slh',
                          public_key_b64=new, client=host, replace_existing=True, execute=True)
    assert result['status'] == 'TRUST_DEPLOY_QUEUED_NOT_VERIFIED'
    assert result['trust_readback_verified'] is True
    assert len(recorded) == 1
    assert len(recorded[0]['data']) == 1
    assert recorded[0]['data'][0]['key'] == 'SEH_SLH_PUBLIC_KEY_B64'
    assert host.envs['app-one'][0]['value'] == 'slh'


def test_conflicting_visible_fields_refuse_before_mutation(tmp_path, monkeypatch):
    root, host, _ = configured(tmp_path, monkeypatch)
    old, new = public_b64(), public_b64()
    host.envs['app-one'] = [
        {'key': 'SEH_SLH_ID', 'real_value':'other', 'value': 'slh'},
        {'key': 'SEH_SLH_PUBLIC_KEY_B64', 'value': old},
    ]
    with pytest.raises(git.SEHGitError, match='AMBIGUOUS'):
        git.bind_slh(root, 'mainnet', 'company', slh_id='slh',
                     public_key_b64=new, client=host, replace_existing=True, execute=True)
    assert all(method == 'GET' for method, path in host.calls)


def test_json_quoted_real_value_equivalent_to_plain_value():
    key = public_b64()
    data = [{'key': 'SEH_SLH_ID', 'value': 'slh', 'real_value': '"slh"'},
            {'key': 'SEH_SLH_PUBLIC_KEY_B64', 'value': key, 'real_value': json.dumps(key)}]
    assert git._diagnostic_env_value(data, 'SEH_SLH_ID')['value'] == 'slh'
    assert git._diagnostic_env_value(data, 'SEH_SLH_PUBLIC_KEY_B64')['value'] == key


def test_masked_key_without_valid_fallback_refuses(tmp_path, monkeypatch):
    root, host, _ = configured(tmp_path, monkeypatch)
    host.envs['app-one'] = complicated_rows('********')
    with pytest.raises(git.SEHGitError, match='MASKED'):
        git.bind_slh(root, 'mainnet', 'company', slh_id='slh',
                     public_key_b64=public_b64(), client=host, replace_existing=True, execute=True)
    assert all(method == 'GET' for method, path in host.calls)
