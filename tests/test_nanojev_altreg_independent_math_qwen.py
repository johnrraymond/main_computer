"""Standalone qwen math-head experiment verification."""
from pathlib import Path
import json
import sys
import types

import pytest
import torch
from torch import nn

ROOT=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(ROOT))
import nanojev_altreg_independent_math_qwen as target


class ToyTokenizer:
    pad_token_id=0
    eos_token_id=1
    def __call__(self,text,add_special_tokens=False):
        return {'input_ids':[2+ord(c)%251 for c in text]}


class ToyLM(nn.Module):
    def __init__(self):
        super().__init__()
        self.emb=nn.Embedding(260,4)
        self.config=types.SimpleNamespace(num_hidden_layers=2,model_type='toy',_commit_hash='toy')
        self.eval()
        for p in self.parameters():p.requires_grad_(False)

    def get_input_embeddings(self):return self.emb

    def forward(self,input_ids,attention_mask,output_hidden_states=True,use_cache=False):
        x=self.emb(input_ids)
        seq=torch.arange(x.shape[1],device=x.device).reshape(1,-1,1).float()
        h1=(x*(1.+seq*.01)).cumsum(1)/torch.clamp(seq+1,min=1.)
        h2=h1*1.17+x*.05
        return types.SimpleNamespace(hidden_states=(x,h1,h2))


def loader(name,args):
    assert name==target.BACKBONE
    return torch,ToyLM(),ToyTokenizer(),'cpu',{
       'source':target.MODEL_ID,'resolved_commit':'toy',
       'dtype':'torch.float32','num_hidden_layers':2,'model_type':'toy'}


def cli(root, *extras):
    return target.parser().parse_args(['--results-root',str(root),'--device','cpu',
       '--dtype','float32','--batch-size','2','--width','16',
       '--val-examples','2','--test-examples','2','--max-prompt-tokens','512',
       '--eval-every','5','--report-every','5',*extras])


def test_this_backbone_owns_independent_code_and_root():
    assert target.BACKBONE=='qwen'
    assert target.MODEL_ID
    assert target.DEFAULT_ROOT.name=='independent_altreg_math_qwen_v1'
    source=Path(target.__file__).read_text()
    assert 'import nanojev_altreg_independent_math_' not in source
    assert 'intermediate-only-result-final-original' in source
    assert '16:3:1' in source
    assert target.self_test()==0


def test_frozen_capture_can_train_a_real_gradient():
    tok=ToyTokenizer();lm=ToyLM()
    features=target.capture(lm,tok,'cpu',[
       target.prompt(17,-3,'sym_sub',False),target.prompt(17,-3,'sym_sub',True)],512)
    assert features.shape==(2,32)
    assert not features.is_inference()
    head=target.MathHead(32,16,len(target.OP_IDS))
    out=head(features,torch.tensor([0,0]))
    a,b,_,_=target.asinh_truth([{'a':17,'b':-3,'operation':'sym_sub'}]*2,'cpu')
    loss=(out[0]-a).square().mean()+(out[1]-b).square().mean()
    loss.backward()
    assert head.feature[1].weight.grad is not None
    assert torch.isfinite(loss)


def test_independent_fork_resume_and_evaluate(tmp_path,monkeypatch):
    captured=[]
    original_capture=target.capture
    def watch_capture(model,tokenizer,device,prompts,max_tokens):
        captured.extend(prompts)
        return original_capture(model,tokenizer,device,prompts,max_tokens)
    monkeypatch.setattr(target,'capture',watch_capture)
    monkeypatch.setattr(target.cassette,'load_source',loader)
    root=tmp_path/'only_qwen'
    args=cli(root,'--steps','20')
    assert target.execute(args)==0
    assert (root/'baseline.json').is_file()
    assert (root/'test-last.json').is_file()
    checkpoint=torch.load(root/'latest.pt',map_location='cpu',weights_only=True)
    assert checkpoint['step']==20
    assert checkpoint['config']['backbone']=='qwen'
    assert checkpoint['phase_counts']=={'intermediate':16,'result':3,'final':1}
    assert checkpoint['stream']['draws']==40
    # Both validation caches are captured before training. Each of the 20
    # training ticks has 2 samples: 16 alternating X/Y ticks, then 4
    # ORIGINAL-only RESULT/FINAL ticks.
    train_prompts=captured[-40:]
    assert len(train_prompts)==40
    assert all(p.startswith(target.registers.FIRST+' =') for p in train_prompts[32:])
    for p0,p1 in zip(train_prompts[:32:2],train_prompts[1:32:2]):
        assert p0.startswith(target.registers.FIRST+' =')
        assert p1.startswith(target.registers.SECOND+' =')
    assert (root/'best.pt').is_file()
    val=json.loads((root/'validation-step-00000020.json').read_text())['validation']
    assert set(val)>={'original','reversed','averaged','rows'}
    assert 'result_asinh_mse' in val['reversed']
    assert 'final_asinh_mse' in val['averaged']
    assert target.execute(cli(root,'--resume','--additional-steps','5'))==0
    checkpoint=torch.load(root/'latest.pt',map_location='cpu',weights_only=True)
    assert checkpoint['step']==25
    assert checkpoint['stream']['draws']==50
    assert target.execute(cli(root,'--eval-only'))==0
    assert len(list(tmp_path.iterdir()))==1
    with pytest.raises(RuntimeError,match='ALTREG_INDEPENDENT_RESULTS_ROOT_NOT_EMPTY'):
        target.execute(cli(root,'--steps','2'))
    # All other model identities are rejected before optimizer state can cross.
    ck=torch.load(root/'latest.pt',map_location='cpu',weights_only=True)
    ck['config']['backbone']='not_this_backbone'
    torch.save(ck,root/'latest.pt')
    with pytest.raises(RuntimeError,match='ALTREG_RESUME_IDENTITY_OR_CONFIG_DRIFT'):
        target.execute(cli(root,'--resume','--additional-steps','1'))
