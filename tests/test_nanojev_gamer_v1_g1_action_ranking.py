"""G1-0016 action-span ranking contracts: frozen, read-only, CPU/mocked."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g1_action_ranking as rank
from tools import nanojev_gamer_v1_g1_action_errors as prior
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools.nanojev_gamer_v1_g1_decoder_frob import _candidate_scores, _char_set, decode_whole_span_scores
from tools.nanojev_gamer_v1_g1_null_boundary import _decode_shift
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import SPAN_CLASS_COUNT


def make_logits(tokens=3,max_width=3):
    logits=torch.full((tokens,max_width,SPAN_CLASS_COUNT),-40.,dtype=torch.float)
    logits[:,:,0]=0.
    valid=torch.zeros(tokens,max_width,dtype=torch.bool)
    for start in range(tokens):
        for width in range(min(max_width,tokens-start)):
            valid[start,width]=True
    return logits,valid


def add_action(logits,first,last,margin):
    logits[first,last-first,rank.ACTION_CLASS]=margin


def decoded(logits,valid,power=1.,penalty=0.):
    by_end=_candidate_scores(logits,valid,confidence=.5)
    return rank.decode_action_ranked(by_end,action_power=power,action_penalty=penalty)


def test_baseline_exactly_matches_frozen_linear_and_shifted_decoder():
    torch.manual_seed(11016)
    for length in (1,2,3,8,17):
        logits=torch.randn(length,4,SPAN_CLASS_COUNT)*4
        valid=torch.zeros(length,4,dtype=torch.bool)
        for i in range(length):
            valid[i,:min(4,length-i)]=True
        adjusted=logits.detach().clone()
        adjusted[...,0]-=4
        by_end=_candidate_scores(adjusted,valid)
        assert rank.decode_action_ranked(by_end,action_power=1.,action_penalty=0.) == (
            decode_whole_span_scores(by_end,length_power=1.,per_object_penalty=0.))
        assert decoded(adjusted,valid) == _decode_shift(logits,valid,4.)
        assert torch.equal(logits,logits.detach())


def test_action_width_150_can_resolve_fragment_competition():
    logits,valid=make_logits()
    for i in range(3):add_action(logits,i,i,4.)
    add_action(logits,0,2,2.5)
    baseline=decoded(logits,valid)
    stronger=decoded(logits,valid,power=1.5)
    assert [(x['token_first'],x['token_last']) for x in baseline]==[(0,0),(1,1),(2,2)]
    assert [(x['token_first'],x['token_last']) for x in stronger]==[(0,2)]


def test_action_count_penalty_can_resolve_fragment_competition():
    logits,valid=make_logits()
    for i in range(3):add_action(logits,i,i,4.)
    add_action(logits,0,2,3.5)
    assert len(decoded(logits,valid))==3
    assert [(x['token_first'],x['token_last']) for x in decoded(logits,valid,penalty=2)]==[(0,2)]


def test_non_action_scores_and_eligibility_remain_identical():
    logits,valid=make_logits(4,3)
    quantity=SPAN_KINDS.index('quantity')+1
    logits[0,1,quantity]=4.
    logits[2,1,quantity]=5.
    b=decoded(logits,valid)
    for _,power,penalty in rank.VARIANTS:
        assert decoded(logits,valid,power=power,penalty=penalty)==b
    valid[0,1]=False
    for _,power,penalty in rank.VARIANTS:
        assert all(s['token_first'] != 0 for s in decoded(logits,valid,power,penalty))


@pytest.mark.parametrize('power,penalty', [(-1,0),(1.51,0),(.49,0),(1,-1),(1,3),
    (float('nan'),0),(1,float('inf')),(True,0),(1,False)])
def test_unsafe_settings_fail_closed(power,penalty):
    with pytest.raises(ValueError,match='UNSAFE_RANKING'):
        rank.decode_action_ranked([[],[]],action_power=power,action_penalty=penalty)


def test_invalid_candidates_and_overflow_fail_closed():
    with pytest.raises(ValueError,match='BAD_CANDIDATES'):
        rank.decode_action_ranked([[],[(2,0,rank.ACTION_CLASS,3)]],action_power=1,action_penalty=0)
    with pytest.raises(ValueError,match='TOO_MANY_PREDICTED_OBJECTS'):
        rank.decode_action_ranked([[]]+[[(i,0,rank.ACTION_CLASS,4)] for i in range(129)],
                                  action_power=1,action_penalty=0)


def test_paired_accounting_measures_gained_and_lost_actions_without_gold_in_decoder():
    row={'prompt':'abc'}
    gold={(0,2,'action')}
    baseline={(0,1,'action'),(2,3,'actor')}
    candidate={(0,2,'action')}
    diff=rank._compare(baseline,candidate,gold,row,{(0,2,'action'):'linear_decoder_excluded'})
    assert diff['recovered_gold']==1 and diff['recovered_excluded_actions']==1
    assert diff['removed_false_positives']==2 and diff['new_false_positives']==0
    assert diff['new_exact']==1 and diff['lost_exact']==0
    assert diff['recovered_actions_by_prior_reason']=={'linear_decoder_excluded':1}
    assert diff['recovered_action_spans'][0]['surface']=='ab'


def _reference_fixture(tmp_path):
    folder=tmp_path/'action';folder.mkdir()
    validation_rows=[{'prompt':f'case {i}','family':'encounter' if i%2 else 'allocation'}
                     for i in range(1,321)]
    metric={'cases':320,'expected':8982,'correct':8716,'exact_prompts':134,
            'false_positive_objects':215,'missing_objects':266,'f1':.9731479930776532}
    info={'validation_rows':validation_rows,
        'validation_report':{'metrics':{prior.WINNER_KEY:metric},'validation_dataset_sha256':'dataset-sha'},
        'validation_file_sha256':{'validation_report.json':'report-sha'},
        'entries':{prior.WINNER_HEAD:{'manifest':{'head_sha256':'head-sha'}}},
        'adapter_sha':'adapter-sha','checkpoint_files':{prior.WINNER_HEAD:{'head_file_sha256':'head-file','manifest_sha256':'manifest-file'}}}
    selection={'key':prior.WINNER_KEY,'checkpoint':prior.WINNER_HEAD,
        'null_logit_shift':4.,'decoder':'length_linear','head_sha256':'head-sha',
        'validation_dataset_sha256':'dataset-sha',
        'validation_report_file_sha256':'report-sha','source_adapter_sha256':'adapter-sha',
        'checkpoint_file_sha256':info['checkpoint_files'][prior.WINNER_HEAD],
        'status':'FROZEN_DEVELOPMENT_REFERENCE_NOT_PRODUCTION',
        'training_authorized':False,'production_promotion':False,
        'certification_authorized':False,'g2_unlocked':False}
    cases=[]
    miss_reasons=(['linear_decoder_excluded']*100+['null_wins']*68+
                  ['wrong_type']*33+['below_threshold']*15)
    for i in range(158):
        missing=[{'kind':'action','span':[0,1], 'reason':miss_reasons[i]}]
        if i<58:missing.append({'kind':'action','span':[1,2],'reason':miss_reasons[158+i]})
        fps=[{'kind':'action','span':[2,3]}] if i<121 else []
        row=validation_rows[i]
        cases.append({'index':i+1,'family':row['family'],'prompt':row['prompt'],
            'prompt_sha256':hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest(),
            'missing_actions':missing,'false_positive_actions':fps})
    report={'schema':prior.SCHEMA,'status':'DIAGNOSTIC_ONLY_NOT_CERTIFICATION',
        'cases':320,'families':{'encounter':160,'allocation':160},
        'winner':selection,'baseline_metrics':metric,
        'action_errors':dict(rank.REFERENCE_COUNTS),
        'diagnostic_example_count':337,'errored_prompts_with_action_errors':158,
        'no_training':True,'heads_frozen':True,'decoder_frozen':True,
        'challenge_or_test_split_used':False,'production_promotion':False,
        'certification_authorized':False,'g2_unlocked':False}
    (folder/'frozen_development_candidate.json').write_text(json.dumps(selection))
    (folder/'action_error_report.json').write_text(json.dumps(report))
    (folder/'action_error_cases.jsonl').write_text('\n'.join(map(json.dumps,cases))+'\n')
    return folder,info



def test_0015_real_schema_has_cases_only_at_report_level(tmp_path):
    """G1-0015 emits report.cases, not report.action_errors.cases.

    0016 used to require the nested field, rejecting every real 0015 report
    while its synthetic fixture mistakenly supplied it.
    """
    folder, info = _reference_fixture(tmp_path)
    report = json.loads((folder/'action_error_report.json').read_text())
    assert report['cases'] == 320
    assert 'cases' not in report['action_errors']
    rows, _, _ = rank._load_action_reference(folder, info)
    assert len(rows) == 158


def test_0016_mismatch_reports_field_without_relaxing_integrity(tmp_path):
    folder, info = _reference_fixture(tmp_path)
    reference = folder/'action_error_report.json'
    report = json.loads(reference.read_text())
    report['action_errors']['missing_actions'] = 215
    reference.write_text(json.dumps(report))
    with pytest.raises(ValueError, match=r'INVALID_ACTION_REFERENCE:report.action_errors.missing_actions'):
        rank._load_action_reference(folder, info)
    report['action_errors']['missing_actions'] = 216
    report['cases'] = 319
    reference.write_text(json.dumps(report))
    with pytest.raises(ValueError, match=r'INVALID_ACTION_REFERENCE:report.cases'):
        rank._load_action_reference(folder, info)

def test_rechecks_frozen_report_and_case_counts(tmp_path):
    folder,info=_reference_fixture(tmp_path)
    rows,paths,hashes=rank._load_action_reference(folder,info)
    assert len(rows)==158 and len(hashes)==3
    report=folder/'action_error_report.json'
    j=json.loads(report.read_text());j['g2_unlocked']=True;report.write_text(json.dumps(j))
    with pytest.raises(ValueError,match='INVALID_ACTION_REFERENCE'):
        rank._load_action_reference(folder,info)


def test_rejects_tampered_individual_action_evidence(tmp_path):
    folder,info=_reference_fixture(tmp_path)
    path=folder/'action_error_cases.jsonl'
    cases=[json.loads(s) for s in path.read_text().splitlines()]
    cases[0]['missing_actions'][0]['reason']='null_wins'
    path.write_text('\n'.join(map(json.dumps,cases))+'\n')
    with pytest.raises(ValueError,match='ACTION_REFERENCE_COUNTS'):
        rank._load_action_reference(folder,info)


def test_cli_exposes_no_training_or_selection_knobs(capsys):
    with pytest.raises(SystemExit) as e:
        cur.main(['frob-g1-action-ranking','--help'])
    assert e.value.code==0
    text=capsys.readouterr().out
    for arg in ('--transfer-report','--validation-run','--action-run','--output','--device'):
        assert arg in text
    for arg in ('--shift','--seed','--train','--promote','--g2','--challenge','--power','--penalty'):
        assert arg not in text


def test_full_320_case_mock_replay_and_action_competition(tmp_path,monkeypatch):
    """Replay the winner on the identical logits, then recover 100 blocked spans."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_g1_calibration_validation as val
    rows=[]
    references={}
    records=[]
    cases=[]
    for i in range(1,321):
        fragments=i>100
        gold=([(0,1,'action'),(1,2,'action')] if fragments else [(0,2,'action')])
        row={'idx':i,'prompt':'ab','family':'encounter' if i%2 else 'allocation',
             'object_spans':[{'start':a,'end':b,'kind':k} for a,b,k in gold]}
        rows.append(row)
        baseline={(0,1,'action'),(1,2,'action')}
        records.append((baseline,set(gold)))
        cases.append({'configurations':{rank.WINNER_KEY:{
            'correct':len(baseline & set(gold)),
            'missing':len(set(gold)-baseline),
            'false_positives':len(baseline-set(gold)),
            'exact':baseline==set(gold)}}})
        if not fragments:
            references[i]={'missing_actions':[{'span':[0,2], 'reason':'linear_decoder_excluded'}],
                'false_positive_actions':[{'span':[0,1]},{'span':[1,2]}]}
    metrics=val._summarize(records,rows)
    head_manifest={'stage':'G1','head_sha256':'fixed-head',
        'model_id':'mock-Qwen','source_adapter':'mock-adapter'}
    info={'validation_rows':rows,'validation_cases':cases,
        'validation_report':{'metrics':{rank.WINNER_KEY:metrics},
            'validation_dataset_sha256':'dataset'},
        'validation_file_sha256':{'validation_report.json':'validation-file'},
        'entries':{'null_focused':{'checkpoint':tmp_path/'head','manifest':head_manifest}},
        'adapter_sha':'adapter',
        'checkpoint_files':{'null_focused':{'manifest_sha256':'m','head_file_sha256':'h'}}}
    monkeypatch.setattr(rank,'_load_prior_inputs',lambda *a:info)
    inputs=tmp_path/'ref.json';inputs.write_text('immutable reference')
    monkeypatch.setattr(rank,'_load_action_reference',lambda *a:
        (references,{'ref.json':inputs},{'ref.json':rank._file_sha(inputs)}))
    monkeypatch.setattr(rank,'_verify_prior_unchanged',lambda *a:None)
    monkeypatch.setattr(backbone,'adapter_fingerprint',lambda *a:'adapter')
    class Head:
        def eval(self): return self
        def __call__(self,hidden,*,eligible,active_stage):
            assert not torch.is_grad_enabled() and active_stage=='G1'
            logits,valid=make_logits(2,2)
            for pos in (0,1):add_action(logits,pos,pos,4.)
            if int(hidden[0,0])<=100:add_action(logits,0,1,3.)
            return {'span_logits':logits,'span_valid':valid}
    monkeypatch.setattr(cur,'_load_checkpoint',lambda *a,**kw:(Head(),head_manifest))
    monkeypatch.setattr(backbone,'model_components',lambda *a:(None,None))
    def capture(model,tok,row,device,max_length):
        prepared={'eligible':torch.tensor([True,True]),'prompt':'ab',
                  'prompt_start':0,'offsets':[(0,1),(1,2)]}
        return {'prepared':prepared,'hidden':torch.tensor([[float(row['idx'])],[0.]])}
    monkeypatch.setattr(cur,'capture',capture)
    def labels(prepared,row):
        spans=([{'token_first':0,'token_last':0,'kind':'action'},
                {'token_first':1,'token_last':1,'kind':'action'}] if row['idx']>100 else
               [{'token_first':0,'token_last':1,'kind':'action'}])
        return {'spans':spans}
    monkeypatch.setattr(cur,'aligned_labels',labels)
    out=tmp_path/'result'
    result=rank.run(root=tmp_path,transfer_report='unused',fresh_run='unused',
        null_run='unused',validation_run='unused',action_run='unused',
        output=out,device='cpu')
    assert result['baseline_metrics']==metrics
    assert result['baseline_0015_competing_action_spans']==100
    assert result['alternatives']['action_width_150']['paired_changes']['recovered_excluded_actions']==100
    assert result['alternatives']['action_width_150']['metrics']['exact_prompts']==320
    assert result['g2_unlocked'] is False and result['no_training'] is True
    assert len((out/'action_ranking_competitors.jsonl').read_text().splitlines())==100
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        rank.run(root=tmp_path,transfer_report='unused',fresh_run='unused',
            null_run='unused',validation_run='unused',action_run='unused',
            output=out,device='cpu')
