"""Focused tests for read-only probability geometry / MAE error attribution."""
import csv
import json
import math

import numpy as np
import pytest

from tools import nanojev_gamer_v1_separability as sep
from tools import nanojev_gamer_v1_threshold_calibration as cal


def test_tied_auc_is_half_and_perfect_is_one():
    assert sep._auc([1,1,1,1],[True,True,False,False])==.5
    assert sep._auc([.9,.8,.2,.1],[True,True,False,False])==1.
    assert sep._auc([.1,.2,.8,.9],[True,True,False,False])==0.
    assert sep._auc([.1,.2],[False,False]) is None
    assert sep._average_precision([1,1,1,1],[True,True,False,False])==.5
    assert sep._average_precision([.9,.8,.2,.1],[True,True,False,False])==1.


def test_probability_overlap_and_absent_zero_not_fabricated():
    def r(y,n,z,p):
        return {'target':y,'negative_probability':n,'zero_probability':z,
                'positive_probability':p,
                'zero_score':math.log(z/max(n,p)),
                'negative_score':math.log(n/p),
                'raw_class':int(np.argmax([n,z,p])),
                'calibrated_class':int(np.argmax([n,z,p])),
                'raw_prediction':-2. if n>max(z,p) else (0. if z>max(n,p) else 2.),
                'calibrated_prediction':-2. if n>max(z,p) else (0. if z>max(n,p) else 2.)}
    rr=[r(-3,.8,.1,.1),r(-2,.9,.05,.05),r(0,.05,.9,.05),r(0,.05,.8,.15),
        r(3,.1,.1,.8),r(4,.1,.05,.85)]
    a=sep._one_segment(rr)
    assert a['separation']['zero_vs_nonzero']['auc']==1.
    assert a['separation']['negative_vs_positive_conditional_true_nonzero']['auc']==1.
    assert a['by_true_class']['zero']['probabilities']['zero']['median']==pytest.approx(.85)
    b=sep._one_segment([r for r in rr if r['target']!=0])
    assert b['separation']['zero_vs_nonzero']['status']=='UNIDENTIFIABLE_SINGLE_CLASS'
    assert b['separation']['zero_vs_nonzero']['auc'] is None
    assert b['separation']['negative_vs_positive_conditional_true_nonzero']['auc']==1.


def test_mae_attribution_exact_partition():
    rr=[]
    values=[(0,0,1),(0,2,2),(3,0,1),(3,2,2),(-3,2,2)]
    for y,p,c in values:
        rr.append({'target':y,'raw_prediction':p,'raw_class':c})
    a=sep._attribution(rr,'raw')
    assert a['cases']==5 and math.isclose(a['mae'],(0+2+3+1+5)/5)
    assert all(x['cases']==1 for x in a['groups'].values())
    assert math.isclose(sum(x['contribution_to_overall_mae'] for x in a['groups'].values()),a['mae'])


def test_cluster_bootstrap_fixed_seed_and_one_class():
    rr=[]
    for i,g in enumerate(('game_a','game_b','game_c')):
        for j,y in enumerate((0.,-3.,3.)):
            rr.append({'game_id':g,'target':y,
                'negative_probability':.7 if y<0 else .15,
                'zero_probability':.7 if y==0 else .15,
                'positive_probability':.7 if y>0 else .15})
    a=sep._bootstrap_auc(rr,'zero_vs_nonzero',reps=99,seed=47)
    b=sep._bootstrap_auc(rr,'zero_vs_nonzero',reps=99,seed=47)
    assert a==b and a['p025']==1. and a['replicates_valid']==99
    assert sep._bootstrap_auc(rr,'negative_vs_positive_conditional_true_nonzero',reps=99,seed=47)['p025']==1.
    with pytest.raises(ValueError,match='INVALID_REPS'):
        sep._bootstrap_auc(rr,'zero_vs_nonzero',reps=-1,seed=47)


def _source(tmp_path):
    root=tmp_path/'calibration';root.mkdir()
    arms={'localized':[5],'random_control':[9]}
    thresholds={arm:[{'fold':fold,'zero_threshold':.1,
                        'negative_threshold':-.1} for fold in range(2)] for arm in arms}
    rows=[]
    targets=(-3.,0.,3.,-16.,16.,5.)
    for arm in arms:
        for fold,game in enumerate(('game_a','game_b')):
            for j,y in enumerate(targets):
                index=fold*len(targets)+j
                # Original frozen scores intentionally imperfect around zero.
                if y==0:logit=np.array([.1,1.5,.2])
                elif y<0:logit=np.array([1.5,.25,.1])
                else:logit=np.array([.1,.25,1.5])
                if j==4:logit=np.array([.1,1.6,.3])  # false-zero on reserved magnitude
                if arm=='random_control':logit=logit+np.array([.1,0,-.1])
                probs=cal._softmax(logit[None,:])[0]
                scores=cal._scores(logit[None,:]);rc=int(logit.argmax());
                cc=int(cal._decide(logit[None,:],.1,-.1)[0]);m=np.array([1.])
                rp=float(cal._predictions(np.array([rc]),m)[0]);cp=float(cal._predictions(np.array([cc]),m)[0])
                rows.append({'arm':arm,'fold':fold,'index':index,'game_id':game,'target':float(y),
                    'raw_class':rc,'calibrated_class':cc,'raw_prediction':rp,'calibrated_prediction':cp,
                    'negative_probability':float(probs[0]),'zero_probability':float(probs[1]),
                    'positive_probability':float(probs[2]),
                    'zero_score':float(scores[0][0]),'negative_score':float(scores[1][0])})
    with (root/'predictions.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    report={'schema':cal.SCHEMA,'status':'COMPLETE_READ_ONLY_NOT_PROMOTED',
        'source_balanced_repair':str(tmp_path/'balanced'), 'arms':arms,'thresholds':thresholds,
        'coverage':{'heldout_cases':12},'results':cal._aggregate(rows,arms)}
    (root/'final_report.json').write_text(json.dumps(report),encoding='utf-8')
    return root,rows,report


def test_readonly_end_to_end_and_reproduces_calibration(tmp_path):
    root,rows,report=_source(tmp_path)
    originals={str(p):sep.suff._digest(p) for p in root.iterdir()}
    result=sep.run_separability(root,bootstraps=29,seed=47)
    assert result['status']=='COMPLETE_READ_ONLY_NOT_PROMOTED'
    assert result['coverage']['cases_per_arm']==12
    assert result['results']['localized']['all']['error_attribution']['raw']['cases']==12
    reserved=result['results']['localized']['reserved_untrained_magnitudes']
    assert reserved['true_class_counts']['zero']==0
    assert reserved['separation']['zero_vs_nonzero']['auc'] is None
    assert reserved['separation']['negative_vs_positive_conditional_true_nonzero']['auc'] is not None
    assert (tmp_path/'calibration-separability-0001'/'probability_distributions.csv').is_file()
    assert (tmp_path/'calibration-separability-0001'/'numerical_error_attribution.csv').is_file()
    assert (tmp_path/'calibration-separability-0001'/'probability_histograms.csv').is_file()
    assert (tmp_path/'calibration-separability-0001'/'worst_numerical_errors.csv').is_file()
    assert set(result['results']['localized']['by_fold'])=={'0','1'}
    assert all(sep.suff._digest(p)==hashval for p,hashval in originals.items())
    assert result['promotion'].startswith('BLOCKED_')
    with pytest.raises(ValueError,match='OUTPUT_OVERLAPS_SOURCE'):
        sep.run_separability(root,output=root/'bad',bootstraps=0)


def test_refuses_changed_saved_probabilities_and_mismatched_thresholds(tmp_path):
    root,rows,report=_source(tmp_path)
    csvpath=root/'predictions.csv'
    def rewrite(rows):
        with csvpath.open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    rows[0]['calibrated_class']=1
    rewrite(rows)
    with pytest.raises(ValueError,match='PREDICTION_THRESHOLD_MISMATCH'):
        sep.run_separability(root,bootstraps=0)
    rows[0]['calibrated_class']=0
    rows[0]['zero_probability']=.9
    rewrite(rows)
    with pytest.raises(ValueError,match='BAD_PROBABILITIES'):
        sep.run_separability(root,bootstraps=0)


def test_refuses_duplicate_rows_and_loss_of_arm_matching(tmp_path):
    root,rows,report=_source(tmp_path)
    with (root/'predictions.csv').open('a',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writerow(rows[0])
    with pytest.raises(ValueError,match='DUPLICATE_CASE'):
        sep.run_separability(root,bootstraps=0)


def test_readonly_does_not_recalibrate_using_dev(monkeypatch,tmp_path):
    root,rows,report=_source(tmp_path)
    def forbidden(*a,**k):
        raise AssertionError('HELDOUT_LABEL_RECALIBRATION_ATTEMPTED')
    monkeypatch.setattr(sep.cal,'_calibrate',forbidden)
    result=sep.run_separability(root,bootstraps=0)
    assert result['results']['localized']['all']['separation']['zero_vs_nonzero']['auc'] is not None


def test_source_comparison_protects_calibration_outcomes(tmp_path):
    root,rows,report=_source(tmp_path)
    report['results']['localized']['all']['raw']['mae']+=2.
    (root/'final_report.json').write_text(json.dumps(report),encoding='utf-8')
    with pytest.raises(ValueError,match='PRIOR_METRIC_MISMATCH'):
        sep.run_separability(root,bootstraps=0)
