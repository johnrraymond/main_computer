"""Contracts for bounded, exact, non-arithmetic Head 2 transmission."""
import json
import pytest
import torch
from tools import nanojev_hybrid_emp_operand_stage as stage
from tools import nanojev_hybrid_emp_curriculum as curriculum
from tools import nanojev_hybrid_emp_join as prior
from tests.test_nanojev_hybrid_emp_join import TinyTokenizer,TinyBase,NeuralLocatorStandIn,mockhead


def test_six_bit_sign_magnitude_all_radius_values():
    for n in range(-16,17):
        out=stage.bits([n],'cpu')
        assert out.shape==(1,6)
        assert out[0,0].item()==int(n<0)
        assert sum(int(out[0,1+i])*2**(4-i) for i in range(5))==abs(n)
    assert not stage.bits([0],'cpu').any()
    assert stage.wrong_pair([16,-2])==[-16,-1]
    assert stage.wrong_pair([0,8])==[1,9]
    with pytest.raises(ValueError,match='OVERFLOW'):
        stage.bits([17],'cpu')


def test_exact_sparse_embedding_replacement_and_no_other_coordinates():
    torch.manual_seed(21)
    card=curriculum.Card(8,-8,0,0,8,'test','train')
    example=prior.extract(card,0,TinyTokenizer(),NeuralLocatorStandIn(),TinyBase().embedding,'cpu',7)
    example['bits']=stage.bits(example['values'],'cpu')
    model=stage.OperandTransmission(sparse_dims=8,channels=32)
    corrected=model.edited_embeddings(example)
    assert corrected.shape==example['base'].shape
    assert torch.equal(corrected[example['gate']<0.5],example['base'][example['gate']<0.5])
    expected=model.transmit(example['bits'])
    actual=[]
    for pos in example['positions']:
        actual.append(model.reader(corrected[pos].float().mean(0)[example['dimensions']]))
    assert torch.allclose(expected,torch.stack(actual),atol=1e-5)
    altered=stage.bits(stage.wrong_pair(example['values']),'cpu')
    assert not torch.equal(altered,example['bits'])
    assert not torch.allclose(model.edited_embeddings(example,mode='wrong',wrong_bits=altered),corrected)
    assert not torch.allclose(model.edited_embeddings(example,mode='zero'),corrected)


def test_learner_has_no_arithmetic_resolver_or_target_path():
    model=stage.OperandTransmission(8,32)
    assert not hasattr(model,'resolver')
    assert model.width==6
    card=curriculum.Card(8,-8,0,0,8,'test','train')
    another=curriculum.Card(8,-8,0,0,8,'test','dev')
    assert card.split!=another.split
    assert torch.equal(stage.bits([card.a,card.b],'cpu'),stage.bits([another.a,another.b],'cpu'))
    with pytest.raises(ValueError,match='WRONG_BITS_REQUIRED'):
        model.transmit(stage.bits([8,-8],'cpu'),mode='wrong')
    loss,parts=stage.loss_for(model,stage.bits([8,-8],'cpu'))
    loss.backward()
    assert loss.item()>0
    assert all(p.grad is not None and p.grad.abs().sum().item()>0 for p in model.parameters())
    assert parts['bit_ce']>0


def stable_loader():
    torch.manual_seed(222)
    model=TinyBase()
    tok=TinyTokenizer()
    def run(device='cpu'):
        return model.to(device),tok,{'revision':'tiny-v1'}
    return run


def test_end_to_end_no_test_reuse_and_resume(tmp_path):
    torch.set_num_threads(1)
    kw={'device':'cpu','output':tmp_path/'run','loader':stable_loader(),
        'head1_override':mockhead(),'steps':4,'eval_every':2,'seed':889,'batch_size':16}
    paused=stage.train(**kw,stop_after=2)
    assert paused['status']=='PAUSED'
    resumed=stage.train(**kw,resume=True)
    assert resumed['status']=='TRAINED_UNCERTIFIED'
    gate=stage.gate(device='cpu',output=kw['output'],loader=kw['loader'],head1_override=kw['head1_override'])
    assert gate['status']=='OPERAND_DEV_FAIL'
    assert not gate['test_read'] and not gate['resolver_unlocked']
    assert not gate['arithmetic_trained']
    with pytest.raises(RuntimeError,match='ALREADY_USED'):
        stage.gate(device='cpu',output=kw['output'],loader=kw['loader'],head1_override=kw['head1_override'])


def test_mismatched_control_is_not_commutative_swap():
    for a in range(-16,17):
        for b in range(-16,17):
            wa,wb=stage.wrong_pair([a,b])
            assert wa+wb != a+b
            assert (a,b)!=(wa,wb)
            assert -16<=wa<=16 and -16<=wb<=16


def test_heldout_pair_measurement_is_harder_than_bit_accuracy():
    model=stage.OperandTransmission(sparse_dims=8,channels=32)
    with torch.no_grad():
        for p in model.parameters():p.zero_()
    result=stage.assess(model,stage.bits([8,-8],'cpu').reshape(1,2,6))
    assert result['pair_exact']==0
    assert result['bit_accuracy']>0
    assert result['mismatched_pair_exact']==0


def test_bfloat16_sparse_update_rounds_only_once_and_preserves_reader_path():
    """Regression: trained FP32 codes must survive Qwen BF16 replacement."""
    torch.manual_seed(42)
    card=curriculum.Card(8,-8,0,0,8,'test','train')
    ex=prior.extract(card,0,TinyTokenizer(),NeuralLocatorStandIn(),TinyBase().embedding,'cpu',7)
    ex['bits']=stage.bits(ex['values'],'cpu')
    # Actual Qwen's embedding is BF16; force large baselines that expose
    # double rounding (a mismatch under the original implementation).
    ex['base']=(ex['base'].float()*6 + 1.25).to(torch.bfloat16)
    model=stage.OperandTransmission(sparse_dims=8,channels=32)
    with torch.no_grad():
        model.writer[2].bias.fill_(1.15)  # ~0.8: BF16 spacing > FP32 tolerance
    corrected=model.edited_embeddings(ex)
    assert corrected.dtype is torch.bfloat16
    assert torch.equal(corrected[ex['gate']<.5],ex['base'][ex['gate']<.5])
    codes=model.writer(ex['bits']).to(torch.bfloat16)
    for i,locations in enumerate(ex['positions']):
        for pos in locations:
            assert torch.equal(corrected[pos,ex['dimensions']],codes[i])
    report=stage.assess_edited_embeddings(model,[ex])
    assert report['pairs']==1 and report['input_dtype']=='torch.bfloat16'
    assert report['sparse_coordinates_checked']==16


def test_quantized_edit_gate_verifies_real_readout_not_direct_transmit(tmp_path):
    """The sealed gate must record real sparse-embedding reconstruction."""
    torch.set_num_threads(1)
    loader=stable_loader()
    # Simulate a BF16 Qwen embedding on CPU, retain the toy tokenizer/head.
    model=TinyBase()
    model.embedding.to(dtype=torch.bfloat16)
    def bf16_loader(device='cpu'):
        return model.to(device),TinyTokenizer(),{'revision':'tiny-bf16'}
    kw={'device':'cpu','output':tmp_path/'run','loader':bf16_loader,
        'head1_override':mockhead(),'steps':4,'eval_every':2,'seed':889,'batch_size':16}
    stage.train(**kw)
    result=stage.gate(device='cpu',output=kw['output'],loader=bf16_loader,head1_override=kw['head1_override'])
    assert 'dev_edited' in result
    assert result['dev_edited']['input_dtype']=='torch.bfloat16'
    assert result['status']=='OPERAND_DEV_FAIL'
    assert result['test_read'] is False
