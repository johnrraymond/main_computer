"""Offline coverage for two-number full-token LASSO experiment."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pytest
import torch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_two_number_sparse_decoder as exp


class FakeTokenizer:
    def __call__(self,text,add_special_tokens=False):
        assert not add_special_tokens
        return {'input_ids':[ord(c) for c in text]}


class FakeModel:
    def __call__(self,input_ids,attention_mask,output_hidden_states,use_cache):
        assert output_hidden_states and not use_cache
        u=input_ids.float().unsqueeze(-1)
        a=torch.cat((u,u**2/300,u*3),dim=-1)
        return type('Result',(),{'hidden_states':(a,2*a)})()


def test_pair_generator_balanced_complete_reproducible():
    pairs=exp.generate_pairs(2000)
    assert len(pairs)==2000
    assert pairs==exp.generate_pairs(2000)
    assert pairs!=exp.generate_pairs(2000,seed=12)
    assert set(exp.sparse.multi.grid())=={a for a,_ in pairs}
    assert set(exp.sparse.multi.grid())=={b for _,b in pairs}
    assert (2_000_000,-3) in pairs and (-3,2_000_000) in pairs
    assert (0,0) in pairs
    assert sum(a*b<0 for a,b in pairs)>700
    assert sum(max(abs(a),abs(b))/max(1,min(abs(a),abs(b)))>=1000 for a,b in pairs)>300
    with pytest.raises(ValueError,match='EVEN'):
        exp.generate_pairs(21)


def test_one_newline_in_each_prompt_and_no_position_label():
    pair=(2000,-3)
    assert exp.prompt('decimal',pair)=='2000\n-3'
    assert exp.prompt('logbar',pair).count('\n')==1
    assert exp.prompt('paired',pair).startswith('2000, ')
    assert exp.prompt('paired',pair).split('\n')[1].startswith('-3, ')
    assert exp.prompt('paired',pair)!=exp.prompt('paired',(-3,2000))
    assert len(exp.prompt('logbar',pair).splitlines())==2
    with pytest.raises(ValueError):exp.prompt('missing',pair)


def test_joint_pass_rule_relative_to_each_actual_and_exact_zero():
    pairs=[(2_000_000,-3),(-1,0),(7,7),(100,-100),(0,0)]
    pred=np.array([[1_970_000.,-2.86],[-1.02,0.],[7.2,7.2],[104,-105],[0,0]])
    score,passing,rel=exp.joint_metrics(pairs,pred)
    assert score['all_pairs_pass']
    assert score['both_within_5pct_count']==5
    pred[0,1]=-2.8 # 6.67% error for -3, despite tiny absolute error
    score,passing,rel=exp.joint_metrics(pairs,pred)
    assert score['both_within_5pct_count']==4
    assert not passing[0,1] and passing[0,0]
    pred[1,1]=.00001
    score,passing,rel=exp.joint_metrics(pairs,pred)
    assert not score['zero_exact'] and not passing[1,1]


def test_plan_dedupes_actual_prompts_and_preserves_numeric_labels(tmp_path):
    args=exp.parser().parse_args(['--pair-count','30','--output-root',str(tmp_path)])
    plan,tasks=exp.plan(args)
    assert len(plan['pairs'])==30
    assert len(plan['task_digests']['decimal'])==30
    assert len(plan['task_digests']['paired'])==30
    assert len(tasks)<=90
    assert any(a==b for a,b in plan['pairs'])
    assert plan['prompt_format'].startswith('number1')
    assert all(a in exp.sparse.multi.grid() and b in exp.sparse.multi.grid() for a,b in plan['pairs'])


def test_capture_resume_and_unaveraged_matrix(tmp_path,monkeypatch,capsys):
    def fake_loader(_model,_args):
        return torch,FakeModel(),FakeTokenizer(),'cpu',{'source':'synthetic'}
    monkeypatch.setattr(exp.sparse.cassette,'load_source',fake_loader)
    root=tmp_path/'pairrecords'
    common=['--pair-count','20','--output-root',str(root),
            '--layers','0,1','--report-every','100']
    exp.main(common+['--capture-only'])
    settings=json.loads((root/'manifest.json').read_text())
    identity=json.loads((root/'model_identity.json').read_text())
    total=len(set((k,d) for k,items in settings['task_digests'].items() for d in items))
    assert len(list((root/'records').glob('*.npz')))==total
    for kind in exp.KINDS:
        x,layout=exp.matrix(root,kind,settings,[0,1],identity)
        assert x.shape[0]==20 and layout['pooling']=='none'
        assert x.shape[1]==2*layout['slots']*3
        # Distinct earlier tokens are retained rather than averaging into one.
        assert x.reshape(20,2,layout['slots'],3).shape[2]>1
    exp.main(common+['--capture-only','--resume'])
    assert 'two_number_capture_already_complete' in capsys.readouterr().out
    # One removed record is recaptured when --resume is requested.
    path=next((root/'records').glob('*.npz'))
    path.unlink()
    exp.main(common+['--capture-only','--resume'])
    assert path.exists()
    # A second run with incompatible pair grid is explicitly rejected.
    with pytest.raises(RuntimeError,match='MANIFEST_DRIFT'):
        exp.capture(exp.parser().parse_args(['--pair-count','30',
                    '--output-root',str(root),'--resume']))


def test_synthetic_two_head_analysis_outputs(tmp_path,monkeypatch):
    monkeypatch.setattr(exp.sparse.cassette,'load_source',lambda *_: (
        torch,FakeModel(),FakeTokenizer(),'cpu',{'source':'synthetic'}))
    root=tmp_path/'endtoend'
    common=['--pair-count','20','--output-root',str(root),'--layers','0',
            '--alpha-ratios','0.5','--max-regimes','2','--report-every','100']
    exp.main(common+['--capture-only'])
    exp.main(common+['--analyze-only'])
    summary=json.loads((root/'two_number_summary.json').read_text())
    assert summary['pair_count']==20 and summary['no_token_averaging']
    assert (root/'two_number_fit_failures.json').exists()
    assert (root/'two_number_fit_curve.csv').exists() or summary['failures']
    if summary['results']:
        assert {r['representation'] for r in summary['results']}<=set(exp.KINDS)
        assert all(r['fitted_heads']>=4 for r in summary['results'])
        assert all(r['all_heads_converged'] for r in summary['results'])
        assert (root/'two_number_predictions.csv').exists()


def test_plan_only_performs_no_inference_or_file_write(tmp_path,capsys):
    exp.main(['--prepare-only','--pair-count','40','--output-root',str(tmp_path/'out')])
    out=json.loads(capsys.readouterr().out.strip())
    assert out['pairs']==40
    assert len(out['examples'])==3
    assert not (tmp_path/'out').exists()
