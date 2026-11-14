"""V27: all-candidate capture; independent neural vs text; no oracle borrowing."""
from fractions import Fraction
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
import torch
from tools import nanojev_gamer_v1_candidate_span_causality_v27 as v27
from tools import nanojev_gamer_v1_operand_causality_v23 as v23
from tools import nanojev_gamer_v1_operand_token_causality_v24 as v24
from tools import nanojev_gamer_v1_sign_information_probe as probe


class CharTok:
    is_fast=True; padding_side='right'
    def encode(self,p,add_special_tokens=True):return [ord(c)%128 for c in p]
    def decode(self,ids,skip_special_tokens=False,clean_up_tokenization_spaces=False):
        return ''.join(chr(int(i)) for i in ids)
    def __call__(self,prompts,**kwargs):
        if isinstance(prompts,str):prompts=[prompts]
        n=max(map(len,prompts))
        return {'input_ids':torch.tensor([[ord(c)%128 for c in p]+[0]*(n-len(p)) for p in prompts]),
                'attention_mask':torch.tensor([[1]*len(p)+[0]*(n-len(p)) for p in prompts]),
                'offset_mapping':torch.tensor([[(i,i+1) for i in range(len(p))]+[(0,0)]*(n-len(p)) for p in prompts])}


class Layer(torch.nn.Module):
    def forward(self,z):return (z+0.0001,)


class SmallCore(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.embed_tokens=torch.nn.Embedding(128,8)
        self.layers=torch.nn.ModuleList([Layer() for _ in range(28)])
        self.norm=torch.nn.LayerNorm(8)


class SmallModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        torch.manual_seed(22);self.model=SmallCore();self.eval()
    def forward(self,input_ids,attention_mask,**kw):
        x=self.model.embed_tokens(input_ids)
        for layer in self.model.layers:x=layer(x)[0]
        return SimpleNamespace(logits=self.model.norm(x)[:,-1,:])


def _deck():
    deck=probe.make_deck(14,2,2,False)
    info={'dataset':[r.public() for r in deck], 'dataset_sha256':probe.deck_hash(deck),
          'lineage':{'model_id':'toy','configuration':{'max_length':1024}}}
    tr=v23._operand_rows(info,'train');dev=v23._operand_rows(info,'dev')
    rows=tr+dev
    prompts=v24._indexed_prompts(info)
    return info,tr,dev,rows,prompts


def test_challenges_preserve_exact_target_and_invert_text_role_order():
    _,tr,dev,rows,prompts=_deck()
    examples=v27._entries(rows,prompts,len(tr),4)
    assert len(examples)==len(rows)+16
    for e in examples:
        spans=v27._truth_spans(e['prompt'],e['surface'])
        assert spans==e['_oracle']
        assert Fraction(e['prompt'][slice(*spans[0])])-Fraction(e['prompt'][slice(*spans[1])])==e['target']
        assert len(v27._numeric_candidates(e['prompt']))>=2
        if e['kind']=='role_reordered' and e['surface']=='spine_difference':
            assert spans[0][0]<spans[1][0]  # T textual order reversed from baseline
        if e['kind']=='role_reordered' and e['surface']=='source_native':
            assert spans[0][0]>spans[1][0]
    assert v27._numeric_candidates('Unrelated 17, 29; expression 10 and 2')


def test_capture_all_candidates_includes_distractor_and_both_true_operands():
    _,tr,dev,rows,prompts=_deck()
    entries=v27._entries(rows,prompts,len(tr),2)
    selected=[e for e in entries if e['kind']=='distractor_prefix'][:2]
    before={k:v.clone() for k,v in SmallModel().state_dict().items()}
    model=SmallModel()
    metadata,vectors=v27._capture_variant(torch,CharTok(),model,selected,
        variant='base',device='cpu',batch=2,max_input_tokens=1024,
        max_candidates=16,max_candidate_tokens=16)
    assert set(vectors)==set(v27.STAGES)
    assert len(metadata)>4  # captured distractors too
    assert any(c['text']=='314159265' for c in metadata)
    for i,e in enumerate(selected):
        assert all(any(c['case_index']==i and tuple(c['span'])==span for c in metadata)
                   for span in e['_oracle'])
    assert all(torch.equal(before[k],v) for k,v in model.state_dict().items())
    with pytest.raises(ValueError,match='CANDIDATE_COUNT_EXCEEDS_LIMIT'):
        v27._capture_batch(torch,SmallModel(),CharTok(),selected,device='cpu',
            max_input_tokens=1024,max_candidates=3,max_candidate_tokens=16)


def test_neural_training_is_train_only_and_inference_has_no_oracle_access():
    _,tr,dev,rows,prompts=_deck()
    entries=v27._entries(rows,prompts,len(tr),2)
    # A synthetic hidden state containing pure role features only, not truth at inference.
    metadata=[];by_case={};vec=[]
    for i,e in enumerate(entries):
        for c in v27._numeric_candidates(e['prompt']):
            j=len(metadata);by_case.setdefault(i,[]).append(j)
            marker=c['marker']
            v=np.asarray([int(marker=='payoff_DC' or marker=='coord_spacecraft'),
                          int(marker=='payoff_CC' or marker=='coord_beacon'),
                          int(marker.startswith('payoff_')),
                          1.0],dtype=np.float32)
            vec.append(v)
            metadata.append({'case_index':i,'span':list(c['span']),'marker':marker,
                 'text':c['value'],'token_ids':[ord(c['value'][0])],
                 'start':j,'stop':j+1})
    by_case,gold=v27._gold_candidate_ids(entries,metadata)
    weights=v27._fit_neural_selector({'x':np.asarray(vec)},metadata,entries,gold,
                      list(range(10)),stage='x')
    assert weights.shape==(2,4)
    for i in range(len(tr),len(entries)):
        predicted=v27._select_neural(weights,np.asarray(vec),metadata,by_case[i])
        assert predicted==gold[i]
    with pytest.raises(ValueError,match='NEURAL_FIT_NOT_TRAIN'):
        v27._fit_neural_selector({'x':np.asarray(vec)},metadata,entries,gold,
                      [len(tr)],stage='x')


def test_train_only_token_centroids_and_oov_no_credit():
    meta=[{'case_index':0,'start':0,'stop':1,'token_ids':[49]},
          {'case_index':0,'start':1,'stop':2,'token_ids':[50]},
          {'case_index':1,'start':2,'stop':3,'token_ids':[57]}]
    vectors=np.asarray([[1,0,0],[0,1,0],[1,0,0]],dtype=np.float32)
    fit=v27._fit_token_centroids({'x':vectors},meta,[(0,1)], [0], 'x')
    assert fit[0]==[49,50]
    value,ids,known=v27._decode_candidate(vectors,meta[2],fit,CharTok())
    assert ids==[49] and not known and value is None



def test_independent_parity_refuses_substituted_token_and_nonmatching_activation(tmp_path):
    cases=[{'archive_index':0}]
    metadata=[{'start':0,'stop':1,'token_ids':[49]},
              {'start':1,'stop':2,'token_ids':[50]}]
    gold=[(0,1)]
    vectors={s:np.asarray([[1.,0.],[0.,1.]],dtype=np.float32)
             for s in v27.STAGES}
    archive={s:np.asarray([[[[1.,0.]],[[0.,1.]]]],dtype=np.float32)
             for s in v27.STAGES}
    archive.update(operand_token_ids=np.asarray([[[49],[50]]]),
                   token_mask=np.ones((1,2,1),dtype=np.uint8),
                   archive_indices=np.asarray([0]))
    p=tmp_path/'old.npz';np.savez_compressed(p,**archive)
    assert v27._v25_independent_capture_parity(p,metadata,vectors,gold,cases)==0
    wrong=[dict(metadata[0],token_ids=[51]),metadata[1]]
    with pytest.raises(ValueError,match='V25_SELECTED_TOKEN_ID_PARITY'):
        v27._v25_independent_capture_parity(p,wrong,vectors,gold,cases)
    vectors['layer_18']=np.asarray([[9.,0.],[0.,1.]],dtype=np.float32)
    with pytest.raises(ValueError,match='V25_INDEPENDENT_FORWARD_PARITY_MISMATCH'):
        v27._v25_independent_capture_parity(p,metadata,vectors,gold,cases)

def test_parser_refuses_training_and_has_all_archive_dependencies():
    p=v27.parser()
    for flag in ('--v25-run','--v26-run','--max-candidates','--stress-limit'):
        assert flag in [f for a in p._actions for f in a.option_strings]
    with pytest.raises(SystemExit):p.parse_args(['--mode','train'])


def test_full_v27_smoke_is_readonly_and_saves_full_candidate_sequences(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_handoff as gamer
    info,tr,dev,rows,prompts=_deck()
    head=tmp_path/'head.pt';head.write_bytes(b'head')
    adapter=tmp_path/'adapter';adapter.mkdir();(adapter/'adapter.bin').write_bytes(b'weights')
    info['lineage'].update(checkpoint_path=str(head),
       checkpoint_sha256=v27.spine.file_sha(head),adapter_dir=str(adapter),
       adapter_sha256='a'*64)
    input_dir=tmp_path/'input';input_dir.mkdir()
    args={k:tmp_path/k for k in ('run','expanded_run','source_features','prior',
        'v11_run','v14_run','v19_run','v20_run','v21_run','v22_run',
        'v23_run','v24_run','v25_run','v26_run')}
    args.update(input=input_dir,output=tmp_path/'v27',mode='audit',device='cpu',
        capture_batch=8,max_input_tokens=1024,max_candidates=16,
        max_candidate_tokens=16,stress_limit=2,allow_model_download=False)
    v25p=args['v25_run'];v25p.mkdir();(v25p/'report.json').write_text('{}')
    for var in ('base','lora'):
        (v25p/(var+'_ordered_operand_tokens.npz')).write_bytes(b'archive')
    v26p=args['v26_run'];v26p.mkdir()
    (v26p/'report.json').write_text(json.dumps({
      'schema':v27.v26.SCHEMA,'status':'READ_ONLY_INDEPENDENT_SPAN_AUDIT_NO_PROMOTION',
      'v25_report_sha256':v27.v19._sha(v25p/'report.json'),
      'production_weights_modified':False,'promotion_authorized':False}))
    info['dataset_sha256']='stub-dataset'
    v25_report={'activation_archive_sha256':{'base':'stub-base','lora':'stub-lora'}}
    r26=json.loads((v26p/'report.json').read_text())
    r26.update(v25_activation_archive_sha256=v25_report['activation_archive_sha256'],
               frozen_lineage=info['lineage'],source_dataset_sha256=info['dataset_sha256'],
               test_cases_used=0,original_zero_line_authority_unchanged=True)
    (v26p/'report.json').write_text(json.dumps(r26))
    monkeypatch.setattr(v27.v26,'_verify_source',lambda args:(v25_report,tr,dev,rows,prompts,None,info))
    monkeypatch.setattr(v27.spine,'adapter_identity',lambda p:'a'*64)
    monkeypatch.setattr(v27.spine,'cache_policy',lambda flag:None)
    monkeypatch.setattr(gamer,'model_components',lambda *a,**k:(torch,CharTok(),SmallModel()))
    monkeypatch.setattr(v27,'_v25_independent_capture_parity',lambda *a,**k:0.0)
    result=v27._run(SimpleNamespace(**args))
    assert result['status']=='READ_ONLY_ALL_CANDIDATES_NO_PROMOTION'
    assert not result['promotion_authorized'] and not result['checkpoint_saved']
    assert result['test_cases_forwarded']==0 and result['capture_cases']==len(rows)+8
    assert len(result['comparisons'])==2*5*3*2
    assert (tmp_path/'v27'/'all_candidate_end_to_end_comparison.csv').is_file()
    for var in ('base','lora'):
        with np.load(tmp_path/'v27'/(var+'_all_candidate_activations.npz'),allow_pickle=False) as z:
            assert z['layer_18'].shape[0]==z['token_ids'].shape[0]
            assert z['embedding'].shape[0]>len(rows)*2
    with pytest.raises(FileExistsError):v27._run(SimpleNamespace(**args))
