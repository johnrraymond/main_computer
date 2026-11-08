"""V25 frozen ordered-token audit: no hidden-label or DEV-fitting shortcuts."""
from fractions import Fraction
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_ordered_token_causality_v25 as v25
from tools import nanojev_gamer_v1_operand_token_causality_v24 as v24
from tools import nanojev_gamer_v1_operand_causality_v23 as v23
from tools import nanojev_gamer_v1_sign_information_probe as probe
from tools import nanojev_gamer_v1_joint_head_repair as joint


class Tokenizer:
    padding_side='right';is_fast=True
    def encode(self,p,add_special_tokens=True):return [ord(c)%128 for c in p]
    def decode(self,ids,skip_special_tokens=False,clean_up_tokenization_spaces=False):
        return ''.join(chr(int(x)) for x in ids)
    def __call__(self,prompts,**kwargs):
        if isinstance(prompts,str):prompts=[prompts]
        longest=max(len(p) for p in prompts)
        return {'input_ids':torch.tensor([[ord(x)%128 for x in p]+[0]*(longest-len(p))
                         for p in prompts],dtype=torch.long),
                'attention_mask':torch.tensor([[1]*len(p)+[0]*(longest-len(p))
                         for p in prompts],dtype=torch.long),
                'offset_mapping':torch.tensor([[(i,i+1) for i in range(len(p))]+[(0,0)]*(longest-len(p))
                         for p in prompts],dtype=torch.long)}

class Layer(torch.nn.Module):
    def forward(self,z):return (z+0.0001,)

class ToyCore(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.embed_tokens=torch.nn.Embedding(128,6)
        self.layers=torch.nn.ModuleList(Layer() for _ in range(28))
        self.norm=torch.nn.LayerNorm(6)

class ToyModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        torch.manual_seed(22);self.model=ToyCore();self.eval()
    def forward(self,input_ids,attention_mask,**kwargs):
        x=self.model.embed_tokens(input_ids)
        for layer in self.model.layers:x=layer(x)[0]
        return SimpleNamespace(logits=self.model.norm(x)[:,-1,:])


def _deck():
    source=probe.make_deck(14,2,2,False)
    return {'dataset':[x.public() for x in source],
            'dataset_sha256':probe.deck_hash(source),
            'lineage':{'model_id':'toy','configuration':{'max_length':1024},
                       'checkpoint_path':'unused','checkpoint_sha256':'unused',
                       'adapter_dir':'unused','adapter_sha256':'unused'}}


def test_full_number_token_sequence_preserved_in_arithmetic_role_order():
    info=_deck();rows=v23._operand_rows(info,'train')[:6]
    prompts=v24._indexed_prompts(info)
    entries=[{'row':r,'prompt':prompts[r['archive_index']]} for r in rows]
    model=ToyModel();tok=Tokenizer()
    before={k:v.clone() for k,v in model.state_dict().items()}
    sequences,means,mask,ids,spans,maxdiff=v25._capture_batch(torch,model,tok,entries,
          device='cpu',max_length=1024,max_operand_tokens=16)
    assert set(sequences)==set(v25.STAGES)
    assert sequences['embedding'].shape==(6,2,16,6)
    assert means['final'].shape==(6,3,6)
    assert maxdiff==0
    for i,(meta,row) in enumerate(zip(spans,rows)):
        assert meta['id']==row['id']
        assert meta['tokenizer_roundtrip_both_exact']==1
        assert meta['first_before_second_in_text']==int(row['surface']=='source_native')
        for role,field in ((0,'first_operand'),(1,'second_operand')):
            n=int(mask[i,role].sum())
            assert n>=1
            raw=tok.decode(ids[i,role,:n])
            assert Fraction(raw)==Fraction(row[field])
            assert np.all(mask[i,role,n:]==0)
            assert np.all(ids[i,role,n:]==-1)
            assert np.all(sequences['embedding'][i,role,n:]==0)
    assert all(torch.equal(before[k],v) for k,v in model.state_dict().items())
    with pytest.raises(ValueError,match='OPERAND_TOKEN_CAP_EXCEEDED'):
        v25._capture_batch(torch,model,tok,entries,device='cpu',max_length=1024,
                           max_operand_tokens=1)
    with pytest.raises(ValueError,match='TRUNCATION'):
        v25._capture_batch(torch,model,tok,entries,device='cpu',max_length=20,
                           max_operand_tokens=16)


def test_ordered_sequence_is_not_mean_or_reverse_and_role_swap_is_distinct():
    seq=np.zeros((1,2,3,2),dtype=np.float32)
    seq[0,0,0]=[1,2];seq[0,0,1]=[3,4];seq[0,1,0]=[5,6]
    mask=np.array([[[1,1,0],[1,0,0]]],dtype=float)
    means={'final':np.array([[[2,3],[5,6],[0,0]]],dtype=float)}
    inputs={'final':seq}
    raw=v25._feature_matrix(inputs,means,mask,'final','ordered_token_sequence')
    swapped=v25._feature_matrix(inputs,means,mask,'final','swapped_operand_roles_control')
    reverse=v25._feature_matrix(inputs,means,mask,'final','reversed_within_operand_control')
    pooled=v25._feature_matrix(inputs,means,mask,'final','span_mean_v24_control')
    assert raw.shape==(1,2*3*2+6)
    assert pooled.shape==(1,4)
    assert not np.array_equal(raw,swapped)
    assert not np.array_equal(raw,reverse)
    assert np.array_equal(raw[0,12:],mask.reshape(-1))
    assert np.array_equal(reverse[0,0:4],[3,4,1,2])


def test_train_only_token_identity_prototypes_and_oov_counted_as_errors():
    tok=Tokenizer()
    seq=np.zeros((4,2,2,3),dtype=np.float32)
    labels=np.full((4,2,2),-1,dtype=np.int64)
    mask=np.zeros((4,2,2),dtype=np.float32)
    # TRAIN prototypes of characters '1' and '2', DEV has seen and unseen '3'.
    for i,token in ((0,49),(1,50),(2,49),(3,51)):
        labels[i,:,0]=token
        mask[i,:,0]=1
        seq[i,:,0,:]=[1,0,0] if token==49 else [0,1,0] if token==50 else [0,0,1]
    rows=[{'id':str(i),'group':str(i),'surface':'source_native',
           'first_operand':str({0:1,1:2,2:1,3:3}[i]),
           'second_operand':str({0:1,1:2,2:1,3:3}[i])} for i in range(4)]
    doc,details=v25._token_identity_recovery({'embedding':seq},labels,mask,
             [0,1],[2,3],rows,tok,variant='base',stage='embedding',scope='heldout')
    assert doc['seen_token_positions']==2
    assert doc['unseen_token_positions']==2
    assert doc['known_token_identity_accuracy']==1.0
    assert doc['both_token_sequences_exact']==1
    assert doc['both_operands_exact_from_token_ids']==1
    assert details[1]['all_token_ids_seen_in_fit']==0
    with pytest.raises(ValueError,match='TRAIN_EVALUATION_OVERLAP'):
        v25._token_identity_recovery({'embedding':seq},labels,mask,
              [0,1],[1,3],rows,tok,variant='base',stage='embedding',scope='bad')


def test_token_prediction_parser_does_not_repair_wrong_ids_from_oracle():
    assert v25._parse_decoded_number(' 1079/20 ')==Fraction(1079,20)
    assert v25._parse_decoded_number('12.25')==Fraction(49,4)
    assert v25._parse_decoded_number('oops 12.25') is None
    assert v25._parse_decoded_number('3/0') is None
    assert v25._parse_decoded_number('') is None


def test_verified_v24_report_hash_and_fitting_guards(tmp_path,monkeypatch):
    info=_deck()
    train=v23._operand_rows(info,'train');dev=v23._operand_rows(info,'dev')
    fake=tmp_path/'v24';fake.mkdir()
    monkeypatch.setattr(v24,'_validated_v23',lambda *a:'a'*64)
    monkeypatch.setattr(v25.v19,'_sha',lambda path:'b'*64)
    report={'schema':v24.SCHEMA,'status':'READ_ONLY_CAPTURE_AND_AUDIT_COMPLETE_NO_PROMOTION',
     'frozen_lineage':info['lineage'],'source_dataset_sha256':info['dataset_sha256'],
     'source_capture_sha256':'b'*64,'v23_report_sha256':'a'*64,
     'v22_report_sha256':'b'*64,'capture_cases':len(train)+len(dev),
     'train_fit_groups':len(v24._group_fit_split(train)[0])//6,
     'dev_out_of_range_groups':len(dev)//6,'test_cases_forwarded':0,
     'readout_fit_on_dev_or_test':False,'no_qwen_or_lora_training':True,
     'no_existing_specialist_or_head_training':True,'production_weights_modified':False,
     'checkpoint_saved':False,'promotion_authorized':False,
     'historical_sources_unchanged':True,'readouts':[{}]*60,
     'archive_final_last_parity':[{'variant':'base'},{'variant':'lora'}]}
    (fake/'report.json').write_text(json.dumps(report))
    args=SimpleNamespace(v24_run=fake,input=tmp_path,v22_run=tmp_path)
    assert v25._validated_v24(args,{'probe_info':info},train,dev)[0]=='b'*64
    report['readout_fit_on_dev_or_test']=True
    (fake/'report.json').write_text(json.dumps(report))
    with pytest.raises(ValueError,match='V24_PROVENANCE_MISMATCH: .*no_test'):
        v25._validated_v24(args,{'probe_info':info},train,dev)


def test_parser_audit_only_requires_v24_and_no_training_mode():
    p=v25.parser()
    for field in ('--v24-run','--v23-run','--max-operand-tokens','--output'):
        assert any(field in a.option_strings for a in p._actions)
    with pytest.raises(SystemExit):p.parse_args(['--mode','train'])


def test_v25_full_readonly_smoke(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_handoff as gamer
    info=_deck();tok=Tokenizer();model=ToyModel()
    rows=v23._operand_rows(info,'train')+v23._operand_rows(info,'dev')
    prompts=v24._indexed_prompts(info)
    captured,mean,mask,ids,meta,parity=v25._collect_variant(
        torch,tok,model,rows,prompts,variant='base',device='cpu',batch_size=6,
        max_length=1024,max_operand_tokens=16)
    archive=np.zeros((len(info['dataset']),6),dtype=np.float16)
    for i,r in enumerate(rows):archive[r['archive_index']]=mean['final'][i,2,:]
    data={'probe_info':info,'probe_arrays':{
          'base__final_last':archive,'lora__final_last':archive}}
    inp=tmp_path/'input';inp.mkdir();(inp/probe.CAPTURE_NAME).write_bytes(b'captured')
    head=tmp_path/'head.pt';head.write_bytes(b'original_head')
    adapter=tmp_path/'adapter';adapter.mkdir();(adapter/'weights').write_bytes(b'lora')
    info['lineage'].update(checkpoint_path=str(head),
         checkpoint_sha256=v25.spine.file_sha(head),adapter_dir=str(adapter),
         adapter_sha256='f'*64)
    monkeypatch.setattr(v25.spine,'adapter_identity',lambda p:'f'*64)
    monkeypatch.setattr(joint,'_prepare',lambda args:(data,{'verified':True}))
    monkeypatch.setattr(joint,'_verify_after',lambda data:None)
    monkeypatch.setattr(v25,'_validated_v24',lambda *args:('a'*64,{'v23_report_sha256':'c'*64}))
    monkeypatch.setattr(v25.spine,'cache_policy',lambda flag:None)
    monkeypatch.setattr(gamer,'model_components',lambda *a,**kw:
                          (torch,Tokenizer(),ToyModel()))
    kwargs={name:tmp_path/name for name in ('run','expanded_run','source_features',
          'prior','v11_run','v14_run','v19_run','v20_run','v21_run','v22_run','v23_run','v24_run')}
    kwargs.update(input=inp,output=tmp_path/'v25',device='cpu',capture_batch=6,
                  max_input_tokens=1024,max_operand_tokens=16,
                  ridge_alpha=4.,allow_model_download=False)
    # The production history is hashed and rechecked, even in this toy smoke.
    paths=[Path(kwargs['v24_run'])/'report.json',Path(kwargs['v23_run'])/'report.json',
           Path(kwargs['v22_run'])/'report.json',Path(kwargs['v19_run'])/'report.json',
           Path(kwargs['v20_run'])/'report.json',Path(kwargs['v21_run'])/'report.json',
           Path(kwargs['v14_run'])/'post_consolidation_candidate.pt',
           Path(kwargs['v19_run'])/'precision_joint_disposable_candidate.pt',
           Path(kwargs['v21_run'])/'shadow_precision_disposable_candidate.pt']
    for path in paths:
        path.parent.mkdir(exist_ok=True,parents=True);path.write_bytes(b'fake history')
    report=v25._run(SimpleNamespace(**kwargs))
    assert report['status']=='READ_ONLY_ORDERED_TOKENS_COMPLETE_NO_PROMOTION'
    assert report['test_cases_forwarded']==0
    assert report['no_qwen_or_lora_training'] and not report['promotion_authorized']
    assert report['tokenizer_roundtrip_both_exact']=={'base':len(rows),'lora':len(rows)}
    assert len(report['readouts'])==2*len(v25.STAGES)*len(v25.POLICIES)*2
    assert len(report['token_identity_readouts'])==2*len(v25.STAGES)*2
    assert len(report['archive_final_last_parity'])==2
    assert (tmp_path/'v25'/'sequence_operand_readouts.csv').exists()
    assert (tmp_path/'v25'/'token_identity_readouts.csv').exists()
    with np.load(tmp_path/'v25'/'base_ordered_operand_tokens.npz',allow_pickle=False) as saved:
        assert saved['embedding'].shape==(len(rows),2,16,6)
        assert saved['operand_token_ids'].shape==(len(rows),2,16)
        assert np.array_equal(saved['token_mask'].sum(axis=-1),mask.sum(axis=-1))
    assert report['activation_archive_sha256']['base']==v25.v19._sha(
        tmp_path/'v25'/'base_ordered_operand_tokens.npz')
    assert not list((tmp_path/'v25').glob('*.pt'))
    with pytest.raises(FileExistsError):v25._run(SimpleNamespace(**kwargs))
