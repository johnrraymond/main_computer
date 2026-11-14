"""Contracts for Qwen LoRA consuming certified numerical edits (CPU toy only)."""
import json
from types import SimpleNamespace

import pytest
import torch
from torch import nn
from tools import nanojev_hybrid_emp_backbone_zero_line as target
from tools import nanojev_hybrid_emp_operand_stage as operand
from tools import nanojev_hybrid_emp_curriculum as curriculum
from tools import nanojev_hybrid_emp_join as join
from tests.test_nanojev_hybrid_emp_join import TinyTokenizer, NeuralLocatorStandIn, mockhead


class Tokenizer(TinyTokenizer):
    def __call__(self,text,add_special_tokens=False,return_offsets_mapping=False):
        return {'input_ids':[ord(c)%256 for c in text],
                **({'offset_mapping':[(i,i+1) for i in range(len(text))]}
                   if return_offsets_mapping else {})}


class ToyTransformer(nn.Module):
    def __init__(self):
        super().__init__()
        self.embedding=nn.Embedding(256,64)
        self.self_attn=nn.Module()
        self.self_attn.q_proj=nn.Linear(64,64)
        self.self_attn.v_proj=nn.Linear(64,64)
        self.lm_head=nn.Linear(64,256,bias=False)
        self.config=SimpleNamespace(num_hidden_layers=1)
    def get_input_embeddings(self):return self.embedding
    def forward(self,*,inputs_embeds=None,attention_mask=None,use_cache=False,return_dict=True):
        assert inputs_embeds is not None and attention_mask is not None
        # One causal stand-in aggregation; makes LoRA gradients testable.
        z=inputs_embeds.cumsum(dim=1)/torch.arange(1,inputs_embeds.shape[1]+1,device=inputs_embeds.device).view(1,-1,1)
        h=torch.tanh(self.self_attn.q_proj(z)+self.self_attn.v_proj(z))
        return SimpleNamespace(logits=self.lm_head(h))


def toy_loader():
    torch.manual_seed(631)
    original=ToyTransformer().state_dict()
    def load(device='cpu'):
        model=ToyTransformer().to(device)
        model.load_state_dict(original)
        return model,Tokenizer(),{'revision':'toy-only'}
    return load


def setup_certified(tmp_path):
    opdir=tmp_path/'operand'
    opdir.mkdir()
    headmeta=mockhead()[1]
    identity={'revision':'toy-only','hidden':64,'tokenizer_size':256}
    (opdir/'gate_result.json').write_text(json.dumps({
        'status':'OPERAND_TRANSMISSION_PASS','gate_pass':True,'resolver_unlocked':True,
        'test_read':True,'test_edited':{'pair_exact':1.0}}))
    (opdir/'config.json').write_text(json.dumps({
        'schema':operand.SCHEMA,'radius':16,'embedding':identity,'sources':{'head1':headmeta}}))
    torch.save({'schema':operand.SCHEMA,'head':operand.OperandTransmission(sparse_dims=8).state_dict()},opdir/'best.pt')
    return opdir


def test_class_code_is_atomic_unique_and_signed():
    codes=target.class_codes(Tokenizer(),16)
    assert len(codes)==33 and len(set(codes.values()))==33
    assert codes[-16]!=codes[0]!=codes[16]
    with pytest.raises(ValueError,match='CERTIFICATION'):
        target.class_codes(Tokenizer(),17)


def test_provider_residual_restriction_and_cells():
    pop=curriculum.LocalCurriculum(16)
    for r in target.STAGES:
        dev=target.stage_cards(pop,'dev',r)
        assert {c.delta for c in dev}==set(range(-r,r+1))
        assert all(abs(c.a)<=16 and abs(c.b)<=16 for c in dev)
    with pytest.raises(ValueError):target.stage_cards(pop,'train',3)
    perfect={'exact':1.,'zero_exact':0.,'wrong_exact':0.,
             'by_residual':{str(d):{'total':2,'correct':2} for d in (-1,0,1)},
             'cells':{'-1:1-4':{'total':2,'correct':2,'accuracy':1.},
                      '0:5-8':{'total':2,'correct':2,'accuracy':1.},
                      '1:9-16':{'total':2,'correct':2,'accuracy':1.}}}
    assert target.cell_gate(perfect,1)
    assert not target.cell_gate({**perfect,'zero_exact':1.},1)
    assert not target.cell_gate({**perfect,'cells':{'0:1-4':{'total':2,'correct':0,'accuracy':0.}}},1)
    assert not target.cell_gate({**perfect,'by_residual':{'0':{'total':2}}},1)


def test_masked_numerals_and_controls_are_not_operand_swaps():
    torch.manual_seed(68)
    model=ToyTransformer()
    head=NeuralLocatorStandIn()
    tok=Tokenizer()
    card=curriculum.Card(8,-8,0,0,8,'regression','train')
    example=join.extract(card,0,tok,head,model.embedding,'cpu',7)
    sender=operand.OperandTransmission(sparse_dims=8)
    example['bits']=operand.bits(example['values'],'cpu')
    cases={mode:target.sanitize_numerical_embeddings(example,sender,tok,model.embedding,mode=mode)
           for mode in ('intact','zero','wrong')}
    nums=example['positions'][0]+example['positions'][1]
    sparse=example['dimensions']
    other=torch.ones(64,dtype=torch.bool);other[sparse]=False
    for vec in cases.values():
        assert (vec[nums][:,other]==0).all()
        assert vec.shape[0]>example['base'].shape[0]
    assert not torch.equal(cases['intact'][nums][:,sparse],cases['zero'][nums][:,sparse])
    assert not torch.equal(cases['intact'][nums][:,sparse],cases['wrong'][nums][:,sparse])
    assert torch.equal(cases['intact'][0,other],cases['zero'][0,other])
    assert sum(operand.wrong_pair(example['values']))!=sum(example['values'])


def test_only_attention_lora_receives_gradient():
    torch.manual_seed(22)
    model=ToyTransformer()
    model.requires_grad_(False)
    target.lora_tools.install_lora(model,4,8)
    opt=torch.optim.AdamW([p for _,p in target.lora_tools.lora_named_parameters(model)],lr=.001)
    inp=torch.randn(8,64)
    classes=torch.tensor([65,66,67])
    loss,_=target._training_step(model,opt,inp,classes,1,device='cpu')
    assert loss>0
    assert all(p.grad is None for name,p in model.named_parameters() if '.lora_' not in name)
    assert any(p.grad is not None and p.grad.abs().sum()>0 for _,p in target.lora_tools.lora_named_parameters(model))


def test_certification_mandatory_and_preview(tmp_path):
    with pytest.raises(FileNotFoundError):
        target.load_certified_stack(device='cpu',head1_dir=tmp_path,operand_dir=tmp_path)
    code=target.main(['--stage','preview','--device','cpu','--max-residual','2'])
    assert code==0


def test_end_to_end_smoke_dev_failure_keeps_test_sealed(monkeypatch,tmp_path):
    torch.set_num_threads(1)
    opdir=setup_certified(tmp_path)
    monkeypatch.setattr(target,'load_consumption_warmstart',lambda *a,**kw: {'standin':'certified'})
    load=toy_loader()
    head=mockhead()
    output=tmp_path/'output'
    kw=dict(device='cpu',output=output,operand_dir=opdir,head1_dir=tmp_path,
            model_loader=load,head1_override=head,steps_per_stage=2,
            eval_every=1,max_residual=1,seed=20261026,rank=4)
    result=target.run(**kw,stop_after=1)
    assert result['status']=='PAUSED_RESUMABLE'
    result=target.run(**{**kw,'resume':True})
    assert result['status']=='STAGE_DEV_FAIL'
    assert result['test_read'] is False
    assert not (output/'gate_result.json').exists()
    with pytest.raises(RuntimeError,match='DEV_CERTIFICATION_REQUIRED'):
        target.gate(device='cpu',output=output,operand_dir=opdir,head1_dir=tmp_path,
                    model_loader=load,head1_override=head)
    with pytest.raises(RuntimeError,match='OUTPUT_EXISTS'):
        target.run(**kw)


def test_gate_uses_test_only_after_successful_dev(monkeypatch,tmp_path):
    """Simulated perfect metrics audit advancement and exactly-once test read."""
    torch.set_num_threads(1)
    opdir=setup_certified(tmp_path)
    monkeypatch.setattr(target,'load_consumption_warmstart',lambda *a,**kw: {'standin':'certified'})
    load=toy_loader()
    head=mockhead()
    root=tmp_path/'lora'
    counts={'dev':0,'test':0}
    def perfect(backbone,tokenizer,embedding,head1,sender,cards,codebook,radius,device,**kwargs):
        split=cards[0].split
        counts[split]+=1
        n=len(cards)
        cells={f'{x.delta}:{target.band(x.magnitude)}' for x in cards}
        return {'cases':n,'exact':1.,'zero_exact':0.,'wrong_exact':0.,
                'min_cell_exact':1., 'signed_error_stddev':0.,
                'cells':{c:{'correct':2,'total':2,'accuracy':1.} for c in cells},
                'by_residual':{str(d):{'correct':2,'total':2} for d in range(-radius,radius+1)}}
    monkeypatch.setattr(target,'evaluate',perfect)
    result=target.run(device='cpu',output=root,operand_dir=opdir,head1_dir=tmp_path,
                      model_loader=load,head1_override=head,steps_per_stage=2,
                      eval_every=1,max_residual=1,rank=4)
    assert result['status']=='TRAINED_UNCERTIFIED'
    assert counts['test']==0
    gate=target.gate(device='cpu',output=root,operand_dir=opdir,head1_dir=tmp_path,
                     model_loader=load,head1_override=head)
    assert gate['status']=='BACKBONE_ZERO_LINE_PASS' and gate['test_read']
    assert counts['test']==1
    with pytest.raises(RuntimeError,match='ALREADY_USED'):
        target.gate(device='cpu',output=root,operand_dir=opdir,head1_dir=tmp_path,
                    model_loader=load,head1_override=head)


def test_prompt_style_cannot_predict_balanced_residual():
    # Historical defect: delta index and style both used step%3 at radius 1.
    # This must fail if a future change reintroduces that shared modulo.
    for radius in target.STAGES:
        audit=target.style_schedule_audit(radius)
        assert audit['all_style_label_pairs_covered']
        labels=list(range(-radius,radius+1))
        crossed={(target.counterbalanced_style(step,radius),labels[step%len(labels)])
                 for step in range(5*len(labels))}
        assert len(crossed)==5*len(labels)
        for style in range(5):
            assert {delta for s,delta in crossed if s==style}==set(labels)
    with pytest.raises(ValueError,match='STYLE_SCHEDULE'):
        target.counterbalanced_style(0,3)


def test_preview_reports_counterbalanced_training_styles(capsys):
    assert target.main(['--stage','preview','--device','cpu','--max-residual','2'])==0
    output=capsys.readouterr().out
    line=next(x for x in output.splitlines() if x.startswith('HYBRID_BACKBONE_PREVIEW='))
    payload=json.loads(line.split('=',1)[1])
    assert payload['style_schedule']['1']['styles']==5
    assert payload['style_schedule']['1']['classes']==3
    assert payload['style_schedule']['2']['classes']==5
    assert payload['style_schedule']['1']['min_samples_per_style_class']==1

