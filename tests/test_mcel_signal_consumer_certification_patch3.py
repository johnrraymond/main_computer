"""Patch 3 adversarial class certification and isolated renderer parity gates."""
from __future__ import annotations

import copy

import pytest
from tools import mcel_signal_consumer_certification as cert
from tools import mcel_signal_differential_acceptance as diff


def test_adversarial_probes_are_separate_per_class_and_fail_closed():
    result=cert.run_adversarial_probes()
    assert result['status']=='PASS',result
    assert set(result['classes'])==set(diff.EXPERIMENTAL_KINDS)
    for kind,entry in result['classes'].items():
        assert entry['status']=='PASS', (kind,entry)
        assert [p['phase'] for p in entry['phases']]==list(cert.ADVERSARIAL_PHASES)
        assert all(p['status']=='PASS' for p in entry['phases'])
        assert entry['privacyPreserved'] is True
        assert entry['productionSchedulerChanged'] is False
        assert entry['productionCertified'] is False
        assert any(p['skipped'] for p in entry['phases'] if p['phase']=='unrelated-state')
        assert any(not p['skipped'] for p in entry['phases'] if p['phase']=='changed-active-input')


def test_mounted_candidate_html_only_authorizes_requested_class():
    for app in ('slh','task-manager'):
        for kind in diff.EXPERIMENTAL_KINDS:
            html=diff._html(app,True,candidate_kinds=(kind,))
            assert html.count('signalCandidateIsolatedTest:true')==1
            assert html.count(diff.GATE)==1
            assert html.count('signalCandidateTestKinds:["'+kind+'"]')==1
        assert 'signalCandidateTestKinds:' not in diff._html(app,False)
    for bad in ((), ('invalid',), ('condition','condition')):
        with pytest.raises(ValueError,match='INVALID_EXPERIMENTAL_CONSUMER_CLASSES'):
            diff._html('slh',True,candidate_kinds=bad)


def test_certification_rejects_parity_failure_missing_stress_or_unexercised_consumer(monkeypatch):
    class Result:
        @staticmethod
        def baseline(kind,app):
            return {'status':'PASS_DIFFERENTIAL','failures':[],'errors':{},'browser':'fixture',
             'candidate':{'observedConsumersByClass':{kind:1},'actualSkips':3},
             'stages':[{'phase':p,'differenceCount':0} for p in cert.BROWSER_STRESS_PHASES],
             'boundaries':{'productionSchedulerChanged':False}}
    kind='display';app='task-manager'
    monkeypatch.setattr(cert,'run_adversarial_probes',lambda repo: {
        'status':'PASS','classes':{kind:{'status':'PASS'}}})
    original=Result.baseline(kind,app)
    def run_with(edit):
        result=copy.deepcopy(original);edit(result)
        monkeypatch.setattr(cert,'run_differential',lambda *a,**kw:result)
        return cert.certify(apps=(app,),kinds=(kind,))
    ok=run_with(lambda r:None)
    assert ok['status']=='PASS_ISOLATED_CERTIFICATION'
    assert ok['boundaries']['productionCutoverAuthorized'] is False
    for change in (
      lambda r:r.update(status='FAIL_DIFFERENTIAL',failures=['DOM_MISMATCH']),
      lambda r:r['stages'].pop(),
      lambda r:r['candidate'].update(actualSkips=0),
    ):
        report=run_with(change)
        assert report['status']=='BLOCKED'
        assert report['classes'][0]['status']=='BLOCKED'
    unused=run_with(lambda r:r['candidate'].update(
        observedConsumersByClass={kind:0},actualSkips=0))
    assert unused['status']=='BLOCKED', 'Unexercised class cannot be certified'
    assert unused['classes'][0]['applications'][0]['status']=='NOT_APPLICABLE'


@pytest.mark.parametrize('kind,app',[
    ('condition','slh'),('display','task-manager'),
    ('selected-record','slh'),('typed-record','task-manager'),
])
def test_individual_class_has_real_browser_skips_and_adversarial_parity(kind,app):
    pytest.importorskip('playwright')
    report=diff.run_differential(app,candidate_kinds=(kind,),stress=True,require_skips=False)
    assert report['status']=='PASS_DIFFERENTIAL',(kind,report.get('failures'),report.get('errors'))
    assert report['candidate']['experimentalClasses']==[kind]
    assert report['candidate']['actualSkipsByClass'].get(kind,0)>0
    assert sum(report['candidate']['actualSkipsByClass'].values())==report['candidate']['actualSkips']
    assert all(stage['differenceCount']==0 for stage in report['stages'])
    assert set(cert.BROWSER_STRESS_PHASES).issubset({s['phase'] for s in report['stages']})
    assert report['boundaries']['productionSchedulerChanged'] is False
