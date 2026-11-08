"""V29: V28-hybrid-only role reversal training, exact acquired TRAIN retention."""
import json
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
import torch
from tools import nanojev_gamer_v1_role_reversal_retention_v29 as v29
from tools import nanojev_gamer_v1_selector_retention_training_v28 as v28
from tools import nanojev_gamer_v1_candidate_span_causality_v27 as v27
from tools import nanojev_gamer_v1_operand_token_causality_v24 as v24
from tools import nanojev_gamer_v1_sign_information_probe as probe
from tools import nanojev_gamer_v1_operand_causality_v23 as v23


def _deck():
    deck=probe.make_deck(14,2,2,False)
    info={'dataset':[x.public() for x in deck],'dataset_sha256':probe.deck_hash(deck)}
    return (v23._operand_rows(info,'train'),v23._operand_rows(info,'dev'),
            v24._indexed_prompts(info))


def test_new_role_variants_are_train_only_and_have_exact_role_labels():
    train,dev,prompts=_deck();original=v27._entries(train+dev,prompts,len(train),2)
    result=v29._challenge_entries(original,list(range(6)))
    assert len(result)==4*6 and {x['kind'] for x in result}==set(v29.KINDS)
    assert all(x['scope']=='train_role_aug' and x['row_index']<len(train) for x in result)
    for x in result:
        a,b=x['_oracle']
        assert Fraction(x['prompt'][slice(*a)])-Fraction(x['prompt'][slice(*b)])==x['target']
        spans={tuple(c['span']) for c in v27._numeric_candidates(x['prompt'])}
        assert a in spans and b in spans
        assert x['target']==original[x['row_index']]['target']
        if x['kind']=='reordered_false_history':
            assert len(spans)>=4
            assert a[0]>min(c[0] for c in spans)
    hold=v29._challenge_entries(original,list(range(6)),heldout=True)
    assert all(x['scope']=='train_role_holdout' for x in hold)
    with pytest.raises(ValueError,match='V29_TRAIN_SOURCE_LEAK'):
        v29._challenge_entries(original,[len(train)])


def _case(good=True,feature=1.,zero=False):
    # 3 candidates. Gold is (0,1) and base prefers (2,1) if bad.
    base=np.array([[4. if good else 1.,0.],[0.,4.],[3.,0.]],np.float64)
    x=np.array([[feature,0.],[0.,feature],[-feature,0.]],np.float64)
    return {'base':base,'x':x,'truth':(0,1),'indices':[0,1,2]}


def test_training_starts_at_anchor_and_never_loses_original_or_acquired_augmented():
    anchor=np.zeros((2,2),np.float32)
    protected=[_case(good=True) for _ in range(8)] + [_case(good=True,feature=-1) for _ in range(15)]
    old=[_case(good=False) for _ in range(15)]+[_case(good=True) for _ in range(7)]
    new=[_case(good=False) for _ in range(50)]
    old0=[v28._predict(x,anchor) for x in old]
    best,hist,metrics=v29._train_from_v28(torch,protected,old,new,anchor,
        steps=40,lr=.02,batch_size=16,eval_every=5,max_halvings=10,seed=12)
    assert hist
    assert all(x['protected_losses']==0 for x in hist)
    assert v28._retention(protected,best,np.ones(len(protected),bool))==0
    assert metrics['v28_correct_protected']==len(protected)
    assert metrics['final_original_role_correct']>=metrics['v28_original_role_correct']
    assert np.array_equal(anchor,np.zeros_like(anchor))
    assert [v28._predict(x,anchor) for x in old]==old0


def test_updated_selector_rejects_all_unprotectable_candidates():
    protected=[_case(good=True,feature=-1.) for _ in range(6)]
    old=[_case(good=False,feature=1.) for _ in range(10)]
    best,hist,metrics=v29._train_from_v28(torch,protected,old,old,
        np.zeros((2,2),np.float32),steps=10,lr=0.5,batch_size=12,
        eval_every=5,max_halvings=0,seed=41)
    assert v28._retention(protected,best,np.ones(len(protected),bool))==0
    assert metrics['accepted']+metrics['rejected']==len(hist)


def test_parser_requires_v28_and_new_output_and_no_audit():
    p=v29.parser()
    flags={x for action in p._actions for x in action.option_strings}
    assert {'--v27-run','--v28-run','--max-halvings','--seed','--steps'}<=flags
    with pytest.raises(SystemExit):p.parse_args(['--mode','audit'])


def test_v28_augmented_source_integrity_rejects_tampering(tmp_path):
    train,dev,prompts=_deck();original=v27._entries(train+dev,prompts,len(train),2)
    fit,*_=v24._group_fit_split(train)
    aug=v28._train_entries(original,fit)
    # One legitimate candidate per row is insufficient and must fail coverage.
    root=tmp_path/'v28';root.mkdir()
    x=np.zeros((1,2),np.float32)
    np.savez_compressed(root/'augmented_train_layer18.npz',layer_18=x,token_ids=np.array([5]))
    (root/'augmented_train_candidates.json').write_text(json.dumps([]))
    report={'augmented_archive_sha256':'wrong','augmented_train_cases':len(aug)}
    with pytest.raises(ValueError,match='V29_V28_AUGMENTED_ARCHIVE_HASH'):
        v29._verify_augmented_archive(root,report,original,fit)


def test_end_to_end_resume_from_real_v28_disposable_checkpoint(tmp_path,monkeypatch):
    """Complete orchestration on a synthetic MOCK model; not CUDA capability evidence."""
    from tools import nanojev_gamer_v1_handoff as gamer
    train,dev,prompts=_deck();rows=train+dev
    original=v27._entries(rows,prompts,len(train),2)
    head=tmp_path/'frozen_head.pt';head.write_bytes(b'NO_CHANGES_ALLOWED')
    adapter=tmp_path/'adapter';adapter.mkdir();(adapter/'adapter.bin').write_bytes(b'frozen')
    info={'lineage':{'model_id':'toy','configuration':{'max_length':1024},
           'checkpoint_path':str(head),'checkpoint_sha256':v19sha(head),
           'adapter_dir':str(adapter),'adapter_sha256':'a'*64},
          'dataset_sha256':'toy-fixed-dataset'}

    def mock_capture(_torch,tok,model,entries,**kwargs):
        meta=[];vec=[]
        for i,e in enumerate(entries):
            for c in v27._numeric_candidates(e['prompt']):
                start=len(vec)
                role=0 if tuple(c['span'])==tuple(e['_oracle'][0]) else (1 if tuple(c['span'])==tuple(e['_oracle'][1]) else -1)
                for letter in c['value']:
                    arr=np.zeros(130,np.float32);arr[ord(letter)%128]=30
                    if role>=0:arr[128+role]=20
                    vec.append(arr)
                meta.append({'case_index':i,'span':list(c['span']),'token_ids':
                      [ord(ch)%128 for ch in c['value']],'text':c['value'],
                      'marker':c['marker'],'start':start,'stop':len(vec)})
        return meta,{'layer_18':np.asarray(vec,dtype=np.float32)}

    class Tok:
        def decode(self,ids,**kw):return ''.join(chr(int(i)) for i in ids)
        def __call__(self,s,**kw):return {'input_ids':[ord(ch)%128 for ch in s]}

    v27_root=tmp_path/'v27';v27_root.mkdir()
    meta,arrays=mock_capture(None,None,None,original)
    archive=v27_root/'base_all_candidate_activations.npz'
    np.savez_compressed(archive,**arrays,token_ids=np.array(
        [t for m in meta for t in m['token_ids']],dtype=np.int64))
    (v27_root/'base_candidate_offsets.json').write_text(json.dumps(meta))
    (v27_root/'report.json').write_text(json.dumps({
        'schema':v27.SCHEMA,'status':'READ_ONLY_ALL_CANDIDATES_NO_PROMOTION',
        'frozen_lineage':info['lineage'],'test_cases_forwarded':0,
        'promotion_authorized':False,'production_weights_modified':False,
        'readout_fit_on_dev_or_test':False,
        'original_capture_max_abs_difference':{'base':0},
        'stress_limit_per_condition':2,'capture_cases':len(original),
        'fit_train_groups':len(v24._group_fit_split(train)[0])//6,
        'activation_archive_sha256':{'base':v19sha(archive)}}))
    common={k:tmp_path/k for k in ('run','expanded_run','input','source_features','prior',
          'v11_run','v14_run','v19_run','v20_run','v21_run','v22_run','v23_run',
          'v24_run','v25_run','v26_run')}
    common.update(v27_run=v27_root,mode='train',device='cpu',capture_batch=4,
        max_input_tokens=1024,max_candidates=16,max_candidate_tokens=16,
        steps=10,lr=.01,batch_size=12,eval_every=5,max_halvings=4,
        seed=29,allow_model_download=False)
    monkeypatch.setattr(v28.v26,'_verify_source',lambda args:(None,train,dev,rows,prompts,None,info))
    monkeypatch.setattr(v28.spine,'adapter_identity',lambda p:'a'*64)
    monkeypatch.setattr(v28.spine,'cache_policy',lambda x:None)
    monkeypatch.setattr(v27,'_capture_variant',mock_capture)
    monkeypatch.setattr(gamer,'model_components',lambda *a,**kw:
             (torch,Tok(),torch.nn.Linear(1,1)))
    v28_args=dict(common,output=tmp_path/'v28')
    r28=v28._run(SimpleNamespace(**v28_args))
    assert r28['promotion_authorized'] is False
    # Mock activations make every synthetic TRAIN case trivially separable, so
    # V28 cannot show acquisition. Mark THIS TEST FIXTURE as approved to test
    # the V29 continuation path. Real V29 requires an actually approved V28.
    synthetic_report=json.loads((tmp_path/'v28'/'report.json').read_text())
    for policy in ('neural_only','hybrid'):
        synthetic_report['acceptance'][policy]['all_gates_met']=True
    (tmp_path/'v28'/'report.json').write_text(json.dumps(synthetic_report))
    v29_args=dict(common,v28_run=tmp_path/'v28',output=tmp_path/'v29',
                  steps=10,eval_every=5,lr=.01)
    original_hash=v19sha(head)
    r29=v29._run(SimpleNamespace(**v29_args))
    from tools import nanojev_gamer_v1_zero_line_decoder_v30 as v30
    v30args=dict(common,v28_run=tmp_path/'v28',v29_run=tmp_path/'v29',
        output=tmp_path/'v30',tokenizer_path=None,
        case_id='dev.offset.0003.zero.spine_difference::role_reordered')
    v30report=v30._run(SimpleNamespace(**v30args))
    assert v30report['promotion_authorized'] is False
    assert v30report['no_dev_fitting_or_checkpoint_selection'] is True
    assert v30report['acceptance_gates']['previously_correct_train_token_retention']
    assert (tmp_path/'v30'/'decoder_case_results.csv').is_file()
    assert r29['promotion_authorized'] is False
    assert r29['dev_used_for_checkpoint_selection'] is False
    assert r29['no_qwen_lora_or_original_head_training'] is True
    assert r29['neural_only_checkpoint_unchanged'] is True
    assert r29['retention_losses']==0
    assert r29['previously_correct_v28_augmented_train']==r28['training']['hybrid']['final_aug_correct']
    assert v19sha(head)==original_hash
    assert (tmp_path/'v29'/'role_training_history.csv').exists()
    assert (tmp_path/'v29'/'disposable_v29_hybrid_role_candidate.pt').exists()
    assert (tmp_path/'v29'/'role_case_results.csv').exists()
    with pytest.raises((ValueError,FileExistsError),match='OUTPUT_ALREADY_EXISTS|exist'):
        v29._run(SimpleNamespace(**v29_args))
    (tmp_path/'v28'/'disposable_selector_candidates.pt').write_bytes(b'corrupted')
    v29_args['output']=tmp_path/'v29-tamper'
    with pytest.raises(ValueError,match='V29_V28_PROVENANCE_MISMATCH'):
        v29._run(SimpleNamespace(**v29_args))


def v19sha(path):return v29.v19._sha(path)
