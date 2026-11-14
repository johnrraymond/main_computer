"""Read-only comparison of saved GAMER disposable readouts on identical dev cases."""
import csv
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_generalization as gen
from tools import nanojev_gamer_v1_sufficiency as suff
from tools import nanojev_gamer_v1_staircase as stairs


def test_cli_and_flags_are_restricted():
    p=stairs.parser()
    args=p.parse_args(['head-100-generalization','--run','abc','--gen-batch','16'])
    assert args.gen_batch==16 and args.phase=='head-100-generalization'
    with pytest.raises(ValueError,match='GENERALIZATION_FLAGS_ONLY'):
        stairs.main(['status','--run','abc','--gen-batch','16'])
    with pytest.raises(ValueError,match='GENERALIZATION_READ_ONLY_NO_STEPS'):
        stairs.main(['head-100-generalization','--run','abc','--steps','5'])


def test_confusion_by_three_classes_and_false_zero_not_masked_by_recall():
    y=[-1,-.5,0,0,.5,2]
    c=[1,2,1,1,1,2]
    p=[0,1,0,0,0,2]
    m=gen._audit_predictions(y,p,c)
    assert m['cases']==6
    assert m['true_class_confusion']['negative']['predicted_as']=={'negative':0,'zero':1,'positive':1}
    assert m['true_class_confusion']['zero']['class_recall']==1
    assert m['true_class_confusion']['positive']['predicted_as']['zero']==1
    assert m['zero_false_positive_rate']==.5
    assert m['zero_precision']==.5
    assert m['small_negative_sign_accuracy']==0
    assert m['always_positive_nonzero_sign_accuracy']==.5


def test_paired_same_rows_separates_within_game_and_unseen_class_gap():
    rows=[]
    for g in range(12):
        for y in [-.5,0,.5]:
            klass=int(suff._classes([y])[0])
            rows.append({'game_id':f'g{g}','target':y,
                         'familiar_prediction':y,'familiar_class':klass,
                         'unseen_prediction':0.,'unseen_class':1})
    r=gen._paired_summary(rows,seed=47,permutations=999)
    assert r['cases']==36 and r['games']==12
    assert r['familiar_game']['three_class_accuracy']==1
    assert abs(r['unseen_game']['three_class_accuracy']-1/3)<1e-8
    assert r['by_true_class']['negative']['unseen_recall']==0
    assert r['paired_class_decision_discordance']['familiar_correct_unseen_wrong']==24
    assert r['mae_advantage_of_familiar']>0
    assert gen._interpret_gap(r)['status']=='GAME_DISJOINT_TRANSFER_DEFICIT_AFTER_FAMILIAR_COMPETENCE'


def test_checkpoint_rejects_swapped_fold_or_arm(tmp_path):
    torch=pytest.importorskip('torch')
    model=suff._model(torch,1024,16)
    file=tmp_path/'model.pt'
    suff._save_state(torch,file,model.state_dict(),{'fold':0,'mode':'localized','positions':[65],
       'source_checkpoint_sha256':'fixed-sha'})
    good=gen._check_checkpoint(torch,file,0,'localized',[65],'fixed-sha',1024,16,'cpu')
    assert good is not None
    with pytest.raises(ValueError,match='LINEAGE_MISMATCH'):
        gen._check_checkpoint(torch,file,1,'localized',[65],'fixed-sha',1024,16,'cpu')


def test_read_only_end_to_end_replay_no_retraining(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    torch.set_num_threads(2)
    main=tmp_path/'suff';causal=tmp_path/'causal';exp=tmp_path/'exp';source=tmp_path/'source'
    capture=tmp_path/'capture';loc=tmp_path/'loc'
    for folder in (main,causal,exp,source,capture,loc):folder.mkdir()
    (exp/'provenance.json').write_text(json.dumps({'configuration':{'input_width':102400}}))
    (exp/'best_model.pt').write_bytes(b'ORIGINAL_HEAD')
    (main/'untouched').write_text('UNCHANGED')
    games=[f'game{i:03d}' for i in range(30)]
    train=[{'game_id':g,'answer':y,'prompt':f'train_{g}_{y}'} for g in games for y in (-.5,0,.5)]
    dev=[{'game_id':g,'answer':y,'prompt':f'dev_{g}_{y}'} for g in games for y in (-.5,0,.5)]
    fx=np.zeros((len(dev),102400),dtype=np.float32)
    for j,row in enumerate(dev):
        fx[j,65*1024]=float(row['answer'])
    (exp/'feature_cache').mkdir()
    (exp/'feature_cache'/'dev.npz').write_bytes(b'FROZEN_FEATURE_CACHE')
    selected=[65,66,71,72]
    arms=suff._arms(selected,seed=47)
    calib=games[:3];folds=suff._split_groups(train,dev,calib,folds=3,seed=47)
    report={'schema':suff.SCHEMA,'status':'COMPLETE_EXPERIMENTAL_NOT_PROMOTED',
         'promotion':'BLOCKED_FROZEN_PRODUCTION_C0_AND_UNCERTIFIED_ZERO_SPELLINGS',
         'source_causal':str(causal),'source_expanded':str(exp),
         'selected_positions':selected,'arms':arms,
         'settings':{'folds':3,'hidden':8,'seed':47},
         'holdout':{'folds':[{'fold':f['fold'],'heldout_games':f['test_games'],'test_cases':len(f['test'])} for f in folds]},
         'certified_split_sizes':{'scored_dev':81},
         'source_fingerprints':{str(exp/'provenance.json'):suff._digest(exp/'provenance.json')},
         'results':{}}
    (main/'final_report.json').write_text(json.dumps(report))
    prior=[]
    for arm in ('localized','neighbor_control'):
        for f in folds:
            fi=f['fold']
            torch.manual_seed(fi+99)
            dim=len(arms[arm])*1024
            net=suff._model(torch,dim,8)
            path=main/'experimental_models'/f'fold{fi+1:02d}_{arm}.pt'
            suff._save_state(torch,path,net.state_dict(),
               {'fold':fi,'mode':arm,'positions':arms[arm],
                'source_checkpoint_sha256':'hash-head'})
            p,c,q=suff._predict(torch,net,fx,f['test'],arms[arm],device='cpu',batch_size=50)
            for i,pred,klass,prob in zip(f['test'],p,c,q):
                prior.append({'mode':arm,'fold':fi,'dev_case_index':i,
                    'game_id':dev[i]['game_id'],'target':dev[i]['answer'],
                    'prediction':pred,'predicted_class':klass,'zero_probability':prob[1]})
    with (main/'heldout_predictions.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=prior[0].keys());w.writeheader();w.writerows(prior)
    monkeypatch.setattr(gen.suff,'_source',lambda _: (
        causal,{'source_localization':str(loc),'checkpoint_sha256':'hash-head'},
        {'contexts':[{'game_id':x} for x in calib]},None,selected,exp,source,capture))
    monkeypatch.setattr(gen.suff,'_cached_split',lambda *args:(dev,fx,exp/'feature_cache'/'dev.npz'))
    monkeypatch.setattr(gen,'_read_training_rows',lambda *args:train)
    monkeypatch.setattr(gen.gamer,'original_prompt',lambda r:r['prompt'])
    before={str(p):suff._digest(p) for p in list(main.rglob('*'))+list(exp.rglob('*')) if p.is_file()}
    monkeypatch.setattr(gen.suff,'_fit',lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('TRAINING_FORBIDDEN')))
    result=gen.run_generalization(main,device='cpu',batch_size=50,arms=('localized','neighbor_control'))
    assert result['status']=='COMPLETE_READ_ONLY_NOT_PROMOTED'
    assert result['certified_dev_unique_cases']==81
    out=main.parent/'suff-generalization-0001'
    assert (out/'final_report.json').is_file()
    with (out/'paired_dev_predictions.csv').open() as f:rows=list(csv.DictReader(f))
    assert len(rows)==162
    assert set(r['arm'] for r in rows)=={'localized','neighbor_control'}
    assert all(r['familiar_fold']!=r['unseen_fold'] for r in rows)
    assert all(suff._digest(p)==sha for p,sha in before.items())
    # The read-only lab must not even instantiate an optimizer.


def test_prior_replay_mismatch_refused():
    dev=[{'game_id':'one','answer':0}]
    prior={('localized',0,0):{'game_id':'one','target':'0','predicted_class':'1',
          'prediction':'0.00','zero_probability':'0.75'}}
    gen._verify_prior_holdout(prior,dev,'localized',0,[0],[0.0],[1],np.asarray([[.15,.75,.1]]))
    with pytest.raises(ValueError,match='REPLAY_PARITY_FAILED'):
        gen._verify_prior_holdout(prior,dev,'localized',0,[0],[2.0],[2],np.asarray([[.15,.75,.1]]))


def test_within_game_head_failure_not_mistaken_for_transfer_failure():
    rows=[]
    for g in range(10):
        for y in (-.5,0.,.5):
            rows.append({'game_id':str(g),'target':y,'familiar_prediction':0.,
                         'familiar_class':1,'unseen_prediction':0.,'unseen_class':1})
    m=gen._paired_summary(rows)
    assert gen._interpret_gap(m)['status']=='FAMILIAR_GAME_SIGN_ZERO_READOUT_NOT_LEARNED'


def test_validation_only_game_retained_in_unseen_audit_but_excluded_from_pairs():
    """Regression for NO_MATCHED_FAMILIAR_FOLD on a game validated in every other fold."""
    dev=[{'game_id':'only_validation_elsewhere','answer':-.5},
         {'game_id':'pairable_game','answer':0.}]
    groups=[
        {'fold':0,'val_games':[], 'fit_games':['pairable_game'],
         'test_games':['only_validation_elsewhere']},
        {'fold':1,'val_games':['only_validation_elsewhere'], 'fit_games':[],
         'test_games':['pairable_game']},
        {'fold':2,'val_games':['only_validation_elsewhere'], 'fit_games':['pairable_game'],
         'test_games':[]},
        {'fold':3,'val_games':['only_validation_elsewhere'], 'fit_games':['pairable_game'],
         'test_games':[]},
    ]
    computed={
       ('localized',0,0):{'cohort':'unseen_game','prediction':.1,'class':2,'zero_probability':.3},
       ('localized',1,1):{'cohort':'unseen_game','prediction':0.,'class':1,'zero_probability':.8},
       ('localized',0,1):{'cohort':'familiar_game','prediction':0.,'class':1,'zero_probability':.9},
       ('localized',2,1):{'cohort':'familiar_game','prediction':.2,'class':2,'zero_probability':.2},
    }
    paired,unmatched,unseen=gen._assemble_arm_pairs(
         'localized',range(2),groups,computed,dev,np.asarray([-.5,0.]))
    assert len(paired)==1 and paired[0]['dev_case_index']==1
    assert paired[0]['familiar_fold']==0 and paired[0]['unseen_fold']==1
    assert len(unmatched)==1 and unmatched[0]['dev_case_index']==0
    assert unmatched[0]['validation_folds']==[1,2,3]
    assert unmatched[0]['reason']=='VALIDATION_IN_ALL_OTHER_FOLDS'
    cover=gen._coverage(paired,unmatched,unseen)
    assert cover['eligible_cases']==2 and cover['paired_cases']==1
    assert cover['unmatched_by_true_class']=={'negative':1}
    all_unseen=gen._unseen_all_metrics(unseen)
    assert all_unseen['cases']==2
    assert all_unseen['true_class_confusion']['negative']['cases']==1


def test_missing_familiar_not_silently_ignored_when_not_explained_by_validation():
    dev=[{'game_id':'g0','answer':-1}]
    groups=[{'fold':0,'val_games':[],'fit_games':[]},
            {'fold':1,'val_games':[],'fit_games':['g0']}]
    computed={('localized',0,0):{'cohort':'unseen_game','prediction':0.,
                 'class':1,'zero_probability':.5}}
    with pytest.raises(ValueError,match='UNEXPLAINED_MISSING_FAMILIAR'):
        gen._assemble_arm_pairs('localized',[0],groups,computed,dev,np.asarray([-1.]))


def test_no_matched_cases_still_supports_standalone_unseen_metrics():
    rows=[{'game_id':'g1','target':-1.,'unseen_prediction':0.,
           'unseen_class':1,'dev_case_index':0}]
    assert gen._paired_summary([])['status']=='BLOCKED_NO_PAIRED_ROWS'
    audit=gen._unseen_all_metrics(rows)
    assert audit['cases']==1 and audit['true_class_confusion']['negative']['cases']==1
