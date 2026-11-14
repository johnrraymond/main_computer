"""CPU contract tests for edge-first A/B continuous glass, no model download."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest

from tools import nanojev_qwen_lm_deformation_smoke as m
from tools import nanojev_qwen_lm_radius_ladder as ladder


def args(**kwargs):
    return SimpleNamespace(inner_shells=1,outer_shells=2,depth_shells=2,
        probe_per_cell=2,probe_seed=81,probe_limit=120,seed=42,
        **kwargs)


def cfg():
    return {'terminal_mode':'numeric','radii':[0,1,2,3,4],
            'operand_limit':32,'max_rung':8,'seed':42257,'per_cell':3,
            't00_mode':'exhaustive'}


def state():
    return {'config':cfg(),'rung':5,'radius_index':4}


def test_edge_is_bidirectional_and_not_r0_origin_first():
    frontier=m._frontier_spec(state(),args())
    assert frontier['current_radius']==4
    assert set(frontier['residuals'])=={-6,-5,-4,-3,0,3,4,5,6}
    assert frontier['rungs']==[0,3,4,5,6,7]
    rows,info=m._sample_candidate_rows(state(),args())
    assert rows
    assert len(rows)<=120
    assert any(r['rung']==5 for r in rows)
    assert any(r['rung']<5 for r in rows)
    assert any(abs(r['residual'])<4 for r in rows)
    assert any(abs(r['residual'])>4 for r in rows)
    assert all('ANSWER:' not in r['target'] for r in rows)
    assert all(not ladder.numerical_completion(r['source'])['complete'] for r in rows)
    # Repeating the snapshot/probe inputs gets exactly the same rows.
    second,_=m._sample_candidate_rows(state(),args())
    assert ladder.deck_fingerprint(rows)==ladder.deck_fingerprint(second)


def test_no_discrete_mode_bank_and_exact_zero_initially():
    torch=pytest.importorskip('torch')
    torch.manual_seed(300)
    for gated in (False,True):
        glass=m.ContinuousGlass(hidden=16,feature_layers=3,width=12,gated=gated)
        h=torch.randn(4,16)
        features=torch.randn(4,48)
        result,delta,prob=glass.apply(h,features)
        assert torch.equal(h,result)
        assert torch.count_nonzero(delta)==0
        assert ('gate' in str(glass.net))==gated
        assert not hasattr(glass.net,'keys')
        assert not hasattr(glass.net,'corrections')
        assert not hasattr(glass.net,'modes')


def test_continuous_residual_is_input_dependent_and_differentiable():
    torch=pytest.importorskip('torch')
    torch.manual_seed(30)
    for gated in (False,True):
        glass=m.ContinuousGlass(hidden=4,feature_layers=2,width=12,gated=gated)
        head=torch.nn.Linear(4,3,bias=False)
        head.requires_grad_(False)
        before=head.weight.detach().clone()
        h=torch.randn(1,4)
        features=torch.randn(1,8)
        optimizer=torch.optim.AdamW(glass.net.parameters(),lr=.1)
        for i in range(15):
            optimizer.zero_grad(set_to_none=True)
            v,delta,prob=glass.apply(h,features)
            loss=torch.nn.functional.cross_entropy(head(v),torch.tensor([2]))
            loss.backward();optimizer.step()
        result=glass.apply(h,features)[1]
        assert result.abs().sum().item()>0
        assert torch.equal(before,head.weight.detach())
        opposite=glass.apply(-h,-features)[1]
        assert not torch.equal(result,opposite)


def test_gate_and_ungated_receive_nearly_identical_parameter_budgets():
    a=m.ContinuousGlass(hidden=64,feature_layers=4,width=24,gated=False)
    b=m.ContinuousGlass(hidden=64,feature_layers=4,width=24,gated=True)
    n=sum(p.numel() for p in a.net.parameters());k=sum(p.numel() for p in b.net.parameters())
    assert 0<k-n<=25
    assert (k-n)/n<.01


def test_identical_stored_failure_sets_and_regression_accounting():
    rows=[{'id':str(i)} for i in range(4)]
    baseline=[{'id':str(i),'correct':i in (0,1),'output':str(i)} for i in range(4)]
    a=[{'id':str(i),'correct':i in (0,2),'output':('x' if i==1 else str(i))} for i in range(4)]
    b=[{'id':str(i),'correct':i in (0,1,2),'output':str(i)} for i in range(4)]
    result=m._compare(rows,baseline,a,b)
    assert result['A']['fixed_ids']==['2']
    assert result['A']['regressed_ids']==['1']
    assert result['B']['fixed_ids']==['2']
    assert result['B']['regressed_ids']==[]


def test_numerical_terminal_source_mandatory_and_snapshot_unchanged(tmp_path):
    torch=pytest.importorskip('torch')
    source_dir=tmp_path/'running';source_dir.mkdir()
    path=source_dir/'working.pt'
    campaign_state={'config':cfg(),'rung':5,'radius_index':4}
    campaign_state['config_hash']=ladder._hash(campaign_state['config'])
    torch.save({'schema':ladder.SCHEMA,'state':campaign_state,
                'lora':{'q_proj.lora_A':torch.zeros(1)}},path)
    source_digest=m._sha(path)
    options=SimpleNamespace(ladder_working=str(path),output=str(tmp_path/'ab'),
                            stage1_checkpoint=None,text_checkpoint=None)
    snap,meta=m._snapshot(options)
    assert snap!=path
    assert meta['snapshot_sha256']==source_digest
    assert m._sha(path)==source_digest
    assert meta['source_state']['config']['terminal_mode']=='numeric'
    # Source may advance independently; experiment must reuse old snapshot.
    torch.save({'schema':ladder.SCHEMA,'state':campaign_state,
                'lora':{'q_proj.lora_A':torch.ones(1)}},path)
    again,meta2=m._snapshot(options)
    assert again==snap
    assert meta2['snapshot_sha256']==source_digest
    assert m._sha(path)!=source_digest


def test_legacy_checkpoint_refused(tmp_path):
    torch=pytest.importorskip('torch')
    path=tmp_path/'legacy.pt'
    old=state();old['config']=dict(old['config']);old['config'].pop('terminal_mode')
    old['config_hash']=ladder._hash(old['config'])
    torch.save({'schema':ladder.SCHEMA,'state':old,'lora':{'l':torch.zeros(1)}},path)
    with pytest.raises(RuntimeError,match='NUMERIC_TERMINAL'):
        m._read_checkpoint_metadata(path)


def test_frozen_hidden_features_are_only_hidden_states():
    torch=pytest.importorskip('torch')
    states=tuple(torch.full((1,2,5),float(i)) for i in range(7))
    output=SimpleNamespace(hidden_states=states)
    features=m._features(output,4)
    assert features.shape==(1,20)
    assert torch.equal(features,torch.cat([states[i][:,-1,:] for i in (0,2,4,6)],dim=-1))
    assert features.requires_grad is False


def test_fast_round_evaluation_has_correct_and_failed_cases():
    cases=[{'id':str(i)} for i in range(100)]
    baseline={str(i):{'correct':i>=50} for i in range(100)}
    options=SimpleNamespace(round_eval_limit=30,seed=3)
    selection=m._round_subset(cases,baseline,options,0)
    assert len(selection)==30
    assert 0<sum(baseline[x['id']]['correct'] for x in selection)<30


def test_paired_training_writes_restartable_checkpoints_and_status(tmp_path,monkeypatch):
    """End-to-end control flow without a Qwen download or CUDA."""
    torch=pytest.importorskip('torch')
    src=tmp_path/'source';src.mkdir()
    path=src/'working.pt'
    info=state();info['config_hash']=ladder._hash(info['config'])
    torch.save({'schema':ladder.SCHEMA,'state':info,'lora':{'small_lora':torch.tensor([1.])}},path)
    class BB:
        def __init__(self):self.head=torch.nn.Linear(8,8,bias=False)
        def get_output_embeddings(self):return self.head
    class Zero:
        @staticmethod
        def lora_state(bb):return {'small_lora':torch.tensor([1.])}
    rows=[{'id':'edge_fail','source':'PROBLEM: 4+(-3)','target':'PROBLEM: 1','rung':0,'residual':1},
          {'id':'edge_good','source':'PROBLEM: 4+(-4)','target':'PROBLEM: 0','rung':0,'residual':0}]
    def probe(bb,tok,items,opts,*,glass=None,limit=0,show_progress=False):
        return [{'id':r['id'],'correct':r['id']=='edge_good','output':
                 r['target'] if r['id']=='edge_good' else 'PROBLEM: 2'} for r in items]
    calls=[]
    def step(glass,opt,bb,tok,row,good,args,rng):
        calls.append((glass.gated,row['id'],good['id']))
        return {'wrong_before':True,'loss':0.1}
    monkeypatch.setattr(m,'_sample_candidate_rows',lambda state,args:(rows,{'current_rung':0,
        'current_radius':0,'deck_hash':ladder.deck_fingerprint(rows)}))
    monkeypatch.setattr(m,'_load_model',lambda *x:(BB(),object(),Zero))
    monkeypatch.setattr(m,'_probe',probe)
    monkeypatch.setattr(m,'_train_paired_step',step)
    s1=tmp_path/'s1';s1.mkdir();text=tmp_path/'text';text.mkdir()
    options=SimpleNamespace(stage='run',device='cpu',stage1_checkpoint=str(s1/'best.pt'),
           text_checkpoint=str(text/'best.pt'),ladder_working=str(path),output=str(tmp_path/'ab'),
           progress_every=1,probe_limit=2,probe_per_cell=1,max_tokens=128,max_new_tokens=8,
           inner_shells=1,outer_shells=1,depth_shells=1,glass_width=12,
           feature_layers=2,round_eval_limit=0,full_eval_every=0,audit_limit=0,audit_scope='frontier',rounds=1,
           steps_per_round=3,seed=42,lr=.001,preserve_weight=1.,delta_l2=.001,gate_loss=.01)
    m._run(options)
    assert calls==[(False,'edge_fail','edge_good'),(True,'edge_fail','edge_good')]*3
    out=Path(options.output)
    assert (out/'rounds/round_00001/complete.json').exists()
    assert (out/'glass_A.pt').is_file() and (out/'glass_B.pt').is_file()
    assert m._sha(path)==m._sha(out/'source_snapshot.pt')
    first=json.loads((out/'comparison.json').read_text())
    assert first['round']==1
    assert first['summary']['baseline']['tested']==2
    m._run(options)
    assert (out/'rounds/round_00002/complete.json').exists()
    assert len(calls)==12
    assert json.loads((out/'comparison.json').read_text())['round']==2
