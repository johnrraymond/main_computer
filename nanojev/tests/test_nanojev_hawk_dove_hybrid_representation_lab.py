"""Separated numerical representation and anchor-free attention diagnostics."""
from __future__ import annotations
import copy
import json
import pytest
import torch
from tools import nanojev_hawk_dove_hybrid_representation_lab as lab
from tools import nanojev_hawk_dove_gaussian_hybrid_agent_v4 as v4
from tools import nanojev_hawk_dove_hybrid_teacher_probe as probe
from tests.test_nanojev_hawk_dove_hybrid_teacher_probe import seed_case


def flags(source, run_dir, output, **kw):
    cfg=dict(reference_checkpoint=source,run_dir=run_dir,output=output,checkpoint='teacher_best.pt',
        device='cpu',steps_reconstruction=2,steps_attention=2,probe_every=1,batch_size=8,
        eval_batch_size=64,lr=.003,seed=909,heads=4,layers=1,tolerance=.05,cpu_threads=1,
        resume=False,stop_after=None,final_test=False)
    cfg.update(kw)
    from argparse import Namespace
    return Namespace(**cfg)


def test_fixed_tokens_retain_products_exactly(tmp_path):
    source,run,_,_=seed_case(tmp_path)
    model,splits,domain,config,saved=probe.load_experiment(source,run)
    x=splits['dev']['features']
    token=lab.fixed_tokens(x,model.reference.hidden)
    assert token.shape==(len(x),4,model.reference.hidden)
    assert torch.equal(token[:,:,4],v4.stage_values(x))
    assert not token.requires_grad


def test_anchor_free_attention_and_gradients(tmp_path,monkeypatch):
    source,run,_,_=seed_case(tmp_path)
    model,splits,domain,config,saved=probe.load_experiment(source,run)
    torch.manual_seed(51)
    arms=lab.make_arms(model,4,1)
    for kind in ('attention_fixed','attention_frozen','attention_joint'):
        arm=arms[kind];x=splits['train']['features'][:8]
        density,location=arm(x,return_location=True)
        assert density.shape==(len(x),2,model.reference.bins)
        loss=location.square().mean(); loss.backward()
        assert arm.core.positions.grad is not None
        assert not any('reference' in name for name,_ in arm.named_parameters())
        if kind=='attention_frozen':
            assert not any(p.requires_grad for p in arm.typed.parameters())
        if kind=='attention_joint':
            assert any(p.grad is not None for p in arm.typed.parameters())


def test_identical_initial_readouts(tmp_path):
    source,run,_,_=seed_case(tmp_path)
    model,*_=probe.load_experiment(source,run)
    arms=lab.make_arms(model,4,1)
    for key in ('attention_frozen','attention_joint'):
        for state,value in arms['attention_fixed'].core.state_dict().items():
            assert torch.equal(value,arms[key].core.state_dict()[state])
        for state,value in arms['attention_fixed'].readout.state_dict().items():
            assert torch.equal(value,arms[key].readout.state_dict()[state])


def test_e2e_no_source_modification_or_test_selection(tmp_path):
    source,run,_,_=seed_case(tmp_path)
    original=(probe.sha256(source),probe.sha256(run/'teacher_best.pt'))
    res=lab.run(flags(source,run,tmp_path/'results'))
    assert set(res['arms'])==set(lab.ARMS)
    assert res['arms']['attention_joint']['DEV']['min_tier_joint5']>=0
    assert res['arms']['recon_joint']['DEV']['mean_abs_normalized_product_error']>=0
    assert res['arms']['attention_fixed']['TEST'] is None
    assert res['controls']['dev']['raw_products']==res['controls']['dev']['fixed_tokens_exact_inverse']
    assert (probe.sha256(source),probe.sha256(run/'teacher_best.pt'))==original
    with pytest.raises(FileExistsError,match='OVERWRITE'):
        lab.run(flags(source,run,tmp_path/'results'))


def test_resume_exact_full_training(tmp_path):
    source,run,_,_=seed_case(tmp_path)
    full=lab.run(flags(source,run,tmp_path/'full'))
    split=lab.run(flags(source,run,tmp_path/'partial',stop_after=1))
    assert split['status']=='PAUSED'
    continued=lab.run(flags(source,run,tmp_path/'partial',resume=True))
    for key in lab.ARMS:
        assert full['arms'][key]==continued['arms'][key]
        a=torch.load(tmp_path/'full'/key/'best.pt',weights_only=True)
        b=torch.load(tmp_path/'partial'/key/'best.pt',weights_only=True)
        for name in a['model']:
            assert torch.equal(a['model'][name],b['model'][name]),(key,name)


def test_source_identity_refusal(tmp_path):
    source,run,_,_=seed_case(tmp_path)
    lab.run(flags(source,run,tmp_path/'result',stop_after=1))
    saved=torch.load(run/'teacher_best.pt',weights_only=True)
    saved['model']['answer.bias'][0]+=1
    torch.save(saved,run/'teacher_best.pt')
    with pytest.raises(RuntimeError,match='IDENTITY_DRIFT'):
        lab.run(flags(source,run,tmp_path/'result',resume=True))


def test_controls_gaussian_ceiling_and_distinct_thought_ablation(tmp_path):
    source,run,_,_=seed_case(tmp_path)
    model,splits,domain,config,saved=probe.load_experiment(source,run)
    x=splits['dev']['features']
    arm=lab.make_arms(model,4,1)['attention_fixed'].eval()
    metrics=lab.oracle_density_ceiling(model.reference,splits['dev'],domain,16,.05)
    assert metrics['presentations']==len(splits['dev']['rows'])
    intervention=lab.attention_ablation(arm,splits['dev'],domain,16,.05)
    assert intervention['all_shuffled_sequences_distinct']
    assert intervention['masks_share_donors']
    assert set(intervention['paired_case_differences'])=={'shuffled','zero_tokens'}
