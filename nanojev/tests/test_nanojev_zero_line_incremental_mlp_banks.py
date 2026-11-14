"""CPU checks of standalone non-Gaussian MLP signed interval-bank comparison."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
import torch

from tools.nanojev_zero_line_incremental_mlp_banks import (
    DEFAULT_INTERVALS, IncrementalComposite, IntervalMLPBank,
    LearnedIntervalGate, exact_mask, interval_report, interval_subset,
    parse_intervals, percentage, protected_regression_audit, run, subset, train_expert, train_gate,
)
from tools.nanojev_zero_line_ladder import deck
from tools.nanojev_zero_line_radius255_probe import sha256
from tools.nanojev_addition_curriculum import target_digits


def test_requested_progression_and_contiguous_intervals():
    assert parse_intervals(DEFAULT_INTERVALS) == ((3,15),(16,32),(33,64),(65,128))
    assert parse_intervals('16:32') == ((16,32),)
    for bad in ('', '3:15,17:32', '32:16', '0:15', '3:129', 'abc:12', '2:15,14:32'):
        with pytest.raises(ValueError):
            parse_intervals(bad)


def test_mlp_banks_are_parameter_matched_without_gaussian_basis():
    from tools.nanojev_zero_line_incremental_banks import IntervalGaussianBank
    for width in (8,64,128):
        a=IntervalMLPBank(width,16,32)
        b=IntervalGaussianBank(width,16,32)
        assert not hasattr(a,'centers') and not hasattr(a,'sigma')
        assert abs(sum(p.numel() for p in a.parameters()) -
                   sum(p.numel() for p in b.parameters())) / sum(p.numel() for p in b.parameters()) < .01
        feats=torch.randn(4,width)
        result,z=a(feats)
        assert result.shape==(4,73) and z.shape==(4,)
        result.sum().backward()
        assert a.coordinate[0].weight.grad is not None


def test_exact_decoding_checks_all_digits_and_sign():
    truth=torch.tensor([-16,0,25])
    logits=torch.full((3,73),-12.)
    for i,y in enumerate(truth):
        logits[i,int(y.sign()+1)]=12.
        for place,d in enumerate(target_digits(y[None])[0]):
            logits[i,3+place*10+int(d)]=12.
    assert exact_mask(logits,truth).tolist()==[True,True,True]
    logits[0,3+0*10+6]=-20.
    logits[0,3+0*10+5]=15.
    assert exact_mask(logits,truth).tolist()==[False,True,True]


def mock_cache(values, features=None, mags=None, bands=None):
    truth=torch.tensor(values,dtype=torch.long)
    n=len(values)
    if features is None: features=torch.randn(n,8)
    if mags is None: mags=[140]*n
    if bands is None: bands=[(0,1)]*n
    base=torch.zeros(n,73)
    for i,y in enumerate(truth):
        base[i,int(y.sign()+1)]=20.
        for pos,d in enumerate(target_digits(y[None])[0]):
            base[i,3+10*pos+int(d)]=20.
    return {'features':features, 'original':base,'target':truth,
            'operands':torch.tensor([(m,values[i]-m) for i,m in enumerate(mags)]),
            'bands':bands}


def test_bank_routing_uses_only_features_not_truth():
    composite=IncrementalComposite()
    bank=IntervalMLPBank(8,16,32)
    gate=LearnedIntervalGate(8)
    with torch.no_grad():
        gate.network[-1].bias.fill_(10.)
    c=mock_cache([16,-16,0])
    a=composite.predict(c,candidate=(bank,gate,16,32))
    c['target']=-c['target']
    b=composite.predict(c,candidate=(bank,gate,16,32))
    assert torch.equal(a,b)


def test_historical_input_magnitude_guard_and_unguarded_audit():
    c=mock_cache([16,-16,16],mags=[12,128,140])
    # First pair is in original input domain; next two are not.
    assert c['operands'][0].abs().max() <= 127
    composite=IncrementalComposite()
    bank=IntervalMLPBank(8,16,32)
    gate=LearnedIntervalGate(8)
    with torch.no_grad():
        gate.network[-1].bias.fill_(10.)
        for x in bank.output.parameters():x.zero_()
    raw=composite.predict(c,candidate=(bank,gate,16,32))
    safe=composite.predict(c,candidate=(bank,gate,16,32),guarded=True)
    assert torch.equal(safe[0],c['original'][0])
    assert not torch.equal(raw[0],c['original'][0])
    assert torch.equal(safe[1:],raw[1:])
    assert percentage(safe[:1],c['target'][:1])==1.


def test_protected_near_zero_rejects_a_bank_that_breaks_old_successes():
    torch.manual_seed(11)
    c=mock_cache([0,1,-1,2,-2,16,-16])
    composite=IncrementalComposite()
    bank=IntervalMLPBank(8,16,32)
    gate=LearnedIntervalGate(8)
    with torch.no_grad():
        gate.network[-1].bias.fill_(10.)
        for param in bank.output.parameters(): param.zero_()
    audit=protected_regression_audit(composite,c,((16,32),),(bank,gate,16,32))
    assert audit['no_regressions'] is False
    assert audit['regions'][0]['interval']==[0,2]
    assert audit['regions'][0]['newly_wrong'] >= 3


def test_protected_previous_bank_interval_is_separately_checked():
    torch.manual_seed(12)
    c=mock_cache([0,1,-1,16,-16])
    composite=IncrementalComposite()
    prior_bank=IntervalMLPBank(8,16,16)
    prior_gate=LearnedIntervalGate(8)
    with torch.no_grad(): prior_gate.network[-1].bias.fill_(-20.)
    composite.promoted.append((prior_bank,prior_gate,16,16))
    newer_bank=IntervalMLPBank(8,17,32)
    newer_gate=LearnedIntervalGate(8)
    with torch.no_grad():
        newer_gate.network[-1].bias.fill_(10.)
        for param in newer_bank.output.parameters(): param.zero_()
    audit=protected_regression_audit(composite,c,((16,16),(17,32)),
                                     (newer_bank,newer_gate,17,32))
    assert len(audit['regions'])==2
    assert audit['regions'][1]['interval']==[16,16]
    assert audit['regions'][1]['newly_wrong'] >= 2


def test_exact_cell_gate_fails_closed_even_with_high_aggregate_accuracy():
    targets=[d for d in (16,-16) for b in range(4) for o in (-1,1)]
    bands=[(b,o) for d in (16,-16) for b in range(4) for o in (-1,1)]
    c=mock_cache(targets,bands=bands)
    # Correct predictions but only one example per signed cell: count 6 fails.
    report=interval_report(c['original'],c,16,16,.8,6)
    assert report['accuracy']==1.
    assert report['certified'] is False
    assert report['failing_cells']==16
    assert interval_report(c['original'],c,16,16,.8,1)['certified']


def test_new_bank_training_changes_bank_not_previous_weights():
    torch.manual_seed(7)
    c=mock_cache([16,17,-16,-17,30,32,-31,-32])
    b=IntervalMLPBank(8,16,32)
    preserved={k:v.clone() for k,v in c.items() if isinstance(v,torch.Tensor)}
    history=train_expert(b,c,c,16,32,steps=2,batch_size=8,lr=.001,probe_every=1,seed=5)
    assert len(history)==2
    assert all(torch.equal(v,c[k]) for k,v in preserved.items())


def test_router_does_not_access_oracle_at_inference_and_handles_no_fixable_case():
    c=mock_cache([16,-16,32,-32])
    incumbent=IncrementalComposite()
    bank=IntervalMLPBank(8,16,32)
    gate=LearnedIntervalGate(8)
    result=train_gate(gate,bank,incumbent,c,c,steps=2,batch_size=8,lr=.001,seed=4)
    assert result['status']=='NO_FIXABLE_TRAIN_EXAMPLES'


def test_router_training_can_distinguish_new_errors_from_old_mastery():
    torch.manual_seed(4)
    old=mock_cache([0]*12,features=torch.tensor([[-3.]+[0.]*7]*12))
    new=mock_cache([16]*12,features=torch.tensor([[3.]+[0.]*7]*12))
    corrected=new['original'].clone()
    new['original']=torch.zeros_like(corrected)  # synthetic incumbent errors

    class AlreadyLearnedBank(torch.nn.Module):
        def forward(self, features):
            return corrected[:len(features)].clone(), torch.ones((len(features),),device=features.device)

    # Bank inference only sees features; constant template is synthetic.
    gate=LearnedIntervalGate(8)
    result=train_gate(gate,AlreadyLearnedBank(),IncrementalComposite(),old,new,
                      steps=120,batch_size=16,lr=.01,seed=5,protected=old)
    assert result['status']=='TRAINED'
    assert result['improvable_train_cards']==12
    with torch.inference_mode():
        assert torch.all(gate(new['features'])>0)
        assert torch.all(gate(old['features'])<0)


class FakePerfectModel(torch.nn.Module):
    """Exact synthetic source ONLY for smoke tests, not a research result."""
    def __init__(self):
        super().__init__()
        self.query=torch.nn.Parameter(torch.zeros(8))
        self.norm=torch.nn.Identity()

    def forward(self, inputs):
        a=inputs[:,0].float();b=inputs[:,1].float()
        features=torch.stack((a/500,b/500,(a+b)/500,a*b/250000,
                              a.sign(),b.sign(),a.abs()/500,b.abs()/500),dim=-1)
        self.norm(features)
        y=inputs.sum(-1)
        logits=torch.full((len(y),73),-10.,device=inputs.device)
        logits.scatter_(1,(y.sign()+1).long().unsqueeze(-1),20.)
        digits=target_digits(y)
        for pos in range(7):
            logits.scatter_(1,(digits[:,pos]+3+pos*10).unsqueeze(-1),20.)
        return {'sign':logits[:,:3], 'digits':logits[:,3:].reshape(-1,7,10)}


def test_end_to_end_smoke_never_mutates_source_and_cannot_promote_fake_gain(tmp_path):
    root=tmp_path/'source';root.mkdir()
    checkpoint=root/'checkpoint.pt';torch.save({'certified':True},checkpoint)
    original=checkpoint.read_bytes()
    model=FakePerfectModel()
    source={'checkpoint':str(checkpoint),'checkpoint_sha256':sha256(checkpoint),'step':4800}
    args=SimpleNamespace(ladder_output=root,output=tmp_path/'out',device='cpu',gaussian_output=None,phase='train',
        intervals='16:16',steps_per_bank=2,router_steps=2,probe_every=1,batch_size=8,
        eval_batch_size=256,train_per_cell=1,dev_per_cell=1,ood_per_cell=1,
        min_cell_count=1,min_cell_rate=.8,lr=.001,router_lr=.001,
        seed=17,cpu_threads=2)
    with mock.patch('tools.nanojev_zero_line_incremental_mlp_banks.load_certified_model',
                    return_value=(model,source)):
        with mock.patch('tools.nanojev_zero_line_incremental_mlp_banks.deck',return_value=deck(1)):
            hist=run(args)
    assert len(hist)==1 and not hist[0]['eligible']
    assert hist[0]['router']['status']=='NO_FIXABLE_TRAIN_EXAMPLES'
    assert checkpoint.read_bytes()==original
    assert not (tmp_path/'out'/'bank_16_16'/'promoted.pt').exists()
    assert (tmp_path/'out'/'bank_16_16'/'candidate.pt').exists()
    summary=json.loads((tmp_path/'out'/'result.json').read_text())
    assert summary['promoted_intervals']==[]
    assert summary['test_untouched']
    # Source and output cannot overlap; existing output cannot be overwritten.
    with mock.patch('tools.nanojev_zero_line_incremental_mlp_banks.load_certified_model',
                    return_value=(model,source)):
        with pytest.raises(FileExistsError):run(args)


def test_mlp_bank_inference_uses_features_only_not_known_residual():
    bank=IntervalMLPBank(8,16,32)
    c=mock_cache([16,-16,0], features=torch.randn(3,8))
    a=bank(c['features'])[0].clone()
    c['target']=-c['target']
    assert torch.equal(a,bank(c['features'])[0])


def test_compare_rejects_incompatible_sources_and_training_arguments(tmp_path):
    from tools.nanojev_zero_line_incremental_mlp_banks import compare_outputs,SCHEMA
    m=tmp_path/'mlp';g=tmp_path/'gauss';m.mkdir();g.mkdir()
    p={k:5 for k in ('steps_per_bank','router_steps','probe_every','batch_size','eval_batch_size',
      'train_per_cell','dev_per_cell','ood_per_cell','min_cell_count','min_cell_rate',
      'lr','router_lr','seed')}
    p['intervals']='16:16'
    a={'schema':SCHEMA,'source_sha256':'same','intervals':[[16,16]],
       'train_bands':[[128,159]],'ood_bands':[[256,319]],'parameters':p}
    b=dict(a);b['schema']='nanojev.zero-line-incremental-interval-banks.v1'
    for path,obj in ((m,a),(g,b)):
        (path/'config.json').write_text(json.dumps(obj))
        (path/'result.json').write_text(json.dumps({'history':[]}))
    assert compare_outputs(m,g)['summary']['comparison_verified']
    b['source_sha256']='different'
    (g/'config.json').write_text(json.dumps(b))
    with pytest.raises(ValueError,match='SOURCE_MISMATCH'):
        compare_outputs(m,g)
    b['source_sha256']='same';b['parameters']=dict(p,seed=123)
    (g/'config.json').write_text(json.dumps(b))
    with pytest.raises(ValueError,match='PARAMETER_MISMATCH:seed'):
        compare_outputs(m,g)


def test_comparison_metrics_are_both_isolated_and_routed(tmp_path):
    from tools.nanojev_zero_line_incremental_mlp_banks import compare_outputs,SCHEMA
    m=tmp_path/'mlp';g=tmp_path/'gaussian';m.mkdir();g.mkdir()
    params=dict(intervals='16:16',steps_per_bank=2,router_steps=2,probe_every=1,batch_size=8,
                eval_batch_size=256,train_per_cell=1,dev_per_cell=1,ood_per_cell=1,
                min_cell_count=1,min_cell_rate=.8,lr=.001,router_lr=.001,seed=17)
    def item(value):
        return {'interval':[16,16], 'trained_cards':16,'dev_cards':16,'ood_cards':16,
                'specialist':{'dev':{'accuracy':value,'certified':False},'ood':{'accuracy':value,'certified':False}},
                'candidate':{'dev':{'unguarded':{'accuracy':value/2}},
                             'ood':{'unguarded':{'accuracy':value/2}},
                             'retention':{'unguarded':1.}},
                'parameters':{'specialist':50000,'router':120},'eligible':False}
    for root, schema,value in ((m,SCHEMA,.8),(g,'nanojev.zero-line-incremental-interval-banks.v1',.5)):
        (root/'config.json').write_text(json.dumps({'schema':schema,'source_sha256':'same',
            'intervals':[[16,16]],'train_bands':[[128,159]],'ood_bands':[[256,319]],'parameters':params}))
        (root/'result.json').write_text(json.dumps({'history':[item(value)]}))
    report=compare_outputs(m,g)
    assert report['rows'][0]['mlp_minus_gaussian']['isolated_dev']==pytest.approx(.3)
    assert report['rows'][0]['mlp_minus_gaussian']['routed_ood']==pytest.approx(.15)
    assert (m/'comparison.json').exists()
