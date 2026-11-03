"""Source-grounded smoke and mathematical gates for the learned log-difference head."""
import copy
import json
import math
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip('torch')

from tools import nanojev_log_difference_smoke as mod


def test_direct_logarithm_equivalence_and_gradient():
    log_a=torch.tensor([2., 0.1, -3.],dtype=torch.float64,requires_grad=True)
    log_b=torch.tensor([1., -0.3, -4.],dtype=torch.float64,requires_grad=True)
    real,imag,null,side=mod.decode_log_pair(torch,log_a,log_b)
    reference=.5*torch.log(torch.exp(2*log_a)-torch.exp(2*log_b))
    torch.testing.assert_close(real,reference,rtol=1e-12,atol=1e-12)
    assert bool((imag==0).all()) and bool((side==1).all()) and not bool(null.any())
    real.sum().backward()
    assert torch.isfinite(log_a.grad).all() and torch.isfinite(log_b.grad).all()
    assert bool((log_a.grad>0).all()) and bool((log_b.grad<0).all())


def test_exact_cancel_is_null_not_finite_zero_answer():
    a=torch.tensor([5.,1000.,-math.inf,0.],dtype=torch.float64)
    b=torch.tensor([5.,1000.,-math.inf,-math.inf],dtype=torch.float64)
    re,im,null,side=mod.decode_log_pair(torch,a,b)
    assert null[:3].tolist()==[True,True,True]
    assert all(math.isinf(v) and v<0 for v in re[:3].tolist())
    assert math.isnan(im[0].item())
    assert re[3].item()==0. and not bool(null[3]) and side[3].item()==1.


def test_complex_continuation_negative_difference_has_pi_half_phase():
    re,im,null,side=mod.decode_log_pair(
        torch,torch.tensor([0.],dtype=torch.float64),
        torch.tensor([1.],dtype=torch.float64))
    assert math.isfinite(re.item()) and abs(im.item()-math.pi/2)<1e-14
    assert not bool(null.item()) and side.item()==-1


def test_near_null_stable_log1mexp():
    gaps=[1e-4,1e-8,1e-12,1e-15]
    for d in gaps:
        val=mod.log1mexp(torch,torch.tensor([-2*d],dtype=torch.float64)).item()
        assert math.isfinite(val)
        assert abs(val-(math.log(2*d)-d))<1e-8
    # Huge log magnitudes must not become infinite merely because exp overflows.
    re,_,null,_=mod.decode_log_pair(torch,torch.tensor([1000000.],dtype=torch.float64),
                                    torch.tensor([999999.9],dtype=torch.float64))
    assert math.isfinite(re.item()) and not bool(null.item())


def test_loggap_smaller_than_float64_resolution_is_decodable():
    g=torch.tensor([-1200.,-60.,-20.],dtype=torch.float64,requires_grad=True)
    result=mod.half_logdiff_from_log_gap(torch,g)
    torch.testing.assert_close(result,0.5*(math.log(2.)+g),rtol=1e-12,atol=1e-10)
    result.sum().backward()
    assert torch.isfinite(g.grad).all()
    torch.testing.assert_close(g.grad,torch.full_like(g,.5))


def test_internal_math_checks():
    assert all(mod._check_mathematics(torch).values())


def test_zero_line_generator_and_all_radius_rows_are_acquired():
    args=mod.parser().parse_args(['--radius','3','--train-cap','64','--outer-count','48'])
    decks=mod.data_decks(args)
    assert set(decks)=={'train','outside'}
    assert len(decks['train'])>64
    assert set(decks['train']).isdisjoint(decks['outside'])
    assert any(mod.answer(*v)==0 for v in decks['train'])
    assert {int(np.sign(mod.answer(*v))) for v in decks['train']}=={-1,0,1}
    assert decks==mod.data_decks(args)
    assert mod.digest_points(decks['train'])==mod.digest_points(mod.data_decks(args)['train'])


def test_numeric_features_are_only_unary_transforms_of_source_operands():
    point=(3,4,20,10)
    feat=mod.numeric_features([point])
    assert feat.shape==(1,8)
    np.testing.assert_equal(feat[0,:4],point)
    np.testing.assert_allclose(feat[0,4:],np.log1p(point),rtol=1e-6)


def test_matched_head_count_and_initial_weights():
    torch.manual_seed(12)
    a=mod.make_net(torch,8,24)
    b=copy.deepcopy(a)
    assert sum(p.numel() for p in a.parameters())==sum(p.numel() for p in b.parameters())
    x=torch.randn(5,8)
    da,_=mod.predict(torch,a,x,'direct',25.)
    db,rep=mod.predict(torch,b,x,'log_difference',25.)
    assert da.shape==db.shape==(5,)
    assert rep['gap'].min().item()>0
    assert (rep['log_a']>rep['log_b']).all()
    assert torch.isfinite(da).all() and torch.isfinite(db).all()


def test_training_smoke_reports_provenance_and_no_frontier_leak(tmp_path):
    output=tmp_path/'run'
    args=mod.parser().parse_args(['--radius','3','--train-cap','64',
        '--outer-count','48','--steps','55','--hidden','24',
        '--batch-size','32','--output',str(output)])
    report=mod.run(args)
    assert report['status']=='PASS'
    assert report['learning_status']=='ALL_IMPROVED'
    assert report['acquisition_policy'].startswith('all radius samples trained')
    assert all(report['mathematical_checks'].values())
    assert all(v['nonfinite_count']==0 for arm in report['metrics'].values() for v in arm.values())
    assert report['source_sha256']['smoke']==mod.hashlib.sha256(Path(mod.__file__).read_bytes()).hexdigest()
    assert report['metrics']['log_difference']['train']['b_contribution_mean_abs']>0
    assert report['metrics']['log_difference']['outside']['b_shuffled_mae']>=0
    for arm in ('log_difference','log_cancellation'):
        assert sum(report['paired']['outside'][arm][k] for k in
                   ('direct_better','candidate_better','ties'))==report['data']['outside']['count']
    assert report['metrics']['log_cancellation']['outside']['raw_float64_pair_unresolvable_fraction']>=0
    assert (output/'report.json').exists()
    assert all((output/(name+'.pt')).exists() for name in ('direct','log_difference','log_cancellation'))
    assert not (output/'validation_predictions.csv').exists()
    for name in ('train','outside'):
        assert (output/(name+'_predictions.csv')).exists()
    reloaded=json.loads((output/'report.json').read_text())
    assert reloaded['data']==report['data']


def test_deterministic_experiment_seed(tmp_path):
    def run(name):
        args=mod.parser().parse_args(['--radius','3','--train-cap','64',
             '--outer-count','48','--steps','25','--hidden','16',
             '--batch-size','32','--output',str(tmp_path/name)])
        return mod.run(args)
    a,b=run('a'),run('b')
    assert a['data']==b['data']
    assert a['metrics']==b['metrics']
    assert a['paired']==b['paired']
    assert a['acquired_history']==b['acquired_history']


def test_reject_invalid_policy(tmp_path):
    args=mod.parser().parse_args(['--steps','0','--output',str(tmp_path/'bad')])
    with pytest.raises(ValueError,match='INVALID_SMOKE_ARGUMENT'):
        mod.run(args)
