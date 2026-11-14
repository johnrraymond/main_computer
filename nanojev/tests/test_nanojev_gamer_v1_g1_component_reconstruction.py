"""G1-0030: surviving-candidate reconstruction contracts and integration tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g1_component_reconstruction as x
from tools import nanojev_gamer_v1_g1_component_diagnosis as d
from tools import nanojev_gamer_v1_g1_competition_rescue as c
from tools import nanojev_gamer_v1_g1_layout_filter as f
from tools.nanojev_gamer_v1_graph_corpus import encounter,allocation


def span(a,b,kind='action'):
    return {'token_first':a,'token_last':b,'kind':kind}


def keys(spans):
    return {(q['token_first'],q['token_last'],q['kind']) for q in spans}


def test_reconstruct_replaces_broken_multi_guard_component_with_surviving_gold():
    # Guard selected two objects. The second was rejected by layout grammar.
    guard=[span(0,1),span(3,3)]
    filtered=[span(0,3)]
    clean={(0,1,'action'):1.9,(0,3,'action'):.6}
    selected,signals=x.reconstruct_components(guard,filtered,filtered,filtered,clean,
        guard_bonus=0.10)
    assert keys(selected)=={(0,1,'action')}
    assert signals['reconstructed_component']==1
    assert signals['removed_guard_candidates_excluded']==1
    assert signals['component_decision_changed']==1
    assert keys(selected)<=clean.keys()


def test_reconstruct_can_retain_filtered_competitor_when_it_wins_on_merit():
    guard=[span(0,1),span(3,3)]
    filtered=[span(0,3)]
    clean={(0,1,'action'):.6,(0,3,'action'):2.0}
    selected,signals=x.reconstruct_components(guard,filtered,filtered,filtered,clean,
        guard_bonus=0.10)
    assert keys(selected)=={(0,3,'action')}
    assert signals['reconstructed_component']==1
    assert signals['component_decision_changed']==0


def test_surviving_base_candidate_can_win_even_if_not_in_final_filter():
    guard=[span(0,0),span(2,2)]
    filtered=[span(0,2)]
    filtered_base=[span(0,0),span(1,2)]
    clean={(0,0,'action'):1.4,(1,2,'action'):1.8,(0,2,'action'):.5}
    selected,_=x.reconstruct_components(guard,filtered_base,filtered,filtered,clean,
        guard_bonus=0.0)
    assert keys(selected)=={(0,0,'action'),(1,2,'action')}


def test_no_reconstruction_without_removed_guard_partner():
    guard=[span(0,0),span(2,2)]
    filtered=[span(0,2)]
    clean={(0,0,'action'):10.,(2,2,'action'):10.,(0,2,'action'):.1}
    selected,counts=x.reconstruct_components(guard,filtered,filtered,filtered,clean,
        guard_bonus=.25)
    assert keys(selected)=={(0,2,'action')}
    assert counts['frozen_component_unchanged']==1


def test_rejected_guard_candidate_never_returns_even_if_highly_scored():
    guard=[span(0,1),span(3,3)]
    filtered=[span(0,3)]
    clean={(0,1,'action'):1.1,(0,3,'action'):1.0}
    for bonus in x.BONUSES.values():
        selected,_=x.reconstruct_components(guard,filtered,filtered,filtered,clean,
            guard_bonus=bonus)
        assert (3,3,'action') not in keys(selected)
        assert keys(selected)<=clean.keys()


def test_all_components_remain_nonoverlapping_and_order_independent():
    guard=[span(0,1),span(3,3),span(6,6),span(8,9),span(11,11)]
    filtered=[span(0,3),span(6,6),span(8,11)]
    clean={(0,1,'action'):2.,(0,3,'action'):.5,(6,6,'action'):3.,
           (8,9,'action'):2.,(8,11,'action'):1.}
    a,stats=x.reconstruct_components(guard,filtered,filtered,filtered,clean,
        guard_bonus=.25)
    b,_=x.reconstruct_components(list(reversed(guard)),list(reversed(filtered)),
        filtered,filtered,clean,guard_bonus=.25)
    assert a==b
    assert keys(a)=={(0,1,'action'),(6,6,'action'),(8,9,'action')}
    assert stats['reconstructed_component']==2


def test_if_candidate_set_too_large_use_frozen_filter():
    guard=[span(0,0),span(51,51)]
    filtered=[span(0,51)]
    base=[span(i,i) for i in range(49)]
    clean={(i,i,'action'):1. for i in range(49)}
    clean[(0,51,'action')]=1.
    selected,stats=x.reconstruct_components(guard,base,filtered,filtered,clean,
        guard_bonus=.1)
    assert keys(selected)=={(0,51,'action')}
    assert stats['bounded_fallback_to_filter']==1


def test_reject_selected_filtered_candidate_missing_from_clean_catalog():
    with pytest.raises(ValueError,match='SELECTED_REJECTED_PROPOSAL'):
        x.reconstruct_components([span(0,0)],[],[],[span(0,0)],{},guard_bonus=.1)


def test_reject_any_undeclared_bonus():
    with pytest.raises(ValueError,match='UNDECLARED_BONUS'):
        x.reconstruct_components([],[],[],[],{},guard_bonus=.11)


def test_neutral_schedule_is_optimal_and_inclusive_nonoverlapping():
    a=(0,1,'action');b=(0,0,'action');c=(1,1,'action')
    result=x._schedule({a,b,c},{a:2.,b:3.,c:3.},set(),0.)
    assert set(result)=={b,c}
    assert x._schedule({a},{a:2.},set(),0.)==[a]


def test_new_layout_remaps_and_preserves_author_gold_for_both_families():
    for i in range(20):
        maker=encounter if i%2==0 else allocation
        source=maker(random.Random(22000+i),'dev',i)
        for name in x.NEW_LAYOUTS:
            row=x.new_layout_row(source,name)
            assert row['source_prompt_sha256']==hashlib.sha256(source['prompt'].encode()).hexdigest()
            assert row['split']=='ood_dev'
            assert len(row['object_spans'])==len(source['object_spans'])
            for before,after in zip(source['object_spans'],row['object_spans']):
                assert before['kind']==after['kind']
                assert source['prompt'][before['start']:before['end']]==row['prompt'][after['start']:after['end']]


def test_new_layout_rejects_invalid_source():
    maker=encounter(random.Random(44),'dev',0)
    with pytest.raises(ValueError,match='BAD_SOURCE'):
        x.new_layout_row(maker,'bracket_register')
    maker['split']='test'
    with pytest.raises(ValueError,match='BAD_SOURCE'):
        x.new_layout_row(maker,'field_cards')


def test_reconstruction_decodes_with_actual_frozen_decoder_api():
    logits=torch.zeros((1,1,8),dtype=torch.float32)
    logits[0,0,1]=10.0
    pred={'span_logits':logits,'span_valid':torch.ones((1,1),dtype=torch.bool)}
    prepared={'prompt':'Alice','offsets':[(0,5)],'prompt_start':0}
    guesses,rejected,counts,stages=x.decode_reconstruction(pred,prepared)
    for arm in x.BONUSES:
        assert guesses[arm]==guesses[f.CHALLENGER]=={(0,5,'actor')}
        assert counts[arm]['reconstructed_component']==0
    assert not rejected


def test_diagnostic_lineage_rejects_tampered_source(tmp_path):
    info={'checkpoint_files':{x.WINNER_HEAD:{'manifest_sha256':'h','head_file_sha256':'c'}},
          'adapter_sha':'adapter'}
    paths26={'report':'26'};paths27={'report':'27'};paths28={'report':'28'};paths22={'report':'22'}
    losses=[{'group':'new_layout_replay','index':i,'kind':'actor','gold_span':[i,i+1]}
            for i in range(13)]
    complete=[]
    for item in losses:
        complete.append({**item,'cause_class':'complex_component_excluded_by_0028',
            'gold_survived_candidate_filter':True,
            'component':{'guard_count':2,'removed_guard_member_count':1}})
    report={'schema':d.SCHEMA,'status':'READ_ONLY_COMPONENT_DIAGNOSIS_NOT_INTERVENTION',
        'prior_losses_replayed':13,'source_0028_files_sha256':paths28,
        'source_0027_files_sha256':paths27,'source_0026_files_sha256':paths26,
        'source_0022_files_sha256':paths22,
        'checkpoint_file_sha256':info['checkpoint_files'][x.WINNER_HEAD],
        'source_adapter_sha256':'adapter',
        'production_promotion':False,'certification_authorized':False,
        'g2_unlocked':False,'sealed_test_used':False,'challenge_or_test_split_used':False,
        'heads_frozen':True,'no_training':True,'gold_used_after_inference_only':True,
        'decoder_unchanged':True,
        'summary':{'count':13,'cause_classes':{'complex_component_excluded_by_0028':13}}}
    (tmp_path/d.REPORT).write_text(json.dumps(report),encoding='utf-8')
    (tmp_path/d.LOSSES).write_text(''.join(json.dumps(rec)+'\n' for rec in complete),encoding='utf-8')
    got=x.load_0029(tmp_path,info,paths28,paths27,paths26,paths22,losses)
    assert len(got[1])==13
    report['source_0028_files_sha256']={'report':'different'}
    (tmp_path/d.REPORT).write_text(json.dumps(report),encoding='utf-8')
    with pytest.raises(ValueError,match='0029_DRIFT:source_0028'):
        x.load_0029(tmp_path,info,paths28,paths27,paths26,paths22,losses)


def test_normal_module_cli_is_available():
    root=Path(__file__).resolve().parents[1]
    proc=subprocess.run([sys.executable,'-u','-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g1-component-reconstruction','--help'],cwd=root,capture_output=True,text=True,
        timeout=25)
    assert proc.returncode==0,proc.stderr
    assert '--component-run' in proc.stdout and '--device' in proc.stdout


def test_new_320_rows_are_prompt_disjoint_and_semantically_valid(monkeypatch):
    # Use the real generators and real gold mapper; no tokenizer/GPU required.
    monkeypatch.setattr(c,'_original_prompts',lambda dataset:[])
    source=encounter(random.Random(1),'dev',0)
    previous=allocation(random.Random(2),'dev',1)
    lineage=lambda s:{'source_prompt_sha256':x._digest(s['prompt'])}
    info={'dataset':'unused','fresh_rows':[source],'validation_rows':[previous]}
    rows=x.new_rows(info,[lineage(source)],[lineage(previous)],[lineage(source)])
    assert len(rows)==320
    assert len(set(row['prompt'] for row in rows))==320
    assert len(set(row['source_prompt_sha256'] for row in rows))==320
    assert set(row['surface_shift'] for row in rows)==set(x.NEW_LAYOUTS)
    assert all(row['source_prompt_sha256'] not in
        {x._digest(source['prompt']),x._digest(previous['prompt'])} for row in rows)


def test_group_runner_compares_frozen_cases_and_emits_audit(tmp_path):
    # A miniature, real decoder forward pass with a synthetic frozen head.
    class Backbone:
        def prepare_prompt(self,tok,text,max_input_tokens):
            return {'prompt':text,'offsets':[(0,len(text))],'prompt_start':0,
                    'eligible':torch.ones((1,1),dtype=torch.bool)}
        def frozen_context(self,model,prepared,device):
            return torch.ones((1,2))
    class Head:
        def __call__(self,hidden,eligible,active_stage):
            logits=torch.zeros((1,1,8),dtype=torch.float32)
            logits[0,0,1]=10.0
            return {'span_logits':logits,'span_valid':torch.ones((1,1),dtype=torch.bool)}
    b=Backbone();row={'prompt':'Alice','split':'dev','family':'encounter',
        'object_spans':[{'kind':'actor','start':0,'end':5}]}
    pred=Head()(None,None,None)
    guesses,_,_,_=x.decode_reconstruction(pred,b.prepare_prompt(None,'Alice',2048))
    summary=x.r._case_summary(guesses,x.r._gold(row))
    # Mirror G1-0028's actual saved per-case shape: there is NO gold_count.
    old={'group':'unit_group','index':1,'layout':'original','family':'encounter',
         'prompt_sha256':x._digest('Alice'),
         'configurations':{name:summary[name] for name in
             (x.CONTROL,x.GUARD,f.CHALLENGER,*c.RATIOS)}}
    assert 'gold_count' not in old
    stats,rows,lost=x.evaluate_group('unit_group',[row],Head(),None,None,b,'cpu',2048,
        prior_cases={('unit_group',1):old})
    assert stats['metrics'][x.PRIMARY]['exact_prompts']==1
    assert rows[0]['configurations'][x.PRIMARY]['correct']==1
    assert not lost
    wrong=dict(old);wrong['configurations']=dict(old['configurations'])
    wrong['configurations'][x.CONTROL]={**wrong['configurations'][x.CONTROL],
        'missing':wrong['configurations'][x.CONTROL]['missing']+1}
    with pytest.raises(ValueError,match='FROZEN_0028_CASE_DRIFT'):
        x.evaluate_group('unit_group',[row],Head(),None,None,b,'cpu',2048,
            prior_cases={('unit_group',1):wrong})
    for key,value in (('group','wrong_group'),('index',2),('layout','wrong_layout'),
                      ('family','allocation'),('prompt_sha256','tampered')):
        tampered={**old,key:value}
        with pytest.raises(ValueError,match='FROZEN_0028_CASE_DRIFT'):
            x.evaluate_group('unit_group',[row],Head(),None,None,b,'cpu',2048,
                prior_cases={('unit_group',1):tampered})
