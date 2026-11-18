"""G2-0068 objective parity, gradients, matching and split-integrity acceptance."""
import math
import json
from types import SimpleNamespace

import pytest
import torch

from tools import nanojev_g2_set_loss_bakeoff_0068 as b
from tools import nanojev_gamer_v1_g2_counterfactual_bakeoff_0057 as p57
from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55


def _pair(index=0, family='allocation', mode='renaming'):
    torch.manual_seed(11+index)
    candidate={'src':torch.tensor([0,1,0,2,1,2]),
               'dst':torch.tensor([1,2,2,0,0,1]),
               'relation':torch.tensor([0,1,2,1,1,2]),
               'kinds':torch.tensor([0,0,0]),
               'margin':torch.zeros(3),
               'geometry':torch.zeros(6,5),
               'node':torch.randn(3,96),
               'positive':torch.tensor([0,1]),'truth_count':2,
               'training_node_ids':('a','b','c'),
               'family':family,'mode':mode,'edge_count':6}
    a={**candidate,'prompt_sha256':f'{index}:left'}
    d={**candidate,'prompt_sha256':f'{index}:right'}
    r={'id':f'pair-{index}','sha256':(a['prompt_sha256'],d['prompt_sha256']),
       'mode':mode,'family':family}
    return (a,d),r


def test_control_loss_exactly_matches_existing_paired_objective_and_gradients():
    samples,receipt=_pair()
    rows={sample['prompt_sha256']:sample for sample in samples}
    torch.manual_seed(5)
    base=p55.model_for('set_prediction')
    left=p55.model_for('set_prediction')
    right=p55.model_for('set_prediction')
    left.load_state_dict(base.state_dict());right.load_state_dict(base.state_dict())
    actual=b._paired_objective(left,[receipt],rows,'control')
    expected=p57.batch_objective(right,[receipt],rows,'D_combined')
    assert torch.equal(actual,expected)
    actual.backward();expected.backward()
    for (_,a),(_,v) in zip(left.named_parameters(),right.named_parameters()):
        assert torch.allclose(a.grad,v.grad,atol=0,rtol=0)


def test_null_supervision_alters_exactly_null_coefficient():
    samples,_=_pair()
    model=p55.model_for('set_prediction')
    logits=model(b._features(samples[0]))
    old,mapping=b._matched_loss_from_forward(logits,samples[0])
    modified,new_map=b._matched_loss_from_forward(logits,samples[0],null_weight=.55)
    assert new_map==mapping
    no_gold=torch.ones(p55.SLOTS,dtype=torch.bool)
    no_gold[list(mapping.values())]=False
    null_ce=torch.nn.functional.cross_entropy(logits[0][no_gold],
                        torch.zeros(int(no_gold.sum()),dtype=torch.long))
    assert torch.allclose(modified-old,.35*null_ce,atol=1e-6)


def test_full_candidate_hard_negative_loss_uses_gold_only_for_training():
    samples,_=_pair()
    model=p55.model_for('set_prediction')
    logits=model(b._features(samples[0]))
    x=b._hard_edge_objective(logits,samples[0])
    assert torch.isfinite(x) and x.item()>0
    x.backward()
    assert model.queries.grad is not None
    assert torch.isfinite(model.queries.grad).all()
    assert model.queries.grad.abs().sum()>0
    bad={**samples[0],'truth_count':3}
    with pytest.raises(ValueError,match='CENSORED'):
        b._hard_edge_objective(logits,bad)


def test_original_decoder_parity_and_gold_blind_features():
    samples,_=_pair()
    model=p55.model_for('set_prediction')
    for i in range(3):
        torch.manual_seed(i)
        with torch.no_grad():
            logits=model(b._features(samples[0]))
        assert b._decode(logits,b._features(samples[0]),0.) == p55.infer_edges(
                model,b._features(samples[0]),'set_prediction')
    assert 'positive' not in b._features(samples[0])
    assert 'truth_count' not in b._features(samples[0])
    assert 'training_node_ids' not in b._features(samples[0])


def test_matching_slot_diagnostic_counts_are_consistent():
    samples,receipt=_pair()
    class DS:
        def pair(self,index,with_labels=True):
            assert with_labels
            return samples,receipt
    model=p55.model_for('set_prediction')
    d=b._slot_diagnostics(model,DS(),[0])
    assert d['graphs']==2 and d['pairs']==1 and d['gold_edges']==4
    assert d['slots_null_supervised']==2*(p55.SLOTS-2)
    assert 0<=d['numeric_slot_agreement_fraction']<=1
    assert 0<=d['hungarian_reassigned_fraction']<=1
    assert 0<=d['assigned_gold_edge_decoded_fraction']<=1
    assert 'Permutation-invariant slots' in d['warning']


def test_pair_schedule_equal_budget_reproducible():
    a=b._batch_schedule([3,9,11,14],3,2)
    assert a==b._batch_schedule([3,9,11,14],3,2)
    assert len(a)==6
    assert all(len(x)==2 for x in a)
    assert all(sorted(v for row in a[i:i+2] for v in row)==[3,9,11,14]
               for i in (0,2,4))
    with pytest.raises(ValueError,match='BAD_MATCHED_BUDGET'):
        b._batch_schedule([3,9,11],2,2)


def test_calibration_does_not_consume_evaluation():
    cases=[{'gold':frozenset({0}),'truth_count':1,'pair_index':0,
            'family':'allocation','mode':'renaming',
            'predicted_by_margin':{str(v):({0} if v<1. else set()) for v in b.MARGINS},
            'prediction':None}]
    chosen,_=b._calibrate(cases)
    assert chosen==0.5 # tie chosen closest to the 1.5 default among best
    assert b._metrics(__import__('collections').Counter())['graphs']==0


def test_training_updates_do_not_mutate_source_weights():
    samples,r=_pair()
    class DS:
        def pair(self,index,with_labels=True):return samples,r
    base=p55.model_for('set_prediction')
    original={k:v.detach().clone() for k,v in base.state_dict().items()}
    m=b._new_model(original,'cpu')
    receipt=b._training_arm(m,DS(),[(0,)],'combined_loss',lr=1e-4,device='cpu')
    assert receipt['optimizer_steps']==1
    assert any(not torch.equal(original[k],m.state_dict()[k]) for k in original)
    assert all(torch.equal(original[k],base.state_dict()[k]) for k in original)


def test_training_rejects_incomplete_and_paired_semantic_mismatch():
    samples,r=_pair()
    class DS:
        def __init__(self,samples):self.samples=samples
        def pair(self,index,with_labels=True):return self.samples,r
    bad={**samples[0],'truth_count':3}
    with pytest.raises(ValueError,match='UNREACHABLE'):
        b._get_training_pair(DS((bad,samples[1])),0)
    other={**samples[1],'positive':torch.tensor([0,2])}
    with pytest.raises(ValueError,match='PAIRED_CANONICAL'):
        b._get_training_pair(DS((samples[0],other)),0)


def test_pair_block_bootstrap_uses_same_pairs():
    def record(tp,g,p):
        return {'true_positive':tp,'gold_edges':g,'predicted_edges':p}
    first={'by_pair':{i:record(2,3,4) for i in range(4)}}
    second={'by_pair':{i:record(3,3,3) for i in range(4)}}
    assert b._paired_ci(first,second)==[42.857,42.857]
    with pytest.raises(ValueError,match='EVALUATION_PAIR_MISMATCH'):
        b._paired_ci(first,{'by_pair':{0:record(2,3,3)}})


def test_integrated_equal_step_arm_calibration_evaluation(monkeypatch,tmp_path):
    """Exercise all four arm updates, calibrated controls and hidden evaluation."""
    from tools import nanojev_g2_joint_graph_bakeoff_0067 as p67
    class Corpus:
        def __init__(self,split,n):
            self.split=split
            self.pairs=[];self.indices=[]
            for i in range(n):
                f=('allocation','encounter')[i%2]
                mode=('renaming','role_swap')[(i//2)%2]
                m={'family':f,'mode':mode}
                self.pairs.append((i,0,m,dict(m)))
                self.indices.append(i)
        def pair(self,index,with_labels=True):
            assert with_labels
            (a,c),r=_pair(index,self.pairs[index][2]['family'],self.pairs[index][2]['mode'])
            if self.split!='train':
                a={k:v for k,v in a.items() if k!='training_node_ids'}
                c={k:v for k,v in c.items() if k!='training_node_ids'}
            return (a,c),r
    train=Corpus('train',8);val=Corpus('validation',12)
    def mocked_load(*args):
        model=p55.model_for('set_prediction').eval().requires_grad_(False)
        return (model,train,val,p55,{'budget':{'requested_training_graphs':100},
             'capture_config_sha256':'capture','plan_sha256':'plan'},
             {'weights_sha256':'frozen'})
    monkeypatch.setattr(p67,'_load_model_and_data',mocked_load)
    report=b.run(training_dir=tmp_path,plan_dir=tmp_path,capture_dir=tmp_path,
                 training_pairs=4,calibration_pairs=2,evaluation_pairs=2,
                 epochs=1,batch_pairs=2,lr=1e-4,cpu_threads=2,audit_pairs=1)
    assert report['split']['optimizer_steps_per_arm']==2
    assert all(m['optimizer_steps']==2 for m in report['training'].values())
    assert report['evaluation']['control']['overall']['graphs']==4
    assert report['evaluation']['frozen_reference']['overall']['graphs']==4
    assert set(report['training'])==set(b.ARMS)
    assert report['gold_at_inference'] is False
    assert report['sealed_test_used'] is False and report['production_promotion'] is False
    assert 'G2-0068' in b.format_report(report)
    assert not list(tmp_path.glob('*.safetensors'))


def test_main_refuses_overwrite(tmp_path):
    file=tmp_path/'existing.json';file.write_text('{}')
    with pytest.raises(SystemExit):
        b.main(['--output',str(file)])
    assert file.read_text()=='{}'
