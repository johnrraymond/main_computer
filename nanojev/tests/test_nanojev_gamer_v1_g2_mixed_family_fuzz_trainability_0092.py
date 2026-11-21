"""G2-0092 mixed-family and live fuzz monitoring regression tests."""
from copy import deepcopy
import json

import pytest
import torch

from tools import nanojev_gamer_v1_g2_mixed_family_fuzz_trainability_0092 as m
from tools.nanojev_gamer_v1_graph_head import GamerGraphHead, BIND_CLASSES


SPEC={'method':'joint','threshold':0.0,'k':3,'out_caps':{},'in_caps':{}}


def _row(family,number,split='train'):
    prompt=f'G2-0092 sample {family} {number} {split}'
    return {'prompt':prompt,'family':family,'split':split,
            'graph':{'nodes':[{'id':'one','kind':'actor','span':[0,4]},
                              {'id':'two','kind':'action','span':[6,10]},
                              {'id':'three','kind':'action','span':[12,16]}],
                     'edges':[{'src':'one','dst':'two','type':'owns'}]}}


def _item(family,number,split='train'):
    obj=[[0,4,'actor'],[6,10,'action'],[12,16,'action']]
    t=torch.zeros(3,3,dtype=torch.long)
    t[0,1]=BIND_CLASSES.index('owns')
    return {'row':_row(family,number,split),'hidden':torch.randn(5,16),
            'eligible':torch.ones(5,dtype=torch.bool),'objects':obj,
            'spans':[{'kind':kind,'token_first':i,'token_last':i}
                     for i,(_,_,kind) in enumerate(obj)],
            'targets':t,'reachable':1,'full_gold_count':1}


@pytest.fixture
def scenario():
    torch.manual_seed(992)
    head=GamerGraphHead(hidden_size=16,width=16,layers=1,
                        latent_slots=2,memory_slots=2,oracle_coupling=True)
    train=[_item(f,i) for f in m.FAMILIES for i in range(4)]
    held=[_item(f,i,'dev') for f in m.FAMILIES for i in range(2)]
    return head,train,held


def test_stage_balances_both_families_and_never_reuses_training_case(scenario):
    _,items,_=scenario
    p4=m.balanced_prefix(items,2)
    p8=m.balanced_prefix(items,4)
    assert len(p4)==4 and len(p8)==8
    assert {r['row']['family'] for r in p4}==set(m.FAMILIES)
    assert all(sum(r['row']['family']==f for r in p8)==4 for f in m.FAMILIES)
    with pytest.raises(ValueError,match='G2_0092_INSUFFICIENT_ENCOUNTER'):
        m.balanced_prefix(items[:4],2)
    for bad in ('100','0,1','1,0','5001,5','bad,4'):
        with pytest.raises(ValueError,match='G2_0092_STEP'):
            m.stage_steps(bad)


def test_per_family_regressions_not_masked_by_other_family():
    prev={'allocation':{'exact':0,'fp':9,'fn':0,'edge_f1':.4},
          'encounter':{'exact':1,'fp':1,'fn':0,'edge_f1':.8}}
    current={'allocation':{'exact':1,'fp':0,'fn':0,'edge_f1':1.0},
             'encounter':{'exact':0,'fp':4,'fn':0,'edge_f1':.5}}
    flags=m.compare_family(current,prev,prev)
    assert 'encounter:lost_exact' in flags
    assert 'encounter:more_edge_errors' in flags
    assert 'encounter:below_initial_f1_by_3pp' in flags
    assert not any('allocation' in x for x in flags)


def test_structural_real_decoder_heldout_counts_unreachable_gold(scenario):
    head,_,held=scenario
    baseline=m._compact_snapshot(head,held,SPEC,'cpu')
    assert baseline['metrics']['cases']==4
    for fam in m.FAMILIES:
        assert baseline['families'][fam]['cases']==2
    # A fourth gold object is absent from G1; the monitor must not count
    # a reachable-only match as an exactly reconstructed full gold graph.
    extra=deepcopy(held[0]);extra['row']=deepcopy(held[0]['row'])
    extra['row']['graph']['nodes'].append({'id':'missing','kind':'quantity','span':[18,20]})
    extra['row']['graph']['edges'].append({'src':'two','dst':'missing','type':'owns'})
    extra['full_gold_count']=2
    out=m._compact_snapshot(head,[extra],SPEC,'cpu')
    assert out['metrics']['g1_reachable_cases']==0
    assert out['families']['allocation']['fn']>=1


def test_fuzz_checks_gold_delta_not_surface_changes(scenario):
    head,_,held=scenario
    a=deepcopy(held[0]);b=deepcopy(held[0])
    b['row']['prompt']+=' renamed'
    pairs=[('pair-a','renaming','allocation',{'base':a,'fuzz':b})]
    result=m.fuzz_probe(head,pairs,SPEC,'cpu')
    assert result['summary']['pairs']==1
    assert result['pairs'][0]['gold_invariant']
    assert result['pairs'][0]['delta_correct']
    # Action removal changes the gold edge set; unchanged predictions are NOT correct.
    b['row']['graph']['edges']=[]
    pairs=[('pair-b','action_removal','allocation',{'base':a,'fuzz':b})]
    result=m.fuzz_probe(head,pairs,SPEC,'cpu')
    assert not result['pairs'][0]['gold_invariant']
    assert not result['pairs'][0]['delta_correct']


def test_fuzz_detects_gold_drift(scenario):
    head,_,held=scenario
    a=deepcopy(held[0]);b=deepcopy(held[0])
    b['row']['graph']['edges']=[]
    with pytest.raises(ValueError,match='G2_0092_FUZZ_GOLD_INVARIANT_DRIFT'):
        m.fuzz_probe(head,[('x','renaming','allocation',{'base':a,'fuzz':b})],SPEC,'cpu')


def test_cross_family_gradients_causal_probe_restores_weights(scenario):
    head,train,_=scenario
    m.trainability._trainable(head)
    old={k:v.detach().clone() for k,v in head.state_dict().items()}
    result=m._cross_family_gradients(head,train,SPEC,'cpu',include_transfer=True)
    assert result['pair_count']==1
    assert result['finite_step_transfer']['probe_count']==2
    assert all(torch.equal(v,old[k]) for k,v in head.state_dict().items())


def test_live_fuzz_training_stage_writes_diagnostic_and_restores_best(tmp_path,monkeypatch,scenario):
    head,train,held=scenario
    subset=m.balanced_prefix(train,2)
    m.trainability._trainable(head)
    opt=torch.optim.AdamW([p for p in head.parameters() if p.requires_grad],lr=5e-5)
    pairs=[('p1','renaming','allocation',
            {'base':deepcopy(held[0]),'fuzz':deepcopy(held[0])})]
    pairs[0][3]['fuzz']['row']['prompt']+=' fuzzed'
    monkeypatch.setattr(m,'_activation_attribution',lambda *a,**k:
                        {'traces':[],'scope':'synthetic_probe'})
    monkeypatch.setattr(m,'_save_experimental',lambda *a,**k:'test-head-sha256')
    result=m._train_stage(head,opt,subset,held,pairs,[],SPEC,'cpu',
                limit=2,arm='A',stage=4,dest=tmp_path,manifest={},paths={})
    assert result['stage']==4
    assert result['final_fuzz']['summary']['pairs']==1
    assert result['history_sha256']==m.sha(tmp_path/'A'/'stage-4'/'history.json')
    assert result['journal_sha256']==m.sha(tmp_path/'A'/'stage-4'/'monitor.jsonl')
    assert len((tmp_path/'A'/'stage-4'/'monitor.jsonl').read_text().splitlines())>=1
    assert result['failure_attribution']['explanation']
    assert result['diagnostic_files']
    for f in result['diagnostic_files']:
        path=tmp_path/'A'/'stage-4'/f['file']
        assert m.sha(path)==f['sha256']
        assert json.loads(path.read_text())['g3_unlocked'] is False
    lock=json.loads((tmp_path/'A'/'stage-4'/'lock.json').read_text())
    assert lock['checkpoint_sha256']=='test-head-sha256'
    assert lock['g3_unlocked'] is False


def test_governance_preserves_original_gates_and_never_unlocks_G3():
    assert m.STAGES==(4,8)
    assert m.trainability.prior.cur.GATE_REQUIREMENTS['G2']=={
        'metric':'binding_exact_rate','dev':0.80,'challenge':0.70,'neural_gain':0.08}
    assert m.trainability.prior.cur.STAGE_MODULES['G2']==(
        'binding.','pair_projection.','interface_couplers.G1_G2.delta.')
    flags=['counterfactual:G2_transform_mismatch_with_exact_G1']
    category=m._diagnosis_label(flags,{'minimum_pair_cosine':-.5},
         {'traces':[{'pair_stats':{'margin_auc':.4}}]})
    assert not category['localized_layer_proven']
    assert 'NEGATIVE_CROSS_FAMILY_GRADIENT_ALIGNMENT' in category['hypotheses']
