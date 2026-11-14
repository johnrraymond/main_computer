"""Contract tests for stage-1 numerical byte-token-embedding localization."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from tools import nanojev_hybrid_lexical_alignment as m


class TinyTokenizer:
    is_fast=True
    def __len__(self):return 512
    def __call__(self,text,*,add_special_tokens=False,return_offsets_mapping=False):
        ids=[ord(c)%512 for c in text]
        offsets=[(i,i+1) for i in range(len(text))]
        d={'input_ids':ids}
        if return_offsets_mapping:d['offset_mapping']=offsets
        return d


class TinyModel(nn.Module):
    def __init__(self):
        super().__init__();self.emb=nn.Embedding(512,16)
    def get_input_embeddings(self):return self.emb


def loader(device):
    torch.manual_seed(91)
    return TinyModel().to(device),TinyTokenizer(),{'revision':'toy-test'}


def test_utf8_byte_offsets_and_numbers():
    row=m.sample('Café 8 + (-8)',TinyTokenizer())
    raw='Café 8 + (-8)'.encode('utf-8')
    assert raw[row['spans'][0]['begin']:row['spans'][0]['end']]==b'8'
    assert raw[row['spans'][1]['begin']:row['spans'][1]['end']]==b'-8'
    assert len(raw)==len('Café 8 + (-8)')+1
    assert len(row['ids'])==len('Café 8 + (-8)')
    assert row['offsets'][3]==(3,5)  # UTF-8 'é' occupies TWO bytes
    assert sum(row['byte_labels'])==3
    assert sum(row['token_labels'])==3


@pytest.mark.parametrize('text,expected',[
    ('0',[0]),('(0)',[0]),('((0))',[0]),('0+0',[0,0]),
    ('(0+0)',[0,0]),('8+(-8)',[8,-8]),('16+(-15)',[16,-15]),
    ('Résumé: ((-16)) and (+15)',[-16,15]),
])
def test_lexical_grammar(text,expected):
    assert [x['value'] for x in m.sample(text,TinyTokenizer())['spans']]==expected


def test_bits_have_unique_sign_and_zero():
    assert m.signed_bits(0)==[0,0,0,0,0,0]
    assert m.signed_bits(-0)==m.signed_bits(0)
    assert m.signed_bits(16)==[0,1,0,0,0,0]
    assert m.signed_bits(-16)==[1,1,0,0,0,0]
    assert m.signed_bits(-1)==[1,0,0,0,0,1]


def test_wrong_range_and_ambiguous_tokenizer_fail_closed():
    with pytest.raises(ValueError,match='RADIUS'):m.sample('17',TinyTokenizer())
    class Slow(TinyTokenizer):is_fast=False
    with pytest.raises(RuntimeError,match='FAST'):m.sample('8',Slow())
    class Merge(TinyTokenizer):
        def __call__(self,text,**kw):return {'input_ids':[1],'offset_mapping':[(0,len(text))]}
    with pytest.raises(ValueError,match='MULTIPLE_LITERALS'):m.sample('1+2',Merge())


def test_only_numeric_token_positions_are_edited_with_perfect_logits():
    row=m.sample('Compute 8 + (-8)',TinyTokenizer())
    head=m.LexicalAlignmentHead(hidden=16,width=8,sparse_dims=3)
    b=torch.tensor([15. if y else -15. for y in row['byte_labels']])
    gate=head.edit_map(b,row['memberships'])
    assert gate.shape==(len(row['ids']),16)
    assert (gate[row['token_labels'] and torch.tensor(row['token_labels'])==0].amax() < 1e-5)
    assert gate.sum(dim=1).argmax().item() in [i for i,x in enumerate(row['token_labels']) if x]
    assert (gate>0.5).sum(dim=1).max().item()==3


def test_apply_edit_is_differentiable_and_nonmutating():
    original=torch.zeros(2,4)
    gate=torch.tensor([[0.,1.,0.,0.],[0.,0.,0.,0.]])
    change=torch.ones(2,4,requires_grad=True)
    actual=m.apply_edit(original,gate,change)
    assert actual.sum().item()==1
    assert original.sum().item()==0
    actual.sum().backward()
    assert torch.equal(change.grad,gate)
    with pytest.raises(ValueError,match='SHAPE'):m.apply_edit(original,gate,change[:1])


def test_training_has_real_byte_and_dimension_gradients():
    head=m.LexicalAlignmentHead(hidden=16,width=12,sparse_dims=4)
    row=m.sample('Café: (8)+(-8)',TinyTokenizer())
    e=nn.Embedding(512,16)
    e.requires_grad_(False)
    emb=e(torch.tensor(row['ids']))
    loss,parts=m.losses(head,row,emb,'cpu')
    loss.backward()
    assert float(head.span_classifier[0].weight.grad.abs().sum())>0
    assert float(head.dim_logits.grad.abs().sum())>0
    assert all(p.grad is None for p in e.parameters())
    assert float(parts['span_ce'])>0
    assert float(parts['bit_ce'])>0


def test_disjoint_split_forms_and_no_arithmetic_targets():
    d=m.build_data(seed=39,train_count=30,dev_count=12,test_count=12)
    assert len({v for s in d.values() for v in s})==54
    assert len(d['dev'])==12
    for text in d['test']:
        assert m.sample(text,TinyTokenizer())['spans']


def test_end_to_end_cpu_pause_resume_gate_and_one_shot(tmp_path):
    args={'device':'cpu','output':tmp_path,'loader':loader,'train_count':6,
          'dev_count':6,'test_count':6,'steps':3,'eval_every':2,
          'width':8,'sparse_dims':3}
    partial=m.train(**args,stop_after=1)
    assert partial['status']=='PAUSED'
    completed=m.train(**args,resume=True)
    assert completed['status']=='TRAINED_UNCERTIFIED'
    assert (tmp_path/'last.pt').is_file()
    gate=m.gate(device='cpu',output=tmp_path,loader=loader)
    assert gate['arithmetic_trained'] is False
    assert gate['numerical_correction_trained'] is False
    assert len(gate['selected_dimensions'])==3
    assert gate['status'] in ('ALIGNMENT_PASS','ALIGNMENT_FAIL')
    with pytest.raises(RuntimeError,match='ALREADY_USED'):
        m.gate(device='cpu',output=tmp_path,loader=loader)
    inspection=m.inspect(device='cpu',output=tmp_path,loader=loader,
                         text='Café 8 + (-8)')
    assert inspection['edit_map_shape']==[len('Café 8 + (-8)'),16]
    assert inspection['edits_applied'] is False


def test_empty_or_unsafe_resume_refused(tmp_path):
    with pytest.raises(Exception):m.gate(device='cpu',output=tmp_path,loader=loader)
    (tmp_path/'sentinel').write_text('do not overwrite')
    with pytest.raises(RuntimeError,match='OUTPUT_EXISTS'):
        m.train(device='cpu',output=tmp_path,loader=loader,steps=1,
                train_count=6,dev_count=6,test_count=6,sparse_dims=3)


def test_cli_help():
    with pytest.raises(SystemExit) as exc:m.main(['--help'])
    assert exc.value.code==0


def test_v3_gate_treats_bitfield_as_diagnostic_not_head1_alignment():
    assert m.SCHEMA.endswith('.v3')
    assert str(m.ROOT).endswith('_v3')
    source=Path(m.__file__).read_text(encoding='utf8')
    assert "report['literal_complete_recall']" in source
    assert "report['lexical_bitfield_exact']>=0.80" not in source


def test_missing_first_literal_is_not_hidden_by_high_background_accuracy():
    row=m.sample('Compute 8 + (-8)',TinyTokenizer())
    assert [(s['begin'],s['end']) for s in row['spans']]==[(8,9),(13,15)]
    class DropsFirst(m.LexicalAlignmentHead):
        def structured_byte_logits(self,bytes_,runs):
            return torch.tensor([15. if i in (13,14) else -15. for i in range(len(bytes_))],device=bytes_.device)
    model=DropsFirst(hidden=16,width=8,sparse_dims=3)
    report=m.judge(model,['Compute 8 + (-8)'],TinyTokenizer(),nn.Embedding(512,16),'cpu')
    assert report['numeric_byte_recall']==pytest.approx(2/3)
    assert report['literal_complete_recall']==0.5
    assert report['example_exact_fraction']==0.


def test_training_grammar_contains_spaced_without_period():
    # The original locator missed the first operand of 'Compute 8 + (-8)'
    # because source spacing varied from the narrow v1 training grammar.
    d=m.build_data(seed=246,train_count=800,dev_count=6,test_count=6)
    assert any(t.startswith('Compute ') and ' + (' in t and not t.endswith('.')
               for t in d['train'])


@pytest.mark.parametrize('text,expected',[
    ('0+0',[(0,1),(2,3)]),
    ('(-8)',[(1,3)]),
    ('(+15)',[(1,4)]),
    ('Résumé: ((-16)) and (((+15)))',[(12,15),(25,28)]),
])
def test_atomic_digit_run_labels_and_utf8(text,expected):
    row=m.sample(text,TinyTokenizer())
    assert [(r['begin']-(r['target']==2),r['end'])
            for r in row['runs'] if r['target']!=0]==expected
    assert [(r['begin'],r['end']) for r in row['runs'] if r['target']==2] == [
        (s['begin']+1,s['end']) for s in row['spans'] if s['literal'][0] in '+-']


def test_hard_negative_candidates_and_unary_vs_binary_plus():
    row=m.sample('Compute 0+0 and (+15) tag_8x v12x label_3',TinyTokenizer())
    labels=[r['target'] for r in row['runs']]
    assert labels[:3]==[1,1,2]  # binary plus is NOT unary sign
    assert labels[3:]==[0,0,0]  # lexical decoys are not literals


def test_structured_decoder_never_emits_partial_literals_or_extraneous_punctuation():
    text='Compute 8 + (-8)'
    row=m.sample(text,TinyTokenizer())
    head=m.LexicalAlignmentHead(hidden=16,width=8,sparse_dims=3)
    def forced_logits(source,runs):
        assert len(runs)==2
        return torch.tensor([[-12.,12.,-1.],[-12.,-1.,12.]])
    head.span_logits=forced_logits
    logits,spans,decisions=head.structured_byte_logits(torch.tensor(row['bytes']),row['runs'],True)
    assert spans==[(8,9),(13,15)] and decisions==[1,2]
    assert [i for i,n in enumerate(logits.sigmoid().tolist()) if n>=.5]==[8,13,14]
    assert (logits.sigmoid()[:8]<.5).all()


def test_exact_spans_reach_token_offsets_without_oracle():
    text='Café 8 + (-8)'
    row=m.sample(text,TinyTokenizer())
    head=m.LexicalAlignmentHead(hidden=16,width=8,sparse_dims=3)
    head.span_logits=lambda source,runs: torch.tensor([[-10.,10.,-10.],[-10.,-10.,10.]])
    raw=torch.tensor(row['bytes'])
    gate=head.edit_map(head.structured_byte_logits(raw,m.digit_runs(text.encode('utf8'))),row['memberships'])
    positions=[i for i,p in enumerate(gate.amax(-1).tolist()) if p>=.5]
    assert positions==[5,10,11]


def test_span_training_dataset_contains_negative_runs():
    data=m.build_data(seed=239,train_count=90,dev_count=12,test_count=12)
    rows=[m.sample(t,TinyTokenizer()) for t in data['train']]
    assert any(r['target']==0 for row in rows for r in row['runs'])
    assert any(r['target']==2 for row in rows for r in row['runs'])
    assert any(r['target']==1 and r['has_sign'] for row in rows for r in row['runs'])
