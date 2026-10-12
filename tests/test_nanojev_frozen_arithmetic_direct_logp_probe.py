"""Direct pretrained-backbone arithmetic audit: isolated, no-GPU contract tests."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
import nanojev_frozen_arithmetic_direct_logp_bank as bank
import nanojev_frozen_arithmetic_direct_logp_probe as probe
from nanojev_arithmetic_task_bank import create as legacy_create
from nanojev_antirom_v2_stream_bank import create as v2_create,load as load_v2,canonical_pair,source_reservations


def roots(tmp_path):
    legacy=tmp_path/'legacy'
    legacy_create(output_dir=legacy,train_groups=54,predev_groups=27,dev_groups=27,seed=40)
    v2=tmp_path/'v2'
    v2_create(output=v2,seed=20261012,count=300,ood_count=30,source_bank=legacy)
    return legacy,v2


def test_fresh_1000_all_unique_balanced_disjoint_and_verified(tmp_path):
    legacy,v2=roots(tmp_path)
    root=tmp_path/'direct'
    meta=bank.create(root,legacy,v2,1000,20261013)
    rows,verified=bank.load(root,legacy,v2,1000,20261013)
    assert verified==meta
    assert len(rows)==1000
    assert set(meta['classes'])=={'LESS','EQUAL','MORE'}
    assert max(meta['classes'].values())-min(meta['classes'].values())==1
    old,_=source_reservations(legacy)
    old.update(canonical_pair(r['a'],r['b']) for split in ('train','ood')
               for r in load_v2(bank_dir=v2,legacy_bank_dir=legacy,split=split)['rows'])
    pairs=[canonical_pair(r['a'],r['b']) for r in rows]
    assert not (set(pairs)&old)
    assert len(set(pairs))==1000
    assert set(r['scale'] for r in rows)=={'small','medium','large'}
    assert set(r['carry'] for r in rows)=={True,False}
    assert set(r['distance'] for r in rows)=={'exact','near','medium','far'}
    assert bank.generate(1000,20261013,old)==rows
    with pytest.raises(RuntimeError,match='OUTPUT_NOT_EMPTY'):
        bank.create(root,legacy,v2,1000,20261013)
    (root/'questions.jsonl').write_text((root/'questions.jsonl').read_text()+'\n')
    with pytest.raises(RuntimeError,match='CASE_HASH_DRIFT'):
        bank.load(root,legacy,v2,1000,20261013)


def test_gold_and_reexposure_checks():
    rows=bank.generate(90,42,set())
    assert bank.validate(rows,90,set())
    with pytest.raises(RuntimeError,match='CASE_REEXPOSURE'):
        bank.validate(rows,90,{canonical_pair(rows[0]['a'],rows[0]['b'])})
    tampered=copy.deepcopy(rows)
    tampered[10]['gold']='LESS' if rows[10]['gold']=='EQUAL' else 'EQUAL'
    with pytest.raises(RuntimeError,match='GOLD_OR_STRATUM_MISMATCH'):
        bank.validate(tampered,90,set())
    with pytest.raises(ValueError):bank.generate(2,12,set())


def test_direct_token_logp_correct_shift_for_variable_answer_lengths():
    torch=pytest.importorskip('torch')
    # Three candidates: one token for LESS, two for EQUAL, one for MORE.
    # In each row, prediction for first answer token MUST be from position prompt_len-1.
    vocab=12
    logits=torch.zeros(3,6,vocab)
    # make scores for answers at selected prediction locations
    logits[0,1,3]=5.0             # LESS: first/only token -> high
    logits[1,1,4]=4.0             # EQUAL: first token
    logits[1,2,5]=3.0             # EQUAL: second token
    logits[2,1,6]=-2.0            # MORE: low
    ids=[[3],[4,5],[6]]
    sums,means,lengths=probe.score_from_logits(torch=torch,logits=logits,prompt_length=2,answer_ids=ids)
    assert lengths==[1,2,1]
    expected=[]
    for row,targets in enumerate(ids):
        expected.append(sum(float(torch.log_softmax(logits[row,1+j],-1)[tok]) for j,tok in enumerate(targets)))
    assert sums==pytest.approx(expected,abs=1e-5)
    assert means==pytest.approx([expected[i]/len(ids[i]) for i in range(3)],abs=1e-5)
    assert sums[0]>sums[1] and sums[0]>sums[2]
    assert math.isclose(sum(probe.softmax(sums)),1.0)
    with pytest.raises(RuntimeError,match='ANSWER_TOKENS_NOT_VISIBLE'):
        probe.score_from_logits(torch=torch,logits=logits,prompt_length=6,answer_ids=ids)


class FakeTokenizer:
    pad_token_id=0
    eos_token_id=1
    def encode(self,text,add_special_tokens=True):
        if text.startswith(' LESS'):return [3]
        if text.startswith(' EQUAL'):return [4,5]
        if text.startswith(' MORE'):return [6]
        return [8,9]


class FakeLM:
    def __init__(self,torch):self.torch=torch;self.calls=0
    def __call__(self,*,input_ids,attention_mask,use_cache):
        self.calls+=1
        assert self.calls==1
        assert attention_mask.shape[0]==3 and input_ids.shape[0]==3
        assert not use_cache
        values=self.torch.zeros(3,input_ids.shape[1],12)
        values[0,1,3]=8.
        values[1,1,4]=-2.
        values[1,2,5]=3.
        values[2,1,6]=-1.
        return SimpleNamespace(logits=values)


def test_direct_scorer_does_not_load_clef_and_uses_masked_batch():
    torch=pytest.importorskip('torch')
    fake=FakeLM(torch)
    score=probe.score_one(torch=torch,model=fake,tokenizer=FakeTokenizer(),
                          prompt='Question\nANS:',device='cpu')
    assert fake.calls==1
    assert score['prefix_tokens']==2
    assert score['token_counts']==[1,2,1]
    assert probe.choose(score['sum_probs'])=='LESS'
    assert score['sum_logp'][0]>score['sum_logp'][2]


def sample_row(i,label):
    return {'id':f'fresh-{i:06d}','a':i+2,'b':9,'threshold':20,'gold':label,
            'scale':'small','distance':'near','carry':False}


def test_metrics_report_both_sum_and_mean_confusion_and_bias():
    rs=[]
    for i,label in enumerate(('LESS','EQUAL','MORE')):
        score={'sum_logp':[-0.1,-2.,-2.],'mean_logp':[-.1,-1.,-2.],
               'token_counts':[1,2,1],'sum_probs':probe.softmax([-.1,-2,-2]),
               'mean_probs':probe.softmax([-.1,-1,-2]),'prefix_tokens':15}
        rs.append(probe.record(sample_row(i,label),score,'C0'))
    result=probe.summary(rs)
    assert result['cases']==3
    assert result['modes']['sum']['accuracy']==pytest.approx(1/3)
    assert result['modes']['sum']['correct']==1
    assert result['modes']['sum']['confusion']['EQUAL']['LESS']==1
    assert result['modes']['sum']['predicted_counts']=={'LESS':3}
    assert result['answer_token_lengths']['EQUAL']=={2:3}
    assert math.isfinite(result['modes']['mean']['cross_entropy'])


def test_manifest_identity_and_resume_prefix_guard(tmp_path):
    case=sample_row(0,'LESS')
    row=probe.record(case,{'sum_logp':[-.1,-2.,-2.],'mean_logp':[-.1,-1.,-2.],
                 'token_counts':[1,2,1],'sum_probs':probe.softmax([-.1,-2,-2]),
                 'mean_probs':probe.softmax([-.1,-1,-2]),'prefix_tokens':15},'C0')
    root=tmp_path/'r';root.mkdir()
    path,meta,expected=probe.checkpoint(root,'qwen','C0','abc',[case],{'resolved_commit':'dummy'})
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps(expected))
    path.write_text(json.dumps(row)+'\n')
    assert probe.inspect_results(path,meta,expected,[case],require_completed=True)==[row]
    wrong={**expected,'bank_sha256':'def'}
    with pytest.raises(RuntimeError,match='RESUME_IDENTITY_DRIFT'):
        probe.inspect_results(path,meta,wrong,[case])
    damaged={**row,'gold':'EQUAL'}
    path.write_text(json.dumps(damaged)+'\n')
    with pytest.raises(RuntimeError,match='SAVED_RECORD_DRIFT'):
        probe.inspect_results(path,meta,expected,[case])


def test_prepare_only_does_not_load_models(tmp_path,monkeypatch):
    legacy,v2=roots(tmp_path)
    out=tmp_path/'direct'
    monkeypatch.setattr(probe,'load_source',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('model loaded')))
    probe.run(probe.parse(['--legacy-bank-dir',str(legacy),'--v2-bank-dir',str(v2),
             '--output-root',str(out),'--count','24','--prepare-only']))
    cases,_=bank.load(out,legacy,v2,24,20261013)
    assert len(cases)==24
    assert not (out/'results').exists()
