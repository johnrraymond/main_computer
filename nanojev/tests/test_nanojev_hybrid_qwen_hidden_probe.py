"""Hidden-state probing tests: frozen-backbone, causal controls and sealed TEST."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from tools import nanojev_hybrid_qwen_hidden_probe as p
from tools import nanojev_hybrid_emp_backbone_zero_line as prior
from tools import nanojev_hybrid_emp_operand_stage as operand
from tools import nanojev_hybrid_emp_curriculum as curriculum
from tests.test_nanojev_hybrid_emp_backbone_zero_line import (
    ToyTransformer,Tokenizer,mockhead,toy_loader,setup_certified,
)


class HiddenToy(ToyTransformer):
    def forward(self,*,inputs_embeds=None,attention_mask=None,use_cache=False,
                return_dict=True,output_hidden_states=False):
        original=inputs_embeds
        z=inputs_embeds.cumsum(dim=1)/torch.arange(1,inputs_embeds.shape[1]+1,
                                                     device=inputs_embeds.device).view(1,-1,1)
        h=torch.tanh(self.self_attn.q_proj(z)+self.self_attn.v_proj(z))
        return SimpleNamespace(logits=self.lm_head(h),hidden_states=(original,h) if output_hidden_states else None)


def test_hidden_retrieval_and_native_use_same_causal_position():
    torch.manual_seed(8)
    network=HiddenToy().eval().requires_grad_(False)
    inputs=torch.randn(6,64)
    classes=torch.tensor([97,98,99],dtype=torch.long)
    h,pred=p.hidden_for_vectors(network,inputs,classes,-1)
    result=network(inputs_embeds=inputs.unsqueeze(0),attention_mask=torch.ones(1,6,dtype=torch.long),
                   output_hidden_states=True)
    assert torch.allclose(h,result.hidden_states[-1][0,-1])
    assert pred==int(result.logits[0,-1,classes].argmax())
    with pytest.raises(RuntimeError,match='HIDDEN_STATES_UNAVAILABLE'):
        p.hidden_for_vectors(network,inputs,classes,99)


def test_extract_masks_raw_numbers_and_freezes_qwen():
    torch.manual_seed(12)
    network=HiddenToy().eval().requires_grad_(False)
    sender=operand.OperandTransmission(sparse_dims=8).eval().requires_grad_(False)
    card=curriculum.Card(8,-8,0,0,8,'probe','dev')
    ids=prior.code_indices(prior.class_codes(Tokenizer(),16),16,'cpu')
    data=p.extract_split(network,Tokenizer(),network.embedding,mockhead()[0],sender,
        [card],'cpu',ids,modes=p.MODES,progress_every=0)
    assert data['labels'].tolist()==[[24,8]]
    assert data['features']['intact'].shape==(1,2,64)
    assert not torch.equal(data['features']['intact'],data['features']['wrong'])
    assert not torch.equal(data['features']['intact'],data['features']['zero'])
    assert not any(x.requires_grad for x in network.parameters())
    assert all(x.grad is None for x in network.parameters())
    network.self_attn.q_proj.weight.requires_grad_(True)
    with pytest.raises(RuntimeError,match='BACKBONE_NOT_FROZEN'):
        p.extract_split(network,Tokenizer(),network.embedding,mockhead()[0],sender,
            [card],'cpu',ids,progress_every=0)


def synthetic(n=8):
    # Every signed value appears for both roles; each has a distinguishable
    # one-hot feature in the intact stream. Zero and wrong carry no identity.
    values=torch.arange(33).repeat(n)
    labels=torch.stack((values,values.roll(1)),dim=1)
    intact=torch.nn.functional.one_hot(labels,num_classes=33).float()
    zeros=torch.zeros_like(intact)
    wrong=torch.nn.functional.one_hot((labels+1)%33,num_classes=33).float()
    return {'labels':labels,
            'features':{'intact':intact,'zero':zeros,'wrong':wrong},
            'native':{k:torch.zeros_like(labels) for k in p.MODES}}


def test_probe_can_learn_transmitted_code_and_controls_fail():
    torch.set_num_threads(1)
    data=synthetic(8)
    model,mean,std,best=p.train_probe(data,data,kind='linear',steps=200,eval_every=25,lr=.04)
    result=p.assess(model,data,mean,std)
    assert result['gate_pass']
    assert result['intact']['pair_exact']==1.
    assert result['zero']['pair_exact']<.1
    assert result['wrong']['pair_exact']<.1
    assert result['intact']['native_pair_exact']<.1
    assert best['step']>0


def test_constant_predictor_cannot_pass_and_low_support_is_rejected():
    data=synthetic(2)
    result=p.assess(p.Readout(33),data,*p.normalization(data['features']['intact']))
    assert not result['gate_pass']
    perfect={'pairs':100,'pair_exact':1.,'operand_exact':1.,
             'by_value':{str(v):{'correct':2,'total':2,'accuracy':1.} for v in range(-16,17)}}
    bad={**perfect,'by_value':{**perfect['by_value'],'-16':{'correct':1,'total':2,'accuracy':.5}}}
    assert p.passes(perfect,{'pair_exact':0.},{'pair_exact':0.})
    assert not p.passes(perfect,{'pair_exact':1.},{'pair_exact':0.})
    assert not p.passes(bad,{'pair_exact':0.},{'pair_exact':0.})


def test_failed_probe_keeps_test_unread(tmp_path,monkeypatch):
    torch.set_num_threads(1)
    calls=[]
    monkeypatch.setattr(p,'load_frozen_stack',lambda **kw: (object(),Tokenizer(),object(),object(),object(),{'source':'base'}))
    def fake_extract(*args,split,**kwargs):
        calls.append(split)
        if split=='test':raise AssertionError('TEST WAS READ')
        full=synthetic(2)
        if split=='train':
            full['features']={'intact':full['features']['intact']}
            full['native']={'intact':full['native']['intact']}
        return full
    monkeypatch.setattr(p,'extract_split',fake_extract)
    result=p.run(device='cpu',source='base',output=tmp_path/'probe',steps=2,eval_every=1)
    assert result['status']=='HIDDEN_PROBE_DEV_FAIL'
    assert result['test_read'] is False
    assert calls==['train','dev']
    assert not (tmp_path/'probe'/'test.json').exists()
    assert (tmp_path/'probe'/'best.pt').exists()
    with pytest.raises(RuntimeError,match='OUTPUT_EXISTS'):
        p.run(device='cpu',source='base',output=tmp_path/'probe')


def test_preview_safe_without_loading_models(capsys):
    assert p.main(['--stage','preview','--device','cpu','--source','base'])==0
    report=json.loads(capsys.readouterr().out.split('HYBRID_HIDDEN_PREVIEW=',1)[1])
    assert report['trainable_scope']=='probe readout only'
    assert report['backbone_frozen'] and report['head2_frozen']
    assert not report['arithmetic_targets_used']
    assert report['test_sealed_until_dev']


def test_certification_success_reads_test_only_after_dev(tmp_path,monkeypatch):
    torch.set_num_threads(1)
    calls=[]
    monkeypatch.setattr(p,'load_frozen_stack',lambda **kw:(object(),Tokenizer(),object(),object(),object(),{'source':'base'}))
    def fake_extract(*args,split,**kwargs):
        calls.append(split)
        record=synthetic(4)
        if split=='train':
            record['features']={'intact':record['features']['intact']}
            record['native']={'intact':record['native']['intact']}
        return record
    monkeypatch.setattr(p,'extract_split',fake_extract)
    result=p.run(device='cpu',source='base',output=tmp_path/'passed',
                 steps=200,eval_every=20,lr=.05)
    assert result['status']=='HIDDEN_PROBE_PASS'
    assert result['dev']['gate_pass']
    assert result['test']['gate_pass']
    assert calls==['train','dev','test']
    assert result['backbone_trainable_parameters']==0


def test_existing_failed_consumption_lora_is_restored_but_frozen(tmp_path):
    torch.set_num_threads(1)
    opdir=setup_certified(tmp_path)
    baseline=toy_loader()
    head=mockhead()
    stack=prior.load_certified_stack(device='cpu',head1_dir=tmp_path,
        operand_dir=opdir,model_loader=baseline,head1_override=head)
    original,_,_,_,_,identity,hashes=stack
    prior.lora_tools.install_lora(original,4,8)
    saved={name:torch.full_like(param,.0123)
        for name,param in prior.lora_tools.lora_named_parameters(original)}
    root=tmp_path/'consume';root.mkdir()
    (root/'config.json').write_text(json.dumps({
        'schema':p.consume.SCHEMA,'identity':identity,'operand_hashes':hashes,
        'rank':4,'alpha':8}))
    torch.save({'schema':p.consume.SCHEMA,'step':200,'lora':saved},root/'last.pt')
    network,_,_,_,_,evidence=p.load_frozen_stack(device='cpu',head1_dir=tmp_path,
        operand_dir=opdir,consumption_dir=root,source='consumption-last',
        model_loader=baseline,head1_override=head)
    assert evidence['consumption_step']==200
    for name,param in prior.lora_tools.lora_named_parameters(network):
        assert torch.equal(param,saved[name])
    assert not any(v.requires_grad for v in network.parameters())
    (root/'config.json').write_text(json.dumps({
        'schema':p.consume.SCHEMA,'identity':identity,'operand_hashes':{'bad':'drift'},
        'rank':4,'alpha':8}))
    with pytest.raises(RuntimeError,match='PROVENANCE_DRIFT'):
        p.load_frozen_stack(device='cpu',head1_dir=tmp_path,operand_dir=opdir,
            consumption_dir=root,source='consumption-last',model_loader=baseline,
            head1_override=head)
