"""Patch 2: real isolated candidate execution, parity and fail-closed gates."""
from copy import deepcopy
import pytest
from tools import mcel_signal_differential_acceptance as d


def test_only_isolated_candidate_html_supplies_both_execution_gates():
 for app in ('slh','task-manager'):
  incumbent=d._html(app,False)
  candidate=d._html(app,True)
  assert d.GATE not in incumbent and 'signalCandidateIsolatedTest:true' not in incumbent
  assert candidate.count(d.GATE)==1
  assert candidate.count('signalCandidateIsolatedTest:true')==1


def test_comparator_detects_each_material_failure_type():
 x={'rows':[{'path':'0','attrs':{'aria-pressed':'true'},'text':'Run','geometry':[0,0,100,20]}],
   'state':{'state:running':True},'focus':'0','selection':None,
   'receipts':[{'intent':'intent:refresh','disposition':'completed'}],
   'transport':[{'action':'toggle'}],'effectCount':0,'overflow':False}
 assert d._diff(x,deepcopy(x))[0]==0
 for mutate in (
   lambda y:y['rows'][0]['attrs'].update({'aria-pressed':'false'}),
   lambda y:y['rows'][0].update({'text':'Stop'}),
   lambda y:y['rows'][0].update({'geometry':[0,0,99,20]}),
   lambda y:y['state'].update({'state:running':False}),
   lambda y:y.update({'focus':'outside-root'}),
   lambda y:y['receipts'].clear(),
   lambda y:y['transport'].append({'action':'toggle'}),
   lambda y:y.update({'effectCount':1}),
 ):
  y=deepcopy(x);mutate(y)
  count,differences=d._diff(x,y)
  assert count>0 and differences


@pytest.mark.parametrize('app,phases',[
 ('slh',['mounted-and-initial-read','seed-running','stop','start','disable','enable']),
 ('task-manager',['mounted-and-initial-read','manual-refresh','connections','hardware','schedules','automatic-poll']),
])
def test_candidate_actually_skips_with_parity(app,phases):
 pytest.importorskip('playwright')
 result=d.run_differential(app)
 assert result['status']=='PASS_DIFFERENTIAL',result.get('failures')
 assert [x['phase'] for x in result['stages']]==phases
 assert result['candidate']['actualSkips']>0
 assert result['candidate']['mode']=='isolated-differential'
 assert all(x['differenceCount']==0 for x in result['stages'])
 assert all(not x['pixelDifferences'] or x['microRasterVariance'] for x in result['stages'])
 assert any(entry.get('actualSkipConsumers') for stage in result['stages']
   for entry in stage['candidateRecentRevisions'])
 assert result['boundaries']['productionSchedulerChanged'] is False
 assert result['boundaries']['selectiveSchedulingCertified'] is False


def test_browser_rejects_either_single_gate():
 pytest.importorskip('playwright')
 from playwright.sync_api import sync_playwright
 from tools.mcel_phase2b_browser import launch_semantic_browser
 from tools.mcel_task_manager_functional_parity_frob import FIXTURE_JS
 html=d._html('task-manager',True)
 with sync_playwright() as pw:
  browser,_=launch_semantic_browser(pw)
  try:
   for altered in (html.replace(d.GATE,''),html.replace('signalCandidateIsolatedTest:true,','')):
    page=browser.new_page();page.evaluate(FIXTURE_JS);page.set_content(altered)
    page.wait_for_function('()=>!!window.__mcelWorkbench?.app')
    report=page.evaluate('()=>window.__mcelWorkbench.app.signalCandidateReport()')
    assert report['mode']=='disabled'
    assert report['productionSchedulerChanged'] is False
    page.close()
  finally:browser.close()
