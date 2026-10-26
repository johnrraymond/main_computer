"""Scale-invariance checks: exact signed zero, proportional error, no inference oracle."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from tools import nanojev_zero_line_scale_invariance as s
from tools.nanojev_addition_curriculum import AdditionNet, target_digits


def fixed_logits(nums):
    v=torch.as_tensor(nums,dtype=torch.long)
    logits=torch.full((len(v),73),-12.,dtype=torch.float32)
    logits[torch.arange(len(v)),v.sign()+1]=12.
    digs=target_digits(v)
    for i in range(7):logits[torch.arange(len(v)),3+10*i+digs[:,i]]=12.
    return logits


class RecordingBase(nn.Module):
    def __init__(self):
        super().__init__();self.query=nn.Parameter(torch.zeros(2))
        self.norm=nn.Identity();self.input=nn.Identity()
        self.calls=[]
    def forward(self,x):
        self.calls.append(x.detach().clone())
        self.norm(x.float())
        logits=fixed_logits(x.sum(-1).tolist()).to(x.device)
        return {'sign':logits[:,:3],'digits':logits[:,3:].reshape(-1,7,10)}


class RecordingChain:
    include_tokens=False
    def __init__(self):self.seen=[]
    def existing(self,cache,chunk=256):
        assert set(cache)=={'original','features','tokens'}
        assert cache['tokens'].shape[1] == 0
        self.seen.append(cache['features'].detach().clone())
        return cache['original'],torch.zeros(len(cache['original']),dtype=torch.long)


def test_full_signed_operand_span_and_fixed_residuals():
    cards=s.generate_cards(per_band=12)
    assert len(cards)>1000
    assert max(r['magnitude'] for r in cards)==2_000_000
    assert all(max(abs(r['a']),abs(r['b'])) <= 2_000_000 for r in cards)
    assert all(r['target']==r['a']+r['b'] for r in cards)
    assert any(r['a']==2_000_000 and r['b']==-1_999_999 for r in cards)
    assert any(r['a']==-2_000_000 and r['b']==1_999_999 for r in cards)
    assert any(r['a']==2_000_000 and r['b']==2_000_000 for r in cards)
    assert any(r['a']==-2_000_000 and r['b']==-2_000_000 for r in cards)
    assert any(r['target']==0 and abs(r['a'])==2_000_000 for r in cards)
    assert any(r['target']==1 for r in cards) and any(r['target']==-1 for r in cards)
    assert any(r['target']==4000000 for r in cards)
    assert set(r['regime'] for r in cards)=={'near','far'}
    for r in cards:
        assert any((r['magnitude'] in range(lo,hi+1)) for lo,hi in s.BANDS)


def test_deterministic_magnitude_and_exact_test_family_balance():
    one=s.generate_cards(per_band=8,seed=43)
    two=s.generate_cards(per_band=8,seed=43)
    assert one==two
    assert len({(r['a'],r['b']) for r in one})==len(one)
    pairs={(r['a'],r['b']) for r in one}
    for a,b in pairs:
        assert (b,a) in pairs
        assert (-a,-b) in pairs


def test_exact_zero_line_and_five_percent_are_disjoint_requirements():
    cards=[{'a':100,'b':-100,'target':0,'magnitude':100,'band':'0-127',
            'regime':'near','abs_residual':0,'operation':'cancellation'},
           {'a':2_000_000,'b':-1_999_999,'target':1,'magnitude':2_000_000,
            'band':'1572864-2000000','regime':'near','abs_residual':1,'operation':'cancellation'},
           {'a':2_000_000,'b':-1_999_000,'target':1000,'magnitude':2_000_000,
            'band':'1572864-2000000','regime':'far','abs_residual':1000,'operation':'cancellation'},
           {'a':2_000_000,'b':-1_999_000,'target':1000,'magnitude':2_000_000,
            'band':'1572864-2000000','regime':'far','abs_residual':1000,'operation':'cancellation'}]
    logits=fixed_logits([1,2,1049,1051])
    rows=s.evaluate_cards(cards,{'test':logits})
    assert rows[0]['test_relative_5pct'] is False
    assert rows[1]['test_relative_5pct'] is False
    assert rows[2]['test_relative_5pct'] is True
    assert rows[3]['test_relative_5pct'] is False
    assert rows[2]['test_abs_error']==49
    assert rows[3]['test_abs_error']==51
    assert s.statistics(rows,'test')['five_pct_pass']==1


def test_no_labels_or_oracle_in_prediction():
    base=RecordingBase();chain=RecordingChain()
    x=torch.tensor([(1, -1),(2_000_000,-1_999_999),(-2_000_000,1_999_998)])
    result=s.predict(base,x,batch_size=2,chain=chain)
    assert list(result)==['backbone','accepted_chain']
    assert torch.equal(result['backbone'],result['accepted_chain'])
    assert torch.equal(s.decode(result['backbone'])[0],torch.tensor([0,1,-2]))
    assert len(chain.seen)==2
    assert len(base.calls)==2


def test_zero_canonical_check_and_false_zeros():
    one=fixed_logits([0,0])
    one[0,3]=-12.; one[0,4]=12. # sign=0 but nonzero digit
    values,sgn,canonical=s.decode(one)
    assert values.tolist()==[0,0]
    assert canonical.tolist()==[False,True]


def test_actual_digit_model_forward_accepts_two_million():
    torch.set_num_threads(1)
    model=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1,seed=42)
    out=s.predict(model,torch.tensor([[2_000_000,-1_999_999],[-2_000_000,2_000_000],
                                      [2_000_000,2_000_000]],dtype=torch.long),batch_size=2)
    assert out['backbone'].shape==(3,73)
    decoded=s.decode(out['backbone'])[0]
    assert decoded.shape==(3,)


def test_read_only_reporting_mocked_certified_checkpoint(tmp_path,monkeypatch):
    path=tmp_path/'source';path.mkdir()
    checkpoint=path/'certified.pt';checkpoint.write_bytes(b'never change')
    source_hash=s.sha256(checkpoint)
    fake=RecordingBase()
    def mocked_loader(ladder,device):
        assert str(ladder)==str(path)
        return fake,{'checkpoint':str(checkpoint),'checkpoint_sha256':source_hash}
    monkeypatch.setattr(s,'load_certified_model',mocked_loader)
    argv=['--ladder-output',str(path),'--output',str(tmp_path/'out'),'--device','cpu',
          '--sample-per-band','4','--eval-batch-size','1000','--cpu-threads','1']
    result=s.main(argv)
    assert result['tested_max_operand_magnitude']==2_000_000
    assert result['source_checkpoints_unchanged'] is True
    assert result['models']['backbone']['checks']['zero_line_hypothesis_supported_on_sample'] is True
    assert s.sha256(checkpoint)==source_hash
    assert (tmp_path/'out'/'cards.csv').is_file()
    assert (tmp_path/'out'/'result.json').is_file()
    with pytest.raises(FileExistsError,match='OUTPUT_ALREADY_EXISTS'):
        s.main(argv)
    with pytest.raises(ValueError,match='OUTPUT_OVERLAPS_SOURCE'):
        s.main(['--device','cpu','--ladder-output',str(path),'--output',str(path/'run')])


def test_plan_without_loading_models_and_guard_chain_arg_partials(tmp_path):
    args=s.parser().parse_args(['--phase','plan','--sample-per-band','4'])
    out=s.run(args)
    assert out['max_magnitude']==2_000_000
    with pytest.raises(ValueError,match='CHAIN_PATHS_ALL_OR_NONE'):
        s.main(['--phase','plan','--prior-bank-output','only-bank'])


def test_sign_and_swap_invariance_pair_counts():
    cards=s.generate_cards(near=(0,1,2),far=(128,),per_band=4)
    base=RecordingBase()
    logits=s.predict(base,torch.tensor([(r['a'],r['b']) for r in cards],dtype=torch.long),batch_size=1000)
    rows=s.evaluate_cards(cards,logits)
    inv=s.invariants(rows,'backbone')
    assert inv['operand_swap']['pairs']>0
    assert inv['both_signs_reversed']['pairs']>0
    assert inv['operand_swap']['consistency_rate']==1.0
    assert inv['both_signs_reversed']['consistency_rate']==1.0


def test_optional_accepted_chain_loaded_and_hashes_verified(tmp_path,monkeypatch):
    from tools import nanojev_zero_line_bank_autopilot as auto
    source=tmp_path/'certified';source.mkdir()
    checkpoint=source/'frozen.pt';checkpoint.write_bytes(b'frozen')
    b=tmp_path/'bank';r=tmp_path/'router';repair=tmp_path/'repair'
    for folder in (b,r,repair):folder.mkdir()
    bank=b/'promoted.pt';router=r/'promoted.pt'
    bank.write_bytes(b'bank-weights');router.write_bytes(b'gate-weights')
    model=RecordingBase()
    def loader(path,device):
        return model,{'checkpoint':str(checkpoint),'checkpoint_sha256':s.sha256(checkpoint)}
    def bootstrap(args,model,src):
        assert args.prior_bank_output==str(b)
        assert src['checkpoint_sha256']==s.sha256(checkpoint)
        return RecordingChain(),[{'bank_path':str(bank),'bank_sha256':s.sha256(bank),
                                  'gate_path':str(router),'gate_sha256':s.sha256(router)}]
    monkeypatch.setattr(s,'load_certified_model',loader)
    monkeypatch.setattr(auto,'_bootstrap',bootstrap)
    args=['--ladder-output',str(source),'--prior-bank-output',str(b),
          '--prior-router-output',str(r),'--repair-output',str(repair),
          '--output',str(tmp_path/'report'),'--device','cpu',
          '--sample-per-band','4','--eval-batch-size','2000','--cpu-threads','1']
    report=s.main(args)
    assert set(report['models'])=={'backbone','accepted_chain'}
    assert report['models']['accepted_chain']['overall']['exact_rate']==1.0
    assert report['chain_hashes'][str(bank)]==s.sha256(bank)


def test_source_mutation_rejected_before_writing_report(tmp_path,monkeypatch):
    source=tmp_path/'certified';source.mkdir()
    checkpoint=source/'frozen.pt';checkpoint.write_bytes(b'frozen')
    model=RecordingBase()
    def loader(path,device):
        return model,{'checkpoint':str(checkpoint),'checkpoint_sha256':s.sha256(checkpoint)}
    monkeypatch.setattr(s,'load_certified_model',loader)
    original_predict=s.predict
    def malicious_predict(*args,**kwargs):
        pred=original_predict(*args,**kwargs)
        checkpoint.write_bytes(b'tampered')
        return pred
    monkeypatch.setattr(s,'predict',malicious_predict)
    output=tmp_path/'report'
    with pytest.raises(RuntimeError,match='SOURCE_CHANGED_DURING_EVALUATION'):
        s.main(['--ladder-output',str(source),'--output',str(output),
                '--device','cpu','--sample-per-band','4','--cpu-threads','1'])
    assert not (output/'result.json').exists()


def test_conditional_far_accuracy_gate_is_by_population_not_exact_integer():
    cards=s.generate_cards(near=(0,1,2),far=(1000,),per_band=4)
    target=[r['target'] for r in cards]
    approx=[v+int(v>0)*40-int(v<0)*40 if abs(v)>=1000 else v for v in target]
    rows=s.evaluate_cards(cards,{'model':fixed_logits(approx)})
    summary=s.summary(rows,(0,1,2),(1000,),['model'],min_far_rate=0.8)
    model=summary['models']['model']
    assert model['checks']['zero_line_hypothesis_supported_on_sample'] is True
    assert model['far']['exact_rate']<1.0
    assert model['far']['five_pct_rate']==1.0


def test_error_in_small_residual_10_refuses_hypothesis_even_if_far_ok():
    cards=s.generate_cards(near=(0,1,2,5,10),far=(1000,),per_band=4)
    guesses=[r['target']+1 if r['target']==10 else r['target'] for r in cards]
    rows=s.evaluate_cards(cards,{'model':fixed_logits(guesses)})
    out=s.summary(rows,(0,1,2,5,10),(1000,),['model'],min_far_rate=.8)
    checks=out['models']['model']['checks']
    assert checks['plus_minus_one_two_exact_all_magnitudes'] is True
    assert checks['all_nonzero_residuals_up_to_16_exact'] is False
    assert checks['zero_line_hypothesis_supported_on_sample'] is False
