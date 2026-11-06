"""Bounded, read-only attribution of flat register predictions."""
import csv
import copy
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_register_attribution as audit
from tools import nanojev_gamer_v1_flat_registers as flat
from tools import nanojev_gamer_v1_staircase as stair
from tools import nanojev_gamer_v1_pairs as pairs


def _case(game,ix=4):
    return flat.build_oracle(pairs.generate_case(game,ix,47))


def _entry(doc,values,plan,*,forced=None):
    y,_=flat._eval_plan(plan,values)
    return {'case_sha256':doc['case_sha256'],'game_id':doc['game_id'],
            'predicted_game_id':forced if forced is not None else doc['game_id'],
            'target_zone':doc['target_zone'],'target':doc['final_answer']['float'],
            'predicted_answer':y,'learned_route_error':None}


def test_oracle_substitutions_match_certified_all_five_source_kinds():
    games=('captain_fuel_reserve','bertrand_oligopoly','rock_paper_scissors',
           'cournot_quantity','captain_relative_navigation')
    kinds=set()
    for game in games:
        doc=_case(game)
        kinds.add(doc['source_kind'])
        plan=flat._plan(doc)
        truth={r['id']:r['value']['float'] for r in doc['registers']}
        predicted=dict(truth)
        for key in sorted(predicted)[:2]:
            predicted[key]+=0.125
        item=_entry(doc,predicted,plan)
        case,single,pair=audit._attribution_for_case(item,
            {name:{'truth':v,'predicted':predicted[name]} for name,v in truth.items()},
            {game:{'plan':plan}})
        assert case['status']=='ATTRIBUTED'
        assert case['oracle_formula_answer']==pytest.approx(doc['final_answer']['float'],abs=1e-8)
        assert len(single)==len(truth)
        assert len(pair)==len(truth)*(len(truth)-1)//2
    assert 'matrix' in kinds or 'bounded_named_matrix' in kinds
    assert 'bounded_state_transition' in kinds


def test_single_register_attribution_exact_recovery_and_zero_sign():
    doc=_case('captain_fuel_reserve')
    plan=flat._plan(doc)
    truth={r['id']:r['value']['float'] for r in doc['registers']}
    predicted=dict(truth)
    predicted['parameter:fuel_spent']+=3
    item=_entry(doc,predicted,plan)
    case,rows,pairs=audit._attribution_for_case(item,
        {k:{'predicted':predicted[k],'truth':v} for k,v in truth.items()},
        {doc['game_id']:{'plan':plan}})
    row=next(r for r in rows if r['register_id']=='parameter:fuel_spent')
    assert row['register_absolute_error']==3
    assert row['substituted_error']==pytest.approx(0.,abs=1e-9)
    assert row['absolute_error_improvement']==pytest.approx(case['baseline_error'])
    assert len(pairs)==len(rows)*(len(rows)-1)//2


def test_interactions_disclose_error_cancellation_and_synergy():
    # f=a+b, truth=(1,-1) => zero; model=(2,-2) => also zero.
    # Correcting either one register makes the answer WORSE, while correcting
    # both gives an accurate zero. The attribution must not hide negative gain.
    plan={'kind':'formula','required_registers':['a','b'],'steps':[
        {'id':'step:1','operation':'add','operands':[
            {'source':'public_register','ref':'a'},
            {'source':'public_register','ref':'b'}]}], 'final_step':'step:1'}
    rec={'case_sha256':'abc','game_id':'game','predicted_game_id':'game',
         'target_zone':'zero','target':0.,'predicted_answer':0.,'learned_route_error':None}
    case,one,two=audit._attribution_for_case(rec,{
        'a':{'predicted':2.,'truth':1.},'b':{'predicted':-2.,'truth':-1.}},
        {'game':{'plan':plan}})
    assert case['baseline_zero_exact'] is True
    assert sorted(r['absolute_error_improvement'] for r in one)==[-1.,-1.]
    assert len(two)==1 and two[0]['interaction_extra_improvement']==2.
    assert all(r['zero_exact_damaged'] for r in one)
    assert not two[0]['zero_exact_repaired'] # already correct


def test_sign_restore_not_confused_with_zero_precision():
    plan={'kind':'formula','required_registers':['v','offset'],'steps':[
        {'id':'step:1','operation':'subtract','operands':[
            {'source':'public_register','ref':'v'},
            {'source':'public_register','ref':'offset'}]}], 'final_step':'step:1'}
    rec={'case_sha256':'xyz','game_id':'game','predicted_game_id':'game',
         'target_zone':'small_negative','target':-0.2,'predicted_answer':0.1,'learned_route_error':None}
    case,one,pair=audit._attribution_for_case(rec,{
        'v':{'predicted':.1,'truth':-.2},'offset':{'predicted':0.,'truth':0.}},
        {'game':{'plan':plan}})
    v=next(r for r in one if r['register_id']=='v')
    assert case['baseline_sign_correct'] is False
    assert v['sign_repaired'] is True
    assert v['zero_exact_repaired'] is False
    assert v['absolute_error_improvement']==pytest.approx(.3)


def test_wrong_game_id_is_not_given_oracle_route_as_learned():
    doc=_case('captain_fuel_reserve')
    vals={r['id']:r['value']['float'] for r in doc['registers']}
    rec=_entry(doc,vals,flat._plan(doc),forced='some_other_game')
    case,rows,pairs=audit._attribution_for_case(rec,
        {k:{'truth':v,'predicted':v} for k,v in vals.items()},
        {doc['game_id']:{'plan':flat._plan(doc)}})
    assert case['status']=='ROUTE_MISMATCH_NO_REGISTER_SUBSTITUTION'
    assert not rows and not pairs


def _write_flat_fixture(tmp_path,*,games=('captain_fuel_reserve','captain_crew_risk',
                                        'bertrand_oligopoly','rock_paper_scissors')):
    """Real oracle docs and trained ridge; saved evaluation format is exact."""
    import torch
    torch.set_num_threads(1)
    from tools.nanojev_gamer_v1_flat_registers import _train,_evaluate
    train=[pairs.generate_case(game,i,47) for game in games for i in range(5)]
    dev=[pairs.generate_case(game,5,47) for game in games]
    def feat(rows):
        return np.asarray([[len(r['prompt_08'])%99,
              sum(map(ord,r['prompt_08']))%113,
              len(r['prompt_08'].split()),
              sum(ord(c)**2 for c in r['prompt_08'])%97,1.] for r in rows],dtype=np.float32)
    source={'selected':{'train':{'rows':train,'features':feat(train)},
                        'dev':{'rows':dev,'features':feat(dev)}}}
    docs={key:[flat.build_oracle(r) for r in value['rows']]
          for key,value in source['selected'].items()}
    learned=_train(source,docs,device='cpu',epochs=1,batch_size=16,lr=.001,seed=7)
    # Make routing deterministic in the fixture. Learned route is tested
    # elsewhere; this test audits predictions, not classification training.
    tablelog,gamelog,reglog=learned['outputs']['dev']
    for i,doc in enumerate(docs['dev']):
        gamelog[i,:]=-1000.
        gamelog[i,learned['games'].index(doc['game_id'])]=1000.
    learned['outputs']['dev']=(tablelog,gamelog,reglog)
    result,details,regrows=_evaluate('dev',docs['dev'],learned)
    root=tmp_path/'flat-0002';root.mkdir()
    (root/'dev_predictions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in details))
    with (root/'dev_register_predictions.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(regrows[0]));writer.writeheader();writer.writerows(regrows)
    (root/'train_formula_templates.json').write_text(json.dumps(learned['plans']))
    (root/'final_report.json').write_text(json.dumps({
        'schema':'nanojev.gamer.v1.flat-register-training.v2',
        'status':'TRAINED_DIAGNOSTIC_NOT_PROMOTED','output':str(root),
        'source':{'dataset_sha256':'corpus-digest','frozen_adapter_sha256':'adapter-digest',
                  'head_checkpoint_sha256':'head-digest'},
        'training':{'dev_cases':len(dev),'dev_register_labels':len(regrows)},
        'dev':result,'baseline_saved_C0_Wide':{'raw_mae':6.05}}))
    return root


def test_saved_flat_v2_artifacts_end_to_end_no_training_and_immutable(tmp_path,monkeypatch):
    root=_write_flat_fixture(tmp_path)
    originals={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}
    # Reuse the project command path, without importing a training or capture runner.
    report=stair.main(['flat-attribution','--run',str(root)])
    assert report['status']=='COMPLETED_READ_ONLY_DIAGNOSTIC'
    assert report['coverage']['dev_cases']==4
    assert report['coverage']['attributed_matched_route_cases']==4
    assert report['coverage']['single_interventions']>4
    assert report['coverage']['pair_interventions']>=4
    assert report['baseline']['replayed_matched_route_mae']==pytest.approx(
        report['baseline']['source_flat_report_learned_route_mae'],rel=1e-8)
    out=Path(report['output'])
    assert out!=root and not out.is_relative_to(root)
    for name in report['artifacts']:assert (out/name).is_file()
    assert originals=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}
    assert report['baseline']['oracle_all_registers_result_matches_target_for_all_attributed_cases']
    with pytest.raises(FileExistsError,match='EXISTS'):
        audit.run_attribution(root,output=out)


def test_saved_predictions_must_match_frozen_register_csv(tmp_path):
    root=_write_flat_fixture(tmp_path)
    cases=list(audit._rows(root/'dev_predictions.jsonl'))
    cases[0]['predicted_answer']+=17.
    (root/'dev_predictions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in cases))
    with pytest.raises(ValueError,match='SAVED_PREDICTION_DOES_NOT_REPLAY'):
        audit.run_attribution(root)


def test_source_evidence_must_be_complete_and_dev_only(tmp_path):
    root=_write_flat_fixture(tmp_path)
    regfile=root/'dev_register_predictions.csv'
    rows=list(csv.DictReader(regfile.open()))
    rows[0]['split']='train'
    with regfile.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
    with pytest.raises(ValueError,match='NON_DEV_REGISTER'):
        audit.run_attribution(root)


def test_output_separation_and_phase_flag_validation(tmp_path):
    root=_write_flat_fixture(tmp_path)
    with pytest.raises(ValueError,match='OUTSIDE_ACTIVE_RUN'):
        audit.run_attribution(root,output=root/'insider')
    with pytest.raises(ValueError,match='FLAGS_ONLY'):
        stair.main(['status','--run',str(root),'--attribution-output',str(tmp_path/'elsewhere')])
    with pytest.raises(ValueError,match='DOES_NOT_TRAIN'):
        stair.main(['flat-attribution','--run',str(root),'--steps','7'])


def test_zero_line_summary_reports_exact_and_sign_separately():
    examples=[{'target_zone':'zero','status':'ATTRIBUTED','baseline_error':.02,
               'baseline_zero_exact':False,'baseline_zero_within_0_1':True},
              {'target_zone':'small_negative','status':'ATTRIBUTED','baseline_error':.2,
               'baseline_sign_correct':False},
              {'target_zone':'small_positive','status':'ATTRIBUTED','baseline_error':.01,
               'baseline_sign_correct':True}]
    result=audit._zone_summary(examples)
    assert result['zero']['baseline_exact_zero_within_tolerance']==0.
    assert result['zero']['baseline_zero_within_0_1']==1.
    assert result['small_negative']['baseline_sign_accuracy']==0.
    assert result['small_positive']['baseline_sign_accuracy']==1.


def test_all_146_games_three_cases_full_saved_artifact_attribution(tmp_path):
    """No ML execution: replay saved predictions through real certified formula plans."""
    from tools import nanojev_game_transformation_library as tr
    from tools import nanojev_gamer_v1_catalog_extensions as extension
    from tools import nanojev_gamer_v1_transition_games as transition
    game_ids=sorted(set(tr.MATRICES)|set(tr.SCALAR_MODELS)|
                    set(extension.EXTRA_IDS)|set(transition.TRANSITION_IDS))
    assert len(game_ids)==146
    root=tmp_path/'flat_all_games';root.mkdir()
    cases=[];regs=[];plans={}
    for game in game_ids:
        for index in (4,5,6):
            doc=_case(game,index)
            plan=flat._plan(doc)
            if game not in plans:plans[game]={'plan':plan,'table_families':doc['table_families']}
            assert plans[game]['plan']==plan
            truth={r['id']:r['value']['float'] for r in doc['registers']}
            cases.append(_entry(doc,truth,plan))
            regs.extend({'split':'dev','case_sha256':doc['case_sha256'],
                         'game_id':game,'register_id':rid,'target':v,'prediction':v}
                        for rid,v in truth.items())
    (root/'dev_predictions.jsonl').write_text(''.join(json.dumps(c)+'\n' for c in cases))
    with (root/'dev_register_predictions.csv').open('w',newline='') as stream:
        w=csv.DictWriter(stream,fieldnames=['split','case_sha256','game_id','register_id',
                                             'target','prediction']);w.writeheader();w.writerows(regs)
    (root/'train_formula_templates.json').write_text(json.dumps(plans))
    (root/'final_report.json').write_text(json.dumps({
        'schema':'nanojev.gamer.v1.flat-register-training.v2',
        'status':'TRAINED_DIAGNOSTIC_NOT_PROMOTED','output':str(root),
        'source':{'dataset_sha256':'test-dataset','frozen_adapter_sha256':'test-adapter',
                  'head_checkpoint_sha256':'test-checkpoint'},
        'training':{'dev_cases':len(cases),'dev_register_labels':len(regs)},
        'dev':{'learned_route':{'metrics_on_valid_predictions':{'raw_mae':0.}}},
        'baseline_saved_C0_Wide':{'raw_mae':6.0506}}))
    report=audit.run_attribution(root)
    assert report['coverage']['dev_cases']==438
    assert report['coverage']['attributed_matched_route_cases']==438
    assert report['coverage']['single_interventions']==len(regs)
    assert report['coverage']['pair_interventions']>438
    assert report['baseline']['replayed_matched_route_mae']==pytest.approx(0.,abs=1e-12)
    assert all(abs(row['net_absolute_error_recovery'])<1e-10 for row in
               report['register_groups_by_net_consequence'])
