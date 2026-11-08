"""V28: selector-only training with TRAIN retention and no DEV model selection."""
from fractions import Fraction
from pathlib import Path
import numpy as np
import pytest
import torch
from tools import nanojev_gamer_v1_selector_retention_training_v28 as v28
from tools import nanojev_gamer_v1_candidate_span_causality_v27 as v27
from tools import nanojev_gamer_v1_operand_causality_v23 as v23
from tools import nanojev_gamer_v1_operand_token_causality_v24 as v24
from tools import nanojev_gamer_v1_sign_information_probe as probe


def _deck():
    deck=probe.make_deck(14,2,2,False)
    info={'dataset':[r.public() for r in deck],'dataset_sha256':probe.deck_hash(deck)}
    train=v23._operand_rows(info,'train');dev=v23._operand_rows(info,'dev')
    return train,dev,v24._indexed_prompts(info)


def test_train_augmentations_use_only_fit_groups_and_score_current_task():
    train,dev,prompts=_deck()
    original=v27._entries(train+dev,prompts,len(train),3)
    subset=[0,1,2,3,4,5,6,7]
    extra=v28._train_entries(original,subset)
    assert len(extra)==6*len(subset)
    assert len({e['kind'] for e in extra})==6
    assert all(e['scope']=='train_aug' and e['row_index'] in subset for e in extra)
    for e in extra:
        first,second=e['_oracle']
        assert Fraction(e['prompt'][slice(*first)])-Fraction(e['prompt'][slice(*second)])==e['target']
        spans=[tuple(c['span']) for c in v27._numeric_candidates(e['prompt'])]
        assert first in spans and second in spans
        if e['kind']=='false_role_example':
            assert len(spans)>=4
            assert first[0]>spans[0][0] and second[0]>spans[0][0]
        if e['kind']!='novel_operands':
            assert e['target']==original[e['row_index']]['target']
    with pytest.raises(ValueError,match='V28_TRAIN_AUGMENT_LEAK'):
        v28._train_entries(original,[len(train)])


def _case(*,bad=False,baseline_margin=5.,feature_sign=1.):
    x=np.asarray([[feature_sign,0],[-feature_sign,0],[0,0]],dtype=np.float32)
    first_score=0.5 if bad else baseline_margin
    base=np.asarray([[first_score,0],[0,baseline_margin],[1.,0.]],dtype=np.float32)
    return {'base':base,'x':x,'truth':(0,1)}


def test_selector_trains_from_synthetic_and_never_loses_previously_correct_train():
    original=[_case() for _ in range(12)]
    stress=[_case(bad=True) for _ in range(48)]
    before=[c['base'].copy() for c in original+stress]
    best,hist,summary=v28._train_selector(torch,original,stress,None,
        steps=80,lr=.04,batch_size=12,eval_every=5,max_halvings=10,
        seed=11,use_context=False)
    assert hist and summary['accepted']>=1
    assert summary['final_aug_correct']>summary['initial_aug_correct']
    assert v28._retention(original,best,np.ones(len(original),bool))==0
    assert all(np.array_equal(c['base'],b) for c,b in zip(original+stress,before))
    assert all(x['protected_original_losses']==0 for x in hist)


def test_retention_rejects_updates_that_cannot_preserve_original():
    original=[_case(baseline_margin=2.,feature_sign=1.) for _ in range(5)]
    stress=[_case(bad=True,feature_sign=-1.) for _ in range(10)]
    best,hist,summary=v28._train_selector(torch,original,stress,None,
        steps=20,lr=.1,batch_size=8,eval_every=5,max_halvings=6,
        seed=17,use_context=False)
    assert v28._retention(original,best,np.ones(len(original),bool))==0
    assert all(h['protected_original_losses']==0 for h in hist)
    assert summary['accepted']+summary['rejected']==len(hist)


def test_select_uses_no_oracle_and_supports_extra_candidates():
    case={'base':np.asarray([[2,0],[0,2],[0,0],[-1,-1]],dtype=np.float32),
          'x':np.zeros((4,3),np.float32),'truth':(0,1)}
    assert v28._predict(case,np.zeros((2,3),np.float32))==(0,1)
    changed=dict(case,truth=(2,3))
    assert v28._predict(changed,np.zeros((2,3),np.float32))==(0,1)


def test_summary_counts_complete_stress_groups_and_zero_separately():
    records=[]
    for i in range(6):
        records.append({'scope':'stress_dev','kind':'role_reordered','policy':'x',
                        'case_id':'dev.offset.0000.sign.surface::role_reordered',
                        'role_spans_correct':1,'both_operands_exact':1,
                        'exact_difference':1,'zero_exact':int(i%3==0),
                        'target_sign':0 if i%3==0 else (1 if i%3==1 else -1),'oov':0})
    summary=v28._summarize(records)[0]
    assert summary['complete_six_exact']==1 and summary['complete_six_groups']==1
    assert summary['zero_total']==2 and summary['zero_exact']==2
    records[0]['exact_difference']=0
    assert v28._summarize(records)[0]['complete_six_exact']==0


def test_only_training_checkpoint_selection_not_dev_and_parser_rejects_audit():
    p=v28.parser()
    flags={option for a in p._actions for option in a.option_strings}
    for x in ('--v27-run','--max-halvings','--steps','--batch-size','--eval-every'):
        assert x in flags
    with pytest.raises(SystemExit):p.parse_args(['--mode','audit'])


def test_train_feature_context_does_not_use_truth():
    prompt='Irrelevant 11, 22. The task: DC=7, DD=5, CC=3, CD=1.'
    all_candidates=v27._numeric_candidates(prompt)
    for c in all_candidates:
        features=v28._local_features(prompt,c,all_candidates)
        assert len(features)==len(v28.MARKERS)+13
        assert np.isfinite(features).all()
        assert features.max()<=1 and features.min()>=0


def test_full_case_pipeline_requires_correct_span_even_if_values_equal():
    class FakeTok:
        def decode(self,ids,**kwargs):return ''.join(map(chr,ids))
    tok=FakeTok()
    prompt='11 11 11 11'
    entries=[{'id':'sample','kind':'original','prompt':prompt,'target':Fraction(0),
              '_oracle':((0,2),(3,5))}]
    meta=[];vec=[]
    for j,c in enumerate(v27._numeric_candidates(prompt)):
        meta.append({'case_index':0,'span':list(c['span']),'start':2*j,
                     'stop':2*j+2,'token_ids':[49,49],'marker':c['marker']})
        vec.extend([[1,0],[1,0]])
    vectors=np.asarray(vec,dtype=np.float32)
    by_case,gold=v27._gold_candidate_ids(entries,meta)
    weights=np.asarray([[1,0],[0,1]],dtype=np.float32)
    # Explicitly choose the wrong two identical-text candidates.
    theta=np.zeros((2,2),dtype=np.float32)
    token_fit=([49],np.asarray([[1,0]],dtype=np.float64))
    result=v28._full_evaluation(entries,meta,vectors,by_case,gold,[0],
                 weights,theta,token_fit,tok,scope='audit',policy='neural_only')[0]
    assert result['exact_difference']==0 or result['role_spans_correct']==1


def test_v27_source_archive_integrity_and_end_to_end_training_smoke(tmp_path,monkeypatch):
    """Actual orchestration, CSV/checkpoint outputs; Qwen forward is mocked."""
    from types import SimpleNamespace
    from tools import nanojev_gamer_v1_handoff as gamer
    import json
    train,dev,prompts=_deck()
    rows=train+dev
    original=v27._entries(rows,prompts,len(train),2)
    head=tmp_path/'head.pt';head.write_bytes(b'head-unchanged')
    adapter=tmp_path/'adapter';adapter.mkdir();(adapter/'adapter.bin').write_bytes(b'do-not-change')
    info={'lineage':{'model_id':'toy','configuration':{'max_length':1024},
           'checkpoint_path':str(head),
           'checkpoint_sha256':v28.v19._sha(head),
           'adapter_dir':str(adapter),'adapter_sha256':'a'*64},
          'dataset_sha256':'unit-test-dataset'}

    # Token ID is encoded in the first 128 dimensions. Extra dimensions
    # encode a synthetic role feature in this SMOKE MODEL ONLY. This is not
    # evidence of Qwen mathematical ability.
    def capture_mock(_torch,tok,model,entries,**kwargs):
        meta=[];vec=[]
        for i,e in enumerate(entries):
            for c in v27._numeric_candidates(e['prompt']):
                a=len(vec);truth=e['_oracle']
                role=0 if c['span']==truth[0] else 1 if c['span']==truth[1] else -1
                for letter in c['value']:
                    v=np.zeros(130,dtype=np.float32);v[ord(letter)%128]=30
                    if role>=0:v[128+role]=20
                    vec.append(v)
                meta.append({'case_index':i,'span':list(c['span']),
                    'token_ids':[ord(ch)%128 for ch in c['value']],
                    'text':c['value'],'marker':c['marker'],'start':a,'stop':len(vec)})
        return meta,{'layer_18':np.asarray(vec,dtype=np.float32)}

    class MockTok:
        def decode(self,ids,**kw):return ''.join(chr(int(x)) for x in ids)
        def __call__(self,s,**kw):
            return {'input_ids':[ord(ch)%128 for ch in s]}

    v27p=tmp_path/'v27';v27p.mkdir()
    meta,arrays=capture_mock(None,None,None,original)
    archive=v27p/'base_all_candidate_activations.npz'
    np.savez_compressed(archive,**arrays,token_ids=np.asarray(
        [t for c in meta for t in c['token_ids']],dtype=np.int64))
    (v27p/'base_candidate_offsets.json').write_text(json.dumps(meta))
    report={'schema':v27.SCHEMA,'status':'READ_ONLY_ALL_CANDIDATES_NO_PROMOTION',
        'frozen_lineage':info['lineage'],'test_cases_forwarded':0,
        'promotion_authorized':False,'production_weights_modified':False,
        'readout_fit_on_dev_or_test':False,'original_capture_max_abs_difference':{'base':0},
        'stress_limit_per_condition':2,'capture_cases':len(original),
        'fit_train_groups':len(v24._group_fit_split(train)[0])//6,
        'activation_archive_sha256':{'base':v28.v19._sha(archive)}}
    (v27p/'report.json').write_text(json.dumps(report))
    args={k:tmp_path/k for k in ('run','expanded_run','input','source_features',
        'prior','v11_run','v14_run','v19_run','v20_run','v21_run','v22_run',
        'v23_run','v24_run','v25_run','v26_run')}
    args.update(v27_run=v27p,output=tmp_path/'v28',mode='train',device='cpu',
        capture_batch=4,max_input_tokens=1024,max_candidates=16,
        max_candidate_tokens=16,steps=10,eval_every=5,lr=.01,
        batch_size=12,max_halvings=4,seed=29,allow_model_download=False)
    monkeypatch.setattr(v28.v26,'_verify_source',lambda args:(None,train,dev,rows,prompts,None,info))
    monkeypatch.setattr(v28.spine,'adapter_identity',lambda p:'a'*64)
    monkeypatch.setattr(v28.spine,'cache_policy',lambda x:None)
    monkeypatch.setattr(v27,'_capture_variant',capture_mock)
    monkeypatch.setattr(gamer,'model_components',
                        lambda *a,**kw:(torch,MockTok(),torch.nn.Linear(1,1)))
    before=head.read_bytes()
    output=v28._run(SimpleNamespace(**args))
    assert output['status']=='SELECTOR_TRAINING_COMPLETE_NO_PROMOTION'
    assert output['promotion_authorized'] is False
    assert output['production_weights_modified'] is False
    assert output['dev_used_for_checkpoint_selection'] is False
    assert output['augmented_train_cases']==6*len(v24._group_fit_split(train)[0])
    assert head.read_bytes()==before
    assert (tmp_path/'v28'/'disposable_selector_candidates.pt').is_file()
    assert (tmp_path/'v28'/'selector_comparison.csv').is_file()
    assert (tmp_path/'v28'/'selector_training_history.csv').is_file()
    assert (tmp_path/'v28'/'augmented_train_layer18.npz').is_file()
    with pytest.raises((FileExistsError,ValueError),match='OUTPUT_ALREADY_EXISTS|exist'):
        v28._run(SimpleNamespace(**args))
    # Archived activation tampering must fail BEFORE any training or capture.
    args['output']=tmp_path/'v28-retry';archive.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='V27_BASE_ARCHIVE_HASH'):
        v28._run(SimpleNamespace(**args))


def test_true_unseen_token_identity_challenge_requires_training_vocab_gap():
    class DeliberateNovelToken:
        def __call__(self,text,**kwargs):
            ids=[999,49] if text=='987654321987' else [49]
            return {'input_ids':ids}
    # This test concerns challenge construction, not the scoring-oracle format;
    # an authentic prompt is needed for both operands.
    train,dev,prompts=_deck()
    originals=v27._entries(train+dev,prompts,len(train),1)
    metadata=[{'case_index':i,'token_ids':[49]} for i in range(5)]
    entries,status=v28._new_token_challenge(DeliberateNovelToken(),metadata,
                                           list(range(5)),originals,limit=2)
    assert status['true_oov_found'] and status['novel_token_ids']==[999,49]
    assert len(entries)==2 and all(e['target']==0 for e in entries)
    assert all(e['scope']=='oov_holdout' for e in entries)
