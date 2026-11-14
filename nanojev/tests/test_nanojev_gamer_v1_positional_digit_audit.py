"""Frozen GAMER digit-position audit: alignment, provenance and attribution."""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
import re
from types import SimpleNamespace

import numpy as np
import pytest

from tools import nanojev_gamer_v1_positional_digit_audit as lab
from tools import nanojev_gamer_v1_compositional_belief_lab as compose


class MultiDigitTokenizer:
    padding_side='right'

    def __init__(self):
        self.dictionary={}

    def __call__(self, text, **kwargs):
        # Splits numerical substrings into at most three digits per token,
        # including a two-token four-digit denominator.
        pieces=[m for m in re.finditer(r'\d{1,3}|[^\d]',text)]
        ids=[]
        for match in pieces:
            word=match.group()
            if word not in self.dictionary:
                self.dictionary[word]=len(self.dictionary)+1
            ids.append(self.dictionary[word])
        return {'input_ids':ids,'offset_mapping':[m.span() for m in pieces]}


def _case(q):
    from tools import nanojev_gamer_v1_zero_sign_frob as prompts
    from tools import nanojev_two_game_spine_smoke as spine
    state=spine.Stage(Fraction(113),Fraction(119),Fraction(125),Fraction(115))
    return SimpleNamespace(id='example',prompt=prompts._spine_belief_prompt(state,q))


def _frame_from_tokens(align,token_vectors):
    row=np.zeros((compose.SLOTS,1024),dtype=np.float32)
    for t in align['tokens']:
        row[t['slot']]=token_vectors[t['text']]
    return row.reshape(-1)


def test_char_positions_and_slots_for_four_digit_denominator():
    case=_case(Fraction(3581,5700))
    row=lab._token_alignment(MultiDigitTokenizer(),[case])[0]
    denominator=row['denominator']
    assert denominator['true_text']=='5700'
    assert denominator['token_pattern']==[3,1]
    assert [t['text'] for t in denominator['tokens']]==['570','0']
    assert [t['slot'] for t in denominator['tokens']]==[4,5]
    assert [d['activation_slot'] for d in denominator['digits']]==[4,4,4,5]
    assert [d['token_char_offset'] for d in denominator['digits']]==[0,1,2,0]
    for digit in denominator['digits']:
        assert case.prompt[digit['prompt_char_offset']]==digit['true_digit']


def test_six_digit_component_has_no_truncation():
    case=_case(Fraction(123457,200011))
    # A one-digit-token tokenizer is the maximum six-slot case.
    class OneDigitTokenizer(MultiDigitTokenizer):
        def __call__(self,text,**kwargs):
            matches=list(re.finditer(r'\d|[^\d]',text))
            return {'input_ids':list(range(1,len(matches)+1)),
                    'offset_mapping':[m.span() for m in matches]}
    row=lab._token_alignment(OneDigitTokenizer(),[case])[0]
    for part in lab.PARTS:
        assert len(row[part]['tokens'])==6
        assert [p['slot'] for p in row[part]['tokens']]==list(range(6))
        assert len(row[part]['digits'])==6


def test_span_boundary_crossing_fails_closed():
    case=_case(Fraction(3581,5700))
    class Bad(MultiDigitTokenizer):
        def __call__(self,text,**kwargs):
            row=super().__call__(text,**kwargs)
            from tools import nanojev_gamer_v1_token_localization_lab as token
            a,b=token._spans(text)['belief'][0]
            i=next(k for k,p in enumerate(row['offset_mapping']) if p[0]==a)
            row['offset_mapping'][i]=(a,b)
            return row
    with pytest.raises(ValueError,match='CROSSES_BOUNDARY|PARTIAL_NUMERIC_SPAN'):
        lab._token_alignment(Bad(),[case])


def test_train_only_token_prototype_recovers_known_embedded_token():
    cases=[_case(Fraction(3581,5700)),_case(Fraction(3619,5700))]
    tok=MultiDigitTokenizer()
    alignment=lab._token_alignment(tok,cases)
    all_words={t['text'] for row in alignment for comp in row.values() for t in comp['tokens']}
    rng=np.random.default_rng(7)
    vectors={word:rng.normal(size=1024).astype(np.float32)
             for word in sorted(all_words)}
    frames={part:np.stack([_frame_from_tokens(row[part],vectors)
                   for row in alignment]) for part in lab.PARTS}
    proto=lab._build_prototypes(alignment,frames,np.array([0]))
    pred=lab._prototype_rows(alignment,frames,proto,[1])
    assert pred['denominator'][1]['reconstructed']=='5700'
    assert pred['denominator'][1]['train_prototype_exact_coverage']
    assert not pred['numerator'][1]['train_prototype_exact_coverage']
    # No evaluation label or evaluation-token ID is used for a lookup: even
    # changing these cannot alter the embedding-only prediction.
    tampered=lab._token_alignment(tok,cases)
    for t in tampered[1]['denominator']['tokens']:
        t['token_id']+=100000
        t['text']='xxx'
    rechecked=lab._prototype_rows(tampered,frames,proto,[1])
    assert rechecked['denominator'][1]['reconstructed']=='5700'


def test_unseen_embedding_is_not_declared_missing_information():
    rng=np.random.default_rng(13)
    proto={'labels':['570','0'],'training_token_ids':[11,12],
           'vectors':np.stack([rng.normal(size=1024).astype(np.float32),
                               rng.normal(size=1024).astype(np.float32)])}
    seen=lab._prototype_decode(proto['vectors'],proto)
    assert all(x['exact_training_embedding_match'] for x in seen)
    unknown=lab._prototype_decode(np.ones((1,1024),dtype=np.float32)*18,proto)
    assert not unknown[0]['exact_training_embedding_match']


def test_wrong_digit_with_exact_input_prototype_is_decoder_error():
    case=_case(Fraction(3581,5700))
    alignment=lab._token_alignment(MultiDigitTokenizer(),[case])[0]
    q=Fraction(3581,5700)
    logits={p:np.zeros((1,60),dtype=np.float64) for p in lab.PARTS}
    for part in lab.PARTS:
        original=str(getattr(q,part)).zfill(6)
        for j,digit in enumerate(original):logits[part][0,j*10+int(digit)]=5
    # Intentionally give one wrong head output; input prototype still decodes.
    preds={'numerator':[3581],'denominator':[4800]}
    pdecode={p:{0:{'train_prototype_exact_coverage':True,
       'reconstructed':str(getattr(q,p)),
       'tokens':[{'exact_training_embedding_match':True} for _ in alignment[p]['tokens']]}}
       for p in lab.PARTS}
    trace=lab._case_trace(0,case,alignment,q,preds,logits,pdecode)
    den=trace['components']['denominator']
    assert den['diagnosis']=='DECODER_ERROR_INPUT_EMBEDDINGS_RECONSTRUCT_EXACTLY'
    assert [digit['activation_slot'] for digit in den['digit_positions']]==[4,4,4,5]


def test_wrong_digit_with_unseen_embedding_is_not_declared_upstream_absence():
    case=_case(Fraction(3581,5700))
    alignment=lab._token_alignment(MultiDigitTokenizer(),[case])[0]
    q=Fraction(3581,5700)
    logits={p:np.zeros((1,60),dtype=np.float64) for p in lab.PARTS}
    preds={'numerator':[3581],'denominator':[4800]}
    pdecode={p:{0:{'train_prototype_exact_coverage':False,
       'reconstructed':None,
       'tokens':[{'exact_training_embedding_match':False} for _ in alignment[p]['tokens']]}}
       for p in lab.PARTS}
    trace=lab._case_trace(0,case,alignment,q,preds,logits,pdecode)
    assert 'NOT_PROVEN_ABSENT' in trace['components']['denominator']['diagnosis']


def test_digit_logit_trace_has_place_from_right_and_margin():
    logits=np.zeros((60,),dtype=np.float64)
    for i,c in enumerate('005700'):
        logits[10*i+int(c)]=3
    logits[10*2+4]=1
    records=lab._digit_logits(logits,5700,5700)
    assert [r['readout_place_from_left'] for r in records]==[2,3,4,5]
    assert all(r['matches'] for r in records)
    assert records[0]['runner_up_digit']=='4'
    assert records[0]['score_margin']==2


def test_checkpoint_logit_replay_matches_frozen_fit_and_rejects_mutation(tmp_path):
    rng=np.random.default_rng(12)
    x=rng.normal(size=(20,6144)).astype(np.float32)
    values=list(range(100,120))
    fit=next(compose._candidates(x,values,np.arange(12),
                    kernels=('linear',),regs=(.1,)))
    fit={**fit,'stage':'embedding','view':'slots'}
    checkpoint=tmp_path/'numerator.npz'
    compose._checkpoint(checkpoint,fit,x,'a'*64,'b'*64)
    predicted,logits,meta=lab._replay_scores(checkpoint,x,'a'*64,'b'*64)
    assert predicted==fit['prediction']
    assert logits.shape==(20,60)
    with pytest.raises(ValueError,match='LINEAGE_INVALID'):
        lab._replay_scores(checkpoint,x,'c'*64,'b'*64)
    changed=x.copy(); changed[0,0]+=1
    with pytest.raises(ValueError,match='LINEAGE_INVALID'):
        lab._replay_scores(checkpoint,changed,'a'*64,'b'*64)


def test_metric_aggregation_keeps_denominator_width_and_token_patterns():
    cases=[_case(Fraction(3581,5700)),_case(Fraction(3619,5700))]
    tok=MultiDigitTokenizer(); align=lab._token_alignment(tok,cases)
    rows=[]
    for i,q in enumerate((Fraction(3581,5700),Fraction(3619,5700))):
        item={'index':i,'true_q':str(q),'predicted_q':str(q) if i==0 else '3619/4800',
              'components':{}}
        for part in lab.PARTS:
            item['components'][part]={'actual':str(getattr(q,part)),
             'exact':(i==0 or part=='numerator'),
             'diagnosis':'READOUT_CORRECT' if i==0 or part=='numerator' else 'DECODER_ERROR',
             'token_pattern':align[i][part]['token_pattern']}
        rows.append(item)
    summary=lab._summary(rows,{'challenge_zero_anchor':np.array([0,1])})
    assert summary['panels']['challenge_zero_anchor']['exact_fraction']==1
    den=[x for x in summary['by_digit_width'] if x['component']=='denominator'][0]
    assert den['digits']==4 and den['correct']==1
    assert any(x['token_lengths']=='3,1' for x in summary['by_token_pattern'])


def test_feature_archive_rejects_non_float32_or_bad_keys(tmp_path):
    f=tmp_path/'test.npz'
    np.savez_compressed(f,numerator=np.zeros((2,6144)),
                        denominator=np.zeros((2,6144)))
    with pytest.raises(ValueError,match='SHAPE'):
        lab._load_features({'archive':f},2)


def test_local_parser_has_no_model_training_and_uses_composition_argument():
    assert 'composition' in lab.main.__code__.co_names or 'composition' in lab.run.__code__.co_names
    assert lab.SCHEMA.endswith('.v1')


def test_mock_end_to_end_writes_full_trace_and_preserves_inputs(tmp_path,monkeypatch):
    """Integration fixture: cached activations only; NO assertion of CUDA accuracy."""
    import sys
    from tools import nanojev_gamer_v1_handoff as gamer
    cases=[_case(Fraction(3581,5700)),_case(Fraction(3619,5700)),
           _case(Fraction(5,19)),_case(Fraction(7,23)),
           _case(Fraction(15,23)),_case(Fraction(6,29)),
           _case(Fraction(7,31)),_case(Fraction(8,37))]
    for i,case in enumerate(cases):case.id='synthetic.'+str(i)
    q=[Fraction(x) for x in (Fraction(3581,5700),Fraction(3619,5700),
       Fraction(5,19),Fraction(7,23),Fraction(15,23),Fraction(6,29),
           Fraction(7,31),Fraction(8,37))]
    panels={'train':np.array([0,2]),'legacy_dev':np.array([3]),
            'fresh_dev':np.array([1]),'heldout_independent':np.array([4]),
            'heldout_zero_anchor':np.array([5]),
            'challenge_independent':np.array([6]),
            'challenge_zero_anchor':np.array([7])}
    # Produce the synthetic feature archive and train-token labels with a
    # consistent tokenizer (no transformer model download in CI).
    tokenizer=MultiDigitTokenizer()
    aligned=lab._token_alignment(tokenizer,cases)
    rng=np.random.default_rng(16)
    token_words={t['text'] for row in aligned for comp in row.values()
                 for t in comp['tokens']}
    token_emb={word:rng.normal(size=1024).astype(np.float32)
               for word in sorted(token_words)}
    features={part:np.stack([_frame_from_tokens(row[part],token_emb)
                   for row in aligned]) for part in lab.PARTS}
    base=tmp_path/'previous';base.mkdir()
    output=tmp_path/'digit-audit'
    upstream=tmp_path/'upstream';upstream.mkdir()
    mainreport=base/'final_report.json';mainreport.write_text('{}')
    archives=base/'archive.npz';np.savez_compressed(archives,**features)
    ck={}
    for part in lab.PARTS:
        p=base/f'{part}.npz';p.write_bytes(b'frozen-checkpoint:'+part.encode())
        ck[part]={'path':str(p),'sha256':lab.token._sha(p)}
    comp_report={'combined_deck_sha256':'abcd',
      'selected_feature_archive':{'sha256':lab.token._sha(archives)},
      'source_belief_report_sha256':'b'*64,
      'frozen_lora_sha256':'a'*64,
      'checkpoints':ck,
      'component_accuracy':{part:{key:{'exact':1.} for key in panels
                   if key not in ('train','legacy_dev')}
                   for part in lab.PARTS},
      'fraction_accuracy':{key:{'exact':1.} for key in panels
                   if key not in ('train','legacy_dev')}}
    # One deliberately bad denominator in synthetic fresh development.
    comp_report['component_accuracy']['denominator']['fresh_dev']['exact']=0.
    comp_report['fraction_accuracy']['fresh_dev']['exact']=0.
    source={'root':base,'report_path':mainreport,
            'report_sha256':lab.token._sha(mainreport),
            'report':comp_report,'archive':archives,
            'upstream':{'root':upstream,
                'precision':{'root':upstream,'previous':{'source':{
                    'origin':{'meta':{'lineage':{'model_id':'mock'}}}}}}}}
    monkeypatch.setattr(lab,'_load_source',lambda *_:source)
    monkeypatch.setattr(lab,'_deck',lambda *_:(cases,[{}]*len(cases),panels,q))
    # Full zero-triple reconstruction has its own test; this eight-case
    # synthetic integration fixture has no full 24-case anchor panels.
    monkeypatch.setattr(lab,'_zero_line_replay',lambda *_:{'heldout':{},'challenge':{}})
    monkeypatch.setattr(lab.compose,'_verify_frozen',lambda *_:None)
    preds={'numerator':[x.numerator for x in q],
           'denominator':[x.denominator for x in q]}
    preds['denominator'][1]=4800
    def mocked_replay(path,frame,*args):
        part=Path(path).stem
        result=preds[part]
        logits=np.zeros((len(cases),60),dtype=np.float64)
        for i,n in enumerate(result):
            for j,c in enumerate(str(n).zfill(6)):
                logits[i,10*j+int(c)]=1
        return result,logits,{'kernel':'linear'}
    monkeypatch.setattr(lab,'_replay_scores',mocked_replay)
    fake=SimpleNamespace(from_pretrained=lambda *_args,**_kwargs:tokenizer)
    monkeypatch.setitem(sys.modules,'transformers',
                       SimpleNamespace(AutoTokenizer=fake))
    result=lab.run(SimpleNamespace(composition=base,output=output))
    assert result['status']=='COMPLETE_READ_ONLY_POSITIONAL_DIAGNOSTIC'
    assert result['results']['panels']['fresh_dev']['exact_fraction']==0
    assert result['failed_component_cases']==1
    rows=[json.loads(line) for line in (output/'digit_trace.jsonl').read_text().splitlines()]
    assert len(rows)==len(cases)
    item=next(row for row in rows if row['index']==1)
    assert item['components']['denominator']['actual']=='5700'
    assert [p['true_digit'] for p in item['components']['denominator']['digit_positions']]==list('5700')
    assert (output/'failure_trace.json').exists()
    assert mainreport.read_text()=='{}'
    assert all(Path(ck[part]['path']).read_bytes()==b'frozen-checkpoint:'+part.encode()
               for part in lab.PARTS)
    assert result['trace_files']['all']['sha256']==lab.token._sha(output/'digit_trace.jsonl')


def test_zero_line_replay_counts_complete_triples_not_only_centers():
    from tools import nanojev_gamer_v1_exact_belief_lab as exact
    challenge, tags = exact._challenge()
    ids=np.asarray([i for i,t in enumerate(tags) if t['panel']=='zero_anchor'],dtype=int)
    actual=exact._exact_q(challenge)
    rows=[{'index':i,'true_q':str(q),'predicted_q':str(q)} for i,q in enumerate(actual)]
    baseline={'zero_anchor_triples':{'challenge':{
        'exact_center_q':8,'exact_q_all_three':8,
        'all_payoffs_exact_all_three':8,'strict_exact_full_triples':8,'accepted':True},
        'heldout':{'exact_center_q':8,'exact_q_all_three':8,
        'all_payoffs_exact_all_three':8,'strict_exact_full_triples':8,'accepted':True}}}
    # Reuse the challenge set as two independent 8-triple panels for this unit
    # test; structural group-count rules remain real.
    panels={'challenge_zero_anchor':ids,'heldout_zero_anchor':ids}
    expected=lab._zero_line_replay(rows,tags,panels,baseline)
    assert expected['challenge']['exact_q_all_three']==8
    # The center can remain exact while both neighbors are wrong: FAIL.
    wrong=next(i for i in ids if tags[int(i)]['edit']=='negative')
    rows[int(wrong)]['predicted_q']='1/17'
    baseline['zero_anchor_triples']['challenge']['exact_q_all_three']=7
    baseline['zero_anchor_triples']['heldout']['exact_q_all_three']=7
    baseline['zero_anchor_triples']['challenge']['strict_exact_full_triples']=7
    baseline['zero_anchor_triples']['heldout']['strict_exact_full_triples']=7
    baseline['zero_anchor_triples']['challenge']['accepted']=False
    baseline['zero_anchor_triples']['heldout']['accepted']=False
    changed=lab._zero_line_replay(rows,tags,panels,baseline)
    assert changed['challenge']['exact_center_q']==8
    assert changed['challenge']['exact_q_all_three']==7

