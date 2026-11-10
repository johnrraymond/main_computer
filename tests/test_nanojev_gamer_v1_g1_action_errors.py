"""G1-0015 action-error freeze and attribution contracts: CPU/mock only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g1_action_errors as diag
from tools import nanojev_gamer_v1_g1_calibration_validation as val
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import SPAN_CLASS_COUNT
from tests.test_nanojev_gamer_v1_graph_system import CharTokenizer


def test_selection_and_diagnostics_are_frozen():
    assert (diag.WINNER_KEY, diag.WINNER_HEAD, diag.WINNER_SHIFT) == (
        'null_focused_4', 'null_focused', 4.0)
    assert diag.PINNED_WINNER == {
        'cases':320, 'expected':8982, 'correct':8716, 'exact_prompts':134,
        'missing_objects':266, 'false_positive_objects':215,
    }
    assert 0 < diag.PINNED_WINNER_F1 < 1


def test_summary_distinguishes_null_decoder_and_boundary_mechanisms():
    misses = [
        {'family':'encounter','reason':'null_wins', 'span_length_bucket':'5-8',
         'overlapping_predicted_action':0,'overlapping_predicted_other_kind':0,
         'overlapping_correct_predicted_action':0, 'best_nonnull_is_correct_kind':True},
        {'family':'allocation','reason':'linear_decoder_excluded', 'span_length_bucket':'1-4',
         'overlapping_predicted_action':1,'overlapping_predicted_other_kind':0,
         'overlapping_correct_predicted_action':0,
         'conflicts_only_false_positives':True,'conflicts_any_correct_gold':False},
    ]
    false = [
        {'family':'allocation','span_length_bucket':'1-4','overlaps_gold_action':True,
         'overlaps_gold_other_kind':False},
    ]
    summary = diag._summary(misses,false,5,4)
    assert summary['missing_actions'] == 2
    assert summary['correct_actions'] == 3
    assert summary['null_wins_with_action_best_nonnull'] == 1
    assert summary['decoder_exclusions_conflicting_only_with_false_positives'] == 1
    assert summary['misses_with_overlapping_incorrect_action'] == 1
    assert summary['false_positives_overlapping_gold_action'] == 1
    assert summary['miss_reasons_by_family']['allocation']['missing_actions'] == 1
    with pytest.raises(ValueError,match='UNKNOWN_REASON'):
        diag._summary([{**misses[0],'reason':'unexplained'}], [],1,0)


def _fixture_for_report(tmp_path, monkeypatch):
    ds = tmp_path/'orig.jsonl'
    corpus.generate(ds, train=8, dev=8, challenge=4, test=4, seed=31)
    from tools.nanojev_gamer_v1_g1_fresh_dev import fresh_rows
    previous = fresh_rows(ds)
    rows = val.validation_rows(ds,previous)
    data = val._jsonl_bytes(rows)
    expected = [({},{(s['start'],s['end'],s['kind']) for s in row['object_spans']})
                for row in rows]
    # Simulated null predictions for all four configurations.
    records = [(set(),gold) for _,gold in expected]
    metrics = val._summarize(records,rows)
    cases = []
    for idx,(row,(_,gold)) in enumerate(zip(rows,expected),1):
        cases.append({'index':idx,'family':row['family'],
            'prompt_sha256':hashlib.sha256(row['prompt'].encode()).hexdigest(),
            'gold_count':len(gold),
            'configurations':{name: {
                'correct':0,'missing':len(gold),'false_positives':0,'exact':False}
                for name,_,_ in val.CONFIGURATIONS}})
    info = {'validation_rows':rows, 'dataset':ds, 'fresh_rows':previous, 'dataset_sha':'ds','transfer_sha':'tr',
            'fresh_report_sha':'fr','fresh_dataset_sha':'fd',
            'fresh_dataset_file_sha':'fb', 'null_report_sha':'nr',
            'adapter_sha':'ad','entries':{name:{'manifest':{'head_sha256':name}}
                for name in ('structured','null_focused')},
            'checkpoint_files':{name:{'head_file_sha256':name+'-head',
                'manifest_sha256':name+'-manifest'} for name in ('structured','null_focused')}}
    report = {'schema':val.SCHEMA,
        'status':'FROZEN_WITHIN_GENERATOR_DEVELOPMENT_REPLICATION_ONLY',
        'seed':val.SEED,'cases':320,'split':'new_dev',
        'families':{'encounter':160,'allocation':160},
        'configurations_predeclared':[{'key':key,'checkpoint':arm,'null_logit_shift':shift}
            for key,arm,shift in val.CONFIGURATIONS],
        'exploratory_ranking_not_certification':[
            'null_focused_4','structured_4','structured_2','structured_0'],
        'ranking_rule_predeclared':'exact_prompts_desc,f1_desc,false_positives_asc,key_asc',
        'validation_dataset_sha256':hashlib.sha256(data).hexdigest(),
        'original_dataset_sha256':'ds', 'original_transfer_report_sha256':'tr',
        'prior_fresh_report_sha256':'fr','prior_fresh_dataset_sha256':'fd',
        'prior_fresh_dataset_file_sha256':'fb','null_boundary_report_sha256':'nr',
        'source_adapter_sha256':'ad',
        'head_sha256':{'structured':'structured','null_focused':'null_focused'},
        'checkpoint_file_sha256':info['checkpoint_files'],
        'decoder':'length_linear','length_power':1.0,'per_object_penalty':0.0,
        'same_prompts_for_all_configurations':True,
        'one_feature_capture_per_prompt':True,
        'prior_train_dev_and_fresh_prompt_overlap':0,
        'no_training':True,'heads_frozen':True,
        'decoder_adjustments_inference_only':True,
        'original_decoder_unchanged':True,'challenge_or_test_split_used':False,
        'certification_authorized':False,'production_promotion':False,
        'g2_unlocked':False,
        'metrics':{name:metrics for name,_,_ in val.CONFIGURATIONS}}
    monkeypatch.setattr(diag,'PINNED_WINNER',{
        'cases':metrics['cases'],'expected':metrics['expected'],
        'correct':0,'exact_prompts':0,'missing_objects':metrics['missing_objects'],
        'false_positive_objects':0})
    monkeypatch.setattr(diag,'PINNED_WINNER_F1',metrics['f1'])
    return info,report,data,cases


def test_report_rejects_replaced_prompts_scores_and_winner(tmp_path,monkeypatch):
    info,report,data,cases=_fixture_for_report(tmp_path,monkeypatch)
    diag._require_report(report,info,data,cases,data)
    import copy
    bad=copy.deepcopy(report)
    bad['exploratory_ranking_not_certification'][0]='structured_4'
    with pytest.raises(ValueError,match='INVALID_VALIDATION_LINEAGE'):
        diag._require_report(bad,info,data,cases,data)
    bad=copy.deepcopy(report)
    bad['g2_unlocked']=True
    with pytest.raises(ValueError,match='INVALID_VALIDATION_LINEAGE'):
        diag._require_report(bad,info,data,cases,data)
    damaged=copy.deepcopy(cases)
    damaged[0]['configurations']['null_focused_4']['missing']+=1
    with pytest.raises(ValueError,match='INVALID_CASE_ACCOUNTING'):
        diag._require_report(report,info,data,damaged,data)
    with pytest.raises(ValueError,match='VALIDATION_DATASET_CHANGED'):
        diag._require_report(report,info,data,cases,data.replace(b'\n',b'\r\n'))
    bad=copy.deepcopy(report)
    bad['metrics']['null_focused_4']['correct']+=1
    with pytest.raises(ValueError,match='WINNER_NOT_PINNED'):
        diag._require_report(bad,info,data,cases,data)


def test_gold_is_diagnostic_only_and_action_reasons_use_adjusted_null(monkeypatch):
    # Two one-character action gold spans. First is below raw null but above
    # shifted null; second is still below null at +4 and must be missed.
    row={'prompt':'ab','family':'encounter','object_spans':[
        {'start':0,'end':1,'kind':'action'},
        {'start':1,'end':2,'kind':'action'}]}
    prepared={'eligible':torch.tensor([1,1]),'offsets':[(0,1),(1,2)],
              'prompt_start':0,'prompt':'ab'}
    monkeypatch.setattr(cur,'aligned_labels',lambda p,r:{'spans':[
        {'token_first':0,'token_last':0,'kind':'action'},
        {'token_first':1,'token_last':1,'kind':'action'}]})
    logits=torch.full((2,1,SPAN_CLASS_COUNT),-30.)
    logits[:,:,0]=0.
    action=SPAN_KINDS.index('action')+1
    logits[0,0,action]=-3.0
    logits[1,0,action]=-7.0
    valid=torch.ones((2,1),dtype=torch.bool)
    prediction={'span_logits':logits,'span_valid':valid}
    original=logits.clone()
    spans=diag._decode_shift(logits,valid,diag.WINNER_SHIFT)
    assert len(spans)==1
    guess=_set(spans,prepared)
    misses,fps=diag._analyze_case(prediction,prepared,row,spans,guess,index=1)
    assert len(misses)==1 and not fps
    assert misses[0]['reason']=='null_wins'
    assert misses[0]['raw_action_vs_null_margin']==-7.0
    assert misses[0]['calibrated_action_vs_null_margin']==-3.0
    assert torch.equal(original,logits)


def _set(spans,prepared):
    return diag._char_set(spans,prepared)


class NoActionHead:
    def __init__(self): self.calls=0
    def eval(self):return self
    def __call__(self, hidden,*,eligible,active_stage):
        assert not torch.is_grad_enabled() and active_stage=='G1'
        self.calls+=1
        count=hidden.shape[0]
        logits=torch.full((count,1,SPAN_CLASS_COUNT),-30.)
        logits[:,:,0]=20.
        return {'span_logits':logits,'span_valid':eligible.bool().reshape(-1,1)}


def test_full_320_case_readonly_replay_and_freeze_manifest(tmp_path,monkeypatch):
    info, _, data, cases=_fixture_for_report(tmp_path,monkeypatch)
    ds=tmp_path/'dataset.jsonl'; ds.write_bytes(data)
    adapter=tmp_path/'adapter';adapter.mkdir()
    (adapter/'adapter_config.json').write_text('{}')
    (adapter/'adapter_model.safetensors').write_bytes(b'fake')
    head_dir=tmp_path/'head';head_dir.mkdir()
    (head_dir/'manifest.json').write_text('{}')
    (head_dir/cur.HEAD_FILE).write_bytes(b'head')
    info['entries']['null_focused']={'checkpoint':head_dir,'manifest':{
        'stage':'G1','head_sha256':'null_focused',
        'model_id':'mock-Qwen','source_adapter':str(adapter)}}
    info['adapter_sha']=backbone.adapter_fingerprint(adapter)
    info['validation_report']={'metrics': {'null_focused_4':val._summarize(
        [(set(),{(s['start'],s['end'],s['kind']) for s in row['object_spans']})
         for row in info['validation_rows']],info['validation_rows'])},
         'validation_dataset_sha256':hashlib.sha256(data).hexdigest()}
    info['validation_cases']=cases
    info['validation_file_sha256']={'validation_report.json':'fake-report'}
    head=NoActionHead()
    monkeypatch.setattr(diag,'_load_inputs',lambda *a:info)
    monkeypatch.setattr(diag,'_verify_unchanged',lambda *a:None)
    monkeypatch.setattr(cur,'_load_checkpoint',lambda cp,*,device:
        (head,info['entries']['null_focused']['manifest']))
    monkeypatch.setattr(backbone,'model_components',lambda *a:(object(),CharTokenizer()))
    def capture(model,tok,row,device,max_length):
        prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
        return {'prepared':prepared,'hidden':torch.zeros((len(prepared['eligible']),8))}
    monkeypatch.setattr(cur,'capture',capture)
    out=tmp_path/'g1_0015'
    report=diag.run(root=tmp_path,transfer_report='unused',fresh_run='unused',
                   null_run='unused',validation_run='unused',output=out,device='cpu')
    assert head.calls==320
    assert report['winner']['checkpoint']=='null_focused'
    assert report['winner']['null_logit_shift']==4.0
    assert report['winner']['status']=='FROZEN_DEVELOPMENT_REFERENCE_NOT_PRODUCTION'
    assert report['action_errors']['missing_actions']>0
    assert report['action_errors']['miss_reasons']['span_exceeds_window']>0
    assert report['g2_unlocked'] is False and report['no_training'] is True
    assert report['challenge_or_test_split_used'] is False
    assert (out/'frozen_development_candidate.json').is_file()
    assert len((out/'action_error_cases.jsonl').read_text().splitlines())<=320
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        diag.run(root=tmp_path,transfer_report='unused',fresh_run='unused',
                 null_run='unused',validation_run='unused',output=out,device='cpu')


def test_cli_exposes_only_diagnostic_inputs(capsys):
    with pytest.raises(SystemExit) as exc:
        cur.main(['frob-g1-action-errors','--help'])
    assert exc.value.code==0
    rendered=capsys.readouterr().out
    for name in ('--root','--transfer-report','--fresh-run','--null-run',
                 '--validation-run','--output','--device'):
        assert name in rendered
    for forbidden in ('--train','--certify','--promote','--challenge','--g2','--seed','--shift'):
        assert forbidden not in rendered


def test_action_boundary_competition_is_separate_from_wrong_type(monkeypatch):
    row={'prompt':'abc','family':'allocation',
         'object_spans':[{'start':0,'end':2,'kind':'action'}]}
    prepared={'eligible':torch.tensor([1,1,1]),
              'offsets':[(0,1),(1,2),(2,3)],'prompt_start':0,'prompt':'abc'}
    monkeypatch.setattr(cur,'aligned_labels',lambda p,r:{'spans':[
        {'token_first':0,'token_last':1,'kind':'action'}]})
    logits=torch.full((3,2,SPAN_CLASS_COUNT),-30.)
    logits[:,:,0]=0.
    action=SPAN_KINDS.index('action')+1
    logits[0,0,action]=7.0  # Shorter, incorrect but higher weighted score
    logits[0,1,action]=1.0  # Whole correct interval has a positive margin
    valid=torch.zeros((3,2),dtype=torch.bool)
    valid[0,0]=True;valid[0,1]=True
    prediction={'span_logits':logits,'span_valid':valid}
    spans=diag._decode_shift(logits,valid,4.0)
    guess=_set(spans,prepared)
    assert guess=={(0,1,'action')}
    missed,fps=diag._analyze_case(prediction,prepared,row,spans,guess,index=1)
    assert len(missed)==1 and len(fps)==1
    assert missed[0]['reason']=='linear_decoder_excluded'
    assert missed[0]['overlapping_predicted_action']==1
    assert missed[0]['conflicts_only_false_positives'] is True
    assert fps[0]['overlaps_gold_action'] is True
    assert fps[0]['surface']=='a'


def test_loading_validation_fails_closed_on_modified_bytes(tmp_path,monkeypatch):
    info, report, data, cases = _fixture_for_report(tmp_path,monkeypatch)
    prior=tmp_path/'prior';prior.mkdir()
    for name in ('structured','null_focused'):
        cp=tmp_path/name;cp.mkdir()
        (cp/'manifest.json').write_text(json.dumps({'stage':'G1'}))
        (cp/cur.HEAD_FILE).write_bytes(name.encode())
        info['entries'][name]['checkpoint']=cp
    info['checkpoint_files']={name:{
        'manifest_sha256':backbone.sha256(info['entries'][name]['checkpoint']/'manifest.json'),
        'head_file_sha256':backbone.sha256(info['entries'][name]['checkpoint']/cur.HEAD_FILE)}
        for name in ('structured','null_focused')}
    report['checkpoint_file_sha256']=info['checkpoint_files']
    (prior/'validation_report.json').write_bytes(json.dumps(report).encode())
    (prior/'validation_dev.jsonl').write_bytes(data)
    (prior/'validation_per_case.jsonl').write_bytes(val._jsonl_bytes(cases))
    monkeypatch.setattr(diag,'_load_prior_inputs',lambda *args:dict(info))
    loaded=diag._load_inputs(tmp_path,'tr','fr','nr',prior,tmp_path/'out')
    assert loaded['validation_report']==report
    assert loaded['validation_cases']==cases
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        out=tmp_path/'out';out.mkdir()
        diag._load_inputs(tmp_path,'tr','fr','nr',prior,out)
    (prior/'validation_dev.jsonl').write_bytes(data.replace(b'\n',b'\r\n'))
    with pytest.raises(ValueError,match='VALIDATION_DATASET_CHANGED'):
        diag._load_inputs(tmp_path,'tr','fr','nr',prior,tmp_path/'not_existing')


def test_modified_validation_byte_detected_at_end(tmp_path,monkeypatch):
    prior=tmp_path/'prior';prior.mkdir()
    files={name:prior/name for name in (
        'validation_report.json','validation_dev.jsonl','validation_per_case.jsonl')}
    for file in files.values():file.write_bytes(b'original\n')
    info={'entries':{},'validation_paths':files,'validation_file_sha256':{
        name:backbone.sha256(path) for name,path in files.items()}}
    monkeypatch.setattr(diag,'_verify_prior_inputs',lambda *args:None)
    diag._verify_unchanged(info,'transfer','fresh','adapter')
    (prior/'validation_dev.jsonl').write_bytes(b'altered\n')
    with pytest.raises(ValueError,match='VALIDATION_INPUT_CHANGED_DURING_RUN'):
        diag._verify_unchanged(info,'transfer','fresh','adapter')
