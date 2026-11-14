"""Fast CPU regression tests for the actual two-pass self-conditioned loss."""
from types import SimpleNamespace
import json
from pathlib import Path
import pytest
import torch
from torch import nn

from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_qwen_hybridagent_activation as activation
from tools import nanojev_qwen_zero_line_backbone as zero
from tools import nanojev_qwen_hybridagent_self_conditioned as phase


class ToyTokenizer:
    eos_token_id=0
    def __call__(self, text, **kwargs):
        if text == hybrid.START: return {'input_ids':[250]}
        if text == hybrid.END: return {'input_ids':[251]}
        return {'input_ids':[ord(c) for c in text]}
    def decode(self, ids, **kwargs):
        return ''.join(chr(i) for i in ids if 0 < i < 250)


class Attention(nn.Module):
    def __init__(self, d):
        super().__init__()
        self.q_proj=nn.Linear(d,d)
        self.v_proj=nn.Linear(d,d)
    def forward(self,x):
        return torch.tanh(self.q_proj(x)+self.v_proj(x))


class Decoder(nn.Module):
    def __init__(self, outer):
        super().__init__()
        self.outer=outer  # In tests call method directly; avoid nesting in nn.Module tree.
    def forward(self, *, inputs_embeds, attention_mask, use_cache=False, return_dict=True):
        raise RuntimeError('unused')


class Backbone(nn.Module):
    def __init__(self):
        super().__init__()
        self.config=SimpleNamespace(num_hidden_layers=1)
        self.tokens=nn.Embedding(256,16)
        self.layer=Attention(16)
        self.lm_head=nn.Linear(16,256)
    def get_input_embeddings(self):return self.tokens
    def get_output_embeddings(self):return self.lm_head
    def get_decoder(self):return self
    def forward(self, *, inputs_embeds, attention_mask, use_cache=False,return_dict=True):
        assert inputs_embeds.ndim==3 and attention_mask.shape==inputs_embeds.shape[:2]
        return SimpleNamespace(last_hidden_state=self.layer(inputs_embeds))


class Agent(nn.Module):
    def __init__(self):
        super().__init__()
        self.backbone=Backbone()
        self.head=hybrid.HybridLink(16)
        self.start_id=250
        self.end_id=251


def loader(**kwargs):
    torch.manual_seed(12)
    return Agent(),ToyTokenizer(),{'base_model':hybrid.MODEL_ID,'revision':hybrid.MODEL_REVISION,
                                  'hidden_size':16,'schema':hybrid.SCHEMA}


def sources(tmp_path, monkeypatch):
    b=tmp_path/'baseline';a=tmp_path/'activation'; z=tmp_path/'backbone'
    for p in (b,a,z):p.mkdir()
    (b/'adapter.pt').write_bytes(b'baseline-adapter')
    (b/'adapter_config.json').write_bytes(b'baseline-config')
    model,_,_=loader()
    # Live adapter restore mocked only for synthetic CPU tests.
    monkeypatch.setattr(phase.hybrid,'load_adapter',lambda *args,**kwargs:None)
    hashes={'adapter.pt':activation.file_hash(b/'adapter.pt'),
            'adapter_config.json':activation.file_hash(b/'adapter_config.json')}
    activation.write_json_atomic(a/'run_config.json',{'schema':activation.SCHEMA,
        'baseline_dir':str(b.resolve()),'base_model':hybrid.MODEL_ID,
        'revision':hybrid.MODEL_REVISION,'baseline_adapter_hash':hashes['adapter.pt'],
        'baseline_config_hash':hashes['adapter_config.json']})
    activation.write_json_atomic(a/'result.json',{'status':'PASS_MECHANICS_ONLY'})
    activation.save_pt_atomic(a/'best_head.pt',{'schema':activation.SCHEMA,'step':8,
        'head':phase.cpu_head(model),'training_stage':'activation_only'})
    model.backbone.requires_grad_(False)
    zero.install_lora(model.backbone,rank=2,alpha=4,required_layers=1)
    zero.atomic_json(z/'run_config.json',{'schema':zero.SCHEMA,'baseline':str(b.resolve()),
       'base_model':hybrid.MODEL_ID,'revision':hybrid.MODEL_REVISION,
       'base_hashes':hashes,'rank':2,'alpha':4.})
    zero.atomic_json(z/'result.json',{'status':'TRAINING_SMOKE_COMPLETED_UNCERTIFIED'})
    zero.atomic_torch(z/'best_lora.pt',{'schema':zero.SCHEMA,'step':4,
                                      'lora':zero.lora_state(model.backbone)})
    return b,a,z


def test_exact_anchor_mask_and_no_prompt_supervision():
    model,tokenizer,_=loader()
    row=zero.make_cases(train_count=6,dev_count=6,test_count=6)['train'][0]
    data=phase.encode_example(tokenizer,model,row,256)
    ent,ex=data['entry_index'],data['exit_index']
    assert all(v==0 for v in data['text_mask'][:ent])
    assert data['text_mask'][ent]==1 and data['text_mask'][ex]==2
    assert data['latent_mask'][ent]==1 and data['latent_mask'][ex]==0
    assert all(v==0 for v in data['text_mask'][ent+1:ex])
    assert all(v==1 for v in data['latent_mask'][ent+1:ex])
    with pytest.raises(AssertionError,match='MAX_SEQ_EXCEEDED'):
        phase.encode_example(tokenizer,model,row,48)


def setup_trainable_agent():
    model,tokenizer,_=loader()
    model.backbone.requires_grad_(False)
    zero.install_lora(model.backbone,2,4,required_layers=1)
    model.head.requires_grad_(True)
    opt=torch.optim.AdamW([{'params':[p for _,p in zero.lora_named_parameters(model.backbone)]},
                            {'params':list(model.head.parameters())}],lr=.001)
    return model,tokenizer,opt


def test_both_components_receive_gradients_and_latent_input_is_causal():
    model,tok,opt=setup_trainable_agent()
    row=zero.make_cases(train_count=6,dev_count=6,test_count=6)['train'][0]
    data=phase.encode_example(tok,model,row,256)
    self_ =phase.forward_losses(model,data,'cpu')
    teacher=phase.forward_losses(model,data,'cpu',mode='teacher_control')
    assert float(self_['feedback_delta'].detach()) > 0
    assert abs(float((self_['text_ce']-teacher['text_ce']).detach())) > 1e-9
    self_['loss'].backward()
    phase.assert_good_gradients(model,opt)
    assert float(self_['link_loss'].detach()) > 0 and float(self_['text_ce'].detach()) > 0


def test_text_loss_alone_backprops_through_predicted_latent_inputs():
    model,tok,opt=setup_trainable_agent()
    row=zero.make_cases(train_count=6,dev_count=6,test_count=6)['train'][0]
    data=phase.encode_example(tok,model,row,256)
    phase.forward_losses(model,data,'cpu')['text_ce'].backward()
    head_grads=[p.grad for p in model.head.parameters()]
    assert any(g is not None and bool((g!=0).any()) for g in head_grads)


def test_run_resume_and_hash_guards(tmp_path,monkeypatch):
    b,a,z=sources(tmp_path,monkeypatch)
    cfg=dict(device='cpu',baseline=b,activation_dir=a,backbone_dir=z,
             steps=3,eval_every=1,seed=71,train_cases=6,dev_cases=6,test_cases=6,
             max_seq=256,blind_cases=0,model_loader=loader)
    first=phase.run(output=tmp_path/'continuous',**cfg)
    assert first['status']=='SELF_CONDITIONED_SMOKE_COMPLETED_UNCERTIFIED'
    assert first['head_updated'] and first['lora_updated'] and first['source_mutations']==0
    paused=phase.run(output=tmp_path/'paused',stop_after=1,**cfg)
    assert paused['status']=='PAUSED_RESUMABLE'
    resumed=phase.run(output=tmp_path/'paused',resume=True,**cfg)
    assert resumed['status']=='SELF_CONDITIONED_SMOKE_COMPLETED_UNCERTIFIED'
    cp1=torch.load(tmp_path/'continuous'/'last.pt',weights_only=True)
    cp2=torch.load(tmp_path/'paused'/'last.pt',weights_only=True)
    for group in ('lora','head'):
        for key in cp1[group]:
            assert torch.equal(cp1[group][key],cp2[group][key]), (group,key)
    with pytest.raises(AssertionError,match='REFUSES_EXISTING_OUTPUT'):
        phase.run(output=tmp_path/'continuous',**cfg)
    (a/'result.json').write_text('{"status":"BAD"}')
    with pytest.raises(AssertionError,match='HEAD_NOT_COMPLETED'):
        phase.run(output=tmp_path/'other',**cfg)


def test_independent_checkpoints_required(tmp_path, monkeypatch):
    b,a,z=sources(tmp_path,monkeypatch)
    (z/'best_lora.pt').unlink()
    with pytest.raises(AssertionError,match='SOURCE_MISSING:lora_best'):
        phase.run(device='cpu',baseline=b,activation_dir=a,backbone_dir=z,
                  output=tmp_path/'output',train_cases=6,dev_cases=6,test_cases=6,
                  steps=1,eval_every=1,model_loader=loader,blind_cases=0)


def test_exact_optimizer_scope_rejects_frozen_weight():
    model,tok,opt=setup_trainable_agent()
    assert len(phase.verify_gradient_scope(model,opt)[0])==4
    opt.add_param_group({'params':[model.backbone.tokens.weight]})
    with pytest.raises(AssertionError,match='OPTIMIZER_SCOPE_DRIFT'):
        phase.verify_gradient_scope(model,opt)


def test_free_running_probe_does_not_insert_teacher_or_gold():
    model,tok,_=loader()
    recorded=[]
    def fake_rollout(prompt_ids,steps,return_trace=False):
        recorded.append(tok.decode(prompt_ids[0].tolist()))
        return None,None,object(),torch.ones((1,1)),[{} for _ in range(steps)]
    model.rollout=fake_rollout
    model.text_after_latents=lambda cache,mask,**kw:[ord(ch) for ch in 'Answer: 3\n']
    row=zero.make_cases(train_count=6,dev_count=6,test_count=6)['test'][2]
    response=phase.blind_probes(model,tok,[row],'cpu',latent_steps=4)
    assert response['cases']==1 and response['records'][0]['latent_steps']==4
    assert recorded==[f"Question: Compute {row['expression']}.\nReasoning: "]
    assert 'equal and opposite' not in recorded[0].lower()
    assert 'Answer:' not in recorded[0]


def test_source_hash_prevents_adversarial_replacement(tmp_path,monkeypatch):
    b,a,z=sources(tmp_path,monkeypatch)
    hashes=phase.source_fingerprints(b,a,z)
    phase.validate_source_contract(b,a,z,hashes)
    (b/'adapter.pt').write_bytes(b'altered checkpoint')
    changed=phase.source_fingerprints(b,a,z)
    with pytest.raises(AssertionError,match='HEAD_ADAPTER_HASH_DRIFT'):
        phase.validate_source_contract(b,a,z,changed)


def test_fp32_decoder_hidden_into_bfloat16_lm_projection_and_both_gradients():
    """Do not assume final hidden dtype matches the BF16 lm_head dtype."""
    model, tokenizer, _ = loader()
    model.backbone.to(dtype=torch.bfloat16)
    model.backbone.requires_grad_(False)
    zero.install_lora(model.backbone, rank=2, alpha=4, required_layers=1)
    model.head.to(dtype=torch.float32)
    model.head.requires_grad_(True)
    original_forward = model.backbone.forward
    def forward_with_fp32_hidden(**kwargs):
        out = original_forward(**kwargs)
        return SimpleNamespace(last_hidden_state=out.last_hidden_state.float())
    model.backbone.forward = forward_with_fp32_hidden
    row = zero.make_cases(train_count=6, dev_count=6, test_count=6)['train'][0]
    data = phase.encode_example(tokenizer, model, row, 256)
    assert model.backbone.get_output_embeddings().weight.dtype == torch.bfloat16
    result = phase.forward_losses(model, data, 'cpu')
    assert torch.isfinite(result['loss'])
    result['loss'].backward()
    lora_grads = [p.grad for _, p in zero.lora_named_parameters(model.backbone)]
    head_grads = [p.grad for p in model.head.parameters()]
    assert any(g is not None and bool((g != 0).any()) for g in lora_grads)
    assert any(g is not None and bool((g != 0).any()) for g in head_grads)


def test_prestep_hash_failure_recovers_only_matching_unstarted_run(tmp_path, monkeypatch):
    """The CUDA BF16 hash exception leaves only run_config.json, not an optimizer step."""
    b, a, z = sources(tmp_path, monkeypatch)
    args = dict(device='cpu', baseline=b, activation_dir=a, backbone_dir=z,
                output=tmp_path/'run', steps=2, eval_every=1,
                train_cases=6, dev_cases=6, test_cases=6,
                max_seq=256, blind_cases=0, model_loader=loader)
    real_hash = phase.zero.frozen_backbone_sha256
    def simulated_old_bf16_failure(_):
        raise TypeError('Got unsupported ScalarType BFloat16')
    monkeypatch.setattr(phase.zero, 'frozen_backbone_sha256', simulated_old_bf16_failure)
    with pytest.raises(TypeError, match='BFloat16'):
        phase.run(**args)
    assert {p.name for p in args['output'].iterdir()} == {'run_config.json'}
    monkeypatch.setattr(phase.zero, 'frozen_backbone_sha256', real_hash)
    report = phase.run(**args)
    assert report['status'] == 'SELF_CONDITIONED_SMOKE_COMPLETED_UNCERTIFIED'
    assert report['start_step'] == 0 and report['optimizer_steps_this_run'] == 2
    assert (args['output']/'last.pt').exists()


def test_prestep_recovery_refuses_config_or_artifact_drift(tmp_path, monkeypatch):
    b, a, z = sources(tmp_path, monkeypatch)
    output = tmp_path/'run'
    output.mkdir()
    (output/'run_config.json').write_text('{"schema":"stale"}')
    args = dict(device='cpu', baseline=b, activation_dir=a, backbone_dir=z,
                output=output, steps=1, eval_every=1, train_cases=6,
                dev_cases=6, test_cases=6, max_seq=256, blind_cases=0,
                model_loader=loader)
    with pytest.raises(AssertionError, match='RECOVERY_CONFIG_OR_SOURCE_DRIFT'):
        phase.run(**args)
    (output/'unexpected.pt').write_bytes(b'not a legitimate pre-step state')
    with pytest.raises(AssertionError, match='REFUSES_EXISTING_OUTPUT'):
        phase.run(**args)
