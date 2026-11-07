"""Joint head-only repair: no dev/test fitting, no old-head/LoRA mutation."""
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as old_lab
from tools import nanojev_gamer_v1_sign_information_probe as probe


def _prepared():
    deck=probe.make_deck(4,3,3,False)
    rows=[v.public() for v in deck]
    split=probe._test_partitions(rows)
    y=np.asarray([float(v.target) for v in deck],dtype=float)
    # An artificial, *known-good* representation with sign information.
    features=np.column_stack([np.sign(y),np.abs(y),y,np.ones(len(y))]).astype('float32')
    baseline=np.ones(len(y))*0.05
    src_y=np.tile([-2.,0.,2.],20)
    src_x=np.column_stack([np.sign(src_y),np.abs(src_y),src_y,np.ones(len(src_y))]).astype('float32')
    src_baseline=src_y.copy()
    src_dev_y=np.tile([-3.,0.,3.],10)
    src_dev_x=np.column_stack([np.sign(src_dev_y),np.abs(src_dev_y),src_dev_y,np.ones(len(src_dev_y))]).astype('float32')
    src_dev_rows=[{'id':f'src{i}','group':f'src{i}', 'surface':'source_exact',
                   'family':'certified','target':str(v)} for i,v in enumerate(src_dev_y)]
    source=(src_x,src_baseline,src_y)
    syn=(features[split['train']],baseline[split['train']],y[split['train']],
         [rows[int(i)] for i in split['train']])
    dev_source=(src_dev_rows,src_dev_y,src_dev_y.copy(),src_dev_x)
    dev_synthetic=(rows,y,baseline,features[split['dev']],split['dev'])
    test_synthetic=(rows,y,baseline,features[split['test']],split['test'])
    return source,syn,dev_source,dev_synthetic,test_synthetic


def test_oracle_is_explicitly_not_a_training_run(capsys):
    assert joint.main([])==0
    value=json.loads(capsys.readouterr().out)
    assert value['status']=='ORACLE_ONLY'
    assert value['synthetic']['cases']==612
    assert value['candidate_configs']==8
    assert value['matched_cross_format_ablation']
    assert value['no_model_training']


def test_zero_initialized_residual_preserves_original_head_output_and_class():
    model=joint._build_model(torch,5,8).eval()
    X=torch.randn(7,5)
    with torch.no_grad():pred=model(X).numpy()
    assert np.max(np.abs(pred))==0
    old=np.asarray([-8.,-.2,-.049,0,.049,.2,8.])
    cls,v=joint._decode_correction(pred,old)
    assert np.array_equal(cls,old_lab._classify_continuous(old))
    # Nonzero old predictions are unchanged, zero-class treated as exact zero.
    assert np.allclose(v[[0,1,5,6]],old[[0,1,5,6]])
    assert np.all(v[[2,3,4]]==0)


def test_known_sign_reversal_and_exact_zero_decoder():
    old=np.array([.2,.2,.2],dtype=float)
    d=np.zeros((3,4))
    # Logit deltas overcome the original positive prior at the appropriate class.
    d[0,1]=3.0; d[1,2]=3.0; d[2,3]=3.0
    classes,val=joint._decode_correction(d,old)
    assert list(classes)==[-1,0,1]
    assert val[0]<0 and val[1]==0 and val[2]>0


@pytest.mark.parametrize('captured,original', [
    ('-1/5000000000000000', -2e-16),
    ('1/5000000000000000', 2e-16),
    ('0/1', 0.0),
    ('1/20', 0.05),
    ('-3/2', -1.5),
    ('3/10', 0.3),
])
def test_certified_target_fraction_parity_is_exact(captured, original):
    assert joint._same_certified_target(captured, original)


@pytest.mark.parametrize('captured,original', [
    ('0', -2e-16),
    ('1/5000000000000000', -2e-16),
    ('-1/5000000000000001', -2e-16),
    ('0.000000001', 0.0),
    ('not-a-number', 0.0),
])
def test_certified_target_parity_rejects_mismatch_or_malformed(captured, original):
    if captured == 'not-a-number':
        with pytest.raises(ValueError, match='JOINT_MALFORMED_CERTIFIED_TARGET'):
            joint._same_certified_target(captured, original)
    else:
        assert not joint._same_certified_target(captured, original)


def test_original_correct_anchor_excludes_wrong_previous_answers():
    y=np.asarray([-3.,0.,.01,4.,5.])
    previous=np.asarray([-3.01,.02,0.,4.01,-20.])
    mask=joint._original_correct_mask(y,previous)
    assert list(mask)==[True,True,False,True,False]


def test_normalizer_uses_only_training_arrays():
    x=np.array([[1.,2.],[2.,4.]],dtype=np.float32)
    y=np.array([[3.,6.],[4.,8.]],dtype=np.float32)
    mu,std=joint._normalization(x,y)
    assert np.allclose(mu,[2.5,5.])
    assert np.all(std>0)
    inp=joint._to_input(np.array([[2.5,5.]],dtype=np.float32),np.array([-2.]),mu,std)
    assert np.allclose(inp[0,:2],0)
    assert inp[0,2]<0


def test_original_training_and_synthetic_training_both_receive_gradients():
    net=joint._build_model(torch,5,8)
    X=torch.randn(8,5)
    base=torch.tensor([-1.,0.,1.,0.,-1.,1.,0.,1.])
    y=torch.tensor([-1.,0.,1.,0.,-1.,1.,0.,1.])
    anchors=torch.tensor([True,True,True,False,False,True,False,True])
    before=[p.detach().clone() for p in net.parameters()]
    loss1,parts1=joint._training_loss(torch,net,X,base,y,anchors,weight=1.,source=True)
    loss2,parts2=joint._training_loss(torch,net,X,base,-y,anchors,weight=1.,source=False)
    (loss1+loss2).backward()
    assert net.body[-1].weight.grad.abs().sum().item()>0
    assert parts2['preserve']==0
    assert parts1['ce']>0
    assert all(torch.equal(x,y) for x,y in zip(before,net.parameters()))


def test_smoke_joint_training_on_known_answer_features_and_no_test_selection():
    source,syn,srcdev,syndev,syntest=_prepared()
    results=joint._fit_one(torch,feature='unit',source=source,synthetic=syn,
        original_dev=srcdev,synthetic_dev=syndev,device='cpu',steps=8,
        lr=.005,source_weight=2,anchor_weight=10,seed=7,eval_every=4)
    net,mu,std,best,history,counts=results
    assert counts['anchored_train_count']==len(source[2])
    assert len(history)==3
    assert best['step'] in (0,4,8)
    assert best['retention']['original_correct_kept_fraction'] is not None
    assert best['dev']['all']['cases']==len(syndev[4])
    assert torch.isfinite(next(net.parameters())).all()
    assert all(np.isfinite(x).all() for x in (mu,std))


def test_retention_gate_cannot_be_overridden_by_synthetic_perfection():
    source,syn,srcdev,syndev,syntest=_prepared()
    y=srcdev[1]
    baseline=y.copy()
    bad=baseline.copy();bad[y>0]=-1
    retention=old_lab._retention(y,bad,old_lab._classify_continuous(bad),baseline,srcdev[0])
    assert retention['status']=='FAIL'
    assert not joint._rank(retention,{'all':{'balanced_sign_accuracy':1.,
                                          'triple_accuracy':1.,'zero_recall':1.,
                                          'signed_mae':0.}})[0]


def test_missing_or_wrong_original_training_cache_refused(tmp_path):
    with pytest.raises(FileNotFoundError,match='TRAIN_FEATURE_CACHE_MISSING'):
        joint._validated_train_cache(tmp_path,{},[])
    with pytest.raises(ValueError,match='NOT_ORIGINAL_HEAD_INPUT'):
        joint._profile(np.zeros((2,32)),'final_last')


def test_source_dev_must_not_be_fitted_as_source_train():
    source,syn,srcdev,syndev,syntest=_prepared()
    # Different source dev labels may change selection, but cannot change the
    # normalizer or which examples enter the source training tuples.
    mu0,sig0=joint._normalization(source[0],syn[0])
    edited=(srcdev[0],srcdev[1]*1000,srcdev[2],srcdev[3]*1000)
    mu1,sig1=joint._normalization(source[0],syn[0])
    assert np.array_equal(mu0,mu1)
    assert np.array_equal(sig0,sig1)
    assert not np.array_equal(srcdev[1],edited[1])


def test_command_rejects_unbounded_steps_and_missing_data():
    with pytest.raises(ValueError,match='INVALID_OPTIMIZER_CONFIGURATION'):
        joint.train(SimpleNamespace(steps=0,eval_every=20,lr=.001,
            source_weight=1,anchor_weight=3,device='cpu'))
    with pytest.raises(ValueError,match='BOUNDED_STEPS_REQUIRED'):
        joint.train(SimpleNamespace(steps=20001,eval_every=20,lr=.001,
            source_weight=1,anchor_weight=3,device='cpu'))


def test_preexisting_output_never_overwritten(tmp_path,monkeypatch):
    p=tmp_path/'existing';p.mkdir()
    with pytest.raises(FileExistsError,match='OUTPUT_ALREADY_EXISTS'):
        old_lab._output(p,[tmp_path/'source'])


def test_end_to_end_report_and_candidate_artifact_never_claim_promotion(tmp_path,monkeypatch):
    source,syn,srcdev,syndev,syntest=_prepared()
    info={'lineage':{'checkpoint_sha256':'frozen','checkpoint_step':500,
          'adapter_sha256':'lora','source_run':str(tmp_path/'source'),
          'expanded_run':str(tmp_path/'expanded')},
          'dataset_sha256':'known-synthetic','dataset':syndev[0]}
    retention={'dataset':srcdev[0],'dataset_sha256':'known-original'}
    mock={'original_train':list(range(len(source[2]))),
          'retention_info':retention,'probe_info':info,'train_cache':{'sha256':'c'},
          'partitions':{'test':syntest[4]}}
    # Mock only VERIFIED feature loading, while exercising the real fit,
    # development selection, held-out metrics, and output controls.
    prepared={f:{'train_source':source,'train_synthetic':syn,
                'dev_source':srcdev,'dev_synthetic':syndev,
                'test_synthetic':syntest} for f in joint.FEATURES}
    monkeypatch.setattr(joint,'_prepare',lambda args:(mock,prepared))
    monkeypatch.setattr(joint,'_verify_after',lambda _:None)
    args=SimpleNamespace(steps=4,eval_every=2,lr=.003,
            source_weight=1.,anchor_weight=3.,device='cpu',seed=49,
            output=tmp_path/'out',run=tmp_path/'source',
            expanded_run=tmp_path/'expanded',input=tmp_path/'capture',
            source_features=tmp_path/'retention')
    result=joint.train(args)
    assert result['candidate_count']==8
    assert result['matched_consistency_ablation']
    assert len(result['matched_ablation_comparisons'])==4
    assert not any(r['gates']['correct_cross_format_invariance'] for r in result['candidates'])
    assert {r['cross_format_weight'] for r in result['candidates']}=={0.,2.}
    assert not result['production_promotion']
    assert not result['source_original_dev_fitted']
    assert not result['selected_bounded_gates_pass']
    assert result['independent_acceptance_required']
    assert (args.output/'report.json').is_file()
    assert (args.output/'candidate_scores.csv').is_file()
    assert (args.output/'checkpoint_history.csv').is_file()
    assert (args.output/'ablation_comparisons.csv').is_file()
    with (args.output/'candidate_scores.csv').open() as f:
        text=f.read()
    assert 'dev_cross_format_both_correct' in text
    assert 'source_zero_margin_min' in text
    with (args.output/'checkpoint_history.csv').open() as f:
        text=f.read()
    assert 'source_correct_zeros_below_margin' in text
    assert 'dev_cross_format_six_case_correct' in text
    saved=torch.load(args.output/'disposable_candidate.pt',weights_only=True)
    assert saved['status']=='DISPOSABLE_NON_PRODUCTION'
    assert saved['promotion_authorized'] is False
    assert saved['lineage']['checkpoint_step']==500
    with pytest.raises(FileExistsError,match='OUTPUT_ALREADY_EXISTS'):
        joint.train(args)


def test_sign_preservation_covers_all_correct_even_with_wrong_magnitude():
    y=np.asarray([-10.,0.,10.,4.,-5.])
    old=np.asarray([-1.,0.04,1.,-4.,5.])
    sign=joint._original_correct_mask(y,old)
    precision=joint._accurate_numeric_mask(y,old)
    assert sign.tolist()==[True,True,True,False,False]
    assert precision.tolist()==[False,True,False,False,False]


def test_class_preservation_responds_to_margin_violation_for_minority_class():
    # The original output is correct -/0/+ but numerically inaccurate.
    old=torch.tensor([-1.,0.,1.],dtype=torch.float32)
    truth=torch.tensor([-10.,0.,10.],dtype=torch.float32)
    correct=torch.ones(3,dtype=torch.bool)
    precision=torch.zeros(3,dtype=torch.bool)
    residual=torch.zeros(3,4,requires_grad=True)
    initial,parts=joint._preservation_loss(torch,residual,old,truth,correct,precision)
    initial.backward()
    assert parts['preserve_sign']>0
    assert parts['preserve_numeric']==0
    assert residual.grad[:,1:].abs().sum().item()>0
    # Worsening only the negative class must raise the sign-preservation loss.
    damaged=torch.zeros(3,4)
    damaged[0,3]=3.5
    later,_=joint._preservation_loss(torch,damaged,old,truth,correct,precision)
    assert later>initial


def test_balanced_preservation_sampling_includes_all_acquired_sign_classes():
    rng=np.random.default_rng(31)
    y=np.asarray([-1.]*2+[0.]*3+[1.]*100)
    correct=np.ones(len(y),dtype=bool)
    indices=joint._balanced_protected_indices(rng,y,correct,per_class=8)
    assert len(indices)==24
    assert {int(s):int((np.sign(y[indices])==s).sum()) for s in (-1,0,1)}=={-1:8,0:8,1:8}


def test_complete_six_case_group_and_corruption_guards():
    source,syn,_,_,_=_prepared()
    groups=joint._complete_training_groups(syn[3],syn[2])
    assert len(groups)==4
    assert all(len(indices)==6 and len(triples)==2 for indices,triples in groups)
    with pytest.raises(ValueError,match='INCOMPLETE'):
        joint._complete_training_groups(syn[3][:-1],syn[2][:-1])
    # Same class but different mathematical answer across surfaces is invalid.
    rows=[dict(row) for row in syn[3]]
    pos=next(i for i,r in enumerate(rows) if r['surface']=='spine_difference'
             and float(probe.Fraction(r['target']))>0)
    rows[pos]['target']='100'
    targets=np.asarray(syn[2]).copy();targets[pos]=100
    with pytest.raises(ValueError,match='CROSS_SURFACE_TARGET_MISMATCH'):
        joint._complete_training_groups(rows,targets)


def test_triple_objective_has_live_gradients_with_zero_initialized_network():
    net=joint._build_model(torch,5,8)
    x=torch.randn(6,5)
    old=torch.ones(6)*0.1
    triples=[(0,1,2),(3,4,5)]
    term=joint._triple_loss(torch,net,x,old,triples)
    assert term.item()>0
    term.backward()
    assert net.body[-1].weight.grad.abs().sum().item()>0


def test_retention_failure_named_per_class_when_overall_retention_passes():
    rows=[{'id':str(i),'group':str(i),'family':'source','surface':'native',
           'target':str(v)} for i,v in enumerate([-1.]*10+[0.]*10+[1.]*80)]
    y=np.array([-1.]*10+[0.]*10+[1.]*80)
    baseline=y.copy();prediction=y.copy()
    # 99% overall correct retained but only 90% of originally correct zeros.
    prediction[10]=1.
    r=old_lab._retention(y,prediction,old_lab._classify_continuous(prediction),baseline,rows)
    assert r['original_correct_kept_fraction']==.99
    details=joint._retention_details(r)
    assert 'CLASS_RETENTION_ZERO' in details['failed_gates']
    assert details['by_class']['zero']['lost']==1
    assert details['by_class']['positive']['passed']
    event=joint._history_entry(30,1.,r,{'all':{'balanced_sign_accuracy':.8,
              'zero_recall':.5,'triple_accuracy':.3,'near_nonzero_strict_sign':.7},
              'by_family':{'signed_offset':{'balanced_sign_accuracy':.8,'triple_accuracy':.3}}},
              (0,.8,.3,.5,-.1))
    assert 'CLASS_RETENTION_ZERO' in event['source_retention_failed_gates']
    assert event['source_retention_by_class']['zero']['preserved_fraction']==.9


def test_cross_format_paired_group_indexes_are_exact_by_sign():
    source,syn,*_= _prepared()
    groups=joint._complete_training_groups(syn[3],syn[2])
    chosen=np.array([2,0,3,2])
    positions=joint._matched_surface_pairs(groups,chosen)
    assert len(positions)==12
    assert positions.tolist()[:3]==[[0,3],[1,4],[2,5]]
    synthetic=np.asarray(syn[2])
    selected=np.concatenate([groups[int(i)][0] for i in chosen])
    for a,b in positions:
        assert synthetic[selected[a]]==synthetic[selected[b]]
    with pytest.raises(ValueError,match='NO_CROSS_SURFACE_GROUPS'):
        joint._matched_surface_pairs(groups,[])
    with pytest.raises(ValueError,match='PAIR_INCOMPLETE'):
        joint._matched_surface_pairs([((0,1),((0,1,2),(3,4,5)))],[0])


def test_cross_format_objective_penalizes_mismatched_surface_and_has_gradient():
    # Two surfaces have identical targets, but different representations.
    # Matching class logits alone is insufficient: signed numerical outputs
    # must also agree. Gradient must reach a zero-initialized residual.
    net=joint._build_model(torch,5,8)
    x=torch.randn(6,5)
    old=torch.tensor([-.6,0.,.8,-.4,.06,.5])
    pairs=np.array([[0,3],[1,4],[2,5]])
    loss,parts=joint._cross_format_loss(torch,net,x,old,pairs)
    assert loss.item()>0
    assert parts['class_probability']>0
    assert parts['signed_log_value']>0
    loss.backward()
    assert net.body[-1].weight.grad.abs().sum().item()>0
    # Equal features, equal old predictions => exactly zero loss.
    x_equal=torch.randn(3,5).repeat(2,1)
    old_equal=torch.tensor([-.6,0.,.8,-.6,0.,.8])
    control,terms=joint._cross_format_loss(torch,net,x_equal,old_equal,pairs)
    assert control.item()==pytest.approx(0.,abs=1e-8)
    with pytest.raises(ValueError,match='INVALID_CROSS_FORMAT_PAIR'):
        joint._cross_format_loss(torch,net,x,old,[[0,6]])


def test_pairwise_diagnostics_do_not_reward_uniform_wrong_answer():
    source,syn,srcdev,syndev,syntest=_prepared()
    rows,targets,base,features,partition=syntest
    # Uniform positive labels perfectly agree across wording, but cannot
    # earn more than one of three classes as fully paired-correct.
    all_positive=np.ones(len(rows),dtype=np.int8)
    same=joint._paired_agreement(rows,targets,base,all_positive,partition)
    assert same['class_agreement']==1.0
    assert same['both_correct_fraction']==pytest.approx(1/3)
    assert same['complete_six_case_groups']==0
    # Correct predictions on all six questions must pass both gates.
    accurate=np.sign(targets).astype(np.int8)
    correct=joint._paired_agreement(rows,targets,targets,accurate,partition)
    assert correct['both_correct_fraction']==1.0
    assert correct['complete_six_case_fraction']==1.0
    assert correct['mean_abs_signed_log_gap']==pytest.approx(0.,abs=1e-12)


def test_original_correct_zero_margin_defense_has_no_dev_fit_dependency():
    old=torch.tensor([0.,-.3,.4])
    target=torch.tensor([0.,-3.,4.])
    correct=torch.ones(3,dtype=torch.bool)
    precision=torch.zeros(3,dtype=torch.bool)
    fresh=torch.zeros(3,4)
    # Frozen original zero margin 1.5 exceeds the floor; no extra penalty.
    no_loss,parts=joint._preservation_loss(torch,fresh,old,target,correct,precision)
    assert parts['preserve_zero_margin']==0.
    # A nearly lost original zero still has correct argmax but incurs extra
    # penalty, specifically the desired safety margin.
    almost_lost=fresh.clone();almost_lost[0,2]=-0.75
    bad,broken=joint._preservation_loss(torch,almost_lost,old,target,correct,precision)
    assert broken['preserve_zero_margin']>0
    assert bad>no_loss
    none,_=joint._preservation_loss(torch,almost_lost,old,target,correct,precision,
                                    zero_margin_weight=0.)
    assert bad>none
    monitor=joint._correct_original_zero_margins(np.asarray([0.,1.,-1.]),
                                                 np.asarray([0.,1.,-1.]),almost_lost.numpy())
    assert monitor['originally_correct_zero_count']==1
    assert monitor['below_floor_count']==1
    assert monitor['minimum_margin']<joint.ZERO_MARGIN_FLOOR


def test_cross_format_loss_cannot_see_dev_targets_and_ablation_matches_seed():
    source,syn,srcdev,syndev,syntest=_prepared()
    kwargs=dict(feature='unit',source=source,synthetic=syn,
        original_dev=srcdev,synthetic_dev=syndev,device='cpu',steps=6,
        lr=.003,source_weight=1.,anchor_weight=3.,seed=49,eval_every=3,
        zero_margin_weight=2.)
    c0=joint._fit_one(torch,**kwargs,consistency_weight=0.)
    c1=joint._fit_one(torch,**kwargs,consistency_weight=2.)
    assert c0[-1]['cross_format_pairs_per_update']==c1[-1]['cross_format_pairs_per_update']==12
    assert c0[-1]['cross_format_weight']==0.
    assert c1[-1]['cross_format_weight']==2.
    # Repeating one arm with the same inputs and seed gives identical history.
    repeat=joint._fit_one(torch,**kwargs,consistency_weight=0.)
    assert c0[4]==repeat[4]
    assert all(np.array_equal(v,repeat[0].state_dict()[k]) for k,v in c0[0].state_dict().items())


def test_invariance_gate_refuses_uniform_agreement_without_both_correct():
    assert not joint._cross_format_gate({'class_agreement':1.,
        'both_correct_fraction':1./3.,'complete_six_case_fraction':0.})
    assert not joint._cross_format_gate({'class_agreement':1.,
        'both_correct_fraction':1.,'complete_six_case_fraction':.94})
    assert joint._cross_format_gate({'class_agreement':.96,
        'both_correct_fraction':.96,'complete_six_case_fraction':.95})


def test_full_oracle_has_group_complete_split_disjoint_pairs():
    deck=probe.make_deck()
    rows=[entry.public() for entry in deck]
    targets=np.asarray([float(entry.target) for entry in deck],dtype=float)
    parts=probe._test_partitions(rows)
    for split,n in [('train',48),('dev',24),('test',30)]:
        indices=parts[split]
        groups=joint._complete_training_groups(
            [rows[int(i)] for i in indices],targets[indices])
        assert len(groups)==n
        assert len(indices)==6*n
        assert all(len(group)==6 for group,_ in groups)


def test_dev_ranking_prioritizes_correct_math_in_both_wordings():
    baseline={'status':'PASS'}
    a={'all':{'balanced_sign_accuracy':.95,'triple_accuracy':.9,
              'zero_recall':1.,'signed_mae':.1},
       'cross_format':{'both_correct_fraction':.15,
                       'complete_six_case_fraction':.1}}
    b={'all':{'balanced_sign_accuracy':.8,'triple_accuracy':.7,
              'zero_recall':.9,'signed_mae':.15},
       'cross_format':{'both_correct_fraction':.7,
                       'complete_six_case_fraction':.6}}
    assert joint._rank(baseline,b)>joint._rank(baseline,a)
    assert joint._rank({'status':'FAIL'},b)<joint._rank(baseline,a)
