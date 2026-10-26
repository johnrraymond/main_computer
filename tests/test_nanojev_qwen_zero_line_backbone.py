"""Fast CPU contract tests for Qwen signed-zero-line low-rank backbone SFT."""
from types import SimpleNamespace
import json
import pytest
import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_qwen_zero_line_backbone as phase


class FakeTokenizer:
    eos_token_id = 0
    def __call__(self,text,**kwargs):
        return {'input_ids':[ord(c) for c in text]}
    def decode(self, ids, **kwargs):
        return ''.join(chr(x) for x in ids if x > 0 and x < 256)


class TinyAttention(nn.Module):
    def __init__(self,width=16):
        super().__init__()
        self.q_proj=nn.Linear(width,width)
        self.v_proj=nn.Linear(width,width)
    def forward(self,x):
        return torch.tanh(self.q_proj(x)+self.v_proj(x))


class FakeBackbone(nn.Module):
    def __init__(self):
        super().__init__()
        self.config=SimpleNamespace(num_hidden_layers=1)
        self.embed=nn.Embedding(256,16)
        self.layer=TinyAttention()
        self.readout=nn.Linear(16,256)
    def forward(self, *, input_ids, attention_mask=None, labels=None,
                past_key_values=None,use_cache=False,return_dict=True):
        if attention_mask is not None:
            assert attention_mask.shape==input_ids.shape
        y=self.readout(self.layer(self.embed(input_ids)))
        loss=None
        if labels is not None:
            loss=F.cross_entropy(y[:,:-1,:].reshape(-1,256).float(),
                                 labels[:,1:].reshape(-1),ignore_index=-100)
        return SimpleNamespace(logits=y, loss=loss, past_key_values=object() if use_cache else None)


class FakeHybrid(nn.Module):
    def __init__(self):
        super().__init__()
        self.backbone=FakeBackbone()
        self.head=hybrid.HybridLink(16)


def fake_loader(**kwargs):
    torch.manual_seed(42)
    return FakeHybrid(),FakeTokenizer(),{'base_model':hybrid.MODEL_ID,
                                        'revision':hybrid.MODEL_REVISION}


def fake_baseline(tmp_path,monkeypatch):
    baseline=tmp_path/'baseline';baseline.mkdir()
    (baseline/'adapter.pt').write_bytes(b'head-test')
    (baseline/'adapter_config.json').write_bytes(b'config-test')
    monkeypatch.setattr(phase.hybrid,'load_adapter',lambda *a,**kw:None)
    return baseline


def test_balanced_disjoint_dataset_and_no_arithmetic_leakage():
    cases=phase.make_cases(71,96,18,18)
    assert {r['split'] for split in cases for r in cases[split]}=={'train','dev','test'}
    assert max(abs(r['a']) for r in cases['train']) < min(abs(r['a']) for r in cases['dev'])
    assert max(abs(r['a']) for r in cases['dev']) < min(abs(r['a']) for r in cases['test'])
    for rows in cases.values():
        assert len({(r['sign'],r['family']) for r in rows[:6]})==6
        assert sum(r['family']=='composed' for r in rows)==len(rows)//2
        assert all(r['u']+r['v']==r['answer'] for r in rows)
        for sign in ('negative','zero','positive'):
            assert sum(r['sign']==sign for r in rows)==len(rows)//3
    assert phase.ORACLE_THOUGHT != phase.CORRUPT_THOUGHT
    import re
    assert not re.search(r'\d', phase.ORACLE_THOUGHT+phase.CORRUPT_THOUGHT)


def test_sft_target_is_after_context_only():
    t=FakeTokenizer(); r=phase.make_cases(train_count=6,dev_count=6,test_count=6)['train'][0]
    ids,labels=phase.encoded_sft(t,r,256)
    i=labels.index(next(x for x in labels if x!=-100))
    assert all(x==-100 for x in labels[:i])
    assert labels[i:]==ids[i:]
    with pytest.raises(AssertionError,match='SEQUENCE_TOO_LONG'):
        phase.encoded_sft(t,r,20)


def test_lora_scope_and_gradient_flow():
    torch.manual_seed(44)
    model=FakeBackbone();model.requires_grad_(False)
    names=phase.install_lora(model,rank=2,alpha=4,required_layers=1)
    assert len(names)==2
    assert all('q_proj' in n or 'v_proj' in n for n in names)
    optimizer=torch.optim.AdamW([p for _,p in phase.lora_named_parameters(model)],lr=0.005)
    start=phase.lora_state(model)
    row=phase.make_cases(train_count=6,dev_count=6,test_count=6)['train'][0]
    loss=phase.train_one(model,FakeTokenizer(),row,optimizer,'cpu',max_seq=256)
    assert loss > 0
    end=phase.lora_state(model)
    assert any(not torch.equal(start[n],end[n]) for n in start)
    assert all(p.grad is None for n,p in model.named_parameters() if 'lora_' not in n)
    with pytest.raises(AssertionError,match='CHECKPOINT_KEY_DRIFT'):
        phase.restore_lora(model, {'none':torch.tensor([1.])})


def test_non_numerical_thought_utility_rejects_gold_leak():
    fake=FakeBackbone();t=FakeTokenizer()
    row=phase.make_cases(train_count=6,dev_count=6,test_count=6)['dev'][0]
    with pytest.raises(AssertionError,match='CONTAINS_NUMERICAL_ANSWER'):
        phase.answer_logprob(fake,t,row,'The answer is 9.','cpu')
    result=phase.thought_utility(fake,t,[row],'cpu')
    assert result['cases']==1 and isinstance(result['mean_advantage'],float)


def test_bounded_training_and_resume_preserve_checkpoint_and_optimizer(tmp_path,monkeypatch):
    baseline=fake_baseline(tmp_path,monkeypatch)
    args={'baseline':baseline,'device':'cpu','model_loader':fake_loader,'steps':4,
          'train_cases':6,'dev_cases':6,'test_cases':6,'eval_every':2,
          'rank':2,'lr':0.001,'max_seq':256,'blind_cases':0}
    complete=phase.run(output=tmp_path/'full',**args)
    assert complete['status']=='TRAINING_SMOKE_COMPLETED_UNCERTIFIED'
    assert complete['head_trainable_parameters']==0 and complete['baseline_mutations']==0
    out=tmp_path/'paused'
    pause=phase.run(output=out,stop_after=2,**args)
    assert pause['status']=='PAUSED_RESUMABLE'
    resumed=phase.run(output=out,resume=True,**args)
    assert resumed['status']=='TRAINING_SMOKE_COMPLETED_UNCERTIFIED'
    assert resumed['optimizer_steps_this_run']==2
    last_full=torch.load(tmp_path/'full'/'last.pt',weights_only=True)
    last_res=torch.load(out/'last.pt',weights_only=True)
    for name in last_full['lora']:
        assert torch.equal(last_full['lora'][name],last_res['lora'][name]), name
    with pytest.raises(FileExistsError,match='REFUSES_EXISTING_OUTPUT'):
        phase.run(output=out,**args)


def test_resume_rejects_baseline_mutation(tmp_path,monkeypatch):
    baseline=fake_baseline(tmp_path,monkeypatch)
    args={'baseline':baseline,'device':'cpu','model_loader':fake_loader,'steps':3,
          'train_cases':6,'dev_cases':6,'test_cases':6,'eval_every':1,
          'rank':2,'lr':0.001,'max_seq':256,'blind_cases':0,
          'output':tmp_path/'run'}
    phase.run(**args,stop_after=1)
    (baseline/'adapter.pt').write_bytes(b'changed')
    with pytest.raises(AssertionError,match='CONFIG_OR_BASELINE_DRIFT'):
        phase.run(**args,resume=True)


def test_output_never_collides_with_baseline(tmp_path):
    p=tmp_path/'head';p.mkdir()
    (p/'adapter.pt').write_bytes(b'head')
    (p/'adapter_config.json').write_bytes(b'config')
    with pytest.raises(ValueError,match='OUTPUT_COLLIDES_WITH_BASELINE'):
        phase.strict_check_input_paths(p,p)


def test_blind_probe_never_supplies_gold_or_teacher(tmp_path):
    model=FakeBackbone();tok=FakeTokenizer()
    row=phase.make_cases(train_count=6,dev_count=6,test_count=6)['test'][0]
    seen=[]
    original=model.forward
    def inspect(**kwargs):
        seen.append(kwargs['input_ids'].detach().cpu().clone())
        return original(**kwargs)
    model.forward=inspect
    result=phase.blind_probe(model,tok,[row],'cpu',tokens=3)
    assert result['cases']==1 and len(result['records'])==1
    prompt=tok.decode(seen[0][0].tolist())
    assert prompt=='Question: Compute '+row['expression']+'.\nReasoning: '
    assert 'equal-and-opposite terms cancel' not in prompt
    assert result['records'][0]['expected']==row['answer']


def test_frozen_sha_detects_unauthorized_mutation():
    model=FakeBackbone();model.requires_grad_(False)
    phase.install_lora(model,rank=2,alpha=4,required_layers=1)
    before=phase.frozen_backbone_sha256(model)
    with torch.no_grad():model.embed.weight[0,0].add_(1)
    assert before!=phase.frozen_backbone_sha256(model)


def test_amp_auto_prefers_bf16_when_supported(monkeypatch):
    monkeypatch.setattr(torch.cuda,'is_bf16_supported',lambda:True)
    assert phase.resolve_amp_dtype('cuda','auto')=='bfloat16'
    assert phase.resolve_amp_dtype('cuda','float16')=='float16'
    monkeypatch.setattr(torch.cuda,'is_bf16_supported',lambda:False)
    assert phase.resolve_amp_dtype('cuda','auto')=='float16'
    with pytest.raises(RuntimeError, match='BF16_NOT_SUPPORTED'):
        phase.resolve_amp_dtype('cuda','bfloat16')
    assert phase.resolve_amp_dtype('cpu','auto')=='cpu'


def test_gradient_report_distinguishes_missing_nonfinite_and_valid():
    model=FakeBackbone();model.requires_grad_(False)
    phase.install_lora(model,rank=2,alpha=4,required_layers=1)
    params=dict(phase.lora_named_parameters(model))
    initial=phase.gradient_report(model)
    assert len(initial['missing'])==len(params)
    name=next(n for n in params if n.endswith('.lora_b'))
    params[name].grad=torch.full_like(params[name],float('inf'))
    assert phase.gradient_report(model)['nonfinite']==[name]
    params[name].grad=torch.ones_like(params[name])
    assert phase.gradient_report(model)['nonzero']==[name]


class CPUScaledOverflow:
    """Controlled grad-scaler double to test first-backward overflow recovery."""
    def __init__(self,scale=256.):
        self.value=scale
        self.backoffs=0
        self.steps=0
        self.opt=None
    def scale(self,loss): return loss*self.value
    def unscale_(self,optimizer):
        for group in optimizer.param_groups:
            for p in group['params']:
                if p.grad is not None:p.grad.div_(self.value)
    def is_enabled(self):return True
    def get_scale(self):return self.value
    def update(self,new_scale=None):
        if new_scale is not None:
            assert new_scale<self.value
            self.backoffs+=1
            self.value=new_scale
    def step(self,optimizer):
        self.steps+=1
        optimizer.step()


def test_nonfinite_fp16_gradients_backoff_retry_without_bad_optimizer_step(monkeypatch):
    torch.manual_seed(80)
    model=FakeBackbone();model.requires_grad_(False)
    phase.install_lora(model,rank=2,alpha=4,required_layers=1)
    opt=torch.optim.AdamW([p for _,p in phase.lora_named_parameters(model)],lr=0.003)
    row=phase.make_cases(train_count=6,dev_count=6,test_count=6)['train'][0]
    b=next(p for n,p in phase.lora_named_parameters(model) if n.endswith('.lora_b'))
    calls={'n':0}
    def first_gradient_bad(grad):
        calls['n']+=1
        return torch.full_like(grad,float('inf')) if calls['n']==1 else grad
    hook=b.register_hook(first_gradient_bad)
    monkeypatch.setattr(phase,'resolve_amp_dtype',lambda device,choice='auto':'float16')
    scaler=CPUScaledOverflow()
    try:
        loss=phase.train_one(model,FakeTokenizer(),row,opt,'cpu',max_seq=256,scaler=scaler,amp_dtype='float16')
    finally:hook.remove()
    assert loss>0 and scaler.backoffs==1 and scaler.steps==1
    assert scaler.get_scale()==128.
    assert phase.gradient_report(model)['nonfinite']==[]
    assert all(torch.isfinite(p).all() for p in model.parameters())


def test_unscaled_nonfinite_gradient_fails_with_specific_parameter_names():
    model=FakeBackbone();model.requires_grad_(False)
    phase.install_lora(model,rank=2,alpha=4,required_layers=1)
    opt=torch.optim.AdamW([p for _,p in phase.lora_named_parameters(model)],lr=0.003)
    row=phase.make_cases(train_count=6,dev_count=6,test_count=6)['train'][0]
    b=next(p for n,p in phase.lora_named_parameters(model) if n.endswith('.lora_b'))
    hook=b.register_hook(lambda grad: torch.full_like(grad,float('nan')))
    try:
        with pytest.raises(AssertionError,match='ZERO_SFT_GRADIENT_NONFINITE:.*lora_b'):
            phase.train_one(model,FakeTokenizer(),row,opt,'cpu',max_seq=256,scaler=None)
    finally:hook.remove()
    assert not opt.state


def test_legacy_prestep_stub_recovered_only_when_untouched(tmp_path,monkeypatch):
    baseline=fake_baseline(tmp_path,monkeypatch)
    out=tmp_path/'previous_failed_step0'
    args={'baseline':baseline,'device':'cpu','model_loader':fake_loader,'steps':2,
          'train_cases':6,'dev_cases':6,'test_cases':6,'eval_every':1,
          'rank':2,'lr':0.001,'max_seq':256,'blind_cases':0,'output':out}
    old_train=phase.train_one
    def fail(*a,**kwargs): raise AssertionError('SIMULATED_FIRST_STEP_FAILURE')
    monkeypatch.setattr(phase,'train_one',fail)
    with pytest.raises(AssertionError,match='SIMULATED_FIRST_STEP_FAILURE'):
        phase.run(**args)
    assert {p.name for p in out.iterdir()}=={'run_config.json'}
    config=json.loads((out/'run_config.json').read_text())
    assert config['amp_dtype']=='cpu'
    # Exact v1 failure left the same inert stub, minus the new amp key.
    del config['amp_dtype']
    (out/'run_config.json').write_text(json.dumps(config))
    monkeypatch.setattr(phase,'train_one',old_train)
    report=phase.run(**args)
    assert report['step']==2
    assert json.loads((out/'run_config.json').read_text())['amp_dtype']=='cpu'
    assert (out/'last.pt').exists()
    # Reusing an output with an actual checkpoint must remain prohibited.
    with pytest.raises(FileExistsError,match='REFUSES_EXISTING_OUTPUT'):
        phase.run(**args)


def test_legacy_prestep_stub_config_drift_refused(tmp_path,monkeypatch):
    baseline=fake_baseline(tmp_path,monkeypatch)
    out=tmp_path/'old_failure';out.mkdir()
    (out/'run_config.json').write_text(json.dumps({'schema':phase.SCHEMA,'steps':999}))
    with pytest.raises(FileExistsError,match='PRESTEP_CONFIG_DRIFT'):
        phase.run(output=out,baseline=baseline,device='cpu',model_loader=fake_loader,
                  steps=2,train_cases=6,dev_cases=6,test_cases=6,blind_cases=0)


def test_disconnected_q_projection_fails_with_missing_gradient_names():
    model=FakeBackbone();model.requires_grad_(False)
    phase.install_lora(model,rank=2,alpha=4,required_layers=1)
    opt=torch.optim.AdamW([p for _,p in phase.lora_named_parameters(model)],lr=0.003)
    row=phase.make_cases(train_count=6,dev_count=6,test_count=6)['train'][0]
    model.layer.q_proj.forward=model.layer.q_proj.base.forward
    with pytest.raises(AssertionError,match=r'ZERO_SFT_GRADIENT_MISSING:.*q_proj.*lora'):
        phase.train_one(model,FakeTokenizer(),row,opt,'cpu',max_seq=256)
    assert not opt.state


def test_frozen_sha_supports_exact_bfloat16_bits_and_detects_mutations():
    """No BF16-to-FP32 conversion or rounding is allowed in the integrity digest."""
    import hashlib
    model = FakeBackbone().to(dtype=torch.bfloat16)
    model.requires_grad_(False)
    phase.install_lora(model, rank=2, alpha=4, required_layers=1)
    expected = hashlib.sha256()
    with torch.no_grad():
        for name, param in model.named_parameters():
            if name.endswith(('.lora_a', '.lora_b')):
                continue
            expected.update(name.encode('utf8'))
            expected.update(str(tuple(param.shape)).encode())
            expected.update(str(param.dtype).encode())
            # Independently inspect raw BF16 bit patterns via int16 view.
            raw_bits = param.detach().contiguous().cpu().reshape(-1).view(torch.int16)
            expected.update(raw_bits.numpy().tobytes())
    original = phase.frozen_backbone_sha256(model)
    assert original == expected.hexdigest()
    with torch.no_grad():
        model.embed.weight[0, 0].add_(1)
    assert phase.frozen_backbone_sha256(model) != original


def test_frozen_sha_preserves_existing_float32_hash_format():
    """Previously saved hashes must remain valid for models with NumPy dtypes."""
    import hashlib
    model = FakeBackbone()
    model.requires_grad_(False)
    phase.install_lora(model, rank=2, alpha=4, required_layers=1)
    legacy = hashlib.sha256()
    for name, param in model.named_parameters():
        if name.endswith(('.lora_a', '.lora_b')):
            continue
        legacy.update(name.encode('utf8'))
        legacy.update(str(tuple(param.shape)).encode())
        legacy.update(str(param.dtype).encode())
        legacy.update(param.detach().contiguous().cpu().numpy().tobytes())
    assert phase.frozen_backbone_sha256(model) == legacy.hexdigest()
