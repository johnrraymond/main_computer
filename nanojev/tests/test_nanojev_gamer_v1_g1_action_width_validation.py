"""G1-0018: read-only, seed-disjoint frozen action-width acceptance (CPU mocks)."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from tools import nanojev_gamer_v1_g1_action_width_validation as val
from tools import nanojev_gamer_v1_g1_action_ranking as rank
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools.nanojev_gamer_v1_g1_fresh_dev import fresh_rows as prior_fresh
from tools.nanojev_gamer_v1_g1_calibration_validation import validation_rows as prior_320
from tools.nanojev_gamer_v1_graph_head import SPAN_CLASS_COUNT


def test_fixed_two_candidates_seed_and_no_training_configuration():
    assert val.CASES == 320
    assert val.SEED not in (174321,94117)
    assert val.CANDIDATES == (
        ('null_focused_4_control',1.,0.),
        ('null_focused_4_action_width_125',1.25,0.),
    )


def test_new_generation_is_balanced_deterministic_and_excludes_prior(tmp_path):
    source = tmp_path/'orig.jsonl'
    corpus.generate(source,train=8,dev=8,challenge=4,test=4,seed=31)
    old=prior_fresh(source)
    preceding=prior_320(source,old)
    rows=val.fresh_rows(source,old,preceding)
    assert rows == val.fresh_rows(source,old,preceding)
    assert len(rows)==320
    assert len({r['prompt'] for r in rows})==320
    assert {r['split'] for r in rows}=={'dev'}
    assert sum(r['family']=='encounter' for r in rows)==160
    assert sum(r['family']=='allocation' for r in rows)==160
    old_prompts={r['prompt'] for r in cur.load_rows(source) if r['split'] in ('train','dev')}
    old_prompts |= {r['prompt'] for r in old+preceding}
    assert not old_prompts.intersection({r['prompt'] for r in rows})
    with pytest.raises(ValueError,match='GENERATION_SETTINGS_LOCKED'):
        val.fresh_rows(source,old,preceding,seed=2)
    with pytest.raises(ValueError,match='GENERATION_SETTINGS_LOCKED'):
        val.fresh_rows(source,old,preceding,cases=80)
    with pytest.raises(ValueError,match='INVALID_PRIOR_ROW_COUNT'):
        val.fresh_rows(source,old,preceding+[preceding[0]] )
    with pytest.raises(ValueError,match='INVALID_PRIOR_PROMPTS'):
        val.fresh_rows(source,old,preceding[:-1]+[old[0]])


def _ranking_fixture(tmp_path):
    path=tmp_path/'rank';path.mkdir()
    selection=dict(key='null_focused_4')
    report={
        'schema':rank.SCHEMA,'status':'FROZEN_DEV_ACTION_RANKING_ABLATION_ONLY',
        'cases':320,'families':{'encounter':160,'allocation':160},
        'baseline':'winner_control','winner_checkpoint':'null_focused',
        'winner_null_logit_shift':4.,'baseline_metrics':{'cases':320,'expected':8982},
        'variants_predeclared':[{'name':name,'action_width_power':power,
                                 'action_per_span_penalty':penalty}
                                for name,power,penalty in rank.VARIANTS],
        'source_0015_file_sha256':{'file':'h'},
        'source_0014_validation_report_sha256':'r',
        'source_0014_validation_dataset_sha256':'d',
        'head_sha256':'head','head_files_sha256':{'manifest_sha256':'x','head_file_sha256':'y'},
        'source_adapter_sha256':'adapter','baseline_0015_competing_action_spans':100,
        'alternatives':{name:{} for name,_,_ in rank.VARIANTS[1:]},
        'exploratory_ranking':['action_width_125'],
        'one_feature_capture_per_prompt':True,'heads_frozen':True,
        'candidate_eligibility_unchanged':True,'decoder_production_unchanged':True,
        'no_training':True,'challenge_or_test_split_used':False,
        'certification_authorized':False,'production_promotion':False,'g2_unlocked':False,
    }
    report['alternatives']['action_width_125']={
        'action_width_power':1.25,'action_per_span_penalty':0.,
        'dev_improves_exact_and_f1':True,
        'metrics':{'cases':320,'expected':8982,'exact_prompts':140,
                   'correct':8715,'predicted':8919,
                   'missing_objects':267,'false_positive_objects':204,'f1':.97369},
        'paired_changes':{'recovered_actions':17,'lost_actions':18},
    }
    info={'validation_report':{'metrics':{'null_focused_4':report['baseline_metrics']},
                               'validation_dataset_sha256':'d'},
          'validation_file_sha256':{'validation_report.json':'r'},
          'entries':{'null_focused':{'manifest':{'head_sha256':'head'}}},
          'checkpoint_files':{'null_focused':report['head_files_sha256']},
          'adapter_sha':'adapter'}
    for file in ('action_ranking_changes.jsonl','action_ranking_competitors.jsonl'):
        (path/file).write_text('')
    (path/'action_ranking_report.json').write_text(json.dumps(report))
    return path,info,report


def test_0016_reference_accepts_actual_report_contract(tmp_path):
    path,info,report=_ranking_fixture(tmp_path)
    result,files,hashes=val._load_ranking_reference(path,info,{'file':'h'})
    assert result==report and len(files)==len(hashes)==3


@pytest.mark.parametrize('field',[
    'cases','source_0015_file_sha256','head_sha256','baseline_metrics',
    'no_training','g2_unlocked','exploratory_ranking','variants_predeclared',
])
def test_0016_reference_rejects_tampering(tmp_path,field):
    path,info,report=_ranking_fixture(tmp_path)
    report[field] = 'tampered'
    (path/'action_ranking_report.json').write_text(json.dumps(report))
    with pytest.raises(ValueError,match='GAMER_G1_0018_UNTRUSTED_RANKING'):
        val._load_ranking_reference(path,info,{'file':'h'})


def test_0016_reference_rejects_modified_challenger(tmp_path):
    path,info,report=_ranking_fixture(tmp_path)
    report['alternatives']['action_width_125']['metrics']['exact_prompts']=141
    (path/'action_ranking_report.json').write_text(json.dumps(report))
    with pytest.raises(ValueError,match='UNTRUSTED_RANKING'):
        val._load_ranking_reference(path,info,{'file':'h'})


def test_guard_detects_changed_reference_bytes(tmp_path,monkeypatch):
    path=tmp_path/'source';path.write_bytes(b'original')
    monkeypatch.setattr(val,'_verify_prior_unchanged',lambda *args:None)
    hashes={'source':hashlib.sha256(path.read_bytes()).hexdigest()}
    val._verify_no_drift({},None,None,None,{'source':path},hashes,{}, {})
    path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='ACTION_INPUT_CHANGED_DURING_RUN'):
        val._verify_no_drift({},None,None,None,{'source':path},hashes,{}, {})


def test_cli_exposes_only_fixed_candidates(capsys):
    with pytest.raises(SystemExit) as exc:
        cur.main(['frob-g1-action-width-validation','--help'])
    assert exc.value.code == 0
    help_text=capsys.readouterr().out
    for arg in ('--ranking-run','--action-run','--validation-run','--output','--device'):
        assert arg in help_text
    for arg in ('--power','--seed','--train','--promote','--g2','--test','--challenge'):
        assert arg not in help_text


def test_frozen_320_case_mock_evaluation_replays_control_and_challenger(tmp_path,monkeypatch):
    rows=[]
    for i in range(320):
        rows.append({'prompt':'abc','family':'encounter' if i%2==0 else 'allocation',
                     'split':'dev','object_spans':[{'start':0,'end':3,'kind':'action'}]})
    source=tmp_path/'weights';source.write_text('mock')
    info={
        'dataset':'ignored','fresh_rows':[], 'validation_rows':[],
        'entries':{'null_focused':{'checkpoint':'cp','manifest':{
            'source_adapter':str(source),'model_id':'mock','head_sha256':'head'}}},
        'adapter_sha':'adapter',
        'checkpoint_files':{'null_focused':{'manifest_sha256':'m','head_file_sha256':'h'}},
        'validation_report':{'validation_dataset_sha256':'old','metrics':{'null_focused_4':{}}},
        'fresh_dataset_sha':'fresh',
    }
    monkeypatch.setattr(val,'_load_prior_inputs',lambda *args:info)
    monkeypatch.setattr(val,'_load_action_reference',lambda *args:({}, {}, {'action_error_report.json':'action-sha'}))
    old={'baseline_metrics':{},'alternatives':{'action_width_125':{'metrics':{}}}}
    monkeypatch.setattr(val,'_load_ranking_reference',lambda *args:(old,{}, {'action_ranking_report.json':'ranking-sha'}))
    monkeypatch.setattr(val,'fresh_rows',lambda *args:rows)
    monkeypatch.setattr(val,'_verify_no_drift',lambda *args:None)
    monkeypatch.setattr(backbone,'adapter_fingerprint',lambda *args:'adapter')
    monkeypatch.setattr(backbone,'model_components',lambda *args:(object(),object()))
    class Head:
        def eval(self):return self
        def __call__(self,hidden,**kwargs):
            logits=torch.full((3,3,SPAN_CLASS_COUNT),-40.,dtype=torch.float)
            logits[:,:,0]=4.
            for i in range(3):logits[i,0,rank.ACTION_CLASS]=4.
            logits[0,2,rank.ACTION_CLASS]=3.5
            valid=torch.zeros(3,3,dtype=torch.bool)
            for i in range(3):valid[i,:3-i]=True
            return {'span_logits':logits,'span_valid':valid}
    monkeypatch.setattr(cur,'_load_checkpoint',lambda *args,**kwargs:(Head(),{
        'stage':'G1','head_sha256':'head'}))
    monkeypatch.setattr(cur,'capture',lambda *args:{
        'prepared':{'eligible':torch.ones(3),'offsets':[(0,1),(1,2),(2,3)],
                    'prompt_start':0,'prompt':'abc'},
        'hidden':torch.zeros(3,4),
    })
    output=tmp_path/'new'
    report=val.run(root='/',transfer_report='transfer',fresh_run='fresh',null_run='null',
                   validation_run='validation',action_run='action',ranking_run='rank',
                   output=output,device='cpu')
    assert report['cases']==320
    assert report['metrics'][val.CONTROL]['exact_prompts']==0
    assert report['metrics'][val.CHALLENGER]['exact_prompts']==320
    assert report['paired_comparison']['b_only_exact']==320
    assert report['metrics'][val.CONTROL]['false_positive_objects']==960
    assert report['metrics'][val.CHALLENGER]['false_positive_objects']==0
    assert report['g2_unlocked'] is False
    assert (output/'action_width_validation_dev.jsonl').exists()
    assert len((output/'action_width_validation_per_case.jsonl').read_text().splitlines())==320
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        val.run(root='/',transfer_report='transfer',fresh_run='fresh',null_run='null',
                validation_run='validation',action_run='action',ranking_run='rank',
                output=output,device='cpu')
