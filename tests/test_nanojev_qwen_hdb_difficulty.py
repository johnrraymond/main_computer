"""CPU-only tests of HDB/T weakness diagnosis and existing-run replay."""
import json
from types import SimpleNamespace

from tools import nanojev_qwen_hdb_difficulty as d
from tools import nanojev_qwen_hdb_frontier as h
from tools import nanojev_qwen_competency_diagnostic as c
from tools import nanojev_qwen_lm_frontier_stress_repair as f
from tools import nanojev_qwen_lm_radius_ladder as ladder


def _row(tier,k,variant=(5,2,3)):
    return h._row(tier,k,variant,label='test')


def test_hdb_zero_side_margin_matrix_and_wrong_output_classes():
    rows=[_row(7,k) for k in (-2,-1,0,1,2)]
    # Negative side wrongly Hawk; tie packet; near-positive empty; far-positive wrong Dove.
    predictions=('HAWK','DOVE','PROBLEM: 0','', 'DOVE')
    before={c._digest(r['source']):{'correct':False} for r in rows}
    after={c._digest(r['source']):{'correct':i==1,
        'emitted':predictions[i]} for i,r in enumerate(rows)}
    data,errors=d.analyze_hdb(rows,before,after,interval={'HDB07':{'net':1}},
        exposures={'HDB07':{'examples':300}})
    tier=data['by_tier']['HDB07']
    assert (tier['tested'],tier['correct'],tier['wrong'],tier['net'])==(5,1,4,1)
    assert data['by_side']['negative']['wrong']==1
    assert data['by_side']['zero']['wrong']==1
    assert data['by_margin']['nearest_1_2']['wrong']==3
    assert tier['failures_by_type']['wrong_strategic_choice']==2
    assert tier['failures_by_type']['arithmetic_packet_instead_of_choice']==1
    assert tier['failures_by_type']['empty_output']==1
    assert data['ranked_tiers'][0]['tier']=='HDB07'
    assert data['ranked_tiers'][0]['priority_reason']=='improving_but_incomplete'
    assert all(e['evaluation_only'] for e in errors)
    assert all(e['delta'] == (5*r['offset']) for e,r in zip(
        [next(e for e in errors if e['offset']==rr['offset']) for rr in rows if rr['offset'] != -1],
        [rr for rr in rows if rr['offset'] != -1]))


def test_t_retention_distinguishes_format_vs_wrong_arithmetic():
    rows=[ladder._case(0,[20,-40],'T00-a','numeric'),
          ladder._case(1,[12,-14,4],'T01-b','numeric'),
          ladder._case(2,[1,2,3,4],'T02-c','numeric')]
    for i,r in enumerate(rows):r['radius']=i
    before={f._key(r):{'correct':True} for r in rows}
    after={f._key(r):{'correct':False,'emitted':text} for r,text in zip(rows,[
        'PROBLEM: 20-40','PROBLEM: (88)+4','PROBLEM: 3+3+4'])}
    data,errors=d.analyze_t(rows,before,after)
    assert data['regressions']==3 and data['wrong_cases']==3
    assert data['by_rung']['T00']['wrong_reduction']==1
    assert data['by_rung']['T01']['wrong_reduction']==1
    assert data['by_rung']['T02']['wrong_reduction']==1
    assert all(e['regression'] and e['evaluation_only'] for e in errors)


def test_difficulty_write_and_read_without_inference(tmp_path,monkeypatch):
    a=SimpleNamespace(output=str(tmp_path/'numeric'),frontier_output=None,hdb_output=None)
    out=h._output(a);out.mkdir()
    rows=[_row(0,k) for k in (-1,0,1)]
    trows=[ladder._case(0,[1,-1],'T0','numeric')]
    for r in trows:r['radius']=0
    initial_h={c._digest(r['source']):{'correct':False,'emitted':''} for r in rows}
    latest_h={c._digest(r['source']):{'correct':r['offset']>0,'emitted':
        ('HAWK' if r['offset']>0 else 'DOVE')} for r in rows}
    initial_t={f._key(r):{'correct':True,'emitted':r['target']} for r in trows}
    latest_t={f._key(r):{'correct':False,'emitted':'PROBLEM: 999'} for r in trows}
    state={'baseline_hdb':initial_h,'last_hdb':latest_h,
           'baseline_t':initial_t,'last_t':latest_t,
           'exposures':{'HDB00':{'examples':3}},'last_step':100}
    (out/'progress.json').write_text(json.dumps(state))
    (out/'hdb_report.json').write_text(json.dumps({'step':100,
        'hdb_since_last':{'by_tier':{'HDB00':{'net':1}}}}))
    (out/'hdb_eval_deck.json').write_text(json.dumps(rows))
    (out/'t_retention_eval_deck.json').write_text(json.dumps(trows))
    report=d.read_latest(a)
    assert report['step']==100
    assert report['no_new_model_inference']
    assert report['hdb']['by_tier']['HDB00']['accuracy']==1/3
    assert report['t_retention']['regressions']==1
    assert (out/'hdb_difficulty_report.json').exists()
    failures=[json.loads(s) for s in (out/'hdb_difficulty_failures.jsonl').read_text().splitlines()]
    assert len(failures)==3
    assert all(x['evaluation_only'] for x in failures)
    summary=d.quick_summary(report)
    assert summary['weakest_hdb_tiers'][0]['tier']=='HDB00'
    assert 'direct_examples' in summary['weakest_hdb_tiers'][0]
    assert 'nearest_zero_accuracy' in summary
    assert summary['t_retention_regressions']==1
    state['last_step']=200
    (out/'progress.json').write_text(json.dumps(state))
    try:
        d.read_latest(a)
    except RuntimeError as error:
        assert 'WRITE_IN_PROGRESS' in str(error)
    else:
        raise AssertionError('expected inconsistent checkpoint guard')
