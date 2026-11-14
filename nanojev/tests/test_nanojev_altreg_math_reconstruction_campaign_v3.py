"""CPU-only reconstruction campaign tests, including real fork/training/resume."""
from __future__ import annotations
import copy
import json
import sys
import types
from pathlib import Path
import numpy as np
import pytest
import torch
from torch import nn

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_altreg_independent_math_qwen as v1
import nanojev_altreg_independent_math_qwen_precision_v2 as v2
import nanojev_altreg_math_reconstruction_compare_v3 as compare
import nanojev_altreg_math_v3_decoder_experiment as pilot
import nanojev_altreg_math_v3_pilot_compare as pilot_compare

class ToyTokenizer:
    pad_token_id=0
    eos_token_id=1
    def __call__(self,text,add_special_tokens=False):
        return {'input_ids':[2+ord(c)%251 for c in text]}

class ToyLM(nn.Module):
    def __init__(self):
        super().__init__()
        self.emb=nn.Embedding(260,4)
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
    assert name=='qwen'
    torch.manual_seed(1234)  # Model fixed across fork & every experiment.
    return torch,ToyLM(),ToyTokenizer(),'cpu',{
        'source':v2.MODEL_ID,'resolved_commit':'toy',
        'dtype':'torch.float32','num_hidden_layers':2,'model_type':'toy'}

def baseline_args(mod,path,*flags):
    return mod.parser().parse_args(['--results-root',str(path),'--device','cpu','--dtype','float32',
                                    '--batch-size','2','--width','16','--val-examples','2',
                                    '--test-examples','2','--max-prompt-tokens','512',
                                    '--eval-every','5','--report-every','5',*flags])

def train_args(path,source,arm,*flags):
    return pilot.parser().parse_args(['--backbone','qwen','--arm',arm,'--parent-root',str(source),
        '--results-root',str(path),'--device','cpu','--dtype','float32','--steps','4',
        '--batch-size','2','--hidden','16','--report-every','2','--eval-every','2',*flags])

@pytest.fixture
def fixture_roots(tmp_path,monkeypatch):
    monkeypatch.setattr(v1.cassette,'load_source',loader)
    source=tmp_path/'v1'
    assert v1.execute(baseline_args(v1,source,'--steps','20'))==0
    weighted=tmp_path/'weighted';control=tmp_path/'control'
    assert v2.execute(baseline_args(v2,weighted,'--steps','5','--fork-v1-root',str(source)))==0
    assert v2.execute(baseline_args(v2,control,'--steps','5','--precision-weight','0','--fork-v1-root',str(source)))==0
    return weighted,control,tmp_path

def test_component_oracles_and_paired_bootstrap():
    assert abs(compare.replacement(-135,135,['sign'])-135)<1e-7
    assert abs(compare.replacement(13.5,135,['exponent'])-135)<1e-7
    assert abs(compare.replacement(1.35,1.99,['mantissa'])-1.99)<1e-7
    assert compare.replacement(0,135,['exponent']) is None
    assert compare.replacement(135,0,['sign'])==0
    assert compare.replacement(135,0,['exponent']) is None
    assert pilot.self_test()==0
    rows=[]
    for order in ('original','reversed'):
        for i in range(10):
            rows.append({'orientation':order,'example_id':i,'x_error':0.,'y_error':.2,'joint_5pct':False})
    ci=compare.bootstrap_pair(rows,rows,200)
    assert ci['operand_5pct']['delta']==0.
    assert ci['joint_5pct']['bootstrap_95pct']==[0.,0.]


def test_comparison_with_real_checkpoints(fixture_roots):
    weighted,control,tmp=fixture_roots
    a=compare.parser().parse_args(['--backbone','qwen','--precision-root',str(weighted),
        '--control-root',str(control),'--output-root',str(tmp/'compare'),
        '--checkpoints','best-precision','best-asinh','--bootstrap-rounds','100'])
    result=compare.compare(a)
    assert len(result['paired'])==2
    assert len(result['lineages'])==4
    rows=list(__import__('csv').DictReader((tmp/'compare'/'predictions.csv').open()))
    assert len(rows)==2*2*3*2
    assert result['paired']['best-precision']['cases']==2
    assert result['lineages']['precision/best-precision']['oracle_summary']['original']['oracles']['sign+exponent+mantissa']['operand_5pct']==1.0
    with pytest.raises(RuntimeError,match='OUTPUT_NOT_EMPTY'):
        compare.compare(a)
    # Protect against impossible comparison of different parent forks.
    wrong=torch.load(control/'latest.pt',map_location='cpu',weights_only=True)
    wrong['config']['parent_v1']['sha256']='other'
    torch.save(wrong,control/'latest.pt')
    a2=compare.parser().parse_args(['--backbone','qwen','--precision-root',str(weighted),
        '--control-root',str(control),'--output-root',str(tmp/'bad'),
        '--checkpoints','latest','--bootstrap-rounds','100'])
    with pytest.raises(RuntimeError,match='DIFFERENT_FORK_ANCESTRY|SOURCE_IDENTITY_DRIFT'):
        compare.compare(a2)


def test_v3_all_arms_resume_comparison_and_isolation(fixture_roots,monkeypatch):
    weighted,control,tmp=fixture_roots
    monkeypatch.setattr(v2.cassette,'load_source',loader)
    runs=tmp/'runs'
    cfg_first=None
    for arm in pilot.ARMS:
        out=runs/('independent_altreg_math_qwen_v3_'+('residual_identity' if arm=='residual' else arm))
        assert pilot.run(train_args(out,weighted,arm))==0
        snap=torch.load(out/'latest.pt',map_location='cpu',weights_only=True)
        assert snap['step']==4 and snap['stream']['draws']==8
        if cfg_first is None:cfg_first=snap['config']
        else:
            for k in ('seed','parent_sha256','batch_size','lr'):
                assert snap['config'][k]==cfg_first[k]
        assert (out/'best.pt').is_file()
        if arm=='attention':assert (out/'attention-cache.pt').is_file()
        assert pilot.run(train_args(out,weighted,arm,'--eval-only','--eval-split','both'))==0
        assert pilot.run(train_args(out,weighted,arm,'--resume','--additional-steps','2'))==0
        resumed=torch.load(out/'latest.pt',map_location='cpu',weights_only=True)
        assert resumed['step']==6 and resumed['stream']['draws']==12
    assert not (weighted/'attention-cache.pt').exists()
    assert (weighted/'latest.pt').is_file()
    # Entire v3 pilot is isolated from parent v2 and between sibling arms.
    cargs=pilot_compare.parser().parse_args(['--backbone','qwen','--runs-root',str(runs),
        '--output-root',str(tmp/'v3-comparison'),'--device','cpu','--bootstrap-rounds','100'])
    report=pilot_compare.run(cargs)
    assert len(report['arms'])==3 and len(report['paired'])==3
    assert (tmp/'v3-comparison'/'predictions-validation.csv').is_file()
    cargs_test=pilot_compare.parser().parse_args(['--backbone','qwen','--runs-root',str(runs),
        '--output-root',str(tmp/'v3-test'),'--split','test','--device','cpu','--bootstrap-rounds','100'])
    with pytest.raises(RuntimeError,match='TEST_REQUIRES'):
        pilot_compare.run(cargs_test)


def test_cross_backbone_and_corrupt_resume_rejected(fixture_roots,monkeypatch):
    weighted,_,tmp=fixture_roots
    monkeypatch.setattr(v2.cassette,'load_source',loader)
    out=tmp/'isolated'
    assert pilot.run(train_args(out,weighted,'factorized'))==0
    p=out/'latest.pt';snap=torch.load(p,map_location='cpu',weights_only=True)
    snap['config']['parent_sha256']='changed'
    torch.save(snap,p)
    with pytest.raises(RuntimeError,match='RESUME_IDENTITY_MISMATCH'):
        pilot.run(train_args(out,weighted,'factorized','--resume','--additional-steps','1'))


def test_resume_matches_uninterrupted_head(fixture_roots,monkeypatch):
    weighted,_,tmp=fixture_roots
    monkeypatch.setattr(v2.cassette,'load_source',loader)
    once=tmp/'uninterrupted';staged=tmp/'staged'
    assert pilot.run(train_args(once,weighted,'residual','--steps','6'))==0
    assert pilot.run(train_args(staged,weighted,'residual','--steps','4'))==0
    assert pilot.run(train_args(staged,weighted,'residual','--resume','--additional-steps','2'))==0
    a=torch.load(once/'latest.pt',map_location='cpu',weights_only=True)
    b=torch.load(staged/'latest.pt',map_location='cpu',weights_only=True)
    assert a['step']==b['step']==6 and a['stream']==b['stream']
    for k in a['head']:
        assert torch.equal(a['head'][k],b['head'][k]),k


def test_residual_identity_initialization_and_trainable_range():
    # A zero-initialized decoder must exactly retain the learned v2 register.
    torch.manual_seed(2026)
    d=pilot.V3Decoder(32,4,'residual',6,hidden=24)
    h=torch.randn(7,32)
    parent_xy=torch.tensor([[-0.99,0.99],[0.0,-0.75],[0.45,0.67],
                            [0.12,-0.12],[0.91,0.03],[-0.25,0.48],[0.999,-0.999]])
    out=d(h,parent_xy,torch.tensor([0,1,2,3,0,1,2]))['xy']
    assert torch.equal(out,parent_xy), 'zero residual must be bit-identical to parent'
    # No second tanh; the training coordinates retain gradient outside bounds.
    with torch.no_grad():
        d.delta.bias.fill_(0.2)
    out=d(h,parent_xy,torch.tensor([0,1,2,3,0,1,2]))['xy']
    assert torch.allclose(out,parent_xy+0.2,atol=1e-7)
    assert float(out[0,1].detach())>1.0


def test_campaign_routes_corrected_residual_to_isolated_directory(tmp_path,capsys):
    import nanojev_altreg_math_campaign_v3 as campaign
    args=campaign.parser().parse_args(['--stage','pilot','--backbones','qwen','--arms','residual',
        '--runs-root',str(tmp_path),'--dry-run'])
    assert campaign.execute(args)==0
    emitted=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any('v3_residual_identity' in ' '.join(record.get('command',[])) for record in emitted)
    cfg_args=train_args(tmp_path/'new',tmp_path/'parent','residual')
    cfg_args._parent_step=1
    stub={'dataset_sha256':'X','model_id':'Y','max_prompt_tokens':30,
        'val_examples':4,'test_examples':4}
    identity=pilot.config(cfg_args,stub,'hash')
    assert identity['residual_semantics']=='identity-plus-delta-v1'
