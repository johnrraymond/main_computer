"""The SLH functional battery must expose oracle success and failure honestly."""
from __future__ import annotations

import json

from tools import mcel_slh_full_functional_acceptance as full
from tools.mcel_slh_functional_shadow import evaluate_shadow, run_mocked_functional_shadow


def _sample():
    return {'oracleVersion':2,'certified':False,
            'productionSchedulerChanged':False,'applicationOperationsInvoked':0,
            'apps':[{'appId':'slh','notifications':4,'consumers':2,
                     'mutationSupported':True,
                     'stats':{'executions':8,'contradictions':0,'predictedSkips':4,
                              'possibleStateOnlySkips':2,'incompleteScopes':1,
                              'unknownBatches':1,'unverified':3,'asyncMutations':2},
                     'coverage':{'completeScopes':7,'totalScoped':8,
                                 'unresolved':['independent-candidate-renderer']}}]}


def _stages():
    return [{'phase':phase,'functionalPass':True,'deltaNotifications':1}
            for phase in ('seed-running','stop','start','disable','enable')]


def _actions():
    return ['toggle','toggle','toggle-enablement','toggle-enablement']


def test_shadow_observation_success_is_not_rendering_certification():
    verdict=evaluate_shadow(_sample(),_stages(),[],_actions())
    assert verdict['status']=='PASS_OBSERVATION'
    assert verdict['coverageStatus']=='INCOMPLETE'
    assert verdict['selectiveSchedulingCertified'] is False
    assert 'independent-candidate-renderer' in verdict['coverageBlockers']
    assert verdict['metrics']['predictedSkips']==4


def test_shadow_observation_detects_contradiction_and_wrong_state():
    data=_sample()
    data['apps'][0]['stats']['contradictions']=1
    stages=_stages()
    stages[2]['functionalPass']=False
    verdict=evaluate_shadow(data,stages,[],_actions())
    assert verdict['status']=='FAIL'
    assert 'PREDICTED_SKIP_CONTRADICTION' in verdict['failures']
    assert 'UI_STATE_MISMATCH' in verdict['failures']


def test_shadow_observation_rejects_missing_events_or_scheduler_authority():
    data=_sample()
    data['productionSchedulerChanged']=True
    data['apps'][0]['notifications']=0
    data['apps'][0]['stats']['executions']=0
    data['apps'][0]['coverage']['completeScopes']=0
    verdict=evaluate_shadow(data,_stages(),['page crashed'],_actions())
    assert verdict['status']=='FAIL'
    assert {'SHADOW_AUTHORITY_VIOLATION','SHADOW_NOT_COLLECTING','BROWSER_PAGE_ERRORS'} <= set(verdict['failures'])
    stages=_stages()
    stages[1]['deltaNotifications']=0
    assert 'NO_OBSERVATIONS_DURING_STOP' in evaluate_shadow(_sample(),stages,[],_actions())['failures']
    assert 'LIFECYCLE_PHASE_COVERAGE_INCOMPLETE' in evaluate_shadow(_sample(),stages[:-1],[],_actions())['failures']


def test_companion_lane_is_included_in_report_without_conflating_intent_status(tmp_path,monkeypatch):
    monkeypatch.setattr(full,'run_case',lambda item,timeout:{'status':'PASS'})
    invoked=[]
    def fake_shadow(*,destination,timeout):
        invoked.append((destination,timeout))
        return {'status':'PASS_OBSERVATION','coverageStatus':'INCOMPLETE',
                'selectiveSchedulingCertified':False,'metrics':{'notifications':7,'contradictions':0}}
    monkeypatch.setattr(full,'run_shadow_lane',fake_shadow)
    target=tmp_path/'test-report.json'
    code=full.main(['--mode','isolated','--case','identity-discovery',
                    '--shadow','on','--report',str(target)])
    report=json.loads(target.read_text())
    assert code==4  # one curated case does not certify the whole app
    assert report['shadow']['status']=='PASS_OBSERVATION'
    assert report['shadow']['coverageStatus']=='INCOMPLETE'
    assert report['combined_status']=='INCOMPLETE'
    assert report['completed_cases']==1
    assert len(invoked)==1
    assert invoked[0][0]==target.with_name('test-report-shadow.json')


def test_companion_lane_failure_is_not_hidden_by_functional_pass(tmp_path,monkeypatch):
    monkeypatch.setattr(full,'run_case',lambda item,timeout:{'status':'PASS'})
    monkeypatch.setattr(full,'run_shadow_lane',lambda **kwargs:{
        'status':'FAIL','coverageStatus':'INCOMPLETE',
        'selectiveSchedulingCertified':False,'failures':['PREDICTED_SKIP_CONTRADICTION']})
    target=tmp_path/'report.json'
    code=full.main(['--mode','isolated','--case','identity-discovery',
                    '--shadow','on','--report',str(target)])
    report=json.loads(target.read_text())
    assert code==1
    assert report['combined_status']=='FAIL'
    assert report['shadow']['failures']==['PREDICTED_SKIP_CONTRADICTION']
    assert report['cases'][1]['result']['status'] in ('NOT_RUN','PASS')


def test_live_browser_fixture_collects_per_phase_oracle_and_preserves_ui():
    report=run_mocked_functional_shadow()
    verdict=report['evaluation']
    assert verdict['status']=='PASS_OBSERVATION', verdict
    assert verdict['coverageStatus']=='INCOMPLETE'
    assert not verdict['selectiveSchedulingCertified']
    assert verdict['metrics']['notifications']>0
    assert verdict['metrics']['executions']>0
    assert verdict['metrics']['contradictions']==0
    assert verdict['metrics']['completeScopes']>0
    assert verdict['metrics']['predictedSkips']>0
    assert [x['phase'] for x in report['stages']]==['seed-running','stop','start','disable','enable']
    assert all(x['functionalPass'] for x in report['stages'])
    assert all(x['deltaNotifications']>0 for x in report['stages'])
    assert report['safety']=={'productionSchedulerChanged':False,
                             'applicationOperationsInvoked':0,
                             'backendCallsMocked':True,'candidateRendererUsed':False}
    assert report['diagnosticFindings']
