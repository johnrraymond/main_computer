"""Frozen LoRA / C0 counterfactual transfer audit contracts."""
from __future__ import annotations
from fractions import Fraction
from pathlib import Path
import json
import numpy as np
import pytest

from tools import nanojev_gamer_v1_representation_transfer_audit as lab
from tools import nanojev_gamer_v1_polarity_magnitude_v2 as v2
from tools import nanojev_gamer_v1_polarity_magnitude_repair as v1
from tools import nanojev_gamer_v1_joint_sign_magnitude_lab as joint
from tools import nanojev_gamer_v1_sign_information_probe as oracle


@pytest.fixture(scope='module')
def data():
    base=oracle.make_deck(96,12,12,True)
    base,_=joint._auxiliary_deck(base)
    base,_=v1._hero_deck(base)
    base,_,_=v2._extension_deck(base)
    new,parts,profiles=lab._new_deck(base)
    return base,new,parts,profiles,[r.public() for r in new]


def test_exact_payoff_triplets_all_roles_and_no_v2_profile_reuse(data):
    base,new,parts,profiles,rows=data
    assert len(base)==1212 and len(new)==1572
    assert {k:len(v) for k,v in parts.items()} == {
        'counterfactual_train':216,'counterfactual_dev':72,'counterfactual_holdout':72}
    assert len(profiles)==20
    expected={
        'counterfactual_train':'train','counterfactual_dev':'dev',
        'counterfactual_holdout':'test'}
    prior={tuple(str(v) for v in (v2._profile(i)[0].R,v2._profile(i)[0].S,
                                  v2._profile(i)[0].T,v2._profile(i)[0].P))
           for i in range(40)}
    assert not prior.intersection(set(tuple(p['payoff']) for p in profiles.values()))
    seen_ids={r.id for r in base}
    for role,items in parts.items():
        assert all(x.split==expected[role] and x.family==role for x in items)
        assert all(x.id not in seen_ids for x in items)
    assert [x.public() for x in new[:1212]]==[x.public() for x in base]
    assert oracle.deck_hash(new)==oracle.deck_hash(lab._new_deck(base)[0])


def test_only_belief_changes_across_sign_and_radii(data):
    _,new,parts,profiles,rows=data
    grouped={}
    for role,cases in parts.items():
        for c in cases:
            grouped.setdefault((c.group,c.surface),[]).append(c)
    assert len(grouped)==len(profiles)*len(lab.RADII)*2
    for items in grouped.values():
        y=sorted(Fraction(v.target) for v in items)
        assert len(y)==3 and y[0]<0 and y[1]==0 and y[2]>0
        assert -y[0]==y[2]
    for i in range(20):
        stage,q0=lab._profile(i)
        assert stage.margin(q0)==0
        assert set([stage.gaps()[0]>stage.gaps()[1]])<={True,False}
        for radius in lab.RADII:
            assert 0<=q0-radius<=1 and 0<=q0+radius<=1
    # Identical zero prompts across radii are permitted only within one role.
    for role,cases in parts.items():
        assert len(set(x.group for x in cases))==len(cases)//6


def test_split_invariance_and_untouched_heldout_indices(data):
    _,new,parts,profiles,rows=data
    train={x.group.split('.radius.')[0] for x in parts['counterfactual_train']}
    dev={x.group.split('.radius.')[0] for x in parts['counterfactual_dev']}
    test={x.group.split('.radius.')[0] for x in parts['counterfactual_holdout']}
    assert not (train&dev or train&test or dev&test)
    assert not any(r['family']=='hawk_indifference' for r in rows[1212:])
    assert len([r for r in rows[:1212] if r['family']=='hawk_indifference'])==36
    assert len([r for r in rows[:1212] if r['family']=='fresh_profile'])==48


def test_feature_maps_fit_train_only_and_are_replayable():
    rng=np.random.default_rng(47)
    raw=rng.normal(size=(24,7)).astype(np.float32)
    tr=np.arange(12)
    mutated=raw.copy();mutated[16:]+=1000
    for kind in lab.KINDS:
        z,spec=lab._train_only_map(raw,tr,kind)
        z2,spec2=lab._train_only_map(mutated,tr,kind)
        assert np.allclose(z[:16],z2[:16])
        assert np.allclose(z,lab._apply_map(raw,kind,spec))
        assert np.allclose(spec[0],spec2[0])
        assert np.allclose(spec[1],spec2[1])
    assert lab._train_only_map(raw,tr,'linear')[0].shape==raw.shape
    with pytest.raises(ValueError,match='UNKNOWN_MAP'):
        lab._train_only_map(raw,tr,'bad')


def test_independent_probes_learn_zero_polarity_magnitude_without_test_fit():
    rng=np.random.default_rng(14)
    m=np.tile(np.array([.02,.08,.25,.8]),16)
    sign=np.repeat(np.asarray([-1,0,1],dtype=np.int8)[None,:],len(m),axis=0).reshape(-1)
    mag=np.repeat(m,3).astype(float);mag[sign==0]=0
    x=np.column_stack(((sign==0).astype(float)*4,
                       sign.astype(float)*3,
                       np.log(np.maximum(mag,.003)),
                       rng.normal(scale=.015,size=len(sign)))).astype(np.float32)
    train=np.arange(0,36*3);dev=np.arange(36*3,48*3);test=np.arange(48*3,len(sign))
    for task in lab.TASKS:
        model=lab._fit_probe(x,sign,mag,train,dev,task,epochs=75)
        pred=lab._score_probe(model,x,task)
        if task=='magnitude':
            stat=lab._magnitude_metrics(mag[test],pred[test]);assert stat['nonzero_mae']<.12
        else:
            stat=lab._binary_metrics(sign[test],pred[test],task)
            assert stat['balanced_accuracy']>=.9, (task,stat)
    assert len(test)==48


def test_probe_checkpoint_replays_and_detects_corrupt_task(tmp_path):
    rng=np.random.default_rng(3)
    x=rng.normal(size=(32,6)).astype(np.float32)
    z,spec=lab._train_only_map(x,np.arange(18),'rbf_1.0')
    model={'weights':np.linspace(-.3,.3,z.shape[1]).astype(np.float32),
           'bias':.15,'epoch':30}
    for task in lab.TASKS:
        f=tmp_path/f'{task}.npz'
        info=lab._save_probe(f,task,'rbf_1.0',spec,model,.5,np.arange(18),'source-sha')
        p,t=lab._replay(f,x,task)
        assert np.allclose(p,lab._score_probe(model,z,task),atol=1e-7)
        assert t==.5 and info['sha256']==lab._sha(f)
        with pytest.raises(FileExistsError,match='EXISTS'):
            lab._save_probe(f,task,'rbf_1.0',spec,model,.5,np.arange(18),'source-sha')
        with pytest.raises(ValueError,match='SCHEMA_MISMATCH'):
            lab._replay(f,x,'polarity' if task!='polarity' else 'magnitude')


def test_counterfactual_geometry_and_radius_sensitivity(data):
    _,new,parts,profiles,rows=data
    idx=np.asarray([i for i,r in enumerate(rows) if r['family']=='counterfactual_holdout'])
    labels,mag=lab._targets(rows)
    # Hand-built features with explicit sign, zero and magnitude coordinates.
    x=np.column_stack([labels,mag,labels==0,mag**2]).astype(np.float32)
    g,axis=lab._geometry(rows,idx,x)
    assert g['triples']==len(idx)//3
    assert g['train_axis_positive_pair_order_fraction']==1
    assert g['median_negative_positive_l2']>0
    d=lab._radius_order(rows,idx,x)
    assert d['adjacent_tests']==lab.NEW_HOLDOUT_PROFILES*2*2*(len(lab.RADII)-1)
    assert d['increasing_activation_distance_fraction']==1
    noisy=x.copy();noisy[:,1]=0;noisy[:,3]=0
    d2=lab._radius_order(rows,idx,noisy)
    assert d2['increasing_activation_distance_fraction']==0
    inverted=x.copy();inverted[:,0]*=-1
    g2,_=lab._geometry(rows,idx,inverted,axis)
    assert g2['train_axis_positive_pair_order_fraction']<1


def test_out_of_distribution_component_metrics_and_trivial_baseline(data):
    _,_,parts,_,rows=data
    idx=np.asarray([i for i,r in enumerate(rows) if r['family']=='counterfactual_holdout'])
    sign,mag=lab._targets(rows)
    zero_pred=(sign==0).astype(float);neg_positive=(sign>0).astype(float)
    zero=lab._by_family(rows,idx,sign,mag,zero_pred,'zero',.5)
    polarity=lab._by_family(rows,idx,sign,mag,neg_positive,'polarity',.5)
    assert zero['overall']['min_recall']==1 and polarity['overall']['min_recall']==1
    base=lab._by_family(rows,idx,sign,mag,np.zeros(len(rows)),'magnitude',.5)
    assert base['overall']['within_tight_tolerance']<1
    exact=lab._by_family(rows,idx,sign,mag,mag,'magnitude',.5)
    assert exact['overall']['within_tight_tolerance']==1
    assert exact['overall']['nonzero_mae']==0


def test_empty_and_broken_geometry_refused(data):
    _,_,_,_,rows=data
    subset=[i for i,r in enumerate(rows) if r['family']=='counterfactual_holdout']
    x=np.random.default_rng(4).normal(size=(len(rows),3))
    with pytest.raises(ValueError,match='INCOMPLETE_GEOMETRY_TRIPLE'):
        lab._geometry(rows,subset[:2],x)
    with pytest.raises(ValueError,match='INCOMPLETE_RADIUS_SET'):
        lab._radius_order(rows,subset[:-1],x)


def test_causal_stage_names_and_nonpromotion_contract():
    assert lab.STAGES==('lora_final_mean100','enc_gelu_3','mid_gelu')
    assert lab.STEP==3000 and lab.SCHEMA.startswith('nanojev.gamer.v1.')
    assert lab.NEW_HOLDOUT_PROFILES>=4
    assert lab.THRESHOLDS[1]==.50
    assert 'BLOCKED_DIAGNOSTIC_ONLY' in lab._gate({})['promotion']


def test_incomplete_v2_report_is_rejected_before_loading_model(tmp_path):
    d=tmp_path/'prior';d.mkdir();(d/'final_report.json').write_text(json.dumps({'schema':v2.SCHEMA,'status':'INCOMPLETE'}))
    with pytest.raises(ValueError,match='REQUIRES_COMPLETED_FROZEN_V2'):
        lab._source(d)


def test_real_joint_lineage_shape_is_validated_before_capture():
    # V2._source(...)["prior"] is the V1 repair source descriptor.
    # Its "prior" is the JOINT report, and only THAT has "steps".
    reference = {'cases': 108, 'balanced_sign_accuracy': 88/108,
                 'complete_triples': 18}
    valid = {'prior': {'prior': {'schema': joint.SCHEMA,
              'status': 'COMPLETE_FROZEN_DUAL_PATH_AND_UNSEEN_PROFILE_TRANSFER',
              'steps': {'3000': {'cross_game_training': {'test': reference}}}}}}
    assert lab._joint_sign_baseline(valid) is reference
    assert 'steps' not in valid['prior']
    for malformed in ({'prior': {'steps': {'3000': {}}}},
                      {'prior': {'prior': {'schema': 'wrong', 'steps': {}}}},
                      {'prior': {'prior': {'schema': joint.SCHEMA, 'steps': {}}}}):
        with pytest.raises(ValueError, match='GAMER_TRANSFER_JOINT_REPORT_LINEAGE_MISMATCH'):
            lab._joint_sign_baseline(malformed)


def test_end_to_end_mocked_frozen_three_stage_capture_reports_and_blocks(monkeypatch,tmp_path,data):
    from tools import nanojev_gamer_v1_dual_sign_head as dual
    from tools import nanojev_gamer_v1_c0_sign_head as three
    from tools import nanojev_gamer_v1_polarity_magnitude_repair as repair
    from tools import nanojev_gamer_v1_handoff as gamer
    _,deck,parts,profiles,rows=data
    indices_by_id={r['id']:i for i,r in enumerate(rows)}
    ix={role:np.asarray([indices_by_id[c.id] for c in cases],dtype=int)
        for role,cases in parts.items()}
    ix['base_train']=np.asarray([i for i,r in enumerate(rows[:1212]) if r['split']=='train'])
    ix['base_dev']=np.asarray([i for i,r in enumerate(rows[:1212]) if r['split']=='dev'])
    ix['original_test']=np.asarray([i for i,r in enumerate(rows[:1212]) if r['split']=='test' and r['family']!='fresh_profile'])
    ix['v2_fresh']=np.asarray([i for i,r in enumerate(rows[:1212]) if r['family']=='fresh_profile'])
    ix['train_extended']=np.concatenate((ix['base_train'],ix['counterfactual_train']))
    ix['dev_extended']=np.concatenate((ix['base_dev'],ix['counterfactual_dev']))
    assert (len(ix['base_train']),len(ix['base_dev']),len(ix['original_test']),len(ix['v2_fresh']))==(936,120,108,48)
    paths={name:tmp_path/name for name in ('v2','repair','joint','lineage','mother','training','source','capture','checkpoint','adapter','audit','causal')}
    for path in paths.values():path.mkdir()
    report=paths['v2']/'final_report.json';report.write_text('V2_FROZEN_REPORT')
    zero=tmp_path/'zero.npz';zero.write_bytes(b'FAKE_FROZEN_ZERO_CHECKPOINT')
    signs,mag=lab._targets(rows)
    original_test_labels=signs[ix['original_test']]+1
    # The real source replay must equal previously verified sign accuracy.
    prior={
      'zero_head':zero,
      'prior':{'schema':joint.SCHEMA,
               'status':'COMPLETE_FROZEN_DUAL_PATH_AND_UNSEEN_PROFILE_TRANSFER',
               'steps':{'3000':{'cross_game_training':{'test':{
                   'cases':108,'complete_triples':36,'balanced_sign_accuracy':1.0}}}}},
      'joint_dir':paths['joint'],
      'lineage':{
         'report_path':paths['lineage']/'final_report.json',
         'origin':{'root':paths['mother'],'source':{
             'training':paths['training'],'source':paths['source'],
             'capture':paths['capture'],'ckpt':paths['checkpoint']/'model.pt',
             'adapter':paths['adapter'],'audit_path':paths['audit']/'audit.json',
             'causal_path':paths['causal']/'causal.json'}}}}
    original={'report_path':paths['repair']/'final_report.json','prior':prior}
    src={'v2_report':{'frozen_lora_sha256':'FROZEN_3000',
                       'combined_deck_sha256':'FROZEN_V2_DECK'},
         'v2_report_path':report,'original':original,
         'deck':deck,'rows':rows,'indices':ix,'profiles':profiles,'role_cases':parts}
    x=np.column_stack((signs*4,(signs==0)*4,np.log1p(mag)*2,mag)).astype(np.float32)
    features={lab.STAGES[0]:x,lab.STAGES[1]:x*.85,lab.STAGES[2]:x*.75}
    monkeypatch.setattr(lab,'_source',lambda _:src)
    monkeypatch.setattr(lab,'_capture',lambda *args: (features,np.zeros(len(rows))))
    monkeypatch.setattr(repair,'_zero_proba',lambda *args:((signs==0).astype(float),.5))
    monkeypatch.setattr(dual,'_reload',lambda *args:(original_test_labels,
                                np.eye(3)[original_test_labels]))
    monkeypatch.setattr(lab,'KINDS',('linear',))
    monkeypatch.setattr(lab,'THRESHOLDS',(.5,))
    args=type('Args',(),dict(v2=paths['v2'],output=tmp_path/'out',
              epochs=10,capture_batch=2,head_batch=8,device='cpu'))()
    returned=lab.run(args)
    assert returned['status']=='COMPLETE_FROZEN_REPRESENTATION_AUDIT_NO_PROMOTION'
    assert returned['component_gate']['promotion']=='BLOCKED_DIAGNOSTIC_ONLY'
    assert returned['no_production_training_or_promotion']
    assert set(returned['stages'])==set(lab.STAGES)
    for st, arms in returned['stages'].items():
        assert set(arms)=={'v2_train_control','counterfactual_train_extension'}
        for info in arms.values():
            assert set(info['tasks'])==set(lab.TASKS)
            for task in lab.TASKS:
                assert set(info['tasks'][task]['evaluation'])=={
                    'original_test','v2_fresh','counterfactual_holdout'}
            assert info['heldout_geometry']['counterfactual_holdout']['magnitude_radius_ordering']['adjacent_tests']==32
    assert (tmp_path/'out'/'final_report.json').exists()
    assert (tmp_path/'out'/'frozen_stage_features.npz').exists()
    assert report.read_text()=='V2_FROZEN_REPORT'
    assert zero.read_bytes()==b'FAKE_FROZEN_ZERO_CHECKPOINT'
    with pytest.raises(FileExistsError,match='GAMER_SIGN_HEAD_OUTPUT_ALREADY_EXISTS'):
        lab.run(args)
