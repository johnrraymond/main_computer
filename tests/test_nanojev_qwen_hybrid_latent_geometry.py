"""CPU-only, no-download proofs that the HybridLink geometry probe is read-only."""
import json
from pathlib import Path

import pytest
import torch
from torch import nn

from tools import nanojev_qwen_hybrid_latent_geometry as geo
from tools import nanojev_qwen_hybrid_interface_repair as repair
from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_qwen_hybridagent_paired_diagnostic as paired


def test_vector_metrics_capture_true_scale_disparity():
    head=hybrid.HybridLink(16)
    x=torch.randn(7,16)
    with torch.no_grad():
        pred=head(x)
    y=pred*0.1
    flags=torch.tensor([False,False,False,False,False,True,True])
    summary=geo.measure_geometry(head,(x,y,flags))
    assert summary['all']['norm_ratio']['median']==pytest.approx(10,rel=1e-5)
    assert summary['all']['cosine_to_target']['mean']==pytest.approx(1,abs=1e-6)
    assert summary['calibration']['median_norm_scale']==pytest.approx(0.1,rel=1e-5)
    assert summary['calibration']['scaled_mse']<1e-11
    assert summary['calibration']['raw_mse']>0.01
    assert summary['exit']['count']==2 and summary['non_exit']['count']==5


def test_distribution_mismatch_not_automatically_geometry_certification():
    head=hybrid.HybridLink(8)
    x=torch.randn(8,8)
    y=torch.randn_like(x)*0.2
    s=geo.measure_geometry(head,(x,y,torch.tensor([True]*4+[False]*4)))
    assert s['all']['direction_only_mse']>0
    assert s['calibration']['raw_mse']>=0
    assert s['calibration']['scaled_mse']>=0


def test_geometry_rejects_corrupt_or_empty_pairs():
    h=hybrid.HybridLink(8)
    with pytest.raises(AssertionError,match='GEOMETRY_PAIR_SHAPE'):
        geo.measure_geometry(h,(torch.ones(0,8),torch.ones(0,8),torch.zeros(0,dtype=torch.bool)))
    with pytest.raises(AssertionError,match='GEOMETRY_NONFINITE_PAIRS'):
        geo.measure_geometry(h,(torch.full((2,8),float('nan')),torch.ones(2,8),torch.tensor([0,1],dtype=torch.bool)))
    with pytest.raises(AssertionError,match='GEOMETRY_EXIT_MASK'):
        geo.measure_geometry(h,(torch.ones(2,8),torch.ones(2,8),torch.tensor([0,1])))


def test_substitute_head_modes_keep_original_weights():
    h=hybrid.HybridLink(8)
    h.eval()
    x=torch.randn(1,8)
    old={k:v.detach().clone() for k,v in h.state_dict().items()}
    p=h(x)
    for mode,expected in [('zero',torch.zeros_like(p)),('scale_0p1',p*0.1),
                          ('norm_match',p*0.4),('native_argmax',torch.ones_like(p))]:
        wrapper=geo.SubstituteHead(h,mode,calibration=0.4,native=torch.ones_like(p))
        assert torch.allclose(wrapper(x),expected)
    assert all(torch.equal(h.state_dict()[k],v) for k,v in old.items())


def test_substitute_head_invalid_controls_fail_closed():
    h=hybrid.HybridLink(8)
    with pytest.raises(AssertionError,match='GEOMETRY_BAD_MODE'):geo.SubstituteHead(h,'teacher')
    with pytest.raises(AssertionError,match='GEOMETRY_BAD_CALIBRATION'):geo.SubstituteHead(h,'norm_match',calibration=-1)
    with pytest.raises(AssertionError,match='GEOMETRY_BAD_NATIVE_REFERENCE'):geo.SubstituteHead(h,'native_argmax')


def test_next_token_is_native_model_prediction_not_teacher():
    from test_nanojev_qwen_hybrid_interface_repair import loader
    m,t,_=loader()
    ids=torch.tensor([[65,66,67]])
    row,token=geo.native_next_embedding(m,ids)
    assert row.shape==(1,16)
    assert torch.equal(row,m.backbone.get_input_embeddings()(torch.tensor([token])).float())
    assert row.requires_grad is False


def test_marker_only_comparison_preserves_latent_count_and_head():
    from test_nanojev_qwen_hybrid_interface_repair import loader
    m,t,_=loader()
    ref=m.head
    res=geo.decode_intervention(m,t,torch.tensor([[65,66]]),'marker_only',budget=3,calibration=1)
    assert res['latents']==0 and m.head is ref
    assert res['tokens']<=3


def test_interventions_restore_original_head_even_after_exception(monkeypatch):
    from test_nanojev_qwen_hybrid_interface_repair import loader
    m,t,_=loader()
    ref=m.head
    def bad(*args,**kwargs):raise RuntimeError('injected')
    monkeypatch.setattr(paired,'decode_once',bad)
    with pytest.raises(RuntimeError,match='injected'):
        geo.decode_intervention(m,t,torch.tensor([[65,66]]),'norm_match',budget=3,calibration=1)
    assert m.head is ref


def test_vocabulary_sample_matches_true_embedding_magnitudes():
    from test_nanojev_qwen_hybrid_interface_repair import loader
    m,_,_=loader()
    r=geo.sample_vocabulary_norms(m,sample_size=24)
    assert r['sample_size']==24 and r['marker_input_norms']['entry']>0
    assert r['sampled_original_token_norm']['count']==24


def test_marker_gate_preflight_refuses_unpatched_restore(tmp_path):
    # This diagnostic must not accidentally measure the previously broken marker gate.
    root=tmp_path/'interface';(root/'align').mkdir(parents=True)
    (root/'format').mkdir(parents=True)
    (root/'format'/'best.pt').write_bytes(b'f')
    (root/'align'/'best.pt').write_bytes(b'h')
    geo.repair.zero.atomic_json(root/'gate_result.json',{'schema':repair.SCHEMA,
        'marker_checkpoint_roundtrip':False,'tied_output_marker_override':False})
    geo.repair.zero.atomic_json(root/'align'/'config.json',{'schema':repair.SCHEMA})
    geo.repair.zero.atomic_json(root/'align'/'result.json',{'status':'HEAD_REALIGNED_UNCERTIFIED'})
    with pytest.raises(AssertionError,match='GEOMETRY_REQUIRES_CORRECTED_MARKER_GATE'):
        geo._preflight(root)


def test_marker_gate_preflight_refuses_checkpoint_change(tmp_path):
    root=tmp_path/'interface';(root/'align').mkdir(parents=True)
    (root/'format').mkdir(parents=True)
    (root/'format'/'best.pt').write_bytes(b'f')
    (root/'align'/'best.pt').write_bytes(b'h')
    geo.repair.zero.atomic_json(root/'gate_result.json',{'schema':repair.SCHEMA,
        'marker_checkpoint_roundtrip':True,'tied_output_marker_override':True,'markers_pass':True,
        'format_checkpoint_sha256':'wrong','aligned_head_sha256':'wrong'})
    geo.repair.zero.atomic_json(root/'align'/'config.json',{'schema':repair.SCHEMA,
        'format_checkpoint_sha256':'wrong'})
    geo.repair.zero.atomic_json(root/'align'/'result.json',{'status':'HEAD_REALIGNED_UNCERTIFIED'})
    with pytest.raises(AssertionError,match='GEOMETRY_GATE_CHECKPOINT_MISMATCH'):
        geo._preflight(root)


def test_do_not_overwrite_output_or_nest_inside_checkpoints(tmp_path):
    source=tmp_path/'sources';source.mkdir()
    output=tmp_path/'out';output.mkdir();(output/'sentinel').write_text('x')
    with pytest.raises(AssertionError,match='GEOMETRY_OUTPUT_EXISTS_REFUSED'):
        geo.run(interface=source,base=tmp_path/'base',numerical=tmp_path/'numeric',output=output)
    assert (output/'sentinel').read_text()=='x'
    with pytest.raises(AssertionError,match='GEOMETRY_OUTPUT_SOURCE_OVERLAP'):
        geo.run(interface=source,base=tmp_path/'base',numerical=tmp_path/'numeric',output=source/'clobber')


def test_describe_guards_nonfinite():
    with pytest.raises(AssertionError,match='GEOMETRY_INVALID_VECTOR_STATISTICS'):
        geo.describe([1,float('inf')])
    with pytest.raises(AssertionError,match='GEOMETRY_INVALID_VECTOR_STATISTICS'):
        geo.describe([])


def test_end_to_end_cpu_read_only_diagnostic_against_versioned_checkpoint(tmp_path,monkeypatch):
    from test_nanojev_qwen_hybrid_interface_repair import sources,loader
    base,numerical,initial_head=sources(tmp_path,monkeypatch)
    interface=tmp_path/'interface'
    repair.train_format(device='cpu',base=base,numerical=numerical,initial_head=initial_head,
        output=interface,steps=1,eval_every=1,train_count=6,dev_count=6,test_count=6,
        max_seq=512,loader=loader)
    repair.realign(device='cpu',base=base,numerical=numerical,output=interface,
        steps=1,eval_every=1,max_seq=512,loader=loader)
    hashes={'format':repair.fingerprint(interface/'format'/'best.pt'),
            'head':repair.fingerprint(interface/'align'/'best.pt')}
    zero=repair.zero
    zero.atomic_json(interface/'gate_result.json',{
        'schema':repair.SCHEMA,'marker_checkpoint_roundtrip':True,
        'tied_output_marker_override':True,'markers_pass':True,
        'format_checkpoint_sha256':hashes['format'],'aligned_head_sha256':hashes['head']})
    location=tmp_path/'diagnostic'
    r=geo.run(device='cpu',base=base,numerical=numerical,interface=interface,
        output=location,cases=6,budget=2,loader=loader)
    assert r['source_mutations']==0 and r['optimizer_steps']==0
    assert r['teacher_forcing_geometry_only']
    assert r['oracle_latents_supplied_at_inference']==0
    assert r['calibration_source']=='dev_only'
    assert r['deeper_training_permitted'] is False
    assert len(r['records'])==6
    assert all(set(row['variants'])==set(geo.CONDITIONS) for row in r['records'])
    assert r['score']['intact']['cases']==6 and r['score']['native_argmax']['cases']==6
    assert repair.fingerprint(interface/'format'/'best.pt')==hashes['format']
    assert repair.fingerprint(interface/'align'/'best.pt')==hashes['head']
    assert json.loads((location/'result.json').read_text())['schema']==geo.SCHEMA
    with pytest.raises(AssertionError,match='GEOMETRY_OUTPUT_EXISTS_REFUSED'):
        geo.run(device='cpu',base=base,numerical=numerical,interface=interface,
                output=location,cases=6,budget=2,loader=loader)
