"""Task Manager shadow acceptance is distinct from renderer certification."""
from __future__ import annotations

from tools import mcel_task_manager_functional_shadow as shadow


def _plugin(**overrides):
    app={'appId':'semantic-task-manager','notifications':4,'consumers':1,
         'mutationSupported':True,
         'stats':{'executions':8,'contradictions':0,'predictedSkips':4,
                  'unverified':2,'unknownBatches':0},
         'coverage':{'completeScopes':8,'totalScoped':8,'unresolved':['independent-candidate-renderer']},
         'eventsEvicted':0,
         'correlation':{'schema':'mcel.reactivity.revision-correlation.v1',
                        'operations':[{'revision':1,'receipts':1,'origin':'dispatch'}],
                        'async':{'receiptMatches':1},
                        'producers':{'identified':0,'unclassified':0,'partitionInvariant':True,
                                     'classificationInvariant':True,'operationLinked':0,'independentUi':0,'unexplained':0}}}
    result={'oracleVersion':2,'productionSchedulerChanged':False,
            'applicationOperationsInvoked':0,'certified':False,'apps':[app]}
    result.update(overrides)
    return result


def _stages():
    return [{'phase':name,'notifications':n,'delta':{'notifications':n-(i-1) if i else 0}}
            for i,(name,n) in enumerate((('mounted',0),('refresh',1),('views',2),
                                           ('controls',3),('final',4)))]


def _refresh(status='PASS'):
    return {'status':status,'steps':[{'phase':'poll-registration','passed':status=='PASS'}]}


def test_oracle_coverage_incomplete_cannot_certify():
    report=shadow.evaluate(_plugin(),_stages(),{'status':'PASS'},[],_refresh())
    assert report['status']=='PASS_OBSERVATION'
    assert report['coverageStatus']=='INCOMPLETE'
    assert report['selectiveSchedulingCertified'] is False
    assert report['renderingEquivalenceTested'] is False
    assert 'independent-candidate-renderer' in report['coverageBlockers']


def test_contradiction_is_a_diagnostic_failure():
    raw=_plugin()
    raw['apps'][0]['stats']['contradictions']=1
    report=shadow.evaluate(raw,_stages(),{'status':'PASS'},[],_refresh())
    assert report['status']=='FAIL'
    assert 'PREDICTED_SKIP_CONTRADICTION' in report['failures']


def test_unobserved_or_missing_plugin_fails_closed():
    report=shadow.evaluate(None,_stages(),{'status':'PASS'},[],_refresh())
    assert report['status']=='FAIL'
    assert 'ORACLE_VERSION_MISMATCH' in report['failures']
    assert 'EXPECTED_ONE_TASK_MANAGER_SESSION' in report['failures']


def test_shadow_cannot_mutate_runtime_or_claim_certification():
    for broken in (_plugin(productionSchedulerChanged=True),
                   _plugin(applicationOperationsInvoked=1),
                   _plugin(certified=True)):
        result=shadow.evaluate(broken,_stages(),{'status':'PASS'},[],_refresh())
        assert result['status']=='FAIL'


def test_functional_failures_do_not_become_observation_passes():
    assert shadow.evaluate(_plugin(),_stages(),{'status':'INCOMPLETE'},[],_refresh())['status']=='FAIL'
    assert shadow.evaluate(_plugin(),_stages(),{'status':'PASS'},[],_refresh('FAIL'))['status']=='FAIL'


def test_real_promoted_task_manager_shadow_campaign():
    result=shadow.run()
    assert result['functional']['status']=='PASS'
    assert result['functional']['scenarioCount']>=35
    assert result['refresh']['status']=='PASS',result['refresh']
    assert result['evaluation']['status']=='PASS_OBSERVATION',result['evaluation']
    assert result['evaluation']['metrics']['consumers']>0
    assert result['evaluation']['metrics']['notifications']>0
    assert len(result['stages'])>=7
    assert result['plugin']['productionSchedulerChanged'] is False
    assert result['plugin']['certified'] is False
