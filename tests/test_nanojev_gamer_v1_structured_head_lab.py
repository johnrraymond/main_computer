"""Frozen 100-vector structured numerical decoder: bounded matched experiment."""
from pathlib import Path
from types import SimpleNamespace
import json
import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_structured_head_lab as structured
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_sign_information_probe as probe


def _flat(values, *, dtype=np.float16):
    value=np.asarray(values,dtype=np.float32)
    # 100 independent token vectors, distinguish last token from mean.
    x=np.zeros((len(value),100,1024),dtype=np.float32)
    x[:,:,0]=value[:,None]
    x[:,-1,1]=value
    x[:,0,2]=1.
    return x.reshape(len(value),102400).astype(dtype)


def _archive_profiles(flat):
    views=structured._views_from_flat(flat)
    return {'lora__final_flat':flat,
            'lora__final_last':views['final_last'].astype(flat.dtype),
            'lora__final_mean100':views['final_mean100'].astype(flat.dtype)}


def _prepared_mock():
    deck=probe.make_deck(4,3,3,False)
    rows=[r.public() for r in deck]
    p=probe._test_partitions(rows)
    y=np.asarray([float(r.target) for r in deck])
    # Existing head fails sign reversal. No synthetic fit may touch dev/test.
    old=np.full(len(y),0.1,dtype=np.float64)
    source_y=np.tile([-2.,0.,2.],20).astype(np.float64)
    src_old=source_y.copy()
    src_flat=_flat(source_y,dtype=np.float32)
    dev_y=np.tile([-2.,0.,2.],10).astype(np.float64)
    dev_old=dev_y+np.where(dev_y<0,-.30,np.where(dev_y>0,.30,0.))
    dev_flat=_flat(dev_y,dtype=np.float16)
    syn_flat=_flat(y,dtype=np.float16)
    original_rows=[{'id':str(i),'group':str(i),'surface':'source',
                    'family':'original','target':str(t)} for i,t in enumerate(dev_y)]
    data={'original_train_features':src_flat,
          'original_train':list(range(len(src_flat))),
          'retention_arrays':_archive_profiles(dev_flat),
          'probe_arrays':{**_archive_profiles(syn_flat),'production_head_predictions':old},
          'retention_info':{'dataset':original_rows,'dataset_sha256':'dev-hash'},
          'probe_info':{'dataset':rows,'dataset_sha256':'syn-hash',
                        'lineage':{'checkpoint_sha256':'frozen',
                                   'adapter_sha256':'lora',
                                   'source_run':'source','expanded_run':'expanded'}},
          'train_cache':{'sha256':'train-hash'},'partitions':p}
    prepared={}
    src_views=structured._views_from_flat(src_flat)
    dev_views=structured._views_from_flat(dev_flat)
    syn_views=structured._views_from_flat(syn_flat)
    for name in ('final_last','final_mean100'):
        # v3 cached single-vector views are cast to float32 from captured dtype.
        src=src_views[name]
        dev=dev_views[name].astype(np.float16).astype(np.float32)
        syn=syn_views[name].astype(np.float16).astype(np.float32)
        prepared[name]={
            'train_source':(src,src_old,source_y),
            'train_synthetic':(syn[p['train']],old[p['train']],y[p['train']],
                               [rows[int(i)] for i in p['train']]),
            'dev_source':(original_rows,dev_y,dev_old,dev),
            'dev_synthetic':(rows,y,old,syn[p['dev']],p['dev']),
            'test_synthetic':(rows,y,old,syn[p['test']],p['test'])}
    return data,prepared


def test_oracle_no_training(capsys):
    assert structured.main([])==0
    out=json.loads(capsys.readouterr().out)
    assert out['status']=='ORACLE_ONLY'
    assert out['synthetic']['cases']==612
    assert out['candidate_configs']==3
    assert out['original_100_vector_head_interface']
    assert out['no_model_training']


@pytest.mark.parametrize('dtype',[np.float16,np.float32])
def test_extraction_is_deterministic_function_of_original_head_flat_input(dtype):
    f=_flat([-.5,0.,.5,3.],dtype=dtype)
    views=structured._views_from_flat(f)
    assert views['final_last'].shape==(4,1024)
    assert views['final_mean100'].shape==(4,1024)
    assert views['structured_last_mean100'].shape==(4,2048)
    assert np.array_equal(views['structured_last_mean100'][:,:1024],views['final_last'])
    assert np.array_equal(views['structured_last_mean100'][:,1024:],views['final_mean100'])
    assert np.array_equal(views['final_last'][:,0],np.asarray([-.5,0.,.5,3.]))
    assert views['final_mean100'][0,1]==pytest.approx(-.005,abs=1e-6)
    assert views['final_last'][0,1]<0
    structured._strict_capture_parity(views,_archive_profiles(f),'synthetic')


def test_bad_width_dtype_nonfinite_and_archive_modification_refused():
    with pytest.raises(ValueError,match='100_VECTOR'):
        structured._views_from_flat(np.zeros((2,102399),dtype=np.float32))
    with pytest.raises(ValueError,match='100_VECTOR'):
        structured._views_from_flat(np.zeros((2,102400),dtype=np.float64))
    bad=np.zeros((1,102400),dtype=np.float32);bad[0,7]=np.nan
    with pytest.raises(ValueError,match='NONFINITE'):
        structured._views_from_flat(bad)
    f=_flat([-.25,0,.25])
    profiles=_archive_profiles(f)
    profiles['lora__final_mean100'][0,0]+=np.float16(1.0)
    with pytest.raises(ValueError,match='CAPTURE_PARITY_FAILED'):
        structured._strict_capture_parity(structured._views_from_flat(f),profiles,'synthetic')


def test_batched_float32_mean_is_not_substituted_for_capture_semantics():
    f=np.zeros((3,102400),dtype=np.float32)
    # Real Qwen input can have very different element magnitudes. Per-case
    # capture reduction is the authoritative policy even when reduction order
    # and float32 cancellation become significant.
    x=f.reshape(3,100,1024)
    x[:,:,0]=np.linspace(-10000,10000,100,dtype=np.float32)
    views=structured._views_from_flat(f)
    expected=np.stack([row.mean(axis=0) for row in x])
    assert np.array_equal(views['final_mean100'],expected)


def test_assembly_has_original_train_and_dev_and_both_synthetic_split_isolation():
    data,p= _prepared_mock()
    v=structured._assemble(data,p)
    assert set(v)==set(structured.VARIANTS)
    for f in structured.VARIANTS:
        s=v[f]
        assert len(s['train_source'][0])==60
        assert len(s['dev_source'][3])==30
        assert len(s['train_synthetic'][0])==24
        assert len(s['dev_synthetic'][3])==18
        assert len(s['test_synthetic'][3])==18
    assert v['structured_last_mean100']['train_source'][0].shape==(60,2048)
    assert v['structured_last_mean100']['train_synthetic'][0].shape==(24,2048)
    assert not np.shares_memory(v['structured_last_mean100']['train_source'][0],
                               v['structured_last_mean100']['dev_source'][3])
    assert not np.shares_memory(v['structured_last_mean100']['train_synthetic'][0],
                               v['structured_last_mean100']['dev_synthetic'][3])
    for split in ('train_synthetic','dev_synthetic','test_synthetic'):
        # Both matched mathematical renderings must remain in this split.
        spec=v['structured_last_mean100'][split]
        groups=joint._complete_training_groups(spec[3] if split=='train_synthetic'
            else [spec[0][int(i)] for i in spec[4]],
            spec[2] if split=='train_synthetic' else spec[1][spec[4]])
        assert len(groups)>0


def test_assembly_rejects_capture_drift_even_if_v3_profiles_look_valid():
    data,p=_prepared_mock()
    data['probe_arrays']['lora__final_last'][0,0]+=np.float16(1.)
    with pytest.raises(ValueError,match='CAPTURE_PARITY_FAILED'):
        structured._assemble(data,p)


def test_training_one_candidate_uses_frozen_head_additively_and_retention_priority():
    data,p=_prepared_mock()
    s=structured._assemble(data,p)['structured_last_mean100']
    args=SimpleNamespace(device='cpu',steps=4,lr=.003,eval_every=2,seed=7,
        source_weight=1.,anchor_weight=3.,zero_margin_weight=2.,cross_format_weight=0.)
    entry,weights=structured._score_candidate(torch,variant='structured_last_mean100',
                                               specs=s,args=args)
    assert entry['selected_step'] in (0,2,4)
    assert entry['decoder_parameters']<1_000_000
    assert entry['original_dev_retention']['status']=='PASS'
    assert entry['dev']['all']['cases']==18
    assert entry['test']['all']['cases']==18
    assert entry['gates']['promotion_authorized'] is False
    assert all(torch.isfinite(v).all() for v in weights['weights'].values())
    assert weights['mu'].shape==(2048,)
    assert weights['sigma'].shape==(2048,)


def test_full_report_and_candidate_remain_disposable(tmp_path,monkeypatch):
    data,p=_prepared_mock()
    monkeypatch.setattr(joint,'_prepare',lambda args:(data,p))
    monkeypatch.setattr(joint,'_verify_after',lambda info:None)
    args=SimpleNamespace(run=tmp_path/'source',expanded_run=tmp_path/'expanded',
        input=tmp_path/'capture',source_features=tmp_path/'retention',
        output=tmp_path/'output',device='cpu',steps=2,lr=.003,
        eval_every=2,seed=7,source_weight=1.,anchor_weight=3.,
        cross_format_weight=0.,zero_margin_weight=2.)
    result=structured.train(args)
    assert result['candidate_count']==3
    assert result['frozen_head']['original_dev_retention']['status']=='PASS'
    assert result['matched_training_budget']
    assert result['original_dev_used_for_fitting'] is False
    assert result['production_head_frozen'] and result['lora_frozen']
    assert not result['production_promotion']
    assert result['independent_acceptance_required']
    assert (args.output/'architecture_comparison.csv').is_file()
    assert (args.output/'checkpoint_history.csv').is_file()
    assert (args.output/'report.json').is_file()
    saved=torch.load(args.output/'disposable_candidate.pt',map_location='cpu',weights_only=False)
    assert saved['status']=='DISPOSABLE_NON_PRODUCTION'
    assert saved['promotion_authorized'] is False
    assert saved['feature']==result['selected_candidate']
    assert {r['architecture'] for r in result['candidates']}==set(structured.VARIANTS)
    with pytest.raises(FileExistsError,match='OUTPUT_ALREADY_EXISTS'):
        structured.train(args)


@pytest.mark.parametrize('bad',[
 {'steps':0},{'steps':20001},{'lr':-1},{'cross_format_weight':-2},
 {'anchor_weight':0},{'zero_margin_weight':-1},
])
def test_invalid_configuration_refused_before_loading_any_source(monkeypatch,bad,tmp_path):
    args=dict(run=tmp_path/'r',expanded_run=tmp_path/'e',input=tmp_path/'p',
        source_features=tmp_path/'s',output=tmp_path/'out',device='cpu',
        steps=2,lr=.003,eval_every=2,seed=7,source_weight=1.,
        anchor_weight=3.,zero_margin_weight=2.,cross_format_weight=0.)
    args.update(bad)
    monkeypatch.setattr(joint,'_prepare',lambda _:pytest.fail('source loaded'))
    with pytest.raises(ValueError,match='INVALID_OPTIMIZER_CONFIGURATION'):
        structured.train(SimpleNamespace(**args))


def test_structured_and_single_view_decoders_are_parameter_matched():
    one=joint._build_model(torch,width=1025,hidden=128)
    fused=joint._build_model(torch,width=2049,hidden=64)
    n=sum(p.numel() for p in one.parameters())
    m=sum(p.numel() for p in fused.parameters())
    assert abs(n-m)/max(n,m)<0.005
    assert n==131844
    assert m==131460


def test_joint_decoder_width_override_keeps_v3_default_behavior():
    data,p=_prepared_mock()
    v=structured._assemble(data,p)
    s=v['final_last']
    kwargs=dict(torch=torch,feature='final_last',source=s['train_source'],
        synthetic=s['train_synthetic'],original_dev=s['dev_source'],
        synthetic_dev=s['dev_synthetic'],device='cpu',steps=2,lr=.003,
        source_weight=1.,anchor_weight=3.,seed=7,eval_every=2,
        consistency_weight=0.,zero_margin_weight=2.)
    regular=joint._fit_one(**kwargs)
    explicit=joint._fit_one(**kwargs,decoder_hidden=128)
    assert regular[4]==explicit[4]
    assert regular[5]['decoder_hidden']==128
    with pytest.raises(ValueError,match='DECODER_HIDDEN'):
        joint._fit_one(**kwargs,decoder_hidden=2)
