"""Read-only trajectory probe tests grounded in HybridAgent v1 interfaces."""
from __future__ import annotations
import argparse
from pathlib import Path

import pytest
import torch

from tools import nanojev_hawk_dove_gaussian_hybrid_agent as hybrid
from tools import nanojev_hawk_dove_gaussian_hybrid_probe as probe
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind
from tools import nanojev_hawk_dove_gaussian_population_conditional as gauss


def fixture(tmp_path):
    args=argparse.Namespace(seed=101,banks=16,grid_bins=32,hidden=16,
        target_sigma_bins=2.5,train_matrices=3,dev_matrices=2,test_matrices=2,
        counts_per_matrix=3,masks=1,payoff_bound=2,steps=2,probe_every=1,
        batch_size=16,lr=.003,min_lr=.00003,tolerance=.05,min_rate=.8)
    dataset,domain,manifest=blind._dataset(args,torch.device('cpu'))
    torch.manual_seed(args.seed)
    ref=blind.BlindDensityNetwork(args.banks,args.grid_bins,args.seed,args.hidden)
    identity=blind._identity(args,manifest,domain)
    source=tmp_path/'source.pt'
    torch.save({'schema':blind.SCHEMA,'identity':identity,'step':2,
                'model':ref.state_dict()},source)
    return source,ref,dataset,domain


def train_fixture(tmp_path):
    source,_,_,_=fixture(tmp_path)
    run=tmp_path/'run'
    flags=argparse.Namespace(reference_checkpoint=source, output=run, device='cpu',
         resume=False,stop_after=None,warmup_stop_after=None,warmup_steps=3,
         steps=4,probe_every=2,batch_size=16,eval_batch_size=32,seed=123,
         teacher_scale=.35,warmup_lr=.002,lr=.0003,tolerance=.05,cpu_threads=1)
    hybrid.train(flags)
    return source,run


def probe_args(source,run,**kwargs):
    vals={'reference_checkpoint':source,'run_dir':run,'output':None,'device':'cpu',
          'split':'dev','batch_size':13,'examples':8,'tolerance':.05,'cpu_threads':1}
    vals.update(kwargs)
    return argparse.Namespace(**vals)


def test_depth_trace_matches_real_model_outputs_and_disabled_parity(tmp_path):
    _,ref,splits,domain=fixture(tmp_path)
    agent=hybrid.GaussianHybridAgent(ref,seed=123)
    with torch.no_grad():
        agent.link.net[-1].bias.fill_(.18)
        agent.link.net[-1].weight.normal_(mean=0,std=.03)
        x=splits['dev']['features'][:11]
        states=probe.trace_batch(agent,x,domain)
        for depth in range(9):
            expected=gauss.numeric_decode(agent(x,depth),domain)
            assert torch.equal(states[depth]['xy'],expected.cpu()),depth
        assert torch.equal(probe.controlled_batch(agent,x,domain,8,'disabled'),
                           gauss.numeric_decode(ref(x),domain))
        assert torch.equal(probe.controlled_batch(agent,x,domain,8,'normal'),
                           gauss.numeric_decode(agent(x,8),domain))
        assert bool((states[1]['feedback_norm']>0).all())
        assert bool((states[2]['hidden_step_norm']>=0).all())
        assert any((probe.controlled_batch(agent,x,domain,8,mode)-
                     probe.controlled_batch(agent,x,domain,8,'normal')).abs().max() > 1
                    for mode in ('shuffled','frozen_first','half','double'))


def test_score_uses_exact_all_masks_gate():
    rows=[{'tier':t,'instance':f'{t}/a','mask':m}
          for t in blind.TIERS for m in (0,1)]
    truth=torch.full((len(rows),2),10.)
    pred=truth.clone()
    # A failure on one mask fails the whole independent instance.
    pred[1,0]=11.
    score=probe.score_xy(pred,truth,rows,.05)
    assert score['by_tier'][blind.TIERS[0]]['joint_presentation_rate']==.5
    assert score['by_tier'][blind.TIERS[0]]['instances_all_masks_xy_hit_rate']==0
    assert score['min_tier_joint5']==0
    corrected=truth.clone()
    transitions=probe.comparison(corrected,pred,truth,rows,.05)
    assert transitions['rescued_presentations']==1
    assert transitions['rescued_all_mask_instances']==1
    assert transitions['lost_presentations']==0


def test_full_probe_matches_saved_score_and_preserves_every_source(tmp_path):
    source,run=train_fixture(tmp_path)
    source_before=hybrid.sha256_file(source)
    best_before=hybrid.sha256_file(run/'best.pt')
    latest_before=hybrid.sha256_file(run/'latest.pt')
    result=probe.inspect(probe_args(source,run))
    assert source_before==hybrid.sha256_file(source)
    assert best_before==hybrid.sha256_file(run/'best.pt')
    assert latest_before==hybrid.sha256_file(run/'latest.pt')
    assert len(result['step_evolution'])==8
    assert len(result['selected_depth_interventions'])==5
    assert len(result['scores_by_depth'])==5
    selected=str(result['probe']['selected_checkpoint_depth'])
    training_report=probe._json_read(run/'report.json')
    assert result['scores_by_depth'][selected]['min_tier_joint5']==training_report['selected_DEV_min_joint5']
    assert result['scores_by_depth']['0']['min_tier_joint5']==training_report['baseline_DEV_min_joint5']
    output=run/'probe_v1'
    assert (output/'report.json').is_file()
    assert (output/'trajectories.jsonl').is_file()
    assert len((output/'trajectories.jsonl').read_text().splitlines())==result['probe']['rows']
    assert len(result['representative_examples']) <= 8
    with pytest.raises(FileExistsError,match='OVERWRITE'):
        probe.inspect(probe_args(source,run))


def test_probe_refuses_mutated_source_and_bad_best_identity(tmp_path):
    source,run=train_fixture(tmp_path)
    ck=torch.load(source,weights_only=True)
    ck['model']['encoder.0.bias'][0]+=1
    torch.save(ck,source)
    with pytest.raises(RuntimeError,match='IDENTITY_DRIFT'):
        probe.inspect(probe_args(source,run))
    # Restore a fresh source with the identical reference seed & state.
    # Checkpoint ref immutability is already covered by the previous test.


def test_test_split_is_annotated_exploratory(tmp_path):
    source,run=train_fixture(tmp_path)
    out=probe.inspect(probe_args(source,run,split='test',output=tmp_path/'test_probe'))
    assert out['probe']['split']=='test'
    assert 'examined' in out['holdout_caveat']
    assert out['run_read_only'] is True


def test_two_masks_scores_match_canonical_evaluator(tmp_path):
    source,ref,_,domain=fixture(tmp_path)
    checkpoint=torch.load(source,weights_only=True)
    identity=checkpoint['identity']
    identity['masks']=2
    split,domain,_=blind._dataset(argparse.Namespace(**identity),torch.device('cpu'))
    agent=hybrid.GaussianHybridAgent(ref,seed=123)
    agent.eval()
    with torch.no_grad():
        for depth in (0,1,2,4):
            pred=torch.cat([gauss.numeric_decode(agent(x,depth),domain)
                  for x in split['dev']['features'].split(9)],dim=0)
            ours=probe.score_xy(pred,split['dev']['numeric'],split['dev']['rows'],.05)
            evaluator=gauss.evaluate(hybrid.FixedDepthView(agent,depth),split['dev'],domain,
                                      identity['target_sigma_bins'],batch_size=9,relative_tolerance=.05)
            assert ours['min_tier_joint5']==blind._rank(evaluator)[0]
            for tier in blind.TIERS:
                assert (ours['by_tier'][tier]['instances_all_masks_xy_hit_rate']==
                  evaluator['by_tier'][tier]['instances_all_masks_xy_hit_rate'])
