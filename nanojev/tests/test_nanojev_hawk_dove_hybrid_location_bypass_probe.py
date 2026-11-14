"""Immutable paired numerical-location bypass diagnostics."""
from __future__ import annotations

import json
from argparse import Namespace

import pytest
import torch
from tools import nanojev_hawk_dove_hybrid_location_bypass_probe as p
from tools import nanojev_hawk_dove_hybrid_representation_lab as lab
from tools import nanojev_hawk_dove_hybrid_teacher_probe as probe
from tests.test_nanojev_hawk_dove_hybrid_teacher_probe import seed_case


def stub_completed_lab(tmp_path):
    source,run,_,_=seed_case(tmp_path)
    model,splits,domain,config,_=probe.load_experiment(source,run)
    options=Namespace(steps_reconstruction=1,steps_attention=1,batch_size=8,
        eval_batch_size=16,probe_every=1,lr=.001,seed=92,heads=4,layers=1,
        tolerance=.05,cpu_threads=1,checkpoint='teacher_best.pt',final_test=False)
    identity=lab.make_identity(source,run,config,config['source_identity'],options)
    out=tmp_path/'lab';out.mkdir()
    (out/'config.json').write_text(json.dumps(identity))
    torch.manual_seed(12)
    for name,arm in lab.make_arms(model,4,1).items():
        if not name.startswith('attention_'):continue
        dev=lab.evaluate_attention(arm,splits['dev'],domain,13,.05)
        (out/name).mkdir()
        torch.save({'schema':lab.SCHEMA+'.best','identity':identity,'key':name,
                    'step':1,'model':arm.state_dict(),'DEV':dev},out/name/'best.pt')
        (out/name/'done.json').write_text(json.dumps({'schema':lab.SCHEMA,
            'arm':name,'best_step':1,'DEV':dev,'train_updates':1}))
    return source,run,out,splits,domain


def cli(source,run,lab_dir,output):
    return Namespace(reference_checkpoint=source,run_dir=run,lab_dir=lab_dir,
        output=output,device='cpu',batch_size=11,cpu_threads=1,tolerance=.05)


def test_location_roundtrip_and_gaussian_disagreement():
    domain=[[-5e6,5e6],[-5e6,5e6]]
    coords=torch.tensor([[0.,.5],[.5,1.],[.33,.77]],dtype=torch.float32)
    original=p.location_to_numeric(coords,domain)
    back=p.numeric_to_location(original,domain)
    assert torch.allclose(coords,back,atol=1e-7)
    # Exact-zero scoring must never turn a tiny numerical discrepancy into a hit.
    from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind
    truth=torch.zeros(len(blind.TIERS)*2,2)
    data={'numeric':truth,'rows':[{'tier':tier,'instance':tier} for tier in blind.TIERS for _ in (0,1)]}
    good=lab.strict_metrics(truth,data,.05)
    imperfect=truth.clone();imperfect[0,0]=1e-6
    bad=lab.strict_metrics(imperfect,data,.05)
    assert good['min_tier_joint5']==1
    assert bad['min_tier_joint5']==0
    assert bad['exact_zero_hits']==truth.numel()-1


def test_pairing_requires_all_masks_and_identifies_rescues():
    from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind
    rows=[{'tier':tier,'instance':tier+'-0'} for tier in blind.TIERS for _ in (0,1)]
    truth=torch.ones(len(rows),2)
    numeric={'rows':rows,'numeric':truth}
    v={k:truth.clone() for k in ('direct','gaussian','oracle_direct','oracle_gaussian','oracle_numeric')}
    v['gaussian'][0,0]=1.2  # one mask fails, both masks grouped
    metrics,pairs=p.paired_metrics(v,numeric,.05)
    assert metrics['direct']['min_tier_joint5']==1
    assert metrics['gaussian']['per_tier'][blind.TIERS[0]]==0
    assert pairs['direct_vs_gaussian']['rescued']==1
    assert pairs['direct_vs_gaussian']['regressed']==0


def test_output_replay_immutable_and_test_not_touched(tmp_path,monkeypatch):
    source,run,lab_dir,splits,domain=stub_completed_lab(tmp_path)
    watched=[source,run/'teacher_best.pt',lab_dir/'config.json']
    watched.extend(lab_dir/name/'best.pt' for name in p.ARMS)
    before={str(x):probe.sha256(x) for x in watched}
    # Reject any accidental access to TEST at evaluation time.
    real_eval=p.evaluate_arm
    def only_dev(arm,split,*other):
        assert len(split['rows'])==len(splits['dev']['rows'])
        return real_eval(arm,split,*other)
    monkeypatch.setattr(p,'evaluate_arm',only_dev)
    output=tmp_path/'report_only'
    report=p.run(cli(source,run,lab_dir,output))
    assert set(report['arms'])==set(p.ARMS)
    assert report['test_evaluated'] is False
    assert report['source_verified'] is True
    assert report['checkpoint_immutable'] is True
    assert (output/'report.json').exists()
    assert {str(x):probe.sha256(x) for x in watched}==before
    for name in p.ARMS:
        outcomes=report['arms'][name]['DEV']
        assert set(outcomes['paths'])=={'direct','gaussian','oracle_direct','oracle_gaussian','oracle_numeric'}
        assert p._replay_matches(outcomes['paths']['gaussian'],json.loads((lab_dir/name/'done.json').read_text())['DEV'])
    with pytest.raises(FileExistsError,match='REFUSES_OVERWRITE'):
        p.run(cli(source,run,lab_dir,output))


def test_refuse_drifted_best_identity(tmp_path):
    source,run,lab_dir,_,_=stub_completed_lab(tmp_path)
    path=lab_dir/'attention_fixed'/'best.pt'
    saved=torch.load(path,weights_only=True)
    saved['identity']['source_sha256']='wrong'
    torch.save(saved,path)
    with pytest.raises(RuntimeError,match='IDENTITY_DRIFT'):
        p.run(cli(source,run,lab_dir,tmp_path/'blocked'))
    assert not (tmp_path/'blocked').exists()


def test_true_location_gaussian_from_same_coordinates(tmp_path):
    source,run,lab_dir,splits,domain=stub_completed_lab(tmp_path)
    model,*_=probe.load_experiment(source,run)
    identity=json.loads((lab_dir/'config.json').read_text())
    arm,_,_=p._load_arm('attention_fixed',model,lab_dir,identity,torch.device('cpu'))
    data=splits['dev']
    comparison=p.evaluate_arm(arm,data,domain,13,.05)
    assert comparison['paths']['oracle_numeric']['min_tier_joint5']==1.
    # All true-location controls use labels, never the neural prediction.
    assert comparison['paths']['oracle_direct']['mean_abs_xy_error']<1.
    assert p._replay_matches(comparison['paths']['gaussian'],lab.evaluate_attention(arm,data,domain,13,.05))
