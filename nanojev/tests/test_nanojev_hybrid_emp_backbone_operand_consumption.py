"""Qwen's own LoRA and LM head, not the FP32 external operand reader, are certified."""
import json
import torch
import pytest
from pathlib import Path

from tools import nanojev_hybrid_emp_backbone_operand_consumption as c
from tools import nanojev_hybrid_emp_backbone_zero_line as arithmetic
from tools import nanojev_hybrid_emp_curriculum as curriculum
from tools import nanojev_hybrid_emp_operand_stage as operand
from tests.test_nanojev_hybrid_emp_backbone_zero_line import (
    Tokenizer, ToyTransformer, toy_loader, setup_certified, mockhead,
)


def test_curriculum_probe_covers_every_signed_operand():
    pop=arithmetic.stratified_provider(20261026)
    cards=c.probe_cards(pop.decks['dev'])
    values=[n for card in cards for n in (card.a,card.b)]
    assert set(values)==set(range(-16,17))
    assert all(values.count(n)>=2 for n in range(-16,17))
    assert len(cards)<len(pop.decks['dev'])


def test_backbone_decodes_pair_from_masked_qwen_input():
    model=ToyTransformer()
    model.requires_grad_(False)
    arithmetic.lora_tools.install_lora(model,4,8)
    sender=operand.OperandTransmission(sparse_dims=8).eval().requires_grad_(False)
    card=curriculum.Card(8,-8,0,0,8,'probe','train')
    tokenizer=Tokenizer()
    ids=arithmetic.code_indices(arithmetic.class_codes(tokenizer,16),16,'cpu')
    outputs={mode:c.paired_logits(model,tokenizer,model.embedding,mockhead()[0],sender,
                                  card,0,'cpu',ids,mode=mode)
             for mode in ('intact','zero','wrong')}
    assert all(x.shape==(2,33) for x in outputs.values())
    assert not torch.allclose(outputs['intact'],outputs['zero'])
    assert not torch.allclose(outputs['intact'],outputs['wrong'])


def test_gradients_only_to_qwen_lora_and_not_arithmetic():
    torch.set_num_threads(1)
    model=ToyTransformer(); model.requires_grad_(False)
    arithmetic.lora_tools.install_lora(model,4,8)
    opt=torch.optim.AdamW([p for _,p in arithmetic.lora_tools.lora_named_parameters(model)],lr=.0005)
    sender=operand.OperandTransmission(sparse_dims=8).eval().requires_grad_(False)
    card=curriculum.Card(8,-8,0,0,8,'probe','train')
    tok=Tokenizer()
    codes=arithmetic.code_indices(arithmetic.class_codes(tok,16),16,'cpu')
    loss,_=c.gradient_step(model,opt,tok,model.embedding,mockhead()[0],sender,card,0,'cpu',codes)
    assert loss>0
    assert any(p.grad is not None and p.grad.abs().sum()>0 for _,p in arithmetic.lora_tools.lora_named_parameters(model))
    assert all(p.grad is None for n,p in model.named_parameters() if not n.endswith(('.lora_a','.lora_b')))
    assert not any(p.grad is not None for p in sender.parameters())


def test_cell_exactness_and_controls_are_compulsory():
    perfect={'pairs':100,'operand_exact':1.,'pair_exact':1.,
             'zero_pair_exact':0.,'wrong_pair_exact':0.,'controls_evaluated':True,
             'by_value':{str(i):{'total':4,'correct':4,'accuracy':1.} for i in range(-16,17)}}
    assert c.passes(perfect)
    assert not c.passes({**perfect,'zero_pair_exact':1.})
    assert not c.passes({**perfect,'wrong_pair_exact':1.})
    assert not c.passes({**perfect,'pair_exact':.97})
    assert not c.passes({**perfect,'controls_evaluated':False})
    altered={**perfect,'by_value':{**perfect['by_value'],'-1':{'total':4,'correct':3,'accuracy':.75}}}
    assert not c.passes(altered)


def test_run_resume_dev_failure_seals_test(tmp_path):
    torch.set_num_threads(1)
    opdir=setup_certified(tmp_path)
    loader=toy_loader(); head=mockhead()
    kw=dict(device='cpu',output=tmp_path/'consumer',head1_dir=tmp_path,operand_dir=opdir,
            model_loader=loader,head1_override=head,steps=2,eval_every=1,rank=4)
    r=c.run(**kw,stop_after=1)
    assert r['status']=='PAUSED_RESUMABLE'
    r=c.run(**kw,resume=True)
    assert r['status']=='CONSUMPTION_DEV_FAIL'
    assert r['test_read'] is False
    assert not (tmp_path/'consumer'/'gate_result.json').exists()
    with pytest.raises(RuntimeError,match='DEV_CERTIFICATION_REQUIRED'):
        c.gate(device='cpu',output=tmp_path/'consumer',head1_dir=tmp_path,
               operand_dir=opdir,model_loader=loader,head1_override=head)


def test_test_read_only_after_full_dev(monkeypatch,tmp_path):
    torch.set_num_threads(1)
    opdir=setup_certified(tmp_path)
    load=toy_loader(); head=mockhead()
    counts={'test':0,'dev':0}
    def perfect(backbone,tokenizer,embedding,head1,sender,cards,device,class_ids,**kwargs):
        split=cards[0].split
        counts[split]+=1
        return {'pairs':len(cards),'operand_exact':1.,'pair_exact':1.,
                'zero_pair_exact':0.,'wrong_pair_exact':0.,'controls_evaluated':True,
                'minimum_value_accuracy':1.,
                'by_value':{str(i):{'total':4,'accuracy':1.,'correct':4} for i in range(-16,17)}}
    monkeypatch.setattr(c,'evaluate',perfect)
    root=tmp_path/'consumer'
    cfg=dict(device='cpu',output=root,head1_dir=tmp_path,operand_dir=opdir,
             model_loader=load,head1_override=head,steps=2,eval_every=1,rank=4)
    result=c.run(**cfg)
    assert result['status']=='TRAINED_UNCERTIFIED'
    assert counts['test']==0
    result=c.gate(**{k:cfg[k] for k in ('device','output','head1_dir','operand_dir','model_loader','head1_override')})
    assert result['gate_pass'] and result['test_read']
    assert counts['test']==1
    with pytest.raises(RuntimeError,match='ALREADY_READ'):
        c.gate(**{k:cfg[k] for k in ('device','output','head1_dir','operand_dir','model_loader','head1_override')})


def test_arithmetic_rejects_missing_and_false_consumption_cert(tmp_path):
    torch.set_num_threads(1)
    opdir=setup_certified(tmp_path)
    kw=dict(device='cpu',output=tmp_path/'zero',operand_dir=opdir,head1_dir=tmp_path,
            model_loader=toy_loader(),head1_override=mockhead(),steps_per_stage=1,
            eval_every=1,max_residual=1,rank=4,consumption_dir=tmp_path/'not_certified')
    with pytest.raises(FileNotFoundError):arithmetic.run(**kw)
    assert not (tmp_path/'zero'/'config.json').exists()
    root=tmp_path/'not_certified';root.mkdir()
    (root/'config.json').write_text('{}')
    (root/'gate_result.json').write_text(json.dumps({'status':'BACKBONE_OPERAND_CONSUMPTION_FAIL'}))
    with pytest.raises(RuntimeError,match='CERTIFICATION_REQUIRED'):
        arithmetic.run(**kw)


def test_certified_consumption_weights_are_loaded_as_arithmetic_warmstart(tmp_path):
    model=ToyTransformer();model.requires_grad_(False)
    arithmetic.lora_tools.install_lora(model,4,8)
    root=tmp_path/'consumption';root.mkdir()
    hashes={'config.json':'a','gate_result.json':'b','best.pt':'c'}
    identity={'revision':'toy-only','hidden':64,'tokenizer_size':256}
    operand_hashes={'best.pt':'abc','config.json':'def','gate_result.json':'ghi'}
    model_lora={n:torch.full_like(param,0.0125) for n,param in arithmetic.lora_tools.lora_named_parameters(model)}
    true={'pairs':33,'pair_exact':1.,'operand_exact':1.,'controls_evaluated':True,
          'zero_pair_exact':0.,'wrong_pair_exact':0.,
          'by_value':{str(i):{'total':3,'accuracy':1.} for i in range(-16,17)}}
    (root/'config.json').write_text(json.dumps({'schema':c.SCHEMA,'identity':identity,
        'operand_hashes':operand_hashes,'rank':4,'alpha':8}))
    (root/'gate_result.json').write_text(json.dumps({'schema':c.SCHEMA,
        'status':'BACKBONE_OPERAND_CONSUMPTION_PASS',
        'gate_pass':True,'test_read':True,'arithmetic_trained':False,'dev':true,'test':true}))
    torch.save({'schema':c.SCHEMA,'lora':model_lora},root/'best.pt')
    evidence=arithmetic.load_consumption_warmstart(model,root,identity=identity,
        operand_hashes=operand_hashes,rank=4,alpha=8)
    assert set(evidence)==set(hashes)
    assert all(torch.equal(p,model_lora[name]) for name,p in arithmetic.lora_tools.lora_named_parameters(model))
    with pytest.raises(RuntimeError,match='PROVENANCE_MISMATCH'):
        arithmetic.load_consumption_warmstart(model,root,identity={**identity,'hidden':32},
            operand_hashes=operand_hashes,rank=4,alpha=8)
    modified=json.loads((root/'gate_result.json').read_text())
    modified['test']['zero_pair_exact']=1.0
    (root/'gate_result.json').write_text(json.dumps(modified))
    with pytest.raises(RuntimeError,match='METRICS_DRIFT'):
        arithmetic.load_consumption_warmstart(model,root,identity=identity,
            operand_hashes=operand_hashes,rank=4,alpha=8)
