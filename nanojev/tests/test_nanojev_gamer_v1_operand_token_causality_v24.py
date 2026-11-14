"""V24 span-level operands: no training of frozen weights or DEV-fitting."""
from fractions import Fraction
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_operand_token_causality_v24 as v24
from tools import nanojev_gamer_v1_operand_causality_v23 as v23
from tools import nanojev_gamer_v1_sign_information_probe as probe


def _deck(groups=14):
    d=probe.make_deck(groups,2,2,False)
    return {'dataset':[x.public() for x in d],
            'dataset_sha256':probe.deck_hash(d),
            'lineage':{'model_id':'toy','configuration':{'max_length':1024},
                       'checkpoint_path':'unused','checkpoint_sha256':'unused',
                       'adapter_dir':'unused','adapter_sha256':'unused'}}


class Tokenizer:
    padding_side='right'
    is_fast=True
    def encode(self,prompt,add_special_tokens=True):
        return [ord(c)%128 for c in prompt]
    def __call__(self,prompts,**kwargs):
        if isinstance(prompts,str):prompts=[prompts]
        maxlength=max(len(p) for p in prompts)
        ids=[];mask=[];offsets=[]
        for p in prompts:
            n=len(p)
            ids.append([ord(c)%128 for c in p]+[0]*(maxlength-n))
            mask.append([1]*n+[0]*(maxlength-n))
            offsets.append([(i,i+1) for i in range(n)]+[(0,0)]*(maxlength-n))
        return {'input_ids':torch.tensor(ids,dtype=torch.long),
                'attention_mask':torch.tensor(mask,dtype=torch.long),
                'offset_mapping':torch.tensor(offsets,dtype=torch.long)}


class Layer(torch.nn.Module):
    def forward(self,x):return (x*1.0001,)


class Backbone(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.embed_tokens=torch.nn.Embedding(128,6)
        self.layers=torch.nn.ModuleList(Layer() for _ in range(28))
        self.norm=torch.nn.LayerNorm(6)


class ToyModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        torch.manual_seed(39)
        self.model=Backbone()
        self.eval()
    def forward(self,input_ids,attention_mask,**kwargs):
        z=self.model.embed_tokens(input_ids)
        for layer in self.model.layers:z=layer(z)[0]
        z=self.model.norm(z)
        return SimpleNamespace(logits=z[:,-1,:])


def test_operands_exact_spans_and_arithmetic_order():
    info=_deck();rows=v23._operand_rows(info,'train')
    deck=probe.make_deck(14,2,2,False)
    prompts={i:x.prompt for i,x in enumerate(deck)}
    seen=set()
    for row in rows:
        prompt=prompts[row['archive_index']]
        left,right,a,b=v24._spans(prompt,row['surface'])
        assert a-b==Fraction(row['target_fraction'])
        assert (a,b)==(Fraction(row['first_operand']),Fraction(row['second_operand']))
        if row['surface']=='source_native':assert left[0]<right[0]
        else:assert left[0]>right[0] # DC=T appears after CC=R
        seen.add(row['surface'])
    assert seen=={'source_native','spine_difference'}


def test_offsets_cover_full_fraction_and_refuse_truncation():
    prompt='x=12/5, y=7.25'
    offsets=[(i,i+1) for i in range(len(prompt))]
    assert v24._aligned_tokens(offsets,(2,6))==[2,3,4,5]
    with pytest.raises(ValueError,match='SPAN_INCOMPLETE'):
        v24._aligned_tokens(offsets[:4],(2,6))
    with pytest.raises(ValueError,match='EMPTY'):
        v24._aligned_tokens(offsets,(30,35))


def test_group_holdout_is_interpolated_and_not_seen_in_fit():
    train=v23._operand_rows(_deck(),'train')
    fit,held,groups=v24._group_fit_split(train)
    assert set(train[i]['group'] for i in fit).isdisjoint(groups)
    assert set(train[i]['group'] for i in held)==set(groups)
    assert len(held)==len(groups)*6
    assert len(fit)+len(held)==len(train)


def test_capture_hooks_correct_role_alignment_and_no_prompt_or_label_forward():
    info=_deck();rows=v23._operand_rows(info,'train')[:4]
    prompts=v24._indexed_prompts(info)
    entries=[{'prompt':prompts[r['archive_index']],'row':r} for r in rows]
    model=ToyModel();tokenizer=Tokenizer()
    before={k:v.clone() for k,v in model.state_dict().items()}
    out,spans=v24._capture_token_batch(torch,model,tokenizer,entries,
       device='cpu',max_length=1024)
    assert len(out)==5 and len(spans)==4
    assert all(x.shape==(4,3,6) for x in out.values())
    for a,b in zip(spans,rows):
        assert a['id']==b['id']
        assert a['first_token_count']>=1 and a['second_token_count']>=1
        assert a['first_before_second_in_text']==(b['surface']=='source_native')
    assert all(torch.equal(model.state_dict()[k],v) for k,v in before.items())
    with pytest.raises(ValueError,match='TRUNCATION'):
        v24._capture_token_batch(torch,model,tokenizer,entries,
           device='cpu',max_length=15)


def test_frozen_pooled_ablation_and_ordered_pair():
    tensor=np.arange(42,dtype=float).reshape(2,3,7)
    x={'final':tensor}
    assert np.array_equal(v24._features(x,'final','prompt_last_control'),tensor[:,2])
    assert np.array_equal(v24._features(x,'final','unordered_operand_mean'),
                          (tensor[:,0]+tensor[:,1])/2)
    assert np.array_equal(v24._features(x,'final','ordered_operand_pair'),
                          np.concatenate([tensor[:,0],tensor[:,1]],axis=1))


def test_ridge_uses_estimates_not_text_oracle_and_reports_null():
    info=_deck();train=v23._operand_rows(info,'train');dev=v23._operand_rows(info,'dev')
    fit,held,_=v24._group_fit_split(train)
    # Finite, intentionally uninformative synthetic representations.
    x=np.asarray([[int(r['surface']=='spine_difference'),
                   int(r['target_sign']==0),
                   float(Fraction(r['second_operand']))/100]
                  for r in train+dev],dtype=float)
    rows=[train[i] for i in fit]
    metrics,cases=v24._predict_operands(x[fit],x[held],rows,
                           [train[i] for i in held],alpha=4.)
    assert metrics['sample_count']==len(held)
    assert len(metrics['shuffled_pair_null_sign_correct'])==3
    assert len(cases)==len(held)
    assert all(Fraction(r['decoded_first_0_01'])-
               Fraction(r['decoded_second_0_01'])==Fraction(r['decoded_difference'])
               for r in cases)
    assert metrics['exact_difference_matches']<=len(cases)
    with pytest.raises(ValueError,match='SHAPE'):
        v24._predict_operands(x[fit],x[:3,:2],rows,train[:3])


def test_archived_last_token_parity_detects_wrong_capture():
    capture={'final':np.ones((3,3,4),dtype=np.float32)}
    archive={'lora__final_last':np.ones((3,4),dtype=np.float16)}
    summary=v24._archive_parity(capture,archive,np.arange(3),'lora')
    assert summary['max_abs_difference']==0
    archive['lora__final_last'][:]=100
    with pytest.raises(ValueError,match='PARITY_FAILURE'):
        v24._archive_parity(capture,archive,np.arange(3),'lora')



def _complete_history(tmp_path,report,*,v14=None,v19=None,v20=None,v21=None):
    roots={}
    for name in ('v14','v19','v20','v21'):
        folder=tmp_path/name;folder.mkdir(exist_ok=True)
        roots[name]=folder
        (folder/'report.json').write_text('{}')
    for prefix,key in [('v19','v19_report_sha256'),('v20','v20_report_sha256'),
                       ('v21','v21_report_sha256')]:
        report[key]=v24.v19._sha(roots[prefix]/'report.json')
    candidates={'v14_post_consolidation':roots['v14']/'post_consolidation_candidate.pt',
      'v19_precision_joint':roots['v19']/'precision_joint_disposable_candidate.pt',
      'v21_shadow_precision':roots['v21']/'shadow_precision_disposable_candidate.pt'}
    for name,path in candidates.items():path.write_bytes(name.encode())
    report['starting_checkpoints_verified']=[{'arm':arm,
        'checkpoint_sha256':v24.v19._sha(file)} for arm,file in candidates.items()]
    return roots

def test_historical_v23_provenance_must_match(tmp_path):
    info=_deck();tr=v23._operand_rows(info,'train');dev=v23._operand_rows(info,'dev')
    root=tmp_path/'v23';root.mkdir()
    v22dir=tmp_path/'v22';v22dir.mkdir()
    (v22dir/'report.json').write_text('{}')
    data={'probe_info':info}
    doc={'schema':v23.SCHEMA,'status':'READ_ONLY_VERIFIED_NO_PROMOTION',
        'frozen_lineage':info['lineage'],'no_trainable_parameters_modified':True,
        'history_checkpoints_unchanged':True,'promotion_authorized':False,
        'previously_seen_test_used_for_fitting_selection_or_metrics':False,
        'train_operand_groups':len(tr)//6,'dev_operand_groups':len(dev)//6,
        'v22_report_sha256':v24.v19._sha(v22dir/'report.json')}
    roots=_complete_history(tmp_path,doc)
    (root/'report.json').write_text(json.dumps(doc))
    args=SimpleNamespace(v23_run=root,v22_run=v22dir,**{name+'_run':path for name,path in roots.items()})
    assert v24._validated_v23(args,data,tr,dev)==v24.v19._sha(root/'report.json')
    doc['train_operand_groups']+=1
    (root/'report.json').write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='V23_VERIFICATION_FAILED: .*groups'):
        v24._validated_v23(args,data,tr,dev)


def test_parser_audit_only_and_all_lineage_required():
    p=v24.parser()
    for key in ('--v23-run','--v22-run','--input','--output'):
        assert any(key in a.option_strings for a in p._actions)
    with pytest.raises(SystemExit):p.parse_args(['--mode','train'])


def test_v24_full_readonly_capture_and_report_smoke(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_joint_head_repair as joint
    from tools import nanojev_gamer_v1_handoff as gamer
    import gc
    info=_deck()
    run=tmp_path/'source';run.mkdir()
    input_dir=tmp_path/'capture';input_dir.mkdir()
    (input_dir/probe.CAPTURE_NAME).write_bytes(b'mock capture source bytes')
    cp=tmp_path/'model.pt';cp.write_bytes(b'frozen head')
    adapter=tmp_path/'adapter';adapter.mkdir()
    (adapter/'adapter.safetensors').write_bytes(b'frozen adapter')
    info['lineage']['checkpoint_path']=str(cp)
    info['lineage']['checkpoint_sha256']=v24.spine.file_sha(cp)
    info['lineage']['adapter_dir']=str(adapter)
    adapter_identity='ad'*32
    info['lineage']['adapter_sha256']=adapter_identity
    monkeypatch.setattr(v24.spine,'adapter_identity',lambda path:adapter_identity)
    monkeypatch.setattr(v24.spine,'cache_policy',lambda flag:None)
    tok=Tokenizer();model=ToyModel()
    train=v23._operand_rows(info,'train');dev=v23._operand_rows(info,'dev')
    allrows=train+dev;prompts=v24._indexed_prompts(info)
    captured,spans=v24._collect_variant(torch,tok,model,allrows,prompts,variant='base',
                          device='cpu',batch_size=6,max_length=1024,layers=v24.LAYERS)
    assert len(spans)==len(train)+len(dev)
    archive=np.zeros((len(info['dataset']),6),dtype=np.float16)
    for i,r in enumerate(allrows):archive[r['archive_index']]=captured['final'][i,2,:]
    data={'probe_info':info,'probe_arrays':{
        'base__final_last':archive.copy(),'lora__final_last':archive.copy()}}
    monkeypatch.setattr(joint,'_prepare',lambda args:(data,{'some_verified_source':True}))
    monkeypatch.setattr(joint,'_verify_after',lambda data:None)
    monkeypatch.setattr(gamer,'model_components',lambda model_id,device,adapter_dir=None:
                        (torch,Tokenizer(),ToyModel()))
    v22=tmp_path/'v22';v22.mkdir();(v22/'report.json').write_text('{}')
    v23dir=tmp_path/'v23';v23dir.mkdir()
    doc={'schema':v23.SCHEMA,'status':'READ_ONLY_VERIFIED_NO_PROMOTION',
        'frozen_lineage':info['lineage'],'no_trainable_parameters_modified':True,
        'history_checkpoints_unchanged':True,'promotion_authorized':False,
        'previously_seen_test_used_for_fitting_selection_or_metrics':False,
        'train_operand_groups':len(train)//6,'dev_operand_groups':len(dev)//6,
        'v22_report_sha256':v24.v19._sha(v22/'report.json')}
    roots=_complete_history(tmp_path,doc)
    (v23dir/'report.json').write_text(json.dumps(doc))
    kwargs={key:tmp_path/key for key in ('run','expanded_run','source_features','prior',
                 'v11_run','v14_run','v19_run','v20_run','v21_run')}
    kwargs.update(input=input_dir,v22_run=v22,v23_run=v23dir,**{name+'_run':path for name,path in roots.items()},
        output=tmp_path/'v24',device='cpu',capture_batch=6,max_input_tokens=1024,
        ridge_alpha=4.,allow_model_download=False)
    args=SimpleNamespace(**kwargs)
    report=v24._run(args)
    assert report['status']=='READ_ONLY_CAPTURE_AND_AUDIT_COMPLETE_NO_PROMOTION'
    assert report['test_cases_forwarded']==0
    assert report['readout_fit_on_dev_or_test'] is False
    assert report['checkpoint_saved'] is False
    assert len(report['readouts'])==2*5*3*2
    assert len(report['archive_final_last_parity'])==2
    assert (tmp_path/'v24'/'token_spans.csv').is_file()
    assert (tmp_path/'v24'/'operand_predictions.csv').is_file()
    assert not list((tmp_path/'v24').glob('*.pt'))
    with pytest.raises(FileExistsError):v24._run(args)
    # A forged historical V23 report must fail before a model is loaded.
    doc['v22_report_sha256']='0'*64
    (v23dir/'report.json').write_text(json.dumps(doc))
    args.output=tmp_path/'v24-forged'
    with pytest.raises(ValueError,match='V23_VERIFICATION_FAILED'):
        v24._run(args)
