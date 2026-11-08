"""V26 independent span lookup / cached-token reconstruction safety tests."""
from fractions import Fraction
import json
from pathlib import Path
import sys
import types
from types import SimpleNamespace

import numpy as np
import pytest

from tools import nanojev_gamer_v1_operand_span_causality_v26 as v26
from tools import nanojev_gamer_v1_sign_information_probe as probe
from tools import nanojev_gamer_v1_operand_causality_v23 as v23
from tools import nanojev_gamer_v1_operand_token_causality_v24 as v24


class CharTokenizer:
    is_fast=True
    def decode(self,ids,skip_special_tokens=False,clean_up_tokenization_spaces=False):
        return ''.join(chr(int(i)) for i in ids)
    def __call__(self,prompt,**kwargs):
        return {'offset_mapping':[(i,i+1) for i in range(len(prompt))],
                'input_ids':[ord(c) for c in prompt]}


def _cases():
    deck=probe.make_deck(14,2,2,False)
    info={'dataset':[x.public() for x in deck],
          'dataset_sha256':probe.deck_hash(deck),
          'lineage':{'model_id':'toy-char'}}
    train=v23._operand_rows(info,'train')
    dev=v23._operand_rows(info,'dev')
    return info,train,dev,v24._indexed_prompts(info)


def test_independent_learned_locator_handles_operand_order_and_fraction_values():
    info,train,dev,prompts=_cases()
    fit,held,_=v24._group_fit_split(train)
    model,features=v26._fit_locator(train,prompts,fit)
    assert len(features)>len(fit)
    for scope,ids in [('heldout_in_range_train_groups',held),
                      ('dev_out_of_range',list(range(len(train),len(train)+len(dev))))]:
        rows=train+dev
        trials=v26._locator_trials(model,rows,prompts,fit,ids,scope)
        assert len(trials)==len(ids)
        assert sum(t['ordered_spans_correct'] for t in trials)==len(ids)
        assert sum(t['text_only_exact_difference'] for t in trials)==len(ids)
        game=next(t for t in trials if t['surface']=='spine_difference')
        assert game['_spans'][0][0]>game['_spans'][1][0]  # DC comes after CC
    assert v26._locate(model,'Here is 1 and 2 in a new unknown structure') is None
    # Inference is prompt-only: changing the metadata cannot change the result.
    source=prompts[dev[0]['archive_index']]
    a=v26._locate(model,source)
    assert a==v26._locate(model,source)
    assert all('1299' not in str(k) for k in model)


def test_locator_never_fits_evaluation_rows_and_fails_if_no_candidates():
    _,train,dev,prompts=_cases()
    fit,hold,_=v24._group_fit_split(train)
    model,_=v26._fit_locator(train,prompts,fit)
    with pytest.raises(ValueError,match='LOCATOR_TRAIN_HOLDOUT_LEAKAGE'):
        v26._locator_trials(model,train,prompts,fit,fit[:1],'bad')
    with pytest.raises(ValueError,match='LOCATOR_FIT_NOT_TRAIN'):
        v26._fit_locator(train+dev,prompts,[len(train)])
    assert v26._locate(model,'no numbers') is None


def test_archive_gating_refuses_wrong_span_instead_of_using_true_operand_vector():
    _,train,_,prompts=_cases()
    row=next(r for r in train if r['surface']=='spine_difference')
    prompt=prompts[row['archive_index']]
    a,b,_,_=v24._spans(prompt,row['surface'])
    tok=CharTokenizer()
    oracle={'first_token_indices':json.dumps(list(range(*a))),
            'second_token_indices':json.dumps(list(range(*b))),
            'first_token_ids':json.dumps([ord(x) for x in prompt[slice(*a)]]),
            'second_token_ids':json.dumps([ord(x) for x in prompt[slice(*b)]]),
            'first_char_span':str(a),'second_char_span':str(b)}
    trial={'_spans':(a,b)}
    assert v26._role_token_match(tok,prompt,trial,oracle)
    wrong={'_spans':(b,a)}
    assert not v26._role_token_match(tok,prompt,wrong,oracle)
    assert not v26._role_token_match(tok,prompt,{'_spans':()},oracle)


def test_case_metrics_separate_text_parse_oracle_control_from_token_dependent_result():
    row={'archive_index':0,'id':'case','group':'g','surface':'source_native',
        'target_fraction':'0','target_sign':0,'first_operand':'1','second_operand':'1'}
    rows=[dict(row,id=f'case{i}') for i in range(6)]
    trials=[{'row_index':i,'id':f'case{i}','group':'g','surface':'source_native',
             'evaluation':'heldout','ordered_spans_correct':1,
             'text_only_exact_difference':1,'_archived_positions_match':False}
            for i in range(6)]
    tokens=[{'id':f'case{i}','variant':'base','stage':'layer_18',
              'evaluation':'heldout','first_reconstructed':'1',
              'second_reconstructed':'1'} for i in range(6)]
    summary,details=v26._metrics(rows,trials,tokens,variant='base',
                                   stage='layer_18',scope='heldout')
    assert summary['independent_text_parser_exact_difference']==6
    assert summary['cached_pair_exact_after_independent_span']==0
    assert summary['cached_difference_exact_after_independent_span']==0
    assert summary['cached_zero_exact_after_independent_span']==0
    for t in trials:t['_archived_positions_match']=True
    summary,_=v26._metrics(rows,trials,tokens,variant='base',
                            stage='layer_18',scope='heldout')
    assert summary['cached_pair_exact_after_independent_span']==6
    assert summary['cached_zero_exact_after_independent_span']==6
    assert summary['complete_six_exact']==1


def test_archive_hash_and_oracle_token_alignment_is_enforced(tmp_path):
    ids=np.full((2,2,3),-1,dtype=np.int64)
    mask=np.zeros((2,2,3),dtype=np.uint8)
    for i in range(2):
        ids[i,:,0]=[49,50]
        mask[i,:,0]=1
    tensors={s:np.ones((2,2,3,4),dtype=np.float32) for s in v26.STAGES}
    path=tmp_path/'archive.npz'
    np.savez_compressed(path,**tensors,operand_token_ids=ids,token_mask=mask,
                        archive_indices=np.asarray([0,1]))
    from tools.nanojev_gamer_v1_precision_continuation_v19 import _sha
    report={'activation_archive_sha256':{'base':_sha(path)}}
    rows=[{'archive_index':0},{'archive_index':1}]
    spans=[{'variant':'base','first_token_ids':'[49]','second_token_ids':'[50]'}]*2
    actual=v26._verify_archive(path,report,'base',rows,spans)
    assert actual[1].shape==(2,2,3)
    tampered=[dict(x,second_token_ids='[51]') for x in spans]
    with pytest.raises(ValueError,match='ARCHIVE_TOKEN_ID_SPAN_MISMATCH'):
        v26._verify_archive(path,report,'base',rows,tampered)
    report['activation_archive_sha256']['base']='bad_hash'
    with pytest.raises(ValueError,match='ACTIVATION_ARCHIVE_HASH_MISMATCH'):
        v26._verify_archive(path,report,'base',rows,spans)


def test_parser_disallows_training_and_requires_v25():
    p=v26.parser()
    assert '--v25-run' in [flag for a in p._actions for flag in a.option_strings]
    with pytest.raises(SystemExit):p.parse_args(['--mode','train'])


def test_complete_read_only_smoke_uses_saved_activations_no_qwen_forward(tmp_path,monkeypatch):
    info,train,dev,prompts=_cases()
    rows=train+dev
    n=len(rows);M=16;W=128;tok=CharTokenizer()
    ids=np.full((n,2,M),-1,dtype=np.int64)
    mask=np.zeros((n,2,M),dtype=np.uint8)
    spans=[];seq={s:np.zeros((n,2,M,W),dtype=np.float32) for s in v26.STAGES}
    for i,row in enumerate(rows):
        p=prompts[row['archive_index']]
        a,b,_,_=v24._spans(p,row['surface'])
        for role,span in enumerate((a,b)):
            char_ids=[ord(c) for c in p[slice(*span)]]
            assert len(char_ids)<=M
            ids[i,role,:len(char_ids)]=char_ids
            mask[i,role,:len(char_ids)]=1
            for j,t in enumerate(char_ids):
                for s in v26.STAGES:seq[s][i,role,j,t]=1
        spans.append({'id':row['id'],'prompt_sha256':row['prompt_sha256'],
              'first_char_span':str(a),'second_char_span':str(b),
              'first_token_indices':json.dumps(list(range(*a))),
              'second_token_indices':json.dumps(list(range(*b))),
              'first_token_ids':json.dumps(ids[i,0,:int(mask[i,0].sum())].tolist()),
              'second_token_ids':json.dumps(ids[i,1,:int(mask[i,1].sum())].tolist())})
    source=tmp_path/'v25';source.mkdir()
    report25={'activation_archive_sha256':{}}
    for variant in ('base','lora'):
        npz=source/(variant+'_ordered_operand_tokens.npz')
        np.savez_compressed(npz,**seq,operand_token_ids=ids,token_mask=mask,
                   archive_indices=np.asarray([r['archive_index'] for r in rows]))
        report25['activation_archive_sha256'][variant]=v26.v19._sha(npz)
    (source/'report.json').write_text(json.dumps(report25))
    (tmp_path/'v23_run').mkdir()
    (tmp_path/'v23_run'/'report.json').write_text(json.dumps({'decoder_scores':[
        {'model':'v14_post_consolidation','dev_correct':108,'zero_correct':48}]}))
    monkeypatch.setattr(v26,'_verify_source',lambda args:(report25,train,dev,
                 rows,prompts,[{'variant':variant,**s} for variant in ('base','lora')
                                for s in spans],info))
    mod=types.ModuleType('transformers')
    mod.AutoTokenizer=SimpleNamespace(from_pretrained=lambda *a,**k:tok)
    monkeypatch.setitem(sys.modules,'transformers',mod)
    args={name:tmp_path/name for name in ('run','expanded_run','input',
       'source_features','prior','v11_run','v14_run','v19_run','v20_run',
       'v21_run','v22_run','v23_run','v24_run')}
    args.update(v25_run=source,output=tmp_path/'v26',mode='audit',
                allow_tokenizer_download=False)
    result=v26._run(SimpleNamespace(**args))
    assert result['status']=='READ_ONLY_INDEPENDENT_SPAN_AUDIT_NO_PROMOTION'
    assert len(result['audits'])==2*5*2
    assert all(x['independent_ordered_spans_correct']==x['cases'] for x in result['audits'])
    assert result['test_cases_used']==0
    assert result['promotion_authorized'] is False
    assert (tmp_path/'v26'/'end_to_end_case_results.csv').exists()
    assert not list((tmp_path/'v26').glob('*.pt'))
    with pytest.raises(FileExistsError):v26._run(SimpleNamespace(**args))
