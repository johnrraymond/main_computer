"""Offline tests for full-token capture, right-aligned design, and true L1 selection."""
from __future__ import annotations
import json
from pathlib import Path
import sys

import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import nanojev_full_activation_sparse_hierarchy as exp


class Tokenizer:
    def __call__(self, text, add_special_tokens=False):
        assert add_special_tokens is False
        return {'input_ids':[ord(c) for c in text]}

class FakeModel:
    def __call__(self, input_ids, attention_mask, output_hidden_states, use_cache):
        assert output_hidden_states and not use_cache
        ids=input_ids.float().unsqueeze(-1)
        a=torch.cat([ids,ids+1,ids*2],dim=-1)
        b=a*3
        return type('Result', (), {'hidden_states':(a,b)})()


def save(root,kind,key,number,identity,scale=1.0):
    text=exp.multi.prompt(kind,key)
    md,hidden=exp.capture_full(torch,FakeModel(),Tokenizer(),text,'cpu',128,'float32')
    hidden=hidden*scale
    md['identity']=identity
    p=exp.full_record_path(root,kind,key)
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('wb') as file:
        np.savez_compressed(file,metadata=json.dumps(md),hidden_states=hidden)
    return md,hidden


def test_capture_preserves_each_layer_and_token():
    md,hidden=exp.capture_full(torch,FakeModel(),Tokenizer(),'Number: 12','cpu',128,'float32')
    assert hidden.shape==(2,len('Number: 12'),3)
    assert hidden[0,-1,0]==ord('2')
    assert hidden[0,-2,0]==ord('1')
    assert hidden[1,-1,0]==ord('2')*3
    assert not np.array_equal(hidden[:,-1,:],hidden[:,-2,:])
    assert md['input_token_count']==len('Number: 12')
    with pytest.raises(RuntimeError,match='PROMPT_TOKENS'):
        exp.capture_full(torch,FakeModel(),Tokenizer(),'Number: 12','cpu',4,'float16')


def test_design_uses_all_token_states_with_explicit_right_alignment(tmp_path):
    identity={'model':'fake'}
    values=[1,11]
    save(tmp_path,'decimal',1,1,identity)
    save(tmp_path,'decimal',11,11,identity)
    x,layout=exp.design_matrix(tmp_path,'decimal',values,[0,1],identity)
    assert layout['slots']==len('Number: 11')
    assert x.shape==(2,2*len('Number: 11')*3)
    restored=x.reshape(2,2,len('Number: 11'),3)
    assert np.all(restored[0,:,0,:]==0)  # one shorter prompt, left padded
    assert np.allclose(restored[0,0,-1],[ord('1'),ord('1')+1,ord('1')*2])
    assert np.allclose(restored[1,0,-2],[ord('1'),ord('1')+1,ord('1')*2])
    assert np.allclose(restored[1,0,-1],[ord('1'),ord('1')+1,ord('1')*2])
    z,columns,means,scales=exp.standardize_design(x)
    assert z.shape[0]==2 and len(columns)==z.shape[1]
    assert np.allclose(z*scales+means,x[:,columns],atol=1e-4)


def test_independent_representation_records_are_not_mixed(tmp_path):
    identity={'model':'fake'}
    n=2000
    save(tmp_path,'decimal',n,n,identity,scale=1.)
    q=exp.multi.quantize(n)
    save(tmp_path,'logbar',q,n,identity,scale=3.)
    save(tmp_path,'paired',n,n,identity,scale=5.)
    for kind, key in [('decimal',n),('logbar',q),('paired',n)]:
        text=exp.multi.prompt(kind,key)
        md,raw=exp.read_full(tmp_path,kind,key,text,identity)
        assert raw.shape[1]==len(text)
        assert md['prompt']==text
    a,_=exp.design_matrix(tmp_path,'decimal',[n],[0],identity)
    b,_=exp.design_matrix(tmp_path,'logbar',[n],[0],identity)
    c,_=exp.design_matrix(tmp_path,'paired',[n],[0],identity)
    assert a[0,-1]==ord('0')*2
    assert not np.array_equal(a,b) and not np.array_equal(b,c)


def test_lasso_selects_sparse_features_and_uses_no_pooling():
    rng=np.random.default_rng(17)
    x=rng.normal(size=(70,42)).astype(np.float32)
    y=2.8*x[:,2]-1.6*x[:,19]+0.1*x[:,36]
    fit=exp.lasso_sparse(x,y,0.05)
    assert fit['kkt_passed']
    assert fit['nonzero'] > 0
    assert {2,19}.issubset(set(fit['indices']))
    estimate=x[:,fit['indices']]@fit['weights']+fit['intercept']
    assert np.mean(np.abs(estimate-y))<0.2
    assert np.count_nonzero(np.abs(fit['weights'])>1e-10)==fit['nonzero']


def test_lasso_constant_and_singleton_heads():
    x=np.arange(15,dtype=np.float32).reshape(5,3)
    for count in (1,5):
        f=exp.lasso_sparse(x[:count],np.full(count,7.),0.1)
        assert f['nonzero']==0 and f['intercept']==7.
    factory=exp.SparseHeadFactory(x,0.2)
    result=factory.fit('constant',np.array([True,False,False,False,False]),np.array([9.]))
    assert np.all(result==9.)
    assert factory.heads['constant']['nonzero']==0


def test_5pct_is_per_actual_not_range():
    real=np.array([-2_000_000.,-10.,-1.,0.,1.,10.,2_000_000.])
    estimated=np.array([-1_901_000.,-9.6,-.96,0.,1.06,10.2,2_095_000.])
    out,passing,_=exp.hier.metrics(real,estimated,(200,))
    assert not out['all_probes_pass']
    assert passing.tolist()==[True,True,True,True,False,True,True]


def test_cli_selftest_and_prepare_only(tmp_path,capsys):
    exp.main(['--self-test'])
    assert 'full_activation_lasso_alpha_self_test_passed' in capsys.readouterr().out
    exp.main(['--prepare-only','--output-root',str(tmp_path/'no_capture')])
    plan=json.loads(capsys.readouterr().out.strip())
    assert plan['numbers']==507 and plan['tasks']==1253
    assert not (tmp_path/'no_capture').exists()
    assert len(exp.DEFAULT_ALPHA_RATIOS)==12


def test_manifest_record_corruption_detected(tmp_path):
    identity={'model':'fake'}
    save(tmp_path,'decimal',11,11,identity)
    with pytest.raises(RuntimeError,match='RECORD_DRIFT'):
        exp.read_full(tmp_path,'decimal',11,'Number: 21',identity)


def test_capture_then_sparse_decode_end_to_end(tmp_path,monkeypatch):
    # Tiny complete offline run verifies record layout, three independent
    # representations, classifier/router/experts, and persisted coefficients.
    values=[-500,-250,-10,-1,0,1,10,250,500]
    monkeypatch.setattr(exp.multi,'grid',lambda limit: values)
    def loader(_model,_args):
        return torch,FakeModel(),Tokenizer(),'cpu',{'source':'offline-fake'}
    monkeypatch.setattr(exp.cassette,'load_source',loader)
    root=tmp_path/'full'
    common=['--output-root',str(root),'--layers','0','--alpha-ratios','0.25',
            '--max-regimes','2','--report-every','100']
    exp.main(common+['--capture-only'])
    assert len(list((root/'records').glob('*.npz')))==27
    exp.main(common+['--analyze-only'])
    data=json.loads((root/'lasso_penalty_summary.json').read_text(encoding='utf8'))
    assert len(data['results'])==3
    assert {row['representation'] for row in data['results']}==set(exp.KINDS)
    assert all(row['kkt_verified'] for row in data['results'])
    assert all(row['coefficient_count']>=0 for row in data['results'])
    assert (root/'lasso_penalty_predictions.csv').exists()
    assert all((root/'sparse_models'/kind/'alpha_0p25_stage_1'/'heads.json').is_file()
               for kind in exp.KINDS)


def test_penalty_strength_controls_sparsity_not_selected_count():
    rng=np.random.default_rng(9)
    x=rng.normal(size=(80,24)).astype(np.float32)
    y=2*x[:,2]-1.5*x[:,9]+0.6*x[:,17]+rng.normal(scale=0.02,size=80)
    heavy=exp.lasso_sparse(x,y,0.75)
    medium=exp.lasso_sparse(x,y,0.25)
    light=exp.lasso_sparse(x,y,0.03)
    assert heavy['alpha']>medium['alpha']>light['alpha']
    assert heavy['nonzero']<=medium['nonzero']<=light['nonzero']
    assert light['nonzero']>heavy['nonzero']
    assert all(z['kkt_passed'] for z in (heavy,medium,light))

def test_alpha_argument_rejects_invalid_values():
    x=np.arange(18,dtype=np.float32).reshape(6,3)
    y=np.arange(6,dtype=float)
    for alpha in (-1,0,1.2):
        with pytest.raises(ValueError,match='ALPHA'):
            exp.lasso_sparse(x,y,alpha)
    args=exp.parser().parse_args(['--alpha-ratios','0.5,0.1'])
    assert args.alpha_ratios=='0.5,0.1'


def test_solver_warning_is_captured_retried_and_not_printed(monkeypatch, capsys):
    import warnings
    from sklearn.linear_model import Lasso
    from sklearn.exceptions import ConvergenceWarning
    original = Lasso.fit
    counter = {'calls':0}
    def first_warning(self, x, y, *args, **kwargs):
        result = original(self, x, y, *args, **kwargs)
        counter['calls']+=1
        if counter['calls']==1:
            warnings.warn('simulated initial nonconvergence', ConvergenceWarning)
        return result
    monkeypatch.setattr(Lasso, 'fit', first_warning)
    rng=np.random.default_rng(33)
    x=rng.normal(size=(40,12)).astype('float32')
    y=x[:,2]*3-x[:,9]
    out=exp.lasso_sparse(x,y,0.2,solver_max_iter=1000)
    assert out['kkt_passed'] is True
    assert out['solver_warning_count']==1
    assert out['solver_fit_calls']>=2
    assert counter['calls']>=2
    assert 'ConvergenceWarning' not in capsys.readouterr().err


def test_nonconverged_solver_does_not_qualify(monkeypatch, capsys):
    import warnings
    from sklearn.linear_model import Lasso
    from sklearn.exceptions import ConvergenceWarning
    original = Lasso.fit
    def never_converges(self, x, y, *args, **kwargs):
        result = original(self,x,y,*args,**kwargs)
        warnings.warn('simulated persistent nonconvergence',ConvergenceWarning)
        return result
    monkeypatch.setattr(Lasso, 'fit', never_converges)
    rng=np.random.default_rng(10)
    x=rng.normal(size=(40,12)).astype('float32')
    y=x[:,2]*3-x[:,9]
    with pytest.raises(exp.SparseFitFailure, match='SPARSE_HEAD_SOLVER_NOT_CONVERGED') as raised:
        exp.lasso_sparse(x,y,0.2,solver_max_iter=1000)
    assert raised.value.diagnostics['solver_warning_count']>=1
    assert 'ConvergenceWarning' not in capsys.readouterr().err


def test_failed_alpha_stays_invalid_and_later_alpha_continues(tmp_path,monkeypatch,capsys):
    values=[-500,-250,-10,-1,0,1,10,250,500]
    monkeypatch.setattr(exp.multi,'grid',lambda limit: values)
    monkeypatch.setattr(exp.cassette,'load_source',
        lambda _model,_args:(torch,FakeModel(),Tokenizer(),'cpu',{'source':'offline-fake'}))
    base=exp.lasso_sparse
    def fail_one_ratio(x,y,alpha_ratio,**kwargs):
        if alpha_ratio==0.25:
            raise exp.SparseFitFailure('SIMULATED_SOLVER_FAILURE',alpha_ratio=alpha_ratio)
        return base(x,y,alpha_ratio,**kwargs)
    monkeypatch.setattr(exp,'lasso_sparse',fail_one_ratio)
    root=tmp_path/'recovery'
    options=['--output-root',str(root),'--layers','0',
             '--representations','decimal','--alpha-ratios','0.25,0.5',
             '--max-regimes','2','--report-every','100']
    exp.main(options+['--capture-only'])
    exp.main(options+['--analyze-only'])
    report=json.loads((root/'lasso_penalty_summary.json').read_text())
    assert report['failed_stages']==1
    assert report['failures'][0]['reason']=='SIMULATED_SOLVER_FAILURE'
    assert report['failures'][0]['details']['head']=='zero'
    assert report['completed_penalty_points']==1
    assert all(r['kkt_verified'] for r in report['results'])
    assert {r['alpha_ratio'] for r in report['results']}=={0.5}
    assert (root/'lasso_penalty_failures.json').is_file()
    assert 'lasso_penalty_hierarchy_complete' in capsys.readouterr().out
