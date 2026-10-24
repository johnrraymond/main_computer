"""Read-only probes of saved, post-training HybridLink v1 states."""
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_hawk_dove_hybridlink_boundary_probe as diag
from tools import nanojev_hawk_dove_hybridlink_sequence as hy
from tests.test_nanojev_hawk_dove_hybridlink_sequence import options, setup


def _train_tiny(tmp):
    source,run,net,splits=setup(tmp)
    out=tmp/'saved_run'
    hy.run(options(source,run,out))
    return source,out,splits


def _probe_args(source,run_dir,**kwargs):
    args=['--reference-checkpoint',str(source),'--run-dir',str(run_dir),'--device','cpu',
          '--batch-size','16','--cpu-threads','1']
    for k,v in kwargs.items():
        args.extend(['--'+k.replace('_','-'),str(v)])
    return diag.parser().parse_args(args)


def test_readonly_stage_diagnostics_and_checkpoint_integrity(tmp_path):
    source,run_dir,splits=_train_tiny(tmp_path)
    files=[source,run_dir/'config.json']+[run_dir/arm/(kind+'.pt')
                                        for arm in hy.ARMS for kind in diag.CHECKPOINTS]
    before=[hy.file_sha256(p) for p in files]
    result=diag.run(_probe_args(source,run_dir))
    assert result['provenance']['split']=='DEV'
    assert result['provenance']['TEST_evaluated'] is False
    assert [hy.file_sha256(p) for p in files]==before
    for arm in hy.ARMS:
        for k in diag.CHECKPOINTS:
            state=result['states'][arm][k]
            assert set(state['DEV_paths'])==set(diag.PATHS)
            assert len(state['alignment_probe']['per_step']['autonomous'])==4
            assert 'next_product_from_preceding_state' in state['format_probe']
            assert state['checkpoint']['historical_format_boundary_weights'] is False
            assert state['checkpoint']['historical_alignment_boundary_weights'] is False
            assert len(state['paired_teacher_vs_other_paths']['autonomous_generated'])==len(hy.blind.TIERS)
    assert (run_dir/'boundary_probe_v1'/'report.json').exists()
    assert result['states']['recurrent']['latest']['checkpoint']['saved_stage']=='joint'
    with pytest.raises(FileExistsError,match='OUTPUT_EXISTS'):
        diag.run(_probe_args(source,run_dir))


def test_exact_pairing_and_no_mask_shortcut():
    rows=[{'tier':tier,'instance':tier+'one','mask':k} for tier in hy.blind.TIERS for k in (0,1)]
    split={'rows':rows,'numeric':torch.ones(len(rows),2)}
    correct=split['numeric'].clone();alt=correct.clone()
    for i in range(0,len(rows),2):alt[i,0]=0.
    result=diag._paired_control(correct,alt,split,.05)
    assert all(result[tier]['rescued']==1 for tier in hy.blind.TIERS)
    assert all(result[tier]['regressed']==0 for tier in hy.blind.TIERS)
    assert all(result[tier]['accuracy_gain']==1. for tier in hy.blind.TIERS)


def test_missing_or_tampered_checkpoint_refused(tmp_path):
    source,run_dir,_=_train_tiny(tmp_path)
    args=_probe_args(source,run_dir,arms='recurrent',checkpoints='best')
    good=run_dir/'recurrent'/'best.pt'
    state=torch.load(good,weights_only=True)
    state['arm']='static'
    torch.save(state,good)
    with pytest.raises(RuntimeError,match='CHECKPOINT_IDENTITY_DRIFT'):
        diag.run(args)
    good.unlink()
    with pytest.raises(RuntimeError,match='CHECKPOINT_MISSING'):
        diag.run(args)


def test_generated_thoughts_not_oracle_in_autonomous_path(tmp_path,monkeypatch):
    source,run_dir,splits=_train_tiny(tmp_path)
    reference,_,_,_=hy.load_source(source,'cpu')
    model=hy.HybridSequence(reference)
    saved=torch.load(run_dir/'recurrent'/'best.pt',weights_only=True)
    model.load_state_dict(saved['model'])
    x=splits['dev']['features'][:8]
    expected=model.rollout(x,4,'recurrent')
    monkeypatch.setattr(model,'teacher_tokens',lambda _: (_ for _ in ()).throw(AssertionError('oracle')))
    assert torch.equal(model.rollout(x,4,'recurrent'),expected)
    with pytest.raises(AssertionError,match='oracle'):
        diag.diagnose_state(model,splits['dev'],2,'recurrent',8,.05)


def test_source_identity_and_selection_reject(tmp_path):
    source,run_dir,_=_train_tiny(tmp_path)
    args=_probe_args(source,run_dir,arms='bad')
    with pytest.raises(ValueError,match='INVALID_SELECTION'):diag.run(args)
    c=run_dir/'config.json'
    ident=json.loads(c.read_text());ident['source_sha256']='0'*64
    c.write_text(json.dumps(ident),encoding='utf-8')
    with pytest.raises(RuntimeError,match='SOURCE_IDENTITY_DRIFT'):
        diag.run(_probe_args(source,run_dir,arms='recurrent',checkpoints='best'))


def test_alignment_metrics_well_formed():
    u=torch.tensor([[1.,0.],[0.,1.]])
    out=diag._vector_alignment(u,u)
    assert out['mean_squared_error']==0.
    assert out['mean_cosine']==pytest.approx(1.)
    got=diag._product_metrics(torch.tensor([[1.,2.,3.,4.]]),torch.tensor([[1.,2.,3.,4.]]))
    assert got['normalized_product_mae']==0.
    assert got['normalized_product_R2']==1.
