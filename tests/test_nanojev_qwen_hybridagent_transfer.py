"""Fast CPU offline contracts for real transfer evaluator (no Qwen download)."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_qwen_hybridagent_activation as activation
from tools import nanojev_qwen_hybridagent_transfer as transfer


class TinyTokenizer:
    eos_token_id = 19
    def __call__(self,text,*,add_special_tokens=False,**kwargs):
        if text == '0': return {'input_ids':[5]}
        if text == '1': return {'input_ids':[6]}
        if text == '-1': return {'input_ids':[7]}
        if text == '\nAnswer:':return {'input_ids':[8,9]}
        return {'input_ids':[2,3,4]}
    def decode(self,ids,skip_special_tokens=True):
        return ''.join({5:'0',6:'1',7:'-1',18:'xx'}.get(i,'') for i in ids)

class TinyBackbone(nn.Module):
    def __init__(self):
        super().__init__()
        self.emb=nn.Embedding(24,12)
        self.out=nn.Linear(12,24,bias=False)
    def get_input_embeddings(self):return self.emb
    def get_output_embeddings(self):return self.out

class TinyModel(nn.Module):
    end_id=17
    def __init__(self):
        super().__init__();self.backbone=TinyBackbone();self.head=hybrid.HybridLink(12)
    def rollout(self,prompt,latent_steps,*,enter_latent,stop_on_end,return_trace):
        assert enter_latent and latent_steps==4 and not stop_on_end
        h=self.backbone.emb(prompt).mean(dim=1)
        for _ in range(latent_steps):
            h=self.head(h)
        return h,torch.zeros((1,24)),('cache',),torch.ones((1,prompt.shape[1]+latent_steps+1),dtype=torch.long),[]
    def _step(self,*,input_ids,cache,mask):
        logits=torch.full((1,24),-4.)
        logits[0,5]=0.4+float(self.head.proj2.bias[0])
        logits[0,6]=0.45-float(self.head.proj2.bias[0])
        logits[0,19]=0.1
        return torch.zeros((1,12)),logits,cache


def setup_artifacts(tmp_path):
    torch.manual_seed(1)
    base=tmp_path/'base';train=tmp_path/'train'
    base.mkdir();train.mkdir()
    model=TinyModel()
    details={'schema':hybrid.SCHEMA,'base_model':hybrid.MODEL_ID,'revision':hybrid.MODEL_REVISION,
             'training_status':'untrained'}
    (base/'adapter_config.json').write_text(json.dumps(details))
    torch.save({'schema':hybrid.SCHEMA,'head':copy.deepcopy(model.head.state_dict())},base/'adapter.pt')
    cfg={'schema':activation.SCHEMA,'adapter_schema':hybrid.SCHEMA,
         'baseline_dir':str(base.resolve()),'base_model':details['base_model'],'revision':details['revision'],
         'baseline_adapter_hash':activation.file_hash(base/'adapter.pt'),
         'baseline_config_hash':activation.file_hash(base/'adapter_config.json'),
         'stage':'teacher_forced_hybridlink_activation_only',
         'data_version':activation.DATA_VERSION,'data_hash':activation.data_fingerprint()}
    (train/'run_config.json').write_text(json.dumps(cfg))
    result={'status':'PASS_MECHANICS_ONLY','frozen_backbone':True,
            'backbone_hash_preserved':True,'baseline_mutations':0,'step':80,
            'best_dev_loss':0.3}
    (train/'result.json').write_text(json.dumps(result))
    trained=copy.deepcopy(model.head.state_dict())
    trained['proj2.bias'][0]+=0.6
    torch.save({'schema':activation.SCHEMA,'step':80,'head':trained,'dev':{'loss':0.3},
                'training_stage':'activation_only','adaptive_switching_certified':False},train/'best_head.pt')
    return base,train,model,details


def test_holdout_is_fixed_unseen_and_has_both_families_and_all_signs():
    c=transfer.cases()
    assert len(c)==16 and len(set(x['a'] for x in c))==16
    train,dev=activation.examples()
    seen={x['a'] for x in train+dev}
    assert not (set(x['a'] for x in c)&seen)
    assert {x['sign'] for x in c}=={'zero','positive','negative'}
    assert {x['family'] for x in c}=={'direct','composed'}
    assert transfer.case_fingerprint(c)==transfer.case_fingerprint(transfer.cases())

@pytest.mark.parametrize('raw,expected',[(' -7\ntext',-7),('0',0),('+11\n',11),(' \n-1\n',-1),
    ('explanation: 3',None),('1+1',None),('1.5',None),('abc',None)])
def test_only_signed_integer_on_first_line_is_accepted(raw,expected):
    assert transfer.parse_number(raw)==expected


def test_checkpoint_contract_accepts_real_activation_shape(tmp_path):
    b,t,model,cfg=setup_artifacts(tmp_path)
    best,signature,report=transfer.verify_training_artifacts(b,t,cfg)
    assert best['step']==80 and signature['baseline_adapter_hash']
    assert report['status']=='PASS_MECHANICS_ONLY'


def test_refuses_wrong_training_baseline_hash(tmp_path):
    b,t,model,cfg=setup_artifacts(tmp_path)
    run=json.loads((t/'run_config.json').read_text());run['baseline_adapter_hash']='bad'
    (t/'run_config.json').write_text(json.dumps(run))
    with pytest.raises(AssertionError,match='TRANSFER_BASELINE_WEIGHTS_CHANGED'):
        transfer.verify_training_artifacts(b,t,cfg)


def test_refuses_trained_checkpoint_incomplete(tmp_path):
    b,t,model,cfg=setup_artifacts(tmp_path)
    saved=torch.load(t/'best_head.pt',weights_only=True)
    saved['step']=1000
    torch.save(saved,t/'best_head.pt')
    with pytest.raises(AssertionError,match='TRANSFER_TRAINED_HEAD_INVALID_STEP'):
        transfer.verify_training_artifacts(b,t,cfg)


def test_refuses_unfinished_activation(tmp_path):
    b,t,model,cfg=setup_artifacts(tmp_path)
    report=json.loads((t/'result.json').read_text());report['status']='PAUSED_RESUMABLE'
    (t/'result.json').write_text(json.dumps(report))
    with pytest.raises(AssertionError,match='TRANSFER_ACTIVATION_NOT_COMPLETE'):
        transfer.verify_training_artifacts(b,t,cfg)


def test_no_label_enters_autonomous_inference(monkeypatch):
    model=TinyModel(); tok=TinyTokenizer()
    result=transfer.autonomous_answer(model,tok,'No answer or oracle thought here',
                                      latent_steps=4,max_new_tokens=3)
    assert set(result)=={'completion','predicted','first_logits','final_latent_hidden'}
    assert result['final_latent_hidden'].shape==(1,12)


def test_paired_scoring_detects_gain_loss_and_no_gain():
    sample=[{'answer':0,'sign':'zero'},{'answer':1,'sign':'positive'},
            {'answer':-1,'sign':'negative'}]
    initial=[{'predicted':1,'gold_probability':0.1},{'predicted':1,'gold_probability':0.2},
             {'predicted':0,'gold_probability':0.3}]
    trained=[{'predicted':0,'gold_probability':0.4},{'predicted':1,'gold_probability':0.5},
             {'predicted':-1,'gold_probability':0.6}]
    r=transfer.compare_results(initial,trained,sample)
    assert r['status']=='NUMERICAL_TRANSFER_OBSERVED' and r['net_accuracy_gain']==2
    r=transfer.compare_results(initial,initial,sample)
    assert r['status']=='NO_MEASURABLE_ACCURACY_GAIN'
    r=transfer.compare_results(initial,[{'predicted':999,'gold_probability':0}]*3,sample)
    assert r['status']=='NO_NUMERICAL_TRANSFER_DEMONSTRATED'


def test_end_to_end_readonly_and_rerun_guard(tmp_path,monkeypatch):
    base,train,model,details=setup_artifacts(tmp_path)
    original_hashes=[activation.file_hash(base/'adapter.pt'),activation.file_hash(train/'best_head.pt')]
    # Only 3 fast synthetic cases; this tests mechanics not real numerical accuracy.
    sample=transfer.cases()
    selected=[sample[i] for i in [1,2,3]]
    monkeypatch.setattr(transfer,'cases',lambda:selected)
    monkeypatch.setattr(transfer,'autonomous_answer',lambda mod,tok,prompt,**kw: {
      'predicted':0,'completion':'0','first_logits':torch.zeros((1,24)),
      'final_latent_hidden':torch.tensor([[float(mod.head.proj2.bias[0].detach())]])})
    def load_toy_adapter(path, toy_model, _details):
        state = torch.load(path/'adapter.pt', weights_only=True)['head']
        toy_model.head.load_state_dict(state, strict=True)
        return hybrid.read_config(path)
    monkeypatch.setattr(transfer.hybrid, 'load_adapter', load_toy_adapter)
    output=tmp_path/'probe'
    res=transfer.run_transfer(device='cpu',baseline=base,activation_dir=train,
             output=output,model_loader=lambda **_:(model,TinyTokenizer(),details))
    assert res['optimizer_steps']==0 and res['source_mutations']==0
    assert res['backbone_hash_preserved'] and res['oracle_latent_states_supplied']==0
    assert (output/'result.json').exists()
    assert original_hashes==[activation.file_hash(base/'adapter.pt'),activation.file_hash(train/'best_head.pt')]
    with pytest.raises(FileExistsError,match='TRANSFER_REFUSES_EXISTING_OUTPUT'):
        transfer.run_transfer(device='cpu',baseline=base,activation_dir=train,
                              output=output,model_loader=lambda **_:(model,TinyTokenizer(),details))
