"""Read-only, field-specific SEH trust diagnosis; no key or token disclosure."""
import json

import pytest

from seh_mutate_harness import _parser, run, main
from tools.seh_control import git_deployment as g
from tools.seh_control.commands import SEHControl
from tests.test_seh_operator_trust_binding import configured, public_b64


def invoke(tmp_path, monkeypatch, rows, *, key=None):
    root, host, control = configured(tmp_path, monkeypatch)
    host.envs['app-one'] = rows
    before = list(host.calls)
    result = g.diagnostic_slh_trust(root, 'mainnet', 'company', slh_id='slh',
                                 public_key_b64=key, client=host)
    assert result['mutation_performed'] is False and result['sql_used'] is False
    assert set(m for m, _ in host.calls[len(before):]) == {'GET'}
    assert result['application_uuid'] == 'app-one'
    return result


def rows(org='org', slh='slh', key=None):
    return [{'key':'SEH_SLH_ID','value':slh},
            {'key':'SEH_SLH_PUBLIC_KEY_B64','value':key or public_b64()}]


def test_diagnostic_does_not_inspect_legacy_organization_identity(tmp_path, monkeypatch):
    key = public_b64()
    report = invoke(tmp_path, monkeypatch, rows(org='other', key=key), key=key)
    assert report['status'] == 'TRUST_MATCH'
    assert 'SEH_ORGANIZATION_ID' not in report['fields']
    assert report['fields']['SEH_SLH_ID']['status'] == 'MATCH'
    assert report['fields']['SEH_SLH_PUBLIC_KEY_B64']['status'] == 'MATCH'
    assert report['issues'] == []
    assert key not in json.dumps(report)


def test_diagnostic_identifies_exact_mismatched_slh(tmp_path, monkeypatch):
    key = public_b64()
    report = invoke(tmp_path, monkeypatch, rows(slh='not-my-slh', key=key), key=key)
    assert report['issues'] == ['SEH_SLH_ID: MISMATCH']
    assert report['fields']['SEH_SLH_ID']['observed'] == 'not-my-slh'


def test_diagnostic_identifies_public_key_mismatch_by_digest_only(tmp_path, monkeypatch):
    local, remote = public_b64(), public_b64()
    report = invoke(tmp_path, monkeypatch, rows(key=remote), key=local)
    assert report['issues'] == ['SEH_SLH_PUBLIC_KEY_B64: MISMATCH']
    assert report['fields']['SEH_SLH_PUBLIC_KEY_B64']['local_public_key_sha256'] != report['fields']['SEH_SLH_PUBLIC_KEY_B64']['remote_public_key_sha256']
    assert local not in json.dumps(report) and remote not in json.dumps(report)


@pytest.mark.parametrize('remote,expected', [
    ('********','MASKED'), ('', 'EMPTY'), ('[REDACTED]', 'MASKED')
])
def test_diagnostic_mask_and_empty_not_incorrectly_called_mismatch(tmp_path,monkeypatch,remote,expected):
    report = invoke(tmp_path,monkeypatch,rows(slh=remote),key=public_b64())
    assert report['fields']['SEH_SLH_ID']['status'] == expected
    assert report['fields']['SEH_SLH_ID']['observed'] is None


def test_diagnostic_reports_missing_duplicate_and_incomplete(tmp_path, monkeypatch):
    report = invoke(tmp_path,monkeypatch, [{'key':'SEH_SLH_ID','value':'slh'},
                                            {'key':'SEH_SLH_ID','value':'slh'}])
    assert 'SEH_ORGANIZATION_ID' not in report['fields']
    assert report['fields']['SEH_SLH_ID']['status'] == 'DUPLICATE'
    assert report['fields']['SEH_SLH_PUBLIC_KEY_B64']['status'] == 'MISSING'


def test_diagnostic_detects_invalid_remote_key_without_echoing(tmp_path,monkeypatch):
    report = invoke(tmp_path,monkeypatch,rows(key='invalid-EXTRA-SECRET'),key=public_b64())
    assert report['fields']['SEH_SLH_PUBLIC_KEY_B64']['status'] == 'INVALID_REMOTE_KEY'
    assert 'invalid-EXTRA-SECRET' not in json.dumps(report)


def test_diagnostic_local_key_unavailable_still_inspects_remote(tmp_path,monkeypatch):
    remote = public_b64()
    report = invoke(tmp_path,monkeypatch,rows(key=remote),key=None)
    assert report['fields']['SEH_SLH_PUBLIC_KEY_B64']['status'] == 'LOCAL_KEY_UNAVAILABLE'
    assert report['fields']['SEH_SLH_ID']['status'] == 'MATCH'


def test_operator_diagnostic_no_mutation_flags_and_json_stdout(tmp_path,monkeypatch,capsys):
    root,host,control=configured(tmp_path,monkeypatch)
    host.envs['app-one']=rows(slh='wrong',key=public_b64())
    import seh_mutate_harness as mod
    monkeypatch.setattr(mod, 'SEHControl',lambda *_:control)
    monkeypatch.setattr(g,'_client',lambda ctl:host)
    monkeypatch.setattr('tools.slh_seh_connector.service_identity',lambda state,create=False:(None,None))
    # An unavailable signing key must not prevent remote ID diagnosis.
    assert main(['diagnostic','--network','mainnet',
                 '--runtime-state-root',str(root)]) == 0
    io=capsys.readouterr()
    payload=json.loads(io.out)
    assert payload['fields']['SEH_SLH_ID']['status']=='MISMATCH'
    assert payload['fields']['SEH_SLH_ID']['status']=='MISMATCH'
    assert payload['mutation_performed'] is False
    assert 'SEH_SLH_ID: MISMATCH' in io.err
    assert all(method=='GET' for method,path in host.calls)


def test_diagnostic_refuses_unowned_coolify_application(tmp_path,monkeypatch):
    root,host,control=configured(tmp_path,monkeypatch)
    host.apps[0]['description']='unrelated-app'
    with pytest.raises(g.SEHGitError,match='ownership'):
        g.diagnostic_slh_trust(root,'mainnet','company',slh_id='slh',client=host)
    assert all(m=='GET' for m,p in host.calls)


def test_diagnostic_is_the_only_operator_name():
    parser = _parser()
    assert parser.parse_args(['diagnostic']).operation == 'diagnostic'
    with pytest.raises(SystemExit):
        parser.parse_args(['twiddle'])
