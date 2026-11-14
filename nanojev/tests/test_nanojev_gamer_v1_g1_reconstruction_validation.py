"""G1-0031 contracts: report replay, frozen 1.25 arm, disjoint gold-safe sources."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g1_reconstruction_validation as y
from tools import nanojev_gamer_v1_g1_component_reconstruction as x
from tools import nanojev_gamer_v1_g1_layout_filter as f
from tools import nanojev_gamer_v1_g1_competition_rescue as c
from tools.nanojev_gamer_v1_graph_corpus import encounter,allocation


def span(a,b,kind='action'):
    return {'token_first':a,'token_last':b,'kind':kind}


def keys(spans):
    return {(v['token_first'],v['token_last'],v['kind']) for v in spans}


def test_frozen_0030_contract_still_rejects_undeclared_preferences():
    with pytest.raises(ValueError,match='UNDECLARED_BONUS'):
        x.reconstruct_components([],[],[],[],{},guard_bonus=.4)
    with pytest.raises(ValueError,match='UNDECLARED_BONUS'):
        x.reconstruct_components([],[],[],[],{},guard_bonus=.4,experimental_bonuses=(.5,))
    with pytest.raises(ValueError,match='UNDECLARED_BONUS'):
        x.reconstruct_components([],[],[],[],{},guard_bonus=.4,experimental_bonuses=(float('nan'),))


def test_shadow_arms_recover_surviving_guard_but_never_rejected_member():
    guard=[span(0,1),span(3,3)]
    frozen=[span(0,3)]
    clean={(0,1,'action'):1.1,(0,3,'action'):.61}
    for name,bonus in y.SHADOW.items():
        selected,stats=x.reconstruct_components(guard,frozen,frozen,frozen,clean,
            guard_bonus=bonus,experimental_bonuses=tuple(y.SHADOW.values()))
        assert keys(selected)<={*clean.keys()}
        assert (3,3,'action') not in keys(selected)
        assert stats['removed_guard_candidates_excluded']==1


def test_candidate_decoders_preserve_frozen_125_and_0026_outputs():
    logits=torch.zeros((1,1,8),dtype=torch.float32)
    logits[0,0,1]=10.
    pred={'span_logits':logits,'span_valid':torch.ones((1,1),dtype=torch.bool)}
    prepared={'prompt':'Alice','offsets':[(0,5)],'prompt_start':0}
    before,_,_,_=x.decode_reconstruction(pred,prepared)
    after,rejected,counts,stages=y.decode(pred,prepared)
    assert not rejected
    for arm in x.BONUSES:
        assert before[arm]==after[arm]
    assert before[f.CHALLENGER]==after[f.CHALLENGER]
    for arm in y.SHADOW:
        assert after[arm]=={(0,5,'actor')}
        assert counts[arm]['reconstructed_component']==0


def test_layouts_remap_gold_and_preserve_semantics():
    for i in range(30):
        original=(encounter if i%2==0 else allocation)(random.Random(51000+i),'dev',i)
        for layout in y.LAYOUTS:
            new=y.structural_row(original,layout)
            assert new['source_prompt_sha256']==y._sha(original['prompt'])
            assert len(new['object_spans'])==len(original['object_spans'])
            for old,changed in zip(original['object_spans'],new['object_spans']):
                assert old['kind']==changed['kind']
                assert original['prompt'][old['start']:old['end']]==new['prompt'][changed['start']:changed['end']]


def test_layout_refuses_test_split_and_unsupported_family():
    z=encounter(random.Random(40),'dev',1)
    with pytest.raises(ValueError,match='BAD_SOURCE'):
        y.structural_row(z,'unknown')
    z['split']='test'
    with pytest.raises(ValueError,match='BAD_SOURCE'):
        y.structural_row(z,y.LAYOUTS[0])


def test_fresh_prompts_are_new_unique_and_semantically_disjoint(monkeypatch):
    monkeypatch.setattr(c,'_original_prompts',lambda dataset:[])
    a=encounter(random.Random(20),'dev',0)
    b=allocation(random.Random(21),'dev',1)
    prior={'source_prompt_sha256':y._sha(a['prompt'])}
    info={'dataset':'unused','fresh_rows':[a],'validation_rows':[b]}
    rows=y.new_rows(info,[prior],[prior],[prior],[prior])
    assert len(rows)==640
    assert len({z['prompt'] for z in rows})==640
    assert len({z.get('source_prompt_sha256',y._sha(z['prompt'])) for z in rows})==640
    assert all(z['split']=='dev' for z in rows[:320])
    assert all(z['split']=='ood_dev' for z in rows[320:])
    assert set(z['surface_shift'] for z in rows[320:])==set(y.LAYOUTS)
    assert all(z.get('source_prompt_sha256',y._sha(z['prompt'])) not in
               {y._sha(a['prompt']),y._sha(b['prompt'])} for z in rows)


def test_old_per_case_replay_verifies_exact_span_accounting():
    original=encounter(random.Random(10),'dev',0)
    prompt=original['prompt']
    gold=y.r._gold(original)
    guesses={name:set(gold) for name in
             (x.CONTROL,x.GUARD,f.CHALLENGER,*c.RATIOS,*x.BONUSES,*y.SHADOW)}
    saved={'group':'known_layout_replay','index':5,'family':'encounter',
           'layout':'original','prompt_sha256':y._sha(prompt),
           'configurations':{name:y.r._case_summary(guesses,gold)[name]
               for name in (x.CONTROL,x.GUARD,f.CHALLENGER,*c.RATIOS,*x.BONUSES)}}
    y._verify_replay(original,'known_layout_replay',5,saved,guesses)
    wrong={**saved,'index':6}
    with pytest.raises(ValueError,match='CASE_REPLAY_DRIFT'):
        y._verify_replay(original,'known_layout_replay',5,wrong,guesses)
    wrong={**saved,'configurations':dict(saved['configurations'])}
    wrong['configurations'][x.STRICT]={**wrong['configurations'][x.STRICT], 'missing':1}
    with pytest.raises(ValueError,match='CASE_REPLAY_DRIFT'):
        y._verify_replay(original,'known_layout_replay',5,wrong,guesses)


def test_assessment_detects_regression_and_newly_lost_gold():
    row=encounter(random.Random(8),'dev',0)
    gold=y.r._gold(row)
    original=set(gold)
    guesses={name:set(gold) for name in y.ARMS}
    result=y._assessment([(guesses,gold)],[row])
    assert all(result['checks'][name].values() for name in y.ARMS[1:])
    lost=sorted(original)[0]
    guesses[y.BASE].remove(lost)
    result=y._assessment([(guesses,gold)],[row])
    assert result['checks'][y.BASE]['no_previously_correct_gold_lost'] is False
    assert result['checks'][y.BASE]['no_missing_object_regression'] is False


def test_single_command_help_available():
    root=Path(__file__).resolve().parents[1]
    proc=subprocess.run([sys.executable,'-u','-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g1-reconstruction-validation','--help'],cwd=root,capture_output=True,text=True,timeout=25)
    assert proc.returncode==0,proc.stderr
    assert '--reconstruction-run' in proc.stdout
    assert '--device' in proc.stdout


def test_0030_lineage_reads_four_files_and_rejects_forged_12_of_13(tmp_path):
    from tools.nanojev_gamer_v1_g1_structural_diagnosis import WINNER_HEAD
    hashes=[{'anchor':str(i)} for i in range(5)]
    info={'checkpoint_files':{WINNER_HEAD:{'manifest_sha256':'m','head_file_sha256':'h'}},
          'adapter_sha':'frozen_adapter'}
    cases=[{'group':group,'index':i+1} for group,total in x.GROUP_SIZES.items()
           for i in range(total)]
    assert len(cases)==1760
    losses=[{'group':'known_layout_replay','index':i+1,'kind':'action',
             'gold_span':[i,i+1],
             'recovered':{x.NEUTRAL:i<5,x.PRIMARY:i<7,x.STRICT:i<12}}
             for i in range(13)]
    fresh=[{'source_prompt_sha256':str(i)} for i in range(320)]
    report={'schema':x.SCHEMA,
        'status':'INFERENCE_ONLY_COMPONENT_RECONSTRUCTION_NO_PROMOTION',
        'source_0029_file_sha256':hashes[0],
        'source_0028_file_sha256':hashes[1],
        'source_0027_file_sha256':hashes[2],
        'source_0026_file_sha256':hashes[3],
        'source_0022_file_sha256':hashes[4],
        'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
        'source_adapter_sha256':'frozen_adapter',
        'prior_losses_replayed':13,
        'prior_losses_recovered':{x.NEUTRAL:5,x.PRIMARY:7,x.STRICT:12},
        'newly_lost_gold_vs_filter_by_arm':{arm:0 for arm in x.BONUSES},
        'primary_candidate_meets_all_development_gates':False,
        'fresh_cases':x.NEW_CASES,'fresh_seed':x.NEW_SEED,
        'fresh_layouts':list(x.NEW_LAYOUTS),
        'configurations_predeclared':[x.CONTROL,x.GUARD,f.CHALLENGER,*c.RATIOS,*x.BONUSES],
        'no_training':True,'heads_frozen':True,'gold_used_after_inference_only':True,
        'original_retention_pass':True,'fresh_validation_pass':True,
        'production_promotion':False,'certification_authorized':False,
        'sealed_test_used':False,'challenge_or_test_split_used':False,'g2_unlocked':False,
        'groups':{g:{} for g in x.GROUP_SIZES},
        'fresh_dataset_sha256':hashlib.sha256(x._jsonl_bytes(fresh)).hexdigest()}
    (tmp_path/x.REPORT).write_text(json.dumps(report),encoding='utf-8')
    for name,rows in ((x.CASES,cases),(x.LOSSES,losses),(x.FRESH_ROWS,fresh)):
        (tmp_path/name).write_bytes(x._jsonl_bytes(rows))
    out=y.load_0030(tmp_path,info,*hashes)
    assert len(out[2])==13
    assert len(out[1])==1760
    assert len(out[3])==320
    report['prior_losses_recovered'][x.STRICT]=13
    (tmp_path/x.REPORT).write_text(json.dumps(report),encoding='utf-8')
    with pytest.raises(ValueError,match='REFERENCE_DRIFT:prior_losses_recovered'):
        y.load_0030(tmp_path,info,*hashes)


def test_residual_audit_uses_0030_recovery_not_0029_record():
    """0029 diagnostic rows have no `recovered`; 0030 loss rows do."""
    old={'group':'known_layout_replay','index':1,'kind':'action',
         'layout':'ledger_grid','gold_span':[0,1],
         'guard_gold_token_selection':[0,0,'action']}
    row={'prompt':'A B C','object_spans':[{'start':0,'end':1,'kind':'action'}]}
    prepared={'prompt':row['prompt'],'prompt_start':0,
              'offsets':[(0,1),(2,3),(4,5)]}
    guard=[span(0,0),span(2,2)]
    filtered=[span(0,2)]
    clean={(0,0,'action'):0.9,(0,2,'action'):0.4}
    stages={'selected':{'guard':guard,'filtered':filtered,
                        'filtered_base':filtered,'filtered_wide':filtered},
            'filtered_catalog':clean,
            'all_catalog':{**clean,(2,2,'action'):0.6},
            'rejected':[{'token_first':2,'token_last':2,'kind':'action',
                         'reason':'scaffold_heading'}]}
    guesses={arm:set() for arm in (f.CHALLENGER,*x.BONUSES,*y.SHADOW)}
    expected={x.NEUTRAL:False,x.PRIMARY:False,x.STRICT:False}
    result=y._residual_audit(old,row,prepared,stages,guesses,expected)
    assert result['old_125_recovered'] is False
    assert set(result['candidate_recovered'])==set(x.BONUSES)|set(y.SHADOW)
    assert all(v is False for v in result['candidate_recovered'].values())
    y._verify_loss_replay({'recovered':expected},result)
    assert result['guard_component_count']==2
    assert result['rejected_guard_member_count']==1
    assert result['candidate_pool_size']==2
    expected[x.STRICT]=True
    result=y._residual_audit(old,row,prepared,stages,guesses,expected)
    assert result['old_125_recovered'] is True
    # Exercise the actual residual-audit arm recording, not only a synthetic map.
    guesses[x.NEUTRAL].add((0,1,'action'))
    guesses[x.PRIMARY].add((0,1,'action'))
    guesses[x.STRICT].add((0,1,'action'))
    recovered=y._residual_audit(old,row,prepared,stages,guesses,expected)
    assert all(recovered['candidate_recovered'][arm] for arm in x.BONUSES)
    observed={arm:True for arm in x.BONUSES}
    y._verify_loss_replay({'recovered':observed},recovered)
    with pytest.raises(ValueError,match='0030_LOSS_REPLAY_DRIFT'):
        y._verify_loss_replay({'recovered':{**observed,x.NEUTRAL:False}},recovered)


def test_loss_replay_checks_all_thirteen_frozen_0030_arms():
    """Avoid the 0031 KeyError: only 1.25/shadows are in evaluation ARMS."""
    assert x.NEUTRAL not in y.ARMS and x.PRIMARY not in y.ARMS
    assert set(x.BONUSES)=={x.NEUTRAL,x.PRIMARY,x.STRICT}
    for i in range(13):
        expected={x.NEUTRAL:i<5,x.PRIMARY:i<7,x.STRICT:i<12}
        item={'candidate_recovered':{**expected,
             **{arm:False for arm in y.SHADOW}}}
        y._verify_loss_replay({'recovered':expected},item)
        for arm in x.BONUSES:
            corrupted={'candidate_recovered':dict(item['candidate_recovered'])}
            corrupted['candidate_recovered'][arm]=not expected[arm]
            with pytest.raises(ValueError,match='0030_LOSS_REPLAY_DRIFT'):
                y._verify_loss_replay({'recovered':expected},corrupted)
        omitted={'candidate_recovered':dict(item['candidate_recovered'])}
        omitted['candidate_recovered'].pop(x.NEUTRAL)
        with pytest.raises(ValueError,match='0030_LOSS_REPLAY_DRIFT'):
            y._verify_loss_replay({'recovered':expected},omitted)


def test_loss_replay_rejects_incomplete_saved_0030_recovery():
    item={'candidate_recovered':{arm:False for arm in (*x.BONUSES,*y.SHADOW)}}
    for invalid in (None,{}, {x.STRICT:False},
                    {**{arm:False for arm in x.BONUSES},'unexpected':False}):
        with pytest.raises(ValueError,match='0030_LOSS_REPLAY_DRIFT'):
            y._verify_loss_replay({'recovered':invalid},item)
