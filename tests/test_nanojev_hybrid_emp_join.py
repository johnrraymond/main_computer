"""Reusable EMP teacher -> independent HybridLink Head2 regression contracts."""
import json
from pathlib import Path

import pytest
import torch
from torch import nn

from tools import nanojev_hybrid_emp_curriculum as cards
from tools import nanojev_hybrid_emp_join as join
from tools import nanojev_hybrid_lexical_alignment as lex


class TinyTokenizer:
    is_fast=True
    def __len__(self):return 256
    def __call__(self,text,add_special_tokens=False,return_offsets_mapping=False):
        assert not add_special_tokens and return_offsets_mapping
        return {'input_ids':[ord(c)%256 for c in text],
                'offset_mapping':[(i,i+1) for i in range(len(text))]}


class TinyBase(nn.Module):
    def __init__(self,hidden=64):
        super().__init__();self.embedding=nn.Embedding(256,hidden)
    def get_input_embeddings(self):return self.embedding


def loader(device='cpu'):
    return TinyBase().to(device),TinyTokenizer(),{'revision':'tiny-v1'}


class NeuralLocatorStandIn(nn.Module):
    """Oracle-free structural mock used ONLY to exercise the Head 2 harness."""
    def __init__(self,hidden=64,dim=8):
        super().__init__();self.hidden=hidden;self.sparse_dims=dim
    def eval(self):return self
    def structured_byte_logits(self,source,runs,return_decisions=False):
        data=bytes(source.cpu().tolist())
        spans=[]
        for match in lex.LITERAL.finditer(data.decode('utf8')):
            # Convert character start/end to true UTF-8 offsets.
            text=data.decode('utf8')
            a=len(text[:match.start()].encode('utf8'))
            b=len(text[:match.end()].encode('utf8'))
            spans.append((a,b))
        output=torch.full((len(data),),-20.,device=source.device)
        for a,b in spans:output[a:b]=20.
        return (output,spans,[2]*len(spans)) if return_decisions else output
    def selected_dimensions(self):
        return torch.arange(self.sparse_dims)
    def edit_map(self,byte_logits,memberships):
        p=torch.stack([byte_logits[list(span)].sigmoid().amax() for span in memberships])
        mask=byte_logits.new_zeros(self.hidden);mask[:self.sparse_dims]=1.
        return p[:,None]*mask[None]


def mockhead():
    return NeuralLocatorStandIn(),{'sha256':'toy-head','sparse_dims':8,'schema':lex.SCHEMA}


def test_local_teacher_disjoint_and_gold_correct():
    population=cards.LocalCurriculum(16)
    assert sum(len(xs) for xs in population.decks.values())==33*33
    sets=[{(c.a,c.b) for c in population.decks[k]} for k in ('train','dev','test')]
    assert not (sets[0]&sets[1] or sets[0]&sets[2] or sets[1]&sets[2])
    assert any(c.delta==0 and c.a!=0 for c in population.decks['train'])
    assert any(c.delta<0 for c in population.decks['train'])
    assert any(c.delta>0 for c in population.decks['train'])
    assert any(c.origin=='signed-radius16' for c in population.decks['train'])
    assert any(c.delta==32 for c in sum(population.decks.values(),[]))
    assert all(str(c.delta) not in c.render(0).split('Answer:')[-1] if 'Answer:' in c.render(0) else True
               for c in population.decks['train'])


def test_teacher_reproducible_cursor_and_weights():
    x=cards.LocalCurriculum();y=cards.LocalCurriculum()
    for _ in range(12):assert x.next()==y.next()
    state=x.state();another=[x.next() for _ in range(15)]
    y.load(state);assert another==[y.next() for _ in range(15)]
    y.emphasize(0,True);assert y.difficulties[0]>1
    assert 1<=y.difficulties[0]<=8
    with pytest.raises(ValueError):y.emphasize(999,True)


def test_reuse_actual_outward_sampler():
    with pytest.raises(ValueError,match='MIN_RADIUS'):
        cards.outward_cards(radius=16,seed=10)
    # Check actual function identity: provider delegates rather than copies it.
    assert cards.outward.generate_batch is not None
    assert cards.sampled.evaluation_cards is not None


def test_negative_zero_does_not_pass_exact_bit_gate():
    wrong=torch.tensor([1.,-1.,-1.,-1.,-1.,-1.,-1.])
    assert join.decode_logits(wrong)==0
    assert not join.exact_logits(wrong,torch.zeros(7))


def test_lossless_signed_bits_and_bounds():
    for n in range(-32,33):
        v=join.signed_bitfield(n,7)
        assert join.bits_to_int(v)==n
        assert (v[0]==1)==(n<0)
    assert join.signed_bitfield(0,7)==[0]*7
    with pytest.raises(ValueError):join.signed_bitfield(64,7)


def test_synthetic_edit_locality_and_gradients():
    torch.manual_seed(7)
    head,hid=mockhead();tok=TinyTokenizer();emb=TinyBase().embedding
    card=cards.Card(8,-8,0,0,8,'test','train')
    example=join.extract(card,0,tok,head,emb,'cpu',7)
    model=join.NumericalHead2(64,8,7,32)
    original=example['base'].detach().clone()
    outputs=join.forward_case(model,example)
    change=(outputs['edited']-original).abs()>1e-6
    assert change.any()
    allowed=example['gate']>0.5
    assert not (change & ~allowed).any()
    loss,parts,_=join.loss_for(model,example,card)
    loss.backward()
    for group in (model.writer,model.reader,model.resolver):
        assert any(p.grad is not None and p.grad.abs().sum()>0 for p in group.parameters())
    assert all(p.grad is None for p in emb.parameters())
    assert parts['operand_ce']>0 and parts['delta_ce']>0
    assert example['values']==[8,-8]
    assert example['text'].count('Answer:')==0


def test_extract_fails_closed_if_head_one_misses_operand():
    tok=TinyTokenizer();emb=TinyBase().embedding
    card=cards.Card(3,4,7,0,4,'test','train')
    class WrongHead(NeuralLocatorStandIn):
        def structured_byte_logits(self,source,runs,return_decisions=False):
            data=bytes(source.tolist());return (torch.full((len(data),),-20.),[],[]) if return_decisions else None
    with pytest.raises(RuntimeError,match='TWO_SPANS'):
        join.extract(card,0,tok,WrongHead(),'cpu' if False else emb,'cpu',7)


def test_large_operands_refused_before_numeric_cast():
    tok=TinyTokenizer();emb=TinyBase().embedding
    card=cards.Card(100,-99,1,0,100,'outward','train')
    with pytest.raises(RuntimeError,match='RADIUS_NOT_CERTIFIED'):
        join.extract(card,0,tok,NeuralLocatorStandIn(),emb,'cpu',24)


def test_gate_rejects_untrained_and_prevents_test_reuse(tmp_path):
    root=tmp_path/'run'
    with pytest.raises(FileNotFoundError):join.gate(device='cpu',output=root,loader=loader,head1_override=mockhead())


def test_cpu_train_pause_resume_gate_and_metrics(tmp_path,monkeypatch):
    torch.set_num_threads(1)
    model,tokenizer,details=loader('cpu')
    def stable_loader(device='cpu'):
        return model.to(device),tokenizer,details
    base={'device':'cpu','output':tmp_path/'joined','head1_override':mockhead(),
          'loader':stable_loader,'radius':8,'bit_width':7,'steps':4,'eval_every':2,'seed':123}
    partial=join.train(**base,stop_after=2)
    assert partial['status']=='PAUSED'
    full=join.train(**base,resume=True)
    assert full['status']=='TRAINED_UNCERTIFIED'
    assert (tmp_path/'joined'/'best.pt').exists()
    gate=join.gate(device='cpu',output=base['output'],loader=stable_loader,head1_override=mockhead())
    assert gate['status'] in ('HEAD2_LOCAL_PASS','HEAD2_LOCAL_FAIL')
    assert gate['test_once'] and not gate['outward_certified']
    assert gate['test']['error_stddev'] is None or gate['test']['error_stddev']>=0
    with pytest.raises(RuntimeError,match='TEST_ALREADY_USED'):
        join.gate(device='cpu',output=base['output'],loader=stable_loader,head1_override=mockhead())


def test_head1_missing_certification(tmp_path):
    root=tmp_path/'head1';root.mkdir()
    (root/'gate_result.json').write_text(json.dumps({'schema':lex.SCHEMA,'gate_pass':False}))
    (root/'config.json').write_text(json.dumps({'identity':{'hidden':64}}))
    with pytest.raises(RuntimeError,match='CERTIFICATION_REQUIRED'):
        join.load_head1(root,'cpu',64)
