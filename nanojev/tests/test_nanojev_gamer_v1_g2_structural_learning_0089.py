"""G2-0089 gate, structured-set gradient, artifact-integrity and fuzz tests."""
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g2_structural_learning_0089 as m


@pytest.fixture
def triple():
    objs=[[0,4,'actor'],[6,11,'action'],[12,17,'action']]
    targets=torch.zeros((3,3),dtype=torch.long)
    targets[0,1]=m.BIND_CLASSES.index('owns')
    spec={'method':'joint','threshold':0,'k':2,'out_caps':{},'in_caps':{}}
    scores=torch.full((3,3,len(m.BIND_CLASSES)), -6.,dtype=torch.float32)
    scores[...,0]=0
    scores[0,1,1]=0.1
    scores[0,2,1]=2.0
    scores.requires_grad_(True)
    return objs,targets,spec,scores


def test_gold_surface_is_oracle_only(triple):
    objs,targets,spec,logits=triple
    surface=m._surface_from_logits(objs,logits)
    gold=m._gold_keys(surface,targets)
    assert len(gold)==1
    assert m.previous.decode(m._oracle_surface(surface,targets),spec)==gold
    assert m.previous.decode(surface,spec)!=gold


def test_structured_hinge_detects_wrong_destination_and_has_gradient(triple):
    objs,targets,spec,logits=triple
    loss,stats=m.graph_set_hinge(logits,targets,objs,spec)
    assert stats['exact'] is False
    assert stats['rival_hamming']>=1
    assert loss.item()>0
    loss.backward()
    assert logits.grad is not None
    assert logits.grad[0,1,1]<0   # decrease loss by increasing true edge score
    assert logits.grad[0,2,1]>0   # decrease loss by decreasing false edge score


def test_exact_decoder_has_zero_structured_loss_without_cost_augmented_violation(triple):
    objs,targets,spec,logits=triple
    with torch.no_grad():
        logits[0,1,1]=12
        logits[0,2,1]=-12
    loss,stats=m.graph_set_hinge(logits,targets,objs,spec)
    assert stats['exact']
    assert loss.item()==pytest.approx(0.0)


def test_dense_loss_and_structural_loss_both_differentiable(triple):
    objs,targets,spec,logits=triple
    spans=[{'kind':o[2],'token_first':i,'token_last':i} for i,o in enumerate(objs)]
    loss,stats=m.objective(logits,targets,spans,objs,spec)
    assert stats['gold_reachable_edges']==1
    assert stats['negative_directed_pairs']==5
    loss.backward()
    assert torch.isfinite(logits.grad).all()


@pytest.mark.parametrize('bad',[
    torch.zeros((3,3,2)),torch.zeros((3,4,len(m.BIND_CLASSES))),
    torch.zeros((2,2,len(m.BIND_CLASSES)))])
def test_reject_malformed_binding_dimensions(triple,bad):
    objs,targets,spec,_=triple
    with pytest.raises(ValueError,match='GRAPH_LOGIT_SHAPE'):
        m.graph_set_hinge(bad,targets,objs,spec)


def test_reject_mismatched_target_shape(triple):
    objs,targets,spec,logits=triple
    with pytest.raises(ValueError,match='GOLD_TARGET_SHAPE'):
        m.graph_set_hinge(logits,targets[:2],objs,spec)


def test_edge_serialization_roundtrip(triple):
    objs,targets,spec,logits=triple
    edges=m._gold_keys(m._surface_from_logits(objs,logits),targets)
    assert m.native._keyed_edges(m._serialize_edges(edges))==edges


def test_weight_authority_is_G2_only():
    from tools.nanojev_gamer_v1_graph_head import GamerGraphHead
    head=GamerGraphHead(hidden_size=16,width=16,layers=1,
                        latent_slots=2,memory_slots=2,oracle_coupling=True)
    params=m._mutable(head)
    assert params
    assert all(p.requires_grad==any(name.startswith(x) for x in m.cur.STAGE_MODULES['G2'])
               for name,p in head.named_parameters())
    with pytest.raises(ValueError,match='UNAUTHORIZED_LOCAL_REPAIR'):
        m._mutable(head,prefix='encoder.')
    params=m._mutable(head,prefix='binding.')
    assert len(params)==2


def test_counterfactual_consistency_rejects_wrong_ids():
    from tools.nanojev_gamer_v1_graph_head import GamerGraphHead
    head=GamerGraphHead(hidden_size=16,width=16,layers=1,
                        latent_slots=2,memory_slots=2,oracle_coupling=True)
    head.eval()
    def item(indices):
        return {'spans':[{'kind':'actor','token_first':0,'token_last':0},
                         {'kind':'action','token_first':1,'token_last':1}],
                'hidden':torch.randn(3,16),'eligible':torch.tensor([True,True,True]),
                'ids':indices}
    left=item({'A':0,'B':1})
    right=item({'X':0,'Y':1})
    assert m._consistency(head,left,right,'renaming','cpu') is None
    assert m._consistency(head,left,left,'renaming','cpu').item()==0
    assert m._consistency(head,left,left,'action_removal','cpu') is None


def test_causal_arm_C_fails_closed_when_report_absent(tmp_path):
    answer=m.causal_target({'causal_0086':tmp_path})
    assert answer['eligible'] is False
    assert answer['reason']=='NO_LOCALIZED_REPAIR_EVIDENCE'


def test_causal_arm_C_rejects_nonlocalized_shared_effect(tmp_path,monkeypatch):
    d=tmp_path/'report.json'
    d.write_text(json.dumps({'checkpoint_sha256':'pinned','checkpoint_mutation':False,
        'independent_certification':False,'summary':{'interventions':{'by_intervention':{
          'sham_false:self_pair_input_control':{'n':48,'mean_delta_typed_margin':0.0},
          'false_to_true:encoder':{'n':48,'mean_graph_decision_changes':0.5},
          'false_to_true:g1_g2_coupler':{'n':48,'mean_graph_decision_changes':0.5}}}}}))
    path=tmp_path/'training'/m.cur.HEAD_FILE
    path.parent.mkdir(parents=True)
    path.write_bytes(b'current weights')
    out=m.causal_target({'causal_0086':tmp_path,'parent_0087':tmp_path})
    assert out['eligible'] is False
    assert out['reason']=='CAUSAL_EVIDENCE_ON_OLD_CHECKPOINT'


def test_seal_fails_on_gold_tamper(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'_check_provenance',lambda paths,dest: {})
    (tmp_path/'seal').mkdir()
    (tmp_path/'seal'/'prompts.jsonl').write_text('prompt\n')
    (tmp_path/'seal'/'answers.SECRET.jsonl').write_text('gold\n')
    (tmp_path/'selection.json').write_text('selected\n')
    (tmp_path/'provenance.json').write_text('provenance\n')
    (tmp_path/'historical.json').write_text('{}\n')
    c={'schema':m.SCHEMA,'status':'SELECTION_FROZEN_GOLD_COMMITTED',
       'g3_unlocked':False,'oracle_used_in_blind':False,'runner_sha256':m.sha(m.__file__),
       'selection_sha256':m.sha(tmp_path/'selection.json'),
       'provenance_sha256':m.sha(tmp_path/'provenance.json'),
       'historical_sha256':m.sha(tmp_path/'historical.json'),
       'prompts_sha256':m.sha(tmp_path/'seal'/'prompts.jsonl'),
       'gold_sha256':m.sha(tmp_path/'seal'/'answers.SECRET.jsonl')}
    (tmp_path/'seal'/'commit.json').write_text(json.dumps(c))
    assert m._check_seal({},tmp_path,verify_gold=False)==c
    (tmp_path/'seal'/'answers.SECRET.jsonl').write_text('changed\n')
    assert m._check_seal({},tmp_path,verify_gold=False)==c
    with pytest.raises(ValueError,match='GOLD_CHANGED'):
        m._check_seal({},tmp_path,verify_gold=True)


def test_original_contract_is_read_not_rewritten():
    assert m.cur.GATE_REQUIREMENTS['G2']=={'metric':'binding_exact_rate',
             'dev':0.80,'challenge':0.70,'neural_gain':0.08}
    assert m.DEV==96 and m.CHALLENGE==64


def test_actual_G2_native_head_structured_gradients_and_frozen_G1(triple):
    from tools.nanojev_gamer_v1_graph_head import GamerGraphHead
    objs,targets,spec,_=triple
    head=GamerGraphHead(hidden_size=16,width=16,layers=1,
                        latent_slots=2,memory_slots=2,oracle_coupling=True)
    params=m._mutable(head)
    spans=[{'kind':kind,'token_first':i,'token_last':i}
           for i,(_,_,kind) in enumerate(objs)]
    hidden=torch.randn(5,16)
    before={k:v.detach().clone() for k,v in head.named_parameters()
            if k.startswith(('project.','encoder.','span_classifier.'))}
    output=head(hidden,eligible=torch.ones(5,dtype=torch.bool),
                inference_spans=spans,active_stage='G2')
    logits=output['binding_logits'][:3,:3]
    loss,_=m.objective(logits,targets,spans,objs,spec)
    assert torch.isfinite(loss)
    loss.backward()
    assert any(p.grad is not None and p.grad.abs().sum()>0 for p in params)
    optimizer=torch.optim.SGD(params,lr=.01)
    optimizer.step()
    for k,v in head.named_parameters():
        if k in before:assert torch.equal(v.detach(),before[k])


def _build_synthetic_blind(tmp_path,monkeypatch,*,tamper=False):
    monkeypatch.setattr(m,'DEV',1)
    monkeypatch.setattr(m,'CHALLENGE',1)
    monkeypatch.setattr(m,'FUZZ_PAIRS',0)
    monkeypatch.setattr(m,'_check_provenance',lambda paths,dest:{})
    dest=tmp_path/'out'
    (dest/'seal').mkdir(parents=True)
    (dest/'blind').mkdir(parents=True)
    spec={'method':'joint','threshold':0.,'out_caps':{},'in_caps':{}}
    m.write_json(dest/'selection.json',{'winner':'B','frozen_decoder':spec})
    m.write_json(dest/'provenance.json',{'schema':m.SCHEMA})
    m.write_json(dest/'historical.json',{'failures':[]})
    objs=[[0,5,'actor'],[6,12,'action']]
    surface={'g1_objects':objs,'binding_edges':[],
       'pair_margins':[[0,1,[3.,-9.,-9.,-9.]],[1,0,[-9.,-9.,-9.,-9.]]]}
    edges=m._serialize_edges(m.previous.decode(surface,spec))
    graph={'nodes':[{'id':'a','kind':'actor','span':[0,5]},
                    {'id':'b','kind':'action','span':[6,12]}],
           'edges':[{'src':'a','dst':'b','type':'owns'}]}
    prompts=[];answers=[];predictions=[]
    for split in ('dev','challenge'):
        prompt='Alice gave' + split
        digest=m.hashlib.sha256(prompt.encode()).hexdigest()
        ident='case-'+split
        basic={'case_id':ident,'split':split,'family':'encounter','prompt_sha256':digest}
        prompts.append({**basic,'prompt':prompt})
        answers.append({**basic,'graph':graph,
                        'gold_objects':[{'start':0,'end':5,'kind':'actor'},
                                        {'start':6,'end':12,'kind':'action'}]})
        predictions.append({**basic,'modes':{
            'selected':{'g1_objects':objs,'binding_edges':edges},
            'parent':{'g1_objects':objs,'binding_edges':[]},
            'zero_hidden':{'g1_objects':objs,'binding_edges':[]},
            'no_binding':{'g1_objects':objs,'binding_edges':[]}},
            'raw_surface':surface})
    m.write_jsonl(dest/'seal'/'prompts.jsonl',prompts)
    m.write_jsonl(dest/'seal'/'answers.SECRET.jsonl',answers)
    m.write_jsonl(dest/'blind'/'predictions.jsonl',predictions)
    commit={'schema':m.SCHEMA,'status':'SELECTION_FROZEN_GOLD_COMMITTED',
        'g3_unlocked':False,'oracle_used_in_blind':False,
        'runner_sha256':m.sha(m.__file__),
        'selection_sha256':m.sha(dest/'selection.json'),
        'provenance_sha256':m.sha(dest/'provenance.json'),
        'historical_sha256':m.sha(dest/'historical.json'),
        'prompts_sha256':m.sha(dest/'seal'/'prompts.jsonl'),
        'gold_sha256':m.sha(dest/'seal'/'answers.SECRET.jsonl')}
    m.write_json(dest/'seal'/'commit.json',commit)
    lock={'schema':m.SCHEMA,'status':'LOCKED_UNSCORED','cases':2,
          'predictions_sha256':m.sha(dest/'blind'/'predictions.jsonl'),
          'prompts_sha256':commit['prompts_sha256'],
          'selection_sha256':commit['selection_sha256'],
          'checkpoint_sha256':'frozen','gold_opened_during_inference':False}
    m.write_json(dest/'blind'/'lock.json',lock)
    commit['checkpoint_sha256']='frozen'
    m.write_json(dest/'seal'/'commit.json',commit)
    if tamper:(dest/'blind'/'predictions.jsonl').write_text('tampered\n')
    return dest


def test_synthetic_seal_score_perfect_cannot_certify(tmp_path,monkeypatch):
    output=_build_synthetic_blind(tmp_path,monkeypatch)
    scored=m.score(root=tmp_path,output=output)
    assert scored['metrics']['dev']['arms']['selected']['exact']==1
    assert scored['metrics']['challenge']['arms']['selected']['exact']==1
    assert scored['status']=='DEVELOPMENT_GATES_PASSED_NOT_CERTIFIED'
    assert scored['g2_certified'] is False
    assert scored['g3_training_unlocked'] is False
    with pytest.raises(ValueError,match='SCORE_ALREADY_EXISTS'):
        m.score(root=tmp_path,output=output)


def test_synthetic_blind_tamper_rejected(tmp_path,monkeypatch):
    output=_build_synthetic_blind(tmp_path,monkeypatch,tamper=True)
    with pytest.raises(ValueError,match='BLIND_LOCK_DRIFT'):
        m.score(root=tmp_path,output=output)
