"""G1-0029: diagnose prior decoder losses without introducing new selection rules."""
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

import pytest

from tools import nanojev_gamer_v1_g1_component_diagnosis as d
from tools import nanojev_gamer_v1_g1_competition_rescue as c
from tools import nanojev_gamer_v1_g1_layout_filter as f


def _span(a,b,kind):
    return {'token_first':a,'token_last':b,'kind':kind}


def test_one_to_one_margin_gate_recognizes_both_sides():
    guard=[_span(0,1,'actor')]
    filtered=[_span(0,2,'action')]
    group,missing,cause=d.locate_loss_component(
        (0,1,'actor'),guard,filtered,{(0,1,'actor'):1.0,(0,2,'action'):2.0})
    assert cause=='0028_confidence_gate_rejected_guard'
    assert not missing
    group,missing,cause=d.locate_loss_component(
        (0,1,'actor'),guard,filtered,{(0,1,'actor'):3.0,(0,2,'action'):2.0})
    assert cause=='0028_should_have_restored_guard'


def test_removed_guard_partner_is_distinct_from_valid_gold():
    guard=[_span(0,0,'actor')]
    filtered=[_span(0,1,'action')]
    _,missing,cause=d.locate_loss_component((0,0,'actor'),guard,filtered,{(0,1,'action'):2.0})
    assert missing==[(0,0,'actor')]
    assert cause=='removed_guard_partner_blocks_0028'


def test_multiple_guard_spans_trigger_0028_ambiguity_even_when_gold_survives():
    guard=[_span(0,0,'actor'),_span(2,2,'outcome')]
    filtered=[_span(0,3,'action')]
    _,missing,cause=d.locate_loss_component((0,0,'actor'),guard,filtered,
        {(0,0,'actor'):2.0,(0,3,'action'):4.0})
    assert cause=='complex_component_excluded_by_0028'
    assert missing==[(2,2,'outcome')]


def test_zero_challenger_is_not_misdiagnosed_as_confidence_gate():
    _,missing,cause=d.locate_loss_component(
        (0,0,'actor'),[_span(0,0,'actor')],[],{(0,0,'actor'):1.0})
    assert not missing
    assert cause=='no_filtered_selection_in_component'


def test_present_on_both_sides_is_not_a_lost_span():
    with pytest.raises(ValueError,match='GOLD_NOT_LOST'):
        d.locate_loss_component((0,0,'actor'),[_span(0,0,'actor')],
                                [_span(0,0,'actor')],{(0,0,'actor'):1.0})


def test_span_descriptor_shows_token_and_character_boundaries():
    prepared={'prompt':'ACTOR','offsets':[(0,5)],'prompt_start':0}
    key=(0,0,'actor')
    desc=d.describe_span(key,prepared,{key:2.0},{key:2.0},{}, {(0,5,'actor')})
    assert desc['gold_match'] and desc['survives_filter']
    assert desc['char_span']==[0,5] and desc['surface']=='ACTOR'
    assert desc['linear_interval_score']==2.0
    assert desc['wide_interval_score']==2.0
    assert desc['filter_rejection_reason'] is None


def test_diagnostic_gold_cannot_make_a_rejected_proposal_survive():
    prepared={'prompt':'ACTOR','offsets':[(0,5)],'prompt_start':0}
    key=(0,0,'actor')
    desc=d.describe_span(key,prepared,{key:2.0},{},{key:'scaffold_heading'}, {(0,5,'actor')})
    assert desc['gold_match']
    assert not desc['survives_filter']
    assert desc['filter_rejection_reason']=='scaffold_heading'


def test_exact_thirteen_record_coverage_required():
    with pytest.raises(ValueError,match='WRONG_LOSS_COUNT'):
        d.summarize([])
    records=[{'cause_class':'complex_component_excluded_by_0028','layout':'key_value_index',
              'gold_survived_candidate_filter':True,
              'stage_selected_gold':{'filtered_base':False,'filtered_wide':True},
              'component':{'guard_count':2,'filtered_count':1,'removed_guard_member_count':1}}
             for _ in range(13)]
    counts=d.summarize(records)
    assert counts['components_with_multiple_guard_spans']==13
    assert counts['gold_proposals_rejected']==0
    assert counts['gold_selected_by_filtered_wide']==13


def test_0028_lineage_rejects_wrong_recovery_even_if_13_losses(tmp_path):
    info={'checkpoint_files':{d.WINNER_HEAD:{'manifest_sha256':'x','head_file_sha256':'y'}},
          'adapter_sha':'adapter'}
    hash26={'a':'26'};hash27={'b':'27'};hash22={'c':'22'}
    old=[{'group':'new_layout_replay','index':i,'kind':'action','gold_span':[i,i+1]}
         for i in range(1,14)]
    report={'schema':c.SCHEMA,'status':'INFERENCE_ONLY_DEVELOPMENT_NO_PROMOTION',
       'prior_losses_replayed':13,'prior_losses_recovered':{key:0 for key in c.RATIOS},
       'newly_lost_gold_by_arm':{key:0 for key in c.RATIOS},
       'primary_candidate_meets_all_development_gates':False,
       'retention_0027_file_sha256':hash27,'layout_0026_file_sha256':hash26,
       'ood_0022_file_sha256':hash22,'checkpoint_file_sha256':info['checkpoint_files'][d.WINNER_HEAD],
       'source_adapter_sha256':'adapter',
       'configurations_predeclared':[d.CONTROL,d.GUARD,f.CHALLENGER,*c.RATIOS],
       'heads_frozen':True,'no_training':True,'gold_used_after_inference_only':True,
       'original_retention_pass':True,'independent_validation_pass':True,
       'production_promotion':False,'certification_authorized':False,
       'g2_unlocked':False,'sealed_test_used':False,'challenge_or_test_split_used':False,
       'independent_dataset_sha256':hashlib.sha256(d._jsonl_bytes([])).hexdigest()}
    (tmp_path/c.REPORT).write_text(json.dumps(report),encoding='utf-8')
    (tmp_path/c.CASES).write_text('\n'.join(json.dumps({'group': 'x','index':i})
                                              for i in range(1440))+'\n',encoding='utf-8')
    (tmp_path/c.LOSSES).write_text('\n'.join(json.dumps({**x,
            'recovered':{arm:False for arm in c.RATIOS}}) for x in old)+'\n',encoding='utf-8')
    (tmp_path/c.FRESH_ROWS).write_text('\n'.join('{}' for _ in range(320))+'\n',encoding='utf-8')
    with pytest.raises(ValueError,match='INDEPENDENT_DATASET_DRIFT'):
        d.load_0028(tmp_path,info,hash26,hash27,hash22,old)
    report['prior_losses_recovered'][c.PRIMARY]=1
    (tmp_path/c.REPORT).write_text(json.dumps(report),encoding='utf-8')
    with pytest.raises(ValueError,match='REFERENCE_DRIFT:prior_losses_recovered'):
        d.load_0028(tmp_path,info,hash26,hash27,hash22,old)


def test_cli_help_is_the_normal_module_command():
    root=Path(__file__).resolve().parents[1]
    completed=subprocess.run([sys.executable,'-u','-m',
        'tools.nanojev_gamer_v1_graph_curriculum','frob-g1-component-diagnosis','--help'],
        cwd=root,text=True,capture_output=True,timeout=25)
    assert completed.returncode==0,completed.stderr
    assert '--device' in completed.stdout
    assert '--competition-run' in completed.stdout


def test_full_loss_trace_captures_component_and_two_decoder_stages():
    prompt='AA BB'
    prepared={'prompt':prompt,'offsets':[(0,2),(3,5)],'prompt_start':0}
    old={'group':'known_layout_replay','index':4,'layout':'ledger_grid',
        'kind':'actor','gold_span':[0,2],'prompt_sha256':d._digest(prompt),
        'newly_selected_overlapping_spans':[{'span':[0,5],'kind':'action'}]}
    row={'prompt':prompt,'family':'allocation','surface_shift':'ledger_grid',
         'object_spans':[{'start':0,'end':2,'kind':'actor'}]}
    g=(0,0,'actor');b=(0,1,'action')
    stages={'selected':{'original_base':[_span(0,0,'actor')],
             'original_wide':[_span(0,0,'actor')],
             'guard':[_span(0,0,'actor')],
             'filtered_base':[_span(0,1,'action')],
             'filtered_wide':[_span(0,1,'action')],
             'filtered':[_span(0,1,'action')]},
        'guesses':{d.GUARD:{(0,2,'actor')},f.CHALLENGER:{(0,5,'action')},
                   c.PRIMARY:{(0,5,'action')},c.STRICT:{(0,5,'action')}},
        'all_catalog':{g:1.0,b:2.0},
        'filtered_catalog':{g:1.0,b:2.0},'rejected':[],
        'guard_decisions':[{'control':[[0,0,'actor']], 'challenger':[[0,1,'action']],
                             'reason':'not_longer_overlap','take_challenger':False}],
        'filtered_decisions':[{'control':[[0,0,'actor']], 'challenger':[[0,1,'action']],
                                'reason':'accepted','take_challenger':True}]}
    record=d.examine_loss(old,row,prepared,stages,
        {'prompt_sha256':d._digest(prompt),'layout':'ledger_grid','family':'allocation'})
    assert record['cause_class']=='0028_confidence_gate_rejected_guard'
    assert record['gold_survived_candidate_filter']
    assert record['stage_selected_gold']['guard']
    assert not record['stage_selected_gold']['filtered']
    assert record['component']['guard_selected'][0]['surface']=='AA'
    assert record['component']['filtered_selected'][0]['surface']=='AA BB'
    assert record['component']['filtered_linear_score_sum']==4.0
    assert record['filtered_selective_gate_decisions'][0]['take_challenger']


def test_frozen_stages_integrates_four_part_competition_decoder():
    """Regression for actual 0028 four-value API: (guesses,rejected,kept,signals)."""
    import torch

    logits=torch.zeros((1,1,8),dtype=torch.float32)
    logits[0,0,1]=10.0  # actor candidate; original and filter keep it
    pred={'span_logits':logits,'span_valid':torch.ones((1,1),dtype=torch.bool)}
    prepared={'prompt':'Alice','offsets':[(0,5)],'prompt_start':0}
    stages=d.frozen_stages(pred,prepared)
    expected={(0,5,'actor')}
    assert stages['guesses'][d.GUARD]==expected
    assert stages['guesses'][f.CHALLENGER]==expected
    assert stages['guesses'][c.PRIMARY]==expected
    assert stages['guesses'][c.STRICT]==expected
    assert stages['selected']['filtered']==[{'kind':'actor','token_first':0,'token_last':0}]
    assert not stages['rejected']
    assert stages['kept_count']==1
