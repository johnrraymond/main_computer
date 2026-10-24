"""Stage-gated HybridAgent v4: pretrained readout, four products, causal learning."""
from __future__ import annotations
import copy
import argparse
import pytest
import torch
from pathlib import Path
from tools import nanojev_hawk_dove_gaussian_hybrid_agent_v4 as v4
from tools import nanojev_hawk_dove_gaussian_hybrid_agent as prior
from tools import nanojev_hawk_dove_gaussian_population_conditional as gauss
from tests.test_nanojev_hawk_dove_gaussian_hybrid_agent_v3 import fixture, flags


def opts(source, output, **kw):
    a=flags(source,output,min_teacher_gain=kw.pop('min_teacher_gain', .02),**kw)
    return a


def test_source_baseline_readout_without_reference_call(tmp_path):
    _,ref,splits=fixture(tmp_path)
    agent=v4.ContextualGaussianHybridAgent(ref)
    data=splits['dev']['features']
    assert torch.equal(agent.teacherless_density(data),ref(data))
    assert torch.equal(agent(data,0),ref(data))
    # The teacherless student is genuinely trainable, not a frozen alias.
    assert agent.answer.weight.data_ptr()!=ref.encoder[4].weight.data_ptr()
    assert torch.equal(agent.answer.weight,ref.encoder[4].weight)
    with torch.no_grad():
        agent.answer.bias.add_(0.5)
    assert not torch.equal(agent.teacherless_density(data),ref(data))
    assert torch.equal(agent(data,0),ref(data))


def test_teacher_has_four_distinct_product_roles_and_no_repetitions(tmp_path):
    _,ref,splits=fixture(tmp_path)
    agent=v4.ContextualGaussianHybridAgent(ref)
    x=splits['train']['features'][:7]
    tokens=agent.teacher_tokens(x)
    assert tokens.shape==(7,4,ref.hidden)
    assert torch.equal(v4.stage_values(x),prior.partial_products(x))
    assert not torch.equal(tokens[:,0], tokens[:,1])
    assert torch.equal(agent.teacher_prefix(x,4)[:,2:],tokens)
    assert v4.phase_depth(1,12)==1
    assert v4.phase_depth(5,12)==2
    assert v4.phase_depth(9,12)==4
    for method in (lambda:agent.teacher_prefix(x,5),
                   lambda:agent.link_activation(x,5),
                   lambda:agent.first_pass(x,5),
                   lambda:agent.rollout(x,8)):
        with pytest.raises(ValueError):method()



def test_stage_parameter_gradients(tmp_path):
    _,ref,splits=fixture(tmp_path)
    agent=v4.ContextualGaussianHybridAgent(ref)
    x=splits['train']['features'][:5]
    y=splits['train']['numeric'][:5]
    domain=((-2500000.,2500000.),)*2
    v4.configure_trainable(agent,'bootstrap')
    loss=v4._numerical_loss(agent.teacher_answer(x,4),y,domain,32,2.5)
    loss.backward()
    for part in ('core','typed','answer','thought_update'):
        assert any(p.grad is not None and p.grad.abs().sum()>0
                   for p in getattr(agent,part).parameters()),part
    assert all(p.grad is None for p in agent.link.parameters())
    agent.zero_grad(set_to_none=True)
    v4.configure_trainable(agent,'activation')
    loss=agent.link_activation(x,depth=4)
    loss.backward()
    assert any(p.grad is not None for p in agent.link.parameters())
    assert all(p.grad is None for p in agent.core.parameters())
    assert all(p.grad is None for p in ref.parameters())
    agent.zero_grad(set_to_none=True)
    v4.configure_trainable(agent,'joint')
    loss,_=v4.self_conditioned_loss(agent,x,y,domain,32,2.5,depth=4)
    loss.backward()
    for part in ('core','link','answer','thought_update'):
        assert any(p.grad is not None and p.grad.abs().sum()>0
                   for p in getattr(agent,part).parameters()),part
    assert all(p.grad is None for p in agent.typed.parameters())


def test_gate_refusal_before_activation(tmp_path):
    source,_,_=fixture(tmp_path)
    with pytest.raises(RuntimeError,match='TEACHER_INFORMATIVITY_GATE_FAILED'):
        v4.train(opts(source,tmp_path/'reject',min_teacher_gain=.02))
    out=tmp_path/'reject'
    assert (out/'recovery_gate.json').exists()
    assert (out/'teacher_gate.json').exists()
    assert not (out/'activation.json').exists()
    assert not (out/'best.pt').exists()


def test_end_to_end_read_only_source(tmp_path,monkeypatch):
    patch_accept_gate_for_training_smoke(monkeypatch)
    source,_,_=fixture(tmp_path)
    before=prior.sha256_file(source)
    out=tmp_path/'run'
    result=v4.train(opts(source,out))
    assert prior.sha256_file(source)==before
    assert result['recovery_gate']['status']=='PASS'
    assert result['teacher_gate']['status']=='PASS'
    assert result['teacher_gate']['teacher_depth']==4
    assert result['teacher_gate']['selected_teacher_step']>0
    assert result['trained_steps']==4
    assert (out/'teacher_best.pt').exists()
    assert (out/'best.pt').exists()
    assert (out/'latest.pt').exists()
    assert (out/'activation.json').exists()


@pytest.mark.parametrize('phase,stop',[
    ('bootstrap',{'bootstrap_stop_after':1}),
    ('activation',{'warmup_stop_after':1}),
    ('joint',{'stop_after':2}),
])
def test_exact_resume_all_stages(tmp_path,phase,stop,monkeypatch):
    patch_accept_gate_for_training_smoke(monkeypatch)
    source,_,_=fixture(tmp_path)
    full=v4.train(opts(source,tmp_path/'full'))
    target=tmp_path/phase
    part=v4.train(opts(source,target,**stop))
    assert part['status'].startswith('PAUSED_')
    end=v4.train(opts(source,target,resume=True))
    assert end['best_step']==full['best_step']
    lhs=torch.load(tmp_path/'full'/'latest.pt',weights_only=True)
    rhs=torch.load(target/'latest.pt',weights_only=True)
    assert lhs['history']==rhs['history']
    for name in lhs['model']:
        assert torch.equal(lhs['model'][name],rhs['model'][name]),(phase,name)


def test_resume_refuses_modified_reference(tmp_path):
    source,_,_=fixture(tmp_path)
    out=tmp_path/'run'
    v4.train(opts(source,out,bootstrap_stop_after=1))
    checkpoint=torch.load(source,weights_only=True)
    checkpoint['model']['encoder.0.bias'][0]+=1.
    torch.save(checkpoint,source)
    with pytest.raises(RuntimeError,match='IDENTITY_DRIFT'):
        v4.train(opts(source,out,resume=True))


def patch_accept_gate_for_training_smoke(monkeypatch):
    """ONLY lifecycle tests simulate acceptance; real gate runs in other tests."""
    original=v4.teacher_gate_decision
    def simulated(diag,threshold):
        evidence=original(diag,threshold)
        evidence['status']='PASS'  # Not evidence of teacher utility
        evidence['simulation_for_restart_tests_only']=True
        return evidence
    monkeypatch.setattr(v4,'teacher_gate_decision',simulated)


def fabricated_paired_diagnostic(gain, wins=3, losses=0, winning_matrices=3):
    contrasts={name:{'absolute_gain':gain,'rescued':wins,'regressed':losses,
                     'winning_matrices':winning_matrices,'losing_matrices':0,
                     'matrix_net_rescues':{'0':1,'1':1,'2':1}}
               for name in ('reference','shuffled','zero_tokens')}
    return {'min_paired_tier_gain':gain,
            'tiers':{tier:{'paired':copy.deepcopy(contrasts)} for tier in v4.blind.TIERS}}


def test_real_default_gate_rejects_identical_correct_shuffled_and_tiny_gain():
    assert v4.parser().parse_args(['--reference-checkpoint','fake.pt','--output','out']).min_teacher_gain==.02
    tied=fabricated_paired_diagnostic(0.,wins=0,losses=0,winning_matrices=0)
    assert v4.teacher_gate_decision(tied,.02)['status']=='FAIL'
    small=fabricated_paired_diagnostic(.000012,wins=1,losses=0,winning_matrices=1)
    assert v4.teacher_gate_decision(small,.02)['status']=='FAIL'
    # Same overall metrics can hide a tie in one tier. Every tier must pass.
    mixed=fabricated_paired_diagnostic(.04)
    mixed['tiers']['T08']['paired']['shuffled']['absolute_gain']=0.
    assert v4.teacher_gate_decision(mixed,.02)['status']=='FAIL'


def test_paired_teacher_gate_requires_two_winning_matrices_and_can_pass():
    good=fabricated_paired_diagnostic(.04)
    assert v4.teacher_gate_decision(good,.02)['status']=='PASS'
    good['tiers']['T04']['paired']['zero_tokens']['winning_matrices']=1
    assert v4.teacher_gate_decision(good,.02)['status']=='FAIL'


def test_real_paired_evaluation_refuses_teacher_identical_to_source(tmp_path,monkeypatch):
    source,ref,splits=fixture(tmp_path)
    agent=v4.ContextualGaussianHybridAgent(ref)
    # Every teacher intervention returns exactly the reference's predictions.
    monkeypatch.setattr(agent,'teacher_answer',lambda features,depth=4,intervention='normal',shuffled_tokens=None:ref(features))
    args=opts(source,tmp_path/'out')
    domain=((-2500000.,2500000.),)*2
    args.target_sigma_bins=2.5
    diag=v4.paired_teacher_diagnostic(agent,splits['dev'],domain,args)
    assert diag['teacher_depth']==4
    assert diag['min_paired_tier_gain']==0
    assert v4.teacher_gate_decision(diag,.02)['status']=='FAIL'
    for tier in v4.blind.TIERS:
        for name in ('reference','shuffled','zero_tokens'):
            assert diag['tiers'][tier]['paired'][name]['rescued']==0
            assert diag['tiers'][tier]['paired'][name]['regressed']==0


def test_teacher_comparison_uses_original_source_not_retrained_student(tmp_path,monkeypatch):
    source,ref,splits=fixture(tmp_path)
    agent=v4.ContextualGaussianHybridAgent(ref)
    args=opts(source,tmp_path/'out')
    args.target_sigma_bins=2.5
    domain=((-2500000.,2500000.),)*2
    before=v4.paired_teacher_diagnostic(agent,splits['dev'],domain,args)
    with torch.no_grad():agent.answer.bias.add_(.5)
    after=v4.paired_teacher_diagnostic(agent,splits['dev'],domain,args)
    for tier in v4.blind.TIERS:
        assert before['tiers'][tier]['all_masks_joint_xy_accuracy']['reference']==after['tiers'][tier]['all_masks_joint_xy_accuracy']['reference']


def test_paired_all_masks_requires_both_presentations(tmp_path,monkeypatch):
    source,ref,splits=fixture(tmp_path)
    original=splits['dev']
    # Duplicate each instance under a second mask. Shuffled feedback still
    # changes the group as a whole; pairs use the SAME instances and masks.
    indexes=list(range(len(original['rows'])))
    rows=[]
    for i in indexes:
        r=original['rows'][i]
        for mask in (0,1):rows.append({**r,'mask':mask})
    double={'rows':rows,
            'features':original['features'].repeat_interleave(2,dim=0),
            'numeric':original['numeric'].repeat_interleave(2,dim=0)}
    agent=v4.ContextualGaussianHybridAgent(ref)
    args=opts(source,tmp_path/'out');args.target_sigma_bins=2.5
    domain=((-2500000.,2500000.),)*2
    single=v4.paired_teacher_diagnostic(agent,original,domain,args)
    doubled=v4.paired_teacher_diagnostic(agent,double,domain,args)
    assert single['sample_instances']==doubled['sample_instances']
    assert single['min_paired_tier_gain']==doubled['min_paired_tier_gain']



def test_shuffle_never_swaps_two_masks_of_same_instance(tmp_path):
    _, ref, splits = fixture(tmp_path)
    agent = v4.ContextualGaussianHybridAgent(ref)
    # Every instance has two presentation masks and identical products within
    # its pair. The old roll(1, 0) left every second example unchanged.
    base = splits['dev']
    x = base['features'].repeat_interleave(2, dim=0)
    rows = []
    for row in base['rows']:
        rows += [{**row, 'mask': 0}, {**row, 'mask': 1}]
    thoughts = agent.teacher_tokens(x)
    old = thoughts.roll(1, 0)
    assert bool((old == thoughts).flatten(1).all(dim=1).any())
    donors = v4.distinct_instance_thought_donors(x, thoughts, rows)
    assert not bool((thoughts.index_select(0,donors) == thoughts).flatten(1).all(dim=1).any())
    products = v4.stage_values(x)
    assert not bool((products.index_select(0,donors) == products).all(dim=1).any())
    for i in range(0, x.shape[0], 2):
        assert donors[i].item() == donors[i+1].item()
        assert rows[donors[i]]['instance'] != rows[i]['instance']
    assert torch.equal(donors, v4.distinct_instance_thought_donors(x, thoughts, rows))


def test_shuffle_batch_independent_and_control_changes_all_rows(tmp_path):
    source, ref, splits = fixture(tmp_path)
    agent = v4.ContextualGaussianHybridAgent(ref)
    original = splits['dev']
    split = dict(original)
    split['features'] = original['features'].repeat_interleave(2, dim=0)
    split['numeric'] = original['numeric'].repeat_interleave(2, dim=0)
    split['rows'] = [{**row,'mask':mask} for row in original['rows'] for mask in (0,1)]
    args = opts(source,tmp_path/'out')
    args.target_sigma_bins = 2.5
    domain = ((-2500000.,2500000.),)*2
    args.eval_batch_size=64
    first = v4.paired_teacher_diagnostic(agent,split,domain,args)
    args.eval_batch_size=17  # Deliberately splits mask siblings.
    second = v4.paired_teacher_diagnostic(agent,split,domain,args)
    assert first['tiers'] == second['tiers']
    assert first['shuffle_control'] == second['shuffle_control']
    assert first['shuffle_control']['changed_teacher_sequences'] == len(split['rows'])
    assert first['shuffle_control']['paired_masks_share_donor']


def test_shuffle_fails_closed_when_only_identical_products(tmp_path):
    _, ref, splits = fixture(tmp_path)
    agent = v4.ContextualGaussianHybridAgent(ref)
    x = splits['dev']['features'][:1].repeat(4, 1)
    thoughts = agent.teacher_tokens(x)
    with pytest.raises(ValueError, match='SHUFFLE_NO_DISTINCT_DONOR'):
        v4.distinct_instance_thought_donors(x, thoughts)
    with pytest.raises(ValueError, match='SHUFFLE_NO_DISTINCT_DONOR'):
        agent.teacher_answer(x, 4, 'shuffled')
    # Even when visible products differ, indistinguishable tokens must refuse.
    distinct = splits['dev']['features'][:32]
    fixed = torch.zeros((len(distinct), 4, ref.hidden))
    with pytest.raises(ValueError, match='SHUFFLE_INDISTINGUISHABLE_TOKENS'):
        v4.distinct_instance_thought_donors(distinct, fixed)
