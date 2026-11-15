"""G2-0041 causal factors, oracle invariants, parent lineage, TRAIN-only scope."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import numpy as np
import pytest

from tools import nanojev_gamer_v1_g2_causal_oracle_diagnosis as d


def example(*,ranking_error=True,count_error=False):
    edges=[('a','b','rel'),('a','c','rel'),('d','e','rel'),('d','f','rel')]
    truth={edges[0],edges[2]}
    logits=np.array([3.,4. if ranking_error else 1.,2.,1.],dtype=np.float64)
    counts=np.full((2,d.x33.COUNT_CLASSES),-50.,dtype=np.float64)
    counts[:,1]=0.
    if count_error:counts[1,0]=10.
    item={'edges':edges,'raw':{},'groups':np.array([0,0,1,1])}
    policy={'enabled':True,'source':d.g40.SOURCE,'group_weight':.7,
            'global_weight':0.,'fallback':None}
    selected={edges[1] if ranking_error else edges[0],edges[2]}
    return {'row':{'prompt':'a test graph','family':'encounter','split':'train'},
      'admitted':True,'original':{'anchors':['a','d']},
      'recovered':{'anchors':['a','b','c','d','e']},
      'shortlisted':item,'oracle_policy':policy,
      'features':{'index':np.array([0,0,1,1]),'groups':np.zeros((2,d.x33.GROUP_DIM)),
                  'edges':np.zeros((4,d.x33.EDGE_DIM))},
      'structured_pred':(logits,counts,2.),
      'routed_decode':({'candidate__structured_s460333':selected},{}),
      'gold':truth}


def test_oracle_ranking_suffices_when_total_count_correct():
    record=example()
    assert d.oracle_decode(record,record['gold'],total_count=True)!=record['gold']
    assert d.oracle_decode(record,record['gold'],ranking=True,total_count=True)==record['gold']
    assert d.oracle_decode(record,record['gold'],ranking=True,group_counts=True)==record['gold']
    assert d.oracle_decode(record,record['gold'],ranking=True,total_count=True,group_counts=True)==record['gold']


def test_oracle_count_never_fixes_wrong_order_inside_fixed_group():
    record=example()
    assert d.oracle_decode(record,record['gold'],group_counts=True)!=record['gold']
    assert d.oracle_decode(record,record['gold'],total_count=True,group_counts=True)!=record['gold']


def test_oracle_all_factors_is_exact_independent_of_bad_logits_and_counts():
    record=example(count_error=True)
    assert d.oracle_decode(record,record['gold'],ranking=True,total_count=True,group_counts=True)==record['gold']


def test_full_case_measurement_uses_gold_only_on_train(monkeypatch):
    record=example()
    monkeypatch.setattr(d.base,'gold_binding_edges',lambda row:record['gold'])
    result=d.one_case(record,tag='train_fit',index=3)
    assert result['pairwise_rank_inversions']==1
    assert result['outcomes']['routed']['exact'] is False
    assert result['outcomes']['oracle_all_factors']['exact'] is True
    assert result['outcomes']['oracle_ranking_and_total']['exact'] is True
    assert result['shortlist_representable'] is True
    assert result['repairable'] is True
    assert d.summarize([result])['repairable']['cases']==1
    assert d.root_cause_counts([result])['counts']['reachable_cases']==1
    record['row']['split']='dev'
    with pytest.raises(ValueError,match='DEV_GOLD_FORBIDDEN'):
        d.one_case(record,tag='dev',index=1)


def test_unreachable_graph_never_receives_oracle_credit(monkeypatch):
    record=example();record['gold']=set(record['gold'])|{('x','y','rel')}
    monkeypatch.setattr(d.base,'gold_binding_edges',lambda row:record['gold'])
    result=d.one_case(record,tag='train_fit',index=1)
    assert result['shortlist_representable'] is False
    assert 'oracle_all_factors' not in result['outcomes']
    assert d.summarize([result])['expanded_unreachable']['cases']==1


def test_bad_candidate_alignment_fails_closed():
    record=example();record['structured_pred']=(np.array([1.,2.]),record['structured_pred'][1],2.)
    with pytest.raises(ValueError,match='MISALIGNED_CANDIDATES'):
        d.oracle_decode(record,record['gold'])


def test_diagnostic_has_no_training_or_dev_controls():
    assert d.ARMS[0]=='routed'
    assert d.ARMS[-1]=='direct_gold_control'
    assert len(d.ARMS)==10
    result=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-causal-oracles','--help'],capture_output=True,text=True,check=True)
    assert '--bakeoff40-run' in result.stdout
    assert '--device' in result.stdout


def test_parent40_contract_fails_closed(tmp_path):
    (tmp_path/d.g40.REPORT).write_text(json.dumps({'schema':'wrong'}))
    with pytest.raises(ValueError,match='PARENT_0040_CONTRACT:schema'):
        d.verify_parent40(tmp_path)


def test_immutable_result_and_metric_reconciliation(tmp_path):
    record=example()
    result={'schema':d.SCHEMA,'no_training':True,'production_promotion':False}
    out=tmp_path/'diagnostic'
    d.persist(out,result,[])
    assert result['ledger_sha256']==d.backbone.sha256(out/d.LEDGER)
    assert json.loads((out/d.REPORT).read_text())['no_training'] is True
    with pytest.raises(FileExistsError):d.persist(out,result,[])


def test_model_decoder_parity_without_oracles():
    record=example()
    expected=d.x33.decode(record['shortlisted'],record['features'],
                         record['structured_pred'],record['oracle_policy'])
    assert d.oracle_decode(record,set(),ranking=False,total_count=False,
                           group_counts=False)==expected


def test_diagnostic_retains_edge_level_failure_witnesses(monkeypatch):
    record=example()
    monkeypatch.setattr(d.base,'gold_binding_edges',lambda row:record['gold'])
    case=d.one_case(record,tag='train_fit',index=2)
    assert case['ranking_inversion_witnesses'][0]['gold_edge']==['a','b','rel']
    assert case['ranking_inversion_witnesses'][0]['negative_edge']==['a','c','rel']
    assert case['routed_missing_edges']==[['a','b','rel']]
    assert case['routed_false_edges']==[['a','c','rel']]
    assert case['unrepresented_gold_edges']==[]


def test_oracle_dp_matches_exact_frozen_viterbi_across_random_groups():
    rng=np.random.default_rng(40041)
    for trial in range(12):
        group_sizes=[int(v) for v in rng.integers(2,7,size=5)]
        ids=np.repeat(np.arange(len(group_sizes)),group_sizes)
        edges=[(f'entity_{int(g)}',f'neighbor_{i}','rel') for i,g in enumerate(ids)]
        logits=rng.normal(size=len(edges)).astype(np.float64)*3.
        counts=rng.normal(size=(len(group_sizes),d.x33.COUNT_CLASSES)).astype(np.float64)
        policy={'enabled':True,'source':d.g40.SOURCE,'group_weight':float(rng.uniform(0,2)),
                'global_weight':float(rng.uniform(0,2)),'fallback':None}
        pred=(logits,counts,float(rng.uniform(0,12)))
        record={'shortlisted':{'edges':edges},
                'features':{'index':ids,'groups':np.zeros((5,d.x33.GROUP_DIM))},
                'structured_pred':pred,'oracle_policy':policy}
        frozen=d.x33.decode(record['shortlisted'],record['features'],pred,policy)
        diagnostic=d.oracle_decode(record,set(),ranking=False,total_count=False,group_counts=False)
        assert diagnostic==frozen, f'Viterbi discrepancy trial={trial}'


def test_diagnostic_hook_precedes_any_retraining_or_dev_loop():
    import inspect
    source=inspect.getsource(d.g40.run)
    assert source.index('if diagnostic_callback is not None:') < source.index('fine_tune_decoder(training')
    assert source.index('if diagnostic_callback is not None:') < source.index('for split,rows in {**fresh,**dev40}')
    assert source.index('assert_all_frozen()',source.index('if diagnostic_callback is not None:')) < source.index('return diagnostic_callback(')
