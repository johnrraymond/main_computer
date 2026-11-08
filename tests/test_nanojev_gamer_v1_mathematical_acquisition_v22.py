"""V22 matched acquisition: three verified starting states and cumulative math gates."""
from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_mathematical_acquisition_v22 as v22
from tools import nanojev_gamer_v1_corrected_margin_v14 as v14
from tools import nanojev_gamer_v1_margin_consolidation_v13 as v13
from tools import nanojev_gamer_v1_boundary_isolation_v12 as v12
from tools import nanojev_gamer_v1_capacity_retention_v11 as v11
from tools import nanojev_gamer_v1_conflict_projection_v10 as v10
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_precision_continuation_v19 as v19
from tools import nanojev_gamer_v1_shadow_precision_v21 as v21
from tests.test_nanojev_gamer_v1_conflict_projection_v10 import _setup
from tests.test_nanojev_gamer_v1_protected_math_repair_v7 import _checkpoint


@pytest.fixture(autouse=True)
def _threads():
    before=torch.get_num_threads();torch.set_num_threads(2)
    yield
    torch.set_num_threads(before)


def _metrics():
    cases=[]
    for group in ['dev.offset.0001','dev.offset.0002']:
        for surface in ('source_native','spine_difference'):
            for sign in (-1,0,1):
                ok=(group.endswith('1') or sign==0)
                cases.append({'id':group+'.'+str(sign)+'.'+surface,
                    'group':group,'split':'dev','family':'signed_offset',
                    'surface':surface,'target_fraction':str(sign),
                    'target_sign':sign,'correct':int(ok),
                    'predicted_sign':sign if ok else -sign,
                    'zero_miss':int(not ok and sign==0),
                    'nonzero_as_zero':0,'wrong_direction':int(not ok and sign!=0),
                    'magnitude_band':'small','correction_admitted':1})
    return cases


def test_group_and_zero_metrics_expose_regressions():
    result=v22._atlas_metrics(_metrics())
    assert result['dev_correct']==8
    assert result['dev_total']==12
    assert result['dev_complete_six']==1
    assert result['zero_misses']==0
    assert result['reversal_miss_groups']==1
    assert result['negative_correct']==2


def test_seeds_and_validation_fail_closed():
    assert v22._seeds('49,53')==[49,53]
    for bad in ('49,49','-1','1,2,3,4,5,6,7','abc'):
        with pytest.raises(argparse.ArgumentTypeError):
            v22._seeds(bad)
    with pytest.raises(ValueError,match='V22_MISSING_REQUIRED_INPUT'):
        v22._validate(SimpleNamespace(run=None),torch)
    assert len(v22.ARMS)==3


def test_unverified_history_refused_before_checkpoint_deserialization():
    with pytest.raises(ValueError,match='V22_HISTORICAL_PROVENANCE_OR_HASH_MISMATCH'):
        v22._metadata_check({}, {}, {}, sha19='x',sha20='y',sha19cp='z',
            sha19trials='zz',capture='capture',prior_hash='p',proof={},
            verified14={},lineage={})


def test_matched_pair_requires_identical_seed_and_control():
    base={'state':'selected','seed':49,'dev_correct':3,'dev_complete_six':1,
        'dev_complete_triples':1,'zero_correct':1,'negative_correct':1,
        'positive_correct':1,'dev_lost_cases':0,'dev_lost_groups':0,
        'train_correct':3,'train_six':0,'train_triples':0}
    rows=[dict(base,arm=v22.ARMS[0]),dict(base,arm=v22.ARMS[1],dev_correct=4),
          dict(base,arm=v22.ARMS[2],train_correct=5)]
    pairs=v22._matched_pair(rows,49,'selected')
    assert pairs[0]['delta_dev_correct']==1
    assert pairs[1]['delta_train_correct']==2
    with pytest.raises(ValueError,match='V22_INCOMPLETE_MATCHED_COMPARISON'):
        v22._matched_pair(rows[:-1],49,'selected')


def _make_history(tmp_path,prior,mu,sd,data,spec,capture,proof,v14root):
    """Synthetic historical evidence, with real weights and actual TRAIN masks."""
    report14=json.loads((v14root/'report.json').read_text())
    cp14=torch.load(v14root/'post_consolidation_candidate.pt',weights_only=True)
    sha14=v19._sha(v14root/'post_consolidation_candidate.pt')
    sha14rep=v19._sha(v14root/'report.json')
    verified14={'v14_report_sha256':sha14rep,
                'v14_post_consolidation_sha256':sha14,'train_ledger_verified':True}
    p19=tmp_path/'v19';p19.mkdir()
    c19=p19/'precision_joint_disposable_candidate.pt'
    torch.save({'schema':v19.SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
       'weights':cp14['weights'],'mean':torch.as_tensor(mu),'std':torch.as_tensor(sd),
       'training_capture_sha256':capture,'v14_post_sha256':sha14,
       'v18_checkpoint_sha256':'v18_sha',
       'frozen_lineage':data['probe_info']['lineage'],
       'margin_width':report14['config']['margin_width'],
       'capacity_width':report14['config']['capacity_width'],
       'train_ledger_mask':cp14['train_ledger_mask'],
       'hard_full_train_retention':True,'protected_original_passthrough':True,
       'promotion_authorized':False},c19)
    (p19/'precision_joint_trials.csv').write_text('iteration,decision\n1,REJECT\n')
    r19={'schema':v19.SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
       'source_anchor':'VERIFIED_V14_POST_CONSOLIDATION_NOT_V18','checkpoint_saved':True,
       'training_capture_sha256':capture,'v6_prior_sha256':v19._sha(prior),
       'v11_verified':proof,'v14_verified':verified14,
       'frozen_lineage':data['probe_info']['lineage'],
       'reference_v18':{'v18_checkpoint_sha256':'v18_sha'},
       'summary':{'accepted_iterations':1,'trial_count':1},
       'synthetic_dev_used_for_training_or_selection':False,
       'previously_seen_test_used_for_training_or_selection':False,
       'production_weights_modified':False,'promotion_authorized':False}
    (p19/'report.json').write_text(json.dumps(r19))
    p20=tmp_path/'v20';p20.mkdir()
    r20={'schema':v22.v20.SCHEMA,'status':'READ_ONLY_VERIFIED_DIAGNOSTIC_NO_PROMOTION',
       'v19_report_sha256':v19._sha(p19/'report.json'),
       'v19_trials_sha256':v19._sha(p19/'precision_joint_trials.csv'),
       'v19_checkpoint_verified':{'checkpoint_sha256':v19._sha(c19),
                                 'verified':True,'max_float32_parameter_difference_from_replay':0},
       'v19_trial_count_verified':1,'v19_accepted_updates':1,
       'source_anchor':'VERIFIED_V14_POST_CONSOLIDATION',
       'candidate_probes_verified':1,'classification':{'classification_counts':{'x':1}},
       'TRAIN_only_gradients':True,'hard_train_retention_unchanged':True,
       'checkpoint_saved':False,'production_weights_modified':False,
       'promotion_authorized':False,'synthetic_dev_used_for_training_or_selection':False,
       'previously_seen_test_used_for_training_or_selection':False,
       'frozen_lineage':data['probe_info']['lineage']}
    (p20/'report.json').write_text(json.dumps(r20))
    p21=tmp_path/'v21';p21.mkdir()
    c21=p21/'shadow_precision_disposable_candidate.pt'
    cp21={'schema':v21.SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
       'weights':cp14['weights'],'mean':torch.as_tensor(mu),'std':torch.as_tensor(sd),
       'training_capture_sha256':capture,'v14_post_sha256':sha14,
       'v19_checkpoint_sha256':v19._sha(c19),
       'v20_report_sha256':v19._sha(p20/'report.json'),
       'frozen_lineage':data['probe_info']['lineage'],
       'margin_width':report14['config']['margin_width'],
       'capacity_width':report14['config']['capacity_width'],
       'train_ledger_mask':cp14['train_ledger_mask'],
       'hard_full_train_retention':True,'protected_original_passthrough':True,
       'promotion_authorized':False}
    torch.save(cp21,c21)
    r21={'schema':v21.SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
       'checkpoint_saved':True,'summary':{'float32_export_updates':1,'checkpoint_saved':True},
       'source_anchor':'VERIFIED_V14_POST_CONSOLIDATION',
       'training_capture_sha256':capture,'v6_prior_sha256':v19._sha(prior),
       'v11_verified':proof,'v14_verified':verified14,
       'frozen_lineage':data['probe_info']['lineage'],
       'v19_report_sha256':v19._sha(p19/'report.json'),
       'v19_trials_sha256':v19._sha(p19/'precision_joint_trials.csv'),
       'v19_checkpoint_sha256':v19._sha(c19),
       'v20_report_sha256':v19._sha(p20/'report.json'),
       'hard_train_retention':'UNCHANGED_V14_FULL_TRAIN_EXACT_INFERENCE',
       'synthetic_dev_used_for_training_or_selection':False,
       'previously_seen_test_used_for_training_or_selection':False,
       'production_weights_modified':False,'promotion_authorized':False}
    (p21/'report.json').write_text(json.dumps(r21))
    return p19,p20,p21


def test_real_full_train_smoke_with_verified_chain_and_checkpoints(monkeypatch,tmp_path):
    data,prepared,spec,mu,sd,_=_setup()
    prior=tmp_path/'v6'/'disposable_candidate.pt';prior.parent.mkdir()
    _checkpoint(prior,data,mu,sd)
    monkeypatch.setattr(joint,'_prepare',lambda args:(data,prepared))
    monkeypatch.setattr(joint,'_verify_after',lambda data:None)
    common=dict(device='cpu',run=tmp_path/'r',expanded_run=tmp_path/'e',
       input=tmp_path/'i',source_features=tmp_path/'s',prior=prior,
       steps=1,eval_every=1,lr=.001,seed=49,replay_weight=2.,
       max_halvings=1,projection_passes=1,max_constraints=4)
    v9.run(SimpleNamespace(**{k:v for k,v in common.items()
        if k not in ('projection_passes','max_constraints')},output=tmp_path/'v9'))
    v10.run(SimpleNamespace(**common,v9_run=tmp_path/'v9',output=tmp_path/'v10'))
    v11.run(SimpleNamespace(**common,v10_run=tmp_path/'v10',
           output=tmp_path/'v11',capacity_width=16))
    proof=v12._verify_v11(torch,tmp_path/'v11',data,
        v9._groups_and_fingerprints(spec)[1],v19._sha(prior),spec,'cpu')[3]
    capture=v9._groups_and_fingerprints(spec)[1]
    focus=spec['train_synthetic'][3][0]['group']
    v12root=tmp_path/'v12';v12root.mkdir()
    (v12root/'report.json').write_text(json.dumps(dict(
       schema=v12.SCHEMA,status='EXPERIMENT_COMPLETE_NO_PROMOTION',mode='repair',
       promotion_authorized=False,source_dev_used_for_fitting=False,
       synthetic_dev_used_for_fitting=False,
       previously_seen_test_used_for_fitting_selection_or_metric=False,
       training_capture_sha256=capture,v6_sha256=v19._sha(prior),
       v11_verified_checkpoints=proof,focus_group=focus,focus_member_count=6,
       v11_historical_conflict_events=1,
       v11_report_sha256=v19._sha(tmp_path/'v11'/'report.json'))))
    provenance=v13._check_v12(v12root,proof,capture,v19._sha(prior),focus,
                   v19._sha(tmp_path/'v11'/'report.json'))
    v13root=tmp_path/'v13';v13root.mkdir()
    (v13root/'report.json').write_text(json.dumps(dict(
       schema=v13.SCHEMA,status='EXPERIMENT_COMPLETE_NO_PROMOTION',
       training_capture_sha256=capture,v6_prior_sha256=v19._sha(prior),
       v11_verified=proof,v12_verified=provenance,focus_group=focus,
       source_dev_used_for_training=False,synthetic_dev_used_for_training=False,
       previously_seen_test_used_for_training_or_selection=False,
       production_weights_modified=False,promotion_authorized=False,
       comparison=[{}, {}, {}, {}],consolidation={'accepted':0},
       selected='no_consolidation_control')))
    v14.run(SimpleNamespace(**common,v11_run=tmp_path/'v11',v12_run=v12root,
        v13_run=v13root,output=tmp_path/'v14',focus_group=focus,
        consolidation_steps=1,consolidation_lr=.001,reserve=.08,margin_width=16))
    p19,p20,p21=_make_history(tmp_path,prior,mu,sd,data,spec,capture,proof,tmp_path/'v14')
    args=SimpleNamespace(**{k:v for k,v in common.items()
            if k not in ('seed',)},seeds=[49],v11_run=tmp_path/'v11',
        v14_run=tmp_path/'v14',v19_run=p19,v20_run=p20,v21_run=p21,
        output=tmp_path/'v22',focus_group=focus)
    report=v22.run(args)
    assert report['promotion_authorized'] is False
    assert report['same_architecture_optimizer_and_seed_schedule'] is True
    assert len(report['comparison'])==6
    assert len(report['pairwise'])==4
    assert (args.output/'dev_case_transitions.csv').is_file()
    assert (args.output/'paired_effects.csv').is_file()
    for arm in v22.ARMS:
        for state in ('selected','terminal'):
            cp=torch.load(args.output/f'seed-49_{arm}_{state}_candidate.pt',weights_only=True)
            assert cp['status']=='DISPOSABLE_NON_PRODUCTION'
            assert cp['promotion_authorized'] is False
            assert int(cp['train_ledger_mask'].sum())>=1
    with pytest.raises(FileExistsError):v22.run(args)
    changed=json.loads((p21/'report.json').read_text());changed['promotion_authorized']=True
    (p21/'report.json').write_text(json.dumps(changed))
    args.output=tmp_path/'v22-other'
    with pytest.raises(ValueError,match='V22_HISTORICAL_PROVENANCE_OR_HASH_MISMATCH'):
        v22.run(args)


def test_production_checkpoint_capture_field_is_required():
    """V19 and V21 write training_capture_sha256, not train_capture_sha256."""
    for arm in v22.ARMS[1:]:
        cp={'schema':v19.SCHEMA if arm==v22.ARMS[1] else v21.SCHEMA,
            'status':'DISPOSABLE_NON_PRODUCTION',
            'v14_post_sha256':'anchor',
            'protected_original_passthrough':True,
            'hard_full_train_retention':True,
            'promotion_authorized':False,
            'training_capture_sha256':'capture',
            'frozen_lineage':{'lineage':'matched'},
            'margin_width':16,'capacity_width':32,
            'mean':torch.tensor([0.]),'std':torch.tensor([1.]),
            'weights':{'correction':torch.tensor([0.],dtype=torch.float32)},
            'train_ledger_mask':torch.tensor([True,False])}
        kw=dict(arm=arm,expected_capture='capture',expected_anchor='anchor',
            lineage={'lineage':'matched'},width=16,capacity_width=32,
            mu=np.array([0.],dtype=np.float32),
            sd=np.array([1.],dtype=np.float32))
        assert v22._checkpoint_mask(torch,cp,**kw).tolist()==[True,False]
        cp['train_capture_sha256']=cp.pop('training_capture_sha256')
        with pytest.raises(ValueError,match='V22_SOURCE_CHECKPOINT_PROVENANCE'):
            v22._checkpoint_mask(torch,cp,**kw)


def test_actual_v19_anchor_is_required_and_tampering_refused(monkeypatch):
    """V19's production report explicitly distinguishes its anchor from V18."""
    anchor='VERIFIED_V14_POST_CONSOLIDATION_NOT_V18'
    lineage={'source':'unchanged'}
    proof={'v11':'verified'}
    v14_proof={'v14':'verified'}
    base={'frozen_lineage':lineage}
    r19={**base,'schema':v19.SCHEMA,
         'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
         'source_anchor':anchor,'checkpoint_saved':True,
         'training_capture_sha256':'capture','v6_prior_sha256':'prior',
         'v11_verified':proof,'v14_verified':v14_proof,
         'production_weights_modified':False,'promotion_authorized':False,
         'synthetic_dev_used_for_training_or_selection':False,
         'previously_seen_test_used_for_training_or_selection':False}
    r21={**base,'schema':v21.SCHEMA,
         'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
         'checkpoint_saved':True,
         'summary':{'float32_export_updates':4,'checkpoint_saved':True},
         'source_anchor':'VERIFIED_V14_POST_CONSOLIDATION',
         'training_capture_sha256':'capture','v6_prior_sha256':'prior',
         'v11_verified':proof,'v14_verified':v14_proof,
         'v19_report_sha256':'sha19','v19_checkpoint_sha256':'sha19cp',
         'v19_trials_sha256':'sha19trials','v20_report_sha256':'sha20',
         'production_weights_modified':False,'promotion_authorized':False,
         'synthetic_dev_used_for_training_or_selection':False,
         'previously_seen_test_used_for_training_or_selection':False,
         'hard_train_retention':'UNCHANGED_V14_FULL_TRAIN_EXACT_INFERENCE'}
    r20={**base}
    calls=[]
    monkeypatch.setattr(v21,'_validate_v20',lambda *args:calls.append(args))
    kw=dict(sha19='sha19',sha20='sha20',sha19cp='sha19cp',
            sha19trials='sha19trials',capture='capture',prior_hash='prior',
            proof=proof,verified14=v14_proof,lineage=lineage)
    assert v22._metadata_check(r19,r20,r21,**kw)
    assert len(calls)==1
    with pytest.raises(ValueError,match='V22_HISTORICAL_PROVENANCE_OR_HASH_MISMATCH: v19.source_anchor'):
        v22._metadata_check(dict(r19,source_anchor='VERIFIED_V14_POST_CONSOLIDATION'),
                            r20,r21,**kw)
    with pytest.raises(ValueError,match='V22_HISTORICAL_PROVENANCE_OR_HASH_MISMATCH: v21.v19_checkpoint_sha256'):
        v22._metadata_check(r19,r20,dict(r21,v19_checkpoint_sha256='tampered'),**kw)
    # Multiple failures must all be reported without relaxing any check.
    with pytest.raises(ValueError) as exc:
        v22._metadata_check(dict(r19,source_anchor='wrong',training_capture_sha256='wrong'),
                            r20,dict(r21,v20_report_sha256='wrong'),**kw)
    message=str(exc.value)
    assert 'v19.source_anchor' in message
    assert 'v19.training_capture_sha256' in message
    assert 'v21.v20_report_sha256' in message
