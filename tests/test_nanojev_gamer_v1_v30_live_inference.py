"""V31 explicitly opt-in live inference; production Wide remains unchanged."""
from __future__ import annotations
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
import json

import numpy as np
import pytest
import torch
from tools import nanojev_gamer_v1_handoff as gamer
from tools import nanojev_gamer_v1_v30_live_inference as live
from tools import nanojev_gamer_v1_zero_line_decoder_v30 as v30
from tools import nanojev_gamer_v1_selector_retention_training_v28 as v28
from tools import nanojev_gamer_v1_candidate_span_causality_v27 as v27
from tools import nanojev_gamer_v1_role_reversal_retention_v29 as v29


class Tokenizer:
    def decode(self, ids, **kwargs):
        return ''.join(chr(int(x)) for x in ids)
    def encode(self, prompt, **kwargs):
        return [1]*len(prompt)


def test_live_predictions_are_not_scored_from_true_token_ids():
    prompt='A spacecraft is at signed coordinate 8, while a navigation beacon is at signed coordinate 8 on the same axis.'
    lexical=v27._numeric_candidates(prompt)
    assert len(lexical)==2
    candidates=[{'span':c['span'],'indices':[0], 'token_ids':[999999],
                 'marker':c['marker']} for c in lexical]
    arrays={'layer_18':np.array([[1.,0.],[0.,1.]],dtype=np.float32)}
    base=np.zeros((2,2),dtype=np.float64)
    selector=np.zeros((2,2+len(v28.MARKERS)+13),dtype=np.float32)
    # Tie ordering chooses later candidate as first and earlier as second.
    decoder=([56,57],np.array([[1.,0.],[0.,1.]],dtype=np.float64))
    result=live.infer_from_capture(prompt,candidates,arrays,base,selector,decoder,Tokenizer())
    assert result['difference']=='1'
    assert result['first_operand']=='9' and result['second_operand']=='8'
    assert result['oracle_used_for_prediction'] is False
    assert result['calculation_authority']=='exact_fraction_postprocess'
    assert result['predicted_token_ids']==[[57],[56]]
    assert all(x!=999999 for pair in result['predicted_token_ids'] for x in pair)


def test_live_zero_line_uses_decoder_repair_not_input_lexical_number():
    prompt='A spacecraft is at signed coordinate 8, while a navigation beacon is at signed coordinate 8 on the same axis.'
    candidates=[{'span':c['span'],'indices':[0]} for c in v27._numeric_candidates(prompt)]
    base=np.zeros((2,2))
    selector=np.zeros((2,2+len(v28.MARKERS)+13),dtype=np.float32)
    arrays={'layer_18':np.array([[1.,0.],[0.,1.]],np.float32)}
    # Repair makes both contextual representations read token '8'.
    decoder=([56,57],np.array([[.7,.7],[.7,-.7]],dtype=np.float64))
    result=live.infer_from_capture(prompt,candidates,arrays,base,selector,decoder,Tokenizer())
    assert result['difference']=='0'
    assert result['zero_exact']


def test_unsupported_prompt_rejected_and_no_oracle_lookup():
    with pytest.raises(ValueError,match='V31_UNSUPPORTED_OR_AMBIGUOUS_PROMPT_FAMILY'):
        live._validate_prompt('Solve 1 + 3.')
    with pytest.raises(ValueError,match='V31_UNSUPPORTED_OR_AMBIGUOUS_PROMPT_FAMILY'):
        live._validate_prompt('CC=8, DC=8. Please add them.')


def test_expected_answer_is_never_passed_to_prediction(monkeypatch,tmp_path):
    deck=tmp_path/'deck.jsonl'
    prompt='A spacecraft is at signed coordinate 8, while a navigation beacon is at signed coordinate 8 on the same axis.'
    deck.write_text(json.dumps({'id':'a','prompt':prompt,'expected':'-999'})+'\n')
    args=SimpleNamespace(v30_prompt=None,v30_prompt_file=None,v30_cases_jsonl=str(deck))
    rows=live._inputs(args)
    assert rows==[{'id':'a','prompt':prompt,'expected':'-999'}]
    assert live._validate_prompt(rows[0]['prompt'])


def test_cli_v30_live_is_explicit_not_training_stage():
    flags={x for a in gamer.parser()._actions for x in a.option_strings}
    assert {'--v27-run','--v29-run','--v30-run','--v30-plan-only',
            '--v30-prompt','--v30-expected','--v30-cases-jsonl','--v30-report'}<=flags
    args=gamer.parser().parse_args(['v30-live','--output','existing',
        '--v27-run','v27','--v29-run','v29','--v30-run','v30',
        '--v30-prompt','A spacecraft is at signed coordinate 8, while a navigation beacon is at signed coordinate 8.','--v30-report','fresh.json'])
    gamer.validate_flags(args)
    bad=gamer.parser().parse_args(['to-numerical','--output','existing',
                                   '--v30-run','v30'])
    with pytest.raises(ValueError,match='GAMER_V30_FLAGS_ONLY_V30_LIVE_STAGE'):
        gamer.validate_flags(bad)


def test_plan_lineage_and_real_dispatch_mocked_no_model(monkeypatch,tmp_path):
    import tools.nanojev_two_game_spine_smoke as spine
    from tools import nanojev_gamer_v1_precision_continuation_v19 as sha_tool
    v27dir=tmp_path/'v27';v29dir=tmp_path/'v29';v30dir=tmp_path/'v30'
    for p in (v27dir,v29dir,v30dir):p.mkdir()
    adapter=tmp_path/'adapter';adapter.mkdir()
    head=tmp_path/'head.pt';head.write_bytes(b'frozen')
    lineage={'adapter_dir':str(adapter),'adapter_sha256':'a'*64,
       'checkpoint_path':str(head),'checkpoint_sha256':sha_tool._sha(head),
       'model_id':'toy','configuration':{'max_length':1024}}
    w29={'schema':v29.SCHEMA,'production':False,
         'base_weight':torch.zeros((2,2)),
         'hybrid_v29_candidate':torch.zeros((2,2+len(v28.MARKERS)+13))}
    p29=v29dir/'disposable_v29_hybrid_role_candidate.pt'
    torch.save(w29,p29)
    r27=v27dir/'report.json';r29=v29dir/'report.json'
    r27.write_text(json.dumps({'schema':v27.SCHEMA,'status':'READ_ONLY_ALL_CANDIDATES_NO_PROMOTION',
                               'frozen_lineage':lineage}))
    r29.write_text(json.dumps({'schema':v29.SCHEMA,'status':'ROLE_REVERSAL_TRAINING_COMPLETE_NO_PROMOTION',
                               'frozen_lineage':lineage,'promotion_authorized':False,
                               'production_weights_modified':False}))
    m={str(p29):sha_tool._sha(p29)}
    cp=v30dir/'disposable_v30_decoder.pt'
    torch.save({'schema':v30.SCHEMA,'production':False,
        'v29_checkpoint_sha256':sha_tool._sha(p29),
        'centroid_tokens':torch.tensor([56,57]),
        'centroids':torch.eye(2),
        'alpha':.25,'frozen_source_sha256':m},cp)
    (v30dir/'report.json').write_text(json.dumps({
        'schema':v30.SCHEMA,'status':'DECODER_TRAIN_ONLY_EXPERIMENT_COMPLETE_NO_PROMOTION',
        'all_gates_met':True,'acceptance_gates':{x:True for x in (
          'frozen_sources_unchanged','full_stress_zero_line',
          'non_reversal_stress_nonregression','numeric_decoder_available',
          'original_dev_exact_retained','original_dev_zero_exact',
          'previously_correct_train_numeric_answer_retention',
          'previously_correct_train_token_retention','train_selection_only')},
        'promotion_authorized':False,'production_weights_modified':False,
        'no_dev_fitting_or_checkpoint_selection':True,'no_test_used':True,
        'v27_report_sha256':sha_tool._sha(r27),
        'v29_report_sha256':sha_tool._sha(r29),
        'source_sha256':m,'selection_alpha':.25}))
    monkeypatch.setattr(spine,'adapter_identity',lambda p:'a'*64)
    args=SimpleNamespace(v27_run=str(v27dir),v29_run=str(v29dir),
                         v30_run=str(v30dir),v30_plan_only=True)
    plan=live.run(args)
    assert plan['status']=='V31_PLAN_VERIFIED_NO_FORWARD'
    assert not plan['production_modified']
    # Production Wide is not changed by planning or artifact loading.
    assert head.read_bytes()==b'frozen'
    cp.write_bytes(b'tampered')
    with pytest.raises(Exception):
        live.load(args)


def test_explicit_gamer_live_stage_forwards_and_writes_unpromoted_report(monkeypatch,tmp_path):
    """Real CLI dispatch + raw-prompt kernel; model pass MOCKED, not GPU proof."""
    import tools.nanojev_two_game_spine_smoke as spine
    prompt=('A spacecraft is at signed coordinate 8, while a navigation beacon '
            'is at signed coordinate 8 on the same axis.')
    frozen=tmp_path/'frozen.pt';frozen.write_bytes(b'untouched')
    def fake_load(_args):
        return {'lineage':{'model_id':'toy','adapter_sha256':'a'*64,
                           'configuration':{'max_length':1024}},
          'adapter':tmp_path,'base':np.zeros((2,2)),
          'selector':np.zeros((2,2+len(v28.MARKERS)+13),np.float32),
          'decoder':([56,57],np.array([[.7,.7],[.7,-.7]])),
          'source_files':{str(frozen):live._sha(frozen)},'alpha':.25}
    class Model:
        def eval(self):return self
    def fake_capture(_torch,_model,_tok,entries,**kwargs):
        assert entries==[{'prompt':prompt}]
        cs=[{'span':c['span'],'indices':[0],'token_ids':[49]} for c in
            v27._numeric_candidates(prompt)]
        return cs,{'layer_18':np.array([[1.,0.],[0.,1.]],np.float32)}
    monkeypatch.setattr(live,'load',fake_load)
    monkeypatch.setattr(spine,'adapter_identity',lambda _: 'a'*64)
    monkeypatch.setattr(spine,'cache_policy',lambda _: None)
    def base_model_only(*a,**kw):
        assert 'adapter_dir' not in kw or kw['adapter_dir'] is None
        return (torch,Tokenizer(),Model())
    monkeypatch.setattr(gamer,'model_components',base_model_only)
    monkeypatch.setattr(v27,'_capture_batch',fake_capture)
    args=gamer.parser().parse_args(['v30-live','--output',str(tmp_path),
        '--v27-run','v27','--v29-run','v29','--v30-run','v30',
        '--v30-prompt',prompt,'--v30-expected','0',
        '--v30-report',str(tmp_path/'result.json'),
        '--device','cpu'])
    report=live.run(args)
    assert report['status']=='V31_READ_ONLY_LIVE_INFERENCE_COMPLETE'
    assert report['cases'][0]['difference']=='0'
    assert report['activation_source']=='frozen_base_qwen_layer18_no_lora_adapter'
    assert report['zero_exact']==1 and report['zero_cases']==1
    assert report['all_scored_exact']
    assert report['promotion_authorized'] is False
    assert not report['production_modified']
    assert frozen.read_bytes()==b'untouched'
    assert (tmp_path/'result.json').is_file()
    with pytest.raises(ValueError,match='V31_REPORT_ALREADY_EXISTS'):
        live.run(args)
