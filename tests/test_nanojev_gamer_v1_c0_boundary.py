"""Read-only exact-oracle C0 boundary and causal activation diagnostic regression."""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_c0_boundary as c0
from tools import nanojev_gamer_v1_sign_information_probe as oracle
from tools import nanojev_gamer_v1_staircase as staircase


def test_complete_fraction_oracle_triples_and_group_isolation():
    deck=oracle.make_deck(2,2,2,include_ood=True)
    rows=[x.public() for x in deck]
    triples,groups=c0._triples(rows)
    assert len(groups['train'])==4 and len(groups['dev'])==4
    assert len(groups['test'])==16  # 2 held-out offset groups + 6 unseen hawk groups, each 2 surfaces
    assert all(set(triples[k])=={-1,0,1} for k in triples)
    missing=[r for r in rows if r['id']!='test.offset.0000.zero.source_native']
    with pytest.raises(ValueError,match='INCOMPLETE_ORACLE_TRIPLE'):
        c0._triples(missing)
    leaked=[dict(r) for r in rows]
    leaked[0]['split']='test'
    with pytest.raises(ValueError,match='LEAKED_GROUP'):
        c0._triples(leaked)
    duplicate=rows+[rows[0]]
    with pytest.raises(ValueError,match='DUPLICATE_SIGN'):
        c0._triples(duplicate)


def test_baseline_parity_fails_closed():
    assert c0._verify_baseline(np.array([-.02,0.,.34]),np.array([-.02,0.,.34]))['status'].startswith('EXACT_')
    with pytest.raises(ValueError,match='REPLAY_PARITY_FAILED'):
        c0._verify_baseline(np.array([-.4]),np.array([+.4]))
    with pytest.raises(ValueError,match='SHAPE'):
        c0._verify_baseline(np.array([1]),np.array([1.,2.]))


def _synthetic_math_features():
    deck=oracle.make_deck(3,2,2,include_ood=True)
    rows=[v.public() for v in deck]
    x=np.zeros((len(rows),102400),dtype=np.float32)
    for i,row in enumerate(rows):
        sign=int(np.sign(float(Fraction(row['target']))))
        # Each group has a different signed direction even within one token arm.
        group_number=int(row['group'].split('.')[-1])
        for token in (1,2,3):
            x[i,token*1024]=(sign*(token+1))
            x[i,token*1024+1]=(sign*(group_number+1)/(token+1))
        # Independent stable group context in other dimensions.
        x[i,16]=group_number*.5
    return rows,x


def test_axes_train_only_grouped_and_equal_norm_controls():
    rows,x=_synthetic_math_features();triples,groups=c0._triples(rows)
    arms={'localized':[1],'neighbor_control':[2],'random_control':[3],'full100':None}
    axes=c0._axes(x,triples,groups,arms,controls=3)
    assert set(axes)==set(arms)
    for arm,info in axes.items():
        assert info['train_groups']==3
        assert len(info['nulls'])==3
        assert info['step']>0
        assert np.isclose(np.linalg.norm(info['axis']),1.)
        assert all(np.isclose(np.linalg.norm(n),1.) for n in info['nulls'])
        assert all(abs(float(info['axis']@n))<.98 for n in info['nulls'])
    for j in [i for i,row in enumerate(rows) if row['split']=='test']:
        x[j]=np.random.default_rng(j).normal(0,3,size=x.shape[1])
    same=c0._axes(x,triples,groups,arms,controls=3)
    assert all(np.array_equal(axes[a]['axis'],same[a]['axis']) for a in arms)
    assert all(axes[a]['step']==same[a]['step'] for a in arms)
    bad=dict(arms,localized=[100])
    with pytest.raises(ValueError,match='INVALID_ARM_POSITIONS'):
        c0._axes(x,triples,groups,bad)


def test_causal_response_metrics_distinguish_math_from_synthetic_activation():
    deck=oracle.make_deck(2,2,2,include_ood=False)
    rows=[x.public() for x in deck];triples,groups=c0._triples(rows)
    baseline=np.array([float(Fraction(r['target'])) for r in rows])
    plus=baseline+.3;minus=baseline-.3
    result=c0._response_metrics(rows,triples,groups,baseline,plus,minus)['signed_offset']
    assert result['triples']==4
    assert result['both_signed_interventions_expected_direction']==1.
    assert result['baseline_negative_correct']==1.
    assert result['baseline_positive_correct']==1.
    assert result['median_zero_antisymmetry_error']==pytest.approx(0.)


def test_real_torch_head_layer_path_and_deterministic_readout():
    import torch
    from torch import nn
    class Tiny(nn.Module):
        def __init__(self):
            super().__init__()
            self.norm=nn.LayerNorm(6)
            self.enc=nn.Sequential(nn.Linear(6,4),nn.GELU())
            self.mid=nn.Sequential(nn.Linear(4,3),nn.GELU())
            self.corrected=nn.Linear(3,1)
    torch.manual_seed(13)
    head=Tiny().eval()
    for p in head.parameters():p.requires_grad_(False)
    x=np.arange(30,dtype=np.float32).reshape(5,6)/13
    predictions,layers=c0._forward(head,x,device='cpu',batch_size=2,stages=True)
    assert predictions.shape==(5,)
    assert layers['c0_norm'].shape==(5,6)
    assert layers['c0_encoder'].shape==(5,4)
    assert layers['c0_mid'].shape==(5,3)
    same,_=c0._forward(head,x,device='cpu',batch_size=5,stages=False)
    assert np.allclose(predictions,same,atol=1e-6)
    with pytest.raises(ValueError,match='BAD_BOUNDARY_FEATURES'):
        c0._forward(head,x[:,:5],device='cpu')


def test_cli_boundary_flags_restricted_and_no_steps():
    args=staircase.parser().parse_args(['head-100-c0-boundary','--run','upstream','--device','cpu'])
    assert args.boundary_controls==3
    assert args.boundary_batch==8
    with pytest.raises(ValueError,match='READ_ONLY_NO_STEPS'):
        staircase.main(['head-100-c0-boundary','--run','upstream','--steps','1'])
    with pytest.raises(ValueError,match='FLAGS_ONLY_BOUNDARY'):
        staircase.main(['head-100-upstream-path','--run','somewhere','--boundary-controls','4'])


def test_source_resolution_refuses_unverified_upstream(tmp_path):
    p=tmp_path/'u';p.mkdir()
    (p/'final_report.json').write_text(json.dumps({'schema':'other','status':'COMPLETE'}))
    with pytest.raises(ValueError,match='COMPLETED_UPSTREAM_ORACLE'):
        c0._source(p)
    with pytest.raises(ValueError,match='OVERLAPS'):
        c0._destination(p,p/'child')
    with pytest.raises(FileExistsError,match='ALREADY_EXISTS'):
        c0._destination(p,p)


def test_end_to_end_mocked_frozen_boundary_report_without_mutating_inputs(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_sufficiency as suff
    import hashlib
    rows,x=_synthetic_math_features()
    root=tmp_path/'upstream';root.mkdir()
    source=tmp_path/'sign';source.mkdir()
    capture=root/'oracle-capture';capture.mkdir()
    checkpoint=tmp_path/'best_model.pt';checkpoint.write_bytes(b'FROZEN_CHECKPOINT')
    (root/'final_report.json').write_text('{"source":"frozen"}')
    arms={'localized':[1],'neighbor_control':[2],'random_control':[3],'full100':None}
    (source/'final_report.json').write_text(json.dumps({'arms':arms}))
    (capture/'frozen_features.npz').write_bytes(b'FAKE_CAPTURE_UNCHANGED')
    analysis={'production_head_test':{'balanced_sign_accuracy':.4},
              'development_selected_readouts':{'lora':{'feature':'layer_09_mean100','test':{'balanced_sign_accuracy':.6}}}}
    (capture/'analysis.json').write_text(json.dumps(analysis))
    base=lambda features:np.asarray(features[:,1024]*.25+.1,dtype=np.float32)
    prediction=base(x)
    meta={'dataset':rows,'dataset_sha256':'frozen-deck','lineage':{
        'checkpoint_path':str(checkpoint),'checkpoint_sha256':suff._digest(checkpoint),
        'adapter_sha256':'FROZEN_ADAPTER'}}
    source_report={'source_expanded':str(tmp_path/'exp'),
        'sign_only_source':str(source),'oracle_analysis_sha256':suff._digest(capture/'analysis.json'),
        'source_sign_only_sha256':suff._digest(source/'final_report.json')}
    arrays={'lora__final_flat':x,'production_head_predictions':prediction}
    monkeypatch.setattr(c0,'_source',lambda *_:(root,source_report,meta,arrays,capture))
    monkeypatch.setattr(c0,'_load_head',lambda *a,**kw:object())
    from tools import nanojev_two_game_spine_smoke as spine
    monkeypatch.setattr(spine,'adapter_identity',lambda *a:'FROZEN_ADAPTER')
    meta['lineage']['adapter_dir']=str(tmp_path/'adapter')
    def fake_forward(net,features,**kwargs):
        p=base(features)
        if not kwargs.get('stages'):return p,None
        return p,{'c0_norm':features[:,:6], 'c0_encoder':features[:,:4],
                  'c0_mid':features[:,:2]}
    monkeypatch.setattr(c0,'_forward',fake_forward)
    monkeypatch.setattr(c0,'_measured_readouts',lambda *a:{'c0_mid':{
        'test':{'balanced_sign_accuracy':0.5,'triple_accuracy':0.0}}})
    before={p:suff._digest(p) for p in (checkpoint,source/'final_report.json',
              capture/'analysis.json',capture/'frozen_features.npz')}
    report=c0.run_boundary(root,device='cpu',controls=2)
    assert report['status']=='COMPLETE_READ_ONLY_NO_CAUSAL_CERTIFICATION'
    assert report['baseline_replay_parity']['max_abs_delta']==0.0
    assert report['no_production_training_or_weight_update'] is True
    assert report['test_groups']==8
    assert len(report['causal_boundary_interventions'])==4
    assert report['real_oracle_endpoints_and_synthetic_interpolations']['signed_offset']['triples']==4
    assert all(len(a['null_responses'])==2 for a in report['causal_boundary_interventions'].values())
    assert before=={p:suff._digest(p) for p in before}
    output=Path(report['source_upstream_report']).parent.with_name(root.name+'-c0-boundary-0001')
    assert (output/'final_report.json').is_file()
    with pytest.raises(FileExistsError,match='ALREADY_EXISTS'):
        c0._destination(root,output)
