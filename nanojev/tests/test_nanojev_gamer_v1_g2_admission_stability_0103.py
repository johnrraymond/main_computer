"""0103: frozen admission comparison, secure blind scoring, distribution/gate audit."""
from collections import Counter
import json
import pytest

from tools import nanojev_gamer_v1_g2_admission_stability_0103 as m
from tests.test_nanojev_gamer_v1_g2_admission_constraints_0100 import _stage, surface, edges


def _setup(tmp_path,monkeypatch):
    root,src,original=_stage(tmp_path,monkeypatch)
    monkeypatch.setattr(m,'_prior_prompt_hashes',lambda root,dataset:(set(),{}))
    def cohort(root,dataset,audit_cases,holdout_cases):
        result={}
        for split,count,ngroups in (('audit',audit_cases,6),('holdout',holdout_cases,10)):
            pub,ans=[],[]
            size=count//ngroups
            for i in range(count):
                stream=i//size
                cid=f'g2-0103-{split}-{i:05d}'
                family='allocation' if (i%size)%2==0 else 'encounter'
                attrs={'case_id':cid,'family':family,'prompt_sha256':m.digest(cid),
                       'stream':stream,'fold':m.FOLDS[stream] if split=='audit' else None}
                pub.append({**attrs,'prompt':cid})
                ans.append({**attrs,'graph':{}})
            result[split]=pub,ans
        return result,{}
    monkeypatch.setattr(m,'_make_cohort',cohort)
    def infer(row,*args):
        s=surface(a=.3,b=.8)
        return {k:row[k] for k in ('case_id','family','prompt_sha256','stream','fold')} | {
             'surface':s,'predictions':m._predictions(s,m.prior._source(root)[1])}
    monkeypatch.setattr(m,'_infer',infer)
    return root,src,original


def test_fixed_protocol_and_no_tuning(tmp_path,monkeypatch):
    root,_,_= _setup(tmp_path,monkeypatch)
    c=m.prepare(root=root,audit_cases=48,cases=40)
    assert c['factors']=={'baseline':0.0,'strict_1':1.0}
    assert c['policy']['classification']=='DIAGNOSTIC_ONLY_NO_PRODUCTION_PERMISSION'
    assert c['holdout_seeds']==list(m.HOLDOUT_SEEDS)
    assert c['weight_edits']==0 and c['gradient_steps']==0
    assert c['production_promotion'] is False


def test_end_to_end_gold_physically_absent_during_inference(tmp_path,monkeypatch):
    root,src,original=_setup(tmp_path,monkeypatch)
    c=m.prepare(root=root,audit_cases=48,cases=40)
    dest=m.location(root)
    secret_train=dest/'seal'/'audit_answers.SECRET.jsonl'
    secret_holdout=dest/'seal'/'holdout_answers.SECRET.jsonl'
    hidden_train=secret_train.with_suffix('.hidden')
    hidden_holdout=secret_holdout.with_suffix('.hidden')
    secret_train.rename(hidden_train);secret_holdout.rename(hidden_holdout)
    m.capture(root=root,device='cpu')
    hidden_train.rename(secret_train)
    # Even the entire audit phase cannot access holdout answers.
    training=m.audit(root=root)
    assert training['source_sha256']==original
    assert len(training['folds'])==6
    assert training['legacy_unanimous_fold_veto']
    m.predict(root=root,device='cpu')
    assert m.read(dest/'holdout'/'lock.json')['gold_accessed'] is False
    hidden_holdout.rename(secret_holdout)
    report=m.score(root=root)
    assert report['cases']==40 and report['audit_cases']==48
    assert report['source_unchanged'] is True
    assert report['production_promotion'] is False
    assert report['g3_unlocked'] is False and report['weight_edits']==0
    assert all(not v['available'] for v in report['historical_0102_not_used_for_selection'].values())
    assert report['case_ledger']['cases']==40
    assert report['case_ledger']['sha256']==m.sha(dest/'scored'/'case_ledger.jsonl')
    assert 'distribution_shift_diagnostics' in report['training_vs_holdout']
    assert len(report['holdout']['seed_streams'])==10
    assert report['training_vs_holdout']['sampling_variation_compatible'] in (True,False)
    assert m.sha(src/m.prior.previous.previous.cur.HEAD_FILE)==original
    assert m.run(root=root,device='cpu')['source_unchanged'] is True


def test_source_and_prediction_tampering_block_score(tmp_path,monkeypatch):
    root,src,original=_setup(tmp_path,monkeypatch)
    m.prepare(root=root,audit_cases=48,cases=40)
    m.capture(root=root,device='cpu');m.audit(root=root)
    m.predict(root=root,device='cpu')
    dest=m.location(root)
    shard=dest/'holdout'/'shards'/'g2-0103-holdout-00000.json.gz'
    obj=m.prior.previous._gzip_read(shard)
    obj['surface']['pair_margins'][0][2][3]=900.0
    m.prior.previous._gzip_write(shard,obj)
    with pytest.raises(ValueError,match='SHARD_SHA_DRIFT'):
        m.score(root=root)
    assert m.sha(src/m.prior.previous.previous.cur.HEAD_FILE)==original


def test_predeclared_factor_mutation_fails_closed(tmp_path,monkeypatch):
    root,_,_=_setup(tmp_path,monkeypatch)
    m.prepare(root=root,audit_cases=48,cases=40)
    dest=m.location(root)
    c=m.read(dest/'seal'/'prepared.json')
    c['factors']['strict_1']=0.5
    m.write(dest/'seal'/'prepared.json',c)
    with pytest.raises(ValueError,match='PREDECLARATION_DRIFT'):
        m.capture(root=root,device='cpu')


def test_resumability_without_second_model_or_duplicate_capture(tmp_path,monkeypatch):
    root,_,_=_setup(tmp_path,monkeypatch)
    m.prepare(root=root,audit_cases=48,cases=40)
    m.capture(root=root,device='cpu')
    dest=m.location(root)
    assert len(list((dest/'audit'/'shards').glob('*.json.gz')))==48
    m.audit(root=root)
    m.predict(root=root,device='cpu')
    m.score(root=root)
    assert len(list((dest/'holdout'/'shards').glob('*.json.gz')))==40
    assert m.run(root=root,device='cpu')['source_unchanged']


def test_statistical_rules_are_separate_from_legacy_single_fold_veto():
    assert m._wilson_upper(0,2000)<.015
    assert m._wilson_upper(1,2000)<.015
    assert m._wilson_upper(0,0)==1.0
    assert m._wilson_upper(10,10)>0.95
    assert m._ci([0]*200)==[0,0]


def test_paired_metrics_and_label_attribution():
    e=edges()[1]
    p={'family':'allocation','fold':m.FOLDS[0],'stream':0,
       'gold':set(), 'sets':{'baseline':{e},'strict_1':set()},
       'surface':surface()}
    outcome=m._metrics([p])
    assert outcome['delta']['f1']==0
    assert outcome['delta']['fp']==-1
    assert outcome['paired']['removed_fp']==1
    assert outcome['changes_by_relation_label']['applies_to']['removed_false_positive']==1


def test_generator_prompts_distinct_from_0102(monkeypatch,tmp_path):
    root=tmp_path/'reports';root.mkdir()
    path=root/m.prior.NAME/'seal';path.mkdir(parents=True)
    m.write_rows(path/'holdout_prompts.jsonl',[{'prompt_sha256':m.digest('prior') }])
    # Keep only known paths in this test; avoid expensive base dataset access.
    monkeypatch.setattr(m.prior,'_prompt_hashes',lambda root,dataset:(set(),{}))
    orig=m._prior_prompt_hashes(root,tmp_path/'dataset.jsonl')
    assert m.digest('prior') in orig[0]
    assert m.prior.NAME+'/holdout_prompts.jsonl' in orig[1]
    cohorts,ex=m._make_cohort(root,tmp_path/'dataset.jsonl',48,40)
    prompts=cohorts['audit'][0]+cohorts['holdout'][0]
    hashes={x['prompt_sha256'] for x in prompts}
    assert len(hashes)==88 and m.digest('prior') not in hashes
    assert len({x['stream'] for x in cohorts['holdout'][0]})==10


def test_score_delta_ci_bootstrap_stable():
    e=edges()[1]
    records=[]
    for i in range(40):
        records.append({'family':'allocation' if i%2==0 else 'encounter',
                        'stream':i//4,'fold':None,'gold':set(),
                        'sets':{'baseline':{e},'strict_1':set()}})
    ci=m._bootstrap(records,seed=3,iterations=60)
    assert ci['ci95']==[0,0]
    block=m._bootstrap(records,seed=3,iterations=60,block=True)
    assert block['ci95']==[0,0]
