"""CPU controls for the non-destructive scale/alignment/cache/blind harness."""
import json
import math
from pathlib import Path

import pytest
import torch
from torch import nn
from tools import nanojev_qwen_hybrid_alignment_repair as fix
from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_qwen_zero_line_backbone as zero


def test_calibration_uses_dev_regression_only():
    head=hybrid.HybridLink(16)
    x=torch.randn(20,16)
    y=head(x).detach()*0.03
    y[-2:]*=1.2
    result=fix.calibration_summary(head,(x,y,torch.tensor([False]*18+[True]*2)))
    assert 0.02<result['gain']<0.04
    assert result['candidates'][result['selected']]['mse_ratio_to_zero']<0.1



def test_calibration_forwards_head_device_to_geometry(monkeypatch):
    """GPU heads and CPU-stored teacher pairs must not default to CPU LayerNorm."""
    from tools import nanojev_qwen_hybrid_latent_geometry as geom
    original=geom.measure_geometry
    observed=[]
    def geometry_on_head_device(head,pairs,**kwargs):
        assert 'device' in kwargs, 'Geometry must explicitly choose the head device'
        device=torch.device(kwargs['device'])
        assert device==next(head.parameters()).device
        observed.append(device)
        return original(head,pairs,**kwargs)
    monkeypatch.setattr(geom,'measure_geometry',geometry_on_head_device)
    head=hybrid.HybridLink(16)
    x=torch.randn(8,16)
    y=head(x).detach()*0.03
    result=fix.calibration_summary(head,(x.cpu(),y.cpu(),torch.tensor([False]*6+[True]*2)))
    assert len(observed)==1 and observed[0]==torch.device('cpu')
    assert 0<result['gain']<1


@pytest.mark.skipif(not torch.cuda.is_available(),reason='Requires CUDA for real cross-device regression')
def test_calibration_gpu_head_cpu_pairs():
    """Reproduces the user's FP32/CUDA LayerNorm and CPU input mismatch."""
    head=hybrid.HybridLink(16).to('cuda')
    x=torch.randn(8,16)
    with torch.inference_mode():
        y=head(x.to('cuda')).cpu()*0.03
    result=fix.calibration_summary(head,(x,y,torch.tensor([False]*6+[True]*2)))
    assert 0<result['gain']<1
    assert math.isfinite(result['candidates'][result['selected']]['mse'])


def test_scaled_head_does_not_change_inner_graph_and_backprops():
    head=hybrid.HybridLink(16)
    initial={k:v.clone() for k,v in head.state_dict().items()}
    net=fix.ScaledHead(head,0.031)
    out=net(torch.randn(4,16))
    assert net.gain()==pytest.approx(0.031,rel=1e-5)
    (out.square().mean()).backward()
    assert net.log_gain.grad is not None and net.log_gain.grad.abs().sum()>0
    assert net.head.proj1.weight.grad is not None
    assert all(torch.equal(v,initial[k]) for k,v in head.state_dict().items())
    with pytest.raises(AssertionError,match='ALIGN_REPAIR_SCALE_UNSAFE'):
        fix.ScaledHead(hybrid.HybridLink(16),31.0)


def test_alignment_loss_distinguishes_semantics_and_zero():
    class Echo(nn.Module):
        def __init__(self):
            super().__init__();self.dummy=nn.Parameter(torch.zeros(1))
        def forward(self,x):return x+self.dummy
    net=Echo()
    x=torch.eye(8)
    flag=torch.tensor([False]*6+[True]*2)
    good=fix.alignment_loss(net,x,x,flag)
    bad=fix.alignment_loss(net,x,torch.roll(x,1,0),flag)
    assert float(good['loss'].detach())<float(bad['loss'].detach())
    assert float(good['relative_mse'].detach())<float(bad['relative_mse'].detach())
    good['loss'].backward()
    assert net.dummy.grad is not None


def test_alignment_loss_duplicate_targets_have_no_false_negatives():
    head=hybrid.HybridLink(8)
    x=torch.randn(4,8)
    y=torch.ones_like(x)*0.05
    terms=fix.alignment_loss(head,x,y,torch.tensor([False]*3+[True]))
    assert float(terms['contrastive'])==0.0
    terms['loss'].backward()
    assert head.proj2.weight.grad is not None


def test_eval_metrics_require_nonexit_exit_and_finite():
    net=fix.ScaledHead(hybrid.HybridLink(8),0.04)
    x=torch.randn(6,8)
    y=torch.randn_like(x)*0.04
    summary=fix._metrics(net,(x,y,torch.tensor([False]*4+[True]*2)))
    assert summary['relative_mse_to_zero']>0
    assert math.isfinite(summary['median_norm_ratio'])
    with pytest.raises(AssertionError,match='ALIGN_REPAIR_MISSING_CLASSES'):
        fix._metrics(net,(x,y,torch.zeros(6,dtype=torch.bool)))


def test_controls_restore_head_after_exception(monkeypatch):
    from test_nanojev_qwen_hybrid_interface_repair import loader
    from tools import nanojev_qwen_hybridagent_paired_diagnostic as paired
    model,tok,_=loader()
    original=model.head
    def boom(*a,**kw):raise RuntimeError('CONTROL_ERROR')
    monkeypatch.setattr(paired,'decode_once',boom)
    with pytest.raises(RuntimeError,match='CONTROL_ERROR'):
        fix._decode(model,tok,torch.tensor([[65,66]]),'shuffled',3,torch.ones(1,16))
    assert model.head is original


def test_gate_rows_are_balanced_and_new_magnitudes_are_disjoint():
    cfg={'seed':71,'train_count':96,'dev_count':18,'test_count':18}
    rows=fix._gate_rows(cfg,18)
    assert len(rows)==18 and len(set(abs(x['a']) for x in rows))==18
    assert all(sum(r['sign']==s for r in rows)==6 for s in ('negative','zero','positive'))
    assert all(sum(r['family']==f for r in rows)==9 for f in ('direct','composed'))
    with pytest.raises(AssertionError,match='ALIGN_REPAIR_INVALID_CASES'):
        fix._gate_rows(cfg,5)


def test_refuses_overlapping_output_with_source(tmp_path):
    with pytest.raises(AssertionError,match='ALIGN_REPAIR_SOURCE_OUTPUT_OVERLAP'):
        fix._verify_output_root(tmp_path/'interface'/'child',tmp_path/'a',tmp_path/'b',tmp_path/'interface')


def test_cache_consistency_on_mock_backbone():
    from test_nanojev_qwen_hybrid_interface_repair import loader
    model,tok,_=loader()
    head=fix.ScaledHead(model.head,0.029)
    row=zero.make_cases(71,6,6,6)['test'][:2]
    r=fix.cache_equivalence(model,tok,row,device='cpu',head=head)
    assert r['pass'] and r['checks']==2 and r['max_relative_rmse']<1e-6
    assert all(x['argmax_equal'] for x in r['records'])


def test_blind_gate_reports_scores_without_oracle_in_inputs():
    from test_nanojev_qwen_hybrid_interface_repair import loader
    model,tok,_=loader()
    head=fix.ScaledHead(model.head,0.03)
    rows=zero.make_cases(71,6,6,6)['test']
    r=fix.behavioural_gate(model,tok,head,rows,device='cpu',budget=2)
    assert len(r['records'])==6
    assert set(r['score'])=={'text','marker_only','zero','shuffled','intact'}
    assert model.head is not head
    for entry in r['records']:
        assert set(entry['variants'])==set(r['score'])
        assert all(v['tokens']<=2 for v in entry['variants'].values())


def test_end_to_end_train_saves_new_checkpoint_without_modifying_sources(tmp_path,monkeypatch):
    from test_nanojev_qwen_hybrid_interface_repair import sources,loader
    from tools import nanojev_qwen_hybrid_interface_repair as prior
    from tools import nanojev_qwen_hybrid_latent_geometry as geom
    def small_loader(**kwargs):
        model,tok,det=loader(**kwargs)
        with torch.no_grad():
            model.backbone.get_input_embeddings().weight.mul_(0.03)
        return model,tok,det
    base,numeric,oldhead=sources(tmp_path,monkeypatch)
    interface=tmp_path/'interface'
    prior.train_format(device='cpu',base=base,numerical=numeric,initial_head=oldhead,
       output=interface,steps=1,eval_every=1,train_count=6,dev_count=6,test_count=6,
       max_seq=512,loader=small_loader)
    prior.realign(device='cpu',base=base,numerical=numeric,output=interface,
       steps=1,eval_every=1,max_seq=512,loader=small_loader)
    hashes={'format':prior.fingerprint(interface/'format'/'best.pt'),
            'head':prior.fingerprint(interface/'align'/'best.pt')}
    zero.atomic_json(interface/'gate_result.json',{'schema':prior.SCHEMA,
       'marker_checkpoint_roundtrip':True,'tied_output_marker_override':True,'markers_pass':True,
       'format_checkpoint_sha256':hashes['format'],'aligned_head_sha256':hashes['head']})
    root=tmp_path/'repair'
    assert fix.run(stage='calibrate',device='cpu',base=base,numerical=numeric,
       interface_dir=interface,output=root,loader=small_loader)==0
    assert fix.run(stage='train',device='cpu',base=base,numerical=numeric,
       interface_dir=interface,output=root,loader=small_loader,steps=2,eval_every=1)==0
    assert (root/'alignment'/'best.pt').is_file()
    assert prior.fingerprint(interface/'format'/'best.pt')==hashes['format']
    assert prior.fingerprint(interface/'align'/'best.pt')==hashes['head']
    with pytest.raises(AssertionError,match='ALIGN_REPAIR_TRAIN_ALREADY_EXISTS'):
        fix.run(stage='train',device='cpu',base=base,numerical=numeric,
            interface_dir=interface,output=root,loader=small_loader,steps=2,eval_every=1)
    alt=tmp_path/'restarted';alt.mkdir()
    (alt/'calibration.json').write_bytes((root/'calibration.json').read_bytes())
    assert fix.run(stage='train',device='cpu',base=base,numerical=numeric,
        interface_dir=interface,output=alt,loader=small_loader,steps=2,eval_every=1,
        stop_after=1)==0
    assert json.loads((alt/'alignment'/'result.json').read_text())['status']=='PAUSED_RESUMABLE'
    assert fix.run(stage='train',device='cpu',base=base,numerical=numeric,
        interface_dir=interface,output=alt,loader=small_loader,steps=2,eval_every=1,
        resume=True)==0
    straight=torch.load(root/'alignment'/'last.pt',map_location='cpu',weights_only=True)
    restarted=torch.load(alt/'alignment'/'last.pt',map_location='cpu',weights_only=True)
    assert straight['step']==restarted['step']==2
    for key in straight['state']:
        assert torch.equal(straight['state'][key],restarted['state'][key])
    with pytest.raises(AssertionError,match='GEOMETRY_GATE_CHECKPOINT_MISMATCH'):
        (interface/'align'/'best.pt').write_bytes(b'corrupt')
        fix.run(stage='recurrence',device='cpu',base=base,numerical=numeric,
            interface_dir=interface,output=root,loader=small_loader)
