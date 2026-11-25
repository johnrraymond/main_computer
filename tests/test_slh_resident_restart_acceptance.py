"""R01 acceptance cannot pass on repeated reads or weak signer ACLs."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from tools.slh_seh_lab import resident_restart as lane
from tools.slh_seh_lab import org_msk_passthrough as gate

CALLER = 'S-1-5-21-1-2-3-1000'


def acl():
    return {'owner': CALLER, 'protected': True, 'rules': [
        {'sid': sid, 'allow': True, 'inherited': False, 'inherit_only': False, 'rights': 2032127}
        for sid in (CALLER, 'S-1-5-18', 'S-1-5-32-544')]}


@pytest.mark.parametrize('defect', ['none','broad','inherited','unprotected','owner','missing','deny','weak'])
def test_acl_metadata_requires_exact_trusted_principals(defect):
    record = acl()
    if defect == 'broad': record['rules'].append({'sid':'S-1-1-0','allow':True,'inherited':False,'inherit_only':False,'rights':1})
    if defect == 'inherited': record['rules'][0]['inherited'] = True
    if defect == 'unprotected': record['protected'] = False
    if defect == 'owner': record['owner'] = 'S-1-1-0'
    if defect == 'missing': record['rules'].pop()
    if defect == 'deny': record['rules'][0]['allow'] = False
    if defect == 'weak': record['rules'][0]['rights'] = 1
    assert lane.acl_is_private(record, CALLER) is (defect == 'none')


def report(pid=101, created=1000., captured=1020.):
    binding = {'wallet_address':'0x'+'ab'*20, 'hub_id':'hub', 'cluster_id':'cluster', 'chain_id':'42424240',
               'account_id':'account', 'hub_url':'https://mainnet.example', 'trust_mode':'SYSTEM_TLS'}
    policy = lane.policy_evidence({'enabled':False,'revision':1,'hub_url':binding['hub_url'],'interval_seconds':30},
        {'enabled':False,'revision':0,'max_request_credit_wei':'0','budget_credit_wei':'0'})
    return {'status':'PASS_PREFLIGHT_ONLY', 'restart_evidence': {'schema':lane.SCHEMA,
        'process':{'pid':pid,'created':created},'process_stable':True,'captured_at':captured,
        'readiness_checked_at':captured-5,'binding':lane.binding_evidence(binding),'policy':policy,
        'signer_acl':{'platform':'WINDOWS','directory_private':True,'file_private':True}}}


@pytest.mark.parametrize('defect', ['none','same-process','wallet','hub','policy','old-live','stale','acl','blocked','no-baseline','unstable'])
def test_restart_is_fail_closed(monkeypatch, defect):
    before, after = report(), report(202, 1050., 1070.)
    monkeypatch.setattr(lane, 'old_process_gone', lambda _: defect != 'old-live')
    e = after['restart_evidence']
    if defect == 'same-process': e['process'] = before['restart_evidence']['process']
    if defect == 'wallet': e['binding']['wallet_address'] = '0x'+'cd'*20
    if defect == 'hub': e['binding']['cluster_id'] = 'different'
    if defect == 'policy': e['policy']['requester']['enabled'] = True
    if defect == 'stale': e['readiness_checked_at'] = 1000.
    if defect == 'acl': e['signer_acl']['file_private'] = False
    if defect == 'blocked': after['status'] = 'BLOCKED'
    if defect == 'no-baseline': before = {}
    if defect == 'unstable': e['process_stable'] = False
    result = lane.compare(before,after)
    assert (result['status']=='PASS_RESTART') is (defect=='none'), result


def test_cli_archives_before_and_after_and_does_not_overwrite_baseline_on_failure(tmp_path, monkeypatch):
    before, after = report(), report(202, 1050., 1070.)
    reports = iter([before, {'status':'BLOCKED'}, after])
    monkeypatch.setattr(gate, 'resident', lambda *, restart_evidence: next(reports))
    monkeypatch.setattr(lane,'old_process_gone',lambda _:True)
    base = ['--resident','--report-dir',str(tmp_path)]
    assert gate.main(base+['--restart-baseline']) == 0
    saved = tmp_path/'resident-restart-before.json'
    contents = saved.read_bytes()
    assert gate.main(base+['--restart-baseline']) == 1
    assert saved.read_bytes() == contents
    assert gate.main(base+['--verify-restart']) == 0
    result = json.loads((tmp_path/'resident-restart-after.json').read_text())
    assert result['status'] == 'PASS_RESTART'
    assert saved.read_bytes() == contents


def test_policy_projection_ignores_accounting_counters_and_unknown_fields():
    p={'enabled':False,'revision':1,'max_request_credit_wei':'0','budget_credit_wei':'0','reserved_ceiling_credit_wei':'5','secret':'not-for-report'}
    c={'enabled':False,'revision':2,'hub_url':'https://mainnet.example','interval_seconds':30}
    observed=lane.policy_evidence(c,p)
    assert 'secret' not in str(observed) and 'reserved_ceiling' not in str(observed)
    assert observed==lane.policy_evidence(c,{**p,'reserved_ceiling_credit_wei':'8'})
