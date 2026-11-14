"""CPU no-model-download tests of trainable marker interface, provenance, and gate."""
from pathlib import Path
import json
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_qwen_zero_line_backbone as zero
from tools import nanojev_qwen_hybridagent_activation as activation
from tools import nanojev_qwen_hybridagent_format_activation as fmt
from tools import nanojev_qwen_hybrid_interface_repair as fix


class Tok:
    eos_token_id=0
    def __call__(self,text,**kwargs):
        if text==hybrid.START:return {'input_ids':[250]}
        if text==hybrid.END:return {'input_ids':[251]}
        return {'input_ids':[ord(c) if ord(c)<240 else 239 for c in text]}
    def decode(self,ids,**k):return ''.join(chr(i) for i in ids if 0<i<240)

class Layer(nn.Module):
    def __init__(self,d):
        super().__init__();self.q_proj=nn.Linear(d,d);self.v_proj=nn.Linear(d,d)
    def forward(self,x):return torch.tanh(self.q_proj(x)+self.v_proj(x))

class Backbone(nn.Module):
    def __init__(self):
        super().__init__();self.config=SimpleNamespace(num_hidden_layers=1)
        self.emb=nn.Embedding(256,16);self.layer=Layer(16);self.out=nn.Linear(16,256)
    def get_decoder(self):return self
    def get_input_embeddings(self):return self.emb
    def get_output_embeddings(self):return self.out
    def forward(self,*,input_ids=None,inputs_embeds=None,attention_mask=None,
            past_key_values=None,output_hidden_states=False,use_cache=False,return_dict=True):
        x=self.emb(input_ids) if input_ids is not None else inputs_embeds
        y=self.layer(x)
        if output_hidden_states:
            old=0 if past_key_values is None else past_key_values[0]
            return SimpleNamespace(hidden_states=(y,),logits=self.out(y),past_key_values=(old+x.size(1),))
        return SimpleNamespace(last_hidden_state=y)

def loader(**kwargs):
    torch.manual_seed(444)
    bb=Backbone();bb.requires_grad_(False)
    return hybrid.HybridQwen(bb,hybrid.HybridLink(16),250,251),Tok(),{
        'base_model':hybrid.MODEL_ID,'revision':hybrid.MODEL_REVISION}


def sources(tmp_path,monkeypatch):
    base,nb,h=(tmp_path/x for x in ('baseline','numerical','head'))
    for p in (base,nb,h):p.mkdir()
    (base/'adapter.pt').write_bytes(b'initial')
    (base/'adapter_config.json').write_bytes(b'initial_cfg')
    monkeypatch.setattr(fix.hybrid,'load_adapter',lambda *_args,**_kwargs:None)
    b,m=loader()[0],loader()[0]
    zero.install_lora(b.backbone,rank=2,alpha=4.,required_layers=1)
    zero.atomic_json(nb/'run_config.json',{'schema':zero.SCHEMA,
      'baseline':str(base.resolve()),'base_model':hybrid.MODEL_ID,'revision':hybrid.MODEL_REVISION,
      'base_hashes':{'adapter.pt':fix.fingerprint(base/'adapter.pt'),
                     'adapter_config.json':fix.fingerprint(base/'adapter_config.json')},
      'rank':2,'alpha':4.})
    zero.atomic_json(nb/'result.json',{'status':'TRAINING_SMOKE_COMPLETED_UNCERTIFIED'})
    zero.atomic_torch(nb/'best_lora.pt',{'schema':zero.SCHEMA,'step':1,'lora':zero.lora_state(b.backbone)})
    zero.atomic_json(h/'run_config.json',{'schema':activation.SCHEMA,'baseline_dir':str(base.resolve()),
      'baseline_adapter_hash':fix.fingerprint(base/'adapter.pt'),
      'base_model':hybrid.MODEL_ID,'revision':hybrid.MODEL_REVISION})
    zero.atomic_json(h/'result.json',{'status':'PASS_MECHANICS_ONLY'})
    zero.atomic_torch(h/'best_head.pt',{'schema':activation.SCHEMA,'training_stage':'activation_only',
      'head':m.head.state_dict(),'step':1})
    return base,nb,h


def test_marker_rows_only_change_two_logits_and_input_positions():
    model,_,_=loader()
    rows=fix.MarkerRows(model)
    src=torch.tensor([[5,250,6,251,7]])
    emb=model.backbone.get_input_embeddings()(src)
    mod=rows.input_override(emb,src)
    assert torch.equal(mod[:,[0,2,4]],emb[:,[0,2,4]])
    with torch.no_grad(): rows.input_rows[0].add_(0.5)
    assert not torch.equal(rows.input_override(emb,src)[:,1],emb[:,1])
    hidden=torch.randn(4,16)
    base_logits=model.backbone.get_output_embeddings()(hidden)
    scores=rows.output_override(base_logits,hidden)
    assert torch.equal(scores[:,0:250],base_logits[:,0:250])
    assert not torch.equal(scores[:,250:252],base_logits[:,250:252])
    assert scores.shape==base_logits.shape


def test_loss_propagates_to_input_output_marker_and_lora_not_frozen_base():
    model,tok,_=loader()
    model.backbone.requires_grad_(False)
    zero.install_lora(model.backbone,2,4.,required_layers=1)
    rows=fix.MarkerRows(model)
    row=zero.make_cases(71,6,6,6)['train'][0]
    d=fmt.encode_format(tok,model,row,512)
    result=fix.marker_format_loss(model,rows,d,'cpu')
    result['loss'].backward()
    assert rows.input_rows.grad is not None and rows.input_rows.grad.abs().sum()>0
    assert rows.output_rows.grad is not None and rows.output_rows.grad.abs().sum()>0
    assert zero.gradient_report(model.backbone)['nonzero']
    assert not any(p.grad is not None for n,p in model.backbone.named_parameters() if '.lora_' not in n)


def test_stage_format_resume_is_bitwise_and_refuses_source_modification(tmp_path,monkeypatch):
    base,nb,h=sources(tmp_path,monkeypatch)
    kw=dict(device='cpu',base=base,numerical=nb,initial_head=h,
      steps=2,eval_every=1,train_count=6,dev_count=6,test_count=6,max_seq=512,loader=loader)
    fix.train_format(output=tmp_path/'straight',**kw)
    one=fix.train_format(output=tmp_path/'paused',stop_after=1,**kw)
    assert one['status']=='PAUSED_RESUMABLE'
    two=fix.train_format(output=tmp_path/'paused',resume=True,**kw)
    assert two['status']=='FORMAT_TRAINED_UNCERTIFIED'
    a=torch.load(tmp_path/'straight'/'format'/'last.pt',weights_only=True)
    b=torch.load(tmp_path/'paused'/'format'/'last.pt',weights_only=True)
    for key in a['lora']:assert torch.equal(a['lora'][key],b['lora'][key])
    for key in a['markers']:assert torch.equal(a['markers'][key],b['markers'][key])
    with pytest.raises(AssertionError,match='INTERFACE_FORMAT_OUTPUT_EXISTS'):
        fix.train_format(output=tmp_path/'straight',**kw)
    (base/'adapter.pt').write_bytes(b'mutated')
    with pytest.raises(AssertionError,match='FORMAT_BASE_ADAPTER_DRIFT|INTERFACE_HEAD_ADAPTER_CHANGED'):
        fix.train_format(output=tmp_path/'new',**kw)


def test_realign_uses_new_checkpoint_and_does_not_reuse_unaligned_head(tmp_path,monkeypatch):
    base,nb,h=sources(tmp_path,monkeypatch)
    output=tmp_path/'v2'
    fix.train_format(device='cpu',base=base,numerical=nb,initial_head=h,output=output,
      steps=2,eval_every=1,train_count=6,dev_count=6,test_count=6,max_seq=512,loader=loader)
    r=fix.realign(device='cpu',base=base,numerical=nb,output=output,steps=2,eval_every=1,
                 max_seq=512,loader=loader)
    assert r['status']=='HEAD_REALIGNED_UNCERTIFIED'
    cp=torch.load(output/'align'/'best.pt',weights_only=True)
    cfg=json.loads((output/'align'/'config.json').read_text())
    assert cfg['format_checkpoint_sha256']==fix.fingerprint(output/'format'/'best.pt')
    assert cp['config_hash']==fix._hash_config(cfg)
    with pytest.raises(AssertionError,match='INTERFACE_ALIGNMENT_CONFIG_DRIFT|INTERFACE_ALIGNMENT_OUTPUT_EXISTS'):
        fix.realign(device='cpu',base=base,numerical=nb,output=output,steps=2,eval_every=1,max_seq=512,loader=loader)


def test_gate_is_explicitly_uncertified_on_random_markers(tmp_path,monkeypatch):
    base,nb,h=sources(tmp_path,monkeypatch)
    output=tmp_path/'gate'
    fix.train_format(device='cpu',base=base,numerical=nb,initial_head=h,output=output,
      steps=1,eval_every=1,train_count=6,dev_count=6,test_count=6,max_seq=512,loader=loader)
    fix.realign(device='cpu',base=base,numerical=nb,output=output,steps=1,eval_every=1,max_seq=512,loader=loader)
    r=fix.gate(device='cpu',base=base,numerical=nb,output=output,n=6,tokens=2,loader=loader,
      min_entry_probability=0.999,min_exit_probability=0.999)
    assert r['status']=='ONE_STEP_GATE_FAIL'
    assert not r['deeper_training_permitted'] and r['oracle_latents_supplied']==0
    assert (output/'gate_result.json').is_file()


def test_bfloat16_marker_boundaries_preserve_gradient_without_dense_optimizer():
    model,_,_=loader()
    rows=fix.MarkerRows(model)
    token_ids=torch.tensor([[250,5,251]])
    original=model.backbone.get_input_embeddings()(token_ids).to(torch.bfloat16)
    moved=rows.input_override(original,token_ids)
    assert moved.dtype==torch.bfloat16
    fake_hidden=torch.randn(3,16,dtype=torch.bfloat16)
    fake_logits=torch.randn(3,256,dtype=torch.bfloat16)
    patched=rows.output_override(fake_logits,fake_hidden)
    assert patched.dtype==torch.float32
    assert torch.equal(patched[:,:250],fake_logits[:,:250].float())
    (moved.float().square().mean()+patched[:,-2:].square().mean()).backward()
    assert rows.input_rows.grad is not None and torch.isfinite(rows.input_rows.grad).all()
    assert rows.output_rows.grad is not None and torch.isfinite(rows.output_rows.grad).all()


def test_output_marker_casts_autocast_bf16_scores_to_fp32_before_index_copy(monkeypatch):
    """Mimic BF16 CUDA projection results on CPU; preserve marker gradients."""
    model,_,_=loader()
    rows=fix.MarkerRows(model)
    hidden=torch.randn(3,16,requires_grad=True)
    base_logits=torch.randn(3,256,requires_grad=True)
    real_linear=torch.nn.functional.linear
    bf16_scores=real_linear(hidden,rows.output_rows).to(torch.bfloat16)
    with monkeypatch.context() as m:
        # Native CUDA autocast emits BF16 here even when both inputs are FP32.
        m.setattr(fix.F,'linear',lambda x,w: real_linear(x,w).to(torch.bfloat16))
        patched=rows.output_override(base_logits,hidden)
    assert patched.dtype==torch.float32
    assert torch.equal(patched[:,:250],base_logits[:,:250])
    assert torch.equal(patched[:,250:252],bf16_scores.float())
    (patched[:,250:252].square().mean()+patched[:,:250].square().mean()).backward()
    assert rows.output_rows.grad is not None
    assert torch.isfinite(rows.output_rows.grad).all() and rows.output_rows.grad.abs().sum()>0
    assert hidden.grad is not None and hidden.grad.abs().sum()>0
    assert base_logits.grad is not None and base_logits.grad.abs().sum()>0


def test_marker_format_loss_under_bf16_autocast_remains_differentiable():
    model,tok,_=loader()
    model.backbone.requires_grad_(False)
    zero.install_lora(model.backbone,2,4.,required_layers=1)
    rows=fix.MarkerRows(model)
    row=zero.make_cases(71,6,6,6)['train'][0]
    example=fmt.encode_format(tok,model,row,512)
    with torch.autocast('cpu',dtype=torch.bfloat16):
        terms=fix.marker_format_loss(model,rows,example,'cpu')
    assert torch.isfinite(terms['loss'])
    terms['loss'].backward()
    assert torch.isfinite(rows.input_rows.grad).all()
    assert torch.isfinite(rows.output_rows.grad).all()
    assert rows.input_rows.grad.abs().sum()>0
    assert rows.output_rows.grad.abs().sum()>0
    assert zero.gradient_report(model.backbone)['nonzero']


def tied_loader(**kwargs):
    """Reproduce Qwen3-0.6B-Base's tied input/output embedding layout."""
    model,tok,details=loader(**kwargs)
    model.backbone.out=nn.Linear(16,256,bias=False)
    model.backbone.out.weight=model.backbone.emb.weight
    assert model.backbone.get_output_embeddings().weight.data_ptr()==model.backbone.get_input_embeddings().weight.data_ptr()
    return model,tok,details


def test_tied_qwen_output_rows_must_remain_distinct_after_checkpoint_load():
    model,tok,_=tied_loader()
    rows=fix.MarkerRows(model)
    with torch.no_grad():
        rows.input_rows.fill_(1.25)
        rows.output_rows[0].fill_(3.5)
        rows.output_rows[1].fill_(-2.5)
    rows.apply_to_model(model)
    assert model._hybrid_marker_tied_output
    assert torch.equal(model.backbone.get_input_embeddings().weight[250],rows.input_rows[0])
    assert torch.equal(model.backbone.get_input_embeddings().weight[251],rows.input_rows[1])
    # The shared matrix still holds input rows, but the actual LM logits must
    # use separate *output* rows. The old code skipped this and lost them.
    ids=torch.tensor([[6,7,8]])
    with torch.no_grad():
        output=model.backbone(input_ids=ids,output_hidden_states=True,use_cache=True,return_dict=True)
        hidden=output.hidden_states[-1]
        selected=output.logits[...,250:252]
        expected=torch.nn.functional.linear(hidden.float(),rows.output_rows)
        raw_tied=torch.nn.functional.linear(hidden.float(),model.backbone.emb.weight[250:252])
    assert torch.allclose(selected,expected,atol=1e-5)
    assert not torch.allclose(selected,raw_tied,atol=1e-5)
    with pytest.raises(AssertionError,match='ALREADY_INSTALLED'):
        rows.apply_to_model(model)


def test_tied_qwen_marker_rows_are_used_on_cached_recurrence():
    model,_,_=tied_loader()
    rows=fix.MarkerRows(model)
    with torch.no_grad():
        rows.output_rows[0].fill_(2.5)
        rows.output_rows[1].fill_(-2.)
    rows.apply_to_model(model)
    ids=torch.tensor([[2,3,4]])
    with torch.no_grad():
        _,logits,cache,mask,_=model.rollout(ids,1)
        assert cache is not None and mask.size(1)==5
        assert torch.isfinite(logits).all()
        # Once recurrence reaches the head, logits must be computed with saved rows.
        # Check the hook on the same LM projection used by backbone.
        h=torch.ones((1,16))
        values=model.backbone.get_output_embeddings()(h)
        expected=torch.nn.functional.linear(h,rows.output_rows)
        assert torch.allclose(values[:,250:252],expected)


def test_tied_checkpoint_gate_retains_training_time_marker_probabilities(tmp_path,monkeypatch):
    base,nb,h=sources(tmp_path,monkeypatch)
    output=tmp_path/'tied-gate'
    fix.train_format(device='cpu',base=base,numerical=nb,initial_head=h,output=output,
      steps=2,eval_every=1,train_count=6,dev_count=6,test_count=6,max_seq=512,loader=tied_loader)
    fix.realign(device='cpu',base=base,numerical=nb,output=output,steps=1,eval_every=1,
      max_seq=512,loader=tied_loader)
    # Retain the original failed gate report before overwriting the result.
    zero.atomic_json(output/'gate_result.json',{'status':'ONE_STEP_GATE_FAIL','original':True})
    # Prior to the correction, gate reconstructed rows from tied input weights
    # and silently discarded the learned output rows. The gate must now assert
    # saved-vs-reloaded marker probability parity rather than pretending PASS.
    result=fix.gate(device='cpu',base=base,numerical=nb,output=output,n=6,tokens=2,
      loader=tied_loader,min_entry_probability=0.999,min_exit_probability=0.999)
    assert result['marker_checkpoint_roundtrip'] is True
    assert json.loads((output/'gate_pre_tied_output.json').read_text())['original'] is True
    assert result['tied_output_marker_override'] is True
    dev=torch.load(output/'format'/'best.pt',map_location='cpu',weights_only=True)['dev']
    assert abs(result['entry_probability']-dev['entry_prob'])<1e-3
    assert abs(result['exit_probability']-dev['exit_prob'])<1e-3
    assert result['status']=='ONE_STEP_GATE_FAIL' # untrained toy cannot meet 0.999 threshold
