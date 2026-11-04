"""Gamer v1 v10: real adapter-value transfer, fast start and replay/repair contracts."""
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from tools import nanojev_gamer_v1_handoff as gamer


class Tokenizer:
    pad_token_id=0
    eos_token_id=2
    def encode(self,text,add_special_tokens=True):
        return ([1] if add_special_tokens else [])+[3+ord(c)%40 for c in text]
    def __call__(self,text,truncation,return_tensors):
        return {'input_ids':torch.tensor([[7,8]]), 'attention_mask':torch.ones((1,2),dtype=torch.long)}
    def decode(self,ids,skip_special_tokens=True):
        return 'math' if int(ids[0])==9 else 'bad'


class Adapter(torch.nn.Module):
    def __init__(self,tracking,value=0.125):
        super().__init__()
        self.value=torch.nn.Parameter(torch.tensor([value],dtype=torch.float))
        self.tracking=tracking
        self.config=SimpleNamespace(use_cache=True)
    def forward(self,**kwargs):
        return SimpleNamespace(loss=(self.value-1).square().sum())
    def generate(self,**kwargs):
        self.tracking['generations']+=1
        self.tracking['gen_limits'].append(kwargs['max_new_tokens'])
        return torch.tensor([[7,8,9]],dtype=torch.long)
    def save_pretrained(self,folder):
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        (folder/'adapter_config.json').write_text(json.dumps({'r':16,'task_type':'CAUSAL_LM','base_model_name_or_path':'fake-qwen'}))
        (folder/'weights.json').write_text(json.dumps({'value':float(self.value.detach())}))


def fake_peft(monkeypatch,tracking):
    peft=types.ModuleType('peft')
    peft.LoraConfig=lambda **kwargs: SimpleNamespace(**kwargs)
    peft.get_peft_model=lambda base,cfg: Adapter(tracking)
    class PeftModel:
        @staticmethod
        def from_pretrained(base,folder,*,is_trainable):
            assert is_trainable
            value=json.loads((Path(folder)/'weights.json').read_text())['value']
            tracking['loaded_value']=value
            return Adapter(tracking,value)
    peft.PeftModel=PeftModel
    util=types.ModuleType('peft.utils')
    sub=types.ModuleType('peft.utils.save_and_load')
    sub.get_peft_model_state_dict=lambda a:{k:v.detach().clone() for k,v in a.state_dict().items()}
    sub.set_peft_model_state_dict=lambda a,state:a.load_state_dict(state)
    monkeypatch.setitem(sys.modules,'peft',peft)
    monkeypatch.setitem(sys.modules,'peft.utils',util)
    monkeypatch.setitem(sys.modules,'peft.utils.save_and_load',sub)
    monkeypatch.setattr(gamer,'model_components',lambda *a,**kw:(torch,Tokenizer(),object()))


def create_parent(tmp_path,*,final=False):
    parent=tmp_path/'original'
    folder=parent/'lora'/'best_adapter'
    Adapter({'generations':0,'gen_limits':[]},0.75).save_pretrained(folder)
    sha=gamer.file_tree_sha256(folder)
    (parent/'handoff_manifest.json').write_text(json.dumps({
        'schema':gamer.SCHEMA,'dataset_sha256':'a'*64,'model':'fake-qwen',
        'provenance':'source_recomputed'}))
    if final:
        (parent/'lora'/'report.json').write_text(json.dumps({
            'model':'fake-qwen','dataset_sha256':'a'*64,
            'best_adapter_sha256':sha,'best_step':100}))
    else:
        torch.save({'schema':gamer.SCHEMA,'phase':'lora','dataset_sha256':'a'*64,
                    'best_step':100,'best_score':0.8,'best_eval':{'exact':79}},
                   parent/'lora'/'latest.pt')
    return parent


def deck():
    cases=[{'game_id':'hawk_dove','prompt_08':'train named','math_prompt':'math','split':'train'},
           {'game_id':'hawk_dove','prompt_08':'train named2','math_prompt':'math','split':'train'},
           {'game_id':'hawk_dove','prompt_08':'train blind','math_prompt':'math','split':'train',
            'blind_matrix':True,'blind_matrix_regime':'exact_tie'},
           {'game_id':'hawk_dove','prompt_08':'dev named','math_prompt':'math','split':'dev'},
           {'game_id':'hawk_dove','prompt_08':'dev blind','math_prompt':'math','split':'dev',
            'blind_matrix':True,'blind_matrix_regime':'exact_tie'}]
    manifest={'dataset_sha256':'b'*64,'original_prompt_corpus_sha256':'c'*64,
              'provenance':'source_recomputed'}
    return cases,manifest


def args_for(output,parent,*,resume=False,steps=2):
    return SimpleNamespace(output=str(output),init_lora_from=str(parent),resume=resume,
         model='fake-qwen',device='cpu',seed=31,max_length=256,max_new_tokens=24,
         lora_rank=16,lora_batch=1,lora_accum=2,lora_lr=0.01,
         lora_eval_every=2,lora_save_every=1,lora_steps=steps,
         lora_probe_cases=17,allow_partial_smoke=True)


def test_source_requires_real_best_adapter_and_rejects_mismatched_rank(tmp_path):
    parent=create_parent(tmp_path)
    args=args_for(tmp_path/'new',parent)
    assert gamer.parent_lora_identity(args,torch)['source_best_step']==100
    assert gamer.parent_lora_identity(args,torch)['source_evidence']=='durable_best_checkpoint_not_finalized'
    cfg=parent/'lora'/'best_adapter'/'adapter_config.json'
    data=json.loads(cfg.read_text());data['r']=32;cfg.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='RANK_OR_TASK_MISMATCH'):
        gamer.parent_lora_identity(args,torch)
    cfg.write_text(json.dumps({'r':16,'task_type':'CAUSAL_LM','base_model_name_or_path':'fake-qwen'}))
    (parent/'lora'/'best_adapter'/'weights.json').unlink()
    # Fingerprint still exists, but the PEFT loader will detect missing weights.
    with pytest.raises(FileNotFoundError):
        (parent/'lora'/'best_adapter'/'weights.json').read_text()


def test_pretrained_transfer_starts_optimizer_without_generations(tmp_path,monkeypatch):
    parent=create_parent(tmp_path)
    tracker={'generations':0,'gen_limits':[]}
    fake_peft(monkeypatch,tracker)
    cases,manifest=deck()
    args=args_for(tmp_path/'transferred',parent)
    original=gamer.save_training_state
    def capture_step_zero(torch_mod,path,data):
        original(torch_mod,path,data)
        if data['phase']=='lora' and data['step']==0:
            assert data['baseline']['status']=='SKIPPED_PRETRAINED_TRANSFER'
            assert float(data['adapter']['value'][0]) == pytest.approx(.75)
            assert tracker['generations']==0
            raise RuntimeError('STOP_AT_FIRST_CHECKPOINT')
    monkeypatch.setattr(gamer,'save_training_state',capture_step_zero)
    with pytest.raises(RuntimeError,match='STOP_AT_FIRST_CHECKPOINT'):
        gamer.train_lora(args,cases,manifest)
    assert tracker['loaded_value']==pytest.approx(.75)
    assert (tmp_path/'transferred'/'lora'/'latest.pt').exists()
    assert not (tmp_path/'transferred'/'lora'/'eval_baseline.json').exists()
    assert gamer.file_tree_sha256(parent/'lora'/'best_adapter')==gamer.parent_lora_identity(args,torch)['adapter_sha256']


def test_transferred_run_resumes_after_optimizer_interrupt(tmp_path,monkeypatch):
    parent=create_parent(tmp_path)
    tracker={'generations':0,'gen_limits':[]}
    fake_peft(monkeypatch,tracker)
    cases,manifest=deck()
    args=args_for(tmp_path/'transferred',parent)
    real=gamer.save_training_state
    fired={'value':False}
    def cut_after_first_step(torch_mod,path,data):
        real(torch_mod,path,data)
        if data['phase']=='lora' and data['step']==1 and not fired['value']:
            fired['value']=True
            raise RuntimeError('STOP_AFTER_STEP_ONE')
    monkeypatch.setattr(gamer,'save_training_state',cut_after_first_step)
    with pytest.raises(RuntimeError,match='STOP_AFTER_STEP_ONE'):
        gamer.train_lora(args,cases,manifest)
    state=torch.load(tmp_path/'transferred'/'lora'/'latest.pt',weights_only=True)
    assert state['step']==1 and state['baseline']['total']==0
    assert sum(state['sampling_counts'].values())==2
    assert float(state['adapter']['value'][0])!=pytest.approx(.125)
    monkeypatch.setattr(gamer,'save_training_state',real)
    args.resume=True
    report=gamer.train_lora(args,cases,manifest)
    assert report['resumed'] and report['bootstrap']['source_best_step']==100
    assert report['best_step']==2 and report['baseline']['total']==0
    assert report['handoff_eligible'] is False
    assert report['retention_certification']=='NOT_AUDITED'
    assert report['evaluation_scope']=='BOUNDED_PROBE_NOT_FULL_AUDIT'
    assert sum(report['sampling_counts'].values())==4
    assert tracker['generations']==2  # only two selected dev prompts at step 2
    assert max(tracker['gen_limits']) < args.max_new_tokens


def test_probe_fixed_anchor_and_rotating_coverage_all_games():
    games=sorted(gamer.registry_ids())
    regimes=('anti_coordination','coordination','exact_tie','independent',
             'large_cancellation','positive_affine','signed_neighbor','symmetric','zero_sum')
    dev=[{'game_id':g,'prompt_08':g,'math_prompt':'math'} for g in games]
    dev += [{'game_id':'hawk_dove','prompt_08':'blind'+g,'math_prompt':'math',
             'blind_matrix':True,'blind_matrix_regime':g} for g in regimes]
    all_named=set();all_blind=set();fixed=None
    for i in range(5):
        deck,anchor=gamer.fast_probe_deck(dev,budget=17,evaluation_index=i)
        assert len(deck)==17 and len({r['prompt_08'] for r in deck})==17
        if fixed is None:fixed=[r['prompt_08'] for r in deck[:anchor]]
        else:assert [r['prompt_08'] for r in deck[:anchor]]==fixed
        all_named.update(r['game_id'] for r in deck if not r.get('blind_matrix'))
        all_blind.update(r['blind_matrix_regime'] for r in deck if r.get('blind_matrix'))
    assert all_named==set(games)
    assert all_blind==set(regimes)


def test_old_v8_lora_hyperparameters_unchanged():
    x=SimpleNamespace(model='fake-qwen',seed=31,max_length=256,max_new_tokens=24,
      lora_rank=16,lora_batch=1,lora_accum=2,lora_lr=.01,lora_eval_every=2,
      init_lora_from=None,lora_probe_cases=17)
    assert gamer.frozen_hyperparameters(x,'lora') == {
       'model':'fake-qwen','seed':31,'max_length':256,'max_new_tokens':24,
       'lora_rank':16,'lora_batch':1,'lora_accum':2,'lora_lr':.01,'lora_eval_every':2}
